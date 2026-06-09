"""Native storage backend for the Books / E-Reader (replaces the Obsidian vault).

Book bytes live under ``DATA_DIR/books/<owner-slug>/<book_id><ext>``. Per-book
reading progress, custom title, and annotations live in dedicated DB tables
(BookFile / BookProgress / BookAnnotation). Book full text is indexed into the
shared RAG store (kind="book") so Iris can search inside books. No Obsidian
vault, no filesystem Markdown mirrors.

Path-safety (owner slug, rel-path sanitisation, traversal guard) is a faithful
port of the old vault logic so existing book paths resolve identically.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from src.constants import BOOKS_DIR

logger = logging.getLogger(__name__)

SUPPORTED_BOOK_EXTENSIONS = {".epub", ".pdf"}
RAG_KIND = "book"
_MAX_TEXT_CHARS = 200_000
TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}


# --------------------------------------------------------------------------- #
# Identity & paths (faithful port of the vault path-safety logic)             #
# --------------------------------------------------------------------------- #

def owner_slug(owner: Optional[str]) -> str:
    raw = (owner or "local").strip() or "local"
    safe = re.sub(r"[^A-Za-z0-9_.@-]+", "_", raw).strip("._-")
    return safe[:120] or "local"


def safe_rel_path(rel_path: str, *, default_name: str = "book") -> str:
    """Sanitise a book identifier. Preserves the real filename (unicode, spaces,
    punctuation) while dropping path separators / traversal / control chars."""
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel:
        rel = default_name
    parts = []
    for part in rel.split("/"):
        part = re.sub(r"[\x00-\x1f\x7f]", "", part.strip())
        if not part or part in {".", ".."}:
            continue
        parts.append(part[:200])
    if not parts:
        parts = [default_name]
    return "/".join(parts)


def book_id(owner: Optional[str], rel_path: str) -> str:
    return hashlib.sha256(f"{owner_slug(owner)}/{safe_rel_path(rel_path)}".encode()).hexdigest()


def books_root() -> Path:
    root = Path(BOOKS_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _owner_dir(owner: Optional[str]) -> Path:
    d = books_root() / owner_slug(owner)
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_book_file(owner: Optional[str], rel_path: str) -> Path:
    """Physical path of a book's bytes. Stable function of (owner, rel_path),
    keyed by book_id so unusual filenames can't collide or escape the dir."""
    ext = Path(safe_rel_path(rel_path)).suffix.lower()
    base = _owner_dir(owner).resolve()
    target = (base / f"{book_id(owner, rel_path)}{ext}").resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Unsafe book path")
    return target


def store_bytes(owner: Optional[str], rel_path: str, content: bytes) -> Path:
    path = resolve_book_file(owner, rel_path)
    path.write_bytes(content)
    return path


# --------------------------------------------------------------------------- #
# Text extraction (for excerpt + RAG indexing)                                #
# --------------------------------------------------------------------------- #

def extract_text(path: Path, max_chars: int = _MAX_TEXT_CHARS) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            parts: list[str] = []
            for idx, page in enumerate(reader.pages):
                if len("\n".join(parts)) >= max_chars:
                    break
                try:
                    txt = (page.extract_text() or "").strip()
                except Exception:
                    txt = ""
                if txt:
                    parts.append(f"Page {idx + 1}\n{txt}")
            return "\n\n".join(parts)[:max_chars]
        except Exception:
            return ""
    if ext == ".epub":
        try:
            with zipfile.ZipFile(path) as zf:
                parts = []
                names = [
                    n for n in zf.namelist()
                    if n.lower().endswith((".xhtml", ".html", ".htm")) and not n.startswith("__MACOSX/")
                ]
                for name in names:
                    if len("\n".join(parts)) >= max_chars:
                        break
                    raw = zf.read(name).decode("utf-8", errors="replace")
                    try:
                        from bs4 import BeautifulSoup
                        txt = BeautifulSoup(raw, "html.parser").get_text("\n", strip=True)
                    except Exception:
                        txt = re.sub(r"<[^>]+>", " ", raw)
                    txt = re.sub(r"\s+", " ", txt).strip()
                    if txt:
                        parts.append(txt)
                return "\n\n".join(parts)[:max_chars]
        except Exception:
            return ""
    if ext in TEXT_EXTENSIONS:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        except OSError:
            return ""
    return ""


