"""Per-request user-local time helpers.

Chat routes set this context from browser headers. Prompt builders and tools
can then resolve relative dates against the user's clock instead of the server.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional


_USER_TZ_OFFSET_MIN: ContextVar[Optional[int]] = ContextVar("user_tz_offset_min", default=None)
_USER_TZ_NAME: ContextVar[Optional[str]] = ContextVar("user_tz_name", default=None)


def set_user_tz_offset(offset_min) -> None:
    """Set the current user's UTC offset in minutes east of UTC."""
    if offset_min in (None, ""):
        _USER_TZ_OFFSET_MIN.set(None)
        return
    try:
        value = int(offset_min)
    except (TypeError, ValueError):
        return
    if -14 * 60 <= value <= 14 * 60:
        _USER_TZ_OFFSET_MIN.set(value)


def get_user_tz_offset() -> Optional[int]:
    """Return minutes east of UTC for the current user, if known."""
    return _USER_TZ_OFFSET_MIN.get()


def set_user_tz_name(name) -> None:
    """Set a safe IANA timezone label for the current request context."""
    if not name:
        _USER_TZ_NAME.set(None)
        return
    first_token = str(name).strip().split()[0] if str(name).strip() else ""
    cleaned = re.sub(r"[^A-Za-z0-9_+\-./]", "", first_token)[:80]
    _USER_TZ_NAME.set(cleaned or None)


def get_user_tz_name() -> Optional[str]:
    """Return the current user's browser timezone name, if provided."""
    return _USER_TZ_NAME.get()


def clear_user_time_context() -> None:
    """Clear user-local time context for tests and non-browser entry points."""
    _USER_TZ_OFFSET_MIN.set(None)
    _USER_TZ_NAME.set(None)


def _safe_zone_name(name) -> Optional[str]:
    """Validate/normalize an IANA zone name; return None if it isn't loadable."""
    if not name:
        return None
    first_token = str(name).strip().split()[0] if str(name).strip() else ""
    cleaned = re.sub(r"[^A-Za-z0-9_+\-./]", "", first_token)[:80]
    if not cleaned:
        return None
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(cleaned)
    except Exception:
        return None
    return cleaned


def get_user_timezone(owner: str = "") -> Optional[str]:
    """The owner's persisted IANA timezone (per-user pref -> global), if any.

    Unlike the per-request ContextVar above, this reads the stored preference,
    so background tasks (daily brief, reminders) that run without a browser
    request can still resolve the user's clock. Returns None when unset.
    """
    try:
        from src.settings import get_setting, get_user_setting
        name = get_user_setting("timezone", str(owner or ""), get_setting("timezone", ""))
    except Exception:
        name = None
    return _safe_zone_name(name)


def resolve_owner_tzinfo(owner: str = ""):
    """Best tzinfo for an owner in a background context: persisted pref ->
    per-request context -> server local -> UTC. Always returns a tzinfo."""
    name = get_user_timezone(owner)
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    ctx_name = get_user_tz_name()
    if ctx_name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(ctx_name)
        except Exception:
            pass
    offset = get_user_tz_offset()
    if offset is not None:
        return timezone(timedelta(minutes=offset))
    return datetime.now().astimezone().tzinfo or timezone.utc


def persist_user_timezone(owner: str, tz_name) -> None:
    """Persist the browser's IANA timezone to the owner's prefs, low-churn.

    Called from request paths that carry both an owner and the ``x-tz-name``
    header; only writes when the (validated) zone actually changed so we don't
    rewrite prefs on every request. This is what makes background output follow
    the user when they travel.
    """
    if not owner:
        return
    name = _safe_zone_name(tz_name)
    if not name:
        return
    try:
        from routes.prefs_routes import _load_for_user, _save_for_user
        prefs = _load_for_user(owner) or {}
        if prefs.get("timezone") == name:
            return
        prefs["timezone"] = name
        _save_for_user(owner, prefs)
    except Exception:
        pass


