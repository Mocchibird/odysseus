"""Books / E-Reader service for Iris.

A book is a PDF/EPUB in the Knowledge base; this is the reading layer over it.
Everything is addressed by the knowledge file id (carried through the API as
`path` for the existing frontend). Storage + text extraction + search live in
the Knowledge base; reading progress / annotations live in book_store.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import HTTPException

from src import epub_reader, book_store

SUPPORTED_BOOK_EXTENSIONS = book_store.SUPPORTED_BOOK_EXTENSIONS


def _require_book(owner: str | None, kb_id: str) -> dict:
    b = book_store.get_book(owner, kb_id)
    if not b:
        raise HTTPException(404, "Book not found")
    return b


def list_books(owner: str | None, query: str = "", limit: int = 50) -> list[dict]:
    return book_store.list_books(owner, query, limit)


def save_uploaded_book(owner: str | None, filename: str, content: bytes, *, mime: str = "",
                       index_content: bool = True) -> dict:
    return book_store.add_book(owner, filename, content, mime=mime)


def open_book(owner: str | None, kb_id: str) -> dict:
    b = _require_book(owner, kb_id)
    if b["kind"] == "epub":
        book = epub_reader.parse_epub_toc(owner, kb_id)
        book["kind"] = "epub"
        return book
    if b["kind"] == "pdf":
        return parse_pdf(owner, kb_id, include_pages=False)
    raise HTTPException(400, "Unsupported book type")


def pdf_file_path(owner: str | None, kb_id: str) -> Path:
    """Return the original PDF path for authenticated in-browser viewing."""
    b = _require_book(owner, kb_id)
    if b["kind"] != "pdf":
        raise HTTPException(400, "Book is not a PDF")
    path = book_store.resolve_book_file(owner, kb_id)
    if not path.is_file():
        raise HTTPException(404, "PDF not found")
    return path


def _metadata_text(value) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def parse_pdf(owner: str | None, kb_id: str, *, include_pages: bool = True) -> dict:
    b = _require_book(owner, kb_id)
    path = book_store.resolve_book_file(owner, kb_id)
    if not path.is_file():
        raise HTTPException(404, "PDF not found")
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
    except Exception as exc:
        raise HTTPException(422, f"Could not read PDF: {exc}")

    metadata = getattr(reader, "metadata", None) or {}
    author = _metadata_text(getattr(metadata, "author", None) or metadata.get("/Author"))

    chapters = []
    for idx, page in enumerate(reader.pages):
        if not include_pages:
            chapters.append({"index": idx, "title": f"Page {idx + 1}", "href": f"page-{idx + 1}", "word_count": None})
            continue
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        page_html = ("".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
                     if paragraphs else "<p class=\"books-empty-page\">No extractable text on this page.</p>")
        chapters.append({
            "index": idx, "title": f"Page {idx + 1}", "href": f"page-{idx + 1}",
            "html": page_html, "text_excerpt": text[:1200], "word_count": len(re.findall(r"\w+", text)),
        })

    return {
        "id": kb_id, "kind": "pdf", "path": kb_id,
        "title": b["title"], "author": author,
        "chapter_count": len(chapters), "chapters": chapters,
        "progress": book_store.get_progress(owner, kb_id),
    }


def read_book_chapter(owner: str | None, kb_id: str, chapter_index: int = 0) -> dict:
    b = _require_book(owner, kb_id)
    idx = max(0, int(chapter_index or 0))
    if b["kind"] == "epub":
        return epub_reader.read_epub_chapter(owner, kb_id, idx)
    if b["kind"] != "pdf":
        raise HTTPException(400, "Unsupported book type")

    path = book_store.resolve_book_file(owner, kb_id)
    if not path.is_file():
        raise HTTPException(404, "PDF not found")
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
    except Exception as exc:
        raise HTTPException(422, f"Could not read PDF: {exc}")
    if not reader.pages:
        raise HTTPException(404, "PDF has no pages")
    idx = min(idx, len(reader.pages) - 1)
    try:
        text = reader.pages[idx].extract_text() or ""
    except Exception:
        text = ""
    text = text.strip()
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    page_html = ("".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
                 if paragraphs else "<p class=\"books-empty-page\">No extractable text on this page.</p>")
    return {
        "index": idx, "title": f"Page {idx + 1}", "href": f"page-{idx + 1}",
        "html": page_html, "text_excerpt": text[:1200], "word_count": len(re.findall(r"\w+", text)),
    }


def get_progress(owner: str | None, kb_id: str) -> dict:
    _require_book(owner, kb_id)  # owner-scope: 404 if the book isn't the caller's
    return book_store.get_progress(owner, kb_id)


def save_progress(owner: str | None, kb_id: str, *, chapter_index: int, scroll_percent: float = 0,
                  chapter_title: str = "", title: str = "", author: str = "", kind: str = "") -> dict:
    _require_book(owner, kb_id)  # owner-scope: 404 if the book isn't the caller's
    return book_store.save_progress(
        owner, kb_id, chapter_index=chapter_index, scroll_percent=scroll_percent,
        chapter_title=chapter_title, title=title, author=author, kind=kind,
    )


def save_title(owner: str | None, kb_id: str, title: str) -> dict:
    return book_store.set_title(owner, kb_id, title)


def set_favorite(owner: str | None, kb_id: str, favorite: bool) -> dict:
    return book_store.set_favorite(owner, kb_id, favorite)


def delete_book(owner: str | None, kb_id: str) -> bool:
    return book_store.delete_book(owner, kb_id)


def read_book_location(owner: str | None, kb_id: str, chapter_index: int = 0) -> dict:
    book = open_book(owner, kb_id)
    chapters = book.get("chapters") or []
    if not chapters:
        return {"book": book, "chapter": None}
    idx = max(0, min(int(chapter_index or 0), len(chapters) - 1))
    return {"book": {k: v for k, v in book.items() if k != "chapters"},
            "chapter": read_book_chapter(owner, kb_id, idx)}


# --------------------------------------------------------------------------- #
# Full-text search within a single book                                       #
# --------------------------------------------------------------------------- #

def search_book_text(owner: str | None, kb_id: str, query: str, *, max_results: int = 120,
                     radius: int = 70) -> dict:
    """Search the full text of one book; return located matches with snippets so
    the reader can jump straight to the chapter/page."""
    b = _require_book(owner, kb_id)
    q = (query or "").strip()
    if not q:
        return {"query": "", "matches": [], "total": 0, "truncated": False}
    needle = q.lower()
    matches: list[dict] = []
    total = 0

    def _scan(idx: int, title: str, text: str) -> None:
        nonlocal total
        if not text:
            return
        low = text.lower()
        start = 0
        while True:
            pos = low.find(needle, start)
            if pos < 0:
                break
            total += 1
            if len(matches) < max_results:
                s = max(0, pos - radius)
                e = min(len(text), pos + len(needle) + radius)
                snippet = re.sub(r"\s+", " ", text[s:e]).strip()
                matches.append({
                    "chapter_index": idx, "chapter_title": title,
                    "snippet": ("…" if s > 0 else "") + snippet + ("…" if e < len(text) else ""),
                    "match": text[pos:pos + len(needle)],
                })
            start = pos + len(needle)

    if b["kind"] == "epub":
        # Parse the TOC + open the zip ONCE, then read each chapter's html from
        # the already-open archive. The old loop called read_epub_chapter() per
        # chapter, which re-parsed the whole TOC (container.xml + OPF + nav/NCX)
        # and reopened the zip every time — O(M^2) full re-parses + M zip opens
        # + M DB queries for an M-chapter book. Now it's one parse + one open.
        import zipfile
        toc = epub_reader.parse_epub_toc(owner, kb_id)
        path = book_store.resolve_book_file(owner, kb_id)
        try:
            zf = zipfile.ZipFile(path)
        except Exception:
            zf = None
        if zf is not None:
            try:
                for ch in (toc.get("chapters") or []):
                    if len(matches) >= max_results:
                        break
                    try:
                        raw = epub_reader._zip_read_text(zf, ch["href"])
                        chapter_title, html = epub_reader._chapter_html(raw)
                        text = epub_reader._plain_text(html)
                    except Exception:
                        continue
                    _scan(int(ch.get("index", 0)), chapter_title or ch.get("title") or "", text)
            finally:
                zf.close()
    elif b["kind"] == "pdf":
        path = book_store.resolve_book_file(owner, kb_id)
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
        except Exception as exc:
            raise HTTPException(422, f"Could not read PDF: {exc}")
        for i, page in enumerate(reader.pages):
            if len(matches) >= max_results:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            _scan(i, f"Page {i + 1}", text)
    else:
        raise HTTPException(400, "Unsupported book type")

    return {"query": q, "matches": matches, "total": total, "truncated": total > len(matches)}


# --------------------------------------------------------------------------- #
# Cover thumbnails                                                            #
# --------------------------------------------------------------------------- #

def get_cover(owner: str | None, kb_id: str) -> tuple[bytes, str] | None:
    """(image_bytes, content_type) for an EPUB cover, or None (PDFs fall back to
    an icon in the UI)."""
    b = book_store.get_book(owner, kb_id)
    if b and b["kind"] == "epub":
        return epub_reader.extract_cover(owner, kb_id)
    return None


# --------------------------------------------------------------------------- #
# Bookmarks & highlights                                                       #
# --------------------------------------------------------------------------- #

def list_annotations(owner: str | None, kb_id: str) -> dict:
    _require_book(owner, kb_id)  # owner-scope: 404 if the book isn't the caller's
    return book_store.list_annotations(owner, kb_id)


def add_annotation(owner: str | None, kb_id: str, *, type: str = "bookmark", chapter_index: int = 0,
                   chapter_title: str = "", text: str = "", note: str = "", color: str = "",
                   scroll_percent: float = 0) -> dict:
    _require_book(owner, kb_id)  # owner-scope: 404 if the book isn't the caller's
    return book_store.add_annotation(
        owner, kb_id, type=type, chapter_index=chapter_index, chapter_title=chapter_title,
        text=text, note=note, color=color, scroll_percent=scroll_percent,
    )


def delete_annotation(owner: str | None, kb_id: str, ann_id: str) -> bool:
    _require_book(owner, kb_id)  # owner-scope: 404 if the book isn't the caller's
    return book_store.delete_annotation(owner, kb_id, ann_id)


# --------------------------------------------------------------------------- #
# AI "explain this passage"                                                   #
# --------------------------------------------------------------------------- #

async def explain_passage(owner: str | None, text: str, *, title: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "No text to explain")
    if len(text) > 4000:
        text = text[:4000]
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        url, model, headers = resolve_endpoint("default", owner=owner)
    if not url or not model:
        raise HTTPException(503, "No language model is configured")

    book_ctx = f' from the book "{title}"' if title else ""
    system = (
        "You are a thoughtful reading companion. Explain the passage clearly and "
        "concisely for a curious reader: define difficult words, unpack references "
        "and allusions, and give brief context. Keep it short — a few sentences, "
        "no preamble."
    )
    user = f"Explain this passage{book_ctx}:\n\n\"\"\"\n{text}\n\"\"\""
    raw = await llm_call_async(
        url=url, model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3, max_tokens=500, headers=headers, timeout=60,
    )
    try:
        from src.text_helpers import strip_think as _st
        out = _st(raw or "", prose=True, prompt_echo=False).strip()
    except Exception:
        out = (raw or "").strip()
    return {"explanation": out or "(no response)", "model": model}
