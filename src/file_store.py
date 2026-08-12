"""
file_store.py — the native Files store.

Any uploaded file that isn't media (Gallery), or authored text
(Documents) lives here: docx/xlsx/csv/json/txt/md/audio/zip/… The bytes are
copied into DATA_DIR/files (so they stay openable), the text is extracted and
RAG-indexed under kind="file", and a `files` row records the metadata + tags so
the user can search/tag/browse/open it and Iris can recall it.

Reuses the shared helpers (src.content_extract for text, src.content_rag for
indexing) so files are handled exactly like every other content store.
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import List, Optional

from src import content_extract, content_rag

logger = logging.getLogger(__name__)

RAG_KIND = "file"


def _data_dir() -> str:
    try:
        from src.constants import DATA_DIR
        return DATA_DIR
    except Exception:
        return os.path.join(os.getcwd(), "data")


def _files_dir() -> str:
    """The Files-owned byte store (durable, separate from chat uploads)."""
    return os.path.join(_data_dir(), "files")


def _owner_slug(owner) -> str:
    raw = str(owner or "_anon")
    return "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in raw) or "_anon"


def _copy_into_store(owner, file_id: str, src_path: str, filename: str) -> Optional[str]:
    try:
        ext = os.path.splitext(filename or src_path)[1].lower()
        rel = os.path.join(_owner_slug(owner), file_id[:2], f"{file_id}{ext}")
        dest = os.path.join(_files_dir(), rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src_path, dest)
        return rel
    except Exception as e:
        logger.warning("files: could not copy %s into store: %s", filename, e)
        return None


def file_abspath(owner: Optional[str], file_id: str) -> Optional[str]:
    """Absolute path to a file's bytes (owner-scoped) for serving — None if
    missing/forbidden."""
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        fi = db.query(FileItem).filter(FileItem.id == file_id).first()
        if not fi or (owner is not None and fi.owner != owner) or not fi.path:
            return None
        p = os.path.join(_files_dir(), fi.path)
        return p if os.path.exists(p) else None
    finally:
        db.close()


def _to_dict(fi) -> dict:
    return {
        "id": fi.id,
        "owner": fi.owner,
        "upload_id": fi.upload_id,
        "filename": fi.filename,
        "mime": fi.mime,
        "file_size": fi.file_size,
        "sha256": fi.sha256,
        "tags": content_extract.split_tags(fi.tags),
        "ai_tags": content_extract.split_tags(fi.ai_tags),
        "source": fi.source,
        "indexed": bool(fi.indexed),
        "favorite": bool(getattr(fi, "favorite", False)),
        "url": (f"/api/files/{fi.id}/raw" if fi.path
                else (f"/api/upload/{fi.upload_id}" if fi.upload_id else None)),
        "has_file": bool(fi.path or fi.upload_id),
        "excerpt": (fi.text or "")[:300],
        "created_at": fi.created_at.isoformat() if fi.created_at else None,
    }


def _index(fi) -> bool:
    return content_rag.index_text(
        fi.owner, fi.id, fi.text or "", RAG_KIND,
        filename=fi.filename or "", source=fi.source or "",
    )


def ingest(owner: Optional[str], *, file_path: str, filename: str = "",
           mime: Optional[str] = None, upload_id: Optional[str] = None,
           source: str = "upload", tags="", extract: bool = True) -> dict:
    """Ingest one file: extract text, dedupe by content hash (per owner), record
    it, copy its bytes into the store, and RAG-index it. Returns the row dict
    (the existing row when the same content was already ingested)."""
    from core.database import SessionLocal, FileItem

    filename = filename or os.path.basename(file_path)
    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = None
    sha = content_extract.sha256_file(file_path)

    db = SessionLocal()
    try:
        if sha:
            existing = (
                db.query(FileItem)
                .filter(FileItem.owner == owner, FileItem.sha256 == sha)
                .first()
            )
            if existing:
                return _to_dict(existing)

        text = content_extract.extract_text(file_path, filename, mime or "", owner=owner) if extract else ""
        fid = uuid.uuid4().hex
        rel_path = _copy_into_store(owner, fid, file_path, filename)
        fi = FileItem(
            id=fid, owner=owner, upload_id=upload_id, filename=filename, mime=mime,
            file_size=size, sha256=sha, path=rel_path, text=text,
            tags=content_extract.norm_tags(tags), ai_tags="", source=source, indexed=False,
        )
        db.add(fi)
        db.commit()
        db.refresh(fi)
        if _index(fi):
            fi.indexed = True
            db.commit()
            db.refresh(fi)
        return _to_dict(fi)
    finally:
        db.close()


def extract_and_index(owner: Optional[str], file_id: str) -> Optional[dict]:
    """Finish a deferred ingest (``ingest(extract=False)``) off the request path:
    extract text from the stored bytes, RAG-index it, update the row."""
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        fi = (
            db.query(FileItem)
            .filter(FileItem.id == file_id, FileItem.owner == owner)
            .first()
        )
        if not fi or not fi.path:
            return None
        abspath = os.path.join(_files_dir(), fi.path)
        try:
            text = content_extract.extract_text(abspath, fi.filename or "", fi.mime or "", owner=owner)
        except Exception:
            text = ""
        if text:
            fi.text = text
        if _index(fi):
            fi.indexed = True
        db.commit()
        db.refresh(fi)
        return _to_dict(fi)
    finally:
        db.close()


def search(owner: Optional[str], q: str = "", tags=None, limit: int = 50) -> list:
    """Keyword / metadata search (filename / text / tags / ai_tags), owner-scoped,
    newest-first. Each entry in `tags` further filters (AND)."""
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        query = db.query(FileItem)
        if owner is not None:
            query = query.filter(FileItem.owner == owner)
        q = (q or "").strip()
        if q:
            like = f"%{q}%"
            query = query.filter(
                FileItem.filename.ilike(like) | FileItem.text.ilike(like)
                | FileItem.tags.ilike(like) | FileItem.ai_tags.ilike(like)
            )
        for t in (tags or []):
            t = str(t).strip()
            if not t:
                continue
            like = f"%{t}%"
            query = query.filter(FileItem.tags.ilike(like) | FileItem.ai_tags.ilike(like))
        rows = (
            query.order_by(FileItem.created_at.desc())
            .limit(max(1, min(int(limit or 50), 500)))
            .all()
        )
        return [_to_dict(r) for r in rows]
    finally:
        db.close()


def get(owner: Optional[str], file_id: str) -> Optional[dict]:
    """Full record incl. the FULL extracted text (owner-scoped) — backs the
    open-the-real-file verification path."""
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        fi = db.query(FileItem).filter(FileItem.id == file_id).first()
        if not fi or (owner is not None and fi.owner != owner):
            return None
        d = _to_dict(fi)
        d["text"] = fi.text or ""
        return d
    finally:
        db.close()


def set_tags(owner: Optional[str], file_id: str, tags) -> Optional[dict]:
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        fi = db.query(FileItem).filter(FileItem.id == file_id).first()
        if not fi or (owner is not None and fi.owner != owner):
            return None
        fi.tags = content_extract.norm_tags(tags)
        db.commit()
        db.refresh(fi)
        return _to_dict(fi)
    finally:
        db.close()


def rename(owner: Optional[str], file_id: str, new_filename: str) -> Optional[dict]:
    from core.database import SessionLocal, FileItem

    new_filename = (new_filename or "").strip()
    if not new_filename:
        return None
    db = SessionLocal()
    try:
        fi = db.query(FileItem).filter(FileItem.id == file_id).first()
        if not fi or (owner is not None and fi.owner != owner):
            return None
        fi.filename = new_filename[:255]
        db.commit()
        db.refresh(fi)
        return _to_dict(fi)
    finally:
        db.close()


def update_text(owner: Optional[str], file_id: str, new_text: Optional[str], *,
                filename: Optional[str] = None) -> Optional[dict]:
    """Edit a file's searchable text (owner-scoped) and RE-INDEX RAG. For text
    files this also rewrites the stored bytes; for binaries it updates only the
    extracted text (the user's correction of OCR/extraction)."""
    from core.database import SessionLocal, FileItem

    if new_text is None and not filename:
        return None
    db = SessionLocal()
    try:
        fi = db.query(FileItem).filter(FileItem.id == file_id).first()
        if not fi or (owner is not None and fi.owner != owner):
            return None
        if filename:
            fi.filename = str(filename).strip()[:255] or fi.filename
        if new_text is not None:
            ext = os.path.splitext(fi.filename or fi.path or "")[1].lower()
            if ext in content_extract.TEXT_EXTS and fi.path:
                abspath = os.path.join(_files_dir(), fi.path)
                try:
                    os.makedirs(os.path.dirname(abspath), exist_ok=True)
                    with open(abspath, "w", encoding="utf-8") as fh:
                        fh.write(new_text)
                    fi.sha256 = content_extract.sha256_file(abspath)
                    fi.file_size = os.path.getsize(abspath)
                except OSError as e:
                    logger.warning("files: could not rewrite bytes for %s: %s", file_id, e)
            fi.text = new_text
            content_rag.deindex(file_id)       # drop stale chunks first…
            fi.indexed = _index(fi)            # …then index the new text
        db.commit()
        db.refresh(fi)
        return _to_dict(fi)
    finally:
        db.close()


def append_text(owner: Optional[str], file_id: str, extra_text: str) -> Optional[dict]:
    extra = (extra_text or "").strip()
    rec = get(owner, file_id)
    if not rec:
        return None
    if not extra:
        return rec
    combined = ((rec.get("text") or "").rstrip() + "\n\n" + extra).strip()
    return update_text(owner, file_id, combined)


def generate_ai_tags(owner: Optional[str], file_id: str) -> Optional[dict]:
    """Generate + store AI topical tags from the extracted text (Utility model).
    Best-effort; safe to run as a background task."""
    rec = get(owner, file_id)
    if not rec:
        return None
    tags = content_extract.generate_tags_via_llm(rec.get("text") or "", owner)
    if not tags:
        return rec
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        fi = db.query(FileItem).filter(FileItem.id == file_id).first()
        if not fi or (owner is not None and fi.owner != owner):
            return None
        fi.ai_tags = content_extract.norm_tags(tags)
        db.commit()
        db.refresh(fi)
        return _to_dict(fi)
    finally:
        db.close()


def list_tags(owner: Optional[str]) -> List[str]:
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        # Project only the two tag columns instead of hydrating full FileItem
        # rows (each carries the large extracted-`text` blob) just to read tags.
        q = db.query(FileItem.tags, FileItem.ai_tags)
        if owner is not None:
            q = q.filter(FileItem.owner == owner)
        seen = {}
        for tags, ai_tags in q.all():
            for t in content_extract.split_tags(tags) + content_extract.split_tags(ai_tags):
                seen.setdefault(t.lower(), t)
        return sorted(seen.values(), key=str.lower)
    finally:
        db.close()


def delete(owner: Optional[str], file_id: str) -> bool:
    """Delete a file record (owner-scoped) + its bytes + its RAG chunks."""
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        fi = db.query(FileItem).filter(FileItem.id == file_id).first()
        if not fi or (owner is not None and fi.owner != owner):
            return False
        rel = fi.path
        db.delete(fi)
        db.commit()
    finally:
        db.close()
    content_rag.deindex(file_id)
    if rel:
        try:
            os.remove(os.path.join(_files_dir(), rel))
        except OSError:
            pass
    return True


def count(owner: Optional[str]) -> int:
    from core.database import SessionLocal, FileItem

    db = SessionLocal()
    try:
        q = db.query(FileItem)
        if owner is not None:
            q = q.filter(FileItem.owner == owner)
        return q.count()
    finally:
        db.close()
