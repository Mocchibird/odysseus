"""
content_rag.py — shared RAG indexing for every content store.

Books, the Files store, authored Documents (and the legacy Knowledge base while
it is being retired) all index their extracted text into ONE ChromaDB
collection through this module, tagged with a per-source ``kind`` so a single
semantic search can span every store. The per-row id is carried in the
``kb_id`` metadata key (kept unchanged so ``delete_by_kb_id`` and the
content-hash dedup keep working across the rename).

Best-effort throughout: a RAG/ChromaDB outage returns False/0/[] rather than
raising, so a missing vector store never blocks ingestion or browsing.
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Every store's text lands in the one collection, discriminated by `kind`.
# "knowledge" is the legacy kind written by the Knowledge base (retired in a
# later phase); the per-domain stores write their own kind.
CONTENT_KINDS = ("knowledge", "book", "file", "document", "image")


def index_text(owner: Optional[str], source_id: str, text: str, kind: str,
               filename: str = "", source: str = "") -> bool:
    """Index a row's extracted text into the shared RAG collection (owner-scoped).
    Returns False (no raise) when RAG is unavailable, so the row can be
    re-indexed later."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        from src.rag_singleton import get_rag_manager
        rag = get_rag_manager()
        if not rag:
            return False
        meta = {
            "owner": owner or "",
            "kind": kind,
            "kb_id": source_id,
            "filename": filename or "",
            "source": source or "",
        }
        return bool(rag.add_document(text, meta))
    except Exception as e:
        logger.debug("content_rag: index failed for %s (%s): %s", source_id, kind, e)
        return False


def deindex(source_id: str) -> int:
    """Drop a row's chunks from the shared RAG collection (by source id). Call
    before re-indexing an edit and on delete so removed text never resurfaces in
    recall. Best-effort — returns 0 when RAG is unavailable."""
    try:
        from src.rag_singleton import get_rag_manager
        rag = get_rag_manager()
        if not rag:
            return 0
        fn = getattr(rag, "delete_by_kb_id", None)
        return int(fn(source_id)) if fn else 0
    except Exception as e:
        logger.debug("content_rag: deindex failed for %s: %s", source_id, e)
        return 0


def semantic_search(owner: Optional[str], q: str, k: int = 5,
                    kinds: Optional[List[str]] = None) -> list:
    """Vector recall across the shared collection (owner-scoped). ``kinds``
    restricts to a subset of content kinds; None spans every store. Returns
    [] gracefully when RAG is unavailable."""
    q = (q or "").strip()
    if not q:
        return []
    allow = set(kinds) if kinds else set(CONTENT_KINDS)
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
            if meta.get("kind") not in allow:
                continue
            if owner is not None and (meta.get("owner") or None) not in (owner, None):
                continue
            out.append({
                "kb_id": meta.get("kb_id"),
                "filename": meta.get("filename"),
                "kind": meta.get("kind"),
                "text": h.get("document") or h.get("text") or "",
                "score": h.get("score"),
            })
            if len(out) >= k:
                break
        return out
    except Exception as e:
        logger.debug("content_rag semantic_search failed: %s", e)
        return []
