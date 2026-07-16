"""Functional coverage for the EPUB/PDF reader engine (src/epub_reader.py,
src/book_reader.py).

Previously the engine had only source-grep guards; this exercises the real
parse paths end-to-end against tiny in-memory fixtures, plus the parsed-structure
cache (hit + mtime/size invalidation).

Books are installed DIRECTLY into the (temp-redirected) Books store — file at the
same relative path add_book uses + a Book row — so the reader tests don't depend
on add_book's ingest/copy path and stay deterministic regardless of suite order
or shared-DB state.
"""
import hashlib
import io
import os
import sys
import uuid
import zipfile

import pytest

pytest.importorskip("sqlalchemy")
from pypdf import PdfWriter  # noqa: E402

import core.database as _core_db  # noqa: E402  (captured real module — see _harness)
from core.database import Base, engine, SessionLocal, Book  # noqa: E402
from src import book_store, book_reader, epub_reader  # noqa: E402


# ── tiny valid fixtures ──────────────────────────────────────────────────────
_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>
  </metadata>
  <manifest>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="ch1"/></spine>
</package>"""

_NCX = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap><navPoint id="np1" playOrder="1">
    <navLabel><text>{chapter_title}</text></navLabel><content src="ch1.xhtml"/>
  </navPoint></navMap>
</ncx>"""

_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{chapter_title}</title></head>
<body><h1>{chapter_title}</h1><p>{body}</p></body></html>"""


def _make_epub(title="Test Book", author="A. Author", chapter_title="Chapter One",
               body="The quick brown fox jumps over the lazy dog.") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", _CONTAINER)
        z.writestr("content.opf", _OPF.format(title=title, author=author))
        z.writestr("toc.ncx", _NCX.format(chapter_title=chapter_title))
        z.writestr("ch1.xhtml", _XHTML.format(chapter_title=chapter_title, body=body))
    return buf.getvalue()


def _make_pdf(pages=1) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _harness(monkeypatch, tmp_path):
    # Other test modules replace/delete sys.modules["core.database"] at MODULE
    # scope (fires during full-suite collection); book_store.resolve_book_file
    # does a function-local `from core.database import SessionLocal, Book`, so it
    # would otherwise pick up a contaminated binding and 404. This module imports
    # early, so pin the real module + ORM bindings back for the duration of each
    # test. (monkeypatch restores them afterwards.)
    monkeypatch.setitem(sys.modules, "core.database", _core_db)
    monkeypatch.setattr(_core_db, "SessionLocal", SessionLocal, raising=False)
    monkeypatch.setattr(_core_db, "Book", Book, raising=False)
    monkeypatch.setattr(_core_db, "engine", engine, raising=False)

    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("src.book_store._books_dir", lambda: str(tmp_path / "books"))
    epub_reader._TOC_CACHE.clear()
    book_reader._PDF_CACHE.clear()
    yield
    db = SessionLocal()
    try:
        db.query(Book).delete()
        db.commit()
    finally:
        db.close()


def _install(owner, filename, content, mime="") -> str:
    """Place a book in the (temp) store exactly like add_book's storage layout,
    without going through the ingest/extract/RAG/copy path."""
    bid = uuid.uuid4().hex
    ext = os.path.splitext(filename)[1].lower()
    rel = os.path.join(book_store._owner_slug(owner), bid[:2], f"{bid}{ext}")
    dest = os.path.join(book_store._books_dir(), rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(content)
    db = SessionLocal()
    try:
        db.add(Book(id=bid, owner=owner, filename=filename, mime=mime or None,
                    file_size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                    path=rel, text="", indexed=False))
        db.commit()
    finally:
        db.close()
    return bid


# ── EPUB ─────────────────────────────────────────────────────────────────────
def test_parse_epub_toc():
    kb = _install("alice", "book.epub", _make_epub())
    toc = epub_reader.parse_epub_toc("alice", kb)
    assert toc["title"] == "Test Book" and toc["author"] == "A. Author"
    assert toc["chapter_count"] == 1
    assert toc["chapters"][0]["title"] == "Chapter One"  # from the NCX


def test_read_epub_chapter():
    kb = _install("alice", "book.epub", _make_epub())
    ch = epub_reader.read_epub_chapter("alice", kb, 0)
    assert "quick brown fox" in ch["html"]
    assert ch["word_count"] > 0


def test_search_epub_text():
    kb = _install("alice", "book.epub", _make_epub())
    res = book_reader.search_book_text("alice", kb, "brown")
    assert res["total"] >= 1
    assert "brown" in res["matches"][0]["snippet"].lower()


# ── PDF ──────────────────────────────────────────────────────────────────────
def test_parse_pdf():
    kb = _install("bob", "doc.pdf", _make_pdf(pages=2), mime="application/pdf")
    parsed = book_reader.parse_pdf("bob", kb)
    assert parsed["kind"] == "pdf"
    assert parsed["chapter_count"] == 2
    assert parsed["chapters"][0]["href"] == "page-1"


# ── cache: hit + invalidation ────────────────────────────────────────────────
def test_epub_toc_cache_hits_then_invalidates(monkeypatch):
    kb = _install("alice", "book.epub", _make_epub(title="First"))
    calls = {"n": 0}
    orig = epub_reader._epub_package

    def _counting(owner, kb_id):
        calls["n"] += 1
        return orig(owner, kb_id)

    monkeypatch.setattr(epub_reader, "_epub_package", _counting)

    assert epub_reader.parse_epub_toc("alice", kb)["title"] == "First"
    assert epub_reader.parse_epub_toc("alice", kb)["title"] == "First"
    assert calls["n"] == 1, "second parse should hit the cache (no re-parse)"

    # Overwrite the stored bytes with a different-sized EPUB → mtime+size change
    # must invalidate the cache and surface the new title.
    path = book_store.resolve_book_file("alice", kb)
    path.write_bytes(_make_epub(title="Second Edition (longer)"))
    assert epub_reader.parse_epub_toc("alice", kb)["title"] == "Second Edition (longer)"
    assert calls["n"] == 2, "changed file must miss the cache"


def test_pdf_cache_hits_then_invalidates(monkeypatch):
    kb = _install("bob", "doc.pdf", _make_pdf(pages=1), mime="application/pdf")
    calls = {"n": 0}
    import pypdf
    orig = pypdf.PdfReader

    def _counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(pypdf, "PdfReader", _counting)

    assert book_reader.parse_pdf("bob", kb)["chapter_count"] == 1
    assert book_reader.parse_pdf("bob", kb)["chapter_count"] == 1
    assert calls["n"] == 1, "second parse_pdf should hit the cache"

    path = book_store.resolve_book_file("bob", kb)
    path.write_bytes(_make_pdf(pages=3))
    assert book_reader.parse_pdf("bob", kb)["chapter_count"] == 3
    assert calls["n"] == 2, "changed PDF must miss the cache"
