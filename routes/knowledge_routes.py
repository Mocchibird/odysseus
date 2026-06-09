"""
knowledge_routes.py — REST API for the native knowledge base (src/knowledge_base.py).

This is the user's TRUST ANCHOR: search/list/get here are DETERMINISTIC keyword +
tag matching (no LLM, no embeddings), and every result carries `upload_id` so the
UI can open the ACTUAL stored file via /api/upload/{id} and `/{id}` returns the
full extracted text to verify against the original. The RAG/semantic path is
Iris-only and lives in the agent tool, never here.
"""
import asyncio
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse

from src.auth_helpers import get_current_user
from src import knowledge_base as kb

logger = logging.getLogger(__name__)


def setup_knowledge_routes(upload_handler) -> APIRouter:
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

    @router.get("")
    async def kb_search(request: Request, q: str = "", tags: str = "", limit: int = 50):
        """Deterministic keyword + tag search. Returns files (newest-first), each
        with `upload_id` so the client can open the real file."""
        owner = get_current_user(request)
        tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
        return {"files": kb.search(owner, q=q, tags=tag_list, limit=limit)}

    @router.get("/tags")
    async def kb_tags(request: Request):
        """All distinct tags (user + AI) for the tag-filter UI."""
        owner = get_current_user(request)
        return {"tags": kb.list_tags(owner)}

    @router.get("/{kb_id}")
    async def kb_get(request: Request, kb_id: str):
        """Full record incl. the complete extracted text (verify against the file)."""
        owner = get_current_user(request)
        rec = kb.get(owner, kb_id)
        if not rec:
            raise HTTPException(404, "Not found")
        return rec

    @router.get("/{kb_id}/raw")
    async def kb_raw(request: Request, kb_id: str):
        """Serve the ACTUAL stored file — the user's open-and-verify path."""
        owner = get_current_user(request)
        path = kb.file_abspath(owner, kb_id)
        if not path:
            raise HTTPException(404, "File not found")
        rec = kb.get(owner, kb_id) or {}
        return FileResponse(
            path,
            media_type=rec.get("mime") or None,
            filename=rec.get("filename") or os.path.basename(path),
        )

    @router.post("")
    async def kb_add(request: Request, background_tasks: BackgroundTasks):
        """Ingest an already-uploaded file into the knowledge base. Flow: upload the
        bytes via /api/upload (any type), then POST its id here to extract + index +
        tag it. The bytes stay in the uploads store and remain openable."""
        owner = get_current_user(request)
        body = await request.json()
        upload_id = str(body.get("upload_id") or "").strip()
        if not upload_id:
            raise HTTPException(400, "upload_id required")
        info = upload_handler.resolve_upload(upload_id, owner=owner)
        if not info or not info.get("path"):
            raise HTTPException(404, "Upload not found")
        # Ingest is SYNC and, for images, runs the remote vision model for OCR
        # (can take minutes). Off-thread it so a single slow upload never blocks
        # the event loop and freezes the whole app (the 504 seen during migration).
        rec = await asyncio.to_thread(
            kb.ingest,
            owner,
            file_path=info["path"],
            filename=info.get("name") or info.get("original_name") or upload_id,
            mime=info.get("mime"),
            upload_id=upload_id,
            source="upload",
            tags=body.get("tags") or "",
        )
        # Auto-tag off the request path so the upload returns immediately and bulk
        # uploads aren't slowed by a per-file LLM call. Skip files deduped into an
        # already-tagged row, or with no extractable text.
        if rec and rec.get("id") and not rec.get("ai_tags") and (rec.get("excerpt") or "").strip():
            background_tasks.add_task(kb.generate_ai_tags, owner, rec["id"])
        return rec

    @router.put("/{kb_id}/tags")
    async def kb_set_tags(request: Request, kb_id: str):
        """Replace a file's user tags."""
        owner = get_current_user(request)
        body = await request.json()
        rec = kb.set_tags(owner, kb_id, body.get("tags") or "")
        if not rec:
            raise HTTPException(404, "Not found")
        return rec

    @router.put("/{kb_id}")
    async def kb_update(request: Request, kb_id: str):
        """Edit a file's content / searchable text (and optionally its name). For
        text files this rewrites the stored bytes; for binaries it corrects only
        the extracted text. Re-indexes RAG, so it's off-threaded (can be slow)."""
        owner = get_current_user(request)
        body = await request.json()
        text = body.get("text")
        filename = body.get("filename")
        if text is None and filename is None:
            raise HTTPException(400, "Nothing to update (send 'text' and/or 'filename')")
        rec = await asyncio.to_thread(kb.update_text, owner, kb_id, text, filename=filename)
        if not rec:
            raise HTTPException(404, "Not found")
        return rec

    @router.post("/{kb_id}/autotag")
    async def kb_autotag(request: Request, kb_id: str):
        """Generate AI topical tags for a file from its extracted text (Utility
        model). Off-threaded — makes an LLM call."""
        owner = get_current_user(request)
        rec = await asyncio.to_thread(kb.generate_ai_tags, owner, kb_id)
        if not rec:
            raise HTTPException(404, "Not found")
        return rec

    @router.delete("/{kb_id}")
    async def kb_delete(request: Request, kb_id: str):
        """Remove a file's knowledge-base record + its KB-owned bytes."""
        owner = get_current_user(request)
        if not kb.delete(owner, kb_id):
            raise HTTPException(404, "Not found")
        return {"ok": True}

    return router
