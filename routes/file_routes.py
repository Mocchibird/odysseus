"""
file_routes.py — REST API for the native Files store (src/file_store.py).

Deterministic keyword + tag search/list/get (no LLM, no embeddings); every
result can open the ACTUAL stored file via /api/files/{id}/raw and /{id}
returns the full extracted text to verify against. The RAG/semantic path is
Iris-only (the agent tool), never here.
"""
import asyncio
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, Response

from src.auth_helpers import require_user
from src import file_store as fs
from src import usercontent

logger = logging.getLogger(__name__)


def _with_standalone_url(rec: dict) -> dict:
    """Attach the content-origin standalone-page URL to an HTML file record when
    the feature is configured (no-op / absent key otherwise). Stateless — just a
    signed URL, so it's free to compute per-response (see src/usercontent.py)."""
    if isinstance(rec, dict):
        url = usercontent.standalone_url(rec)
        if url:
            rec["standalone_url"] = url
    return rec


def setup_file_routes(upload_handler) -> APIRouter:
    router = APIRouter(prefix="/api/files", tags=["files"])

    @router.get("")
    async def files_search(request: Request, q: str = "", tags: str = "", limit: int = 50):
        owner = require_user(request) or None
        tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
        files = await asyncio.to_thread(fs.search, owner, q=q, tags=tag_list, limit=limit)
        return {"files": [_with_standalone_url(f) for f in files]}

    @router.get("/tags")
    async def files_tags(request: Request):
        owner = require_user(request) or None
        return {"tags": await asyncio.to_thread(fs.list_tags, owner)}

    @router.get("/{file_id}")
    async def files_get(request: Request, file_id: str):
        owner = require_user(request) or None
        rec = await asyncio.to_thread(fs.get, owner, file_id)
        if not rec:
            raise HTTPException(404, "Not found")
        return _with_standalone_url(rec)

    @router.get("/{file_id}/raw")
    async def files_raw(request: Request, file_id: str):
        """Serve the ACTUAL stored file — the open-and-verify path."""
        owner = require_user(request) or None
        path = await asyncio.to_thread(fs.file_abspath, owner, file_id)
        if not path:
            raise HTTPException(404, "File not found")
        rec = await asyncio.to_thread(fs.get, owner, file_id) or {}
        return FileResponse(
            path,
            media_type=rec.get("mime") or None,
            filename=rec.get("filename") or os.path.basename(path),
        )

    @router.get("/{file_id}/view")
    async def files_view(request: Request, file_id: str):
        """Render a stored HTML file inline as a live page (opens in a new tab).

        /raw serves with Content-Disposition: attachment (downloads); this
        serves inline as text/html so the browser renders it — the way to view
        an .html file on a device (e.g. iOS) that won't open a local file.

        SECURITY: the file is untrusted user content that would run on the app
        origin. SecurityHeadersMiddleware tags this exact path with a
        `Content-Security-Policy: sandbox` (NO allow-same-origin), so the
        rendered document gets an OPAQUE origin — its scripts run for a faithful
        view but it cannot read the app's cookies/localStorage or call
        same-origin APIs with the user's session. Owner-scoped like /raw.
        """
        owner = require_user(request) or None
        rec = await asyncio.to_thread(fs.get, owner, file_id)
        if not rec:
            raise HTTPException(404, "Not found")
        fname = (rec.get("filename") or "").lower()
        mime = (rec.get("mime") or "").lower()
        if not (fname.endswith(".html") or fname.endswith(".htm") or "html" in mime):
            raise HTTPException(400, "Not an HTML file")
        path = await asyncio.to_thread(fs.file_abspath, owner, file_id)
        if not path:
            raise HTTPException(404, "File not found")
        data = await asyncio.to_thread(lambda p: open(p, "rb").read(), path)
        return Response(
            content=data,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": "inline", "Content-Encoding": "identity"},
        )

    @router.post("")
    async def files_add(request: Request, background_tasks: BackgroundTasks):
        """Ingest an already-uploaded file into the Files store. Upload the bytes
        via /api/upload, then POST its id here to extract + index + tag it."""
        owner = require_user(request) or None
        body = await request.json()
        upload_id = str(body.get("upload_id") or "").strip()
        if not upload_id:
            raise HTTPException(400, "upload_id required")
        info = upload_handler.resolve_upload(upload_id, owner=owner)
        if not info or not info.get("path"):
            raise HTTPException(404, "Upload not found")
        # Off-thread: extraction can run a remote vision model (image OCR), which
        # would otherwise block the event loop.
        rec = await asyncio.to_thread(
            fs.ingest,
            owner,
            file_path=info["path"],
            filename=info.get("name") or info.get("original_name") or upload_id,
            mime=info.get("mime"),
            upload_id=upload_id,
            source="upload",
            tags=body.get("tags") or "",
        )
        if rec and rec.get("id") and not rec.get("ai_tags") and (rec.get("excerpt") or "").strip():
            background_tasks.add_task(fs.generate_ai_tags, owner, rec["id"])
        return rec

    @router.put("/{file_id}/tags")
    async def files_set_tags(request: Request, file_id: str):
        owner = require_user(request) or None
        body = await request.json()
        rec = fs.set_tags(owner, file_id, body.get("tags") or "")
        if not rec:
            raise HTTPException(404, "Not found")
        return rec

    @router.put("/{file_id}")
    async def files_update(request: Request, file_id: str):
        """Edit a file's content / searchable text (and optionally its name).
        Re-indexes RAG, so it's off-threaded."""
        owner = require_user(request) or None
        body = await request.json()
        text = body.get("text")
        filename = body.get("filename")
        if text is None and filename is None:
            raise HTTPException(400, "Nothing to update (send 'text' and/or 'filename')")
        rec = await asyncio.to_thread(fs.update_text, owner, file_id, text, filename=filename)
        if not rec:
            raise HTTPException(404, "Not found")
        return rec

    @router.post("/{file_id}/autotag")
    async def files_autotag(request: Request, file_id: str):
        owner = require_user(request) or None
        rec = await asyncio.to_thread(fs.generate_ai_tags, owner, file_id)
        if not rec:
            raise HTTPException(404, "Not found")
        return rec

    @router.delete("/{file_id}")
    async def files_delete(request: Request, file_id: str):
        owner = require_user(request) or None
        if not fs.delete(owner, file_id):
            raise HTTPException(404, "Not found")
        return {"ok": True}

    return router
