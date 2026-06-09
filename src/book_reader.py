"""Books / E-Reader service for Iris.

Books live in the native Books store (src/book_store.py): bytes under
DATA_DIR/books, with reading progress / titles / annotations in the database and
full text indexed into the shared RAG store so Iris can search inside books.
No Obsidian vault.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import HTTPException

from src import epub_reader, book_store

SUPPORTED_BOOK_EXTENSIONS = book_store.SUPPORTED_BOOK_EXTENSIONS


def _safe_book_path(rel_path: str) -> str:
    safe = book_store.safe_rel_path(rel_path)
    if Path(safe).suffix.lower() not in SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(400, "File is not a supported book")
    return safe


def get_metadata(owner: str | None, rel_path: str, *, missing_ok: bool = False) -> dict:
    safe_path = _safe_book_path(rel_path)
    row = book_store.get_book(owner, safe_path)
    title = (row or {}).get("custom_title") or ""
    return {"book_id": book_store.book_id(owner, safe_path), "path": safe_path, "title": title}


def _apply_metadata(owner: str | None, safe_path: str, book: dict) -> dict:
    title = get_metadata(owner, safe_path, missing_ok=True).get("title") or ""
    if title:
        book["title"] = title
        book["custom_title"] = title
    return book


def _book_metadata_from_file(owner: str | None, rel_path: str) -> dict:
    ext = Path(rel_path or "").suffix.lower()
    try:
        if ext == ".epub":
            return epub_reader.parse_epub_toc(owner, rel_path)
        if ext == ".pdf":
            return parse_pdf(owner, rel_path, include_pages=False)
    except Exception:
        return {}
    return {}


def list_books(owner: str | None, query: str = "", limit: int = 50) -> list[dict]:
    cap = max(1, int(limit or 50))
    needle = (query or "").strip().lower()
    candidates = book_store.query_books(owner, "", 200 if needle else cap)
    books: list[dict] = []
    for item in candidates:
        ext = Path(item.get("path") or "").suffix.lower()
        if ext not in SUPPORTED_BOOK_EXTENSIONS:
            continue
        custom = item.get("custom_title") or ""
        file_meta = _book_metadata_from_file(owner, item["path"])
        if not custom and file_meta.get("title"):
            item["title"] = file_meta["title"]
        if file_meta.get("author"):
            item["author"] = file_meta["author"]
        if file_meta.get("chapter_count") is not None:
            item["chapter_count"] = file_meta.get("chapter_count")
        try:
            item["progress"] = book_store.get_progress(owner, item["path"], missing_ok=True)
        except Exception:
            item["progress"] = None
        if needle:
            haystack = " ".join([
                item.get("title") or "",
                item.get("path") or "",
                item.get("excerpt") or "",
                item.get("author") or "",
            ]).lower()
            if needle not in haystack:
                continue
        books.append(item)
        if len(books) >= cap:
            break
    return books


def save_uploaded_book(owner: str | None, filename: str, content: bytes, *, mime: str = "",
                       index_content: bool = True) -> dict:
    ext = Path(filename or "").suffix.lower()
    if ext not in SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(400, "Upload must be an .epub or .pdf file")
    return book_store.upsert_book(owner, filename or f"book{ext}", content, mime=mime)


def index_book(owner: str | None, rel_path: str) -> dict:
    return book_store.index_book(owner, _safe_book_path(rel_path))


def open_book(owner: str | None, rel_path: str) -> dict:
    safe_path = _safe_book_path(rel_path)
    ext = Path(safe_path).suffix.lower()
    # Register the book in the index on open (lightweight, no content parse) so a
    # book opened by path also appears in the Books list.
    try:
        book_store.register_book(owner, safe_path)
    except Exception:
        pass
    if ext == ".epub":
        book = epub_reader.parse_epub_toc(owner, safe_path)
        book["kind"] = "epub"
        book["progress"] = book_store.get_progress(owner, safe_path, missing_ok=True)
        return _apply_metadata(owner, safe_path, book)
    if ext == ".pdf":
        return _apply_metadata(owner, safe_path, parse_pdf(owner, safe_path, include_pages=False))
    raise HTTPException(400, "Unsupported book type")


def pdf_file_path(owner: str | None, rel_path: str) -> Path:
    """Return the original PDF path for authenticated in-browser viewing."""
    safe_path = _safe_book_path(rel_path)
    if Path(safe_path).suffix.lower() != ".pdf":
        raise HTTPException(400, "Book is not a PDF")
    path = book_store.resolve_book_file(owner, safe_path)
    if not path.is_file():
        raise HTTPException(404, "PDF not found")
    return path


def _metadata_text(value) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def parse_pdf(owner: str | None, rel_path: str, *, include_pages: bool = True) -> dict:
    safe_path = _safe_book_path(rel_path)
    path = book_store.resolve_book_file(owner, safe_path)
    if not path.is_file():
        raise HTTPException(404, "PDF not found")

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
    except Exception as exc:
        raise HTTPException(422, f"Could not read PDF: {exc}")

    metadata = getattr(reader, "metadata", None) or {}
    title = _metadata_text(getattr(metadata, "title", None) or metadata.get("/Title")) or path.stem
    author = _metadata_text(getattr(metadata, "author", None) or metadata.get("/Author"))

    chapters = []
    for idx, page in enumerate(reader.pages):
        if not include_pages:
            chapters.append({
                "index": idx,
                "title": f"Page {idx + 1}",
                "href": f"page-{idx + 1}",
                "word_count": None,
            })
            continue
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        if paragraphs:
            page_html = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
        else:
            page_html = "<p class=\"books-empty-page\">No extractable text on this page.</p>"
        chapters.append({
            "index": idx,
            "title": f"Page {idx + 1}",
            "href": f"page-{idx + 1}",
            "html": page_html,
            "text_excerpt": text[:1200],
            "word_count": len(re.findall(r"\w+", text)),
        })

    return {
        "id": book_store.book_id(owner, safe_path),
        "kind": "pdf",
        "path": safe_path,
        "title": title,
        "author": author,
        "chapter_count": len(chapters),
        "chapters": chapters,
        "progress": book_store.get_progress(owner, safe_path, missing_ok=True),
    }


def read_book_chapter(owner: str | None, rel_path: str, chapter_index: int = 0) -> dict:
    safe_path = _safe_book_path(rel_path)
    ext = Path(safe_path).suffix.lower()
    idx = max(0, int(chapter_index or 0))
    if ext == ".epub":
        return epub_reader.read_epub_chapter(owner, safe_path, idx)
    if ext != ".pdf":
        raise HTTPException(400, "Unsupported book type")

    path = book_store.resolve_book_file(owner, safe_path)
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
    if paragraphs:
        page_html = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    else:
        page_html = "<p class=\"books-empty-page\">No extractable text on this page.</p>"
    return {
        "index": idx,
        "title": f"Page {idx + 1}",
        "href": f"page-{idx + 1}",
        "html": page_html,
        "text_excerpt": text[:1200],
        "word_count": len(re.findall(r"\w+", text)),
    }


def get_progress(owner: str | None, rel_path: str, *, missing_ok: bool = False) -> dict:
    return book_store.get_progress(owner, _safe_book_path(rel_path), missing_ok=missing_ok)


def save_progress(owner: str | None, rel_path: str, *, chapter_index: int, scroll_percent: float = 0,
                  chapter_title: str = "", title: str = "", author: str = "", kind: str = "") -> dict:
    return book_store.save_progress(
        owner, _safe_book_path(rel_path),
        chapter_index=chapter_index, scroll_percent=scroll_percent,
        chapter_title=chapter_title, title=title, author=author, kind=kind,
    )


def save_title(owner: str | None, rel_path: str, title: str) -> dict:
    return book_store.set_title(owner, _safe_book_path(rel_path), title)


def read_book_location(owner: str | None, rel_path: str, chapter_index: int = 0) -> dict:
    book = open_book(owner, rel_path)
    chapters = book.get("chapters") or []
    if not chapters:
        return {"book": book, "chapter": None}
    idx = max(0, min(int(chapter_index or 0), len(chapters) - 1))
    return {"book": {k: v for k, v in book.items() if k != "chapters"}, "chapter": read_book_chapter(owner, rel_path, idx)}


# --------------------------------------------------------------------------- #
# Full-text search within a single book                                       #
# --------------------------------------------------------------------------- #

def search_book_text(owner: str | None, rel_path: str, query: str, *, max_results: int = 120,
                     radius: int = 70) -> dict:
    """Search the full text of one book and return located matches with snippets,
    so the reader can jump straight to the chapter/page."""
    safe_path = _safe_book_path(rel_path)
    ext = Path(safe_path).suffix.lower()
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
                    "chapter_index": idx,
                    "chapter_title": title,
                    "snippet": ("…" if s > 0 else "") + snippet + ("…" if e < len(text) else ""),
                    "match": text[pos:pos + len(needle)],
                })
            start = pos + len(needle)

    if ext == ".epub":
        toc = epub_reader.parse_epub_toc(owner, safe_path)
        for ch in (toc.get("chapters") or []):
            if len(matches) >= max_results:
                break
            try:
                chapter = epub_reader.read_epub_chapter(owner, safe_path, ch.get("index", 0))
            except Exception:
                continue
            text = epub_reader._plain_text(chapter.get("html") or "")
            _scan(int(ch.get("index", 0)), chapter.get("title") or ch.get("title") or "", text)
    elif ext == ".pdf":
        path = book_store.resolve_book_file(owner, safe_path)
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

def get_cover(owner: str | None, rel_path: str) -> tuple[bytes, str] | None:
    """Return (image_bytes, content_type) for a book cover, or None. EPUB covers
    are extracted from the archive; PDFs return None (the UI falls back to an icon)."""
    safe_path = _safe_book_path(rel_path)
    if Path(safe_path).suffix.lower() == ".epub":
        return epub_reader.extract_cover(owner, safe_path)
    return None


# --------------------------------------------------------------------------- #
# Bookmarks & highlights                                                       #
# --------------------------------------------------------------------------- #

def list_annotations(owner: str | None, rel_path: str) -> dict:
    return book_store.list_annotations(owner, _safe_book_path(rel_path))


def add_annotation(owner: str | None, rel_path: str, *, type: str = "bookmark", chapter_index: int = 0,
                   chapter_title: str = "", text: str = "", note: str = "", color: str = "",
                   scroll_percent: float = 0) -> dict:
    return book_store.add_annotation(
        owner, _safe_book_path(rel_path),
        type=type, chapter_index=chapter_index, chapter_title=chapter_title,
        text=text, note=note, color=color, scroll_percent=scroll_percent,
    )


def delete_annotation(owner: str | None, rel_path: str, ann_id: str) -> bool:
    return book_store.delete_annotation(owner, _safe_book_path(rel_path), ann_id)


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
