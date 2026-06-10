"""
knowledge_base.py — native knowledge base.

Any uploaded file (pdf / image / md / docx / txt / …) is stored in the uploads
store, its text is extracted, recorded in the `knowledge_files` table, and
indexed into the RAG vector store — so Iris can recall it AND the user can
search / tag / browse it. The native, DB-backed knowledge store: files are added
manually (upload → POST /api/knowledge) and live entirely inside Odysseus.

Reuses, never reinvents:
  • text extraction  -> src.personal_docs (pdf / office / plain text)
  • image OCR/caption -> src.document_processor.analyze_image_with_vl
  • embeddings + hybrid search -> src.rag_singleton.get_rag_manager (VectorRAG)
  • tags pattern (`tags` user + `ai_tags` LLM) + q-search -> GalleryImage
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from typing import List, Optional

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
_PDF_EXTS = {".pdf"}
_OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".xls", ".epub"}
_TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".log",
              ".html", ".htm", ".rst", ".yaml", ".yml", ".tsv"}
# Binary types markitdown can't turn into text — skip extraction (the file is
# still stored + taggable, just has no searchable text) to avoid noisy
# "filetype not supported" warnings and wasted conversion attempts.
_SKIP_EXTS = {
    ".ogg", ".mp3", ".wav", ".m4a", ".flac", ".aac", ".opus", ".wma",  # audio
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv",           # video
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2",              # archives
    ".bin", ".exe", ".dmg", ".iso", ".so", ".dll",                     # binaries
}

RAG_KIND = "knowledge"  # metadata tag so RAG recall can be scoped to the KB


def _sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _data_dir() -> str:
    try:
        from src.constants import DATA_DIR
        return DATA_DIR
    except Exception:
        return os.path.join(os.getcwd(), "data")


def _kb_files_dir() -> str:
    """KB-owned file store (separate from chat uploads): the durable bytes the
    knowledge base serves, so files stay openable even after the vault is gone."""
    return os.path.join(_data_dir(), "knowledge_files")


def _owner_slug(owner) -> str:
    raw = str(owner or "_anon")
    return "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in raw) or "_anon"


def _copy_into_kb(owner, kb_id: str, src_path: str, filename: str) -> Optional[str]:
    """Copy the source file into the KB-owned store; return the path relative to
    _kb_files_dir() (stored on the row, served via /api/knowledge/{id}/raw)."""
    try:
        ext = os.path.splitext(filename or src_path)[1].lower()
        rel = os.path.join(_owner_slug(owner), kb_id[:2], f"{kb_id}{ext}")
        dest = os.path.join(_kb_files_dir(), rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src_path, dest)
        return rel
    except Exception as e:
        logger.warning("knowledge: could not copy %s into KB store: %s", filename, e)
        return None


def file_abspath(owner: Optional[str], kb_id: str) -> Optional[str]:
    """Absolute path to a KB file's bytes (owner-scoped) for serving — None if
    missing/forbidden. The user's deterministic 'open the real file' path."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner) or not kf.path:
            return None
        p = os.path.join(_kb_files_dir(), kf.path)
        return p if os.path.exists(p) else None
    finally:
        db.close()


