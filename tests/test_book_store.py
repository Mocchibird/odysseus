"""Functional tests for the native Books store (src/book_store.py).

A book is a PDF/EPUB in the Books store — it owns its own bytes (BOOKS_DIR) and
extracted text (RAG-indexed under kind="book"), decoupled from the Knowledge
base. Covers add→list, reading-progress, rename, annotations, delete (row +
bytes + state), favourites, and owner-scoping. RAG is stubbed and the Books byte
store is redirected to a temp dir.
"""
import pytest

pytest.importorskip("sqlalchemy")

from core.database import Base, engine, SessionLocal, Book  # noqa: E402
from src import book_store  # noqa: E402
from fastapi import HTTPException  # noqa: E402


@pytest.fixture(autouse=True)
def _tables_and_tmp(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("src.book_store._books_dir", lambda: str(tmp_path / "books"))
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: None)

    def _clear_books():
        db = SessionLocal()
        try:
            db.query(Book).delete()
            db.commit()
        finally:
            db.close()

    # These tests assert exact per-owner book sets, so start (and leave) from a
    # clean Book table — otherwise a book left by any earlier test in the run
    # makes list/count assertions flaky under a given collection order.
    _clear_books()
    yield
    _clear_books()


def test_add_book_appears_in_books_owner_scoped():
    rec = book_store.add_book("alice", "Dune.pdf", b"%PDF-1.4 alice dune", mime="application/pdf")
    assert rec["kind"] == "pdf" and rec["title"] == "Dune" and rec["path"] == rec["id"]
    assert [b["id"] for b in book_store.list_books("alice")] == [rec["id"]]
    assert book_store.list_books("bob") == []  # owner-scoped


def test_add_book_rejects_non_book_types():
    book_store.add_book("u", "paper.pdf", b"%PDF-1.4 paper", mime="application/pdf")
    book_store.add_book("u", "novel.epub", b"PK\x03\x04 novel")
    assert {b["kind"] for b in book_store.list_books("u")} == {"pdf", "epub"}
    with pytest.raises(HTTPException):
        book_store.add_book("u", "memo.txt", b"just a note")  # not a book


def test_dedup_same_content_returns_existing():
    a = book_store.add_book("d2", "x.pdf", b"%PDF same", mime="application/pdf")
    b = book_store.add_book("d2", "x-again.pdf", b"%PDF same", mime="application/pdf")
    assert a["id"] == b["id"]  # per-owner content-hash dedup
    assert len(book_store.list_books("d2")) == 1


def test_resolve_book_file_returns_bytes_owner_scoped():
    rec = book_store.add_book("r", "x.pdf", b"%PDF real bytes", mime="application/pdf")
    p = book_store.resolve_book_file("r", rec["id"])
    assert p.is_file() and p.read_bytes() == b"%PDF real bytes"
    assert book_store.get_book("intruder", rec["id"]) is None
    with pytest.raises(HTTPException):
        book_store.resolve_book_file("intruder", rec["id"])


def test_progress_roundtrip_and_in_list():
    rec = book_store.add_book("p", "b.pdf", b"%PDF p", mime="application/pdf")
    book_store.save_progress("p", rec["id"], chapter_index=3, scroll_percent=40.0, kind="pdf")
    got = book_store.get_progress("p", rec["id"])
    assert got["chapter_index"] == 3 and round(got["scroll_percent"]) == 40
    assert book_store.list_books("p")[0]["progress"]["chapter_index"] == 3


def test_rename_updates_book_title():
    rec = book_store.add_book("t", "raw.epub", b"PK raw bytes")
    book_store.set_title("t", rec["id"], "A Lovely Title")
    assert book_store.get_book("t", rec["id"])["title"] == "A Lovely Title"
    assert book_store.get_book("t", rec["id"])["filename"] == "A Lovely Title.epub"
    with pytest.raises(HTTPException):
        book_store.set_title("intruder", rec["id"], "Hijack")  # owner-scoped


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


def test_delete_book_removes_bytes_and_clears_state():
    rec = book_store.add_book("d", "gone.pdf", b"%PDF gone", mime="application/pdf")
    kid = rec["id"]
    p = book_store.resolve_book_file("d", kid)
    book_store.save_progress("d", kid, chapter_index=1)
    book_store.add_annotation("d", kid, type="bookmark")
    assert book_store.delete_book("d", kid) is True
    assert book_store.get_book("d", kid) is None
    assert not p.exists()  # bytes removed
    assert book_store.list_annotations("d", kid)["items"] == []
    assert book_store.get_progress("d", kid)["chapter_index"] == 0
    assert book_store.delete_book("d", kid) is False  # already gone


def test_favorite_sorts_books_to_top():
    a = book_store.add_book("fav", "A.pdf", b"%PDF a", mime="application/pdf")
    book_store.add_book("fav", "B.pdf", b"%PDF b", mime="application/pdf")
    book_store.add_book("fav", "C.pdf", b"%PDF c", mime="application/pdf")
    assert all(x["favorite"] is False for x in book_store.list_books("fav"))
    out = book_store.set_favorite("fav", a["id"], True)
    assert out["favorite"] is True
    rows = book_store.list_books("fav")
    assert rows[0]["id"] == a["id"]
    assert [r["favorite"] for r in rows] == [True, False, False]
    book_store.set_favorite("fav", a["id"], False)
    assert all(x["favorite"] is False for x in book_store.list_books("fav"))


def test_set_favorite_owner_scoped():
    rec = book_store.add_book("o1", "x.pdf", b"%PDF x", mime="application/pdf")
    with pytest.raises(HTTPException):
        book_store.set_favorite("o2", rec["id"], True)  # not their book
    assert book_store.get_book("o1", rec["id"])["favorite"] is False
    book_store.set_favorite("o1", rec["id"], True)
    assert book_store.get_book("o1", rec["id"])["favorite"] is True


def test_books_routes_expose_favorite():
    from routes.book_routes import setup_book_routes
    paths = {r.path for r in setup_book_routes().routes}
    assert "/api/books/favorite" in paths
