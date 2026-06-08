"""Morning/weekly; Session context

@mcp.tool() definitions live here. The shared FastMCP instance is imported
from the package __init__.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Optional

from .. import mcp
from ..core import *  # noqa: F401, F403  — includes parse_iso_date


# ─── from original L7651-7829: Morning/weekly ───
# =============================================================================
# Morning briefing & weekly review
# =============================================================================


def _llm_prose_summary(
    role: str,
    structured_data: str,
    *,
    max_tokens: int = 200,
) -> str:
    """Optionally generate a leading prose paragraph for a routine summary.

    Returns empty string when no LLM is configured — callers should treat
    prose as an enhancement, not a requirement.

    ``role``: short label of what we're summarizing ("morning briefing",
    "weekly review", "evening wrapup"). Goes into the system prompt.
    """
    try:
        from .. import llm
    except ImportError:
        return ""
    if not llm.is_configured():
        return ""
    system = (
        f"You are Iris, a friendly personal-vault assistant writing a {role} "
        "for the user. Given the structured data below, write a "
        "single short paragraph (2–4 sentences) that captures the highlights "
        "in a natural voice. Don't enumerate everything — pick what matters. "
        "Don't use bullet points; the structured list follows separately."
    )
    try:
        return llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": structured_data},
            ],
            max_tokens=max_tokens,
            temperature=0.6,
            think=False,   # routine prose — skip chain-of-thought
        ).strip()
    except llm.LLMError:
        return ""


_PAREN_TRAIL_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _normalize_event_title(title: str) -> str:
    """Lowercase + strip trailing parenthetical bits + collapse spaces.

    Used to dedupe near-identical events. "Pfingstmontag" and
    "Pfingstmontag (No Work)" normalize to the same key — they're
    the same Swiss holiday from different iCal feeds.
    """
    s = (title or "").strip()
    # Strip any trailing "(...)" group (possibly nested-once but we
    # only handle one level — good enough for the holiday case).
    while True:
        new = _PAREN_TRAIL_RE.sub("", s)
        if new == s:
            break
        s = new
    return " ".join(s.lower().split())


def _dedupe_events(events: list) -> list:
    """Drop near-identical events (same date + same normalized title +
    same start time). Keeps the FIRST occurrence — events come from
    ``query_events`` in DB-insertion order, so the one indexed first
    wins. The original iCal-import order tends to be the cleaner
    title; cross-feed duplicates get the "(No Work)" / etc. suffix."""
    seen: set[tuple] = set()
    out: list = []
    for ev in events:
        key = (
            ev.get("date") or "",
            ev.get("time") or "",
            _normalize_event_title(ev.get("title") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def _tasks_completed_elsewhere(idx, open_tasks: list) -> set:
    """Detect open tasks whose text matches a CHECKED task elsewhere in
    the vault. Returns a set of normalized task-text strings that the
    brief should annotate with ⚠️ "may be done elsewhere".

    Common case: a TODO duplicated across two notes (e.g. "Book
    glamping" in a project note AND in a general to-do list). When
    the user checks one off, the brief still shows the other as open.
    This heuristic flags the duplicate so they can clean up.

    Normalization: lowercase + strip whitespace + first 60 chars.
    Not perfect — tasks with very different phrasings won't match —
    but catches the literal-copy-paste case which is the common one.
    """
    if not open_tasks:
        return set()
    open_norms = {
        (t.get("text") or "").lower().strip()[:60]
        for t in open_tasks if (t.get("text") or "").strip()
    }
    if not open_norms:
        return set()
    placeholders = ",".join("?" * len(open_norms))
    # Look for checked tasks whose normalized prefix matches any open one.
    # SQLite has no built-in normalize; do the LIKE-prefix check in Python.
    rows = idx.conn.execute(
        "SELECT lower(text) AS t FROM tasks WHERE checked = 1"
    ).fetchall()
    done_norms = {
        r["t"].strip()[:60]
        for r in rows if r["t"] and r["t"].strip()
    }
    return open_norms & done_norms


def _split_backlog_by_freshness(
    idx, note_paths: list, stale_days: int,
) -> tuple[set, set]:
    """Return (fresh_paths, stale_paths) — note paths whose source file
    has been modified within ``stale_days`` days vs. those older.

    Uses ``files.mtime_ns`` (set by the indexer). When a path has no
    row in ``files`` (rare — happens for tasks indexed from a now-
    deleted note), we treat it as STALE so dead-source backlog items
    don't surface.
    """
    if not note_paths:
        return set(), set()
    from datetime import datetime as _dt, timedelta as _td  # noqa: PLC0415
    cutoff_ns = int(
        (_dt.now() - _td(days=stale_days)).timestamp() * 1e9
    )
    unique = list(set(note_paths))
    placeholders = ",".join("?" * len(unique))
    rows = idx.conn.execute(
        f"SELECT path, mtime_ns FROM files WHERE path IN ({placeholders})",
        unique,
    ).fetchall()
    seen_mtimes = {r["path"]: int(r["mtime_ns"] or 0) for r in rows}
    fresh: set = set()
    stale: set = set()
    for p in unique:
        m = seen_mtimes.get(p)
        if m is None or m < cutoff_ns:
            stale.add(p)
        else:
            fresh.add(p)
    return fresh, stale


@mcp.tool()
def morning_briefing(
    date: str = "today",
    user_id: Optional[int] = None,
) -> str:
    """
    Comprehensive daily overview: schedule, tasks, reminders, inbox, projects.

    If an LLM is configured (IRIS_LLM_MODEL), a short prose summary is
    prepended above the structured sections. Otherwise the structured
    output is returned alone.

    ``date`` accepts natural language: "today", "tomorrow", etc.

    ``user_id`` — when provided, the brief is scoped to that user's
    per-user vault content (tasks/reminders/projects/inbox under
    ``users/<discord_id>/``). Calendar events stay global since they
    typically come from shared iCal feeds. Without user_id (legacy
    single-user / Claude Desktop), nothing is scoped — same content as
    before multi-user shipped. The Discord bot's per-user briefing
    loop always passes user_id; manual MCP callers can opt in.
    """
    resolved = resolve_natural_date(date)
    if resolved is None:
        return f"Cannot parse date: {date}"
    d = datetime.strptime(resolved, "%Y-%m-%d").date()
    today = datetime.now().date()
    idx = get_vault_index()

    # Per-user scoping: resolve user_id → vault_subdir prefix, used to
    # filter tasks/reminders/projects/inbox by note_path. None outside
    # multi-user setups; events stay global either way.
    vault_subdir: Optional[str] = None
    if user_id is not None:
        row = idx.conn.execute(
            "SELECT vault_subdir FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if row is not None and row["vault_subdir"]:
            vault_subdir = str(row["vault_subdir"]).rstrip("/")

    # NOTE: prior to this cleanup, morning_briefing used a stricter
    # variant that hid empty-path tasks AND vault-root (shared) tasks
    # from a user's brief. That was inconsistent with weekly_review /
    # daily_agenda (both correctly included shared content) and arguably
    # a bug — family to-dos placed at vault root should appear in
    # everyone's brief. Now unified on ``path_visible_to_user`` semantics.

    lines: list[str] = []

    # Header
    day_name = d.strftime("%A")
    if d == today:
        lines.append(f"# Good morning! {day_name}, {resolved}")
    else:
        lines.append(f"# Briefing for {day_name}, {resolved}")

    # 1. Schedule — v24: events are scoped per-user. Visibility filter
    # in core handles: own folder, shared (vault root), or attendee
    # opt-in via the ``attendees`` column on events. Look up speaker's
    # discord_id for the attendee check.
    user_discord_id: Optional[str] = None
    if user_id is not None:
        urow = idx.conn.execute(
            "SELECT discord_id FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if urow is not None:
            user_discord_id = urow["discord_id"]
    all_events = idx.query_events(date_from=resolved, date_to=resolved)
    if vault_subdir is not None:
        events = [
            ev for ev in all_events
            if event_visible_to_user(ev, vault_subdir, user_discord_id)
        ]
    else:
        events = all_events
    # De-duplicate near-identical events (same date + same fuzzy title).
    # Common case: a Swiss public-holiday iCal feed + a separate "no work"
    # feed both pull "Pfingstmontag" + "Pfingstmontag (No Work)" into the
    # same date → brief shows the holiday twice. Strip trailing parens +
    # whitespace + case for the compare key.
    events = _dedupe_events(events)
    lines.append(f"\n## Schedule ({len(events)} events)")
    if events:
        for ev in events:
            t = ev["time"] + (f"–{ev['end_time']}" if ev["end_time"] else "")
            loc = f" @ {ev['location']}" if ev["location"] else ""
            lines.append(f"- {t} {ev['title']}{loc}")
    else:
        lines.append("- No events scheduled.")

    # 2. Tasks — sorted into time-buckets + a dateless "by source" overview.
    # The bucketing logic deliberately includes ALL open tasks (not just the
    # `## Tasks` section) because the indexer now picks up checkboxes from
    # any heading. Without the by-source overview the brief would silently
    # hide entire backlog notes (Huawei To-Do, Plex queue, etc.).
    #
    # Multi-user (v22): when a user_id was passed, filter every task by
    # its note_path so only tasks under THIS user's per-user vault folder
    # show up. Owner content stays inside the owner's brief; mom's brief
    # only shows mom's notes' tasks.
    all_tasks = [
        t for t in idx.query_tasks(checked=False, limit=1000)
        if path_visible_to_user(t.get("note_path"), vault_subdir)
    ]
    overdue: list[dict] = []
    due_today: list[dict] = []
    upcoming: list[dict] = []
    far_future: list[dict] = []
    dateless: list[dict] = []
    for t in all_tasks:
        due_dt = parse_iso_date(t["due"])
        if due_dt is None:
            dateless.append(t)
            continue
        due_date = due_dt.date()
        if due_date < d:
            overdue.append(t)
        elif due_date == d:
            due_today.append(t)
        elif due_date <= d + timedelta(days=3):
            upcoming.append(t)
        else:
            far_future.append(t)

    # Detect duplicates (open task whose text matches a [x] task somewhere
    # else). Used to annotate the rendered tasks with ⚠️ so the user can
    # spot the "already done in project note, still [ ] in to-do list"
    # case before re-doing the work.
    done_elsewhere = _tasks_completed_elsewhere(idx, all_tasks)

    def _dup_tag(text: str) -> str:
        norm = (text or "").lower().strip()[:60]
        return "  ⚠️ may be done elsewhere" if norm in done_elsewhere else ""

    if overdue:
        lines.append(f"\n## Overdue Tasks ({len(overdue)})")
        for t in overdue:
            lines.append(
                f"- [ ] {t['text']} (due {t['due']}) — {t['note_path']}"
                f"{_dup_tag(t['text'])}"
            )
    if due_today:
        lines.append(f"\n## Today's Tasks ({len(due_today)})")
        for t in due_today:
            lines.append(
                f"- [ ] {t['text']} — {t['note_path']}{_dup_tag(t['text'])}"
            )
    if upcoming:
        lines.append(f"\n## Upcoming Tasks ({len(upcoming)})")
        for t in upcoming[:10]:
            lines.append(
                f"- [ ] {t['text']} (due {t['due']}){_dup_tag(t['text'])}"
            )

    # Backlog overview — dateless + far-future tasks grouped by source note.
    # Capped at top 6 sources × 2-line preview so the brief stays scannable.
    # The user can ask `list_unfinished_tasks(note_path=...)` to drill in.
    #
    # Stale-note filter: tasks from notes that haven't been modified in
    # > IRIS_BACKLOG_STALE_DAYS days (default 60) are excluded from the
    # daily brief. Those notes are likely dormant projects, old meeting
    # notes, abandoned to-do lists. Including their open checkboxes in
    # every morning brief turns the section into noise. They still
    # appear in `list_unfinished_tasks` if explicitly queried, and the
    # tail line reports how many tasks were filtered.
    backlog = dateless + far_future
    if backlog:
        from collections import defaultdict
        import os as _os  # noqa: PLC0415
        stale_days = int(_os.environ.get("IRIS_BACKLOG_STALE_DAYS", "60"))
        fresh_paths, stale_paths = _split_backlog_by_freshness(
            idx, [t["note_path"] for t in backlog], stale_days,
        )
        fresh_backlog = [t for t in backlog if t["note_path"] in fresh_paths]
        stale_count = len(backlog) - len(fresh_backlog)

        by_source: dict[str, list[dict]] = defaultdict(list)
        for t in fresh_backlog:
            by_source[t["note_path"]].append(t)
        # Sort sources by open-task count desc, then by path for stability.
        sources_ranked = sorted(
            by_source.items(),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )
        total = len(fresh_backlog)
        if total > 0:
            lines.append(
                f"\n## Open Backlog ({total} task{'s' if total != 1 else ''} "
                f"across {len(by_source)} note{'s' if len(by_source) != 1 else ''})"
            )
            for path, items in sources_ranked[:6]:
                # Strip the `.md` and use the last path segment for compactness.
                title = path.rsplit("/", 1)[-1].removesuffix(".md")
                future_count = sum(1 for t in items if t["due"])
                label = f"{len(items)} open"
                if future_count:
                    label += f" ({future_count} dated)"
                lines.append(f"- **[[{path}|{title}]]** — {label}")
                for t in items[:2]:
                    due_tag = f" *(due {t['due']})*" if t.get("due") else ""
                    preview = t["text"][:80] + ("…" if len(t["text"]) > 80 else "")
                    lines.append(f"  - {preview}{due_tag}")
                if len(items) > 2:
                    lines.append(f"  - _…and {len(items) - 2} more_")
            if len(sources_ranked) > 6:
                tail_total = sum(len(items) for _, items in sources_ranked[6:])
                lines.append(
                    f"- _…and {tail_total} more across "
                    f"{len(sources_ranked) - 6} other notes_"
                )
        if stale_count > 0:
            lines.append(
                f"- _{stale_count} open task{'s' if stale_count != 1 else ''} "
                f"in {len(stale_paths)} stale note{'s' if len(stale_paths) != 1 else ''} "
                f"(not modified in {stale_days}+ days) — "
                f"ask me to triage if you want them back._"
            )

    # 3. Reminders — same per-user scoping as tasks.
    all_reminders = [
        r for r in idx.query_reminders(checked=False, limit=500)
        if path_visible_to_user(r.get("note_path"), vault_subdir)
    ]
    remind_overdue: list[dict] = []
    remind_today: list[dict] = []
    for r in all_reminders:
        r_dt = parse_iso_date(r["remind_on"])
        if r_dt is None:
            continue
        r_date = r_dt.date()
        if r_date < d:
            remind_overdue.append(r)
        elif r_date == d:
            remind_today.append(r)

    if remind_overdue or remind_today:
        lines.append(f"\n## Reminders")
        for r in remind_overdue:
            lines.append(f"- ⚠️ OVERDUE: {r['text']} (was {r['remind_on']})")
        for r in remind_today:
            lines.append(f"- 🔔 {r['text']}")

    # 4. Unfinished items from recent daily notes (only when briefing TODAY)
    if d == today:
        try:
            from .tasks import _collect_unfinished_in_daily_notes
            unfinished = _collect_unfinished_in_daily_notes(days_back=7)
            if unfinished:
                lines.append(f"\n## Unfinished from Recent Days ({len(unfinished)})")
                for item in unfinished[:10]:
                    p = item["parsed"]
                    lines.append(f"- {item['date']} {item['section'][:1]}| {p['text']}")
                if len(unfinished) > 10:
                    lines.append(f"- _…and {len(unfinished) - 10} more_")
                lines.append(
                    "→ Say _\"roll them forward\"_ and I'll move these to today "
                    "with `carry_forward_tasks` (originals stay unchecked and get "
                    "a `rolled:` marker)."
                )
        except ImportError:
            pass

    # 5. Inbox count — per-user inbox lives under the user's 90_Inbox.
    root = get_vault_root()
    inbox_dir = (
        root / vault_subdir / "90_Inbox" / "inbox"
        if vault_subdir else
        root / "90_Inbox" / "inbox"
    )
    inbox_count = len(list(inbox_dir.glob("*.md"))) if inbox_dir.is_dir() else 0
    if inbox_count > 0:
        lines.append(f"\n## Inbox")
        lines.append(f"- {inbox_count} item(s) awaiting triage")

    # 5. Active projects summary — filter by note path prefix.
    c = idx.conn
    if vault_subdir:
        project_rows = c.execute(
            "SELECT path, title FROM notes "
            "WHERE type = 'project' AND status = 'active' "
            "  AND path LIKE ? "
            "ORDER BY path",
            (f"{vault_subdir}/%",),
        ).fetchall()
    else:
        project_rows = c.execute(
            "SELECT path, title FROM notes "
            "WHERE type = 'project' AND status = 'active' "
            "ORDER BY path"
        ).fetchall()
    if project_rows:
        lines.append(f"\n## Active Projects ({len(project_rows)})")
        for pr in project_rows:
            lines.append(f"- {make_wikilink(pr['path'], pr['title'])}")

    structured = "\n".join(lines)
    prose = _llm_prose_summary("morning briefing", structured, max_tokens=180)
    if prose:
        return f"{lines[0]}\n\n_{prose}_\n\n" + "\n".join(lines[1:])
    return structured


@mcp.tool()
def weekly_review(
    date: str = "today",
    user_id: Optional[int] = None,
) -> str:
    """
    Summarize the past 7 days: events attended, tasks completed/added,
    notes created/modified.

    ``date`` accepts natural language or YYYY-MM-DD. The review covers
    the 7 days ending on that date (inclusive).

    ``user_id`` (v24): scopes events + tasks to that user's per-user
    vault subdir. Shared content (events / tasks at vault root, not
    under any ``users/<id>/``) is included for everyone.
    """
    resolved = resolve_natural_date(date)
    if resolved is None:
        return f"Cannot parse date: {date}"
    end_date = datetime.strptime(resolved, "%Y-%m-%d").date()
    start_date = end_date - timedelta(days=6)
    date_from = start_date.isoformat()
    date_to = end_date.isoformat()

    idx = get_vault_index()
    lines: list[str] = [f"# Weekly Review: {date_from} → {date_to}"]

    # v24: resolve user_id → vault_subdir for filtering.
    vault_subdir: Optional[str] = None
    if user_id is not None:
        row = idx.conn.execute(
            "SELECT vault_subdir FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if row is not None and row["vault_subdir"]:
            vault_subdir = str(row["vault_subdir"]).rstrip("/")

    # path_visible_to_user lives in _iris/core.py — shared with
    # morning_briefing and daily_agenda via the wildcard import above.

    # Events — scoped via the central event_visible_to_user helper.
    # Includes attendee-opt-in events (v24 share_with).
    user_discord_id: Optional[str] = None
    if user_id is not None:
        urow = idx.conn.execute(
            "SELECT discord_id FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if urow is not None:
            user_discord_id = urow["discord_id"]
    events = idx.query_events(date_from=date_from, date_to=date_to)
    if vault_subdir is not None:
        events = [
            ev for ev in events
            if event_visible_to_user(ev, vault_subdir, user_discord_id)
        ]
    lines.append(f"\n## Events ({len(events)})")
    if events:
        current_day = ""
        for ev in events:
            if ev["date"] != current_day:
                current_day = ev["date"]
                lines.append(f"### {current_day}")
            t = ev["time"] + (f"–{ev['end_time']}" if ev["end_time"] else "")
            lines.append(f"- {t} {ev['title']}")
    else:
        lines.append("- No events this week.")

    # Completed tasks
    done_tasks = idx.query_tasks(checked=True, limit=500)
    week_done = [
        t for t in done_tasks
        if t.get("done") and date_from <= t["done"] <= date_to
        and path_visible_to_user(t.get("note_path"), vault_subdir)
    ]
    lines.append(f"\n## Completed Tasks ({len(week_done)})")
    if week_done:
        for t in week_done[:30]:
            lines.append(f"- [x] {t['text']} (done {t['done']})")
    else:
        lines.append("- None completed this week.")

    # Open tasks remaining — scoped to user
    open_tasks = [
        t for t in idx.query_tasks(checked=False, limit=500)
        if path_visible_to_user(t.get("note_path"), vault_subdir)
    ]
    overdue = [t for t in open_tasks if t["due"] and t["due"] < date_from]
    lines.append(f"\n## Open Tasks ({len(open_tasks)} total, {len(overdue)} overdue)")

    # Recently modified notes
    c = idx.conn
    # Use mtime_ns from files table to find recently updated notes
    cutoff_ns = int(datetime.combine(start_date, datetime.min.time()).timestamp() * 1e9)
    recent_rows = c.execute(
        "SELECT f.path, n.title FROM files f JOIN notes n ON f.path = n.path "
        "WHERE f.mtime_ns >= ? ORDER BY f.mtime_ns DESC LIMIT 30",
        (cutoff_ns,),
    ).fetchall()
    lines.append(f"\n## Notes Modified ({len(recent_rows)})")
    for r in recent_rows[:20]:
        lines.append(f"- {make_wikilink(r['path'], r['title'])}")

    structured = "\n".join(lines)
    prose = _llm_prose_summary("weekly review", structured, max_tokens=220)
    if prose:
        return f"{lines[0]}\n\n_{prose}_\n\n" + "\n".join(lines[1:])
    return structured



# ─── from original L8298-8404: Session context ───
# =============================================================================
# Session context — lightweight boot info for every conversation
# =============================================================================


@mcp.tool()
def get_session_context() -> str:
    """Return essential context for the current session.

    **Call this at the start of EVERY conversation** before doing anything else.
    It gives you the current date, time, timezone, user basics, and a quick
    snapshot of today's agenda so you never have to guess or infer these.

    Returns a structured block you can reference throughout the conversation.
    """
    import time as _time

    now = datetime.now()
    today = now.date()
    tz_name = _time.tzname[_time.daylight] if _time.daylight else _time.tzname[0]
    try:
        utc_offset_h = -(_time.timezone if _time.daylight == 0 else _time.altzone) / 3600
        utc_str = f"UTC{'+' if utc_offset_h >= 0 else ''}{utc_offset_h:g}"
    except Exception:
        utc_str = ""

    lines = [
        "## Session Context",
        f"date: {today.isoformat()} ({today.strftime('%A')})",
        f"time: {now.strftime('%H:%M')} {tz_name} ({utc_str})",
    ]

    # ── User basics (read from profile note if available) ──
    idx = get_vault_index()
    profile_path = "10_Profile/User Profile.md"
    try:
        root = get_vault_root()
        pf = (root / profile_path).read_text(encoding="utf-8")
        # Extract name from first H1
        m = re.search(r"^#\s+(.+)", pf, re.MULTILINE)
        if m:
            lines.append(f"user: {m.group(1).strip()}")
        # Extract location
        m = re.search(r"\*\*Location\*\*:\s*(.+)", pf)
        if m:
            lines.append(f"location: {m.group(1).strip()}")
        # Extract current role
        m = re.search(r"\*\*Current Role\*\*:\s*(.+)", pf)
        if m:
            lines.append(f"role: {m.group(1).strip()}")
    except Exception:
        pass

    # ── Today's quick snapshot ──
    events = idx.query_events(date_from=today.isoformat(), date_to=today.isoformat())
    all_tasks = idx.query_tasks(checked=False, limit=500)
    overdue_tasks = 0
    today_tasks = 0
    for t in all_tasks:
        due_dt = parse_iso_date(t["due"])
        if due_dt is None:
            continue
        d = due_dt.date()
        if d < today:
            overdue_tasks += 1
        elif d == today:
            today_tasks += 1

    all_reminders = idx.query_reminders(checked=False, limit=500)
    today_reminders = 0
    overdue_reminders = 0
    for r in all_reminders:
        r_dt = parse_iso_date(r["remind_on"])
        if r_dt is None:
            continue
        d = r_dt.date()
        if d < today:
            overdue_reminders += 1
        elif d == today:
            today_reminders += 1

    snap_parts = []
    if events:
        snap_parts.append(f"{len(events)} events")
    if today_tasks:
        snap_parts.append(f"{today_tasks} tasks due")
    if overdue_tasks:
        snap_parts.append(f"{overdue_tasks} overdue tasks")
    if today_reminders:
        snap_parts.append(f"{today_reminders} reminders")
    if overdue_reminders:
        snap_parts.append(f"{overdue_reminders} overdue reminders")
    lines.append(f"today: {', '.join(snap_parts) if snap_parts else 'clear schedule'}")

    # ── Next event (if any today) ──
    future_events = [
        ev for ev in events
        if ev["time"] and ev["time"] > now.strftime("%H:%M")
    ]
    if future_events:
        nxt = future_events[0]
        t = nxt["time"] + (f"–{nxt['end_time']}" if nxt["end_time"] else "")
        lines.append(f"next_event: {t} {nxt['title']}")

    return "\n".join(lines)


