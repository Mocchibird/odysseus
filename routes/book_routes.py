"""Routes for Iris's vault-backed Books / E-Reader."""

from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.auth_helpers import require_user
from src import book_reader

MAX_BOOK_UPLOAD_BYTES = int(
    os.getenv("ODYSSEUS_BOOK_UPLOAD_MAX_BYTES", str(250 * 1024 * 1024))
)


class BookProgressRequest(BaseModel):
    path: str
    chapter_index: int = 0
    scroll_percent: float = 0
    chapter_title: str = ""
    title: str = ""
    author: str = ""
    kind: str = ""


class BookTitleRequest(BaseModel):
    path: str
    title: str


def setup_book_routes() -> APIRouter:
    router = APIRouter(prefix="/api/books", tags=["books"])

    def _owner(request: Request) -> str:
        return require_user(request) or "local"

    @router.get("")
    async def list_books(request: Request, q: str = "", limit: int = 50):
        return {"ok": True, "books": book_reader.list_books(_owner(request), q, limit)}

    @router.post("/upload")
    async def upload_book(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
        filename = file.filename or "book"
        content = await file.read(MAX_BOOK_UPLOAD_BYTES + 1)
        if len(content) > MAX_BOOK_UPLOAD_BYTES:
            raise HTTPException(413, "Book upload exceeds size limit")
        owner = _owner(request)
        file_row = book_reader.save_uploaded_book(
            owner,
            filename,
            content,
            mime=file.content_type or "",
            index_content=False,
        )
        background_tasks.add_task(book_reader.index_book, owner, file_row["path"])
        return {"ok": True, "file": file_row, "indexing": True}

    @router.get("/open")
    async def open_book(request: Request, path: str):
        return {"ok": True, "book": book_reader.open_book(_owner(request), path)}

    @router.get("/file")
    async def open_book_file(request: Request, path: str):
        file_path = book_reader.pdf_file_path(_owner(request), path)
        safe_name = file_path.name.replace('"', "")
        return FileResponse(
            file_path,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
        )

    @router.get("/chapter")
    async def read_chapter(request: Request, path: str, chapter_index: int = 0):
        return {"ok": True, "chapter": book_reader.read_book_chapter(_owner(request), path, chapter_index)}

    @router.post("/progress")
    async def save_progress(body: BookProgressRequest, request: Request):
        progress = book_reader.save_progress(
            _owner(request),
            body.path,
            chapter_index=body.chapter_index,
            scroll_percent=body.scroll_percent,
            chapter_title=body.chapter_title,
            title=body.title,
            author=body.author,
            kind=body.kind,
        )
        return {"ok": True, "progress": progress}

    @router.post("/title")
    async def save_title(body: BookTitleRequest, request: Request):
        return {"ok": True, "book": book_reader.save_title(_owner(request), body.path, body.title)}

    return router
