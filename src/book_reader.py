"""Vault-backed Books / E-Reader service for Iris.

Books live in the user's Iris vault and progress is mirrored into Markdown so
the assistant can retrieve reading state through the normal vault index.
"""

from __future__ import annotations

import html
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from src import epub_reader, iris_vault

SUPPORTED_BOOK_EXTENSIONS = {".epub", ".pdf"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_note_name(text: str, fallback: str = "book") -> str:
    raw = re.sub(r"[^A-Za-z0-9_.@() -]+", "_", text or "").strip(" ._-")
    return (raw or fallback)[:120]


def _book_id(owner: str | None, rel_path: str) -> str:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = iris_vault._safe_rel_path(rel_path)
    return hashlib.sha256(f"{owner_key}/{safe_path}".encode()).hexdigest()


def _progress_path(book_id: str) -> str:
    return f"50_State/book_progress/{book_id}.json"


def _metadata_path(book_id: str) -> str:
    return f"50_State/book_metadata/{book_id}.json"


def _reading_note_path(title: str) -> str:
    return f"30_Reading/{_safe_note_name(title, 'book')}.md"


def _safe_book_path(rel_path: str) -> str:
    safe = iris_vault._safe_rel_path(rel_path)
    if Path(safe).suffix.lower() not in SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(400, "Vault file is not a supported book")
    return safe


def _clean_book_title(title: str) -> str:
    clean = re.sub(r"\s+", " ", title or "").strip()
    if not clean:
        raise HTTPException(400, "Title is required")
    return clean[:200]


def get_metadata(owner: str | None, rel_path: str, *, missing_ok: bool = False) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = _safe_book_path(rel_path)
    book_id = _book_id(owner_key, safe_path)
    try:
        row = iris_vault.read_file(owner_key, _metadata_path(book_id))
        data = json.loads(row.get("content") or "{}")
        if isinstance(data, dict):
            return data
    except Exception:
        if not missing_ok:
            raise
    return {"book_id": book_id, "path": safe_path, "title": ""}


def _apply_metadata(owner: str | None, safe_path: str, book: dict) -> dict:
    data = get_metadata(owner, safe_path, missing_ok=True)
    title = (data.get("title") or "").strip()
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
    fetch_limit = max(10, min(int(limit or 50) * 4, 100))
    needle = (query or "").strip().lower()
    rows = iris_vault.search(owner, query, fetch_limit)
    if needle:
        seen = {row.get("path") for row in rows}
        for row in iris_vault.search(owner, "", 100):
            if row.get("path") not in seen:
                rows.append(row)
                seen.add(row.get("path"))
    books: list[dict] = []
    for row in rows:
        ext = Path(row.get("path") or "").suffix.lower()
        if ext not in SUPPORTED_BOOK_EXTENSIONS:
            continue
        item = dict(row)
        item["kind"] = ext.lstrip(".")
        file_meta = _book_metadata_from_file(owner, item["path"])
        if file_meta.get("title"):
            item["title"] = file_meta["title"]
        if file_meta.get("author"):
            item["author"] = file_meta["author"]
        if file_meta.get("chapter_count") is not None:
            item["chapter_count"] = file_meta.get("chapter_count")
        item = _apply_metadata(owner, item["path"], item)
        try:
            item["progress"] = get_progress(owner, item["path"], missing_ok=True)
        except Exception:
            item["progress"] = None
        if needle:
            haystack = " ".join([
                item.get("title") or "",
                item.get("path") or "",
                item.get("excerpt") or "",
                (item.get("progress") or {}).get("title") or "",
                (item.get("progress") or {}).get("author") or "",
            ]).lower()
            if needle not in haystack:
                continue
        books.append(item)
        if len(books) >= int(limit or 50):
            break
    return books


def save_uploaded_book(
    owner: str | None,
    filename: str,
    content: bytes,
    *,
    mime: str = "",
    index_content: bool = True,
) -> dict:
    ext = Path(filename or "").suffix.lower()
    if ext not in SUPPORTED_BOOK_EXTENSIONS:
        raise HTTPException(400, "Upload must be an .epub or .pdf file")
    row = iris_vault.save_uploaded_file(
        owner,
        filename or f"book{ext}",
        content,
        rel_path=iris_vault.book_upload_rel_path(filename or f"book{ext}", mime),
        mime=mime,
        index_content=index_content,
    )
    return iris_vault.row_to_dict(row)


def index_book(owner: str | None, rel_path: str) -> dict:
    safe_path = _safe_book_path(rel_path)
    path = iris_vault.resolve_owner_file(owner, safe_path)
    row = iris_vault.index_file(owner, path, index_content=True)
    return iris_vault.row_to_dict(row)


def open_book(owner: str | None, rel_path: str) -> dict:
    safe_path = _safe_book_path(rel_path)
    ext = Path(safe_path).suffix.lower()
    if ext == ".epub":
        book = epub_reader.parse_epub_toc(owner, safe_path)
        book["kind"] = "epub"
        book["progress"] = get_progress(owner, safe_path, missing_ok=True)
        return _apply_metadata(owner, safe_path, book)
    if ext == ".pdf":
        return _apply_metadata(owner, safe_path, parse_pdf(owner, safe_path, include_pages=False))
    raise HTTPException(400, "Unsupported book type")


def pdf_file_path(owner: str | None, rel_path: str) -> Path:
    """Return the original PDF path for authenticated in-browser viewing."""
    safe_path = _safe_book_path(rel_path)
    if Path(safe_path).suffix.lower() != ".pdf":
        raise HTTPException(400, "Book is not a PDF")
    path = iris_vault.resolve_owner_file(owner, safe_path)
    if not path.is_file():
        raise HTTPException(404, "PDF not found")
    return path


def _metadata_text(value) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def parse_pdf(owner: str | None, rel_path: str, *, include_pages: bool = True) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = _safe_book_path(rel_path)
    path = iris_vault.resolve_owner_file(owner_key, safe_path)
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
        "id": _book_id(owner_key, safe_path),
        "kind": "pdf",
        "path": safe_path,
        "title": title,
        "author": author,
        "chapter_count": len(chapters),
        "chapters": chapters,
        "progress": get_progress(owner_key, safe_path, missing_ok=True),
    }


