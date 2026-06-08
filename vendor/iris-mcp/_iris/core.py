"""Iris core — all shared helpers + the VaultIndex class.

This module has NO @mcp.tool() registrations — it's pure utility code that the
tool modules import. Splitting helpers and VaultIndex into separate files was
considered but they're tightly coupled (the helpers shape what the VaultIndex
consumes), so one core module is the pragmatic choice.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


# ```sql / ```sqlite block detector used by the per-note indexer to
# populate the sql_views table. Same shape as the renderer's regex in
# `_iris/tools/sqlite.py`, simplified — we only need (lang, query) here.
_SQL_VIEW_INDEX_RE = re.compile(
    r"```(sqlite|sql)\r?\n(.*?)\r?\n```",
    re.DOTALL | re.IGNORECASE,
)


# =============================================================================
# Auto-reload helper for Obsidian's SQLite DB Plugin
# =============================================================================
#
# The Obsidian SQLite DB Plugin caches vault.db in memory on plugin load. After
# the MCP writes (people_upsert, anime_upsert, etc.) the plugin's view is stale.
# The companion plugin at .obsidian/plugins/sqlite-db-reload/ registers a
# protocol handler that forces a re-read. This helper fires that URI — but ONLY
# if Obsidian is already running, so vault writes don't accidentally launch it.


def maybe_reload_db_plugin(notify: bool = False) -> None:
    """Best-effort signal to Obsidian's SQLite DB Plugin to re-read vault.db.

    Behavior:
      • If Obsidian isn't running → no-op (we don't auto-launch it on writes).
      • If the companion plugin (sqlite-db-reload) isn't enabled → no-op.
      • Errors are swallowed; this is a UX nicety, not critical path.

    Called automatically after the *_upsert / *_remove tools.  Pass
    ``reload_db=False`` on those tools to suppress when doing bulk writes;
    then call ``reload_sqlite_db_plugin()`` once at the end.
    """
    try:
        # 1. Is Obsidian running?  Cheap pgrep — bail if no.
        if sys.platform == "darwin":
            check = subprocess.run(
                ["pgrep", "-f", "Obsidian.app"],
                capture_output=True, timeout=2,
            )
            if check.returncode != 0:
                return
        elif sys.platform.startswith("linux"):
            check = subprocess.run(
                ["pgrep", "-fi", "obsidian"],
                capture_output=True, timeout=2,
            )
            if check.returncode != 0:
                return
        # Windows / other: skip the running check, the open call below will
        # spawn Obsidian if not running — accepted trade-off.

        # 2. Fire the URI.
        uri = "obsidian://sqlite-db-reload"
        if notify:
            uri += "?notify=1"

        if sys.platform == "darwin":
            cmd = ["open", uri]
        elif sys.platform.startswith("linux"):
            cmd = ["xdg-open", uri]
        elif sys.platform == "win32":
            cmd = ["cmd", "/c", "start", "", uri]
        else:
            return

        subprocess.run(cmd, capture_output=True, timeout=3)
    except Exception:
        return  # silent


# ─── from original L27-535: Basic vault safety + generic file support ───
# =============================================================================
# Basic vault safety helpers
# =============================================================================


def get_vault_root() -> Path:
    import iris_config as cfg
    root = cfg.VAULT_ROOT.resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(
            f"Vault path does not exist or is not a directory: {root}. "
            f"Set IRIS_VAULT_ROOT (or [vault].root in {cfg.config_path()})."
        )
    return root


def safe_path(relative_path: str) -> Path:
    root = get_vault_root()
    candidate = (root / relative_path).resolve()

    if root not in candidate.parents and candidate != root:
        raise ValueError("Refusing to access path outside vault")

    return candidate


# ── Per-user attachment routing ──────────────────────────────────────────────
# Multi-user mode (PRs #100-#105) puts each user's attachments under
# ``users/<discord_id>/40_Attachments/...``. The owner is the default when
# no explicit ``user_id`` is provided — matches the DB-trigger behaviour
# that defaults unattributed domain writes to the owner.
#
# Why this lives in core (not a tool module): every attachment-writing
# code path (food photos, charts, voice synth, generic file imports)
# needs it, and we want one source of truth.


def resolve_user_id(user_id: Optional[int] = None) -> Optional[int]:
    """Resolve which ``users.id`` a domain tool should read/write under.

    Lookup order:

    1. **Explicit ``user_id`` arg** — validated against the DB. If unknown,
       falls through to the speaker / owner path (defensive).
    2. **Speaker env (``IRIS_SPEAKER_USER_ID``)** — set by the bot on
       every Claude subprocess. The current Discord speaker.
    3. **Owner fallback** — ONLY outside the bot context (no
       ``IRIS_DISCORD_CHANNEL_ID`` set). Preserves single-user Claude
       Desktop / CLI / test behaviour. In bot context this fallback
       is DISABLED — we'd rather return None and let the tool refuse
       than silently attribute a stranger's message to the owner.
    4. **Pre-multi-user** (no users table or no rows) → None.

    The bot-context strict mode is the load-bearing privacy guarantee:
    if `_resolve_and_scaffold_user` fails for any reason, the spawned
    Claude session has no speaker env, and any per-user tool that
    falls through here returns None → gates trigger `err: no speaker
    identified` → Iris refuses honestly rather than acting on
    owner data.
    """
    try:
        idx = get_vault_index()
    except Exception:
        return None
    if user_id is not None:
        row = idx.conn.execute(
            "SELECT id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        # Unknown user_id → fall through to speaker / strict-mode logic
    # Speaker env wins over owner default. Set by the bot per-session.
    raw = os.environ.get("IRIS_SPEAKER_USER_ID")
    if raw:
        try:
            speaker = int(raw)
            row = idx.conn.execute(
                "SELECT id FROM users WHERE id = ?", (speaker,)
            ).fetchone()
            if row is not None:
                return int(row["id"])
        except ValueError:
            pass
    # No speaker resolved.
    # In bot context: REFUSE the owner fallback. The bot was supposed
    # to identify the speaker; if it didn't, we shouldn't silently
    # impersonate the owner. The tool layer (assert_can_access) will
    # return `err: no speaker identified` to Iris.
    if os.environ.get("IRIS_DISCORD_CHANNEL_ID"):
        return None
    # Outside the bot — legacy single-user / CLI / Claude Desktop /
    # test contexts. Owner fallback is fine here; nobody else is on
    # the wire.
    owner = idx.get_owner_user()
    return int(owner["id"]) if owner is not None else None


def scope_clause(
    user_id: Optional[int],
    *,
    column: str = "user_id",
    in_where: bool = True,
) -> tuple[str, tuple]:
    """SQL fragment + params to scope a query by ``user_id``.

    Reduces the repeating boilerplate every per-user tool had — instead of
    writing two parallel ``c.execute`` branches you do:

        clause, params = scope_clause(uid)
        c.execute(
            f"SELECT ... FROM meals WHERE eaten_at >= ?{clause} ORDER BY ...",
            (cutoff, *params, limit),
        )

    - ``user_id``: the resolved user id (from :func:`resolve_user_id`).
      ``None`` returns an empty fragment so the query stays unscoped — same
      behaviour as before multi-user shipped, used as a safety fallback.
    - ``column``: the column name. Default ``"user_id"`` — override only
      if a table has aliased it (e.g. ``h.user_id`` in a join).
    - ``in_where``: True (default) returns ``" AND <col> = ?"`` — append
      after an existing WHERE clause. False returns ``" WHERE <col> = ?"``
      for queries that don't already have a WHERE."""
    if user_id is None:
        return "", ()
    keyword = "AND" if in_where else "WHERE"
    return f" {keyword} {column} = ?", (int(user_id),)


