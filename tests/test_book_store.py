"""Functional tests for the native Books store (src/book_store.py) and the
book_reader facade — the vault-free EPUB/PDF backend.

Covers upsert + unique naming, owner-scoped listing/search, reading-progress
roundtrip, custom title, annotations, delete (row + bytes + child rows), and
path-safety. RAG is stubbed (no ChromaDB). No Obsidian vault involved.
"""
import pytest

pytest.importorskip("sqlalchemy")

from core.database import Base, engine  # noqa: E402
from src import book_store, book_reader  # noqa: E402


@pytest.fixture(autouse=True)
def _tables_and_tmp(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("src.book_store.BOOKS_DIR", str(tmp_path / "books"))
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: None)
    yield


def test_upsert_stores_bytes_and_lists(tmp_path):
    rec = book_store.upsert_book("alice", "Dune.pdf", b"%PDF-1.4 fake", mime="application/pdf")
    assert rec["filename"] == "Dune.pdf"
    assert rec["kind"] == "pdf"
    assert rec["path"] == "Dune.pdf"
    # bytes physically stored + openable
    p = book_store.resolve_book_file("alice", "Dune.pdf")
    assert p.is_file() and p.read_bytes() == b"%PDF-1.4 fake"
    # discoverable, owner-scoped
    assert [b["filename"] for b in book_store.query_books("alice")] == ["Dune.pdf"]
    assert book_store.query_books("bob") == []


def test_unique_rel_path_no_clobber():
    book_store.upsert_book("u", "Book.epub", b"PK\x03\x04 one")
    rec2 = book_store.upsert_book("u", "Book.epub", b"PK\x03\x04 two")
    assert rec2["path"] == "Book 2.epub"
    assert len(book_store.query_books("u")) == 2


def test_search_matches_filename_and_title():
    book_store.upsert_book("s", "huawei-ascend.pdf", b"x")
    book_store.upsert_book("s", "apple.pdf", b"y")
    assert {b["filename"] for b in book_store.query_books("s", "huawei")} == {"huawei-ascend.pdf"}


def test_progress_roundtrip_owner_scoped():
    book_store.upsert_book("p", "novel.pdf", b"x")
    saved = book_store.save_progress("p", "novel.pdf", chapter_index=4, scroll_percent=42.5, kind="pdf")
    assert saved["chapter_index"] == 4 and round(saved["scroll_percent"]) == 42
    got = book_store.get_progress("p", "novel.pdf")
    assert got["chapter_index"] == 4
    # a different owner sees no progress for the same name
    assert book_store.get_progress("other", "novel.pdf", missing_ok=True)["chapter_index"] == 0


def test_custom_title_overrides_and_persists():
    book_store.upsert_book("t", "raw-name.epub", b"x")
    book_store.set_title("t", "raw-name.epub", "A Lovely Title")
    assert book_store.get_book("t", "raw-name.epub")["custom_title"] == "A Lovely Title"
    assert book_store.query_books("t", "Lovely")[0]["title"] == "A Lovely Title"


def test_annotations_add_list_delete():
    book_store.upsert_book("a", "b.pdf", b"x")
    bm = book_store.add_annotation("a", "b.pdf", type="bookmark", chapter_index=2)
    hl = book_store.add_annotation("a", "b.pdf", type="highlight", text="quote", chapter_index=3)
    items = book_store.list_annotations("a", "b.pdf")["items"]
    assert {i["type"] for i in items} == {"bookmark", "highlight"}
    with pytest.raises(Exception):
        book_store.add_annotation("a", "b.pdf", type="highlight", text="")  # highlight needs text
    assert book_store.delete_annotation("a", "b.pdf", bm["id"]) is True
    assert len(book_store.list_annotations("a", "b.pdf")["items"]) == 1


def test_delete_removes_row_bytes_and_children():
    book_store.upsert_book("d", "gone.pdf", b"x")
    book_store.save_progress("d", "gone.pdf", chapter_index=1)
    book_store.add_annotation("d", "gone.pdf", type="bookmark")
    p = book_store.resolve_book_file("d", "gone.pdf")
    assert p.is_file()
    assert book_store.delete_book("d", "gone.pdf") is True
    assert not p.is_file()
    assert book_store.get_book("d", "gone.pdf") is None
    assert book_store.list_annotations("d", "gone.pdf")["items"] == []
    assert book_store.delete_book("d", "gone.pdf") is False  # already gone


def test_two_owners_same_name_dont_collide():
    book_store.upsert_book("o1", "same.pdf", b"one")
    book_store.upsert_book("o2", "same.pdf", b"two")
    assert book_store.resolve_book_file("o1", "same.pdf").read_bytes() == b"one"
    assert book_store.resolve_book_file("o2", "same.pdf").read_bytes() == b"two"


def test_book_reader_facade_uploads_and_lists():
    rec = book_reader.save_uploaded_book("f", "Reader.pdf", b"%PDF fake", mime="application/pdf")
    assert rec["path"] == "Reader.pdf"
    # index_book is best-effort (RAG stubbed) and must not raise
    book_reader.index_book("f", "Reader.pdf")
    names = [b["filename"] for b in book_reader.list_books("f")]
    assert "Reader.pdf" in names
    # rejects unsupported types
    with pytest.raises(Exception):
        book_reader.save_uploaded_book("f", "notabook.txt", b"x")
