"""Tool-output spill — keep the bytes, hand the model a locator.

FORK-ONLY module. See docs/fork-additive-policy.md: all the behaviour lives here
so the upstream seams stay two-line delegations.

THE PROBLEM THIS SOLVES
-----------------------
``_truncate`` used to be head-only::

    text[:limit] + f"... (truncated, {len(text)} chars total)"

Everything past ``MAX_OUTPUT_CHARS`` was DISCARDED. The model was told exactly how
much it had lost and given no way to get any of it back. Two consequences, both
bad and both common:

* The **tail was thrown away**, and for command output the tail is usually where
  the error is. A build that fails after 12k chars of progress logs showed the
  progress and hid the failure.
* A long ``grep``/``bash`` result could not be followed up on at all. The bytes
  existed only in that one truncated string, so "look further down the output"
  was impossible — the agent's only recourse was to re-run the command, which is
  slow, sometimes destructive, and sometimes non-deterministic.

THE FIX
-------
Persist the full output verbatim to a session-scoped file under DATA_DIR, and
give the model a bounded excerpt plus an opaque **locator** and a **retrieval
hint**. The tail is kept as well as the head, and the middle is one grep away
instead of gone.

This works because DATA_DIR is already a tool path root (see
``tool_execution._tool_path_roots``), so ``read_file`` and ``bash`` can reach a
spill file. The retrieval hint is a promise the agent can actually keep — do not
move spill storage outside DATA_DIR without also fixing the hint.

DESIGN CREDIT
-------------
The seam is modelled on the spill capability in DeepSeek Harness
(https://github.com/deepseek-ai/deepseek-harness, MIT): persist verbatim, return
an opaque locator plus retrieval guidance, and keep storage separate from
retention and from tool-result replacement. Their tool-result pruner also
supplies the two properties enforced below — a bounded head/marker/tail, and a
replacement strictly smaller than its input so re-pruning is a no-op.

IDEMPOTENCE IS LOAD-BEARING
---------------------------
``agent_loop`` truncates tool result text a SECOND time, after the tool already
truncated it (see the ``_truncate`` calls around agent_loop.py:4160). Because
every replacement this module returns is ``<= limit``, that second pass is a
no-op: it cannot re-spill, cannot nest markers, and cannot grow the text.

FAILING SOFT IS ALSO LOAD-BEARING
---------------------------------
A full disk, a read-only volume or a permissions problem must never turn into a
failed tool call. Every storage error degrades to the old head-only truncation:
the agent is no worse off than before this module existed.

KNOWN LIMITATIONS
-----------------
* **A read_file overflow is spilled as a copy.** agent_loop trims some result
  fields again at MAX_OUTPUT_CHARS, and read_file's content arrives at up to
  MAX_READ_CHARS, so a large file read spills ~10-20 KB that already exists at
  its own path. Correct but redundant: the ideal hint would say "re-read the
  original with an offset" instead. Doing that needs the tool's arguments here,
  which the bound context does not carry, so it is deliberately not guessed at.
  Retention bounds the cost.
* **Spilling is storage, not access control.** The session id namespaces writes;
  it does not authorise reads. Anything that can reach DATA_DIR can read any
  spill file, exactly as with any other file under the agent's roots.
* **No retrieval API.** The locator is a plain path and retrieval is whatever
  read_file/bash can do with it. That is the point — it reuses tools the agent
  already has rather than adding a bespoke one.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Optional, Tuple

from src.constants import (
    DATA_DIR,
    SPILL_DIR_NAME,
    SPILL_MAX_FILES_PER_SESSION,
    SPILL_RETENTION_DAYS,
)

logger = logging.getLogger(__name__)

# Of the space left after the marker, how much goes to the tail. The head gets
# the rest. Weighted toward the head because that is where a command states what
# it is doing, but the tail is never zero: for a failing command the last lines
# are the whole point, and dropping them was the old behaviour's worst trait.
_TAIL_FRACTION = 0.3

# Below this much room for actual content, a head+marker+tail replacement is not
# worth building — the marker would crowd out the excerpt. We still point at the
# file, because a locator with almost no excerpt beats an excerpt with no locator.
_MIN_BODY_CHARS = 200

# Spilling below this limit is counter-productive: the marker alone would crowd
# out the excerpt, and a caller asking for 10 or 3 chars (there are such callers —
# _truncate takes an arbitrary limit) wants a short label, not a file on disk.
# Real tool limits (MAX_OUTPUT_CHARS 10k, MAX_READ_CHARS 20k) are far above this,
# so every genuine tool output still spills.
_MIN_SPILL_LIMIT = 1024

# Only these characters survive into a filename. The tool name is caller data:
# it names the file but is never trusted as a path.
_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]+")

# The current tool call, bound by tool_execution.execute_tool_block. Absent (or
# lost across a thread boundary that does not copy context) simply means a less
# well-named file in the unscoped bucket — never a failure.
_current_call: ContextVar[Tuple[Optional[str], Optional[str]]] = ContextVar(
    "odysseus_spill_tool_call", default=(None, None)
)


def bind_tool_call(session_id: Optional[str], tool_name: Optional[str]):
    """Bind the current tool call. Returns a token for :func:`unbind_tool_call`."""
    return _current_call.set((session_id or None, tool_name or None))


def unbind_tool_call(token) -> None:
    try:
        _current_call.reset(token)
    except (ValueError, RuntimeError):
        # Reset from a different context than the set — nothing to undo.
        pass


def _safe_component(raw: Optional[str], fallback: str) -> str:
    cleaned = _UNSAFE.sub("-", str(raw or "")).strip("-")
    return (cleaned[:48] or fallback)


def spill_root() -> Path:
    return Path(DATA_DIR) / SPILL_DIR_NAME


def _session_dir(session_id: Optional[str]) -> Path:
    return spill_root() / _safe_component(session_id, "unscoped")


def _prune(directory: Path) -> None:
    """Best-effort retention: drop files past the age limit, then past the count.

    Spill files are a debugging convenience with no durability guarantee, so this
    never raises and never blocks a save.
    """
    try:
        entries = []
        cutoff = time.time() - (SPILL_RETENTION_DAYS * 86400)
        for p in directory.iterdir():
            if not p.is_file():
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                p.unlink(missing_ok=True)
            else:
                entries.append((mtime, p))
        # Oldest first, so the newest SPILL_MAX_FILES_PER_SESSION survive.
        entries.sort()
        for _, p in entries[: max(0, len(entries) - SPILL_MAX_FILES_PER_SESSION)]:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def save_text(content: str, *, session_id: Optional[str] = None,
              tool_name: Optional[str] = None) -> Optional[str]:
    """Persist ``content`` verbatim. Returns the locator, or None on any failure.

    The uuid suffix — not a counter — is what makes concurrent tool calls safe:
    the app is single-worker but tools run concurrently, and two bash calls
    finishing in the same second must not overwrite each other's output.
    """
    try:
        directory = _session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{_safe_component(tool_name, 'output')}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.txt"
        path = directory / name
        # newline="" so the bytes come back exactly as produced; this file is
        # evidence, and a platform newline rewrite would falsify it.
        with open(path, "w", encoding="utf-8", errors="replace", newline="") as fh:
            fh.write(content)
        _prune(directory)
        return str(path)
    except (OSError, ValueError) as e:
        logger.warning("[spill] could not save tool output: %s", e)
        return None


def _plain(text: str, limit: int) -> str:
    """The pre-spill behaviour, kept as the degradation path."""
    return text[:limit] + f"\n... (truncated, {len(text)} chars total)"


def truncate_with_spill(text: str, limit: int) -> str:
    """Bound ``text`` to ``limit`` chars, saving the full copy first.

    Caller guarantees ``len(text) > limit``. Guarantees on the return value:
      * ``len(result) <= limit``  — so a second truncation pass is a no-op
      * ``len(result) < len(text)``
      * head AND tail of the original are both represented
    """
    if limit < _MIN_SPILL_LIMIT:
        # Decided BEFORE saving: writing a file for a caller that asked for a
        # few characters is pure waste, and the resulting excerpt would be too
        # small to carry both content and a path.
        return _plain(text, limit)

    session_id, tool_name = _current_call.get()
    locator = save_text(text, session_id=session_id, tool_name=tool_name)
    if not locator:
        return _plain(text, limit)

    total = len(text)

    def marker_for(omitted: int) -> str:
        return (
            f"\n\n... [{omitted:,} of {total:,} chars omitted. The FULL output is "
            f"saved at {locator} — read_file it, or grep/sed/tail it with bash, "
            f"instead of re-running this command.] ...\n\n"
        )

    # Size the marker against the largest number it could show, so the real
    # marker (with a smaller omitted count) can only be shorter and still fit.
    marker = marker_for(total)
    body = limit - len(marker)
    if body < _MIN_BODY_CHARS:
        # No room for a real excerpt (a very deep DATA_DIR makes the marker long).
        # Still surface the locator: pointing at the bytes is worth more than a
        # few hundred more characters of head.
        short = f"\n... [output saved at {locator}] ...\n"
        if len(short) >= limit:
            # The path itself does not fit. Emitting a CLIPPED path would be worse
            # than emitting none — it looks like a real path and silently isn't.
            return _plain(text, limit)
        return text[: limit - len(short)] + short

    tail_n = max(1, int(body * _TAIL_FRACTION))
    head_n = body - tail_n
    marker = marker_for(total - head_n - tail_n)
    out = text[:head_n] + marker + text[-tail_n:]
    # The recomputed marker is never longer than the one we budgeted for, but
    # clamp anyway: the invariant callers rely on is len(out) <= limit.
    return out[:limit]
