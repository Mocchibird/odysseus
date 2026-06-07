"""REST API for the Pings & Reminders feed (src/pings_store.py).

Branching is handled client-side (the panel creates a chat session via
/api/session and PUTs the id back here), so there's no server branch endpoint —
just read/mark/keep/delete + an unread count for the rail badge.
"""
from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import require_user
from src import pings_store as ps


def setup_ping_routes():
    router = APIRouter(prefix="/api/pings", tags=["pings"])

    def _owner(request: Request) -> str:
        return require_user(request)

    @router.get("")
    def list_pings(request: Request, include_read: bool = True, limit: int = 200):
        return {"pings": ps.list_pings(_owner(request), include_read=include_read, limit=limit)}

    @router.get("/unread-count")
    def unread_count(request: Request):
        return {"count": ps.unread_count(_owner(request))}

    @router.post("/read-all")
    def read_all(request: Request):
        return {"ok": True, "updated": ps.mark_all_read(_owner(request))}

    @router.put("/{ping_id}")
    async def update_ping(ping_id: str, request: Request):
        owner = _owner(request)
        body = await request.json()
        updated = None
        if "read" in body:
            updated = ps.mark_read(owner, ping_id, body["read"])
        if "keep" in body:
            updated = ps.set_keep(owner, ping_id, body["keep"])
        if "session_id" in body:
            updated = ps.link_session(owner, ping_id, body["session_id"])
        if updated is None:
            raise HTTPException(404, "Ping not found")
        return {"ok": True, "ping": updated}

    @router.delete("/{ping_id}")
    def delete_ping(ping_id: str, request: Request):
        if not ps.delete(_owner(request), ping_id):
            raise HTTPException(404, "Ping not found")
        return {"ok": True}

    return router