def extract_text(file_path: str, filename: str = "", mime: str = "",
                 owner: Optional[str] = None) -> str:
    """Best-effort text extraction by file type, reusing the existing extractors.
    Images go through vision OCR/captioning so they're searchable too. Always
    returns a string ("" when nothing could be extracted — the file is still
    stored + taggable)."""
    ext = os.path.splitext(filename or file_path)[1].lower()
    mime = (mime or "").lower()
    if ext in _SKIP_EXTS or mime.startswith(("audio/", "video/")):
        return ""  # binary/media — stored + taggable, but no text to extract
    try:
        if ext in _PDF_EXTS or mime == "application/pdf":
            from src.personal_docs import extract_pdf_text
            return extract_pdf_text(file_path) or ""
        if ext in _IMAGE_EXTS or mime.startswith("image/"):
            try:
                from src.document_processor import analyze_image_with_vl
                return (analyze_image_with_vl(file_path, owner=owner) or "").strip()
            except Exception as e:
                logger.debug("knowledge: image OCR failed for %s: %s", filename, e)
                return ""
        if ext in _TEXT_EXTS or mime.startswith("text/"):
            from src.personal_docs import read_text_file
            return read_text_file(file_path) or ""
        if ext in _OFFICE_EXTS:
            from src.personal_docs import extract_office_text
            return extract_office_text(file_path) or ""
        # Unknown type: try the office/markitdown path, else give up gracefully.
        from src.personal_docs import extract_office_text
        return extract_office_text(file_path) or ""
    except Exception as e:
        logger.warning("knowledge: text extraction failed for %s: %s", filename, e)
        return ""


def _norm_tags(tags) -> str:
    """Normalize tags to a de-duplicated, comma-separated string."""
    if isinstance(tags, (list, tuple)):
        parts = [str(t).strip() for t in tags]
    else:
        parts = [t.strip() for t in str(tags or "").split(",")]
    seen, out = set(), []
    for t in parts:
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            out.append(t)
    return ", ".join(out)


def _split_tags(value) -> List[str]:
    return [t.strip() for t in str(value or "").split(",") if t.strip()]


def _to_dict(kf) -> dict:
    return {
        "id": kf.id,
        "owner": kf.owner,
        "upload_id": kf.upload_id,
        "filename": kf.filename,
        "mime": kf.mime,
        "file_size": kf.file_size,
        "sha256": kf.sha256,
        "tags": _split_tags(kf.tags),
        "ai_tags": _split_tags(kf.ai_tags),
        "source": kf.source,
        "indexed": bool(kf.indexed),
        "favorite": bool(getattr(kf, "favorite", False)),
        # The real-file open URL (KB-owned bytes preferred; falls back to the
        # uploads store). The user's "double-check the actual file" path.
        "url": (f"/api/knowledge/{kf.id}/raw" if kf.path
                else (f"/api/upload/{kf.upload_id}" if kf.upload_id else None)),
        "has_file": bool(kf.path or kf.upload_id),
        "excerpt": (kf.text or "")[:300],
        "created_at": kf.created_at.isoformat() if kf.created_at else None,
    }


def _index_in_rag(kf) -> bool:
    """Add the file's text to the RAG vector store (owner-scoped). Best-effort:
    returns False (no raise) when ChromaDB / embeddings aren't available, so a
    RAG outage never blocks ingestion — the row can be re-indexed later."""
    text = (kf.text or "").strip()
    if not text:
        return False
    try:
        from src.rag_singleton import get_rag_manager
        rag = get_rag_manager()
        if not rag:
            return False
        meta = {
            "owner": kf.owner or "",
            "kind": RAG_KIND,
            "kb_id": kf.id,
            "filename": kf.filename or "",
            "source": kf.source or "",
        }
        return bool(rag.add_document(text, meta))
    except Exception as e:
        logger.debug("knowledge: RAG index failed for %s: %s", getattr(kf, "id", "?"), e)
        return False


def _remove_from_rag(kb_id: str) -> int:
    """Drop a file's chunks from the RAG vector store (by kb_id). Used before
    re-indexing an edit and on delete, so stale/removed text never resurfaces in
    Iris's recall. Best-effort — returns 0 when RAG is unavailable."""
    try:
        from src.rag_singleton import get_rag_manager
        rag = get_rag_manager()
        if not rag:
            return 0
        fn = getattr(rag, "delete_by_kb_id", None)
        return int(fn(kb_id)) if fn else 0
    except Exception as e:
        logger.debug("knowledge: RAG delete failed for %s: %s", kb_id, e)
        return 0


