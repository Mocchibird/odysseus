"""Tool output must be recoverable, never discarded.

Before spill, `_truncate` was head-only: everything past the cap was dropped and
the model was told only how many chars it had lost. For command output that threw
away the tail — which is where the error is — and made "look further down the
output" impossible without re-running the command.

These guards pin the properties that make the replacement safe to sit on the hot
path of every tool call.
"""
import os
import time
from pathlib import Path

import pytest

from src.constants import MAX_OUTPUT_CHARS
from src import tool_output_spill as spill
from src.tool_utils import _truncate


@pytest.fixture
def spill_dir(tmp_path, monkeypatch):
    """Point spill storage at a temp dir instead of the real DATA_DIR."""
    d = tmp_path / "spill"
    monkeypatch.setattr(spill, "spill_root", lambda: d)
    return d


def _bound(session="sess-1", tool="bash"):
    return spill.bind_tool_call(session, tool)


def test_full_output_is_recoverable_byte_for_byte(spill_dir):
    """The whole point: the overflow still exists somewhere."""
    text = "".join(f"line {i}\n" for i in range(20000))
    assert len(text) > MAX_OUTPUT_CHARS
    token = _bound()
    try:
        out = _truncate(text, MAX_OUTPUT_CHARS)
    finally:
        spill.unbind_tool_call(token)

    # The excerpt names a real file, and that file is the original verbatim.
    paths = list(spill_dir.rglob("*.txt"))
    assert len(paths) == 1, f"expected exactly one spill file, got {paths}"
    assert str(paths[0]) in out, "the excerpt must name the file it saved"
    assert paths[0].read_text(encoding="utf-8") == text


def test_the_replacement_fits_the_limit_and_shrinks(spill_dir):
    """Both invariants downstream code relies on."""
    text = "x" * (MAX_OUTPUT_CHARS * 7)
    token = _bound()
    try:
        out = _truncate(text, MAX_OUTPUT_CHARS)
    finally:
        spill.unbind_tool_call(token)
    assert len(out) <= MAX_OUTPUT_CHARS
    assert len(out) < len(text)


def test_truncation_is_idempotent(spill_dir):
    """agent_loop truncates tool text a SECOND time, after the tool already did.

    If a pass could re-spill or nest markers, that double truncation would write
    a redundant file per turn and eat the excerpt with stacked markers.
    """
    text = "y" * (MAX_OUTPUT_CHARS * 4)
    token = _bound()
    try:
        once = _truncate(text, MAX_OUTPUT_CHARS)
        twice = _truncate(once, MAX_OUTPUT_CHARS)
    finally:
        spill.unbind_tool_call(token)
    assert once == twice, "a second pass must be a no-op"
    assert len(list(spill_dir.rglob("*.txt"))) == 1, "the second pass must not spill again"


def test_both_head_and_tail_survive(spill_dir):
    """The old behaviour dropped the tail — where a failing command's error is."""
    head = "START-OF-OUTPUT"
    tail = "FATAL: the actual error"
    text = head + ("filler " * MAX_OUTPUT_CHARS) + tail
    token = _bound()
    try:
        out = _truncate(text, MAX_OUTPUT_CHARS)
    finally:
        spill.unbind_tool_call(token)
    assert head in out
    assert tail in out, "the tail must be represented, not discarded"


def test_a_storage_failure_degrades_to_plain_truncation(spill_dir, monkeypatch):
    """A full disk must not turn into a failed tool call."""
    monkeypatch.setattr(spill, "save_text", lambda *a, **k: None)
    text = "z" * (MAX_OUTPUT_CHARS * 2)
    out = _truncate(text, MAX_OUTPUT_CHARS)
    assert len(out) <= MAX_OUTPUT_CHARS + 64      # plain path appends a short note
    assert "truncated" in out
    assert out.startswith("z")


