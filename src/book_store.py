"""Books / E-Reader = the native PDF/EPUB store.

A book owns its bytes in BOOKS_DIR and its extracted text (RAG-indexed under
kind="book", so Iris can search book contents via the unified search). This
module is the file substrate (ingest / extract / search / open / rename /
favorite / delete) PLUS the Books-specific reading state — progress +
bookmarks/highlights — both keyed by the Book id. Reuses the shared extraction
(src.content_extract) and RAG indexing (src.content_rag) so books are indexed
exactly like every other content store.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from src import content_extract, content_rag

logger = logging.getLogger(__name__)

SUPPORTED_BOOK_EXTENSIONS = {".epub", ".pdf"}
RAG_KIND = "book"


# --------------------------------------------------------------------------- #
# Byte store (BOOKS_DIR, owner-sharded by id)                                 #
# --------------------------------------------------------------------------- #

def _books_dir() -> str:
    from src.constants import BOOKS_DIR
    return BOOKS_DIR


def _owner_slug(owner) -> str:
    raw = str(owner or "_anon")
    return "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in raw) or "_anon"


def _copy_into_books(owner, book_id: str, src_path: str, filename: str) -> Optional[str]:
    """Copy the source file into BOOKS_DIR; return the path relative to it
    (stored on the row, resolved by resolve_book_file)."""
    try:
        ext = os.path.splitext(filename or src_path)[1].lower()
        rel = os.path.join(_owner_slug(owner), book_id[:2], f"{book_id}{ext}")
        dest = os.path.join(_books_dir(), rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src_path, dest)
        return rel
    except Exception as e:
        logger.warning("books: could not copy %s into store: %s", filename, e)
        return None


# --------------------------------------------------------------------------- #
# Identity: a book is a pdf/epub                                              #
# --------------------------------------------------------------------------- #

def kind_of(filename: str) -> Optional[str]:
    return {".pdf": "pdf", ".epub": "epub"}.get(os.path.splitext(filename or "")[1].lower())


def is_book(filename: str) -> bool:
    return kind_of(filename) is not None


def _book_dict(row) -> dict:
    """Map a Book row to a book dict. The Books API identifier is the Book id,
    carried as both `id` and `path` (the frontend passes a book's `path` to
    /api/books/* — that value is the Book id)."""
    bid = row.id
    fname = row.filename or "Book"
    return {
        "id": bid,
        "path": bid,
        "kb_id": bid,
        "filename": fname,
        "title": os.path.splitext(fname)[0] or "Book",
        "kind": kind_of(fname),
        "mime": row.mime,
        "size": row.file_size,
        "url": f"/api/books/file?path={bid}",
        "excerpt": (row.text or "")[:300],
        "tags": content_extract.split_tags(row.tags),
        "favorite": bool(row.favorite),
    }


def get_book(owner: Optional[str], kb_id: str) -> Optional[dict]:
    """The book row as a book dict, or None if missing / not this owner's."""
    from core.database import SessionLocal, Book
    db = SessionLocal()
    try:
        row = db.query(Book).filter(Book.id == kb_id).first()
        if not row or (owner is not None and row.owner != owner):
            return None
        return _book_dict(row)
    finally:
        db.close()


def resolve_book_file(owner: Optional[str], kb_id: str) -> Path:
    """Absolute path to the book's bytes (owner-scoped). 404 if missing/forbidden."""
    from core.database import SessionLocal, Book
    db = SessionLocal()
    try:
        row = db.query(Book).filter(Book.id == kb_id).first()
        if not row or (owner is not None and row.owner != owner) or not row.path:
            raise HTTPException(404, "Book file not found")
        p = os.path.join(_books_dir(), row.path)
    finally:
        db.close()
    if not os.path.exists(p):
        raise HTTPException(404, "Book file not found")
    return Path(p)


def list_books(owner: Optional[str], query: str = "", limit: int = 50) -> list[dict]:
    from core.database import SessionLocal, Book
    cap = max(1, int(limit or 50))
    db = SessionLocal()
    try:
        q = db.query(Book)
        if owner is not None:
            q = q.filter(Book.owner == owner)
        query = (query or "").strip()
        if query:
            like = f"%{query}%"
            q = q.filter(
                Book.filename.ilike(like) | Book.text.ilike(like)
                | Book.tags.ilike(like) | Book.ai_tags.ilike(like)
            )
        rows = q.order_by(Book.created_at.desc()).limit(500).all()
        books = []
        for row in rows:
            b = _book_dict(row)
            b["progress"] = get_progress(owner, b["id"], missing_ok=True)
            books.append(b)
    finally:
        db.close()
    # Favourites first, then newest-first. Stable sort keeps the within-group
    # order; cap AFTER sorting so a starred book never drops off.
    books.sort(key=lambda b: 0 if b.get("favorite") else 1)
    return books[:cap]


def set_favorite(owner: Optional[str], kb_id: str, favorite: bool) -> dict:
    """Star/unstar a book (owner-scoped). 404 if not this owner's book."""
    from core.database import SessionLocal, Book
    db = SessionLocal()
    try:
        row = db.query(Book).filter(Book.id == kb_id).first()
        if not row or (owner is not None and row.owner != owner):
            raise HTTPException(404, "Book not found")
        row.favorite = bool(favorite)
        db.commit()
        db.refresh(row)
        b = _book_dict(row)
    finally:
        db.close()
    b["progress"] = get_progress(owner, kb_id, missing_ok=True)
    return b


def add_book(owner: Optional[str], filename: str, content: bytes, *, mime: str = "") -> dict:
    """Add a book: store the bytes in BOOKS_DIR, extract text + RAG-index it
    (kind="book") so Iris can search its contents. Dedupes by content hash per
    owner (returns the existing book on a repeat upload)."""
    from core.database import SessionLocal, Book
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(400, "Upload must be an .epub or .pdf file")
    tmp = os.path.join(tempfile.gettempdir(), f"bookup-{uuid.uuid4().hex}{ext}")
    try:
        with open(tmp, "wb") as f:
            f.write(content)
        sha = content_extract.sha256_file(tmp)
        fname = filename or f"book{ext}"
        db = SessionLocal()
        try:
            if sha:
                existing = (
                    db.query(Book)
                    .filter(Book.owner == owner, Book.sha256 == sha)
                    .first()
                )
                if existing:
                    return _book_dict(existing)
            try:
                size = os.path.getsize(tmp)
            except OSError:
                size = None
            text = content_extract.extract_text(tmp, fname, mime or "", owner=owner)
            bid = uuid.uuid4().hex
            rel = _copy_into_books(owner, bid, tmp, fname)
            row = Book(
                id=bid, owner=owner, filename=fname, mime=mime or None,
                file_size=size, sha256=sha, path=rel, text=text, indexed=False,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            if content_rag.index_text(owner, bid, text, RAG_KIND, filename=fname, source="book"):
                row.indexed = True
                db.commit()
                db.refresh(row)
            return _book_dict(row)
        finally:
            db.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def set_title(owner: Optional[str], kb_id: str, title: str) -> dict:
    """Rename the book (display filename), preserving its extension."""
    from core.database import SessionLocal, Book
    clean = re.sub(r"\s+", " ", title or "").strip()[:200]
    if not clean:
        raise HTTPException(400, "Title is required")
    db = SessionLocal()
    try:
        row = db.query(Book).filter(Book.id == kb_id).first()
        if not row or (owner is not None and row.owner != owner):
            raise HTTPException(404, "Book not found")
        ext = os.path.splitext(row.filename or "")[1].lower() or f".{kind_of(row.filename or '') or 'pdf'}"
        row.filename = f"{clean}{ext}"
        kind = kind_of(row.filename)
        db.commit()
    finally:
        db.close()
    _update_progress_title(owner, kb_id, clean)
    return {"book_id": kb_id, "path": kb_id, "kind": kind, "title": clean}


def delete_book(owner: Optional[str], kb_id: str) -> bool:
    """Delete the book — its row, its bytes, its RAG chunks, and its reading
    progress + annotations."""
    from core.database import SessionLocal, Book, BookProgress, BookAnnotation
    db = SessionLocal()
    try:
        row = db.query(Book).filter(Book.id == kb_id).first()
        if not row or (owner is not None and row.owner != owner):
            return False
        rel = row.path
        db.delete(row)
        db.query(BookProgress).filter(BookProgress.id == kb_id).delete()
        db.query(BookAnnotation).filter(BookAnnotation.book_id == kb_id).delete()
        db.commit()
    finally:
        db.close()
    content_rag.deindex(kb_id)  # drop the book's vectors so deleted text can't resurface
    if rel:
        try:
            os.remove(os.path.join(_books_dir(), rel))
        except OSError:
            pass
    return True


# --------------------------------------------------------------------------- #
# Reading progress (BookProgress, keyed by the knowledge id)                  #
# --------------------------------------------------------------------------- #

def _progress_to_dict(row, kb_id: str) -> dict:
    return {
        "book_id": kb_id,
        "path": kb_id,
        "kind": row.kind or "",
        "title": row.title or "",
        "author": row.author or "",
        "chapter_index": int(row.chapter_index or 0),
        "chapter_title": row.chapter_title or "",
        "scroll_percent": float(row.scroll_percent or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_progress(owner: Optional[str], kb_id: str, *, missing_ok: bool = False) -> dict:
    from core.database import SessionLocal, BookProgress
    db = SessionLocal()
    try:
        row = db.query(BookProgress).filter(BookProgress.id == kb_id).first()
        if row:
            return _progress_to_dict(row, kb_id)
    finally:
        db.close()
    return {"book_id": kb_id, "path": kb_id, "chapter_index": 0, "scroll_percent": 0, "updated_at": None}


def save_progress(owner: Optional[str], kb_id: str, *, chapter_index: int, scroll_percent: float = 0,
                  chapter_title: str = "", title: str = "", author: str = "", kind: str = "") -> dict:
    from core.database import SessionLocal, BookProgress
    db = SessionLocal()
    try:
        row = db.query(BookProgress).filter(BookProgress.id == kb_id).first()
        if not row:
            row = BookProgress(id=kb_id, owner=owner, rel_path=kb_id)
            db.add(row)
        row.kind = kind or row.kind or ""
        if title:
            row.title = title
        if author:
            row.author = author
        row.chapter_index = max(0, int(chapter_index or 0))
        row.chapter_title = (chapter_title or "")[:200]
        row.scroll_percent = max(0, min(float(scroll_percent or 0), 100))
        db.commit()
        db.refresh(row)
        return _progress_to_dict(row, kb_id)
    finally:
        db.close()


def _update_progress_title(owner: Optional[str], kb_id: str, title: str) -> None:
    from core.database import SessionLocal, BookProgress
    db = SessionLocal()
    try:
        row = db.query(BookProgress).filter(BookProgress.id == kb_id).first()
        if row:
            row.title = title
            db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Annotations (BookAnnotation, keyed by the knowledge id)                     #
# --------------------------------------------------------------------------- #

def _annotation_to_dict(row) -> dict:
    return {
        "id": row.id,
        "type": row.type,
        "chapter_index": int(row.chapter_index or 0),
        "chapter_title": row.chapter_title or "",
        "text": row.text or "",
        "note": row.note or "",
        "color": row.color or "",
        "scroll_percent": float(row.scroll_percent or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_annotations(owner: Optional[str], kb_id: str) -> dict:
    from core.database import SessionLocal, BookAnnotation
    db = SessionLocal()
    try:
        rows = (db.query(BookAnnotation)
                .filter(BookAnnotation.book_id == kb_id)
                .order_by(BookAnnotation.created_at.asc()).all())
        return {"book_id": kb_id, "path": kb_id, "items": [_annotation_to_dict(r) for r in rows]}
    finally:
        db.close()


def add_annotation(owner: Optional[str], kb_id: str, *, type: str = "bookmark", chapter_index: int = 0,
                   chapter_title: str = "", text: str = "", note: str = "", color: str = "",
                   scroll_percent: float = 0) -> dict:
    from core.database import SessionLocal, BookAnnotation
    if type not in ("bookmark", "highlight"):
        raise HTTPException(400, "type must be 'bookmark' or 'highlight'")
    if type == "highlight" and not (text or "").strip():
        raise HTTPException(400, "A highlight needs selected text")
    db = SessionLocal()
    try:
        row = BookAnnotation(
            id=uuid.uuid4().hex[:12], owner=owner, book_id=kb_id, rel_path=kb_id,
            type=type, chapter_index=max(0, int(chapter_index or 0)),
            chapter_title=(chapter_title or "")[:200],
            text=(text or "")[:2000] if type == "highlight" else "",
            note=(note or "")[:1000], color=(color or "")[:24],
            scroll_percent=max(0, min(float(scroll_percent or 0), 100)),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _annotation_to_dict(row)
    finally:
        db.close()


def delete_annotation(owner: Optional[str], kb_id: str, ann_id: str) -> bool:
    from core.database import SessionLocal, BookAnnotation
    db = SessionLocal()
    try:
        n = (db.query(BookAnnotation)
             .filter(BookAnnotation.book_id == kb_id, BookAnnotation.id == ann_id).delete())
        db.commit()
        return bool(n)
    finally:
        db.close()