def _generate_tags_via_llm(text: str, owner: Optional[str]) -> List[str]:
    """Ask the configured Utility model for a few lowercase topical tags for a
    document's text. Reuses the standard utility lane + fallback chain; returns
    [] on any failure so auto-tagging never blocks ingest/edit."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates
        from src.llm_core import llm_call_with_fallback

        url, model, headers = resolve_endpoint("utility", owner=owner)
        candidates = [(url, model, headers)] + resolve_utility_fallback_candidates(owner=owner)
        prompt = (
            "Read the document below and output 3-8 short, lowercase topical tags "
            "that capture what it is ABOUT (single words or 2-3 word phrases; no "
            "sentences). Return ONLY a comma-separated list, nothing else.\n\n"
            f"---\n{text[:6000]}\n---"
        )
        out = llm_call_with_fallback(
            candidates, [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=120,
        ) or ""
        seen, clean = set(), []
        for raw in out.replace("\n", ",").split(","):
            t = raw.strip().strip("#.-•*").strip().lower()
            if t and len(t) <= 40 and t not in seen:
                seen.add(t)
                clean.append(t)
            if len(clean) >= 8:
                break
        return clean
    except Exception as e:
        logger.debug("knowledge: ai-tag generation failed: %s", e)
        return []


def ingest(owner: Optional[str], *, file_path: str, filename: str = "",
           mime: Optional[str] = None, upload_id: Optional[str] = None,
           source: str = "upload", tags="", extract: bool = True) -> dict:
    """Ingest one file into the knowledge base: extract text, dedupe by content
    hash (per owner), record it, and RAG-index it. Returns the row as a dict
    (the existing row when the same content was already ingested)."""
    from core.database import SessionLocal, KnowledgeFile

    filename = filename or os.path.basename(file_path)
    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = None
    sha = _sha256_file(file_path)

    db = SessionLocal()
    try:
        if sha:
            existing = (
                db.query(KnowledgeFile)
                .filter(KnowledgeFile.owner == owner, KnowledgeFile.sha256 == sha)
                .first()
            )
            if existing:
                return _to_dict(existing)

        text = extract_text(file_path, filename, mime or "", owner=owner) if extract else ""
        kid = uuid.uuid4().hex
        rel_path = _copy_into_kb(owner, kid, file_path, filename)  # KB owns its bytes -> always openable
        kf = KnowledgeFile(
            id=kid,
            owner=owner,
            upload_id=upload_id,
            filename=filename,
            mime=mime,
            file_size=size,
            sha256=sha,
            path=rel_path,
            text=text,
            tags=_norm_tags(tags),
            ai_tags="",
            source=source,
            indexed=False,
        )
        db.add(kf)
        db.commit()
        db.refresh(kf)
        if _index_in_rag(kf):
            kf.indexed = True
            db.commit()
            db.refresh(kf)
        return _to_dict(kf)
    finally:
        db.close()


def extract_and_index(owner: Optional[str], kb_id: str) -> Optional[dict]:
    """Finish a deferred ingest (``ingest(extract=False)``): extract text from
    the stored bytes, RAG-index it, and update the row. Runs OFF the request
    path — image OCR can take minutes, and the agent-tool add 504'd through
    reverse proxies when extraction ran inline."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = (
            db.query(KnowledgeFile)
            .filter(KnowledgeFile.id == kb_id, KnowledgeFile.owner == owner)
            .first()
        )
        if not kf or not kf.path:
            return None
        abspath = os.path.join(_kb_files_dir(), kf.path)
        try:
            text = extract_text(abspath, kf.filename or "", kf.mime or "", owner=owner)
        except Exception:
            text = ""
        if text:
            kf.text = text
        if _index_in_rag(kf):
            kf.indexed = True
        db.commit()
        db.refresh(kf)
        return _to_dict(kf)
    finally:
        db.close()