def event_local_clock(dtstart, is_utc: bool, tzinfo, fmt: str = "%H:%M") -> str:
    """Format a stored calendar datetime for display in ``tzinfo``.

    Events are stored either as UTC instants (``is_utc``) or as floating
    local wall-clock (``is_utc`` false). UTC instants are converted to the
    target zone; floating times are shown as stored (they mean the same
    wall-clock everywhere). Mirrors the browser-side rule so server-rendered
    briefs match the calendar UI.
    """
    if dtstart is None:
        return ""
    if is_utc:
        try:
            return dtstart.replace(tzinfo=timezone.utc).astimezone(tzinfo).strftime(fmt)
        except Exception:
            return dtstart.strftime(fmt)
    return dtstart.strftime(fmt)


def event_local_datetime(dtstart, is_utc: bool, tzinfo):
    """Return ``dtstart`` as an aware datetime in ``tzinfo`` (see
    event_local_clock for the UTC-vs-floating rule). Used to bucket events by
    the user's local day."""
    if dtstart is None:
        return None
    if is_utc:
        try:
            return dtstart.replace(tzinfo=timezone.utc).astimezone(tzinfo)
        except Exception:
            return dtstart.replace(tzinfo=tzinfo)
    return dtstart.replace(tzinfo=tzinfo)