def index_in_rag(owner: Optional[str], bid: str, filename: str, text: str) -> bool:
    """Add a book's text to the shared RAG store (owner-scoped, kind="book").
    Best-effort: never raises, so a RAG outage doesn't block reading."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        from src.rag_singleton import get_rag_manager
        rag = get_rag_manager()
        if not rag:
            return False
        meta = {"owner": owner or "", "kind": RAG_KIND, "book_id": bid, "filename": filename or ""}
        return bool(rag.add_document(text, meta))
    except Exception as e:
        logger.debug("book_store: RAG index failed for %s: %s", bid, e)
        return False


# --------------------------------------------------------------------------- #
# BookFile (the discovery index)                                              #
# --------------------------------------------------------------------------- #

def _book_to_dict(row) -> dict:
    return {
        "id": row.id,
        "path": row.rel_path,
        "filename": row.filename,
        "title": row.title or Path(row.rel_path or "").stem,
        "custom_title": row.title or "",
        "kind": row.kind or Path(row.rel_path or "").suffix.lower().lstrip("."),
        "mime": row.mime,
        "size": row.size,
        "excerpt": row.excerpt or "",
        "indexed": bool(row.indexed),
    }


def _unique_rel_path(db, owner_key: str, desired: str) -> str:
    """Avoid clobbering a different book that already owns this filename."""
    from core.database import BookFile
    base = safe_rel_path(desired)
    stem, ext = Path(base).stem, Path(base).suffix
    candidate = base
    for idx in range(2, 1000):
        exists = db.query(BookFile).filter(BookFile.owner == owner_key, BookFile.rel_path == candidate).first()
        if not exists:
            return candidate
        candidate = f"{stem} {idx}{ext}"
    return candidate


def upsert_book(owner: Optional[str], desired_name: str, content: bytes, *, mime: str = "") -> dict:
    """Store book bytes + create/refresh its BookFile row. Returns the row dict."""
    from core.database import SessionLocal, BookFile
    ext = Path(desired_name or "").suffix.lower()
    if ext not in SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(400, "Upload must be an .epub or .pdf file")
    owner_key = owner_slug(owner)
    db = SessionLocal()
    try:
        rel_path = _unique_rel_path(db, owner_key, desired_name or f"book{ext}")
        bid = book_id(owner_key, rel_path)
        path = store_bytes(owner_key, rel_path, content)
        excerpt = (extract_text(path, 4000) or "")[:2000]
        row = db.query(BookFile).filter(BookFile.id == bid).first()
        if not row:
            row = BookFile(id=bid, owner=owner_key)
            db.add(row)
        row.rel_path = rel_path
        row.filename = Path(rel_path).name
        row.kind = ext.lstrip(".")
        row.mime = mime or mimetypes.guess_type(str(path))[0] or ""
        row.size = int(path.stat().st_size)
        row.sha256 = hashlib.sha256(content).hexdigest()
        row.excerpt = excerpt
        row.indexed = False
        db.commit()
        db.refresh(row)
        return _book_to_dict(row)
    finally:
        db.close()


def index_book(owner: Optional[str], rel_path: str) -> dict:
    """Extract full text + add to RAG; mark the row indexed. Idempotent."""
    from core.database import SessionLocal, BookFile
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    path = resolve_book_file(owner_key, safe)
    text = extract_text(path) if path.is_file() else ""
    ok = index_in_rag(owner_key, bid, Path(safe).name, text)
    db = SessionLocal()
    try:
        row = db.query(BookFile).filter(BookFile.id == bid).first()
        if row:
            if text and not (row.excerpt or "").strip():
                row.excerpt = text[:2000]
            row.indexed = bool(ok)
            db.commit()
            db.refresh(row)
            return _book_to_dict(row)
    finally:
        db.close()
    return {"id": bid, "path": safe, "indexed": bool(ok)}


def register_book(owner: Optional[str], rel_path: str, *, mime: str = "") -> Optional[dict]:
    """Ensure a BookFile row exists for an on-disk book (lightweight, no parse).
    Returns the row dict, or None if the file isn't present."""
    from core.database import SessionLocal, BookFile
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    path = resolve_book_file(owner_key, safe)
    if not path.is_file():
        return None
    db = SessionLocal()
    try:
        row = db.query(BookFile).filter(BookFile.id == bid).first()
        if not row:
            row = BookFile(id=bid, owner=owner_key, rel_path=safe)
            db.add(row)
        row.filename = Path(safe).name
        row.kind = Path(safe).suffix.lower().lstrip(".")
        if row.mime is None:
            row.mime = mime or mimetypes.guess_type(str(path))[0] or ""
        if row.size is None:
            row.size = int(path.stat().st_size)
        db.commit()
        db.refresh(row)
        return _book_to_dict(row)
    finally:
        db.close()