def search(owner: Optional[str], q: str = "", tags=None, limit: int = 50) -> list:
    """Keyword / metadata search over the knowledge base (Gallery-style): `q`
    matches filename / extracted-text / tags / ai_tags; each entry in `tags`
    further filters (AND). Owner-scoped. Returns newest-first."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        query = db.query(KnowledgeFile)
        if owner is not None:
            query = query.filter(KnowledgeFile.owner == owner)
        q = (q or "").strip()
        if q:
            like = f"%{q}%"
            query = query.filter(
                KnowledgeFile.filename.ilike(like)
                | KnowledgeFile.text.ilike(like)
                | KnowledgeFile.tags.ilike(like)
                | KnowledgeFile.ai_tags.ilike(like)
            )
        for t in (tags or []):
            t = str(t).strip()
            if not t:
                continue
            like = f"%{t}%"
            query = query.filter(KnowledgeFile.tags.ilike(like) | KnowledgeFile.ai_tags.ilike(like))
        rows = (
            query.order_by(KnowledgeFile.created_at.desc())
            .limit(max(1, min(int(limit or 50), 500)))
            .all()
        )
        return [_to_dict(r) for r in rows]
    finally:
        db.close()


def semantic_search(owner: Optional[str], q: str, k: int = 5) -> list:
    """Vector recall over the knowledge base for Iris (owner-scoped, kind=knowledge).
    Returns [] gracefully when RAG is unavailable."""
    q = (q or "").strip()
    if not q:
        return []
    try:
        from src.rag_singleton import get_rag_manager
        rag = get_rag_manager()
        if not rag:
            return []
        try:
            hits = rag.search(q, k=k * 3, owner=owner or None) or []
        except TypeError:  # older signature without owner
            hits = rag.search(q, k=k * 3) or []
        out = []
        for h in hits:
            meta = h.get("metadata") or {}
            if meta.get("kind") != RAG_KIND:
                continue
            if owner is not None and (meta.get("owner") or None) not in (owner, None):
                continue
            out.append({
                "kb_id": meta.get("kb_id"),
                "filename": meta.get("filename"),
                "text": h.get("document") or h.get("text") or "",
                "score": h.get("score"),
            })
            if len(out) >= k:
                break
        return out
    except Exception as e:
        logger.debug("knowledge semantic_search failed: %s", e)
        return []


def get(owner: Optional[str], kb_id: str) -> Optional[dict]:
    """Full record incl. the FULL extracted text + upload_id (owner-scoped).
    Backs the "open the actual file + see exactly what was extracted from it"
    verification path — the user never has to trust RAG/LLM to inspect a source."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return None
        d = _to_dict(kf)
        d["text"] = kf.text or ""  # full text (search results only carry an excerpt)
        return d
    finally:
        db.close()


def set_tags(owner: Optional[str], kb_id: str, tags) -> Optional[dict]:
    """Replace a file's user tags (owner-scoped). Returns the updated row or None."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return None
        kf.tags = _norm_tags(tags)
        db.commit()
        db.refresh(kf)
        return _to_dict(kf)
    finally:
        db.close()


def set_favorite(owner: Optional[str], kb_id: str, favorite: bool) -> Optional[dict]:
    """Star/unstar a file (owner-scoped). Returns the updated row or None."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return None
        kf.favorite = bool(favorite)
        db.commit()
        db.refresh(kf)
        return _to_dict(kf)
    finally:
        db.close()


def rename(owner: Optional[str], kb_id: str, new_filename: str) -> Optional[dict]:
    """Rename a file's display filename (owner-scoped). The stored bytes stay put
    (their path is keyed by id, not name), so this is a cheap field update — and
    because Books is a view over these rows, a book rename shows up in both
    Books AND Knowledge. Returns the updated row or None."""
    from core.database import SessionLocal, KnowledgeFile

    new_filename = (new_filename or "").strip()
    if not new_filename:
        return None
    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return None
        kf.filename = new_filename[:255]
        db.commit()
        db.refresh(kf)
        return _to_dict(kf)
    finally:
        db.close()


