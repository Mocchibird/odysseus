"""Books / E-Reader = a reading view over the Knowledge base's PDF/EPUB files.

A "book" IS a knowledge file (`KnowledgeFile`) whose type is PDF or EPUB, so the
Books window and the Knowledge panel show the same files and stay in sync, and
Iris can search book contents via `search_knowledge` (the KB already extracts +
RAG-indexes pdf/epub text). This module adds only the Books-specific state on
top — reading progress + bookmarks/highlights — keyed by the knowledge file id.
There is no separate book file store; the bytes live in the KB-owned store.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from src import knowledge_base as kb

logger = logging.getLogger(__name__)

SUPPORTED_BOOK_EXTENSIONS = {".epub", ".pdf"}


# --------------------------------------------------------------------------- #
# Identity: a book is a knowledge file whose type is pdf/epub                 #
# --------------------------------------------------------------------------- #

def kind_of(filename: str) -> Optional[str]:
    return {".pdf": "pdf", ".epub": "epub"}.get(os.path.splitext(filename or "")[1].lower())


def is_book(filename: str) -> bool:
    return kind_of(filename) is not None


def _book_dict(rec: dict) -> dict:
    """Map a knowledge-file record to a book dict. The Books API identifier is the
    knowledge id, carried as both `id` and `path` (the existing frontend passes a
    book's `path` to /api/books/* — here that value is the knowledge id)."""
    kid = rec["id"]
    fname = rec.get("filename") or "Book"
    return {
        "id": kid,
        "path": kid,
        "kb_id": kid,
        "filename": fname,
        "title": os.path.splitext(fname)[0] or "Book",
        "kind": kind_of(fname),
        "mime": rec.get("mime"),
        "size": rec.get("file_size"),
        "url": rec.get("url"),
        "excerpt": rec.get("excerpt") or "",
        "tags": rec.get("tags") or [],
    }


def get_book(owner: Optional[str], kb_id: str) -> Optional[dict]:
    """The knowledge file as a book dict, or None if it isn't a readable book
    (or not this owner's)."""
    rec = kb.get(owner, kb_id)
    if not rec or not is_book(rec.get("filename") or ""):
        return None
    return _book_dict(rec)


def resolve_book_file(owner: Optional[str], kb_id: str) -> Path:
    """Absolute path to the book's bytes (owner-scoped), via the KB-owned store."""
    p = kb.file_abspath(owner, kb_id)
    if not p:
        raise HTTPException(404, "Book file not found")
    return Path(p)


def list_books(owner: Optional[str], query: str = "", limit: int = 50) -> list[dict]:
    cap = max(1, int(limit or 50))
    # KB search matches filename / extracted text / tags; keep only readable books.
    files = kb.search(owner, q=query or "", limit=500)
    books: list[dict] = []
    for rec in files:
        if not is_book(rec.get("filename") or ""):
            continue
        b = _book_dict(rec)
        b["progress"] = get_progress(owner, b["id"], missing_ok=True)
        books.append(b)
        if len(books) >= cap:
            break
    return books


def add_book(owner: Optional[str], filename: str, content: bytes, *, mime: str = "") -> dict:
    """Add a book = ingest it into the Knowledge base (extract text + RAG-index),
    so it shows up in BOTH Books and Knowledge and Iris can search its contents."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(400, "Upload must be an .epub or .pdf file")
    tmp = os.path.join(tempfile.gettempdir(), f"bookup-{uuid.uuid4().hex}{ext}")
    try:
        with open(tmp, "wb") as f:
            f.write(content)
        rec = kb.ingest(owner, file_path=tmp, filename=filename or f"book{ext}",
                        mime=mime or "", source="book")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return _book_dict(rec)


def set_title(owner: Optional[str], kb_id: str, title: str) -> dict:
    """Rename the book — updates the underlying knowledge file's name (so the
    rename appears in BOTH Books and Knowledge), preserving its extension."""
    rec = kb.get(owner, kb_id)
    if not rec or not is_book(rec.get("filename") or ""):
        raise HTTPException(404, "Book not found")
    clean = re.sub(r"\s+", " ", title or "").strip()[:200]
    if not clean:
        raise HTTPException(400, "Title is required")
    ext = os.path.splitext(rec["filename"])[1].lower() or f".{kind_of(rec['filename']) or 'pdf'}"
    kb.rename(owner, kb_id, f"{clean}{ext}")
    _update_progress_title(owner, kb_id, clean)
    return {"book_id": kb_id, "path": kb_id, "kind": kind_of(rec["filename"]), "title": clean}


def delete_book(owner: Optional[str], kb_id: str) -> bool:
    """Delete the book = delete the knowledge file (gone from both Books and
    Knowledge) plus its reading progress + annotations."""
    from core.database import SessionLocal, BookProgress, BookAnnotation
    ok = kb.delete(owner, kb_id)
    db = SessionLocal()
    try:
        db.query(BookProgress).filter(BookProgress.id == kb_id).delete()
        db.query(BookAnnotation).filter(BookAnnotation.book_id == kb_id).delete()
        db.commit()
    finally:
        db.close()
    return ok


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
