"""Functional tests for the unified Books store (src/book_store.py).

A "book" IS a PDF/EPUB in the Knowledge base, so Books and Knowledge stay in
sync. Covers add→list, the Knowledge⇄Books sync (KB pdf/epubs show as books,
other files don't), reading-progress, rename (renames the underlying KB file),
annotations, delete (removes from both), and owner-scoping. RAG is stubbed and
the KB file store is redirected to a temp dir.
"""
import os
import tempfile

import pytest

pytest.importorskip("sqlalchemy")

from core.database import Base, engine  # noqa: E402
from src import book_store, knowledge_base as kb  # noqa: E402


@pytest.fixture(autouse=True)
def _tables_and_tmp(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("src.knowledge_base._data_dir", lambda: str(tmp_path / "data"))
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: None)
    yield


def _kb_ingest(owner, name, content, mime=""):
    """Add a file straight to the Knowledge base (simulating the Knowledge panel)."""
    p = os.path.join(tempfile.gettempdir(), f"bookstore-test-{name}")
    with open(p, "wb") as f:
        f.write(content)
    try:
        return kb.ingest(owner, file_path=p, filename=name, mime=mime, source="upload")
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def test_add_book_appears_in_books_and_knowledge():
    rec = book_store.add_book("alice", "Dune.pdf", b"%PDF-1.4 alice dune", mime="application/pdf")
    assert rec["kind"] == "pdf" and rec["title"] == "Dune" and rec["path"] == rec["id"]
    kid = rec["id"]
    assert [b["id"] for b in book_store.list_books("alice")] == [kid]
    # a book IS a knowledge file — same id, present in the KB (synced)
    assert kb.get("alice", kid)["filename"] == "Dune.pdf"
    assert book_store.list_books("bob") == []  # owner-scoped


def test_knowledge_pdf_epub_show_as_books_others_dont():
    pdf = _kb_ingest("u", "paper.pdf", b"%PDF-1.4 paper", "application/pdf")
    epub = _kb_ingest("u", "novel.epub", b"PK\x03\x04 novel")
    note = _kb_ingest("u", "memo.txt", b"just a note")  # not a book
    ids = {b["id"] for b in book_store.list_books("u")}
    assert pdf["id"] in ids and epub["id"] in ids
    assert note["id"] not in ids
    assert {b["kind"] for b in book_store.list_books("u")} == {"pdf", "epub"}


def test_resolve_book_file_returns_bytes_owner_scoped():
    rec = book_store.add_book("r", "x.pdf", b"%PDF real bytes", mime="application/pdf")
    p = book_store.resolve_book_file("r", rec["id"])
    assert p.is_file() and p.read_bytes() == b"%PDF real bytes"
    assert book_store.get_book("intruder", rec["id"]) is None


def test_progress_roundtrip_and_in_list():
    rec = book_store.add_book("p", "b.pdf", b"%PDF p", mime="application/pdf")
    book_store.save_progress("p", rec["id"], chapter_index=3, scroll_percent=40.0, kind="pdf")
    got = book_store.get_progress("p", rec["id"])
    assert got["chapter_index"] == 3 and round(got["scroll_percent"]) == 40
    assert book_store.list_books("p")[0]["progress"]["chapter_index"] == 3


def test_rename_renames_the_underlying_knowledge_file():
    rec = book_store.add_book("t", "raw.epub", b"PK raw bytes")
    book_store.set_title("t", rec["id"], "A Lovely Title")
    # renames the KB file, so it shows renamed in BOTH Books and Knowledge
    assert kb.get("t", rec["id"])["filename"] == "A Lovely Title.epub"
    assert book_store.get_book("t", rec["id"])["title"] == "A Lovely Title"


def test_annotations_add_list_delete():
    rec = book_store.add_book("a", "b.pdf", b"%PDF a", mime="application/pdf")
    kid = rec["id"]
    bm = book_store.add_annotation("a", kid, type="bookmark", chapter_index=1)
    book_store.add_annotation("a", kid, type="highlight", text="quote", chapter_index=2)
    assert {i["type"] for i in book_store.list_annotations("a", kid)["items"]} == {"bookmark", "highlight"}
    with pytest.raises(Exception):
        book_store.add_annotation("a", kid, type="highlight", text="")  # highlight needs text
    assert book_store.delete_annotation("a", kid, bm["id"]) is True
    assert len(book_store.list_annotations("a", kid)["items"]) == 1


def test_delete_book_removes_from_knowledge_and_clears_state():
    rec = book_store.add_book("d", "gone.pdf", b"%PDF gone", mime="application/pdf")
    kid = rec["id"]
    book_store.save_progress("d", kid, chapter_index=1)
    book_store.add_annotation("d", kid, type="bookmark")
    assert book_store.delete_book("d", kid) is True
    assert kb.get("d", kid) is None  # gone from Knowledge too (one store)
    assert book_store.get_book("d", kid) is None
    assert book_store.list_annotations("d", kid)["items"] == []
    assert book_store.get_progress("d", kid, missing_ok=True)["chapter_index"] == 0
    assert book_store.delete_book("d", kid) is False  # already gone