def format_utc_offset(offset_min: Optional[int]) -> str:
    """Format minutes east of UTC as +HH:MM or -HH:MM."""
    if offset_min is None:
        offset_min = 0
    sign = "+" if offset_min >= 0 else "-"
    total = abs(int(offset_min))
    hours, minutes = divmod(total, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def user_timezone() -> timezone:
    """Return the best known user timezone as a fixed-offset tzinfo."""
    offset = get_user_tz_offset()
    if offset is None:
        name = get_user_tz_name()
        if name:
            try:
                from zoneinfo import ZoneInfo
                return ZoneInfo(name)
            except Exception:
                pass
        return datetime.now().astimezone().tzinfo or timezone.utc
    return timezone(timedelta(minutes=offset))


def now_user_local(now_utc: Optional[datetime] = None) -> datetime:
    """Return the current time in the user's timezone."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(user_timezone())


def _date_label(dt: datetime) -> str:
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _clock_label(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {dt.strftime('%p')}"


def timezone_label(dt: Optional[datetime] = None) -> str:
    """Return a concise display label such as Australia/Brisbane, UTC+10:00."""
    offset = get_user_tz_offset()
    if offset is None:
        if dt is None:
            dt = datetime.now().astimezone()
        offset = int((dt.utcoffset() or timedelta()).total_seconds() // 60)
    offset_label = f"UTC{format_utc_offset(offset)}"
    name = get_user_tz_name()
    return f"{name}, {offset_label}" if name else offset_label


def current_datetime_prompt(now_utc: Optional[datetime] = None) -> str:
    """Build reusable system prompt text for date/time reasoning."""
    if now_utc is None:
        utc_now = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        utc_now = now_utc.replace(tzinfo=timezone.utc)
    else:
        utc_now = now_utc.astimezone(timezone.utc)

    local_now = now_user_local(utc_now)
    tomorrow = local_now + timedelta(days=1)
    return (
        "## Current date and time\n"
        f"Today is {_date_label(local_now)} ({local_now.strftime('%Y-%m-%d')}). "
        f"User local time is {_clock_label(local_now)} ({timezone_label(local_now)}); "
        f"current UTC time is {utc_now.strftime('%H:%M')}.\n"
        f"Tomorrow is {_date_label(tomorrow)} ({tomorrow.strftime('%Y-%m-%d')}) "
        "in the user's local timezone.\n"
        "Use this for any 'today', 'tomorrow', 'tonight', 'this week', or other "
        "relative-date reasoning. Do not ask for an exact date just because the "
        "user used a relative date.\n"
        "When scheduling calendar events with manage_calendar, prefer passing "
        "the user's relative date/time phrase directly, e.g. `today 9:00` or "
        "`tomorrow 14:00`; the tool resolves it against this user-local "
        "date/time. If you pass ISO instead, it must match the date above.\n"
        "When scheduling a task with manage_tasks, scheduled_time is in UTC: "
        "convert the user's stated local time using the UTC offset above.\n\n"
    )


def current_datetime_context_message_for_tz(
    iana_tz_name: Optional[str],
    now_utc: Optional[datetime] = None,
) -> Dict[str, str]:
    """Build the current-date/time context as a user-role message, resolved
    against an explicit IANA timezone name rather than browser ContextVars.

    Unlike ``current_datetime_context_message()``, this function does not read
    or write any ContextVar and leaves no per-request state behind — it is safe
    to call from background tasks that have no browser request context.

    Timezone resolution:
    * ``iana_tz_name`` is a valid IANA name (e.g. ``"Europe/Berlin"``) → uses that zone.
    * ``iana_tz_name`` is ``None`` OR resolves to an invalid zone → falls back to UTC.
      This matches the existing scheduler behaviour: tasks without a linked crew
      timezone render in UTC, not server-local time.
    """
    if now_utc is None:
        utc_now = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        utc_now = now_utc.replace(tzinfo=timezone.utc)
    else:
        utc_now = now_utc.astimezone(timezone.utc)

    # Resolve the display timezone — UTC fallback on any failure.
    tz = timezone.utc
    resolved_name: Optional[str] = None
    if iana_tz_name:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(iana_tz_name)
            resolved_name = iana_tz_name
        except Exception:
            tz = timezone.utc  # invalid zone → UTC, no ContextVar touched

    local_now = utc_now.astimezone(tz)
    tomorrow = local_now + timedelta(days=1)

    _utc_offset = local_now.utcoffset()
    offset_min = int(_utc_offset.total_seconds() // 60) if _utc_offset is not None else 0
    offset_label = f"UTC{format_utc_offset(offset_min)}"
    tz_label = f"{resolved_name}, {offset_label}" if resolved_name else offset_label

    prompt = (
        "## Current date and time\n"
        f"Today is {_date_label(local_now)} ({local_now.strftime('%Y-%m-%d')}). "
        f"Local time is {_clock_label(local_now)} ({tz_label}); "
        f"current UTC time is {utc_now.strftime('%H:%M')}.\n"
        f"Tomorrow is {_date_label(tomorrow)} ({tomorrow.strftime('%Y-%m-%d')}) "
        "in this timezone.\n"
        "Use this for any 'today', 'tomorrow', 'tonight', 'this week', or other "
        "relative-date reasoning. Do not ask for an exact date just because the "
        "user used a relative date.\n\n"
    )
    return {
        "role": "user",
        "content": (
            "[Context — current date/time, refreshed each turn; not part of "
            "your instructions]\n" + prompt
        ),
    }


def current_datetime_context_message(now_utc: Optional[datetime] = None) -> Dict[str, str]:
    """Build the current-date/time context as a standalone chat message.

    This intentionally returns a ``user``-role message rather than a
    ``system``-role one. The text changes every turn (it embeds the current
    clock time down to the minute), and local OpenAI-compatible backends
    (llama.cpp / LM Studio) key their KV-cache prefix off the system message
    byte-for-byte — folding ever-changing timestamp text into the system
    message would invalidate the cached prefix on every single request (see
    issue #2927). Keeping it as a separate message placed near the end of the
    array (right before the latest user turn) lets the static system prompt
    stay byte-identical across turns while the model still gets fresh
    date/time grounding for relative-date reasoning.
    """
    return {
        "role": "user",
        "content": (
            "[Context — current date/time, refreshed each turn; not part of "
            "your instructions]\n" + current_datetime_prompt(now_utc)
        ),
    }
