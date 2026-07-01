"""Unified "Today" dashboard — aggregates today's calendar events, due/overdue
note reminders, and habits still to do, all owner-scoped. One read for the
front-page view. (Warranties-expiring lives in iris-mcp's vault DB, not here, so
it's intentionally omitted until there's a bridge.)"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from src.auth_helpers import require_user, owner_filter


def _to_local_naive(dt, is_utc: bool):
    """Normalize a stored CalendarEvent datetime to naive *local* time.

    Events imported via CalDAV/ICS are stored as UTC instants (is_utc=True);
    others are already naive-local. Without this, the Today dashboard showed
    UTC times (e.g. an 18:00 local event as 16:00)."""
    if dt is None:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    if dt.tzinfo is not None:
        return dt.astimezone(local_tz).replace(tzinfo=None)
    if is_utc:
        return dt.replace(tzinfo=timezone.utc).astimezone(local_tz).replace(tzinfo=None)
    return dt


def _allow_null_owner() -> bool:
    try:
        from core.auth import AuthManager
        return not AuthManager().is_configured
    except Exception:
        return False


def _parse_due(value: str):
    s = (value or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Fall back to a plain date (YYYY-MM-DD)
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    # A tz-aware due date (e.g. stored UTC) must be CONVERTED to local, not just
    # stripped — otherwise reminders showed UTC times (e.g. 15:20 instead of 17:20).
    if dt.tzinfo is not None:
        local_tz = datetime.now().astimezone().tzinfo
        return dt.astimezone(local_tz).replace(tzinfo=None)
    return dt


def setup_today_routes() -> APIRouter:
    router = APIRouter(prefix="/api/today", tags=["today"])

    @router.get("")
    def today(request: Request):
        owner = require_user(request)
        allow_null = _allow_null_owner()
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = start + timedelta(days=1)

        from core.database import SessionLocal, CalendarEvent, CalendarCal, Note

        events, reminders = [], []
        db = SessionLocal()
        try:
            # Widen the SQL window by a day on each side so UTC-stored events
            # near the local-day boundary aren't missed; filter precisely in
            # Python against local-time bounds below.
            ev_q = db.query(CalendarEvent).join(CalendarCal).filter(
                CalendarEvent.dtstart < tomorrow + timedelta(days=1),
                CalendarEvent.dtend > start - timedelta(days=1),
                CalendarEvent.status != "cancelled",
            )
            if owner:
                ev_q = owner_filter(ev_q, CalendarCal, owner, include_shared=allow_null)
            raw_events = ev_q.order_by(CalendarEvent.dtstart).all()
            for e in raw_events:
                # Filter to "today" using local-naive bounds (approximate when the
                # server tz != the user's; the day boundary is the only fuzzy case).
                ls = e.dtstart if e.all_day else _to_local_naive(e.dtstart, e.is_utc)
                le = e.dtend if e.all_day else _to_local_naive(e.dtend, e.is_utc)
                if ls is None:
                    continue
                if not (ls < tomorrow and (le or ls) > start):
                    continue
                # Send a raw ISO INSTANT and let the BROWSER format it in the user's
                # timezone — the server may run in UTC, so formatting server-side
                # showed UTC times. UTC-stored events carry an explicit +00:00 offset;
                # naive-local events are sent bare so the browser shows wall-clock.
                if not e.dtstart:
                    iso = ""
                elif e.is_utc and e.dtstart.tzinfo is None:
                    iso = e.dtstart.replace(tzinfo=timezone.utc).isoformat()
                else:
                    iso = e.dtstart.isoformat()
                events.append({
                    "summary": e.summary or "(untitled)",
                    "start": iso,
                    "all_day": bool(e.all_day),
                    "location": e.location or "",
                })

            from sqlalchemy import or_, and_
            # Only pull notes that actually carry a reminder — a note with no
            # due_date AND no reminder_at can never be an active reminder, so
            # excluding them in SQL avoids materializing + date-parsing the
            # user's entire note collection on every dashboard load. Unset
            # fields are stored as NULL or "" (house style), so guard both.
            n_q = db.query(Note).filter(Note.archived == False).filter(  # noqa: E712
                or_(
                    and_(Note.due_date.isnot(None), Note.due_date != ""),
                    and_(Note.reminder_at.isnot(None), Note.reminder_at != ""),
                )
            )
            if owner:
                n_q = owner_filter(n_q, Note, owner, include_shared=allow_null)
            rem_rows = []
            for n in n_q.all():
                # Active reminder if EITHER the "Due by" deadline or the
                # "Remind me" time is today/overdue; surface the earliest.
                cand = [
                    (_parse_due(v), v)
                    for v in (n.due_date, getattr(n, "reminder_at", None))
                ]
                active = [(d, v) for d, v in cand if d is not None and d < tomorrow]
                if not active:
                    continue  # only due-today or overdue (by either field)
                due, raw = min(active, key=lambda x: x[0])
                rem_rows.append((due, {
                    "id": n.id,
                    "title": n.title or "(untitled)",
                    "due": raw,  # RAW ISO — the browser formats it in local tz
                    "overdue": due < start,
                }))
            rem_rows.sort(key=lambda x: x[0])
            reminders = [d for _, d in rem_rows]
        finally:
            db.close()

        habits_due = []
        try:
            from src import health_store
            habits_due = [
                {"id": h["id"], "name": h["name"], "icon": h.get("icon", ""), "streak": h.get("streak", 0)}
                for h in health_store.list_habits(owner or "") if not h["done_today"]
            ]
        except Exception:
            pass

        return {
            "ok": True,
            "date": start.strftime("%A, %B %d"),
            "events": events,
            "reminders": reminders,
            "habits": habits_due,
        }

    @router.post("/carry-forward")
    async def carry_forward(request: Request):
        """Reschedule overdue reminders to today 09:00 (the carry_forward action)."""
        owner = require_user(request)
        from src.builtin_actions import action_carry_forward
        msg, ok = await action_carry_forward(owner or "")
        return {"ok": ok, "message": msg}

    return router
