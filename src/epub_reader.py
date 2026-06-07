"""Small EPUB reader service backed by the Iris vault.

The reader intentionally keeps the vault as source of truth: EPUB files live in
the user's Obsidian folder and reading progress is mirrored into a Markdown note
so Iris can retrieve and talk about what the user has read.
"""

from __future__ import annotations

import json
import hashlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from fastapi import HTTPException

from src import iris_vault

_NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_note_name(text: str, fallback: str = "book") -> str:
    raw = re.sub(r"[^A-Za-z0-9_.@() -]+", "_", text or "").strip(" ._-")
    return (raw or fallback)[:120]


def _zip_read_text(zf: zipfile.ZipFile, name: str) -> str:
    try:
        return zf.read(name).decode("utf-8", errors="replace")
    except KeyError:
        raise HTTPException(422, f"EPUB item not found: {name}")


def _join_epub_path(base_dir: str, href: str) -> str:
    href = unquote((href or "").split("#", 1)[0])
    path = (Path(base_dir) / href).as_posix() if base_dir else href
    parts: list[str] = []
    for part in path.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _text_or_empty(node) -> str:
    return (node.text or "").strip() if node is not None else ""


def _chapter_html(raw_html: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "svg", "math", "iframe", "object"]):
        tag.decompose()
    body = soup.body or soup
    title = ""
    for sel in ("h1", "h2", "h3", "title"):
        node = body.find(sel) if hasattr(body, "find") else None
        if node and node.get_text(" ", strip=True):
            title = node.get_text(" ", strip=True)
            break
    html = str(body)
    if body.name == "body":
        html = "".join(str(child) for child in body.children)
    try:
        import nh3
        html = nh3.clean(
            html,
            tags={
                "p", "br", "hr", "blockquote", "pre", "code",
                "h1", "h2", "h3", "h4", "h5", "h6",
                "strong", "b", "em", "i", "u", "small", "sup", "sub",
                "ul", "ol", "li", "dl", "dt", "dd",
                "table", "thead", "tbody", "tr", "th", "td",
                "span", "div",
            },
            attributes={"*": {"class"}},
            strip_comments=True,
        )
    except Exception:
        html = BeautifulSoup(html, "html.parser").get_text("\n\n", strip=True)
    return title, html


def _plain_text(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)


def _epub_package(owner: str | None, rel_path: str) -> tuple[Path, str, str, ET.Element]:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = iris_vault._safe_rel_path(rel_path)
    path = iris_vault.resolve_owner_file(owner_key, safe_path)
    if path.suffix.lower() != ".epub":
        raise HTTPException(400, "Vault file is not an EPUB")
    if not path.is_file():
        raise HTTPException(404, "EPUB not found")
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                container_xml = zf.read("META-INF/container.xml")
            except KeyError:
                raise HTTPException(422, "EPUB container.xml not found")
            root = ET.fromstring(container_xml)
            rootfile = root.find(".//container:rootfile", _NS)
            opf_path = rootfile.attrib.get("full-path", "") if rootfile is not None else ""
            if not opf_path:
                raise HTTPException(422, "EPUB root package not found")
            opf = ET.fromstring(zf.read(opf_path))
    except zipfile.BadZipFile:
        raise HTTPException(422, "Invalid EPUB zip file")
    opf_dir = str(Path(opf_path).parent)
    if opf_dir == ".":
        opf_dir = ""
    return path, safe_path, opf_dir, opf


def _epub_toc_titles(zf: zipfile.ZipFile, manifest: dict, opf_dir: str, opf: ET.Element) -> dict[str, str]:
    titles: dict[str, str] = {}

    nav_item = next((item for item in manifest.values() if "nav" in (item.get("properties") or "").split()), None)
    if nav_item:
        try:
            soup = BeautifulSoup(_zip_read_text(zf, nav_item["href"]), "html.parser")
            nav = soup.find("nav", attrs={"epub:type": re.compile(r"\btoc\b")}) or soup.find("nav", attrs={"type": "toc"}) or soup.find("nav")
            for link in (nav or soup).find_all("a", href=True):
                href = _join_epub_path(str(Path(nav_item["href"]).parent), link.get("href") or "")
                title = link.get_text(" ", strip=True)
                if href and title:
                    titles[href] = title
                    titles[href.split("#", 1)[0]] = title
        except Exception:
            pass

    spine = opf.find(".//opf:spine", _NS)
    toc_id = spine.attrib.get("toc", "") if spine is not None else ""
    ncx_item = manifest.get(toc_id) or next((item for item in manifest.values() if item.get("media_type") == "application/x-dtbncx+xml"), None)
    if ncx_item:
        try:
            root = ET.fromstring(zf.read(ncx_item["href"]))
            ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
            base = str(Path(ncx_item["href"]).parent)
            if base == ".":
                base = ""
            for point in root.findall(".//ncx:navPoint", ns):
                text = _text_or_empty(point.find(".//ncx:navLabel/ncx:text", ns))
                content = point.find(".//ncx:content", ns)
                src = content.attrib.get("src", "") if content is not None else ""
                href = _join_epub_path(base, src)
                if href and text:
                    titles[href] = text
                    titles[href.split("#", 1)[0]] = text
        except Exception:
            pass

    return titles


