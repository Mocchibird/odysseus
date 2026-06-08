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
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        # Fall back to a plain date (YYYY-MM-DD)
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None


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
                # All-day events are date-anchored — don't tz-shift them.
                ls = e.dtstart if e.all_day else _to_local_naive(e.dtstart, e.is_utc)
                le = e.dtend if e.all_day else _to_local_naive(e.dtend, e.is_utc)
                if ls is None:
                    continue
                if not (ls < tomorrow and (le or ls) > start):
                    continue
                events.append({
                    "summary": e.summary or "(untitled)",
                    "time": "all day" if e.all_day else ls.strftime("%H:%M"),
                    "all_day": bool(e.all_day),
                    "location": e.location or "",
                })
            events.sort(key=lambda x: (x["all_day"] is False, x["time"]))

            n_q = db.query(Note).filter(Note.archived == False)  # noqa: E712
            if owner:
                n_q = owner_filter(n_q, Note, owner, include_shared=allow_null)
            for n in n_q.all():
                if not n.due_date:
                    continue
                due = _parse_due(n.due_date)
                if due is None or due >= tomorrow:
                    continue  # only due-today or overdue
                reminders.append({
                    "id": n.id,
                    "title": n.title or "(untitled)",
                    "due": due.strftime("%Y-%m-%d %H:%M"),
                    "overdue": due < start,
                })
        finally:
            db.close()
        reminders.sort(key=lambda r: r["due"])

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
