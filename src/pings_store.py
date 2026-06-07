"""Owner-scoped store for the Pings & Reminders feed.

Producers (dispatch_reminder, do_send_ping, the scheduler's task-completion hook)
call `create()` in-process; the REST routes (routes/ping_routes.py) and the
`tidy_pings` housekeeping action read/mutate. One durable home for everything the
assistant surfaces, so reminders, ntfy pings, briefs, and task results all land
in the same feed instead of separate ephemeral notification queues.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _owner(owner: Optional[str]) -> str:
    return owner or ""


def _to_dict(p) -> Dict[str, Any]:
    return {
        "id": p.id,
        "kind": p.kind or "ping",
        "source": p.source or "",
        "title": p.title or "",
        "body": p.body or "",
        "source_ref": p.source_ref or "",
        "status": p.status or "",
        "read": bool(p.read),
        "keep": bool(p.keep),
        "session_id": p.session_id or "",
        "created_at": (p.created_at.isoformat() + "Z") if getattr(p, "created_at", None) else "",
    }


def create(
    owner: Optional[str],
    title: str,
    body: str = "",
    *,
    kind: str = "ping",
    source: str = "",
    source_ref: str = "",
    status: str = "",
) -> Optional[Dict[str, Any]]:
    """Append a feed entry. Best-effort: never raises into a producer's path."""
    try:
        from core.database import SessionLocal, Ping
        db = SessionLocal()
        try:
            p = Ping(
                id=uuid.uuid4().hex,
                owner=_owner(owner),
                kind=kind or "ping",
                source=source or "",
                title=(title or "").strip()[:300] or (kind or "Ping"),
                body=(body or "").strip()[:4000],
                source_ref=source_ref or "",
                status=status or "",
            )
            db.add(p)
            db.commit()
            db.refresh(p)
            return _to_dict(p)
        finally:
            db.close()
    except Exception as e:  # pragma: no cover - defensive; producers must not break
        logger.debug(f"pings_store.create failed: {e}")
        return None


def list_pings(owner: Optional[str], *, include_read: bool = True, limit: int = 200) -> List[Dict[str, Any]]:
    from core.database import SessionLocal, Ping
    db = SessionLocal()
    try:
        q = db.query(Ping).filter(Ping.owner == _owner(owner))
        if not include_read:
            q = q.filter(Ping.read == False)  # noqa: E712
        rows = q.order_by(Ping.created_at.desc()).limit(max(1, min(limit, 500))).all()
        return [_to_dict(p) for p in rows]
    finally:
        db.close()


def unread_count(owner: Optional[str]) -> int:
    from core.database import SessionLocal, Ping
    db = SessionLocal()
    try:
        return db.query(Ping).filter(
            Ping.owner == _owner(owner), Ping.read == False  # noqa: E712
        ).count()
    finally:
        db.close()


def _update(owner: Optional[str], ping_id: str, **fields) -> Optional[Dict[str, Any]]:
    from core.database import SessionLocal, Ping
    db = SessionLocal()
    try:
        p = db.query(Ping).filter(Ping.owner == _owner(owner), Ping.id == ping_id).first()
        if not p:
            return None
        for k, v in fields.items():
            setattr(p, k, v)
        db.commit()
        db.refresh(p)
        return _to_dict(p)
    finally:
        db.close()


def mark_read(owner, ping_id, read=True):
    return _update(owner, ping_id, read=bool(read))


def set_keep(owner, ping_id, keep=True):
    return _update(owner, ping_id, keep=bool(keep))


def link_session(owner, ping_id, session_id):
    return _update(owner, ping_id, session_id=session_id or None)


def mark_all_read(owner: Optional[str]) -> int:
    from core.database import SessionLocal, Ping
    db = SessionLocal()
    try:
        n = db.query(Ping).filter(
            Ping.owner == _owner(owner), Ping.read == False  # noqa: E712
        ).update({Ping.read: True})
        db.commit()
        return n
    finally:
        db.close()


def delete(owner: Optional[str], ping_id: str) -> bool:
    from core.database import SessionLocal, Ping
    db = SessionLocal()
    try:
        p = db.query(Ping).filter(Ping.owner == _owner(owner), Ping.id == ping_id).first()
        if not p:
            return False
        db.delete(p)
        db.commit()
        return True
    finally:
        db.close()


def expire_old(days: int = 30) -> int:
    """Delete read, non-kept pings older than `days`. Returns the count removed.
    Used by the `tidy_pings` housekeeping action."""
    from core.database import SessionLocal, Ping
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=max(1, days))
    db = SessionLocal()
    try:
        n = db.query(Ping).filter(
            Ping.keep == False,  # noqa: E712
            Ping.created_at < cutoff,
        ).delete(synchronize_session=False)
        db.commit()
        return n or 0
    finally:
        db.close()