def test_save_errors_are_swallowed_not_raised(tmp_path, monkeypatch):
    """save_text returns None rather than propagating a storage error."""
    monkeypatch.setattr(spill, "spill_root", lambda: tmp_path / "nope")

    def boom(*a, **k):
        raise OSError("ENOSPC")

    monkeypatch.setattr(spill.Path, "mkdir", boom)
    assert spill.save_text("data", session_id="s", tool_name="bash") is None


def test_short_text_is_untouched_and_never_spills(spill_dir):
    token = _bound()
    try:
        assert _truncate("small", MAX_OUTPUT_CHARS) == "small"
    finally:
        spill.unbind_tool_call(token)
    assert not list(spill_dir.rglob("*.txt"))


def test_a_hostile_tool_name_cannot_escape_the_spill_dir(spill_dir):
    """The tool name names the file; it is never trusted as a path."""
    token = spill.bind_tool_call("../../etc", "../../../bin/sh")
    try:
        locator = spill.save_text("data", session_id="../../etc", tool_name="../../../bin/sh")
    finally:
        spill.unbind_tool_call(token)
    assert locator is not None
    resolved = Path(locator).resolve()
    assert str(resolved).startswith(str(spill_dir.resolve())), resolved
    assert ".." not in Path(locator).parts


def test_output_is_scoped_by_session(spill_dir):
    spill.save_text("a", session_id="sess-A", tool_name="bash")
    spill.save_text("b", session_id="sess-B", tool_name="bash")
    buckets = {p.parent.name for p in spill_dir.rglob("*.txt")}
    assert buckets == {"sess-A", "sess-B"}


def test_concurrent_saves_do_not_overwrite_each_other(spill_dir):
    """Two bash calls finishing in the same second must both survive."""
    for i in range(12):
        spill.save_text(f"body-{i}", session_id="s", tool_name="bash")
    files = list(spill_dir.rglob("*.txt"))
    assert len(files) == 12
    assert len({f.read_text() for f in files}) == 12


def test_missing_context_still_spills(spill_dir):
    """A tool that loses the contextvar across a thread boundary must still work."""
    text = "q" * (MAX_OUTPUT_CHARS * 2)
    out = _truncate(text, MAX_OUTPUT_CHARS)          # no bind_tool_call at all
    paths = list(spill_dir.rglob("*.txt"))
    assert len(paths) == 1
    assert paths[0].parent.name == "unscoped"
    assert str(paths[0]) in out


def test_retention_bounds_one_sessions_disk_use(spill_dir, monkeypatch):
    monkeypatch.setattr(spill, "SPILL_MAX_FILES_PER_SESSION", 5)
    for i in range(9):
        spill.save_text(f"n{i}", session_id="s", tool_name="bash")
    assert len(list(spill_dir.rglob("*.txt"))) <= 5


def test_retention_drops_files_past_the_age_limit(spill_dir, monkeypatch):
    locator = spill.save_text("old", session_id="s", tool_name="bash")
    assert locator
    stale = time.time() - (spill.SPILL_RETENTION_DAYS + 1) * 86400
    os.utime(locator, (stale, stale))
    spill.save_text("new", session_id="s", tool_name="bash")
    remaining = {p.read_text() for p in spill_dir.rglob("*.txt")}
    assert remaining == {"new"}, "the aged-out file should be gone"