def query_books(owner: Optional[str], query: str = "", limit: int = 50) -> list[dict]:
    from core.database import SessionLocal, BookFile
    from sqlalchemy import or_
    owner_key = owner_slug(owner)
    needle = (query or "").strip()
    db = SessionLocal()
    try:
        q = db.query(BookFile).filter(BookFile.owner == owner_key)
        if needle:
            like = f"%{needle}%"
            q = q.filter(or_(
                BookFile.title.ilike(like),
                BookFile.filename.ilike(like),
                BookFile.rel_path.ilike(like),
                BookFile.excerpt.ilike(like),
            ))
        rows = q.order_by(BookFile.updated_at.desc()).limit(max(1, int(limit or 50))).all()
        return [_book_to_dict(r) for r in rows]
    finally:
        db.close()


def get_book(owner: Optional[str], rel_path: str) -> Optional[dict]:
    from core.database import SessionLocal, BookFile
    db = SessionLocal()
    try:
        row = db.query(BookFile).filter(BookFile.id == book_id(owner, rel_path)).first()
        return _book_to_dict(row) if row else None
    finally:
        db.close()


def set_title(owner: Optional[str], rel_path: str, title: str) -> dict:
    from core.database import SessionLocal, BookFile, BookProgress
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    clean = re.sub(r"\s+", " ", title or "").strip()[:200]
    if not clean:
        raise HTTPException(400, "Title is required")
    db = SessionLocal()
    try:
        row = db.query(BookFile).filter(BookFile.id == bid).first()
        if not row:
            raise HTTPException(404, "Book not found")
        row.title = clean
        prog = db.query(BookProgress).filter(BookProgress.id == bid).first()
        if prog:
            prog.title = clean
        db.commit()
        db.refresh(row)
        return {"book_id": bid, "path": safe, "kind": row.kind, "title": clean}
    finally:
        db.close()


def delete_book(owner: Optional[str], rel_path: str) -> bool:
    from core.database import SessionLocal, BookFile, BookProgress, BookAnnotation
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    db = SessionLocal()
    try:
        row = db.query(BookFile).filter(BookFile.id == bid).first()
        if not row:
            return False
        db.query(BookProgress).filter(BookProgress.id == bid).delete()
        db.query(BookAnnotation).filter(BookAnnotation.book_id == bid).delete()
        db.delete(row)
        db.commit()
    finally:
        db.close()
    try:
        p = resolve_book_file(owner_key, safe)
        if p.is_file():
            p.unlink()
    except Exception:
        pass
    return True


# --------------------------------------------------------------------------- #
# Reading progress                                                            #
# --------------------------------------------------------------------------- #

