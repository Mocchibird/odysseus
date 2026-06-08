"""Calendar/scheduling

@mcp.tool() definitions live here. The shared FastMCP instance is imported
from the package __init__.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from .. import mcp
from ..core import *  # noqa: F401, F403  — all helpers, VaultIndex accessor,
                       # and the task/event parsing helpers (parse_event_line,
                       # parse_iso_date, parse_schedule_section, …)
# Underscore-prefixed names are excluded by `import *`, so we import them
# explicitly.
from ..core import _notify_index_of_write, _resolve_date_range
from .tasks import _daily_note_path, format_event_bullet, _ensure_daily_note


# ─── from original L7333-7579: Calendar/scheduling ───
# =============================================================================
# Calendar / scheduling tools
# =============================================================================


@mcp.tool()
def schedule_event(
    date: str,
    time: str,
    title: str,
    end_time: str = "",
    end_date: str = "",
    location: str = "",
    description: str = "",
    all_day: bool = False,
    shared: bool = False,
    share_with: str = "",
    user_id: Optional[int] = None,
) -> str:
    """
    Add event to a daily note's Schedule section. Creates note if needed.

    ``date`` accepts natural language: "today", "tomorrow", "next monday",
    "in 3 days", or a literal YYYY-MM-DD.

    ``end_date``: for cross-day events, the date the event ends (YYYY-MM-DD
    or natural language). The ``(+Nd)`` marker is computed automatically.

    ``all_day``: if True, formats as ``- all-day Title`` with no time.

    **Visibility (v24):**

    - **Default** (``shared=False``, no explicit ``user_id``, empty
      ``share_with``): event lands in the SPEAKER's per-user daily note.
      Only that user's morning brief / daily_agenda / weekly_review
      shows it. Use this for personal appointments.
    - **share_with** (comma-separated display names / discord IDs):
      event STILL lives in the speaker's folder (clearly their event),
      but the listed users will also see it in their briefs. Use this
      for "I'm busy Thursday 14:00 — let mom know so she doesn't plan
      something for me" type sharing. Example:
      ``share_with="Jihyun"`` or ``share_with="mom, dad"``.
      Unknown names are reported back in the ``ok`` line.
    - **Shared** (``shared=True``): event lands at the VAULT ROOT
      (``30_Episodic/<year>/<date>.md``). Visible in every user's
      brief / agenda. Use for genuinely group events: family dinner,
      holidays, school events involving multiple users.
    - **Owner cross-write** (``user_id=N`` with owner as speaker):
      writes to user N's daily note. Auth-gated; only the owner can
      cross-write to another user's calendar.

    Returns ``ok <vault-relative path> [visibility] ...`` — the
    visibility tag tells you which routing rule landed.
    """
    resolved = resolve_natural_date(date)
    if resolved is None:
        return f"Cannot parse date: {date}. Use 'today', 'tomorrow', 'next monday', or YYYY-MM-DD."
    date = resolved
    if not all_day:
        if not re.match(r"^\d{1,2}:\d{2}$", time.strip()):
            return f"time must be HH:MM, got: {time}"
        if end_time.strip() and not re.match(r"^\d{1,2}:\d{2}$", end_time.strip()):
            return f"end_time must be HH:MM, got: {end_time}"
    if not title.strip():
        return "title must not be empty."

    # Resolve end_date for cross-day events
    plus_days = 0
    resolved_end_date = ""
    if end_date.strip():
        resolved_end_date = resolve_natural_date(end_date)
        if resolved_end_date is None:
            return f"Cannot parse end_date: {end_date}"
        try:
            d_start = datetime.strptime(date, "%Y-%m-%d").date()
            d_end = datetime.strptime(resolved_end_date, "%Y-%m-%d").date()
            plus_days = (d_end - d_start).days
            if plus_days < 0:
                return f"end_date ({resolved_end_date}) is before start date ({date})."
        except ValueError:
            pass

    # v24: pick the target note based on shared / user_id args. Default
    # = speaker's user subdir. shared=True wins (event becomes visible
    # to everyone via the v24 visibility filter).
    note = _resolve_event_target_note(
        date=date, shared=shared, user_id=user_id,
    )
    if isinstance(note, str):
        # Helper returned an err string instead of a Path → bail.
        return note
    text = read_text(note) if note.exists() else ""
    bullet = format_event_bullet(
        time=time.strip() if not all_day else "",
        title=title.strip(),
        end_time=end_time.strip() if not all_day else "",
        location=location.strip(),
        description=description.strip(),
        all_day=all_day,
        plus_days=plus_days,
    )

    # Insert event into ## Schedule in sorted order by time
    text = _insert_schedule_bullet(text, bullet, time.strip() if not all_day else "")

    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text, encoding="utf-8")
    _notify_index_of_write(note, text=text)
    rel = relative_to_vault(note)

    # v24: if share_with was provided, resolve display names / discord IDs
    # to a comma-separated list of discord_ids and write to the events
    # row's ``attendees`` column. The visibility filter in
    # ``morning_briefing`` / ``daily_agenda`` / ``weekly_review`` then
    # surfaces this event in each named user's brief.
    resolved_ids: list[str] = []
    unresolved: list[str] = []
    if share_with.strip():
        resolved_ids, unresolved = _resolve_share_with(share_with)
        if resolved_ids:
            try:
                idx = get_vault_index()
                attendees_csv = ",".join(resolved_ids)
                idx.conn.execute(
                    "UPDATE events SET attendees = ? "
                    "WHERE note_path = ? AND date = ? AND time = ? AND title = ?",
                    (attendees_csv, rel, date, time.strip() if not all_day else "",
                     title.strip()),
                )
                idx.conn.commit()
            except Exception:
                pass  # best-effort; the event still landed correctly

    if shared:
        visibility = "shared"
    elif resolved_ids:
        visibility = f"private+shared_with={len(resolved_ids)}"
    else:
        visibility = "private"
    result = f"ok {rel} [{visibility}] {'all-day' if all_day else time} {title}"
    if resolved_end_date and resolved_end_date != date:
        result += f" (→{resolved_end_date})"
    if unresolved:
        result += f" (couldn't resolve: {', '.join(unresolved)})"
    return result


def _resolve_share_with(share_with: str) -> tuple[list[str], list[str]]:
    """Resolve a comma-separated list of names / discord IDs to a list
    of stable discord_id strings.

    Each input is matched against (in priority):
      1. ``users.discord_id`` exact
      2. ``users.display_name`` exact (case-insensitive)
      3. ``users.discord_username`` exact (case-insensitive)

    Returns ``(resolved_discord_ids, unresolved_inputs)`` — the unresolved
    list goes back to Iris so she can flag misspellings or remind the
    speaker which family members are registered.
    """
    inputs = [s.strip() for s in share_with.split(",") if s.strip()]
    if not inputs:
        return [], []
    resolved: list[str] = []
    unresolved: list[str] = []
    try:
        idx = get_vault_index()
    except Exception:
        return [], inputs
    for raw in inputs:
        row = idx.conn.execute(
            "SELECT discord_id FROM users "
            "WHERE discord_id = ? "
            "   OR lower(display_name) = lower(?) "
            "   OR lower(discord_username) = lower(?)",
            (raw, raw, raw),
        ).fetchone()
        if row is not None:
            did = row["discord_id"]
            if did not in resolved:
                resolved.append(did)
        else:
            unresolved.append(raw)
    return resolved, unresolved


def _resolve_event_target_note(
    date: str,
    shared: bool,
    user_id: Optional[int],
):
    """Pick the daily-note Path where a schedule_event write should land.

    Returns a ``Path`` on success, or an ``err: ...`` string on
    authorisation failure (caller returns it directly to Iris).

    Routing:
      - ``shared=True`` → vault root: ``30_Episodic/<year>/<date>.md``.
        Everyone's brief sees the event.
      - Otherwise → target user's folder:
        ``users/<discord_id>/30_Episodic/<year>/<date>.md``. The target
        user defaults to the current speaker; an explicit ``user_id``
        is honored only when the speaker is the owner (cross-write).
    """
    from datetime import date as _date  # noqa: PLC0415
    year = date[:4]
    if shared:
        # Vault root → shared visibility. Use the existing helper to
        # create the note from template if missing.
        return _ensure_daily_note(date)
    # Private path: speaker's user folder. Authorize first.
    target_uid, denial = authorize_user_access(user_id)
    if denial:
        return denial
    idx = get_vault_index()
    if target_uid is None:
        # No multi-user context (Claude Desktop / CLI / tests).
        # Fall back to legacy vault-root behaviour.
        return _ensure_daily_note(date)
    row = idx.conn.execute(
        "SELECT vault_subdir FROM users WHERE id = ?", (target_uid,),
    ).fetchone()
    if row is None or not row["vault_subdir"]:
        return _ensure_daily_note(date)
    vault_subdir = str(row["vault_subdir"]).rstrip("/")
    from pathlib import Path  # noqa: PLC0415
    rel = f"{vault_subdir}/30_Episodic/{year}/{date}.md"
    full = safe_path(rel)
    if not full.exists():
        # Create from the same template _ensure_daily_note uses, just
        # at the per-user path.
        full.parent.mkdir(parents=True, exist_ok=True)
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            day_name = dt.strftime("%A")
        except ValueError:
            day_name = ""
        header = (
            f"---\ndate: {date}\ntags:\n  - daily\ntype: daily\n---\n"
            f"# {date} — {day_name}\n\n"
            "## Schedule\n\n## Tasks\n\n## Reminders\n\n## Notes\n"
        )
        full.write_text(header, encoding="utf-8")
    return full


def _insert_schedule_bullet(text: str, bullet: str, time_str: str) -> str:
    """Insert a bullet into the ## Schedule section in sorted order by time."""
    bounds = find_section_bounds(text, "Schedule")
    if bounds is None:
        return text.rstrip() + "\n\n## Schedule\n\n" + bullet + "\n"

    start, end = bounds
    section = text[start:end]
    lines = section.splitlines(keepends=True)
    insert_idx = len(lines)  # default: append at end
    if time_str:
        time_val = time_str.zfill(5)
        for i, line in enumerate(lines):
            ev = parse_event_line(line)
            if ev and ev.get("time", "").zfill(5) > time_val:
                insert_idx = i
                break
    else:
        # All-day events go at the top (after the heading line)
        for i, line in enumerate(lines):
            if parse_event_line(line):
                insert_idx = i
                break
    lines.insert(insert_idx, bullet + "\n")
    return text[:start] + "".join(lines) + text[end:]