def parse_epub_toc(owner: str | None, rel_path: str) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    path, safe_path, opf_dir, opf = _epub_package(owner_key, rel_path)
    metadata = opf.find("opf:metadata", _NS)
    title = _text_or_empty(metadata.find("dc:title", _NS) if metadata is not None else None) or path.stem
    author = _text_or_empty(metadata.find("dc:creator", _NS) if metadata is not None else None)

    manifest = {}
    for item in opf.findall(".//opf:manifest/opf:item", _NS):
        item_id = item.attrib.get("id", "")
        href = item.attrib.get("href", "")
        if item_id and href:
            manifest[item_id] = {
                "href": _join_epub_path(opf_dir, href),
                "media_type": item.attrib.get("media-type", ""),
                "properties": item.attrib.get("properties", ""),
            }

    with zipfile.ZipFile(path) as zf:
        toc_titles = _epub_toc_titles(zf, manifest, opf_dir, opf)

    chapters = []
    for itemref in opf.findall(".//opf:spine/opf:itemref", _NS):
        item = manifest.get(itemref.attrib.get("idref", ""))
        if not item:
            continue
        if item["media_type"] not in {"application/xhtml+xml", "text/html"}:
            continue
        href = item["href"]
        chapters.append({
            "index": len(chapters),
            "title": toc_titles.get(href) or toc_titles.get(href.split("#", 1)[0]) or f"Chapter {len(chapters) + 1}",
            "href": href,
            "word_count": None,
        })

    return {
        "id": _book_id(owner_key, safe_path),
        "path": safe_path,
        "title": title,
        "author": author,
        "chapter_count": len(chapters),
        "chapters": chapters,
        "progress": get_progress(owner_key, safe_path, missing_ok=True),
    }


def read_epub_chapter(owner: str | None, rel_path: str, chapter_index: int = 0) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    toc = parse_epub_toc(owner_key, rel_path)
    chapters = toc.get("chapters") or []
    if not chapters:
        raise HTTPException(404, "EPUB has no readable chapters")
    idx = max(0, min(int(chapter_index or 0), len(chapters) - 1))
    chapter = chapters[idx]
    path = iris_vault.resolve_owner_file(owner_key, toc["path"])
    with zipfile.ZipFile(path) as zf:
        raw = _zip_read_text(zf, chapter["href"])
    chapter_title, html = _chapter_html(raw)
    text = _plain_text(html)
    return {
        **chapter,
        "title": chapter_title or chapter.get("title") or f"Chapter {idx + 1}",
        "html": html,
        "text_excerpt": text[:1200],
        "word_count": len(re.findall(r"\w+", text)),
    }


def _progress_path(book_id: str) -> str:
    return f"50_State/book_progress/{book_id}.json"


def _reading_note_path(title: str) -> str:
    return f"30_Reading/{_safe_note_name(title, 'book')}.md"


def _book_id(owner: str | None, rel_path: str) -> str:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = iris_vault._safe_rel_path(rel_path)
    return hashlib.sha256(f"{owner_key}/{safe_path}".encode()).hexdigest()


def parse_epub(owner: str | None, rel_path: str) -> dict:
    book = parse_epub_toc(owner, rel_path)
    book["chapters"] = [
        read_epub_chapter(owner, book["path"], chapter.get("index", idx))
        for idx, chapter in enumerate(book.get("chapters") or [])
    ]
    return book


def get_progress(owner: str | None, rel_path: str, *, missing_ok: bool = False) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    book_id = _book_id(owner_key, rel_path)
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
        "path": iris_vault._safe_rel_path(rel_path),
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
) -> dict:
    owner_key = iris_vault.owner_folder_name(owner)
    safe_path = iris_vault._safe_rel_path(rel_path)
    book_id = _book_id(owner_key, safe_path)
    progress = {
        "book_id": book_id,
        "path": safe_path,
        "title": title or Path(safe_path).stem,
        "author": author or "",
        "chapter_index": max(0, int(chapter_index or 0)),
        "chapter_title": chapter_title or "",
        "scroll_percent": max(0, min(float(scroll_percent or 0), 100)),
        "updated_at": _utc_now_iso(),
    }
    iris_vault.write_text_file(owner_key, _progress_path(book_id), json.dumps(progress, indent=2))
    note = (
        f"# {progress['title']}\n\n"
        f"- Author: {progress['author'] or 'Unknown'}\n"
        f"- Vault path: `{safe_path}`\n"
        f"- Last read: chapter {progress['chapter_index'] + 1}"
        f"{' - ' + progress['chapter_title'] if progress['chapter_title'] else ''}\n"
        f"- Chapter progress: {progress['scroll_percent']:.1f}%\n"
        f"- Updated: {progress['updated_at']}\n\n"
        "This note is maintained by Iris's E-Reader so Iris can answer "
        "questions about reading status and recently read books.\n"
    )
    iris_vault.write_text_file(owner_key, _reading_note_path(progress["title"]), note)
    return progress