def _progress_to_dict(row, safe: str, bid: str) -> dict:
    return {
        "book_id": bid,
        "path": safe,
        "kind": row.kind or "",
        "title": row.title or "",
        "author": row.author or "",
        "chapter_index": int(row.chapter_index or 0),
        "chapter_title": row.chapter_title or "",
        "scroll_percent": float(row.scroll_percent or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_progress(owner: Optional[str], rel_path: str, *, missing_ok: bool = False) -> dict:
    from core.database import SessionLocal, BookProgress
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    db = SessionLocal()
    try:
        row = db.query(BookProgress).filter(BookProgress.id == bid).first()
        if row:
            return _progress_to_dict(row, safe, bid)
    finally:
        db.close()
    return {"book_id": bid, "path": safe, "chapter_index": 0, "scroll_percent": 0, "updated_at": None}


def save_progress(owner: Optional[str], rel_path: str, *, chapter_index: int, scroll_percent: float = 0,
                  chapter_title: str = "", title: str = "", author: str = "", kind: str = "") -> dict:
    from core.database import SessionLocal, BookProgress, BookFile
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    ext_kind = kind or Path(safe).suffix.lower().lstrip(".")
    db = SessionLocal()
    try:
        row = db.query(BookProgress).filter(BookProgress.id == bid).first()
        if not row:
            row = BookProgress(id=bid, owner=owner_key, rel_path=safe)
            db.add(row)
        # Prefer an explicit/custom title, then the BookFile custom title, then the file stem.
        display = title
        if not display:
            bf = db.query(BookFile).filter(BookFile.id == bid).first()
            display = (bf.title if bf else "") or Path(safe).stem
        row.rel_path = safe
        row.kind = ext_kind
        row.title = display
        row.author = author or row.author or ""
        row.chapter_index = max(0, int(chapter_index or 0))
        row.chapter_title = (chapter_title or "")[:200]
        row.scroll_percent = max(0, min(float(scroll_percent or 0), 100))
        db.commit()
        db.refresh(row)
        return _progress_to_dict(row, safe, bid)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Annotations (bookmarks & highlights)                                        #
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


def list_annotations(owner: Optional[str], rel_path: str) -> dict:
    from core.database import SessionLocal, BookAnnotation
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    db = SessionLocal()
    try:
        rows = (db.query(BookAnnotation)
                .filter(BookAnnotation.book_id == bid)
                .order_by(BookAnnotation.created_at.asc()).all())
        return {"book_id": bid, "path": safe, "items": [_annotation_to_dict(r) for r in rows]}
    finally:
        db.close()


def add_annotation(owner: Optional[str], rel_path: str, *, type: str = "bookmark", chapter_index: int = 0,
                   chapter_title: str = "", text: str = "", note: str = "", color: str = "",
                   scroll_percent: float = 0) -> dict:
    from core.database import SessionLocal, BookAnnotation
    if type not in ("bookmark", "highlight"):
        raise HTTPException(400, "type must be 'bookmark' or 'highlight'")
    if type == "highlight" and not (text or "").strip():
        raise HTTPException(400, "A highlight needs selected text")
    owner_key = owner_slug(owner)
    safe = safe_rel_path(rel_path)
    bid = book_id(owner_key, safe)
    db = SessionLocal()
    try:
        row = BookAnnotation(
            id=uuid.uuid4().hex[:12], owner=owner_key, book_id=bid, rel_path=safe,
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


def delete_annotation(owner: Optional[str], rel_path: str, ann_id: str) -> bool:
    from core.database import SessionLocal, BookAnnotation
    owner_key = owner_slug(owner)
    bid = book_id(owner_key, rel_path)
    db = SessionLocal()
    try:
        n = (db.query(BookAnnotation)
             .filter(BookAnnotation.book_id == bid, BookAnnotation.id == ann_id).delete())
        db.commit()
        return bool(n)
    finally:
        db.close()
