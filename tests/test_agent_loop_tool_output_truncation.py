"""Tool-output display truncation uses _truncate with an indicator.

Previously agent_loop sliced tool output to a hard character limit ([:2000]
or [:4000]) with no signal to the UI that data was lost.  Now it delegates to
tool_utils._truncate which caps at MAX_OUTPUT_CHARS (10 000) and reports what
was dropped, so the frontend can show a truncation indicator in the tool bubble.

FORK: above _MIN_SPILL_LIMIT, _truncate now saves the full text and returns a
bounded head+marker+tail naming that file (src/tool_output_spill.py), so the
replacement is <= the limit rather than limit-plus-a-suffix, and the tail is
retained. Small limits keep the original plain-suffix behaviour, which the
limit=3 / limit=10 cases below still cover.
"""
from src.tool_utils import _truncate, MAX_OUTPUT_CHARS


def test_short_output_unchanged():
    """Outputs within the limit pass through verbatim."""
    text = "hello world"
    assert _truncate(text) == text


def test_long_output_truncated_with_indicator():
    """Outputs exceeding MAX_OUTPUT_CHARS are bounded and say what was dropped."""
    text = "x" * (MAX_OUTPUT_CHARS + 500)
    result = _truncate(text)
    # Now fits WITHIN the limit, so a second truncation pass is a no-op.
    assert len(result) <= MAX_OUTPUT_CHARS
    assert result.startswith("x")
    assert "omitted" in result                  # what was dropped is stated
    assert f"{len(text):,}" in result           # original length reported


def test_exact_limit_unchanged():
    """An output exactly at the limit is not truncated."""
    text = "a" * MAX_OUTPUT_CHARS
    assert _truncate(text) == text


def test_default_limit_matches_constant():
    """_truncate default limit equals MAX_OUTPUT_CHARS (10 000)."""
    assert MAX_OUTPUT_CHARS == 10_000
    text = "y" * 10_001
    result = _truncate(text)
    assert result != text and len(result) <= MAX_OUTPUT_CHARS


def test_empty_string():
    assert _truncate("") == ""
