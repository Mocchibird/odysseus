"""Route-level tests for DELETE /api/books (routes/book_routes.py).

The delete route takes the book's kb_id as a ``path`` query param (the same
query-param idiom as DELETE /api/books/annotations), resolves the owner via
``require_user(request) or "local"``, and 404s when the book does not exist
or belongs to a different owner. Harness mirrors test_gallery_null_user_routes:
a per-test file-backed SQLite engine with NullPool (TestClient runs handlers on
a worker thread, so the session-wide ``:memory:`` engine — whose pool is
per-thread — would show no tables there), the Books byte store redirected to a
temp dir, RAG stubbed; the router is mounted on a minimal FastAPI app (app.py
is not importable here — conftest stubs src.database) with require_user patched.
"""
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import core.database as cdb  # noqa: E402
from src import book_store  # noqa: E402
import routes.book_routes as book_routes  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'books.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    # book_store imports SessionLocal from core.database inside each function,
    # so patching the module attribute redirects every query to this engine.
    monkeypatch.setattr(
        cdb, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False)
    )
    monkeypatch.setattr("src.book_store._books_dir", lambda: str(tmp_path / "books"))
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: None)
    yield


def _client(monkeypatch, owner=""):
    """Minimal app with just the books router; require_user is patched so the
    route's ``_owner`` sees the given username. The default "" exercises the
    single-user fallback: ``require_user(request) or "local"`` -> "local"."""
    monkeypatch.setattr(book_routes, "require_user", lambda request: owner)
    app = FastAPI()
    app.include_router(book_routes.setup_book_routes())
    return TestClient(app)


def test_delete_route_removes_book_then_404s(monkeypatch):
    rec = book_store.add_book("local", "t.pdf", b"%PDF-1.4 x", mime="application/pdf")
    client = _client(monkeypatch)  # "" -> owner "local"

    res = client.delete("/api/books", params={"path": rec["id"]})
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert book_store.get_book("local", rec["id"]) is None  # row gone

    # Deleting the same book again: already gone -> 404.
    assert client.delete("/api/books", params={"path": rec["id"]}).status_code == 404


def test_delete_route_unknown_book_404(monkeypatch):
    client = _client(monkeypatch)
    res = client.delete("/api/books", params={"path": "no-such-book"})
    assert res.status_code == 404


def test_delete_route_is_owner_scoped(monkeypatch):
    rec = book_store.add_book("alice", "hers.pdf", b"%PDF-1.4 alice", mime="application/pdf")
    client = _client(monkeypatch, owner="mallory")

    res = client.delete("/api/books", params={"path": rec["id"]})
    assert res.status_code == 404  # someone else's book reads as missing
    assert book_store.get_book("alice", rec["id"]) is not None  # untouched
