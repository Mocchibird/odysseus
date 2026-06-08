"""Routes for Iris's Obsidian-backed persistent user vault."""

from __future__ import annotations

import os

import mimetypes

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.auth_helpers import require_user
from src import epub_reader, iris_vault

MAX_IRIS_VAULT_UPLOAD_BYTES = int(
    os.getenv("ODYSSEUS_IRIS_VAULT_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024))
)


class VaultWriteRequest(BaseModel):
    path: str
    content: str = ""


class VaultSearchRequest(BaseModel):
    query: str = ""
    limit: int = 20


class VaultDeleteRequest(BaseModel):
    path: str


class VaultSortInboxRequest(BaseModel):
    limit: int = 200


class EpubProgressRequest(BaseModel):
    path: str
    chapter_index: int = 0
    scroll_percent: float = 0
    chapter_title: str = ""
    title: str = ""
    author: str = ""


def setup_iris_vault_routes() -> APIRouter:
    router = APIRouter(prefix="/api/iris-vault", tags=["iris-vault"])

    def _owner(request: Request) -> str:
        return require_user(request) or "local"

    def _maybe_sort_inbox(owner: str) -> None:
        enabled = os.getenv("ODYSSEUS_IRIS_AUTO_SORT_INBOX", "1").strip().lower()
        if enabled in {"0", "false", "no", "off"}:
            return
        try:
            iris_vault.sort_inbox(owner, limit=200)
        except Exception:
            pass

    @router.get("/status")
    async def status(request: Request):
        owner = _owner(request)
        root = iris_vault.vault_root()
        user_root = iris_vault.owner_root(owner)
        return {
            "ok": True,
            "vault_root": str(root),
            "owner": iris_vault.owner_folder_name(owner),
            "owner_path": str(user_root),
        }

    @router.post("/search")
    async def search(body: VaultSearchRequest, request: Request):
        owner = _owner(request)
        _maybe_sort_inbox(owner)
        return {
            "ok": True,
            "files": iris_vault.search(owner, body.query, body.limit),
        }

    @router.get("/files")
    async def list_files(request: Request, q: str = "", limit: int = 100):
        owner = _owner(request)
        _maybe_sort_inbox(owner)
        # Empty query = browse: list the FULL filesystem tree (every folder/file),
        # not the lazy, 100-capped search index. Non-empty query = search the index.
        if (q or "").strip():
            return {"ok": True, "files": iris_vault.search(owner, q, limit)}
        return {"ok": True, "files": iris_vault.list_files_fs(owner)}

    @router.get("/file")
    async def read_file(request: Request, path: str):
        return {"ok": True, "file": iris_vault.read_file(_owner(request), path)}

    @router.get("/raw")
    async def raw_file(request: Request, path: str):
        """Serve raw file bytes (images, etc.) so the vault reader can show them
        inline. Owner-scoped + path-confined via resolve_owner_file."""
        owner = _owner(request)
        file_path = iris_vault.resolve_owner_file(owner, path)
        if not file_path.is_file():
            raise HTTPException(404, "Vault file not found")
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        return FileResponse(file_path, media_type=mime)

    @router.post("/file")
    async def write_file(body: VaultWriteRequest, request: Request):
        row = iris_vault.write_text_file(_owner(request), body.path, body.content)
        return {"ok": True, "file": iris_vault.row_to_dict(row)}

    @router.post("/upload")
    async def upload_file(
        request: Request,
        file: UploadFile = File(...),
        path: str = "",
        context: str = Form(""),
        source: str = Form("vault"),
    ):
        content = await file.read(MAX_IRIS_VAULT_UPLOAD_BYTES + 1)
        if len(content) > MAX_IRIS_VAULT_UPLOAD_BYTES:
            raise HTTPException(413, "Vault upload exceeds size limit")
        row = iris_vault.save_uploaded_file(
            _owner(request),
            file.filename or "upload",
            content,
            rel_path=path,
            mime=file.content_type or "",
            context=context,
            source=source,
        )
        return {"ok": True, "file": iris_vault.row_to_dict(row)}

    @router.post("/epub/upload")
    async def upload_epub(request: Request, file: UploadFile = File(...)):
        filename = file.filename or "book.epub"
        if not filename.lower().endswith(".epub"):
            raise HTTPException(400, "Upload must be an .epub file")
        content = await file.read(MAX_IRIS_VAULT_UPLOAD_BYTES + 1)
        if len(content) > MAX_IRIS_VAULT_UPLOAD_BYTES:
            raise HTTPException(413, "EPUB upload exceeds size limit")
        owner = _owner(request)
        row = iris_vault.save_uploaded_file(
            owner,
            filename,
            content,
            mime=file.content_type or "application/epub+zip",
        )
        book = epub_reader.parse_epub(owner, row.rel_path)
        return {"ok": True, "file": iris_vault.row_to_dict(row), "book": book}

    @router.get("/epub")
    async def read_epub(request: Request, path: str):
        return {"ok": True, "book": epub_reader.parse_epub(_owner(request), path)}

    @router.post("/epub/progress")
    async def save_epub_progress(body: EpubProgressRequest, request: Request):
        progress = epub_reader.save_progress(
            _owner(request),
            body.path,
            chapter_index=body.chapter_index,
            scroll_percent=body.scroll_percent,
            chapter_title=body.chapter_title,
            title=body.title,
            author=body.author,
        )
        return {"ok": True, "progress": progress}

    @router.delete("/file")
    async def delete_file(body: VaultDeleteRequest, request: Request):
        deleted = iris_vault.delete_file(_owner(request), body.path)
        return {"ok": True, "deleted": deleted}

    @router.get("/graph")
    async def link_graph(request: Request):
        return {"ok": True, "graph": iris_vault.build_link_graph(_owner(request))}

    @router.post("/daily-note")
    async def daily_note(body: VaultWriteRequest, request: Request):
        # Reuses VaultWriteRequest; `content` carries the quick-capture text.
        return iris_vault.append_daily_note(_owner(request), body.content or body.path)

    @router.post("/reindex")
    async def reindex(request: Request):
        owner = _owner(request)
        _maybe_sort_inbox(owner)
        count = iris_vault.reindex_owner(owner)
        return {"ok": True, "indexed": count}

    @router.post("/sort-inbox")
    async def sort_inbox(body: VaultSortInboxRequest, request: Request):
        moved = iris_vault.sort_inbox(_owner(request), limit=body.limit)
        return {"ok": True, "moved": moved, "count": len(moved)}

    return router