def read_book_chapter(owner: str | None, rel_path: str, chapter_index: int = 0) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = _safe_book_path(rel_path)
    ext = Path(safe_path).suffix.lower()
    idx = max(0, int(chapter_index or 0))
    if ext == ".epub":
        return epub_reader.read_epub_chapter(owner_key, safe_path, idx)
    if ext != ".pdf":
        raise HTTPException(400, "Unsupported book type")

    path = iris_vault.resolve_owner_file(owner_key, safe_path)
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
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = _safe_book_path(rel_path)
    book_id = _book_id(owner_key, safe_path)
    try:
        row = iris_vault.read_file(owner_key, _progress_path(book_id))
        data = json.loads(row.get("content") or "{}")
        if isinstance(data, dict):
            return data
    except Exception:
        if not missing_ok:
            raise
    return {
        "book_id": book_id,
        "path": safe_path,
        "chapter_index": 0,
        "scroll_percent": 0,
        "updated_at": None,
    }


def save_progress(
    owner: str | None,
    rel_path: str,
    *,
    chapter_index: int,
    scroll_percent: float = 0,
    chapter_title: str = "",
    title: str = "",
    author: str = "",
    kind: str = "",
) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = _safe_book_path(rel_path)
    book_id = _book_id(owner_key, safe_path)
    ext_kind = kind or Path(safe_path).suffix.lower().lstrip(".")
    location_label = "page" if ext_kind == "pdf" else "chapter"
    display_title = title or get_metadata(owner_key, safe_path, missing_ok=True).get("title") or Path(safe_path).stem
    progress = {
        "book_id": book_id,
        "kind": ext_kind,
        "path": safe_path,
        "title": display_title,
        "author": author or "",
        "chapter_index": max(0, int(chapter_index or 0)),
        "chapter_title": chapter_title or "",
        "scroll_percent": max(0, min(float(scroll_percent or 0), 100)),
        "updated_at": _utc_now_iso(),
    }
    iris_vault.write_text_file(owner_key, _progress_path(book_id), json.dumps(progress, indent=2))
    note = (
        f"# {progress['title']}\n\n"
        f"- Type: {ext_kind.upper()}\n"
        f"- Author: {progress['author'] or 'Unknown'}\n"
        f"- Vault path: `{safe_path}`\n"
        f"- Last read: {location_label} {progress['chapter_index'] + 1}"
        f"{' - ' + progress['chapter_title'] if progress['chapter_title'] else ''}\n"
        f"- Location progress: {progress['scroll_percent']:.1f}%\n"
        f"- Updated: {progress['updated_at']}\n\n"
        "This note is maintained by Iris's E-Reader so Iris can answer "
        "questions about reading status and recently read books.\n"
    )
    iris_vault.write_text_file(owner_key, _reading_note_path(progress["title"]), note)
    return progress


def save_title(owner: str | None, rel_path: str, title: str) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = _safe_book_path(rel_path)
    path = iris_vault.resolve_owner_file(owner_key, safe_path)
    if not path.is_file():
        raise HTTPException(404, "Book not found")
    clean_title = _clean_book_title(title)
    book_id = _book_id(owner_key, safe_path)
    kind = Path(safe_path).suffix.lower().lstrip(".")
    metadata = {
        "book_id": book_id,
        "path": safe_path,
        "kind": kind,
        "title": clean_title,
        "updated_at": _utc_now_iso(),
    }
    iris_vault.write_text_file(owner_key, _metadata_path(book_id), json.dumps(metadata, indent=2))
    try:
        iris_vault.set_index_title(owner_key, safe_path, clean_title)
    except Exception:
        pass

    progress = get_progress(owner_key, safe_path, missing_ok=True)
    progress["title"] = clean_title
    progress["kind"] = progress.get("kind") or kind
    iris_vault.write_text_file(owner_key, _progress_path(book_id), json.dumps(progress, indent=2))
    if progress.get("updated_at"):
        location_label = "page" if kind == "pdf" else "chapter"
        last_read = f"{location_label} {int(progress.get('chapter_index') or 0) + 1}"
        if progress.get("chapter_title"):
            last_read += f" - {progress['chapter_title']}"
    else:
        last_read = "Not started"
    note = (
        f"# {clean_title}\n\n"
        f"- Type: {kind.upper()}\n"
        f"- Vault path: `{safe_path}`\n"
        f"- Last read: {last_read}\n"
        f"- Updated: {metadata['updated_at']}\n\n"
        "This note is maintained by Iris's E-Reader so Iris can answer "
        "questions about reading status and book titles.\n"
    )
    iris_vault.write_text_file(owner_key, _reading_note_path(clean_title), note)
    return metadata


def read_book_location(owner: str | None, rel_path: str, chapter_index: int = 0) -> dict:
    book = open_book(owner, rel_path)
    chapters = book.get("chapters") or []
    if not chapters:
        return {"book": book, "chapter": None}
    idx = max(0, min(int(chapter_index or 0), len(chapters) - 1))
    return {"book": {k: v for k, v in book.items() if k != "chapters"}, "chapter": read_book_chapter(owner, rel_path, idx)}
