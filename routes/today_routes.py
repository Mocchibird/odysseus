"""Unified "Today" dashboard — aggregates today's calendar events, due/overdue
note reminders, and habits still to do, all owner-scoped. One read for the
front-page view. (Warranties-expiring lives in iris-mcp's vault DB, not here, so
it's intentionally omitted until there's a bridge.)"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Request

from src.auth_helpers import require_user, owner_filter


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
            ev_q = db.query(CalendarEvent).join(CalendarCal).filter(
                CalendarEvent.dtstart < tomorrow,
                CalendarEvent.dtend > start,
                CalendarEvent.status != "cancelled",
            )
            if owner:
                ev_q = owner_filter(ev_q, CalendarCal, owner, include_shared=allow_null)
            for e in ev_q.order_by(CalendarEvent.dtstart).all():
                events.append({
                    "summary": e.summary or "(untitled)",
                    "time": "all day" if e.all_day else (e.dtstart.strftime("%H:%M") if e.dtstart else ""),
                    "all_day": bool(e.all_day),
                    "location": e.location or "",
                })

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
