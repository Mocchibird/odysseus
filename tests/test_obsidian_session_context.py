import importlib
import sys
import types

from src.obsidian_context import load_obsidian_session_context, obsidian_context_path
from src.user_time import clear_user_time_context


def teardown_function():
    clear_user_time_context()


def test_obsidian_context_defaults_to_assistant_rules(monkeypatch, tmp_path):
    vault = tmp_path / "Vault"
    note = vault / "00_Index" / "assistant_rules.md"
    note.parent.mkdir(parents=True)
    note.write_text("Always remember the house style.", encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_VAULT_ROOT", str(vault))

    assert obsidian_context_path() == note.resolve()
    loaded = load_obsidian_session_context()
    assert "Persistent Obsidian Session Context" in loaded
    assert "Always remember the house style." in loaded


def test_chat_preface_includes_obsidian_context(monkeypatch, tmp_path):
    search = types.ModuleType("src.search")
    search.comprehensive_web_search = lambda *args, **kwargs: ("", [])
    search.fetch_webpage_content = lambda *args, **kwargs: {"success": False}
    youtube = types.ModuleType("src.youtube_handler")
    youtube.is_youtube_url = lambda _url: False
    monkeypatch.setitem(sys.modules, "src.search", search)
    monkeypatch.setitem(sys.modules, "src.youtube_handler", youtube)
    sys.modules.pop("src.chat_processor", None)
    ChatProcessor = importlib.import_module("src.chat_processor").ChatProcessor

    vault = tmp_path / "Vault"
    note = vault / "00_Index" / "assistant_rules.md"
    note.parent.mkdir(parents=True)
    note.write_text("Use my permanent context.", encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_VAULT_ROOT", str(vault))

    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())
    preface, _, _ = processor.build_context_preface(
        message="hello",
        session=None,
        agent_mode=False,
        use_memory=False,
        use_rag=False,
    )

    contents = "\n\n".join(msg["content"] for msg in preface)
    assert "Use my permanent context." in contents


def test_agent_system_prompt_includes_obsidian_context(monkeypatch, tmp_path):
    import src.agent_loop as agent_loop

    vault = tmp_path / "Vault"
    note = vault / "00_Index" / "assistant_rules.md"
    note.parent.mkdir(parents=True)
    note.write_text("Agent-wide context line.", encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_VAULT_ROOT", str(vault))
    monkeypatch.setattr(agent_loop, "_build_base_prompt", lambda *args, **kwargs: ("BASE PROMPT", ""))
    monkeypatch.setattr(agent_loop, "set_active_model", lambda model: None)
    monkeypatch.setattr(agent_loop, "get_builtin_overrides", lambda: {})
    monkeypatch.setattr(agent_loop, "_cached_base_prompt", None)
    monkeypatch.setattr(agent_loop, "_cached_base_prompt_key", None)

    messages, _ = agent_loop._build_system_prompt(
        [],
        model="gpt-oss-120b",
        active_document=None,
        mcp_mgr=None,
    )

    assert messages[0]["role"] == "system"
    assert "Agent-wide context line." in messages[0]["content"]
    assert "BASE PROMPT" in messages[0]["content"]


class _Memory:
    def load(self, owner=None):
        return []


class _Docs:
    rag_manager = None
