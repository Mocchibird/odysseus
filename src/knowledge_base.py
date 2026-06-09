"""
knowledge_base.py — native knowledge base.

Any uploaded file (pdf / image / md / docx / txt / …) is stored in the uploads
store, its text is extracted, recorded in the `knowledge_files` table, and
indexed into the RAG vector store — so Iris can recall it AND the user can
search / tag / browse it. This replaces the Obsidian-vault file index
(`iris_vault`) with a native, DB-backed system that needs no Obsidian sync or
iris-mcp.

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
import uuid
from typing import List, Optional

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
_PDF_EXTS = {".pdf"}
_OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".xls", ".epub"}
_TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".log",
              ".html", ".htm", ".rst", ".yaml", ".yml", ".tsv"}

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


def extract_text(file_path: str, filename: str = "", mime: str = "",
                 owner: Optional[str] = None) -> str:
    """Best-effort text extraction by file type, reusing the existing extractors.
    Images go through vision OCR/captioning so they're searchable too. Always
    returns a string ("" when nothing could be extracted — the file is still
    stored + taggable)."""
    ext = os.path.splitext(filename or file_path)[1].lower()
    mime = (mime or "").lower()
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
        kf = KnowledgeFile(
            id=uuid.uuid4().hex,
            owner=owner,
            upload_id=upload_id,
            filename=filename,
            mime=mime,
            file_size=size,
            sha256=sha,
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
    """Delete a knowledge file record (owner-scoped). Bytes in the uploads store
    are left as-is (shared/dedup-safe)."""
    from core.database import SessionLocal, KnowledgeFile

    db = SessionLocal()
    try:
        kf = db.query(KnowledgeFile).filter(KnowledgeFile.id == kb_id).first()
        if not kf or (owner is not None and kf.owner != owner):
            return False
        db.delete(kf)
        db.commit()
        return True
    finally:
        db.close()
