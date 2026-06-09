"""Small EPUB reader service backed by the native Books store.

EPUB files live in the Books store (src/book_store.py); reading progress lives in
the database. This module only parses EPUB structure (TOC, chapters, cover).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from fastapi import HTTPException

from src import book_store

_NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


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
    owner_key = book_store.owner_slug(owner)
    safe_path = book_store.safe_rel_path(rel_path)
    path = book_store.resolve_book_file(owner_key, safe_path)
    if path.suffix.lower() != ".epub":
        raise HTTPException(400, "File is not an EPUB")
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
    owner_key = book_store.owner_slug(owner)
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
        "id": book_store.book_id(owner_key, safe_path),
        "path": safe_path,
        "title": title,
        "author": author,
        "chapter_count": len(chapters),
        "chapters": chapters,
        "progress": book_store.get_progress(owner_key, safe_path, missing_ok=True),
    }


_COVER_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}


def extract_cover(owner: str | None, rel_path: str) -> tuple[bytes, str] | None:
    """Return (image_bytes, content_type) for the EPUB's cover image, or None.

    Tries, in order: the OPF ``<meta name="cover">`` pointer, a manifest item
    with ``properties="cover-image"``, an image whose href mentions "cover", and
    finally the first image in the manifest."""
    owner_key = book_store.owner_slug(owner)
    try:
        path, _safe, opf_dir, opf = _epub_package(owner_key, rel_path)
    except Exception:
        return None

    manifest: dict[str, dict] = {}
    for item in opf.findall(".//opf:manifest/opf:item", _NS):
        iid = item.attrib.get("id", "")
        href = item.attrib.get("href", "")
        if iid and href:
            manifest[iid] = {
                "href": _join_epub_path(opf_dir, href),
                "media_type": item.attrib.get("media-type", ""),
                "properties": item.attrib.get("properties", ""),
            }

    cover_href = None
    metadata = opf.find("opf:metadata", _NS)
    if metadata is not None:
        for meta in metadata.findall("opf:meta", _NS):
            if (meta.attrib.get("name") or "").lower() == "cover":
                cid = meta.attrib.get("content", "")
                if cid in manifest:
                    cover_href = manifest[cid]["href"]
                break
    if not cover_href:
        for it in manifest.values():
            if "cover-image" in (it.get("properties") or "").split():
                cover_href = it["href"]
                break
    if not cover_href:
        for it in manifest.values():
            if (it.get("media_type") or "").startswith("image/") and "cover" in it["href"].lower():
                cover_href = it["href"]
                break
    if not cover_href:
        for it in manifest.values():
            if (it.get("media_type") or "").startswith("image/"):
                cover_href = it["href"]
                break
    if not cover_href:
        return None

    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read(cover_href)
    except Exception:
        return None
    if not data:
        return None
    content_type = _COVER_CONTENT_TYPES.get(Path(cover_href).suffix.lower(), "image/jpeg")
    return data, content_type


def read_epub_chapter(owner: str | None, rel_path: str, chapter_index: int = 0) -> dict:
    owner_key = book_store.owner_slug(owner)
    toc = parse_epub_toc(owner_key, rel_path)
    chapters = toc.get("chapters") or []
    if not chapters:
        raise HTTPException(404, "EPUB has no readable chapters")
    idx = max(0, min(int(chapter_index or 0), len(chapters) - 1))
    chapter = chapters[idx]
    path = book_store.resolve_book_file(owner_key, toc["path"])
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


def parse_epub(owner: str | None, rel_path: str) -> dict:
    book = parse_epub_toc(owner, rel_path)
    book["chapters"] = [
        read_epub_chapter(owner, book["path"], chapter.get("index", idx))
        for idx, chapter in enumerate(book.get("chapters") or [])
    ]
    return book
