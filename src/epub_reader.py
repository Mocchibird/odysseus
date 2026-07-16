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


# Per-entry decompression cap. A legit EPUB chapter/cover is well under this;
# the cap stops a decompression bomb (a tiny compressed entry that inflates to
# hundreds of GB) from OOM-killing the single worker on the resource-light box.
# The compressed-upload cap (MAX_BOOK_UPLOAD_BYTES) does NOT bound this — deflate
# reaches ~1000x — so guard at read time, per entry.
_MAX_ZIP_ENTRY_BYTES = 25 * 1024 * 1024  # 25 MiB


def _zip_read_bytes(zf: zipfile.ZipFile, name: str, max_bytes: int = _MAX_ZIP_ENTRY_BYTES) -> bytes:
    """Read one zip entry with a hard decompression cap (SSRF/DoS: zip bomb)."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise HTTPException(422, f"EPUB item not found: {name}")
    # Declared (uncompressed) size is a cheap first gate...
    if info.file_size > max_bytes:
        raise HTTPException(413, f"EPUB item too large: {name}")
    # ...but the central-directory size can lie, so bound the ACTUAL decompression
    # by streaming at most max_bytes+1 decompressed bytes.
    with zf.open(name) as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"EPUB item too large: {name}")
    return data


def _zip_read_text(zf: zipfile.ZipFile, name: str) -> str:
    return _zip_read_bytes(zf, name).decode("utf-8", errors="replace")


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


def _xml_fromstring(data):
    """Parse EPUB metadata XML with a DTD/entity guard.

    container.xml, the OPF package, and the NCX are plain XML that legitimately
    never carry a DOCTYPE. A crafted/hostile EPUB could otherwise smuggle an
    internal ``<!ENTITY>`` definition and blow up memory/CPU via billion-laughs
    entity expansion (stdlib ElementTree still expands internal entities), or an
    external-entity XXE. Rejecting any DTD before parsing neutralizes both with
    no dependency on defusedxml (which isn't guaranteed installed). Accepts
    str or bytes (ET.fromstring handles either)."""
    head = data[:8192]
    if isinstance(head, (bytes, bytearray)):
        head = head.decode("ascii", "ignore")
    low = head.lower()
    if "<!doctype" in low or "<!entity" in low:
        raise ValueError("EPUB XML with a DTD/entity declaration is not allowed")
    return ET.fromstring(data)


def _epub_package(owner: str | None, kb_id: str) -> tuple[Path, str, str, ET.Element]:
    path = book_store.resolve_book_file(owner, kb_id)
    if path.suffix.lower() != ".epub":
        raise HTTPException(400, "File is not an EPUB")
    if not path.is_file():
        raise HTTPException(404, "EPUB not found")
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                container_xml = _zip_read_bytes(zf, "META-INF/container.xml")
            except KeyError:
                raise HTTPException(422, "EPUB container.xml not found")
            root = _xml_fromstring(container_xml)
            rootfile = root.find(".//container:rootfile", _NS)
            opf_path = rootfile.attrib.get("full-path", "") if rootfile is not None else ""
            if not opf_path:
                raise HTTPException(422, "EPUB root package not found")
            opf = _xml_fromstring(_zip_read_bytes(zf, opf_path))
    except zipfile.BadZipFile:
        raise HTTPException(422, "Invalid EPUB zip file")
    except ValueError as e:
        raise HTTPException(422, str(e))
    opf_dir = str(Path(opf_path).parent)
    if opf_dir == ".":
        opf_dir = ""
    return path, kb_id, opf_dir, opf


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
            root = _xml_fromstring(_zip_read_bytes(zf, ncx_item["href"]))
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


# Parsed-structure cache. Reading a book re-hits parse_epub_toc constantly
# (every page turn calls it; search calls it once) and each miss re-opens the
# zip and re-parses container.xml + OPF + nav/NCX. Cache the structure per file,
# keyed on (mtime_ns, size) so an out-of-band change invalidates it, and always
# read reading-progress fresh (it changes as the user reads).
_TOC_CACHE: dict = {}
_TOC_CACHE_MAX = 16


def _file_sig(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def parse_epub_toc(owner: str | None, kb_id: str) -> dict:
    path = book_store.resolve_book_file(owner, kb_id)
    sig = _file_sig(path)
    key = str(path)
    hit = _TOC_CACHE.get(key)
    if hit is not None and sig is not None and hit[0] == sig:
        base = hit[1]
    else:
        base = _parse_epub_structure(owner, kb_id)
        if sig is not None:
            _TOC_CACHE[key] = (sig, base)
            while len(_TOC_CACHE) > _TOC_CACHE_MAX:
                _TOC_CACHE.pop(next(iter(_TOC_CACHE)))
    return {**base, "progress": book_store.get_progress(owner, kb_id)}


def _parse_epub_structure(owner: str | None, kb_id: str) -> dict:
    """The (cacheable) TOC + metadata parse, without reading progress."""
    path, _kb, opf_dir, opf = _epub_package(owner, kb_id)
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
        "id": kb_id,
        "path": kb_id,
        "title": title,
        "author": author,
        "chapter_count": len(chapters),
        "chapters": chapters,
    }


_COVER_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}


def extract_cover(owner: str | None, kb_id: str) -> tuple[bytes, str] | None:
    """Return (image_bytes, content_type) for the EPUB's cover image, or None.

    Tries, in order: the OPF ``<meta name="cover">`` pointer, a manifest item
    with ``properties="cover-image"``, an image whose href mentions "cover", and
    finally the first image in the manifest."""
    try:
        path, _kb, opf_dir, opf = _epub_package(owner, kb_id)
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
            data = _zip_read_bytes(zf, cover_href)
    except Exception:
        return None
    if not data:
        return None
    content_type = _COVER_CONTENT_TYPES.get(Path(cover_href).suffix.lower(), "image/jpeg")
    return data, content_type


def read_epub_chapter(owner: str | None, kb_id: str, chapter_index: int = 0) -> dict:
    toc = parse_epub_toc(owner, kb_id)
    chapters = toc.get("chapters") or []
    if not chapters:
        raise HTTPException(404, "EPUB has no readable chapters")
    idx = max(0, min(int(chapter_index or 0), len(chapters) - 1))
    chapter = chapters[idx]
    path = book_store.resolve_book_file(owner, kb_id)
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
