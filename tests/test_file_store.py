"""Functional tests for the native Files store (src/file_store.py).

The Files store holds any uploaded file that isn't media/book/authored-text. It
owns its bytes (DATA_DIR/files) and extracted text (RAG-indexed under
kind="file"), decoupled from the retired Knowledge base. Covers ingest→search,
get-full-text, tags/favorite/rename, edit+reindex, delete (row + bytes), count,
and owner-scoping. RAG is stubbed and the byte store is redirected to a temp dir.
"""
import os
import tempfile

import pytest

pytest.importorskip("sqlalchemy")

from core.database import Base, engine  # noqa: E402
from src import file_store as fs  # noqa: E402


@pytest.fixture(autouse=True)
def _tables_and_tmp(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("src.file_store._files_dir", lambda: str(tmp_path / "files"))
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: None)
    yield


def _ingest(owner, name, content, mime="text/plain", tags=""):
    p = os.path.join(tempfile.gettempdir(), f"filestore-test-{name}")
    with open(p, "wb") as f:
        f.write(content)
    try:
        return fs.ingest(owner, file_path=p, filename=name, mime=mime, tags=tags)
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def test_ingest_search_and_owner_scope():
    rec = _ingest("alice", "notes.txt", b"alpha bravo charlie", tags="work, refs")
    assert rec["filename"] == "notes.txt" and rec["tags"] == ["work", "refs"]
    assert [r["id"] for r in fs.search("alice", q="bravo")] == [rec["id"]]
    assert fs.search("alice", q="nomatch") == []
    assert fs.search("bob") == []                 # owner-scoped
    assert fs.get("intruder", rec["id"]) is None  # owner-scoped get


def test_dedup_same_content_returns_existing():
    a = _ingest("d", "a.txt", b"same bytes")
    b = _ingest("d", "b.txt", b"same bytes")
    assert a["id"] == b["id"]
    assert fs.count("d") == 1


def test_get_returns_full_text_and_raw_path():
    rec = _ingest("g", "doc.txt", b"the full extracted body")
    full = fs.get("g", rec["id"])
    assert full["text"] == "the full extracted body"
    assert full["url"] == f"/api/files/{rec['id']}/raw"
    p = fs.file_abspath("g", rec["id"])
    assert p and os.path.exists(p)
    assert fs.file_abspath("intruder", rec["id"]) is None


def test_tags_and_rename():
    rec = _ingest("t", "x.txt", b"body")
    assert fs.set_tags("t", rec["id"], "one, two, two")["tags"] == ["one", "two"]  # de-duped
    assert fs.rename("t", rec["id"], "renamed.txt")["filename"] == "renamed.txt"
    assert fs.rename("intruder", rec["id"], "hacked.txt") is None  # owner-scoped


def test_update_text_rewrites_bytes_for_text_files():
    rec = _ingest("u", "edit.txt", b"original")
    fs.update_text("u", rec["id"], "corrected text")
    assert fs.get("u", rec["id"])["text"] == "corrected text"
    p = fs.file_abspath("u", rec["id"])
    with open(p, encoding="utf-8") as fh:
        assert fh.read() == "corrected text"  # bytes rewritten for a .txt


def test_delete_removes_row_and_bytes():
    rec = _ingest("del", "gone.txt", b"bye")
    p = fs.file_abspath("del", rec["id"])
    assert os.path.exists(p)
    assert fs.delete("del", rec["id"]) is True
    assert fs.get("del", rec["id"]) is None
    assert not os.path.exists(p)
    assert fs.delete("del", rec["id"]) is False  # already gone


def test_file_routes_registered():
    from routes.file_routes import setup_file_routes

    class _Stub:
        def resolve_upload(self, *a, **k):
            return None

    paths = {r.path for r in setup_file_routes(_Stub()).routes}
    assert "/api/files" in paths
    assert "/api/files/{file_id}" in paths
    assert "/api/files/{file_id}/raw" in paths
    assert "/api/files/{file_id}/tags" in paths