# Editable-content extensions: keep this LIGHTWEIGHT (a correction textarea), not
# a second document editor — heavy authoring belongs in Library/Documents.
def update_text(owner: Optional[str], kb_id: str, new_text: Optional[str], *,
                filename: Optional[str] = None) -> Optional[dict]:
    """Edit a KB file's content/searchable text (owner-scoped) and RE-INDEX RAG.

    For text-based files (.md/.txt/…) this also rewrites the stored bytes so the
    opened file matches what's searched; for binaries (pdf/image/…) it updates
    only the extracted/searchable text (the user's correction of OCR/extraction),
    leaving the original file + its hash intact. Returns the updated row or None."""
    from core.database import SessionLocal, KnowledgeFile

    if new_text is None and not filename:
        return None
    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return None
        if filename:
            kf.filename = str(filename).strip()[:255] or kf.filename
        if new_text is not None:
            ext = os.path.splitext(kf.filename or kf.path or "")[1].lower()
            if ext in _TEXT_EXTS and kf.path:
                abspath = os.path.join(_kb_files_dir(), kf.path)
                try:
                    os.makedirs(os.path.dirname(abspath), exist_ok=True)
                    with open(abspath, "w", encoding="utf-8") as fh:
                        fh.write(new_text)
                    kf.sha256 = _sha256_file(abspath)
                    kf.file_size = os.path.getsize(abspath)
                except OSError as e:
                    logger.warning("knowledge: could not rewrite bytes for %s: %s", kb_id, e)
            kf.text = new_text
            _remove_from_rag(kb_id)            # drop stale chunks first…
            kf.indexed = _index_in_rag(kf)     # …then index the new text
        db.commit()
        db.refresh(kf)
        return _to_dict(kf)
    finally:
        db.close()


def append_text(owner: Optional[str], kb_id: str, extra_text: str) -> Optional[dict]:
    """Append text to a KB file's content (owner-scoped) and re-index. Convenience
    over update_text for Iris's "add this to my notes on X" flows."""
    extra = (extra_text or "").strip()
    rec = get(owner, kb_id)
    if not rec:
        return None
    if not extra:
        return rec
    combined = ((rec.get("text") or "").rstrip() + "\n\n" + extra).strip()
    return update_text(owner, kb_id, combined)


def generate_ai_tags(owner: Optional[str], kb_id: str) -> Optional[dict]:
    """Generate + store AI topical tags for a file from its extracted text, via
    the Utility model. Best-effort: leaves ai_tags unchanged when nothing usable
    comes back. Safe to run as a background task (does its own LLM call off the
    request path). Returns the (possibly-updated) row."""
    rec = get(owner, kb_id)
    if not rec:
        return None
    tags = _generate_tags_via_llm(rec.get("text") or "", owner)
    if not tags:
        return rec
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return None
        kf.ai_tags = _norm_tags(tags)
        db.commit()
        db.refresh(kf)
        return _to_dict(kf)
    finally:
        db.close()


def list_tags(owner: Optional[str]) -> List[str]:
    """All distinct tags (user + ai) for this owner, for UI tag facets."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        q = db.query(KnowledgeFile)
        if owner is not None:
            q = q.filter(KnowledgeFile.owner == owner)
        seen = {}
        for kf in q.all():
            for t in _split_tags(kf.tags) + _split_tags(kf.ai_tags):
                seen.setdefault(t.lower(), t)
        return sorted(seen.values(), key=str.lower)
    finally:
        db.close()


def delete(owner: Optional[str], kb_id: str) -> bool:
    """Delete a knowledge file record (owner-scoped) + its KB-owned bytes."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return False
        rel = kf.path
        db.delete(kf)
        db.commit()
        _remove_from_rag(kb_id)  # drop the file's vectors so deleted text can't resurface in recall
        if rel:
            try:
                os.remove(os.path.join(_kb_files_dir(), rel))
            except OSError:
                pass
        return True
    finally:
        db.close()


def count(owner: Optional[str]) -> int:
    """Number of knowledge files for an owner."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        q = db.query(KnowledgeFile)
        if owner is not None:
            q = q.filter(KnowledgeFile.owner == owner)
        return q.count()
    finally:
        db.close()