@mcp.tool()
def remove_event(date: str, match: str) -> str:
    """Remove a matching event from a daily note."""
    if parse_iso_date(date) is None:
        return f"date must be YYYY-MM-DD, got: {date}"
    rel = _daily_note_path(date)
    note = safe_path(rel)
    if not note.exists():
        return f"No daily note for {date}."

    text = read_text(note)
    bounds = find_section_bounds(text, "Schedule")
    if bounds is None:
        return f"No ## Schedule section in {rel}."

    start, end = bounds
    section = text[start:end]
    needle = match.strip().lower()
    lines = section.splitlines(keepends=True)
    matches = [(i, line) for i, line in enumerate(lines) if parse_event_line(line) and needle in line.lower()]

    if not matches:
        return f"No event matching '{match}' found in {date}."
    if len(matches) > 1:
        preview = "; ".join(l.strip() for _, l in matches[:5])
        return f"{len(matches)} events match '{match}'. Be more specific: {preview}"

    idx, _ = matches[0]
    del lines[idx]
    text = text[:start] + "".join(lines) + text[end:]
    note.write_text(text, encoding="utf-8")
    _notify_index_of_write(note, text=text)
    return f"ok {date}"


@mcp.tool()
def daily_agenda(
    date: str = "today",
    days: int = 1,
    user_id: Optional[int] = None,
) -> str:
    """
    Show agenda: schedule, tasks, reminders for a date or range.

    ``date`` accepts:
      - Single dates: "today", "tomorrow", "next monday", "in 3 days", YYYY-MM-DD
      - Ranges: "this week", "next week", "this month", "next 3 days",
        "next 2 weeks", "7 days"

    When a range expression is used, ``days`` is auto-calculated.
    Otherwise ``days`` controls how many days to show (default 1).

    ``user_id`` (v24): scopes events + tasks to that user's per-user
    vault subdir, plus shared content at vault root. Without it,
    returns everything (unchanged legacy behaviour).
    """
    # Try range expression first  (e.g. "this week", "next 3 days")
    range_result = _resolve_date_range(date)
    if range_result is not None:
        resolved, days = range_result
    else:
        resolved = resolve_natural_date(date)
        if resolved is None:
            return (
                f"Cannot parse date: {date!r}. "
                "Use 'today', 'tomorrow', 'this week', 'next 3 days', "
                "'next monday', or YYYY-MM-DD."
            )

    start_date = datetime.strptime(resolved, "%Y-%m-%d").date()
    today = datetime.now().date()

    end_date = start_date + timedelta(days=max(1, days) - 1)
    date_from = start_date.isoformat()
    date_to = end_date.isoformat()

    idx = get_vault_index()

    # v24: resolve user_id → vault_subdir for filtering events + tasks.
    # "Visible to this user" = under their folder OR shared (no users/ prefix).
    vault_subdir: Optional[str] = None
    if user_id is not None:
        row = idx.conn.execute(
            "SELECT vault_subdir FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if row is not None and row["vault_subdir"]:
            vault_subdir = str(row["vault_subdir"]).rstrip("/")

    # Path-visibility predicate is in _iris/core.py:path_visible_to_user
    # (imported via the wildcard above). Events use the dedicated
    # event_visible_to_user variant which also consults the v24
    # ``attendees`` column for share_with opt-in.

    # Events — scoped via central helper (own folder + shared root +
    # attendee opt-in via the v24 ``attendees`` column).
    user_discord_id: Optional[str] = None
    if user_id is not None:
        urow = idx.conn.execute(
            "SELECT discord_id FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if urow is not None:
            user_discord_id = urow["discord_id"]
    all_events = idx.query_events(date_from=date_from, date_to=date_to)
    if vault_subdir is not None:
        events = [
            ev for ev in all_events
            if event_visible_to_user(ev, vault_subdir, user_discord_id)
        ]
    else:
        events = all_events

    # Tasks due in this window — same scoping
    all_tasks = [
        t for t in idx.query_tasks(checked=False, limit=500)
        if path_visible_to_user(t.get("note_path"), vault_subdir)
    ]
    tasks_in_range = []
    tasks_overdue = []
    for t in all_tasks:
        due_dt = parse_iso_date(t["due"])
        if due_dt is None:
            continue  # skip no-date tasks for agenda
        due_date = due_dt.date()
        if due_date < start_date:
            tasks_overdue.append(t)
        elif start_date <= due_date <= end_date:
            tasks_in_range.append(t)

    # Reminders due in this window
    all_reminders = idx.query_reminders(checked=False, limit=500)
    reminders_in_range = []
    reminders_overdue = []
    for r in all_reminders:
        r_dt = parse_iso_date(r["remind_on"])
        if r_dt is None:
            continue
        r_date = r_dt.date()
        if r_date < start_date:
            reminders_overdue.append(r)
        elif start_date <= r_date <= end_date:
            reminders_in_range.append(r)

    # Format output
    if days == 1:
        header = f"📅 Agenda for {date_from}"
        if start_date == today:
            header += " (today)"
        elif start_date == today + timedelta(days=1):
            header += " (tomorrow)"
    else:
        header = f"📅 Agenda: {date_from} → {date_to}"

    lines = [header]
    if events:
        lines.append(f"[events:{len(events)}]")
        for ev in events:
            if ev.get("all_day"):
                t = "all-day"
            else:
                t = ev["time"] + (f"-{ev['end_time']}" if ev["end_time"] else "")
            label = f"{ev['date']}|{t}|{ev['title']}"
            end_d = ev.get("end_date", "")
            if end_d and end_d != ev["date"]:
                label += f" (→{end_d})"
            lines.append(label)
    if tasks_overdue:
        lines.append(f"[overdue-tasks:{len(tasks_overdue)}]")
        lines.extend(f"{t['text']}|{t['due']}" for t in tasks_overdue)
    if tasks_in_range:
        lines.append(f"[tasks:{len(tasks_in_range)}]")
        lines.extend(f"{t['text']}|{t['due']}" for t in tasks_in_range)
    if reminders_overdue:
        lines.append(f"[overdue-reminders:{len(reminders_overdue)}]")
        lines.extend(f"{r['text']}|{r['remind_on']}" for r in reminders_overdue)
    if reminders_in_range:
        lines.append(f"[reminders:{len(reminders_in_range)}]")
        lines.extend(f"{r['text']}|{r['remind_on']}" for r in reminders_in_range)
    if not events and not tasks_in_range and not tasks_overdue and not reminders_in_range and not reminders_overdue:
        lines.append("clear")
    return "\n".join(lines)



# ─── from original L8405-8533: vault_cron delegation ───
# =============================================================================
# vault_cron.py delegation (evening wrapup, weekly summary, morning routine)
# =============================================================================

import os
import subprocess
import sys
from pathlib import Path

_VAULT_CRON = str(Path(__file__).resolve().parent.parent.parent / "vault_cron.py")


def _run_vault_cron(*args: str, timeout: int = 30) -> str:
    """Run a vault_cron.py subcommand and return its stdout."""
    cmd = [sys.executable, _VAULT_CRON, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "VAULT_ROOT": str(get_vault_root())})
        output = r.stdout.strip()
        if r.returncode != 0:
            err = r.stderr.strip() or "unknown error"
            return f"vault_cron error: {err}\n{output}"
        return output or "Done (no output)."
    except subprocess.TimeoutExpired:
        return f"vault_cron timed out after {timeout}s"
    except Exception as e:
        return f"vault_cron failed: {e}"


@mcp.tool()
def evening_wrapup(
    date: str = "today",
    user_id: Optional[int] = None,
) -> str:
    """Generate an end-of-day summary and append to the daily note.

    Summarizes: calendar events attended, tasks completed, reminders done,
    and notes modified today.  Appends a ### Daily Summary block to ## Notes.

    Args:
        date: Target date — "today" or YYYY-MM-DD.
        user_id: Owner-only at this time. Non-owner per-user wrap-up
            requires ``vault_cron.evening_wrapup`` to grow user-scoping
            (deferred). Refusing the call beats leaking owner content
            into another user's channel.
    """
    resolved = resolve_natural_date(date)
    if resolved is None:
        return f"Cannot parse date: {date}"
    # Strict refusal for non-owner targets — vault_cron's evening_wrapup
    # isn't user-scoped yet, so we cannot guarantee non-owner content.
    if user_id is not None:
        from ..core import resolve_user_id  # noqa: PLC0415
        idx = get_vault_index()
        target_uid = resolve_user_id(user_id)
        owner = idx.get_owner_user()
        if owner is None or target_uid != int(owner["id"]):
            return (
                "err: per-user evening wrap-up not yet supported "
                "(would leak owner data). Owner-only for now."
            )
    return _run_vault_cron("wrapup", "--date", resolved)


@mcp.tool()
def weekly_summary(date: str = "today", force: bool = False, dry_run: bool = False) -> str:
    """Generate and save a weekly summary note for the ISO week containing the given date.

    Writes to 30_Episodic/{iso_year}/Weekly/{iso_year}-W{NN}.md.
    Summarizes: tasks completed, reminders done, calendar events,
    notes touched, and open tasks carried over.

    Skips if the file already exists unless ``force=True``.

    Args:
        date: Any day in the target ISO week — "today" or YYYY-MM-DD.
        force: Overwrite the file if it already exists.
        dry_run: Build the summary but do not write.
    """
    resolved = resolve_natural_date(date)
    if resolved is None:
        return f"Cannot parse date: {date}"
    args = ["weekly-summary", "--end-date", resolved]
    if force:
        args.append("--force")
    if dry_run:
        args.append("--dry-run")
    return _run_vault_cron(*args, timeout=60)


@mcp.tool()
def morning_routine(dry_run: bool = False) -> str:
    """Run the morning routine: daily note + drop-zone import.

      1. Creates today's daily note
      2. Imports any files dropped into 90_Inbox/inbox/
    """
    args = ["morning"]
    if dry_run:
        args.append("--dry-run")
    return _run_vault_cron(*args, timeout=60)




# ── iCal subscription puller ───────────────────────────────────────────────
# Replacement for the deleted Apple-Calendar AppleScript path. Pulls from any
# public iCal feed (iCloud shared calendar, Google's "secret iCal URL",
# Outlook public link, etc.) without needing OS-level integration.


def _resolve_home_tz_for_ical():
    """Best-effort home timezone for iCal datetime normalisation.

    Read from IRIS_TIMEZONE / TZ env (same precedence as the bot). Returns
    None on any failure so the caller can fall back to leaving the raw
    iCal datetime untouched (rather than crashing the import)."""
    import os as _os
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    name = (_os.environ.get("IRIS_TIMEZONE")
            or _os.environ.get("TZ")
            or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        return None


@mcp.tool()
def pull_ical_subscription(
    url: str,
    days_ahead: int = 30,
    days_back: int = 0,
    dry_run: bool = False,
    source_tag: str = "ical",
    link_to_person: str = "",
    cross_calendar_dedupe: bool = True,
    shared: bool = False,
) -> str:
    """Sync events from a ``webcal://`` or ``https://`` iCalendar feed.

    Works with any source that exposes a public iCal feed:
      * **iCloud shared calendars** — Calendar.app → right-click calendar →
        Share Calendar → Public Calendar. Copy the ``webcal://`` URL.
      * **Google Calendar** — Settings → Settings for my calendars → pick
        one → "Secret address in iCal format" (long random URL).
      * **Outlook / Microsoft 365** — Calendar settings → Shared calendars
        → Publish → choose "ICS — anyone with the link".

    Each event is written into the appropriate daily note's ``## Schedule``
    section via ``schedule_event``. Recurring events (RRULE) are expanded
    over the import window — you get one row per occurrence in the date
    range.

    **Two layers of dedupe so re-syncing is always safe:**

    1. **By iCal UID** (always on). Each event embeds ``[ical-uid:<id>]`` in
       its description; before importing, the vault is scanned for an
       existing event with the same UID. Catches re-imports of the SAME
       calendar.
    2. **By (date, time, title)** — cross-calendar dedupe. When you sync
       multiple feeds that share events (e.g. a meeting in BOTH your work
       and personal calendar with different UIDs), this skips an incoming
       event if there's already one at the same date + start time + title
       in the vault. Toggle off with ``cross_calendar_dedupe=False`` if you
       have legitimately-distinct events at identical slots.

    **Person-linked calendars** (``link_to_person="10_Profile/People/Foo"``):
    set this when syncing a calendar that belongs to a specific contact
    (e.g. a partner's shared iCloud). Each imported event gets a
    ``with: [[<path>]]`` line in its description, so Iris's wikilink graph
    automatically backlinks the event to their profile. You can then ask
    Iris "what's coming up with Foo?" and she pulls from the index.

    Args:
        url: Calendar feed URL. ``webcal://`` is auto-rewritten to ``https://``.
        days_ahead: Days into the future to import (default 30, max 365).
        days_back: Days into the past (default 0, max 365). Useful for
            backfilling.
        dry_run: List what WOULD be imported without writing.
        source_tag: Marker for filtering later, e.g. ``icloud-personal``,
            ``google-work``. Stored in the event description.
        link_to_person: Vault-relative path to a person note (with or
            without ``.md``). When set, each imported event's description
            includes ``with: [[<path>]]`` linking back to the person.
        cross_calendar_dedupe: If True (default), skip events whose
            (date, start_time, normalised title) match an existing event
            from a different source — even if their UIDs differ.

    Returns a summary like ``📅 12 added, 3 dup'd by UID, 2 dup'd by content``.
    """
    try:
        import httpx
        import recurring_ical_events
        from icalendar import Calendar
    except ImportError as exc:
        return (f"err: missing dep — {exc}. Add `icalendar` + "
                "`recurring-ical-events` to pyproject.toml and rebuild.")

    url = (url or "").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith(("http://", "https://")):
        return f"err: URL must be http(s):// or webcal://, got {url!r}"

    days_ahead = max(0, min(int(days_ahead), 365))
    days_back = max(0, min(int(days_back), 365))

    # Fetch
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"err: could not fetch feed: {exc}"

    # Parse
    try:
        cal = Calendar.from_ical(resp.content)
    except Exception as exc:  # icalendar raises generic exceptions
        return f"err: invalid iCalendar feed: {exc}"

    today = datetime.now().date()
    start_dt = datetime.combine(today - timedelta(days=days_back), datetime.min.time())
    end_dt = datetime.combine(today + timedelta(days=days_ahead), datetime.max.time())

    # Expand recurring events into individual occurrences in the window.
    try:
        events_iter = recurring_ical_events.of(cal).between(start_dt, end_dt)
    except Exception as exc:
        return f"err: failed to expand recurring events: {exc}"

    # Pre-load BOTH dedupe sets for O(1) lookups during the import loop:
    #   1. Existing UIDs → catches re-syncs of the same calendar.
    #   2. (date, time, normalised_title) triplets → catches the same
    #      meeting appearing in multiple calendars with different UIDs.
    from ..core import get_vault_index
    idx = get_vault_index()
    existing_uids: set[str] = set()
    existing_content_keys: set[tuple[str, str, str]] = set()

    def _content_key(date_iso: str, time_str: str, title: str) -> tuple[str, str, str]:
        """Normalised dedupe key. Lowercased + whitespace-collapsed title
        so 'Daily Standup ' and 'daily standup' match."""
        norm_title = re.sub(r"\s+", " ", (title or "").strip().lower())
        return (date_iso, time_str or "", norm_title)

    try:
        rows = idx.conn.execute(
            "SELECT date, time, title, description FROM events"
        ).fetchall()
        for r in rows:
            desc = (r["description"] if hasattr(r, "keys") else r[3]) or ""
            for m in re.finditer(r"\[ical-uid:([^\]]+)\]", desc):
                existing_uids.add(m.group(1))
            r_date = r["date"] if hasattr(r, "keys") else r[0]
            r_time = r["time"] if hasattr(r, "keys") else r[1]
            r_title = r["title"] if hasattr(r, "keys") else r[2]
            if r_date and r_title:
                existing_content_keys.add(_content_key(r_date, r_time, r_title))
    except Exception:
        pass  # if events table doesn't exist yet, just skip dedupe

    # Normalise the person-link target. Strip trailing .md so the resulting
    # wikilink is `[[10_Profile/People/Foo]]`, not `[[...Foo.md]]`.
    person_link = ""
    if link_to_person:
        p = link_to_person.strip().lstrip("/")
        if p.endswith(".md"):
            p = p[:-3]
        person_link = f"with: [[{p}]]"

    added = 0
    skipped_uid = 0
    skipped_content = 0
    errors: list[str] = []
    previews: list[str] = []

    for ev in events_iter:
        try:
            dtstart_field = ev.get("DTSTART")
            if dtstart_field is None:
                continue
            dtstart = dtstart_field.dt
            dtend_field = ev.get("DTEND")
            dtend = dtend_field.dt if dtend_field is not None else None

            summary = str(ev.get("SUMMARY") or "(no title)").strip()
            location = str(ev.get("LOCATION") or "").strip()
            ical_desc = str(ev.get("DESCRIPTION") or "").strip()
            uid = str(ev.get("UID") or "").strip()
            # Some feeds (or hand-rolled .ics files) ship events with empty
            # UIDs. Without a UID, the second sync can't dedupe by ID and
            # would re-add the event. Synthesise a stable hash UID from the
            # event's identifying fields so cross-sync dedupe still works.
            if not uid:
                import hashlib as _hashlib
                key = f"{dtstart}|{summary}|{location}".encode("utf-8")
                uid = "synth-" + _hashlib.sha1(key).hexdigest()[:16]

            # Distinguish all-day (datetime.date) vs timed (datetime.datetime)
            is_timed = hasattr(dtstart, "hour")
            if is_timed:
                # Convert TZ-aware datetimes to the user's home TZ before
                # extracting HH:MM. Without this, a Google Calendar event
                # published in UTC for "14:00 UTC" lands in the vault as
                # "14:00" and the event ping fires at 14:00 LOCAL — which
                # could be 16:00 UTC, i.e. 2 hours after the actual event.
                _home_tz = _resolve_home_tz_for_ical()
                if dtstart.tzinfo is not None and _home_tz is not None:
                    dtstart = dtstart.astimezone(_home_tz)
                date_iso = dtstart.date().isoformat()
                time_str = dtstart.strftime("%H:%M")
                if dtend and hasattr(dtend, "hour"):
                    if dtend.tzinfo is not None and _home_tz is not None:
                        dtend = dtend.astimezone(_home_tz)
                    end_time_str = dtend.strftime("%H:%M")
                    end_date_str = (dtend.date().isoformat()
                                    if dtend.date() != dtstart.date() else "")
                else:
                    end_time_str = ""
                    end_date_str = ""
                all_day = False
            else:
                # iCal all-day events: DTEND is exclusive (the day AFTER).
                date_iso = dtstart.isoformat()
                if dtend:
                    last_day = dtend - timedelta(days=1)
                    end_date_str = (last_day.isoformat()
                                    if last_day != dtstart else "")
                else:
                    end_date_str = ""
                time_str = ""
                end_time_str = ""
                all_day = True

            if uid and uid in existing_uids:
                skipped_uid += 1
                continue
            ck = _content_key(date_iso, time_str, summary)
            if cross_calendar_dedupe and ck in existing_content_keys:
                skipped_content += 1
                continue

            marker = f"[ical-uid:{uid}][source:{source_tag}]" if uid else f"[source:{source_tag}]"
            desc_parts: list[str] = []
            if ical_desc:
                desc_parts.append(ical_desc)
            if person_link:
                desc_parts.append(person_link)
            desc_parts.append(marker)
            full_desc = "\n\n".join(desc_parts)

            if dry_run:
                preview_line = f"  {date_iso} {time_str or 'all-day'} — {summary}"
                if location:
                    preview_line += f" @ {location}"
                if person_link:
                    preview_line += f"  ← {person_link}"
                previews.append(preview_line)
                added += 1
                existing_content_keys.add(ck)  # avoid re-counting dups within preview
                if uid:
                    existing_uids.add(uid)
                continue

            result = schedule_event(
                date=date_iso,
                time=time_str,
                title=summary,
                end_time=end_time_str,
                end_date=end_date_str,
                location=location,
                description=full_desc,
                all_day=all_day,
                shared=shared,
            )
            if result.startswith("ok") or result.startswith("✅"):
                added += 1
                if uid:
                    existing_uids.add(uid)
                existing_content_keys.add(ck)
            else:
                errors.append(f"{date_iso} {summary[:40]}: {result[:120]}")
        except Exception as exc:
            errors.append(f"parse failed: {exc}")

    summary_lines: list[str] = []
    verb = "would add" if dry_run else "added"
    skip_parts: list[str] = []
    if skipped_uid:
        skip_parts.append(f"{skipped_uid} dup'd by UID")
    if skipped_content:
        skip_parts.append(f"{skipped_content} dup'd by content")
    skip_summary = (", " + ", ".join(skip_parts)) if skip_parts else ""
    person_note = f" → linked to [[{link_to_person}]]" if link_to_person else ""
    summary_lines.append(
        f"📅 iCal sync ({source_tag}){person_note}: {added} {verb}{skip_summary}"
    )
    if dry_run and previews:
        summary_lines.append(f"Preview (first {min(15, len(previews))}):")
        summary_lines.extend(previews[:15])
        if len(previews) > 15:
            summary_lines.append(f"  ... +{len(previews) - 15} more")
    if errors:
        summary_lines.append(f"⚠️ {len(errors)} error(s):")
        for e in errors[:5]:
            summary_lines.append(f"  - {e}")
        if len(errors) > 5:
            summary_lines.append(f"  - ... +{len(errors) - 5} more")
    return "\n".join(summary_lines)


@mcp.tool()
def sync_all_calendars(
    days_ahead: int = 30,
    days_back: int = 0,
    dry_run: bool = False,
) -> str:
    """Sync all of the speaker's configured calendar sources.

    These are the iCloud / Google (ICS) and CalDAV calendars added in the
    Iris web app's Calendar settings; each is pulled into the user's own
    vault folder. (Calendars are managed in the web UI now — to pull a
    one-off feed without saving it, call ``pull_ical_subscription(url=...)``
    directly.)

    Args:
        days_ahead: Days into the future to import.
        days_back: Days into the past.
        dry_run: Accepted for backwards-compat; per-source sync always writes.

    Returns a short summary of how many sources synced.
    """
    from ..core import resolve_user_id  # noqa: PLC0415
    uid = resolve_user_id(None)
    if not uid:
        return "err: no speaker context — can't tell whose calendars to sync."
    try:
        from iris_web import calendars as _webcal  # noqa: PLC0415
    except Exception as e:  # pragma: no cover - import guard
        return f"err: calendar source store unavailable: {e}"
    out = _webcal.sync_all_for_user(
        uid, days_ahead=days_ahead, days_back=days_back,
    )
    synced, errors = out["synced"], out["errors"]
    if synced == 0 and errors == 0:
        return ("No calendar sources configured. Add your calendars in the "
                "Iris web app (Calendar → add an iCloud / Google / CalDAV source).")
    msg = f"📅 Synced {synced} calendar source{'s' if synced != 1 else ''}"
    if errors:
        msg += f" · {errors} failed"
    return msg
