"""Routes for Iris's vault-backed Books / E-Reader."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
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


class BookFavoriteRequest(BaseModel):
    path: str
    favorite: bool = True


class BookAnnotationRequest(BaseModel):
    path: str
    type: str = "bookmark"  # "bookmark" | "highlight"
    chapter_index: int = 0
    chapter_title: str = ""
    text: str = ""
    note: str = ""
    color: str = ""
    scroll_percent: float = 0


class BookExplainRequest(BaseModel):
    path: str = ""
    text: str
    title: str = ""


def setup_book_routes() -> APIRouter:
    router = APIRouter(prefix="/api/books", tags=["books"])

    def _owner(request: Request) -> str:
        return require_user(request) or "local"

    @router.get("")
    async def list_books(request: Request, q: str = "", limit: int = 50):
        # Off-thread: list_books hits SQLite + builds excerpts; blocking DB I/O
        # in an async handler stalls the single-worker event loop.
        books = await asyncio.to_thread(book_reader.list_books, _owner(request), q, limit)
        return {"ok": True, "books": books}

    @router.post("/upload")
    async def upload_book(request: Request, file: UploadFile = File(...)):
        filename = file.filename or "book"
        content = await file.read(MAX_BOOK_UPLOAD_BYTES + 1)
        if len(content) > MAX_BOOK_UPLOAD_BYTES:
            raise HTTPException(413, "Book upload exceeds size limit")
        owner = _owner(request)
        # A book is just a PDF/EPUB in the Knowledge base — ingest it there
        # (text-extract + index) off-thread so a large book doesn't block the loop.
        file_row = await asyncio.to_thread(
            book_reader.save_uploaded_book, owner, filename, content, mime=file.content_type or "",
        )
        return {"ok": True, "file": file_row, "indexing": False}

    @router.get("/open")
    async def open_book(request: Request, path: str):
        # Off-thread: open_book parses the whole PDF/EPUB (pypdf / zip + XML),
        # which would otherwise block the event loop for a large book.
        book = await asyncio.to_thread(book_reader.open_book, _owner(request), path)
        return {"ok": True, "book": book}

    @router.get("/file")
    async def open_book_file(request: Request, path: str):
        file_path = await asyncio.to_thread(book_reader.pdf_file_path, _owner(request), path)
        safe_name = file_path.name.replace('"', "")
        return FileResponse(
            file_path,
            media_type="application/pdf",
            # identity = opt out of GZipMiddleware: PDF streams are mostly
            # pre-compressed, and the continuous-scroll reader fetches the
            # whole file — gzipping it is pure CPU burn on the server.
            headers={
                "Content-Disposition": f'inline; filename="{safe_name}"',
                "Content-Encoding": "identity",
            },
        )

    @router.get("/chapter")
    async def read_chapter(request: Request, path: str, chapter_index: int = 0):
        # Off-thread: reads/parses a PDF page or EPUB chapter (pypdf / zip).
        chapter = await asyncio.to_thread(book_reader.read_book_chapter, _owner(request), path, chapter_index)
        return {"ok": True, "chapter": chapter}

    @router.post("/progress")
    async def save_progress(body: BookProgressRequest, request: Request):
        progress = await asyncio.to_thread(
            book_reader.save_progress,
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
        book = await asyncio.to_thread(book_reader.save_title, _owner(request), body.path, body.title)
        return {"ok": True, "book": book}

    @router.post("/favorite")
    async def save_favorite(body: BookFavoriteRequest, request: Request):
        book = await asyncio.to_thread(book_reader.set_favorite, _owner(request), body.path, body.favorite)
        return {"ok": True, "book": book}

    @router.delete("")
    async def delete_book(request: Request, path: str):
        """Delete a book (file bytes, reading progress, annotations).

        `path` is the book's kb_id — the same identifier every other book
        route uses (query-param idiom matches delete_annotation below).
        """
        removed = await asyncio.to_thread(book_reader.delete_book, _owner(request), path)
        if not removed:
            raise HTTPException(404, "Book not found")
        return {"ok": True}

    @router.get("/search")
    async def search_book(request: Request, path: str, q: str = "", limit: int = 120):
        # Off-thread: scanning a large book (zip reads + text extraction) would
        # otherwise block the event loop. Matches upload_book's to_thread pattern.
        result = await asyncio.to_thread(
            book_reader.search_book_text, _owner(request), path, q,
            max_results=max(1, min(int(limit or 120), 400)),
        )
        return {"ok": True, **result}

    @router.get("/cover")
    async def book_cover(request: Request, path: str):
        cover = await asyncio.to_thread(book_reader.get_cover, _owner(request), path)
        if not cover:
            raise HTTPException(404, "No cover available")
        data, content_type = cover
        # A cover comes from an untrusted uploaded EPUB. An SVG served inline as
        # image/svg+xml can carry <script> that runs on the app origin (stored
        # XSS). Force a non-executable image type + attachment disposition +
        # a strict CSP so a hostile cover can't script the page.
        if "svg" in (content_type or "").lower():
            content_type = "application/octet-stream"
        # identity = skip GZipMiddleware for already-compressed cover images.
        return Response(content=data, media_type=content_type, headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Encoding": "identity",
            "Content-Disposition": "inline",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        })

    @router.get("/annotations")
    async def list_annotations(request: Request, path: str):
        data = await asyncio.to_thread(book_reader.list_annotations, _owner(request), path)
        return {"ok": True, **data}

    @router.post("/annotations")
    async def add_annotation(body: BookAnnotationRequest, request: Request):
        item = await asyncio.to_thread(
            book_reader.add_annotation,
            _owner(request), body.path,
            type=body.type, chapter_index=body.chapter_index, chapter_title=body.chapter_title,
            text=body.text, note=body.note, color=body.color, scroll_percent=body.scroll_percent,
        )
        return {"ok": True, "annotation": item}

    @router.delete("/annotations")
    async def delete_annotation(request: Request, path: str, id: str):
        removed = await asyncio.to_thread(book_reader.delete_annotation, _owner(request), path, id)
        if not removed:
            raise HTTPException(404, "Annotation not found")
        return {"ok": True}

    @router.post("/explain")
    async def explain_passage(body: BookExplainRequest, request: Request):
        return {"ok": True, **(await book_reader.explain_passage(_owner(request), body.text, title=body.title))}

    return router