def test_the_hint_points_somewhere_the_agent_can_actually_read():
    """The retrieval hint promises the agent can open the locator. Prove it.

    Spill files live under SPILL_PARENT_DIR; if that is not a readable tool path
    root, read_file/bash cannot open one and the hint becomes a lie. This is not
    hypothetical: upstream narrowed the native-file roots from DATA_DIR to an
    explicit carve-out list, and every locator silently became a path read_file
    refuses — files still written, hint still printed, retrieval always failing.

    Deliberately takes no spill_dir fixture so it reads the REAL root (and does
    not reload the module, which would clobber other tests' patches).
    """
    from src.constants import SPILL_DIR_NAME
    from src.tool_execution import (
        _can_traverse_tool_path,
        _is_denied_tool_path,
        _tool_path_roots,
    )

    # Composed from the constants, NOT from spill_root(): conftest redirects that
    # function for the whole session so the suite writes no repo-local artifacts.
    # The constants are what an operator would actually change.
    parent = Path(spill.SPILL_PARENT_DIR).resolve()
    root = (parent / SPILL_DIR_NAME).resolve()
    assert str(root).startswith(str(parent))
    assert SPILL_DIR_NAME in root.parts

    # The parent must be an allowed root, and must not be caught by any of the
    # deny rules layered on top of the allowlist (sensitive path, app state,
    # hard link) — being on the list is not by itself permission to read.
    assert str(parent) in _tool_path_roots(), (
        f"{parent} is not a tool path root — spill locators are unreadable"
    )
    assert not _is_denied_tool_path(str(parent))
    assert _can_traverse_tool_path(str(parent))


def test_the_hint_tells_the_model_what_to_do(spill_dir):
    text = "w" * (MAX_OUTPUT_CHARS * 2)
    token = _bound()
    try:
        out = _truncate(text, MAX_OUTPUT_CHARS)
    finally:
        spill.unbind_tool_call(token)
    lowered = out.lower()
    assert "read_file" in lowered or "bash" in lowered, "the hint must name a way to retrieve it"
    assert "omitted" in lowered


def test_a_locator_too_long_to_fit_is_not_emitted_clipped(tmp_path, monkeypatch):
    """A clipped path looks real and isn't — worse than no path at all."""
    deep = tmp_path / ("d" * 120) / ("e" * 120) / ("f" * 120)
    monkeypatch.setattr(spill, "spill_root", lambda: deep)
    text = "k" * 4000
    token = _bound()
    try:
        out = _truncate(text, 700)          # >= _MIN_SPILL_LIMIT? no -> plain
        out2 = _truncate(text, 1100)        # spills, but the marker is huge
    finally:
        spill.unbind_tool_call(token)
    for result, limit in ((out, 700), (out2, 1100)):
        assert len(result) <= limit + 64
        # Either a WHOLE path is present, or none is — never a fragment.
        if ".txt" in result:
            # The path is a whitespace-delimited token; the marker's prose follows it.
            frag = result.split("saved at ")[-1].split()[0].rstrip("]")
            assert Path(frag).exists(), f"emitted path must be real: {frag!r}"


def test_spilling_is_skipped_for_small_limits(spill_dir):
    """A caller asking for a few chars wants a label, not a file on disk."""
    out = _truncate("abcdefghij" * 10, 10)
    assert out.startswith("abcdefghij")
    assert "truncated" in out
    assert not list(spill_dir.rglob("*.txt")), "no file should be written for a tiny limit"


def test_a_spilled_locator_survives_the_real_path_resolver(spill_dir, monkeypatch):
    """End-to-end: the emitted path must pass the resolver read_file runs.

    spill_dir is a tmp root, so point the resolver's allowlist at it — the test
    above pins the production root. What is checked here is the locator itself:
    that what lands in the marker is a real, resolvable, non-denied path and not
    something only this module's own bookkeeping believes in.
    """
    import os

    from src import tool_execution

    monkeypatch.setattr(
        tool_execution, "_tool_path_roots",
        lambda: [os.path.realpath(str(spill_dir))],
    )
    token = _bound()
    try:
        out = _truncate("z" * 200_000, 4000)
    finally:
        spill.unbind_tool_call(token)

    locator = out.split("saved at ")[-1].split()[0].rstrip("]")
    resolved = tool_execution._resolve_tool_path(locator)   # raises if refused
    assert os.path.isfile(resolved)
    with open(resolved, encoding="utf-8") as handle:
        assert handle.read() == "z" * 200_000, "the spill must be verbatim"