def get_attachments_root(user_id: int | None = None) -> Path:
    """Return the absolute path of the ``40_Attachments`` directory for
    the given user. If ``user_id`` is None, defaults to the owner. If no
    owner is registered yet (pre-multi-user deployment), falls back to
    the legacy vault-root ``40_Attachments`` so behaviour is preserved.

    Always returns a path; the directory may not exist on disk yet —
    callers are expected to ``mkdir(parents=True, exist_ok=True)`` on
    the subdir they actually write into.

    Uses the VaultIndex's own ``_root`` rather than ``get_vault_root()``
    so tests that point at a tmp vault (via a custom VaultIndex
    instance) get the right path even though iris_config.VAULT_ROOT
    is cached from import time.
    """
    try:
        idx = get_vault_index()
        vault_root = idx._root
    except Exception:
        # No index available (e.g. ad-hoc CLI usage with vault root only)
        # — fall back to legacy flat layout via get_vault_root().
        return get_vault_root() / "40_Attachments"
    if user_id is None:
        row = idx.get_owner_user()
    else:
        row = idx.conn.execute(
            "SELECT vault_subdir FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            row = idx.get_owner_user()
    if row is None:
        return vault_root / "40_Attachments"
    return vault_root / row["vault_subdir"] / "40_Attachments"


# ── Cross-user access control ───────────────────────────────────────────────
# Defense in depth: the system prompt instructs Iris not to cross user
# boundaries, but a misbehaving / jailbroken / buggy Iris could still try.
# Per-user tools call these helpers to refuse the call at the MCP layer,
# returning an ``err:`` string Iris can surface honestly rather than silently
# leaking data.
#
# Rule: a caller (the "speaker", current Discord author) can access user_id=N
#   - if N == speaker   (own data), or
#   - if speaker is the owner  (admin override — the human operator).
# Other speaker→target pairs are denied.
#
# Pre-multi-user (no owner registered) the gates pass through — preserves
# legacy single-user behaviour for setups that haven't enabled multi-user.


_USER_PATH_RE = re.compile(r"^users/([^/]+)/")


def current_speaker_id() -> Optional[int]:
    """Return the user_id of the current Discord speaker (the human whose
    message Iris is responding to), or None outside the bot context.

    Reads ``IRIS_SPEAKER_USER_ID`` directly — distinct from
    ``resolve_user_id(None)`` which falls back to owner. Use this when
    the *speaker's* identity is what matters (e.g. authorization checks)
    not "who should I attribute this write to".
    """
    raw = os.environ.get("IRIS_SPEAKER_USER_ID")
    if not raw:
        return None
    try:
        speaker = int(raw)
    except ValueError:
        return None
    try:
        idx = get_vault_index()
    except Exception:
        return None
    row = idx.conn.execute(
        "SELECT id FROM users WHERE id = ?", (speaker,)
    ).fetchone()
    return int(row["id"]) if row else None


def authorize_user_access(
    user_id: Optional[int],
) -> tuple[Optional[int], Optional[str]]:
    """One-call combined gate: resolve target + check authorization.

    Returns ``(target_uid, denial_str_or_None)``. Standard tool pattern:

        target_uid, denial = authorize_user_access(user_id)
        if denial:
            return denial
        # ... proceed with target_uid

    - ``target_uid`` is what the tool should read/write under (== the
      resolved user_id, defaulting to the current speaker if user_id
      arg was None).
    - ``denial`` is None when access is allowed, or an ``err:`` string
      when a non-owner tried to access another user's row.

    Outside the bot context (no ``IRIS_SPEAKER_USER_ID`` env), the
    speaker is the owner — gates always pass. This keeps Claude Desktop
    and CLI usage unchanged.
    """
    speaker = current_speaker_id()
    target = resolve_user_id(user_id)
    return target, assert_can_access(speaker, target)


def _in_bot_context() -> bool:
    """Are we running inside the Discord bot's per-message Claude subprocess?

    The bot always sets ``IRIS_DISCORD_CHANNEL_ID`` on every spawn (see
    ``docker/bot.py:_build_options``). Outside the bot — Claude Desktop,
    CLI tooling, ad-hoc tests — this env is absent, and the access gates
    fail open so single-user / development workflows keep working.

    Inside the bot, the gates fail CLOSED: if ``IRIS_SPEAKER_USER_ID``
    isn't set (the bot couldn't resolve the Discord author to a row),
    every per-user / per-path tool refuses rather than silently
    defaulting to the owner. That prevents a buggy / racy
    ``_resolve_and_scaffold_user`` from leaking owner data into a
    non-owner channel.
    """
    return bool(os.environ.get("IRIS_DISCORD_CHANNEL_ID"))


def assert_can_access(
    speaker_uid: Optional[int],
    target_uid: Optional[int],
) -> Optional[str]:
    """Authorization gate for tools that take a ``user_id`` argument.

    Returns ``None`` when access is allowed, or an ``err:`` string the
    tool should return directly when denied. Tools call this right after
    ``resolve_user_id`` to short-circuit cross-user reads / writes:

        speaker_uid = resolve_user_id(None)
        target_uid  = resolve_user_id(user_id)
        denial = assert_can_access(speaker_uid, target_uid)
        if denial:
            return denial

    Allow cases:
      - speaker == target (own data).
      - speaker is the owner (admin).
      - Outside the bot context AND speaker is None — legacy passthrough.

    Deny cases:
      - Bot context (``IRIS_DISCORD_CHANNEL_ID`` set) AND speaker is None
        — the bot failed to resolve a sender; we refuse to default to
        owner. Returns ``err: no speaker identified`` so the tool
        surfaces the misconfiguration rather than silently leaking.
      - speaker is a non-owner trying to access another user's data.
    """
    in_bot = _in_bot_context()
    if speaker_uid is None:
        if in_bot:
            return (
                "err: no speaker identified in bot context — the bot "
                "didn't pass IRIS_SPEAKER_USER_ID. Refusing to default "
                "to owner data."
            )
        return None  # Outside bot — legacy single-user / CLI / tests.
    if target_uid is None:
        return None
    if speaker_uid == target_uid:
        return None
    try:
        idx = get_vault_index()
        owner = idx.get_owner_user()
    except Exception:
        return None  # No index — pre-multi-user, fail open
    if owner is not None and int(owner["id"]) == int(speaker_uid):
        return None
    return (
        f"err: access denied — your data only "
        f"(speaker_uid={speaker_uid}, target_uid={target_uid}). "
        "Only the owner can access other users' data; non-owners are "
        "strictly limited to their own user_id."
    )


def assert_can_access_path(
    speaker_uid: Optional[int],
    vault_path: Path | str,
) -> Optional[str]:
    """Path-based authorization gate for vault file tools.

    Inspects ``vault_path`` for a ``users/<discord_id>/`` prefix. If the
    path lives under another user's per-user folder, denies the call
    unless the speaker is that user OR the owner. Paths outside any
    ``users/<id>/`` subdir (shared vault content) pass through.

    In bot context with no speaker resolved, refuses any per-user path
    (same fail-closed posture as :func:`assert_can_access`). Shared
    paths still pass through — Iris reading the global knowledge base
    isn't a privacy concern.

    Returns ``None`` when access is allowed, or an ``err:`` string when
    denied.
    """
    rel = str(vault_path).replace("\\", "/").lstrip("/")
    m = _USER_PATH_RE.match(rel)
    if not m:
        return None  # Outside any per-user folder = shared content
    # Per-user path with no speaker → fail closed only when in bot context.
    if speaker_uid is None:
        if _in_bot_context():
            return (
                "err: no speaker identified in bot context — refusing to "
                f"access {rel!r} without an authenticated sender."
            )
        return None
    target_discord_id = m.group(1)
    try:
        idx = get_vault_index()
    except Exception:
        return None
    row = idx.conn.execute(
        "SELECT id FROM users WHERE discord_id = ? AND status = 'active'",
        (target_discord_id,),
    ).fetchone()
    if row is None:
        # Unknown user folder — likely an artifact, no auth to enforce.
        return None
    return assert_can_access(speaker_uid, int(row["id"]))


def speaker_allowed_path(
    speaker_uid: Optional[int],
    vault_path: Path | str,
) -> bool:
    """Predicate version of :func:`assert_can_access_path` for filtering.

    Returns True when the speaker is permitted to see this path —
    shared content, own per-user folder, or owner-as-admin. False when
    the path lives under another user's folder.

    Differs from ``assert_can_access_path`` by returning a bool instead
    of an err string; use this in search/list result filtering where
    you want to silently drop forbidden paths rather than error.
    """
    return assert_can_access_path(speaker_uid, vault_path) is None


def filter_paths_for_speaker(
    speaker_uid: Optional[int],
    paths,
    *,
    key=None,
):
    """Drop entries from ``paths`` the speaker isn't allowed to see.

    ``paths`` can be a list of path strings, or a list of richer objects
    (rows / dicts / tuples) in which case ``key`` is a callable that
    extracts the path string from each entry. Returns the filtered list
    in the original order.

    Owner is the admin — always sees everything. Outside the bot
    context, also pass-through (legacy / tests). Inside the bot, a
    None speaker triggers fail-closed — only shared content survives.

    Use this in search / list / enumerate tools to scope results
    without erroring:

        rows = c.execute(...).fetchall()
        rows = filter_paths_for_speaker(
            current_speaker_id(), rows, key=lambda r: r["path"]
        )
    """
    if not paths:
        return paths
    # Outside bot AND no speaker → legacy passthrough.
    if speaker_uid is None and not _in_bot_context():
        return paths
    # Owner sees everything.
    try:
        idx = get_vault_index()
        owner = idx.get_owner_user()
        if owner and speaker_uid is not None and int(owner["id"]) == int(speaker_uid):
            return paths
    except Exception:
        return paths
    extractor = key if key is not None else (lambda p: p)
    out = []
    for entry in paths:
        try:
            p = extractor(entry)
        except (KeyError, TypeError, IndexError):
            # Can't extract a path — keep the entry (defensive).
            out.append(entry)
            continue
        if p is None:
            out.append(entry)
            continue
        if speaker_allowed_path(speaker_uid, p):
            out.append(entry)
    return out


def path_visible_to_user(
    path: Optional[str],
    vault_subdir: Optional[str],
) -> bool:
    """Generic path-based visibility predicate for tasks / reminders /
    notes / projects.

    Returns True when:

      - ``vault_subdir`` is None (legacy / Claude Desktop / CLI — see
        everything),
      - ``path`` is empty (defensive default — show shared content),
      - ``path`` is under the user's own ``users/<id>/`` folder, OR
      - ``path`` is at vault root (no ``users/`` prefix → shared with
        every user).

    Returns False only when ``path`` is under a DIFFERENT user's
    folder — cross-user content the speaker has no business seeing
    in their brief / agenda.

    For EVENTS, prefer ``event_visible_to_user`` — that variant also
    consults the per-event ``attendees`` column for the v24
    share_with opt-in. Tasks / reminders / notes / projects don't
    have an attendees concept; they're routed purely by source path,
    and the share_with flow for them would be a separate feature.
    """
    if vault_subdir is None:
        return True
    if not path:
        return True
    subdir = vault_subdir.rstrip("/")
    # The directory entry for ``vault_subdir`` itself (no trailing
    # slash) belongs to the user — without this exact-equality check,
    # the user's own folder gets filtered out of vault-tree listings
    # and clicking "users" in the tree expands to nothing because
    # the next level down is missing. Files inside still pass via
    # the startswith branch.
    if path == subdir or path.startswith(f"{subdir}/"):
        return True
    return not path.startswith("users/")


def path_writable_by_user(
    path: Optional[str],
    vault_subdir: Optional[str],
) -> bool:
    """Stricter than ``path_visible_to_user``: only the user's OWN
    ``users/<id>/`` subdir is writable.

    Returns True only when ``path`` is non-empty and lies inside the
    caller's ``vault_subdir``. Shared root-level files (e.g.
    ``60_Knowledge/...``) are read-only via the web — the LLM can
    still edit them via the MCP tools, but a user editing through
    the iris-web vault page is sandboxed to their own folder.

    A NULL ``vault_subdir`` (legacy / Claude Desktop) means "no
    scoping context"; we refuse the write rather than silently
    letting anyone touch anything. Web sessions always carry a
    speaker user_id with a vault_subdir, so this only affects
    pathological callers.

    Returns False on any escape attempt (``..``-style paths must be
    resolved + checked by the endpoint BEFORE calling this).
    """
    if not vault_subdir or not path:
        return False
    subdir = vault_subdir.rstrip("/")
    return path == subdir or path.startswith(f"{subdir}/")


def event_visible_to_user(
    event_row,
    vault_subdir: Optional[str],
    user_discord_id: Optional[str],
) -> bool:
    """Should this event appear in a given user's brief / agenda?

    Returns True when:
      - There's no scoping context (vault_subdir is None) — legacy /
        Claude Desktop / CLI: see everything.
      - Event lives in the user's own ``users/<id>/`` folder.
      - Event lives outside any ``users/`` prefix (vault root → shared
        with everyone).
      - Event is in another user's folder BUT ``user_discord_id`` is
        in the comma-separated ``attendees`` column (v24 share_with
        opt-in).

    Hidden otherwise.

    ``event_row`` accepts a dict-like (sqlite3.Row works; tests can
    pass plain dicts). Missing ``attendees`` / ``note_path`` fields
    default to empty / shared (defensive — better to show than hide).
    """
    if vault_subdir is None:
        return True
    try:
        path = event_row["note_path"]
    except (KeyError, IndexError, TypeError):
        path = event_row.get("note_path", "") if hasattr(event_row, "get") else ""
    path = (path or "").strip()
    if not path:
        return True
    if path.startswith(f"{vault_subdir.rstrip('/')}/"):
        return True
    if not path.startswith("users/"):
        return True  # shared at vault root
    # Path is under a DIFFERENT user's folder. Check attendee opt-in.
    try:
        attendees = event_row["attendees"]
    except (KeyError, IndexError, TypeError):
        attendees = (
            event_row.get("attendees", "") if hasattr(event_row, "get") else ""
        )
    attendees = (attendees or "").strip()
    if not attendees or not user_discord_id:
        return False
    ids = [a.strip() for a in attendees.split(",") if a.strip()]
    return str(user_discord_id) in ids


def assert_can_access_paths(
    speaker_uid: Optional[int],
    paths: list,
) -> Optional[str]:
    """Convenience: assert_can_access_path applied to every path in a list.

    Used by multi-path tools (move_files, copy_files, delete_files,
    etc.). Returns the first denial encountered or None when all paths
    pass. Skips empty / None entries silently — the underlying tool
    handles those.
    """
    for p in paths or []:
        if not p:
            continue
        denial = assert_can_access_path(speaker_uid, p)
        if denial:
            return denial
    return None


# ── Discord snowflake precision-safe coercion ──────────────────────────────
# Discord channel / message / user IDs are 64-bit ("snowflake") integers.
# IEEE-754 doubles — which is how JSON numbers travel through the LLM's
# tool-call serialization — have a 53-bit mantissa, so any snowflake past
# 2^53 (= 9_007_199_254_740_992 ≈ 9 quadrillion) gets silently rounded on
# the wire. A typical Discord channel ID is 18–19 digits, well past that.
#
# Concrete failure mode: Iris is told "send to channel 1505288351090217192",
# emits JSON `{"channel_id": 1505288351090217192}` as a number, but by the
# time the MCP tool sees it the value is `1505288351090217200`. Off by 8 —
# enough to point at a non-existent or wrong channel.
#
# Fix: tools accept ``int | str | None`` for channel_id. Strings travel
# through the LLM as exact digit sequences (no float conversion), and
# Python's ``int(str_value)`` is lossless. The system prompt instructs
# Iris to always pass channel IDs as strings — but we coerce defensively
# either way so a stray int still works for IDs small enough to survive.


def coerce_channel_id(value) -> Optional[int]:
    """Coerce a JSON-supplied Discord channel ID to a precision-safe int.

    Accepts:
      - ``int`` — used as-is (may have already lost precision if it came
        in as a JSON number, but we can't recover what's gone).
      - ``str`` — parsed via ``int(str)``, lossless for any digit length.
      - ``None`` / ``0`` / ``""`` — cleared (returns ``None``).

    Returns ``None`` for any unparseable input rather than raising — tools
    treat None as "no channel specified, fall back to env / per-user
    defaults" and that's the safer behaviour than blowing up.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        if not v or v == "0":
            return None
        try:
            return int(v)
        except ValueError:
            return None
    if isinstance(value, int):
        return None if value == 0 else value
    # Anything else (float, list, dict) — silently None.
    return None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def relative_to_vault(path: Path) -> str:
    # NFC-normalize to match wikilink text (macOS APFS stores filenames in NFD)
    return unicodedata.normalize("NFC", str(path.relative_to(get_vault_root())).replace("\\", "/"))


def today_iso() -> str:
    return datetime.now().date().isoformat()


# -- Natural-language date resolution -----------------------------------------

_WEEKDAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def resolve_natural_date(text: str) -> str | None:
    """
    Resolve a natural-language date expression to YYYY-MM-DD.

    Supports: "today", "tomorrow", "yesterday",
              "next monday", "this friday", "monday",
              "in 3 days", "in 2 weeks",
              "end of month", "end of week",
              or a literal "YYYY-MM-DD" passthrough.

    Returns None if the text can't be parsed.
    """
    s = text.strip().lower()
    today = datetime.now().date()

    # Passthrough ISO date
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s

    # Simple keywords
    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if s == "yesterday":
        return (today - timedelta(days=1)).isoformat()

    # "in N days/weeks/months"
    m = re.match(r"in\s+(\d+)\s+(day|days|week|weeks|month|months)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("day"):
            return (today + timedelta(days=n)).isoformat()
        elif unit.startswith("week"):
            return (today + timedelta(weeks=n)).isoformat()
        elif unit.startswith("month"):
            # Approximate: add n*30 days
            year = today.year
            month = today.month + n
            while month > 12:
                month -= 12
                year += 1
            day = min(today.day, calendar.monthrange(year, month)[1])
            return f"{year:04d}-{month:02d}-{day:02d}"

    # "end of week" (next Sunday)
    if s in ("end of week", "eow"):
        days_until_sunday = (6 - today.weekday()) % 7 or 7
        return (today + timedelta(days=days_until_sunday)).isoformat()

    # "end of month" / "eom"
    if s in ("end of month", "eom"):
        last_day = calendar.monthrange(today.year, today.month)[1]
        return f"{today.year:04d}-{today.month:02d}-{last_day:02d}"

    # "next <weekday>" — the NEXT occurrence (skips this week if today)
    m = re.match(r"(?:next\s+)?(\w+)", s)
    if m:
        day_name = m.group(1)
        if day_name in _WEEKDAY_NAMES:
            target_wd = _WEEKDAY_NAMES[day_name]
            current_wd = today.weekday()
            if "next" in s:
                # Always go to next week's occurrence
                delta = (target_wd - current_wd) % 7
                if delta == 0:
                    delta = 7
            else:
                # "monday" / "this monday" — nearest future
                delta = (target_wd - current_wd) % 7
                if delta == 0:
                    delta = 7  # if today is Monday, "monday" means next Monday
            return (today + timedelta(days=delta)).isoformat()

    return None


def _resolve_date_range(text: str) -> tuple[str, int] | None:
    """
    Parse a natural-language *range* expression into (start_iso, num_days).

    Returns None when the text is a single-date expression (let
    ``resolve_natural_date`` handle those).
    """
    s = text.strip().lower()
    today = datetime.now().date()

    # ── this week / next week ───────────────────────────────────────────
    if s in ("this week", "week", "rest of week", "rest of the week"):
        days_left = 7 - today.weekday()           # Mon=0 → 7, Sun=6 → 1
        return (today.isoformat(), days_left)

    if s == "next week":
        next_monday = today + timedelta(days=(7 - today.weekday()))
        return (next_monday.isoformat(), 7)

    # ── this month / next month ─────────────────────────────────────────
    if s in ("this month", "month", "rest of month", "rest of the month"):
        last_day = calendar.monthrange(today.year, today.month)[1]
        days_left = last_day - today.day + 1
        return (today.isoformat(), days_left)

    if s == "next month":
        m = today.month + 1
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        first = today.replace(year=y, month=m, day=1)
        return (first.isoformat(), calendar.monthrange(y, m)[1])

    # ── "next N days/weeks/months" ──────────────────────────────────────
    match = re.match(r"next\s+(\d+)\s+(day|days|week|weeks|month|months)", s)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("day"):
            return (today.isoformat(), n)
        if unit.startswith("week"):
            return (today.isoformat(), n * 7)
        if unit.startswith("month"):
            return (today.isoformat(), n * 30)

    # ── bare "N days / N weeks" ─────────────────────────────────────────
    match = re.match(r"(\d+)\s+(day|days|week|weeks|month|months)", s)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("day"):
            return (today.isoformat(), n)
        if unit.startswith("week"):
            return (today.isoformat(), n * 7)
        if unit.startswith("month"):
            return (today.isoformat(), n * 30)

    return None


IGNORED_VAULT_PARTS = {
    ".obsidian",
    ".ai_memory_jobs",
    ".ai_memory_cache",
    ".git",
    "_trash",  # 90_Inbox/_trash/ — visible vault trash
}


def is_ignored_path(path: Path) -> bool:
    return any(part in IGNORED_VAULT_PARTS for part in path.parts)


# =============================================================================
# Generic file support: indexing, reading, writing, moving, deleting
# =============================================================================


ALLOWED_VAULT_FILE_EXTENSIONS = {
    ".canvas",
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".svg",
    ".excalidraw",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".zip",
}


TEXT_INDEXABLE_EXTENSIONS = {
    ".canvas",
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".svg",
    ".excalidraw",
}


OPTIONAL_TEXT_EXTRACT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
}


def vault_suffix(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".excalidraw.md"):
        return ".excalidraw.md"
    return path.suffix.lower()


def ensure_allowed_vault_file(path: Path) -> None:
    if is_ignored_path(path):
        raise ValueError("Refusing to access ignored/internal vault path.")

    suffix = vault_suffix(path)
    if suffix == ".excalidraw.md":
        return

    if suffix not in ALLOWED_VAULT_FILE_EXTENSIONS:
        allowed = sorted(ALLOWED_VAULT_FILE_EXTENSIONS | {".excalidraw.md"})
        raise ValueError(
            f"Refusing unsupported file type: {suffix or '(no extension)'}. "
            f"Allowed extensions: {', '.join(allowed)}"
        )


def is_text_indexable_file(path: Path) -> bool:
    suffix = vault_suffix(path)
    return (
        suffix == ".excalidraw.md"
        or suffix in TEXT_INDEXABLE_EXTENSIONS
        or suffix in OPTIONAL_TEXT_EXTRACT_EXTENSIONS
    )


def all_vault_files(
    include_binary: bool = True,
    include_indexable_only: bool = False,
    folder: str = "",
) -> list[Path]:
    root = get_vault_root()
    base = safe_path(folder) if folder.strip() else root

    if not base.exists():
        return []

    candidates = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
    files: list[Path] = []

    for path in candidates:
        if is_ignored_path(path):
            continue
        try:
            ensure_allowed_vault_file(path)
        except ValueError:
            continue
        if include_indexable_only and not is_text_indexable_file(path):
            continue
        if not include_binary and not is_text_indexable_file(path):
            continue
        files.append(path)

    files.sort(key=lambda p: relative_to_vault(p).lower())
    return files


def compact_snippet(text: str, query_terms: list[str], max_chars: int = 500) -> str:
    lower = text.lower()
    first_pos: Optional[int] = None

    for term in query_terms:
        pos = lower.find(term.lower())
        if pos >= 0:
            first_pos = pos if first_pos is None else min(first_pos, pos)

    if first_pos is None:
        snippet = text[:max_chars]
    else:
        start = max(0, first_pos - max_chars // 3)
        snippet = text[start : start + max_chars]

    return re.sub(r"\s+", " ", snippet).strip()


def read_pdf_text(path: Path, max_chars: int) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        return (
            "[PDF text extraction unavailable. Install with `pip install pypdf`. "
            f"Import error: {exc}]"
        )

    try:
        reader = PdfReader(str(path))
        chunks: list[str] = []
        total = 0
        for page in reader.pages:
            if total >= max_chars:
                break
            txt = page.extract_text() or ""
            chunks.append(txt)
            total += len(txt)
        return "\n\n".join(chunks).strip()[:max_chars]
    except Exception as exc:
        return f"[PDF text extraction failed: {exc}]"


def read_docx_text(path: Path, max_chars: int) -> str:
    try:
        import docx
    except Exception as exc:
        return (
            "[DOCX text extraction unavailable. Install with `pip install python-docx`. "
            f"Import error: {exc}]"
        )

    try:
        doc = docx.Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs).strip()
        return text[:max_chars]
    except Exception as exc:
        return f"[DOCX text extraction failed: {exc}]"


def read_xlsx_text(path: Path, max_chars: int) -> str:
    try:
        import openpyxl
    except Exception as exc:
        return (
            "[XLSX text extraction unavailable. Install with `pip install openpyxl`. "
            f"Import error: {exc}]"
        )

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        chunks: list[str] = []
        total = 0
        for sheet in wb.worksheets:
            if total >= max_chars:
                break
            header = f"# Sheet: {sheet.title}"
            chunks.append(header)
            total += len(header)
            for row in sheet.iter_rows(values_only=True):
                if total >= max_chars:
                    break
                values = ["" if v is None else str(v) for v in row]
                if any(v.strip() for v in values):
                    line = "\t".join(values)
                    chunks.append(line)
                    total += len(line)
        return "\n".join(chunks).strip()[:max_chars]
    except Exception as exc:
        return f"[XLSX text extraction failed: {exc}]"


def read_indexable_file_text(path: Path, max_chars: int = 50000) -> str:
    ensure_allowed_vault_file(path)
    max_chars = max(1000, min(max_chars, 200000))
    suffix = vault_suffix(path)

    if suffix == ".excalidraw.md" or suffix in TEXT_INDEXABLE_EXTENSIONS:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]

    if suffix == ".pdf":
        return read_pdf_text(path, max_chars)

    if suffix == ".docx":
        return read_docx_text(path, max_chars)

    if suffix == ".xlsx":
        return read_xlsx_text(path, max_chars)

    stat = path.stat()
    return (
        "[Binary/non-text file]\n"
        f"Name: {path.name}\n"
        f"Type: {suffix or '(no extension)'}\n"
        f"Size bytes: {stat.st_size}\n"
        f"Modified: {datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds')}\n"
    )


def title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def file_title(path: Path, text: str = "") -> str:
    suffix = vault_suffix(path)

    if suffix in {".md", ".excalidraw.md"}:
        return title_from_text(text, path.stem)

    if suffix in {".json", ".excalidraw"}:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                for key in ("title", "name"):
                    if key in data and str(data[key]).strip():
                        return str(data[key]).strip()
        except Exception:
            pass

    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def score_vault_file(path: Path, text: str, terms: list[str], root: Path) -> int:
    rel = str(path.relative_to(root)).replace("\\", "/").lower()
    name = path.name.lower()
    lower = text.lower()
    score = 0

    for term in terms:
        t = term.lower().strip()
        if not t:
            continue
        if t in name:
            score += 80
        if t in rel:
            score += 40
        score += lower.count(t) * 10
        for line in lower.splitlines()[:2000]:
            if t in line:
                if line.startswith("#"):
                    score += 25
                if "tags:" in line or "type:" in line or "status:" in line:
                    score += 10

    if is_text_indexable_file(path):
        score += 3
    return score



# ─── from original L1229-1455: Markdown link helpers + Frontmatter helpers ───
# =============================================================================
# Markdown / Obsidian link helpers
# =============================================================================


def count_words(text: str) -> int:
    return len(re.findall(r"\b\S+\b", text))


def normalize_note_target(path: str) -> str:
    p = path.strip().replace("\\", "/")
    if p.endswith(".md"):
        p = p[:-3]
    return unicodedata.normalize("NFC", p.strip("/"))


def make_wikilink(target_path: str, display_text: Optional[str] = None,
                  table_safe: bool = True) -> str:
    target = normalize_note_target(target_path)
    # Use \| instead of | so the link is safe inside Markdown tables.
    # Obsidian treats [[path\|display]] and [[path|display]] identically.
    sep = r"\|" if table_safe else "|"
    if display_text and display_text.strip():
        return f"[[{target}{sep}{display_text.strip()}]]"
    # Auto-derive display text from the path to keep links human-readable.
    # e.g. "60_Knowledge/Computer_Science/Finite Automata" → "Finite Automata"
    basename = target.rsplit("/", 1)[-1] if "/" in target else target
    # Strip common extensions that might remain
    for ext in (".excalidraw", ):
        if basename.endswith(ext):
            basename = basename[: -len(ext)]
    if basename != target:
        return f"[[{target}{sep}{basename}]]"
    return f"[[{target}]]"


def extract_wikilinks(text: str) -> list[dict[str, str]]:
    # Match both [[path|display]] and [[path\|display]] (table-safe escaped pipe)
    pattern = re.compile(r"\[\[([^\]|#]+(?:#[^\]|]+)?)(?:\\?\|([^\]]+))?\]\]")
    links: list[dict[str, str]] = []
    for match in pattern.finditer(text):
        raw_target = match.group(1).strip().rstrip("\\")  # strip trailing \ from table-safe links
        display = match.group(2).strip() if match.group(2) else ""
        note_target = raw_target.split("#", 1)[0].strip()
        links.append(
            {
                "raw": match.group(0),
                "target": raw_target,
                "note_target": note_target,
                "display_text": display,
            }
        )
    return links


def note_target_to_relative_md(target: str) -> str:
    clean = target.strip().replace("\\", "/").split("#", 1)[0].strip("/")
    if not clean.endswith(".md"):
        clean += ".md"
    return clean


def find_section_bounds(text: str, section: str) -> tuple[int, int] | None:
    escaped = re.escape(section.strip())
    heading_re = re.compile(rf"^(?P<hashes>#+)\s+{escaped}\s*$", re.MULTILINE)
    match = heading_re.search(text)
    if not match:
        return None

    level = len(match.group("hashes"))
    start = match.start()
    next_heading_re = re.compile(r"^(?P<hashes>#+)\s+.+$", re.MULTILINE)
    for next_match in next_heading_re.finditer(text, match.end()):
        next_level = len(next_match.group("hashes"))
        if next_level <= level:
            return (start, next_match.start())
    return (start, len(text))


def ensure_section(text: str, section: str) -> str:
    if find_section_bounds(text, section) is not None:
        return text
    return text.rstrip() + f"\n\n## {section}\n\n"


def append_bullet_to_section(text: str, section: str, bullet: str) -> str:
    text = ensure_section(text, section)
    bounds = find_section_bounds(text, section)
    if bounds is None:
        return text.rstrip() + f"\n\n## {section}\n\n{bullet}\n"
    start, end = bounds
    section_text = text[start:end].rstrip()
    if bullet in section_text:
        return text
    section_text += f"\n{bullet}"
    return text[:start] + section_text + "\n" + text[end:]


def append_unique_line_to_section(text: str, section: str, line: str) -> str:
    text = ensure_section(text, section)
    bounds = find_section_bounds(text, section)
    if bounds is None:
        return text.rstrip() + f"\n\n## {section}\n\n{line}\n"
    start, end = bounds
    section_text = text[start:end].rstrip()
    if line in section_text:
        return text
    section_text += f"\n{line}"
    return text[:start] + section_text + "\n" + text[end:]


def append_table_row_to_section(text: str, section: str, header: str, separator: str, row: str) -> str:
    text = ensure_section(text, section)
    bounds = find_section_bounds(text, section)
    if bounds is None:
        return text.rstrip() + f"\n\n## {section}\n\n{header}\n{separator}\n{row}\n"
    start, end = bounds
    section_text = text[start:end].rstrip()
    if header not in section_text:
        section_text += f"\n\n{header}\n{separator}\n{row}"
    elif row not in section_text:
        section_text += f"\n{row}"
    return text[:start] + section_text + "\n" + text[end:]


def escape_table_cell(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value.replace("|", "\\|")


# =============================================================================
# Frontmatter helpers
# =============================================================================


SAFE_FRONTMATTER_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---", 4)
    if end == -1:
        return {}, text

    raw = text[4:end]
    body = text[end + len("\n---"):]
    if body.startswith("\n"):
        body = body[1:]

    data: dict[str, object] = {}
    current_key: str | None = None

    for line in raw.splitlines():
        if not line.strip():
            continue

        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(line.strip()[2:].strip().strip('"').strip("'"))
            continue

        if ":" not in line:
            current_key = None
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key

        if not SAFE_FRONTMATTER_KEY_RE.match(key):
            continue

        if value == "":
            data[key] = []
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if inner:
                data[key] = [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
            else:
                data[key] = []
        else:
            data[key] = value.strip('"').strip("'")
            current_key = None

    return data, body


def dump_frontmatter(data: dict[str, object], body: str) -> str:
    lines = ["---"]
    for key in sorted(data.keys()):
        if not SAFE_FRONTMATTER_KEY_RE.match(key):
            continue
        value = data[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                s = str(item).strip()
                if s:
                    lines.append(f"  - {s}")
        elif value is None:
            continue
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.lstrip("\n")


def unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = str(item).strip()
        if clean and clean not in seen:
            out.append(clean)
            seen.add(clean)
    return out


def note_has_frontmatter(text: str) -> bool:
    return text.startswith("---\n") and "\n---" in text[4:]



# ─── from original L5593-7229: VaultIndex class ───
# =============================================================================
# SQLite vault DB — mixed ground-truth model
# =============================================================================


class VaultIndex:
    """
    SQLite-backed database for the Obsidian vault.

    Ground-truth split:

    - **Vault index tables** (below) are *derived* from the Markdown files.
      They can be rebuilt at any time by re-walking the vault.
    - **Domain tables** (``meals``, ``weights``, ``training_sessions``,
      ``habits``, ``habit_logs``, ``health_profile``, ``injuries``,
      ``skill_goals``, ``warranties``, ``anime_list``, ``vocab``,
      ``people``) are *ground truth* — there is no ``.md`` counterpart to
      regenerate them from. Surfaced into Obsidian via live SQL view
      blocks (rendered by the SQLite-DB plugin on desktop, or pre-rendered
      into markdown tables by ``refresh_sql_views`` for mobile).

    Implication: deleting ``vault.db`` rebuilds the index half but
    *loses* the domain half. Use ``vault_snapshot()`` to back it up.

    The DB lives at ``<vault>/.ai_memory_cache/vault.db`` (ignored by
    Obsidian and the MCP tool's own ``is_ignored_path`` check).

    Derived-from-markdown tables
    ----------------------------
    files      – every allowed vault file (path, suffix, size, mtime_ns, content_hash)
    notes      – Markdown-specific metadata (title, type, status, word_count)
    frontmatter – key/value pairs extracted from YAML front matter
    tags       – per-note tags (from frontmatter ``tags`` list)
    aliases    – per-note aliases (from frontmatter ``aliases`` list)
    wikilinks  – directed edges (source → target, with display text)
    tasks      – task bullets from ``## Tasks`` sections
    reminders  – reminder bullets from ``## Reminders`` sections
    events     – calendar events from ``## Schedule`` sections
    fts        – FTS5 full-text search over note body text
    """

    SCHEMA_VERSION = 32

    def __init__(self, vault_root: Path):
        self._root = vault_root
        cache_dir = vault_root / ".ai_memory_cache"
        cache_dir.mkdir(exist_ok=True)
        self._db_path = cache_dir / "vault.db"
        self._conn: sqlite3.Connection | None = None

    # -- connection management ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        # check_same_thread=False is required because we share one connection
        # across the main asyncio loop AND any worker threads that asyncio.
        # to_thread() spins up (notably the SQL-view refresh loop, the habit
        # reminder scanner, and Whisper/Edge TTS synth callbacks). The default
        # `check_same_thread=True` is conservative for SQLite — actual
        # concurrent access is safe in WAL mode + a 10s busy timeout as long
        # as no two writers race. Our writers are externally serialized:
        # MCP tools run sequentially within the parent process and the
        # background loops we have only READ, so we satisfy that contract.
        # Bug surfaced as: "SQLite objects created in a thread can only be
        # used in that same thread" when the background SQL view refresh
        # was scheduled via asyncio.to_thread.
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=10,
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._ensure_schema()
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._connect()

    @property
    def db_path(self) -> Path:
        """Public read-only accessor for the on-disk SQLite path.

        Used by tools that want to open a separate connection (e.g.
        ``embed_query`` opens its own short-lived connection so it doesn't
        share state with the long-lived index connection). Also used by the
        bot's snapshot loop to compute the snapshot filename next to the
        live DB."""
        return self._db_path

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- schema ---------------------------------------------------------------

    def _ensure_schema(self):
        c = self._conn
        assert c is not None

        # Check schema version
        c.execute(
            "CREATE TABLE IF NOT EXISTS _meta "
            "(key TEXT PRIMARY KEY, value TEXT)"
        )
        row = c.execute(
            "SELECT value FROM _meta WHERE key='schema_version'"
        ).fetchone()
        current = int(row["value"]) if row else 0

        if current < 3:
            # Fresh install or pre-v3 — full rebuild
            for tbl in [
                "fts", "events", "tasks", "reminders", "wikilinks",
                "aliases", "tags", "frontmatter", "notes", "files",
                "note_access", "revisions",
            ]:
                c.execute(f"DROP TABLE IF EXISTS {tbl}")
            c.execute("DELETE FROM _meta")

        # -- files: all vault files
        c.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path       TEXT PRIMARY KEY,
                suffix     TEXT NOT NULL,
                size       INTEGER NOT NULL,
                mtime_ns   INTEGER NOT NULL,
                content_hash TEXT NOT NULL DEFAULT ''
            )
        """)

        # -- notes: Markdown-specific enrichment
        c.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                path           TEXT PRIMARY KEY REFERENCES files(path) ON DELETE CASCADE,
                title          TEXT NOT NULL DEFAULT '',
                type           TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT '',
                word_count     INTEGER NOT NULL DEFAULT 0,
                summary        TEXT NOT NULL DEFAULT '',
                summary_source TEXT NOT NULL DEFAULT ''
            )
        """)
        # v5 additive migration: add summary columns to existing tables
        for col, typedef in [("summary", "TEXT NOT NULL DEFAULT ''"),
                             ("summary_source", "TEXT NOT NULL DEFAULT ''")]:
            try:
                c.execute(f"ALTER TABLE notes ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists

        # -- frontmatter key/value
        c.execute("""
            CREATE TABLE IF NOT EXISTS frontmatter (
                note_path  TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                PRIMARY KEY (note_path, key, value)
            )
        """)

        # -- tags
        c.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                note_path  TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
                tag        TEXT NOT NULL,
                PRIMARY KEY (note_path, tag)
            )
        """)

        # -- aliases
        c.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                note_path  TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
                alias      TEXT NOT NULL,
                PRIMARY KEY (note_path, alias)
            )
        """)

        # -- wikilinks (source → target)
        c.execute("""
            CREATE TABLE IF NOT EXISTS wikilinks (
                source_path   TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
                target        TEXT NOT NULL,
                display_text  TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (source_path, target, display_text)
            )
        """)

        # -- tasks from ## Tasks sections
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                note_path  TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
                text       TEXT NOT NULL,
                checked    INTEGER NOT NULL DEFAULT 0,
                due        TEXT NOT NULL DEFAULT '',
                priority   TEXT NOT NULL DEFAULT '',
                done       TEXT NOT NULL DEFAULT ''
            )
        """)

        # -- reminders from ## Reminders sections
        c.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                note_path   TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
                text        TEXT NOT NULL,
                checked     INTEGER NOT NULL DEFAULT 0,
                remind_on   TEXT NOT NULL DEFAULT '',
                repeat      TEXT NOT NULL DEFAULT '',
                done        TEXT NOT NULL DEFAULT ''
            )
        """)

        # -- events from ## Schedule sections (calendar/agenda)
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                note_path   TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
                date        TEXT NOT NULL DEFAULT '',
                time        TEXT NOT NULL DEFAULT '',
                end_time    TEXT NOT NULL DEFAULT '',
                title       TEXT NOT NULL,
                location    TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                end_date    TEXT NOT NULL DEFAULT '',
                all_day     INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Additive migration: add end_date/all_day columns to existing tables
        # v24: ``attendees`` carries a comma-separated list of Discord IDs
        # for users with whom this event is shared (in addition to the
        # path-implied owner). Lets a private-folder event still surface
        # in named family members' briefs — "Hyun-Min is busy 14:00–15:00,
        # share with mom so she knows" without making it a full-shared
        # vault-root event that everyone sees.
        for col, typedef in [("end_date",  "TEXT NOT NULL DEFAULT ''"),
                             ("all_day",   "INTEGER NOT NULL DEFAULT 0"),
                             ("attendees", "TEXT NOT NULL DEFAULT ''")]:
            try:
                c.execute(f"ALTER TABLE events ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists

        # -- FTS5 full-text search (body text of Markdown notes)
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
                path, title, body,
                tokenize='unicode61 remove_diacritics 2'
            )
        """)

        # -- v4: hotness / access tracking
        c.execute("""
            CREATE TABLE IF NOT EXISTS note_access (
                path           TEXT PRIMARY KEY,
                access_count   INTEGER NOT NULL DEFAULT 0,
                last_accessed  TEXT NOT NULL DEFAULT ''
            )
        """)

        # -- v4: revision history
        c.execute("""
            CREATE TABLE IF NOT EXISTS revisions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                path           TEXT NOT NULL,
                content        TEXT NOT NULL,
                content_hash   TEXT NOT NULL DEFAULT '',
                saved_at       TEXT NOT NULL DEFAULT '',
                word_count     INTEGER NOT NULL DEFAULT 0
            )
        """)

        # -- v6+: anime list mirror, synced with MAL. The DB is the source of truth;
        # the Anime hub markdown is a live view via the SQLite DB plugin.
        c.execute("""
            CREATE TABLE IF NOT EXISTS anime_list (
                mal_id        INTEGER PRIMARY KEY,
                title         TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT '',
                score         INTEGER,
                start_date    TEXT NOT NULL DEFAULT '',
                end_date      TEXT NOT NULL DEFAULT '',
                eps_watched   INTEGER,
                eps_total     INTEGER,
                priority      TEXT NOT NULL DEFAULT '',
                note          TEXT NOT NULL DEFAULT '',
                updated_at    TEXT NOT NULL DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_anime_status ON anime_list(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_anime_title ON anime_list(title COLLATE NOCASE)")

        # Pre-aggregated views for chart blocks (the SQLite DB plugin can't COUNT(*)
        # itself — it just sums valueColumn raw, so we need pre-grouped views).
        c.execute("""
            CREATE VIEW IF NOT EXISTS anime_status_counts AS
            SELECT status, COUNT(*) AS cnt
            FROM anime_list
            GROUP BY status
        """)
        c.execute("""
            CREATE VIEW IF NOT EXISTS anime_score_counts AS
            SELECT score, COUNT(*) AS cnt
            FROM anime_list
            WHERE score IS NOT NULL
            GROUP BY score
            ORDER BY score
        """)
        # Range views — the plugin only does equality filters, so for range queries
        # ("completed score < 7", etc.) we pre-filter via views.
        c.execute("""
            CREATE VIEW IF NOT EXISTS anime_completed_below_7 AS
            SELECT * FROM anime_list
            WHERE status = 'completed' AND score IS NOT NULL AND score < 7
        """)
        c.execute("""
            CREATE VIEW IF NOT EXISTS anime_completed_unrated AS
            SELECT * FROM anime_list
            WHERE status = 'completed' AND score IS NULL
        """)

        # Pending holding-pen no longer used (markdown isn't parsed anymore).
        c.execute("DROP TABLE IF EXISTS anime_list_pending")

        # -- v8: vocabulary (Japanese + Korean + future languages)
        c.execute("""
            CREATE TABLE IF NOT EXISTS vocab (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                language      TEXT NOT NULL,
                word          TEXT NOT NULL,
                reading       TEXT NOT NULL DEFAULT '',
                meaning       TEXT NOT NULL DEFAULT '',
                category      TEXT NOT NULL DEFAULT '',
                source        TEXT NOT NULL DEFAULT '',
                note          TEXT NOT NULL DEFAULT '',
                last_reviewed TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL DEFAULT '',
                updated_at    TEXT NOT NULL DEFAULT '',
                UNIQUE(language, word)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_vocab_lang ON vocab(language)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_vocab_word ON vocab(word)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_vocab_category ON vocab(category)")
        # v10: spaced repetition (SM-2). Additive — existing entries default to
        # "due immediately" so they show up in the first review session.
        for col, typedef in [
            ("interval_days", "INTEGER NOT NULL DEFAULT 0"),
            ("ease_factor", "REAL NOT NULL DEFAULT 2.5"),
            ("reps", "INTEGER NOT NULL DEFAULT 0"),
            ("lapses", "INTEGER NOT NULL DEFAULT 0"),
            ("due_at", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                c.execute(f"ALTER TABLE vocab ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists
        c.execute("CREATE INDEX IF NOT EXISTS idx_vocab_due ON vocab(due_at)")

        # -- v8: people (family, friends, colleagues — anyone with birthday/contact info)
        # v12: added occupation/employer/team/nicknames/email/phone/socials
        # columns. These used to be crammed into `note` ("AI Research Intern",
        # "Nicknames: Bu, Schwabebe") which made them unqueryable. The
        # migration below is additive (ALTER TABLE ADD COLUMN) so existing
        # rows keep their `note` content untouched — Iris can move it to the
        # structured columns over time as people get edited.
        c.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT NOT NULL,
                category       TEXT NOT NULL DEFAULT '',
                subcategory    TEXT NOT NULL DEFAULT '',
                relationship   TEXT NOT NULL DEFAULT '',
                birthday_day   INTEGER,
                birthday_month INTEGER,
                birthday_year  INTEGER,
                location       TEXT NOT NULL DEFAULT '',
                badge          TEXT NOT NULL DEFAULT '',
                note           TEXT NOT NULL DEFAULT '',
                page_link      TEXT NOT NULL DEFAULT '',
                created_at     TEXT NOT NULL DEFAULT '',
                updated_at     TEXT NOT NULL DEFAULT '',
                UNIQUE(name)
            )
        """)
        # v12 additive migration: structured contact + employment columns.
        # Try/except per column because SQLite has no ALTER TABLE ADD COLUMN
        # IF NOT EXISTS — just swallow the duplicate-column error on re-runs.
        for col, typedef in [
            ("occupation", "TEXT NOT NULL DEFAULT ''"),  # job title (e.g. "AI Research Intern")
            ("employer",   "TEXT NOT NULL DEFAULT ''"),  # company / institution (e.g. "Huawei")
            ("team",       "TEXT NOT NULL DEFAULT ''"),  # team within employer (e.g. "Algorithm Team")
            ("nicknames",  "TEXT NOT NULL DEFAULT ''"),  # comma-separated, e.g. "Bu, Schwabebe"
            ("email",      "TEXT NOT NULL DEFAULT ''"),
            ("phone",      "TEXT NOT NULL DEFAULT ''"),
            ("socials",    "TEXT NOT NULL DEFAULT ''"),  # comma-separated, e.g. "@handle on IG, discord:foo#123"
        ]:
            try:
                c.execute(f"ALTER TABLE people ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists
        c.execute("CREATE INDEX IF NOT EXISTS idx_people_category ON people(category)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_people_bday ON people(birthday_month, birthday_day)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_people_employer ON people(employer)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_people_occupation ON people(occupation)")

        # View: upcoming birthdays — recreated each startup so the name_link
        # column (wikilink markup, picked up by the sqlite-db-companion plugin)
        # stays in sync with schema changes.
        c.execute("DROP VIEW IF EXISTS people_upcoming_birthdays")
        c.execute("""
            CREATE VIEW people_upcoming_birthdays AS
            SELECT
                name, category, relationship, birthday_day, birthday_month, birthday_year,
                CAST(strftime('%Y','now') AS INTEGER) AS this_year,
                printf('%04d-%02d-%02d',
                    CASE
                        WHEN strftime('%m-%d','now') > printf('%02d-%02d', birthday_month, birthday_day)
                        THEN CAST(strftime('%Y','now') AS INTEGER) + 1
                        ELSE CAST(strftime('%Y','now') AS INTEGER)
                    END,
                    birthday_month, birthday_day
                ) AS next_birthday,
                CAST(
                    julianday(printf('%04d-%02d-%02d',
                        CASE
                            WHEN strftime('%m-%d','now') > printf('%02d-%02d', birthday_month, birthday_day)
                            THEN CAST(strftime('%Y','now') AS INTEGER) + 1
                            ELSE CAST(strftime('%Y','now') AS INTEGER)
                        END,
                        birthday_month, birthday_day
                    )) - julianday('now') AS INTEGER
                ) AS days_until,
                CASE
                    WHEN page_link != '' AND page_link IS NOT NULL
                    THEN '[[' || page_link || '|' || name || ']]'
                    ELSE name
                END AS name_link
            FROM people
            WHERE birthday_month IS NOT NULL AND birthday_day IS NOT NULL
            ORDER BY days_until
        """)

        # View: all people, with a name_link column that resolves to an internal
        # wikilink when page_link is set. The sqlite-db-companion plugin parses
        # these `[[…]]` strings into clickable internal-link anchors at render.
        c.execute("DROP VIEW IF EXISTS people_linked")
        c.execute("""
            CREATE VIEW people_linked AS
            SELECT
                id, name, category, subcategory, relationship,
                birthday_day, birthday_month, birthday_year,
                location, badge, note, page_link, created_at, updated_at,
                CASE
                    WHEN page_link != '' AND page_link IS NOT NULL
                    THEN '[[' || page_link || '|' || name || ']]'
                    ELSE name
                END AS name_link
            FROM people
        """)

        # -- v8: warranties (purchased items with warranty expiry)
        c.execute("""
            CREATE TABLE IF NOT EXISTS warranties (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                product         TEXT NOT NULL,
                warranty_until  TEXT NOT NULL DEFAULT '',
                purchase_date   TEXT NOT NULL DEFAULT '',
                receipt_path    TEXT NOT NULL DEFAULT '',
                vendor          TEXT NOT NULL DEFAULT '',
                price           TEXT NOT NULL DEFAULT '',
                note            TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL DEFAULT '',
                UNIQUE(product, receipt_path)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_warranty_until ON warranties(warranty_until)")
        # Warranties views — recreated each startup so product_link stays in
        # sync. product_link wraps the product name in a wikilink that points
        # to the receipt PDF (or any path stored in receipt_path).
        c.execute("DROP VIEW IF EXISTS warranties_active")
        c.execute("""
            CREATE VIEW warranties_active AS
            SELECT *,
                CAST(julianday(warranty_until) - julianday('now') AS INTEGER) AS days_left,
                CASE
                    WHEN receipt_path != '' AND receipt_path IS NOT NULL
                    THEN '[[' || receipt_path || '|' || product || ']]'
                    ELSE product
                END AS product_link
            FROM warranties
            WHERE warranty_until != '' AND julianday(warranty_until) >= julianday('now')
            ORDER BY warranty_until
        """)
        c.execute("DROP VIEW IF EXISTS warranties_expired")
        c.execute("""
            CREATE VIEW warranties_expired AS
            SELECT *,
                CAST(julianday('now') - julianday(warranty_until) AS INTEGER) AS days_since_expiry,
                CASE
                    WHEN receipt_path != '' AND receipt_path IS NOT NULL
                    THEN '[[' || receipt_path || '|' || product || ']]'
                    ELSE product
                END AS product_link
            FROM warranties
            WHERE warranty_until != '' AND julianday(warranty_until) < julianday('now')
            ORDER BY warranty_until DESC
        """)

        # -- note_embeddings: semantic search vectors.
        # PK is (note_path, chunk_id, model) — v1 uses chunk_id=0 for whole-note
        # embedding. The schema is ready for paragraph-level chunking later
        # without a forced reindex; chunk_start/chunk_end carry character offsets
        # into the source note for showing the matching passage.
        if current < 9:
            c.execute("DROP TABLE IF EXISTS note_embeddings")
        c.execute("""
            CREATE TABLE IF NOT EXISTS note_embeddings (
                note_path     TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                chunk_id      INTEGER NOT NULL DEFAULT 0,
                model         TEXT NOT NULL,
                content_hash  TEXT NOT NULL,
                chunk_start   INTEGER NOT NULL DEFAULT 0,
                chunk_end     INTEGER NOT NULL DEFAULT 0,
                dim           INTEGER NOT NULL,
                embedding     BLOB NOT NULL,
                embedded_at   TEXT NOT NULL,
                PRIMARY KEY (note_path, chunk_id, model)
            )
        """)

        # Tracks ```sql / ```sqlite code blocks across the vault so the
        # auto-refresh loop only visits notes that actually contain SQL
        # views (instead of rglob-walking the whole vault every 15 min).
        # Populated by `_index_note_metadata` on each per-note re-scan;
        # rows cascade-delete with the parent file row.
        c.execute("""
            CREATE TABLE IF NOT EXISTS sql_views (
                note_path        TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                block_index      INTEGER NOT NULL,
                query            TEXT NOT NULL,
                lang             TEXT NOT NULL DEFAULT 'sql',
                last_rendered_at INTEGER NOT NULL DEFAULT 0,
                last_data_hash   TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (note_path, block_index)
            )
        """)

        # ── meals: every logged meal / snack / drink with calories ──────────
        # Populated by the `log_meal` MCP tool (and never by the vault
        # indexer — these rows are first-class data, not parsed from notes).
        # Designed for Hyun-Min's weight-loss tracking use case (starting
        # 107.5 kg, May 2026): the brief is "log photo-based estimates with
        # explicit uncertainty + bias slightly high so cutting is safe".
        #
        # Columns:
        #   eaten_at     — ISO 8601 datetime, when the meal was consumed
        #                  (NOT when it was logged — see created_at).
        #   description  — free-text ("braised pork + brown rice + salad").
        #   kcal         — best single estimate. For weight-cutting,
        #                  prefer the higher end of an uncertainty range.
        #   kcal_low/    — explicit uncertainty bracket from a photo
        #     kcal_high    estimate (e.g. 620–720 kcal). Both nullable;
        #                  set them when source='photo' or 'restaurant'.
        #   protein_g/carbs_g/fat_g — optional macros (nutrition labels +
        #                  barcode lookups should fill these; pure photo
        #                  estimates may leave them NULL).
        #   source       — 'manual' (typed), 'photo' (vision estimate),
        #                  'label' (nutrition tag photo, high confidence),
        #                  'barcode' (DB lookup, highest confidence),
        #                  'restaurant' (menu lookup, medium confidence).
        #   confidence   — 'high' (label / barcode), 'medium' (restaurant /
        #                  known dish), 'low' (ambiguous home-cooked photo).
        #   photo_path   — vault-relative path to the original photo, so
        #                  the row links back to the inbox/Attachments copy.
        #   notes        — free-text context ("after gym", "Bu's cooking").
        c.execute("""
            CREATE TABLE IF NOT EXISTS meals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                eaten_at     TEXT NOT NULL,
                description  TEXT NOT NULL,
                kcal         INTEGER NOT NULL,
                kcal_low     INTEGER,
                kcal_high    INTEGER,
                protein_g    REAL,
                carbs_g      REAL,
                fat_g        REAL,
                source       TEXT NOT NULL DEFAULT 'manual',
                confidence   TEXT NOT NULL DEFAULT 'medium',
                photo_path   TEXT,
                notes        TEXT,
                created_at   TEXT NOT NULL
            )
        """)

        # ── weights: every weigh-in ─────────────────────────────────────────
        # Simple log table. The morning-routine smart-scale dream is for
        # future-Iris; this is the manual baseline.
        c.execute("""
            CREATE TABLE IF NOT EXISTS weights (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                measured_at  TEXT NOT NULL,
                kg           REAL NOT NULL,
                notes        TEXT,
                source       TEXT NOT NULL DEFAULT 'manual',
                created_at   TEXT NOT NULL
            )
        """)

        # ── habits + habit_logs: daily check-off habits with GitHub-style heatmap
        # ────────────────────────────────────────────────────────────────────
        # Two-table design: `habits` is the catalog (BunPro / Robokana / Kanji /
        # Asian squat / shoulder rehab / etc.) with cadence + optional reminder
        # time; `habit_logs` is one row per (habit, day) when done, with optional
        # duration_min for habits where "how long" matters (e.g. asian squat
        # hold). The UNIQUE(habit_id, day) constraint makes `INSERT OR REPLACE`
        # the natural "mark done" operation and prevents double-counting.
        #
        # The heatmap is generated by the renderer in tools/habits.py — no
        # materialised matrix in SQL. Iris just queries (habit_id, day) rows
        # for the window and the tool emits a 7×N grid of squares.
        c.execute("""
            CREATE TABLE IF NOT EXISTS habits (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL UNIQUE,
                category      TEXT NOT NULL DEFAULT '',
                cadence       TEXT NOT NULL DEFAULT 'daily',
                cadence_n     INTEGER,
                target_time   TEXT NOT NULL DEFAULT '',
                grace_min     INTEGER NOT NULL DEFAULT 120,
                skill_id      INTEGER,
                injury_id     INTEGER,
                description   TEXT NOT NULL DEFAULT '',
                icon          TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'active',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS habit_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id     INTEGER NOT NULL
                             REFERENCES habits(id) ON DELETE CASCADE,
                day          TEXT NOT NULL,
                done         INTEGER NOT NULL DEFAULT 1,
                duration_min INTEGER,
                notes        TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                UNIQUE(habit_id, day)
            )
        """)

        # View: every active habit with last-done date + simple done-count window.
        # v18 includes ``user_id`` so per-user habit lists are possible —
        # consumers should add ``WHERE user_id = ?`` to scope.
        c.execute("DROP VIEW IF EXISTS habits_active")
        c.execute("""
            CREATE VIEW habits_active AS
            SELECT
                h.id, h.name, h.category, h.cadence, h.target_time,
                h.icon, h.description, h.user_id,
                (SELECT MAX(day) FROM habit_logs WHERE habit_id = h.id AND done = 1) AS last_done,
                (SELECT COUNT(*) FROM habit_logs
                 WHERE habit_id = h.id AND done = 1
                   AND day >= date('now', '-30 days')) AS done_30d,
                (SELECT COUNT(*) FROM habit_logs
                 WHERE habit_id = h.id AND done = 1
                   AND day >= date('now', '-7 days')) AS done_7d
            FROM habits h
            WHERE h.status = 'active'
            ORDER BY h.category, h.name COLLATE NOCASE
        """)

        # ── skill_goals: physical-skill targets (handstand, muscle-up, etc.)
        # ────────────────────────────────────────────────────────────────────
        # Long-running goals with text-described current vs. target levels.
        # `progression` holds the multi-step plan Iris suggests so we don't
        # have to regenerate it from scratch each conversation. `constraints`
        # + `constraint_ref_ids` link to the `injuries` table so Iris's
        # recommendations stay shoulder-aware (or knee-aware, whatever).
        c.execute("""
            CREATE TABLE IF NOT EXISTS skill_goals (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                category          TEXT NOT NULL DEFAULT '',
                target            TEXT NOT NULL DEFAULT '',
                current_level     TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'active',
                priority          INTEGER NOT NULL DEFAULT 2,
                progression       TEXT NOT NULL DEFAULT '',
                constraints       TEXT NOT NULL DEFAULT '',
                constraint_ref_ids TEXT NOT NULL DEFAULT '',
                note_path         TEXT NOT NULL DEFAULT '',
                notes             TEXT NOT NULL DEFAULT '',
                started_at        TEXT NOT NULL DEFAULT '',
                target_date       TEXT NOT NULL DEFAULT '',
                achieved_at       TEXT NOT NULL DEFAULT '',
                updated_at        TEXT NOT NULL
            )
        """)

        # ── injuries: current + historical body issues that gate training ───
        # Tracked as first-class rows because they directly constrain what
        # `skill_goals` can be worked on safely. The shoulder rehab case
        # is the motivating example: an active left-shoulder injury should
        # make Iris route around overhead pressing when suggesting a
        # handstand progression.
        c.execute("""
            CREATE TABLE IF NOT EXISTS injuries (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                body_part         TEXT NOT NULL,
                side              TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'active',
                description       TEXT NOT NULL DEFAULT '',
                severity          TEXT NOT NULL DEFAULT '',
                started_at        TEXT NOT NULL DEFAULT '',
                healed_at         TEXT NOT NULL DEFAULT '',
                physio_started_at TEXT NOT NULL DEFAULT '',
                therapist         TEXT NOT NULL DEFAULT '',
                restrictions      TEXT NOT NULL DEFAULT '',
                note_path         TEXT NOT NULL DEFAULT '',
                notes             TEXT NOT NULL DEFAULT '',
                updated_at        TEXT NOT NULL
            )
        """)

        # ── training_sessions: structured log of training sessions ──────────
        # Lightweight schema — the actual set/rep detail lives in the
        # existing Gym.md note (or wherever the user logs raw sessions).
        # This table is for queries like "how many sessions did I do in
        # the last 30 days?" and for cross-linking to skill_goals via
        # `skill_ids` so we can see which sessions worked which goal.
        c.execute("""
            CREATE TABLE IF NOT EXISTS training_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_at    TEXT NOT NULL,
                kind          TEXT NOT NULL DEFAULT '',
                duration_min  INTEGER,
                rpe           INTEGER,
                summary       TEXT NOT NULL DEFAULT '',
                skill_ids     TEXT NOT NULL DEFAULT '',
                note_path     TEXT NOT NULL DEFAULT '',
                notes         TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL
            )
        """)
        # v15 additive migration: cardio / outdoor-workout metric columns.
        # The original table only captured kind/duration/RPE/summary, which
        # is fine for strength work but loses the interesting data on a
        # 30-min outdoor walk: distance, pace, kcal burned, heart rate,
        # steps, elevation. All nullable because they only apply to
        # cardio/outdoor kinds — strength sessions just leave them NULL.
        # `data_source` tags where the numbers came from (Apple Health,
        # Strava, Fitbit, manual, etc.) so we can sanity-check provenance
        # later if a row looks suspicious.
        for col, typedef in [
            ("distance_km",       "REAL"),
            ("kcal_burned",       "INTEGER"),
            ("avg_hr",            "INTEGER"),  # bpm
            ("max_hr",            "INTEGER"),  # bpm
            ("steps",             "INTEGER"),
            ("elevation_gain_m",  "INTEGER"),
            ("avg_pace_sec_per_km", "INTEGER"),  # easier to query than min/km
            ("data_source",       "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                c.execute(f"ALTER TABLE training_sessions ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # column already exists
        c.execute("CREATE INDEX IF NOT EXISTS idx_training_kind ON training_sessions(kind)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_training_source ON training_sessions(data_source)")

        # Views: a single "training" view that joins sessions with the
        # primary skill they worked (when set), and an "active_goals" view
        # that filters skill_goals to status='active' sorted by priority.
        c.execute("DROP VIEW IF EXISTS skill_goals_active")
        c.execute("""
            CREATE VIEW skill_goals_active AS
            SELECT id, name, category, target, current_level,
                   priority, progression, constraints, constraint_ref_ids,
                   started_at, target_date, notes, updated_at
            FROM skill_goals
            WHERE status = 'active'
            ORDER BY priority ASC, name COLLATE NOCASE ASC
        """)
        c.execute("DROP VIEW IF EXISTS injuries_active")
        c.execute("""
            CREATE VIEW injuries_active AS
            SELECT id, body_part, side, status, severity, description,
                   started_at, physio_started_at, therapist, restrictions,
                   note_path, notes, updated_at
            FROM injuries
            WHERE status IN ('active', 'managing')
            ORDER BY started_at DESC
        """)

        # ── health_profile: singleton row of user stats for TDEE estimates ──
        # Hard-constrained to id=1 so we can never end up with two competing
        # profiles. The values here are inputs to BMR / TDEE / target-intake
        # math in tools/health.py — kept minimal because the downstream
        # formulas (Mifflin-St Jeor + activity multiplier + deficit) only
        # need: weight (pulled from latest `weights` row), height, age
        # (computed from DoB so it doesn't go stale), sex (affects BMR
        # constant), activity level, and target weight-loss rate.
        c.execute("""
            CREATE TABLE IF NOT EXISTS health_profile (
                id                     INTEGER PRIMARY KEY CHECK (id = 1),
                height_cm              REAL,
                date_of_birth          TEXT,
                sex                    TEXT,
                activity_level         TEXT,
                target_kg              REAL,
                target_weekly_loss_kg  REAL,
                notes                  TEXT,
                updated_at             TEXT NOT NULL
            )
        """)

        # ── views: daily nutrition + weekly weight rollups ──────────────────
        # Materialised views would be nice but SQLite doesn't have them; the
        # tables are small enough that a regular VIEW + the indexes below
        # stay snappy for years of daily logging. Both views are surfaced in
        # the markdown dashboard at `10_Profile/Health/Weight & Nutrition.md`
        # via the SQL-views plugin pipeline.
        # v18: views now include user_id so per-user rollups are possible.
        # Existing markdown SQL blocks may need to add ``WHERE user_id = N``
        # to avoid cross-user totals once more than one user logs data.
        # Single-user deployments are unaffected (only one user_id present).
        c.execute("DROP VIEW IF EXISTS meals_daily")
        c.execute("""
            CREATE VIEW meals_daily AS
            SELECT
                substr(eaten_at, 1, 10) AS day,
                user_id,
                COUNT(*)                AS meal_count,
                SUM(kcal)               AS total_kcal,
                SUM(COALESCE(kcal_high, kcal)) AS total_kcal_high,
                SUM(COALESCE(protein_g, 0)) AS total_protein_g,
                SUM(COALESCE(carbs_g, 0))   AS total_carbs_g,
                SUM(COALESCE(fat_g, 0))     AS total_fat_g
            FROM meals
            GROUP BY day, user_id
        """)
        c.execute("DROP VIEW IF EXISTS weights_weekly")
        c.execute("""
            CREATE VIEW weights_weekly AS
            SELECT
                strftime('%Y-W%W', measured_at) AS week,
                user_id,
                MIN(substr(measured_at, 1, 10)) AS first_day,
                MAX(substr(measured_at, 1, 10)) AS last_day,
                ROUND(AVG(kg), 2)               AS avg_kg,
                ROUND(MIN(kg), 2)               AS min_kg,
                ROUND(MAX(kg), 2)               AS max_kg,
                COUNT(*)                        AS reading_count
            FROM weights
            GROUP BY week, user_id
        """)

        # -- useful indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_suffix ON files(suffix)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes_title ON notes(title COLLATE NOCASE)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fm_key ON frontmatter(key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fm_key_value ON frontmatter(key, value)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias COLLATE NOCASE)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wl_target ON wikilinks(target)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_checked ON tasks(checked)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_reminders_remind ON reminders(remind_on)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_datetime ON events(date, time)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_end_date ON events(end_date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_revisions_path ON revisions(path)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_revisions_saved ON revisions(saved_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_access_count ON note_access(access_count)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_model ON note_embeddings(model)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sql_views_path ON sql_views(note_path)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_meals_eaten ON meals(eaten_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_meals_source ON meals(source)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_weights_measured ON weights(measured_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_skill_goals_status ON skill_goals(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_skill_goals_priority ON skill_goals(priority)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_injuries_status ON injuries(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_injuries_body ON injuries(body_part)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_training_sessions_at ON training_sessions(session_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_habits_status ON habits(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_day ON habit_logs(habit_id, day)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_habit_logs_day ON habit_logs(day)")

        # -- v16: users (multi-user support — folder isolation + per-row
        # DB scoping). One row per registered Discord identity. Auto-
        # registration happens via ``get_or_create_user`` on first contact.
        # `is_owner = 1` is reserved for the deployment owner (set via
        # IRIS_OWNER_DISCORD_ID env). The partial unique index enforces
        # at most one owner row.
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id       TEXT NOT NULL UNIQUE,
                discord_username TEXT NOT NULL DEFAULT '',
                display_name     TEXT NOT NULL,
                vault_subdir     TEXT NOT NULL,
                is_owner         INTEGER NOT NULL DEFAULT 0,
                status           TEXT NOT NULL DEFAULT 'active',
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_discord ON users(discord_id)")
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_owner "
            "ON users(is_owner) WHERE is_owner = 1"
        )

        # -- v17: per-user data isolation on existing domain tables.
        # Nullable `user_id` FK so legacy single-user rows stay valid.
        # On INSERT a trigger fills it in from the owner row (defined
        # below) — existing tools that don't yet pass user_id keep
        # writing correctly without code change.
        #
        # Step 1: bootstrap the owner row from env if not yet present,
        # so the backfill below has something to attribute legacy data
        # to. The display-name override picks up IRIS_OWNER_DISPLAY_NAME
        # via get_or_create_user.
        owner_env = os.environ.get("IRIS_OWNER_DISCORD_ID", "").strip()
        if owner_env and self.get_user_by_discord_id(owner_env) is None:
            self.get_or_create_user(
                owner_env,
                discord_username=os.environ.get(
                    "IRIS_OWNER_DISCORD_USERNAME", ""
                ),
            )

        # Step 2: add the user_id column to every per-user domain table.
        # Each ALTER is wrapped because the second run finds the column
        # already present and would otherwise raise OperationalError.
        _user_scoped_tables = (
            "meals", "weights", "training_sessions", "habits", "habit_logs",
            "health_profile", "injuries", "skill_goals",
        )
        for tbl in _user_scoped_tables:
            try:
                c.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN user_id INTEGER "
                    "REFERENCES users(id)"
                )
            except sqlite3.OperationalError:
                pass  # column already exists

        # Step 3: backfill any NULL user_id rows to the owner. If no
        # owner exists yet, leave NULL; the trigger below will fall back
        # to "first owner found at INSERT time" when one is registered.
        owner_row = self.get_owner_user()
        if owner_row is not None:
            owner_id = int(owner_row["id"])
            for tbl in _user_scoped_tables:
                c.execute(
                    f"UPDATE {tbl} SET user_id = ? WHERE user_id IS NULL",
                    (owner_id,),
                )

        # Step 4: indexes for the most common per-user filters.
        for tbl in (
            "meals", "weights", "training_sessions", "habits",
            "habit_logs", "injuries", "skill_goals",
        ):
            c.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{tbl}_user ON {tbl}(user_id)"
            )

        # v19: health_profile is per-user — one row per user_id, not a
        # singleton. The original schema had ``PRIMARY KEY = id`` with
        # callers using ``WHERE id = 1`` to read/write the singleton row.
        # Adding a partial unique index on user_id enforces "at most one
        # profile per user" without rewriting the table. Existing id=1
        # row already has user_id = owner (backfilled in v17), so the
        # constraint is satisfiable from day one.
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_health_profile_user "
            "ON health_profile(user_id) WHERE user_id IS NOT NULL"
        )

        # v20: per-user scheduled briefings. Each user picks their own
        # times + channel to receive them. NULL time = disabled for that
        # user. ``brief_channel_id`` is the Discord channel where their
        # brief / wrap-up lands (typically their dedicated DM channel
        # with Iris). All four columns are nullable / non-breaking.
        #
        # v21: per-user notification channels — ping_channel_id (events,
        # reminders, snooze replays) and health_channel_id (daily health
        # card). Both NULL → fall back to brief_channel_id, then to the
        # IRIS_DISCORD_PING_CHANNEL / IRIS_DISCORD_HEALTH_CHANNEL env
        # vars. So you can leave the env vars unset entirely once each
        # user has their own channels persisted.
        for col, typedef in [
            ("brief_morning_at",  "TEXT"),      # v20
            ("brief_evening_at",  "TEXT"),      # v20
            ("brief_channel_id",  "INTEGER"),   # v20
            ("brief_timezone",    "TEXT"),      # v20
            ("ping_channel_id",   "INTEGER"),   # v21
            ("health_channel_id", "INTEGER"),   # v21
            ("health_daily_at",   "TEXT"),      # v23 — was NOTIFY_HEALTH_DAILY_AT env
            ("health_weekly_at",  "TEXT"),      # v23 — was NOTIFY_HEALTH_WEEKLY_AT env
            ("health_weekly_dow", "INTEGER"),   # v23 — was NOTIFY_HEALTH_WEEKLY_DOW env (1-7 ISO)
            ("web_password_hash",  "TEXT"),     # v25 — bcrypt hash for iris-web login
            ("web_password_set_at","TEXT"),     # v25 — ISO ts when password was last set
        ]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # already added on a previous run

        # Step 5: AFTER INSERT triggers. If a row lands with NULL user_id
        # (any caller that pre-dates the multi-user refactor), fall back
        # to the owner. Keeps existing single-user tool code paths working
        # without modification — tools keep INSERTing without user_id, the
        # trigger fills the column from the owner row.
        for tbl in _user_scoped_tables:
            c.execute(f"DROP TRIGGER IF EXISTS {tbl}_user_id_default")
            c.execute(f"""
                CREATE TRIGGER {tbl}_user_id_default
                AFTER INSERT ON {tbl}
                FOR EACH ROW
                WHEN NEW.user_id IS NULL
                BEGIN
                    UPDATE {tbl}
                    SET user_id = (SELECT id FROM users WHERE is_owner = 1 LIMIT 1)
                    WHERE rowid = NEW.rowid;
                END;
            """)

        # Step 6 (v22): channel → user binding table.
        #
        # Background firing loops (morning brief, health card, ping
        # routing) previously read ``users.brief_channel_id`` etc. as
        # the source of truth for routing. That column is *user→channel*,
        # which can lie: if the owner's row had ``brief_channel_id`` set
        # to mom's channel (by a stale auto-default or a manual UPDATE),
        # the loop would happily fire owner content into mom's channel.
        #
        # The fix is to flip the source of truth. ``user_channels`` is
        # channel-keyed, so a channel can only belong to ONE user. The
        # firing loops JOIN against it and only route to channels where
        # the channel-user binding matches the row's user_id — closing
        # the leak at the data model level instead of relying on a
        # ``WHERE is_owner = 0`` band-aid.
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_channels (
                channel_id INTEGER PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_channels_user "
            "ON user_channels(user_id)"
        )

        # ── v26: iris-web conversation history ──────────────────────
        # web_sessions:  one row per (user, session_id). Created lazily
        #                on the user's first turn in that session. The
        #                session_id matches what's in the browser's
        #                sessionStorage so a refresh continues the same
        #                session.
        # web_turns:     append-only log of (role, content) pairs.
        #                Persisting these gives us a history sidebar
        #                across container restarts; the SDK's
        #                in-memory client only knows about turns
        #                since its last cold start.
        # We deliberately don't reference users(id) FK on web_turns
        # — the join via web_sessions.user_id is enough, and avoiding
        # the extra constraint keeps the insert path light.
        c.executescript("""
            CREATE TABLE IF NOT EXISTS web_sessions (
                id            TEXT    PRIMARY KEY,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                started_at    TEXT    NOT NULL,
                last_used_at  TEXT    NOT NULL,
                title         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_web_sessions_user_lru
                ON web_sessions(user_id, last_used_at DESC);

            CREATE TABLE IF NOT EXISTS web_turns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT    NOT NULL REFERENCES web_sessions(id) ON DELETE CASCADE,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_web_turns_session_created
                ON web_turns(session_id, created_at);

            -- PR-AE: per-user personas (saved system-prompt preambles).
            -- Each web_session optionally references one via persona_id.
            CREATE TABLE IF NOT EXISTS web_personas (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name          TEXT    NOT NULL,
                system_prompt TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL,
                UNIQUE(user_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_web_personas_user
                ON web_personas(user_id);

            -- calendar_sources (v28): subscribed external calendars
            -- (ICS today; CalDAV in a later phase). One row per
            -- (user, source_tag). `source_tag` is the marker the iCal
            -- puller stamps into each event's description ([source:TAG]),
            -- which is how events get tied back to a calendar for
            -- per-calendar colour + on/off in the web UI. `shared_with`
            -- (comma-separated user ids) + `username`/`secret` are
            -- reserved for the sharing / CalDAV phases.
            CREATE TABLE IF NOT EXISTS calendar_sources (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name        TEXT    NOT NULL,
                kind        TEXT    NOT NULL DEFAULT 'ics',
                url         TEXT    NOT NULL DEFAULT '',
                source_tag  TEXT    NOT NULL,
                color       TEXT    NOT NULL DEFAULT '#a78bfa',
                enabled     INTEGER NOT NULL DEFAULT 1,
                shared_with TEXT    NOT NULL DEFAULT '',
                username    TEXT    NOT NULL DEFAULT '',
                secret      TEXT    NOT NULL DEFAULT '',
                last_synced TEXT    NOT NULL DEFAULT '',
                last_status TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT '',
                UNIQUE(user_id, source_tag)
            );
            CREATE INDEX IF NOT EXISTS idx_calendar_sources_user
                ON calendar_sources(user_id);

            -- calendar_dismissed (v29): tombstones for calendar source tags
            -- the user explicitly deleted. Auto-discovery (ensure_discovered)
            -- re-registers any [source:TAG] still present in the user's
            -- events, so without this a deleted *local* (discovered) calendar
            -- would immediately reappear on the next /v1/calendars load.
            CREATE TABLE IF NOT EXISTS calendar_dismissed (
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source_tag  TEXT    NOT NULL,
                PRIMARY KEY (user_id, source_tag)
            );

            -- llm_endpoints (v31): per-user alternative LLM backends — an
            -- OpenAI-compatible endpoint (OpenRouter, a local Ollama / vLLM /
            -- llama.cpp server, etc.). Lets a conversation run on a model
            -- other than the default Claude-subscription backend. api_key is
            -- encrypted at rest (Fernet, same scheme as calendar CalDAV creds).
            -- supports_tools: NULL = infer from provider/model; 0/1 = explicit.
            -- shared_with (v32): comma-separated user ids this endpoint's
            -- owner lets borrow the key — but ONLY for the models listed in
            -- allowed_models (so e.g. mom can use the owner's key for
            -- DeepSeek but not rack up cost on an expensive model).
            CREATE TABLE IF NOT EXISTS llm_endpoints (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name           TEXT    NOT NULL,
                base_url       TEXT    NOT NULL,
                api_key        TEXT    NOT NULL DEFAULT '',
                model          TEXT    NOT NULL DEFAULT '',
                supports_tools INTEGER,
                enabled        INTEGER NOT NULL DEFAULT 1,
                shared_with    TEXT    NOT NULL DEFAULT '',
                allowed_models TEXT    NOT NULL DEFAULT '',
                created_at     TEXT    NOT NULL DEFAULT '',
                UNIQUE(user_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_llm_endpoints_user
                ON llm_endpoints(user_id);
        """)
        # web_sessions.persona_id is added via ALTER if missing (SQLite
        # doesn't let us add NOT NULL with default to existing rows so
        # we make it nullable; new sessions can pick a persona at start).
        try:
            c.execute(
                "ALTER TABLE web_sessions ADD COLUMN persona_id INTEGER "
                "REFERENCES web_personas(id) ON DELETE SET NULL"
            )
        except sqlite3.OperationalError:
            pass  # column already exists

        # web_sessions.folder + tags (v30): lightweight organization for the
        # chat library. folder is a single string ('' / NULL = unfiled); tags
        # is a comma-separated, lowercased list. Both nullable, added via ALTER.
        for _col in ("folder", "tags"):
            try:
                c.execute(f"ALTER TABLE web_sessions ADD COLUMN {_col} TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

        # v32: llm_endpoints key-sharing (shared_with + allowed_models) and
        # the per-session alternative-backend selection (llm_endpoint_id +
        # llm_model). All nullable, added via ALTER for existing DBs.
        for _tbl, _col, _decl in (
            ("llm_endpoints", "shared_with", "TEXT NOT NULL DEFAULT ''"),
            ("llm_endpoints", "allowed_models", "TEXT NOT NULL DEFAULT ''"),
            ("web_sessions", "llm_endpoint_id", "INTEGER"),
            ("web_sessions", "llm_model", "TEXT"),
        ):
            try:
                c.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_decl}")
            except sqlite3.OperationalError:
                pass  # column already exists

        # Backfill: for each users row, register every set channel_id
        # as a binding to that user. Non-owners only (the owner's brief
        # / ping / health columns are env-anchored — we don't want
        # them in user_channels because that would route per-user-loop
        # firings to the owner's row, which is the original leak).
        # ON CONFLICT IGNORE: if the same channel was set on multiple
        # users (impossible in legitimate use), the first row wins;
        # the conflict is the bot operator's signal to clean up.
        for col in ("brief_channel_id", "ping_channel_id", "health_channel_id"):
            c.execute(f"""
                INSERT OR IGNORE INTO user_channels
                    (channel_id, user_id, created_at, updated_at)
                SELECT {col}, id, datetime('now'), datetime('now')
                FROM users
                WHERE {col} IS NOT NULL AND is_owner = 0
            """)

        # Step 7 (v23): owner row is now configured via DB columns, not env.
        # Seed owner's row from the legacy env vars on first init after
        # upgrade. Only fills columns that are currently NULL — preserves
        # any value Iris already wrote via the MCP tools, and leaves existing
        # deployments where the env was the source of truth working
        # transparently.
        owner_row = c.execute(
            "SELECT id, brief_morning_at, brief_evening_at, brief_channel_id, "
            " ping_channel_id, health_channel_id, "
            " health_daily_at, health_weekly_at, health_weekly_dow "
            "FROM users WHERE is_owner = 1 LIMIT 1"
        ).fetchone()
        if owner_row is not None:
            now_iso = datetime.now().isoformat(timespec="seconds")
            seed_pairs: list[tuple[str, str | int | None]] = []
            def _seed(col: str, env_key: str, parse: str = "str") -> None:
                if owner_row[col] is not None:
                    return
                raw = os.environ.get(env_key, "").strip()
                if not raw or raw.lower() in ("off", "disable", "disabled"):
                    return
                try:
                    val: str | int = int(raw) if parse == "int" else raw
                except ValueError:
                    return
                seed_pairs.append((col, val))
            _seed("brief_channel_id",  "IRIS_DISCORD_PING_CHANNEL",   "int")
            _seed("ping_channel_id",   "IRIS_DISCORD_PING_CHANNEL",   "int")
            _seed("health_channel_id", "IRIS_DISCORD_HEALTH_CHANNEL", "int")
            _seed("brief_morning_at",  "NOTIFY_MORNING_AT")
            _seed("brief_evening_at",  "NOTIFY_EVENING_AT")
            _seed("health_daily_at",   "NOTIFY_HEALTH_DAILY_AT")
            _seed("health_weekly_at",  "NOTIFY_HEALTH_WEEKLY_AT")
            _seed("health_weekly_dow", "NOTIFY_HEALTH_WEEKLY_DOW", "int")
            for col, val in seed_pairs:
                c.execute(
                    f"UPDATE users SET {col} = ?, updated_at = ? WHERE id = ?",
                    (val, now_iso, owner_row["id"]),
                )
            # And register the owner's brief/health channels in user_channels
            # so the per-user firing loop (post-v23, no is_owner=0 filter)
            # can route through the existing EXISTS check.
            for col in ("brief_channel_id", "ping_channel_id", "health_channel_id"):
                ch = next((v for k, v in seed_pairs if k == col), None)
                if ch is None:
                    ch = owner_row[col]
                if ch is not None:
                    c.execute(
                        "INSERT OR IGNORE INTO user_channels "
                        " (channel_id, user_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (int(ch), owner_row["id"], now_iso, now_iso),
                    )

        c.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
            (str(self.SCHEMA_VERSION),),
        )
        c.commit()

    # -- content hashing ------------------------------------------------------

    @staticmethod
    def _hash_content(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]

    @staticmethod
    def _auto_summary(body: str, max_chars: int = 300) -> str:
        """Heuristic summary from a note's body. Skips top heading, callouts, code fences, embeds/wikilink-only lines."""
        if not body:
            return ""
        lines = body.split("\n")
        paragraph: list[str] = []
        in_code = False
        for raw in lines:
            line = raw.rstrip()
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            if not stripped:
                if paragraph:
                    break
                continue
            # Skip any heading (H1-H6), frontmatter remnants, image embeds, pure wikilink-only lines, callouts
            if re.match(r"^#{1,6}\s", stripped):
                continue
            if stripped.startswith("> [!"):
                continue
            if stripped.startswith("![[") or stripped.startswith("![]("):
                continue
            if re.fullmatch(r"!?\[\[[^\]]+\]\]", stripped):
                continue
            if stripped.startswith("---") or stripped.startswith("==="):
                continue
            paragraph.append(stripped)
            if sum(len(p) + 1 for p in paragraph) >= max_chars:
                break
        text = " ".join(paragraph).strip()
        # Strip leading list markers / quote markers
        text = re.sub(r"^[-*>]\s+", "", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars].rstrip()

    # -- single-file indexing -------------------------------------------------

    def _index_file(self, path: Path, text: str | None = None):
        """Index (or re-index) a single vault file into the database."""
        c = self.conn
        rel = unicodedata.normalize("NFC", str(path.relative_to(self._root)).replace("\\", "/"))
        suffix = vault_suffix(path)
        stat = path.stat()
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns

        is_md = suffix in {".md", ".excalidraw.md"}

        if text is None and is_md:
            text = read_text(path)

        content_hash = self._hash_content(text) if text else ""

        # Upsert files row
        c.execute(
            "INSERT OR REPLACE INTO files (path, suffix, size, mtime_ns, content_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (rel, suffix, size, mtime_ns, content_hash),
        )

        if not is_md:
            # Non-Markdown files only go into the files table
            return

        # -- Markdown note enrichment --
        assert text is not None

        data, body = split_frontmatter(text)

        title = title_from_text(text, path.stem)
        note_type = str(data.get("type", "")).strip()
        note_status = str(data.get("status", "")).strip()
        wc = count_words(body)

        # Preserve manually-set summaries; otherwise auto-generate from body
        prev = c.execute(
            "SELECT summary, summary_source FROM notes WHERE path = ?", (rel,)
        ).fetchone()
        if prev and prev["summary_source"] == "manual":
            summary = prev["summary"]
            summary_source = "manual"
        else:
            summary = self._auto_summary(body)
            summary_source = "auto" if summary else ""

        # Upsert notes row
        c.execute(
            "INSERT OR REPLACE INTO notes (path, title, type, status, word_count, summary, summary_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rel, title, note_type, note_status, wc, summary, summary_source),
        )

        # -- clear old derived data for this note
        for tbl in ("frontmatter", "tags", "aliases", "wikilinks", "tasks",
                    "reminders", "sql_views"):
            c.execute(f"DELETE FROM {tbl} WHERE {'note_path' if tbl != 'wikilinks' else 'source_path'} = ?", (rel,))

        # Delete old FTS entry
        c.execute("DELETE FROM fts WHERE path = ?", (rel,))

        # -- frontmatter key/value pairs
        fm_rows: list[tuple[str, str, str]] = []
        for key, val in data.items():
            if key in ("tags", "aliases"):
                continue  # handled separately
            if isinstance(val, list):
                for v in val:
                    fm_rows.append((rel, key, str(v).strip()))
            else:
                fm_rows.append((rel, key, str(val).strip()))
        if fm_rows:
            c.executemany(
                "INSERT OR IGNORE INTO frontmatter (note_path, key, value) VALUES (?, ?, ?)",
                fm_rows,
            )

        # -- tags
        tags_raw = data.get("tags", [])
        tag_list = [tags_raw] if isinstance(tags_raw, str) else [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
        tag_rows = [(rel, t.strip()) for t in tag_list if t.strip()]
        if tag_rows:
            c.executemany(
                "INSERT OR IGNORE INTO tags (note_path, tag) VALUES (?, ?)", tag_rows
            )

        # -- aliases
        aliases_raw = data.get("aliases", [])
        alias_list = [aliases_raw] if isinstance(aliases_raw, str) else [str(a) for a in aliases_raw] if isinstance(aliases_raw, list) else []
        alias_rows = [(rel, a.strip()) for a in alias_list if a.strip()]
        if alias_rows:
            c.executemany(
                "INSERT OR IGNORE INTO aliases (note_path, alias) VALUES (?, ?)",
                alias_rows,
            )

        # -- wikilinks
        links = extract_wikilinks(text)
        link_rows = [
            (rel, normalize_note_target(lnk["note_target"]), lnk["display_text"])
            for lnk in links
        ]
        if link_rows:
            c.executemany(
                "INSERT OR IGNORE INTO wikilinks (source_path, target, display_text) "
                "VALUES (?, ?, ?)",
                link_rows,
            )

        # -- tasks + reminders (whole-note walk with section skip-list)
        # Previously only scanned `## Tasks` / `## Reminders` — that silently
        # dropped every checkbox in notes using a different convention
        # (e.g. Huawei To-Do.md uses `## High Priority`, `## General To-Do`).
        # Now we walk the whole note, route reminder-section bullets to the
        # reminders table, skip archive-style sections (Done / Completed /
        # References / Related Notes / etc.), and put everything else in
        # the tasks table — including loose checkboxes before any heading.
        task_lines, reminder_lines = find_all_task_lines(text)
        task_rows = [
            (rel, p["text"], int(p["checked"]), p["due"], p["priority"], p["done"])
            for _, _, p in task_lines
        ]
        if task_rows:
            c.executemany(
                "INSERT INTO tasks (note_path, text, checked, due, priority, done) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                task_rows,
            )

        reminder_rows = [
            (rel, p["text"], int(p["checked"]), p.get("remind_on", ""), p.get("repeat", ""), p["done"])
            for _, _, p in reminder_lines
        ]
        if reminder_rows:
            c.executemany(
                "INSERT INTO reminders (note_path, text, checked, remind_on, repeat, done) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                reminder_rows,
            )

        # -- events from ## Schedule section
        event_entries = parse_schedule_section(text)
        if event_entries:
            # Try to derive date from the note filename (YYYY-MM-DD pattern)
            date_from_name = ""
            m_date = re.search(r"(\d{4}-\d{2}-\d{2})", rel)
            if m_date:
                date_from_name = m_date.group(1)
            event_rows = []
            for ev in event_entries:
                ev_date = ev.get("date", "") or date_from_name
                # Compute end_date for cross-day events
                ev_end_date = ""
                plus_days_str = ev.get("plus_days", "")
                if plus_days_str and ev_date:
                    try:
                        plus_days_int = int(plus_days_str)
                        base = datetime.strptime(ev_date, "%Y-%m-%d").date()
                        ev_end_date = (base + timedelta(days=plus_days_int)).isoformat()
                    except (ValueError, TypeError):
                        pass
                is_all_day = 1 if ev.get("all_day") == "1" else 0
                event_rows.append((
                    rel,
                    ev_date,
                    ev.get("time", ""),
                    ev.get("end_time", ""),
                    ev.get("title", ""),
                    ev.get("location", ""),
                    ev.get("description", ""),
                    ev_end_date,
                    is_all_day,
                ))
            c.executemany(
                "INSERT INTO events "
                "(note_path, date, time, end_time, title, location, description, end_date, all_day) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                event_rows,
            )

        # -- FTS
        # Strip frontmatter delimiters from the body for cleaner search
        fts_body = body.strip()
        c.execute(
            "INSERT INTO fts (path, title, body) VALUES (?, ?, ?)",
            (rel, title, fts_body),
        )

        # -- ```sql / ```sqlite code blocks → sql_views table.
        # Indexed-not-rendered: stores the query so the refresh loop knows
        # which notes/blocks exist without walking the filesystem. The
        # actual rendering happens in `_iris.tools.sqlite.refresh_sql_views`.
        sql_view_rows: list[tuple[str, int, str, str]] = []
        for i, m in enumerate(_SQL_VIEW_INDEX_RE.finditer(text)):
            lang = m.group(1).lower()
            query = m.group(2).strip()
            if query:
                sql_view_rows.append((rel, i, query, lang))
        if sql_view_rows:
            c.executemany(
                "INSERT INTO sql_views "
                "(note_path, block_index, query, lang) VALUES (?, ?, ?, ?)",
                sql_view_rows,
            )

    def _remove_file(self, rel_path: str):
        """Remove a file from the index (cascading deletes handle child rows)."""
        c = self.conn
        c.execute("DELETE FROM fts WHERE path = ?", (rel_path,))
        c.execute("DELETE FROM note_access WHERE path = ?", (rel_path,))
        c.execute("DELETE FROM revisions WHERE path = ?", (rel_path,))
        c.execute("DELETE FROM files WHERE path = ?", (rel_path,))

    # -- bulk sync ------------------------------------------------------------

    def sync(self, force: bool = False) -> dict[str, int]:
        """
        Synchronize the database with the vault filesystem.

        Uses mtime_ns for incremental updates.  Pass ``force=True`` to
        re-index every file regardless of mtime.

        Returns a summary dict: {scanned, added, updated, removed, unchanged, errors}.
        """
        c = self.conn
        root = self._root
        stats = {"scanned": 0, "added": 0, "updated": 0, "removed": 0, "unchanged": 0, "errors": 0}

        # Build set of currently-on-disk files (NFC-normalized to match _index_file)
        disk_files: dict[str, Path] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if is_ignored_path(path):
                continue
            try:
                ensure_allowed_vault_file(path)
            except ValueError:
                continue
            rel = unicodedata.normalize("NFC", str(path.relative_to(root)).replace("\\", "/"))
            disk_files[rel] = path
            stats["scanned"] += 1

        # Build set of currently-indexed files {path: mtime_ns}
        indexed: dict[str, int] = {}
        for row in c.execute("SELECT path, mtime_ns FROM files").fetchall():
            indexed[row["path"]] = row["mtime_ns"]

        # Detect removed files
        removed = set(indexed.keys()) - set(disk_files.keys())
        for rel in removed:
            self._remove_file(rel)
            stats["removed"] += 1

        # On force rebuild, purge all derived data first so stale entries
        # from previous index runs (or silently-failed _index_file calls)
        # can never survive.  The files/notes tables are kept because
        # _index_file will INSERT OR REPLACE them; removed files were
        # already cleaned above.
        if force:
            for tbl in ("frontmatter", "tags", "aliases", "wikilinks",
                        "tasks", "reminders", "fts"):
                c.execute(f"DELETE FROM {tbl}")

        # Index new and changed files
        batch_count = 0
        for rel, path in disk_files.items():
            try:
                mtime_ns = path.stat().st_mtime_ns
            except OSError:
                continue

            if not force and rel in indexed and indexed[rel] == mtime_ns:
                stats["unchanged"] += 1
                continue

            try:
                self._index_file(path)
            except Exception as exc:
                # Log but don't crash the whole sync
                import logging
                logging.getLogger("obsidian_memory_mcp").warning(
                    "sync: failed to index %s: %s", rel, exc,
                )
                stats["errors"] += 1
                continue

            if rel in indexed:
                stats["updated"] += 1
            else:
                stats["added"] += 1

            batch_count += 1
            if batch_count % 200 == 0:
                c.commit()

        c.commit()
        return stats

    def sync_file(self, path: Path):
        """Re-index a single file after a write/update.  Commits immediately."""
        if not path.exists():
            rel = unicodedata.normalize("NFC", str(path.relative_to(self._root)).replace("\\", "/"))
            self._remove_file(rel)
            self.conn.commit()
            return
        self._index_file(path)
        self.conn.commit()

    def sync_note_text(self, path: Path, text: str):
        """Re-index a note whose text you already have in memory (avoids re-read)."""
        self._index_file(path, text=text)
        self.conn.commit()

    def remove_path(self, rel_path: str):
        """Remove an entry by relative path and commit."""
        self._remove_file(rel_path)
        self.conn.commit()

    # -- users (multi-user support) -------------------------------------------

    def get_user_by_discord_id(self, discord_id) -> Optional[sqlite3.Row]:
        """Look up a user row by Discord snowflake. Returns None if not
        registered. The ID may be passed as int or str — both forms are
        normalised to str before lookup."""
        if discord_id in (None, ""):
            return None
        return self.conn.execute(
            "SELECT * FROM users WHERE discord_id = ?",
            (str(discord_id),),
        ).fetchone()

    def get_owner_user(self) -> Optional[sqlite3.Row]:
        """Return the row flagged ``is_owner = 1`` (at most one), or None
        if no owner has been registered yet."""
        return self.conn.execute(
            "SELECT * FROM users WHERE is_owner = 1 LIMIT 1"
        ).fetchone()

    def get_or_create_user(
        self,
        discord_id,
        discord_username: str = "",
        display_name: str = "",
    ) -> sqlite3.Row:
        """Look up by Discord ID, or auto-register if not present.

        - ``discord_id``: Discord snowflake (int or str). Required.
        - ``discord_username``: Discord login handle. Updated on each call
          if it differs from the stored value (Discord usernames change).
        - ``display_name``: How Iris refers to the user in conversation.
          Defaults to ``discord_username`` then to ``user-<last4>``. For
          the owner, ``IRIS_OWNER_DISPLAY_NAME`` env (e.g. ``Hyun-Min``)
          overrides — so the deployment owner can keep their preferred
          name regardless of Discord handle.

        Owner flag is set automatically when ``discord_id`` matches the
        ``IRIS_OWNER_DISCORD_ID`` env var. The partial unique index on
        ``is_owner`` prevents a second owner from being created.

        Returns the user row (sqlite3.Row).
        """
        if discord_id in (None, ""):
            raise ValueError("discord_id is required")
        did = str(discord_id)
        existing = self.get_user_by_discord_id(did)
        if existing is not None:
            # Refresh username if Discord side changed it
            if discord_username and discord_username != existing["discord_username"]:
                now = datetime.now().isoformat(timespec="seconds")
                self.conn.execute(
                    "UPDATE users SET discord_username = ?, updated_at = ? WHERE id = ?",
                    (discord_username, now, existing["id"]),
                )
                self.conn.commit()
                existing = self.get_user_by_discord_id(did)
            return existing

        owner_env = os.environ.get("IRIS_OWNER_DISCORD_ID", "").strip()
        is_owner = 1 if owner_env and did == owner_env else 0

        final_name = display_name or discord_username or f"user-{did[-4:]}"
        if is_owner:
            owner_name_env = os.environ.get("IRIS_OWNER_DISPLAY_NAME", "").strip()
            if owner_name_env:
                final_name = owner_name_env

        vault_subdir = f"users/{did}"
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO users (discord_id, discord_username, display_name, "
            "vault_subdir, is_owner, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (did, discord_username, final_name, vault_subdir, is_owner, now, now),
        )
        self.conn.commit()
        return self.get_user_by_discord_id(did)

    def list_users(self, include_inactive: bool = False) -> list[sqlite3.Row]:
        """All registered users. Owner row first, then alphabetic by
        display name."""
        if include_inactive:
            sql = (
                "SELECT * FROM users "
                "ORDER BY is_owner DESC, display_name COLLATE NOCASE"
            )
        else:
            sql = (
                "SELECT * FROM users WHERE status = 'active' "
                "ORDER BY is_owner DESC, display_name COLLATE NOCASE"
            )
        return list(self.conn.execute(sql).fetchall())

    # -- query helpers --------------------------------------------------------

    def search_fts(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search.  Returns [{path, title, snippet, rank}]."""
        c = self.conn
        # Escape special FTS5 characters in user query
        safe_q = re.sub(r'["\'\(\)\*\-]', " ", query).strip()
        if not safe_q:
            return []
        # Convert multi-word query to prefix-match tokens
        tokens = safe_q.split()
        fts_query = " ".join(f'"{t}"' for t in tokens if t)
        if not fts_query:
            return []
        try:
            rows = c.execute(
                "SELECT path, title, snippet(fts, 2, '»', '«', '…', 40) AS snippet, "
                "rank FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        results = [dict(r) for r in rows]
        for r in results:
            if r.get("snippet"):
                r["snippet"] = r["snippet"].replace("»", "").replace("«", "")
        return results

    def find_backlinks_db(self, target_path: str, limit: int = 100) -> list[dict[str, str]]:
        """Find notes linking to *target_path* via wikilinks."""
        c = self.conn
        target = normalize_note_target(target_path)
        target_basename = Path(target).name
        rows = c.execute(
            "SELECT source_path, target, display_text FROM wikilinks "
            "WHERE target = ? OR target LIKE ? LIMIT ?",
            (target, f"%/{target_basename}", limit),
        ).fetchall()
        seen: set[str] = set()
        results: list[dict[str, str]] = []
        for r in rows:
            sp = r["source_path"]
            if sp not in seen:
                seen.add(sp)
                results.append({"path": sp, "link": f"[[{r['target']}{'|' + r['display_text'] if r['display_text'] else ''}]]"})
        return results

    def query_frontmatter(
        self, field: str, value: str = "", missing: bool = False,
        folder: str = "", limit: int = 500,
    ) -> list[str]:
        """Query notes by frontmatter field/value.  Returns list of paths."""
        c = self.conn

        if field == "tags":
            tbl, col = "tags", "tag"
        elif field == "aliases":
            tbl, col = "aliases", "alias"
        else:
            tbl, col = None, None

        if tbl:
            if missing:
                sql = f"SELECT n.path FROM notes n WHERE NOT EXISTS (SELECT 1 FROM {tbl} t WHERE t.note_path = n.path)"
                params: list[Any] = []
                if folder:
                    sql += " AND n.path LIKE ?"
                    params.append(f"{folder}/%")
            elif value:
                sql = f"SELECT note_path AS path FROM {tbl} WHERE {col} = ?"
                params = [value]
                if folder:
                    sql += " AND note_path LIKE ?"
                    params.append(f"{folder}/%")
            else:
                sql = f"SELECT DISTINCT note_path AS path FROM {tbl}"
                params = []
                if folder:
                    sql += " WHERE note_path LIKE ?"
                    params.append(f"{folder}/%")
            sql += " ORDER BY path LIMIT ?"
            params.append(limit)
            return [r["path"] for r in c.execute(sql, params).fetchall()]

        if missing:
            sql = (
                "SELECT n.path FROM notes n "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM frontmatter f WHERE f.note_path = n.path AND f.key = ?"
                ")"
            )
            params = [field]
            if folder:
                sql += " AND n.path LIKE ?"
                params.append(f"{folder}/%")
            sql += " ORDER BY n.path LIMIT ?"
            params.append(limit)
            return [r["path"] for r in c.execute(sql, params).fetchall()]

        if value:
            sql = (
                "SELECT f.note_path AS path FROM frontmatter f "
                "WHERE f.key = ? AND f.value = ?"
            )
            params = [field, value]
        else:
            sql = (
                "SELECT DISTINCT f.note_path AS path FROM frontmatter f "
                "WHERE f.key = ?"
            )
            params = [field]

        if folder:
            sql += " AND f.note_path LIKE ?"
            params.append(f"{folder}/%")
        sql += " ORDER BY path LIMIT ?"
        params.append(limit)
        return [r["path"] for r in c.execute(sql, params).fetchall()]

    def list_frontmatter_values_db(
        self, field: str, folder: str = "", limit: int = 500,
    ) -> list[tuple[str, int]]:
        """List unique values for a frontmatter key with counts."""
        c = self.conn
        if field == "tags":
            sql = "SELECT tag AS value, COUNT(*) AS cnt FROM tags"
            params: list[Any] = []
            if folder:
                sql += " WHERE note_path LIKE ?"
                params.append(f"{folder}/%")
            sql += " GROUP BY tag ORDER BY cnt DESC, tag LIMIT ?"
            params.append(limit)
        elif field == "aliases":
            sql = "SELECT alias AS value, COUNT(*) AS cnt FROM aliases"
            params = []
            if folder:
                sql += " WHERE note_path LIKE ?"
                params.append(f"{folder}/%")
            sql += " GROUP BY alias ORDER BY cnt DESC, alias LIMIT ?"
            params.append(limit)
        else:
            sql = (
                "SELECT value, COUNT(*) AS cnt FROM frontmatter "
                "WHERE key = ?"
            )
            params = [field]
            if folder:
                sql += " AND note_path LIKE ?"
                params.append(f"{folder}/%")
            sql += " GROUP BY value ORDER BY cnt DESC, value LIMIT ?"
            params.append(limit)
        return [(r["value"], r["cnt"]) for r in c.execute(sql, params).fetchall()]

    def query_tasks(
        self, checked: bool | None = False, limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Query tasks.  checked=None means all, False=open, True=done."""
        c = self.conn
        sql = "SELECT note_path, text, checked, due, priority, done FROM tasks"
        params: list[Any] = []
        if checked is not None:
            sql += " WHERE checked = ?"
            params.append(int(checked))
        sql += " ORDER BY due, note_path LIMIT ?"
        params.append(limit)
        return [dict(r) for r in c.execute(sql, params).fetchall()]

    def query_reminders(
        self, checked: bool | None = False, limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Query reminders.  checked=None means all, False=pending, True=done."""
        c = self.conn
        sql = "SELECT note_path, text, checked, remind_on, repeat, done FROM reminders"
        params: list[Any] = []
        if checked is not None:
            sql += " WHERE checked = ?"
            params.append(int(checked))
        sql += " ORDER BY remind_on, note_path LIMIT ?"
        params.append(limit)
        return [dict(r) for r in c.execute(sql, params).fetchall()]

    def query_events(
        self, date_from: str = "", date_to: str = "", limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Query events that overlap with a date range (inclusive).

        An event overlaps the window [date_from, date_to] if:
          - its start date falls within the window, OR
          - it has an end_date and the window falls between start and end.
        """
        c = self.conn
        cols = ("note_path, date, time, end_time, title, location, description, "
                "end_date, all_day, attendees")
        sql = f"SELECT {cols} FROM events"
        clauses: list[str] = []
        params: list[Any] = []
        if date_from and date_to:
            # Event overlaps window if: event.date <= window.end AND max(event.date, event.end_date) >= window.start
            clauses.append(
                "(date <= ? AND (CASE WHEN end_date != '' THEN end_date ELSE date END) >= ?)"
            )
            params.extend([date_to, date_from])
        elif date_from:
            clauses.append(
                "((CASE WHEN end_date != '' THEN end_date ELSE date END) >= ?)"
            )
            params.append(date_from)
        elif date_to:
            clauses.append("date <= ?")
            params.append(date_to)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY date, time, title LIMIT ?"
        params.append(limit)
        return [dict(r) for r in c.execute(sql, params).fetchall()]

    def query_tags(self, tag: str, limit: int = 500) -> list[str]:
        """Find note paths having a specific tag."""
        c = self.conn
        return [
            r["note_path"]
            for r in c.execute(
                "SELECT note_path FROM tags WHERE tag = ? ORDER BY note_path LIMIT ?",
                (tag, limit),
            ).fetchall()
        ]

    def query_aliases(self, alias: str) -> list[str]:
        """Find note paths having a specific alias (case-insensitive)."""
        c = self.conn
        return [
            r["note_path"]
            for r in c.execute(
                "SELECT note_path FROM aliases WHERE alias = ? COLLATE NOCASE",
                (alias,),
            ).fetchall()
        ]

    def find_duplicate_titles_db(self, limit: int = 200) -> dict[str, list[str]]:
        """Find groups of notes sharing the same title."""
        c = self.conn
        rows = c.execute(
            "SELECT title, GROUP_CONCAT(path, '||') AS paths "
            "FROM notes WHERE path NOT LIKE '00_Index/%' "
            "GROUP BY title COLLATE NOCASE HAVING COUNT(*) > 1 "
            "ORDER BY COUNT(*) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {r["title"]: r["paths"].split("||") for r in rows}

    def find_alias_conflicts_db(self, limit: int = 200) -> dict[str, list[str]]:
        """Find aliases shared by multiple notes."""
        c = self.conn
        rows = c.execute(
            "SELECT alias, GROUP_CONCAT(note_path, '||') AS paths "
            "FROM aliases "
            "GROUP BY alias COLLATE NOCASE HAVING COUNT(*) > 1 "
            "ORDER BY COUNT(*) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {r["alias"]: r["paths"].split("||") for r in rows}

    def find_merge_candidates_db(
        self,
        limit: int = 30,
        min_score: int = 15,
        folder: str = "",
        exclude_folders: tuple[str, ...] = ("00_Index", "50_Templates", "40_Attachments"),
    ) -> list[dict[str, Any]]:
        """Find pairs of notes that may be candidates for merging.

        Uses multiple signals from the SQLite index — no file reads needed:
          - Tag overlap (Jaccard similarity)
          - Shared wikilink targets
          - Title word overlap
          - Same type+status metadata
          - FTS term overlap (top terms per note)

        Returns a list of dicts sorted by score descending:
          [{path_a, path_b, score, reasons: [str]}, ...]
        """
        c = self.conn

        # -- 1. Load note metadata from DB --
        notes: dict[str, dict[str, Any]] = {}
        folder_prefix = folder.rstrip("/") + "/" if folder else ""
        for row in c.execute("SELECT path, title, type, status FROM notes").fetchall():
            p = row["path"]
            if any(p.startswith(f"{ef}/") for ef in exclude_folders):
                continue
            if folder_prefix and not p.startswith(folder_prefix):
                continue
            notes[p] = {
                "title": row["title"],
                "type": row["type"],
                "status": row["status"],
                "title_words": set(w.lower() for w in row["title"].split() if len(w) >= 3),
            }

        if len(notes) < 2:
            return []

        # -- 2. Build tag sets per note --
        tags_by_note: dict[str, set[str]] = {p: set() for p in notes}
        for row in c.execute("SELECT note_path, tag FROM tags").fetchall():
            if row["note_path"] in tags_by_note:
                tags_by_note[row["note_path"]].add(row["tag"])

        # -- 3. Build wikilink target sets per note --
        links_by_note: dict[str, set[str]] = {p: set() for p in notes}
        for row in c.execute("SELECT source_path, target FROM wikilinks").fetchall():
            if row["source_path"] in links_by_note:
                links_by_note[row["source_path"]].add(row["target"].lower())

        # -- 4. Build FTS term sets (top N terms per note body) --
        terms_by_note: dict[str, set[str]] = {p: set() for p in notes}
        for path in notes:
            try:
                # Use FTS5 to get the indexed body text snippet and extract terms
                row = c.execute(
                    "SELECT body FROM fts WHERE path = ?", (path,)
                ).fetchone()
                if row and row["body"]:
                    body = row["body"]
                    word_counts: dict[str, int] = {}
                    _stop = {"the", "and", "for", "with", "that", "this", "from",
                             "are", "was", "were", "you", "your", "have", "has",
                             "not", "but", "can", "will", "use", "using", "into",
                             "about", "note", "notes", "file", "files", "also",
                             "when", "which", "there", "their", "been", "more",
                             "than", "each", "other", "some", "would", "should"}
                    for w in re.findall(r"[a-z0-9_-]{3,}", body.lower()):
                        if w not in _stop:
                            word_counts[w] = word_counts.get(w, 0) + 1
                    top_terms = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:40]
                    terms_by_note[path] = {w for w, _ in top_terms}
            except Exception:
                pass

        # -- 5. Build candidate pairs via inverted index (avoids O(n²)) --
        # Only compare notes that share at least one tag, link target,
        # or title word — the vast majority of pairs share nothing.
        candidate_pairs: set[tuple[str, str]] = set()

        def _add_pair(a: str, b: str) -> None:
            if a < b:
                candidate_pairs.add((a, b))
            else:
                candidate_pairs.add((b, a))

        # Invert tags → notes that share a tag
        tag_to_notes: dict[str, list[str]] = {}
        for p, tset in tags_by_note.items():
            for t in tset:
                tag_to_notes.setdefault(t, []).append(p)
        for group in tag_to_notes.values():
            if 2 <= len(group) <= 50:  # skip very common tags (too noisy)
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        _add_pair(group[i], group[j])

        # Invert link targets → notes that link to the same thing
        target_to_notes: dict[str, list[str]] = {}
        for p, lset in links_by_note.items():
            for t in lset:
                target_to_notes.setdefault(t, []).append(p)
        for group in target_to_notes.values():
            if 2 <= len(group) <= 30:
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        _add_pair(group[i], group[j])

        # Invert title words → notes that share a title word
        word_to_notes: dict[str, list[str]] = {}
        for p, meta in notes.items():
            for w in meta["title_words"]:
                word_to_notes.setdefault(w, []).append(p)
        for group in word_to_notes.values():
            if 2 <= len(group) <= 20:
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        _add_pair(group[i], group[j])

        # -- 6. Score only the candidate pairs --
        candidates: list[dict[str, Any]] = []

        for pa, pb in candidate_pairs:
            if pa not in notes or pb not in notes:
                continue
            na, nb = notes[pa], notes[pb]
            score = 0
            reasons: list[str] = []

            # Tag Jaccard
            ta, tb = tags_by_note[pa], tags_by_note[pb]
            if ta and tb:
                overlap = ta & tb
                jaccard = len(overlap) / len(ta | tb)
                if jaccard >= 0.5:
                    tag_score = int(jaccard * 20)
                    score += tag_score
                    reasons.append(f"tags({len(overlap)}/{len(ta | tb)})")

            # Shared wikilink targets
            la, lb = links_by_note[pa], links_by_note[pb]
            if la and lb:
                shared = la & lb
                if len(shared) >= 2:
                    link_score = min(len(shared) * 3, 15)
                    score += link_score
                    reasons.append(f"links({len(shared)})")

            # Title word overlap
            tw_a, tw_b = na["title_words"], nb["title_words"]
            if tw_a and tw_b:
                title_overlap = tw_a & tw_b
                if title_overlap:
                    t_score = min(len(title_overlap) * 6, 18)
                    score += t_score
                    reasons.append(f"title({','.join(sorted(title_overlap))})")

            # Same type bonus (if both are same non-empty type)
            if na["type"] and na["type"] == nb["type"]:
                score += 3
                reasons.append(f"type={na['type']}")

            # FTS term overlap (Jaccard on top terms)
            fts_a, fts_b = terms_by_note[pa], terms_by_note[pb]
            if fts_a and fts_b:
                fts_overlap = fts_a & fts_b
                union_size = len(fts_a | fts_b)
                if union_size > 0:
                    fts_jaccard = len(fts_overlap) / union_size
                    if fts_jaccard >= 0.2:
                        fts_score = int(fts_jaccard * 25)
                        score += fts_score
                        reasons.append(f"content({len(fts_overlap)}/{union_size})")

            if score >= min_score:
                candidates.append({
                    "path_a": pa,
                    "path_b": pb,
                    "score": score,
                    "reasons": reasons,
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:limit]

    def find_broken_wikilinks_db(
        self, limit: int = 500, offset: int = 0, folder: str = "",
    ) -> tuple[list[dict[str, str]], int]:
        """Find wikilinks whose target doesn't match any existing note path, basename, or alias.

        Returns (items, total_count) so callers can paginate.
        """
        c = self.conn
        # Build sets of known targets from notes (markdown) …
        existing_targets: set[str] = set()
        existing_basenames: set[str] = set()
        for row in c.execute("SELECT path FROM notes").fetchall():
            target = normalize_note_target(row["path"])
            existing_targets.add(target)
            existing_basenames.add(Path(target).name)
        # … and from all vault files (images, PDFs, etc.)
        for row in c.execute("SELECT path FROM files").fetchall():
            fpath = unicodedata.normalize("NFC", row["path"].strip().replace("\\", "/").strip("/"))
            existing_targets.add(fpath)
            existing_basenames.add(Path(fpath).name)
        # … and from aliases (case-insensitive to match Obsidian behavior)
        existing_aliases: set[str] = set()
        for row in c.execute("SELECT alias FROM aliases").fetchall():
            existing_aliases.add(row["alias"].strip().lower())

        # Optionally restrict to sources under a folder prefix
        if folder:
            folder_prefix = folder.rstrip("/") + "/"
            wikilink_rows = c.execute(
                "SELECT source_path, target, display_text FROM wikilinks "
                "WHERE source_path LIKE ?",
                (f"{folder_prefix}%",),
            ).fetchall()
        else:
            wikilink_rows = c.execute(
                "SELECT source_path, target, display_text FROM wikilinks"
            ).fetchall()

        broken: list[dict[str, str]] = []
        for row in wikilink_rows:
            target = normalize_note_target(row["target"])
            basename = Path(target).name
            if (
                target not in existing_targets
                and basename not in existing_basenames
                and basename.lower() not in existing_aliases
                and target.lower() not in existing_aliases
            ):
                display = row["display_text"]
                raw_link = f"[[{row['target']}{'|' + display if display else ''}]]"
                broken.append({
                    "source": row["source_path"],
                    "link": raw_link,
                    "target": target,
                })
        total = len(broken)
        return broken[offset:offset + limit], total

    def count_notes_missing_field(self, field: str, folder: str = "") -> int:
        """Count notes that do NOT have a particular frontmatter key."""
        c = self.conn
        if field == "tags":
            sub = "SELECT 1 FROM tags t WHERE t.note_path = n.path"
        elif field == "aliases":
            sub = "SELECT 1 FROM aliases a WHERE a.note_path = n.path"
        else:
            sub = "SELECT 1 FROM frontmatter f WHERE f.note_path = n.path AND f.key = ?"
        sql = f"SELECT COUNT(*) AS cnt FROM notes n WHERE NOT EXISTS ({sub})"
        params: list[Any] = [] if field in ("tags", "aliases") else [field]
        if folder:
            sql += " AND n.path LIKE ?"
            params.append(f"{folder}/%")
        row = c.execute(sql, params).fetchone()
        return row["cnt"] if row else 0

    def count_notes(self, folder: str = "") -> int:
        """Count total indexed notes."""
        c = self.conn
        if folder:
            row = c.execute(
                "SELECT COUNT(*) AS cnt FROM notes WHERE path LIKE ?",
                (f"{folder}/%",),
            ).fetchone()
        else:
            row = c.execute("SELECT COUNT(*) AS cnt FROM notes").fetchone()
        return row["cnt"] if row else 0

    def db_stats(self) -> dict[str, int]:
        """Return counts of rows in each table."""
        c = self.conn
        stats: dict[str, int] = {}
        for tbl in ("files", "notes", "frontmatter", "tags", "aliases", "wikilinks",
                    "tasks", "reminders", "events", "note_access", "revisions"):
            try:
                row = c.execute(f"SELECT COUNT(*) AS cnt FROM {tbl}").fetchone()
                stats[tbl] = row["cnt"] if row else 0
            except sqlite3.OperationalError:
                stats[tbl] = 0
        return stats

    # -- access tracking (hotness scoring) ------------------------------------

    def record_access(self, rel_path: str):
        """Increment access counter for a note."""
        c = self.conn
        now = datetime.now().isoformat(timespec="seconds")
        c.execute(
            "INSERT INTO note_access (path, access_count, last_accessed) "
            "VALUES (?, 1, ?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "access_count = access_count + 1, last_accessed = ?",
            (rel_path, now, now),
        )
        c.commit()

    def get_access_stats(self, rel_path: str) -> dict[str, Any]:
        """Get access count and last accessed time for a note."""
        row = self.conn.execute(
            "SELECT access_count, last_accessed FROM note_access WHERE path = ?",
            (rel_path,),
        ).fetchone()
        if row:
            return {"access_count": row["access_count"], "last_accessed": row["last_accessed"]}
        return {"access_count": 0, "last_accessed": ""}

    def top_accessed(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get most frequently accessed notes."""
        rows = self.conn.execute(
            "SELECT path, access_count, last_accessed FROM note_access "
            "ORDER BY access_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- vault overview -------------------------------------------------------

    def vault_overview_data(self) -> dict[str, Any]:
        """Compile a compact structural overview of the vault from the DB.

        Returns a dict with:
          - folder_summary: [{folder, note_count, top_tags}]
          - recent_notes: recently modified notes
          - hot_notes: most accessed notes
          - tag_cloud: top tags by usage count
          - type_breakdown: note counts by type
          - stale_active: notes marked active but not modified in 60+ days
          - totals: overall counts
        """
        c = self.conn

        # -- Folder summary with note counts and top tags --
        folder_data: dict[str, dict[str, Any]] = {}
        for row in c.execute("SELECT path, type FROM notes").fetchall():
            parts = row["path"].split("/", 1)
            folder = parts[0] if len(parts) > 1 else "(root)"
            if folder not in folder_data:
                folder_data[folder] = {"count": 0, "types": {}}
            folder_data[folder]["count"] += 1
            t = row["type"] or "(none)"
            folder_data[folder]["types"][t] = folder_data[folder]["types"].get(t, 0) + 1

        # Top tags per folder
        folder_tags: dict[str, dict[str, int]] = {}
        for row in c.execute(
            "SELECT n.path, t.tag FROM notes n JOIN tags t ON n.path = t.note_path"
        ).fetchall():
            folder = row["path"].split("/", 1)[0]
            if folder not in folder_tags:
                folder_tags[folder] = {}
            folder_tags[folder][row["tag"]] = folder_tags[folder].get(row["tag"], 0) + 1

        folder_summary = []
        for f in sorted(folder_data.keys()):
            fd = folder_data[f]
            top_tags_raw = folder_tags.get(f, {})
            top_tags = sorted(top_tags_raw, key=lambda t: top_tags_raw[t], reverse=True)[:5]
            folder_summary.append({
                "folder": f,
                "note_count": fd["count"],
                "types": fd["types"],
                "top_tags": top_tags,
            })

        # -- Recently modified notes (from files table mtime) --
        cutoff_ns = int((datetime.now().timestamp() - 7 * 86400) * 1e9)
        recent = [
            {"path": r["path"], "mtime": datetime.fromtimestamp(r["mtime_ns"] / 1e9).strftime("%Y-%m-%d %H:%M")}
            for r in c.execute(
                "SELECT path, mtime_ns FROM files WHERE suffix = '.md' "
                "AND mtime_ns > ? ORDER BY mtime_ns DESC LIMIT 15",
                (cutoff_ns,),
            ).fetchall()
        ]

        # -- Hot notes (most accessed) --
        hot = self.top_accessed(limit=10)

        # -- Tag cloud (top 25 tags by usage) --
        tag_cloud = [
            {"tag": r["tag"], "count": r["cnt"]}
            for r in c.execute(
                "SELECT tag, COUNT(*) AS cnt FROM tags "
                "GROUP BY tag ORDER BY cnt DESC LIMIT 25"
            ).fetchall()
        ]

        # -- Type breakdown --
        type_breakdown = {
            r["type"] or "(none)": r["cnt"]
            for r in c.execute(
                "SELECT type, COUNT(*) AS cnt FROM notes GROUP BY type ORDER BY cnt DESC"
            ).fetchall()
        }

        # -- Stale active notes (active but not modified in 60+ days) --
        stale_cutoff_ns = int((datetime.now().timestamp() - 60 * 86400) * 1e9)
        stale_rows = c.execute(
            "SELECT n.path, n.title FROM notes n "
            "JOIN files f ON n.path = f.path "
            "WHERE n.status = 'active' AND f.mtime_ns < ? "
            "ORDER BY f.mtime_ns ASC LIMIT 15",
            (stale_cutoff_ns,),
        ).fetchall()
        stale_active = [{"path": r["path"], "title": r["title"]} for r in stale_rows]

        # -- Totals --
        stats = self.db_stats()

        return {
            "folder_summary": folder_summary,
            "recent_notes": recent,
            "hot_notes": hot,
            "tag_cloud": tag_cloud,
            "type_breakdown": type_breakdown,
            "stale_active": stale_active,
            "totals": stats,
        }

    def note_context_data(self, rel_path: str) -> dict[str, Any]:
        """Aggregate a note's full neighborhood from the DB in one call.

        Returns:
          - metadata: title, type, status, tags, aliases, word_count
          - forward_links: notes this note links to
          - backlinks: notes that link to this note
          - tag_siblings: other notes sharing the most tags (top 8)
          - access_stats: access count and last accessed
          - recent_revisions: last 5 revisions (id, saved_at, word_count)
        """
        c = self.conn

        # -- Metadata --
        note_row = c.execute(
            "SELECT title, type, status, word_count FROM notes WHERE path = ?",
            (rel_path,),
        ).fetchone()
        if not note_row:
            return {"error": f"Note not found in index: {rel_path}"}

        tags = [r["tag"] for r in c.execute(
            "SELECT tag FROM tags WHERE note_path = ?", (rel_path,)
        ).fetchall()]
        aliases = [r["alias"] for r in c.execute(
            "SELECT alias FROM aliases WHERE note_path = ?", (rel_path,)
        ).fetchall()]

        metadata = {
            "title": note_row["title"],
            "type": note_row["type"],
            "status": note_row["status"],
            "word_count": note_row["word_count"],
            "tags": tags,
            "aliases": aliases,
        }

        # -- Forward links --
        forward = [
            {"target": r["target"], "display": r["display_text"]}
            for r in c.execute(
                "SELECT target, display_text FROM wikilinks WHERE source_path = ?",
                (rel_path,),
            ).fetchall()
        ]

        # -- Backlinks --
        target = normalize_note_target(rel_path)
        target_basename = Path(target).name
        backlink_rows = c.execute(
            "SELECT DISTINCT source_path FROM wikilinks "
            "WHERE target = ? OR target LIKE ?",
            (target, f"%/{target_basename}"),
        ).fetchall()
        backlinks = [r["source_path"] for r in backlink_rows if r["source_path"] != rel_path]

        # -- Tag siblings (notes sharing the most tags) --
        if tags:
            placeholders = ",".join("?" * len(tags))
            sibling_rows = c.execute(
                f"SELECT note_path, COUNT(*) AS shared "
                f"FROM tags WHERE tag IN ({placeholders}) AND note_path != ? "
                f"GROUP BY note_path ORDER BY shared DESC LIMIT 8",
                (*tags, rel_path),
            ).fetchall()
            tag_siblings = [
                {"path": r["note_path"], "shared_tags": r["shared"]}
                for r in sibling_rows
            ]
        else:
            tag_siblings = []

        # -- Access stats --
        access = self.get_access_stats(rel_path)

        # -- Recent revisions --
        rev_rows = c.execute(
            "SELECT id, saved_at, word_count FROM revisions "
            "WHERE path = ? ORDER BY id DESC LIMIT 5",
            (rel_path,),
        ).fetchall()
        revisions = [dict(r) for r in rev_rows]

        return {
            "metadata": metadata,
            "forward_links": forward,
            "backlinks": backlinks,
            "tag_siblings": tag_siblings,
            "access_stats": access,
            "recent_revisions": revisions,
        }

    def find_cooccurring_tags(self, tag: str, limit: int = 10) -> list[tuple[str, int]]:
        """Find tags that frequently co-occur with the given tag.

        Returns [(co_tag, count)] sorted by count descending.
        """
        c = self.conn
        rows = c.execute(
            "SELECT t2.tag, COUNT(*) AS cnt "
            "FROM tags t1 JOIN tags t2 ON t1.note_path = t2.note_path "
            "WHERE t1.tag = ? AND t2.tag != ? "
            "GROUP BY t2.tag ORDER BY cnt DESC LIMIT ?",
            (tag, tag, limit),
        ).fetchall()
        return [(r["tag"], r["cnt"]) for r in rows]

    # -- revision tracking ----------------------------------------------------

    def save_revision(self, rel_path: str, content: str, content_hash: str = ""):
        """Save a snapshot of note content before it is overwritten."""
        c = self.conn
        if not content_hash:
            content_hash = self._hash_content(content)

        # Skip if content hasn't changed since last revision
        last = c.execute(
            "SELECT content_hash FROM revisions WHERE path = ? ORDER BY id DESC LIMIT 1",
            (rel_path,),
        ).fetchone()
        if last and last["content_hash"] == content_hash:
            return

        now = datetime.now().isoformat(timespec="seconds")
        wc = count_words(content)
        c.execute(
            "INSERT INTO revisions (path, content, content_hash, saved_at, word_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (rel_path, content, content_hash, now, wc),
        )

        # Keep max 20 revisions per note — prune oldest
        c.execute(
            "DELETE FROM revisions WHERE path = ? AND id NOT IN "
            "(SELECT id FROM revisions WHERE path = ? ORDER BY id DESC LIMIT 20)",
            (rel_path, rel_path),
        )
        c.commit()

    def get_revisions(self, rel_path: str, limit: int = 20) -> list[dict[str, Any]]:
        """List revision history for a note (most recent first)."""
        rows = self.conn.execute(
            "SELECT id, path, content_hash, saved_at, word_count FROM revisions "
            "WHERE path = ? ORDER BY id DESC LIMIT ?",
            (rel_path, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_revision_content(self, revision_id: int) -> dict[str, Any] | None:
        """Get the full content of a specific revision."""
        row = self.conn.execute(
            "SELECT id, path, content, content_hash, saved_at, word_count FROM revisions "
            "WHERE id = ?",
            (revision_id,),
        ).fetchone()
        return dict(row) if row else None



# Singleton — initialized lazily on first use
_vault_index: VaultIndex | None = None


def get_vault_index() -> VaultIndex:
    """Get (or create) the singleton VaultIndex, auto-syncing on first call."""
    global _vault_index
    if _vault_index is None:
        _vault_index = VaultIndex(get_vault_root())
        _vault_index.sync()
    return _vault_index


def _notify_index_of_write(path: Path, text: str | None = None):
    """Call after writing/updating a file so the index stays current.

    Index updates are best-effort: the markdown file on disk is the source
    of truth, and a stale index can always be rebuilt with rebuild_vault_index.
    Failures here MUST NOT bubble up — otherwise every write tool returns an
    error to the caller even though the markdown write succeeded (e.g. when
    running in a fresh container where the index hasn't been built yet).
    """
    if _vault_index is None:
        return
    try:
        if text is not None:
            _vault_index.sync_note_text(path, text)
        else:
            _vault_index.sync_file(path)
    except Exception:
        # Index out of sync — non-fatal. Caller will see "ok" on the write
        # tool and can run rebuild_vault_index() if results look stale.
        pass


def _notify_index_of_delete(rel_path: str):
    """Call after deleting a file so the index drops it. Best-effort — see
    _notify_index_of_write for why exceptions are swallowed."""
    if _vault_index is None:
        return
    try:
        _vault_index.remove_path(rel_path)
    except Exception:
        pass




# =============================================================================
# Task / event line parsing helpers (used by VaultIndex.sync + the tool modules)
# =============================================================================
_TASK_BULLET_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<box>[ xX])\]\s+(?P<rest>.*)$")
_KNOWN_META_KEYS = {"due", "priority", "done", "remind_on", "repeat", "id"}


def parse_task_bullet(line: str) -> dict[str, Any] | None:
    m = _TASK_BULLET_RE.match(line.rstrip())
    if not m:
        return None

    rest = m.group("rest")
    parts = [p.strip() for p in rest.split("—")]
    text = parts[0]
    meta: dict[str, str] = {}
    extra: dict[str, str] = {}

    for p in parts[1:]:
        if ":" not in p:
            continue
        key, value = p.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in _KNOWN_META_KEYS:
            meta[key] = value
        else:
            extra[key] = value

    return {
        "checked": m.group("box").lower() == "x",
        "indent": m.group("indent"),
        "text": text,
        "due": meta.get("due", ""),
        "priority": meta.get("priority", ""),
        "done": meta.get("done", ""),
        "remind_on": meta.get("remind_on", ""),
        "repeat": meta.get("repeat", ""),
        "id": meta.get("id", ""),
        "extra": extra,
        "raw": line,
    }


def format_task_bullet(
    text: str,
    due: str = "",
    priority: str = "",
    done: str = "",
    remind_on: str = "",
    repeat: str = "",
    task_id: str = "",
    extra: dict[str, str] | None = None,
    checked: bool = False,
    indent: str = "",
) -> str:
    box = "[x]" if checked else "[ ]"
    parts: list[str] = [text.strip()]
    if due.strip():
        parts.append(f"due: {due.strip()}")
    if priority.strip():
        parts.append(f"priority: {priority.strip()}")
    if remind_on.strip():
        parts.append(f"remind_on: {remind_on.strip()}")
    if repeat.strip():
        parts.append(f"repeat: {repeat.strip()}")
    if task_id.strip():
        parts.append(f"id: {task_id.strip()}")
    if done.strip():
        parts.append(f"done: {done.strip()}")
    if extra:
        for k, v in extra.items():
            if v.strip():
                parts.append(f"{k}: {v.strip()}")
    return f"{indent}- {box} " + " — ".join(parts)


def parse_iso_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return None


def find_task_lines_in_section(text: str, section: str) -> list[tuple[int, str, dict[str, Any]]]:
    bounds = find_section_bounds(text, section)
    if bounds is None:
        return []
    start, end = bounds
    section_text = text[start:end]
    results: list[tuple[int, str, dict[str, Any]]] = []
    cursor = start
    for line in section_text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        parsed = parse_task_bullet(stripped)
        if parsed:
            results.append((cursor, stripped, parsed))
        cursor += len(line)
    return results


# Headings whose contents we deliberately exclude from task indexing. Keyed by
# lower-cased, stripped heading text (without leading `#`s). Matching is
# substring-ish via `_section_is_skipped` below so "Old Tasks", "Archived
# stuff", "Reference Material", etc. all hit.
_TASK_SKIP_HEADINGS: tuple[str, ...] = (
    "done",
    "completed",
    "archive",
    "archived",
    "old",
    "deprecated",
    "abandoned",
    "cancelled",
    "canceled",
    "references",
    "reference",
    "related notes",
    "related",
    "resources",
    "links",
    "appendix",
    "history",
    # Note: deliberately NOT skipping "backlog" — backlog items are real open
    # work and should be visible. Same for "someday", "later", "ideas".
)

# Heading we treat as the *reminders* bucket (gets routed to the reminders
# table instead of tasks). Same matching rules as the skip-list.
_REMINDERS_HEADINGS: tuple[str, ...] = ("reminders", "reminder")


def _heading_text_matches(heading: str, candidates: tuple[str, ...]) -> bool:
    """Return True if any candidate appears as a whole word in the heading.

    Matching is case-insensitive and word-boundary-aware so "Done" matches
    "## Done" and "## ✅ Done — 2026" but NOT "## Doing".
    """
    h = heading.lower()
    for cand in candidates:
        # Word-boundary match: cand surrounded by non-word chars or string ends.
        if re.search(rf"(?:^|\W){re.escape(cand)}(?:\W|$)", h):
            return True
    return False


def find_all_task_lines(
    text: str,
    skip_headings: tuple[str, ...] = _TASK_SKIP_HEADINGS,
    reminder_headings: tuple[str, ...] = _REMINDERS_HEADINGS,
) -> tuple[list[tuple[int, str, dict[str, Any]]], list[tuple[int, str, dict[str, Any]]]]:
    """Walk the whole note and return (task_lines, reminder_lines).

    Why this exists: the previous indexer only looked at `## Tasks` and
    `## Reminders` sections, which silently dropped every checkbox in a note
    that used a different heading convention (e.g. `## High Priority`,
    `## General To-Do`). That made entire to-do notes invisible to the
    morning brief and `query_tasks()`.

    The walk tracks the active heading stack and:
      - Routes bullets under any `reminder_headings` heading (at any depth)
        to the reminders bucket.
      - Skips bullets under any `skip_headings` heading (archive-style:
        Done / Completed / References / Related Notes / etc.).
      - Includes everything else in the tasks bucket — including loose
        checkboxes at the top of a file before any heading.

    A skip/reminder mark applies until a sibling-or-shallower heading closes
    the section, mirroring `find_section_bounds`.
    """
    tasks: list[tuple[int, str, dict[str, Any]]] = []
    reminders: list[tuple[int, str, dict[str, Any]]] = []
    # heading_stack[i] = (level, heading_text) for headings currently open
    heading_stack: list[tuple[int, str]] = []
    # Bit-flags propagated from any active heading in the stack
    skip_active = False
    reminder_active = False

    def _recompute_flags() -> tuple[bool, bool]:
        sk = any(_heading_text_matches(h, skip_headings) for _, h in heading_stack)
        rm = any(_heading_text_matches(h, reminder_headings) for _, h in heading_stack)
        return sk, rm

    heading_re = re.compile(r"^(?P<hashes>#+)\s+(?P<title>.+?)\s*$")
    cursor = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        m = heading_re.match(stripped)
        if m:
            level = len(m.group("hashes"))
            title = m.group("title").strip()
            # Pop any stack entries at this level or deeper (they're closed now)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            skip_active, reminder_active = _recompute_flags()
            cursor += len(line)
            continue
        parsed = parse_task_bullet(stripped)
        if parsed:
            if skip_active:
                pass  # archived / referenced — don't index
            elif reminder_active:
                reminders.append((cursor, stripped, parsed))
            else:
                tasks.append((cursor, stripped, parsed))
        cursor += len(line)
    return tasks, reminders


# -- Schedule / event parsing --------------------------------------------------

# Format: - HH:MM[-HH:MM] Title [@ Location] [— description]
_EVENT_RE = re.compile(
    r"^-\s+"
    r"(?P<time>\d{1,2}:\d{2})"
    r"(?:\s*[-–]\s*(?P<end>\d{1,2}:\d{2})"
    r"(?:\s*\(\+(?P<plus>\d+)d\))?"  # optional (+Nd) cross-day marker
    r")?"
    r"\s+(?P<rest>.+)$"
)

_ALLDAY_RE = re.compile(
    r"^-\s+all[- ]day\s+(?P<rest>.+)$", re.IGNORECASE,
)


def parse_event_line(line: str) -> dict[str, str] | None:
    """Parse a single schedule bullet into an event dict.

    Supported formats:
      - 14:00–16:00 Meeting @ Office — weekly sync
      - 22:00–06:00 (+1d) Flight ZRH→ICN        (cross-day)
      - all-day Conference                        (all-day event)
      - 9:30 Standup
    """
    stripped = line.strip()

    # Try all-day pattern first
    m_ad = _ALLDAY_RE.match(stripped)
    if m_ad:
        rest = m_ad.group("rest")
        location = ""
        description = ""
        if " — " in rest:
            rest, description = rest.split(" — ", 1)
            description = description.strip()
        elif " -- " in rest:
            rest, description = rest.split(" -- ", 1)
            description = description.strip()
        if " @ " in rest:
            rest, location = rest.split(" @ ", 1)
            location = location.strip()
        return {
            "time": "",
            "end_time": "",
            "title": rest.strip(),
            "location": location,
            "description": description,
            "all_day": "1",
            "plus_days": "",
        }

    m = _EVENT_RE.match(stripped)
    if not m:
        return None
    time_str = m.group("time")
    end_str = m.group("end") or ""
    plus_days = m.group("plus") or ""
    rest = m.group("rest")

    # Split off location (@ ...) and description (— ...)
    location = ""
    description = ""
    # Check for — description first
    if " — " in rest:
        rest, description = rest.split(" — ", 1)
        description = description.strip()
    elif " -- " in rest:
        rest, description = rest.split(" -- ", 1)
        description = description.strip()

    # Check for @ location
    if " @ " in rest:
        rest, location = rest.split(" @ ", 1)
        location = location.strip()

    title = rest.strip()
    return {
        "time": time_str,
        "end_time": end_str,
        "title": title,
        "location": location,
        "description": description,
        "all_day": "",
        "plus_days": plus_days,
    }


def parse_schedule_section(text: str) -> list[dict[str, str]]:
    """Extract all events from the ## Schedule section of a note."""
    bounds = find_section_bounds(text, "Schedule")
    if bounds is None:
        return []
    start, end = bounds
    section_text = text[start:end]
    events: list[dict[str, str]] = []
    for line in section_text.splitlines():
        ev = parse_event_line(line)
        if ev:
            events.append(ev)
    return events
