"""Thinking-fold regressions — collapsed-by-default + reasoning_content parity.

The user-visible bugs these pin down:
- Folds rendered EXPANDED by default because markdown.js persisted every
  expanded thought's content-hash to localStorage and a MutationObserver
  re-expanded matching folds on every render, accumulating forever.
- DeepSeek-style models (reasoning in a separate `thinking` SSE flag, rounds
  ending reasoning→tool_call with no content delta) leaked reasoning as plain
  chat bubbles: the frontend's stateful <think> wrapper was never closed or
  reset at agent round boundaries.
- Agent-round reasoning vanished on reload (round_texts only kept content).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_markdown_has_no_thinking_expansion_persistence():
    """Folds are always collapsed on (re)render — no localStorage re-expander."""
    md = _read("static/js/markdown.js")
    assert "_watchThinking" not in md
    assert "_loadExpandedSet" not in md
    assert "_hashThinkingContent" not in md
    # legacy key is actively cleaned up
    assert "localStorage.removeItem('odysseus-thinking-expanded')" in md
    # the expand-on-click toggle itself stays
    assert "_setThinkingExpanded(content, toggle, header, willExpand)" in md


def test_live_fold_collapses_when_thinking_ends():
    """Both thinking-end finalizers fold back a mid-stream peek (label says
    'View …', so the box must not stay expanded)."""
    chat = _read("static/js/chat.js")
    needle = "_liveThinkContent.classList.remove('expanded')"
    assert chat.count(needle) >= 2  # </think> boundary + [DONE] force-close


def test_think_wrapper_closes_at_agent_round_boundaries():
    """DeepSeek rounds end reasoning→tool_call with no content delta, so the
    closing </think> never comes from a delta — the round-boundary handlers
    must close and reset the wrapper or rounds 2+ stream tag-less."""
    chat = _read("static/js/chat.js")
    closer = "if (_thinkOpen) { accumulated += '</think>'; roundText += '</think>'; _thinkOpen = false; }"
    assert chat.count(closer) >= 2  # tool_start + agent_step
    # teacher takeover abandons the student bubble — flag reset there too
    assert "_thinkOpen = false; // student bubble abandoned" in chat


def test_background_resume_skips_reasoning_deltas():
    chat = _read("static/js/chat.js")
    assert "if (json.thinking) continue;" in chat


def test_compare_panes_fold_reasoning_deltas():
    """Compare panes used to concatenate {delta, thinking:true} as plain text."""
    cmp = _read("static/js/compare/stream.js")
    assert "json.thinking" in cmp
    assert "'<think>' + _delta" in cmp
    assert "'</think>' + _delta" in cmp


def test_agent_loop_persists_round_reasoning_for_reload():
    """round_texts (the reload render source) carries the round's reasoning as
    a <think> block so the fold the user saw live survives a history reload."""
    loop = _read("src/agent_loop.py")
    assert '"<think>" + round_reasoning.strip() + "</think>' in loop
    # the completion checks judge real content only (think stripped) — via
    # _strip_think_blocks(), the documented linear-time equivalent of
    # _THINK_RE.sub("", ...) (upstream merge #13 swapped the helper in).
    idx = loop.index('"<think>" + round_reasoning.strip()')
    after = loop[idx:idx + 2000]
    assert '_strip_think_blocks(cleaned_round)' in after
