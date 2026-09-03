"""A built-in skill must not arm the post-external tool gate.

Upstream wraps the injected skills block as untrusted context and arms the gate
unconditionally: skill text is user-editable, and an imported skill genuinely is
external content. That is right for upstream's tree.

This fork additionally ships a VIRTUAL built-in skill (quiz-spoiler-markdown)
that every instance always has. With unconditional arming, the gate was therefore
armed on literally every turn, so every tool call raised an approval card and the
gate no longer distinguished a tainted run from a clean one — the security
signal was gone precisely because it never turned off.

Built-in skill text is shipped code reviewed in this repo. A skill the user wrote
or imported is not, and still arms.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_builtin_skill_is_not_treated_as_external_context():
    """The bundled skill exists and is flagged as built-in, not imported."""
    from services.memory.skills import SkillsManager
    from src.constants import DATA_DIR

    sm = SkillsManager(DATA_DIR)
    index = sm.index_for(owner=None)
    assert index, "the fork should ship at least one built-in skill"
    # load() is the arming signal: it returns the owner's OWN skills only, so the
    # virtual built-ins must NOT appear in it.
    assert all(
        not s.get("_builtin") for s in sm.load(owner=None)
    ), "load() must not include virtual built-in skills — the gate decision reads it"


def test_agent_loop_arms_the_gate_only_for_user_authored_skills():
    src = (ROOT / "src" / "agent_loop.py").read_text(encoding="utf-8")
    block = src.split("_skills_message = untrusted_context_message", 1)
    assert len(block) == 2, "the skills injection moved"
    call = block[1][:400]
    assert "arm_tool_gate=" in call, (
        "the skills block must decide arming rather than taking the default"
    )
    assert "_skills_are_first_party_only" in src
    # The decision must come from the owner's own skills, not from the index
    # (which always contains the built-ins).
    assert "_skills_are_first_party_only = not _own_skills" in src
    assert "_own_skills = sm.load(owner=owner)" in src


def test_untrusted_context_message_still_arms_by_default():
    """Every OTHER caller must keep arming — only skills opt out."""
    from src.prompt_security import untrusted_context_message

    armed = untrusted_context_message("web page", "hello")
    assert armed["metadata"]["tool_gate_untrusted"] is True

    opted_out = untrusted_context_message("skills", "hello", arm_tool_gate=False)
    assert opted_out["metadata"]["tool_gate_untrusted"] is False
    # Still labelled untrusted for the MODEL — the opt-out is only about the gate.
    assert opted_out["metadata"]["trusted"] is False


@pytest.mark.parametrize("label", ["web page", "email", "document", "recent tool context"])
def test_other_untrusted_sources_are_unchanged(label):
    from src.prompt_security import untrusted_context_message

    assert untrusted_context_message(label, "x")["metadata"]["tool_gate_untrusted"] is True


# ── functional: drive the real loop and watch the gate ──────────────────────


def _drive_loop_and_capture_gate(monkeypatch, own_skills):
    """Run one agent turn with a native bash call; report (gate_armed, executed).

    `own_skills` is what SkillsManager.load() returns — i.e. the owner's OWN
    skills. The virtual built-in is injected regardless, by index_for.
    """
    import importlib.util
    import json as _json

    import services.memory.skills as skills_mod
    import src.agent_loop as al
    import src.tool_capabilities as tc

    spec = importlib.util.spec_from_file_location(
        "_fenced", ROOT / "tests" / "test_fenced_example_not_executed_for_native_models.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)
    monkeypatch.setattr(
        skills_mod.SkillsManager, "load",
        lambda self, owner=None: [dict(s) for s in own_skills],
        raising=False,
    )

    executed = []

    async def _fake_exec(block, *a, **k):
        executed.append(block)
        return ("bash", {"output": "ok", "exit_code": 0})

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    armed = []
    original = tc.ToolRunSecurityContext.decision_for

    def _spy(self, tool_name, content=None):
        armed.append(bool(self.external_untrusted_context_seen))
        return original(self, tool_name, content)

    monkeypatch.setattr(tc.ToolRunSecurityContext, "decision_for", _spy, raising=False)

    helper._run_loop(
        monkeypatch, "gpt-4o", ["Sure."],
        native_calls=[{"name": "bash", "arguments": _json.dumps({"command": "echo hi"})}],
        max_rounds=2,
    )
    return (any(armed), [b.tool_type for b in executed])


def test_builtin_only_instance_does_not_gate_every_tool_call(monkeypatch):
    """The regression this fix exists for: with no skills of the user's own, the
    injected block is the built-in alone and must not arm the gate — otherwise
    every tool call on every turn raises an approval card."""
    gate_armed, executed = _drive_loop_and_capture_gate(monkeypatch, own_skills=[])
    assert gate_armed is False, "a built-in-only skills block must not arm the gate"
    assert executed == ["bash"], "the tool should run without an approval card"


def test_user_authored_skill_still_arms_the_gate(monkeypatch):
    """The safety direction: opting built-ins out must not disable the gate."""
    gate_armed, executed = _drive_loop_and_capture_gate(
        monkeypatch,
        own_skills=[{
            "name": "my-own-procedure", "description": "user authored",
            "when_to_use": "always", "procedure": "do the thing",
            "status": "published", "category": "mine",
            "source": "imported", "owner": None,
        }],
    )
    assert gate_armed is True, "a user-authored/imported skill must still arm the gate"
    assert executed == [], "the tool must wait for an explicit approval"
