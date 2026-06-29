"""PUT /api/document/{id} must refuse a soft-deleted (trashed) document.

Regression guard for the workspace zombie-tab bug: deleting a document is a soft
delete (is_active=False → it lives only in the Trash). A stale editor tab whose
2s autosave still PUTs the doc would otherwise silently resurrect it — re-index
it into RAG/search and bump versions — while it still reads as trashed in every
lister. The PUT handler refuses edits to an inactive doc (callers must POST
/restore first); the frontend also closes the tab on delete, but this is the
defense-in-depth backstop for any stale client.
"""
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.document_routes as droutes
from core.database import Document
from routes.document_helpers import DocumentUpdate

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _req(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    router = droutes.setup_document_routes(MagicMock(), None)
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _bind_test_db():
    previous = droutes.SessionLocal
    droutes.SessionLocal = _TS
    return previous


@pytest.mark.asyncio
async def test_put_refuses_trashed_document_and_leaves_it_untouched():
    from fastapi import HTTPException

    previous = _bind_test_db()
    try:
        put = _endpoint("PUT", "/api/document/{doc_id}")
        did = str(uuid.uuid4())
        db = _TS()
        try:
            db.query(Document).delete()
            db.add(Document(
                id=did, title="Trashed", language="markdown",
                current_content="original body", version_count=1,
                is_active=False, owner="alice",
            ))
            db.commit()
        finally:
            db.close()

        with pytest.raises(HTTPException) as exc:
            await put(_req("alice"), did, DocumentUpdate(content="resurrected body"))
        assert exc.value.status_code == 404

        # The trashed doc must be byte-for-byte untouched — not resurrected.
        db = _TS()
        try:
            row = db.query(Document).filter(Document.id == did).first()
            assert row.current_content == "original body"
            assert row.is_active is False
        finally:
            db.close()
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_put_still_updates_an_active_document():
    """The guard must not block normal edits to a live document."""
    previous = _bind_test_db()
    try:
        put = _endpoint("PUT", "/api/document/{doc_id}")
        did = str(uuid.uuid4())
        db = _TS()
        try:
            db.query(Document).delete()
            db.add(Document(
                id=did, title="Live", language="markdown",
                current_content="before", version_count=1,
                is_active=True, owner="alice",
            ))
            db.commit()
        finally:
            db.close()

        res = await put(_req("alice"), did, DocumentUpdate(content="after"))
        assert isinstance(res, dict)
        db = _TS()
        try:
            row = db.query(Document).filter(Document.id == did).first()
            assert row.current_content == "after"
            assert row.is_active is True
        finally:
            db.close()
    finally:
        droutes.SessionLocal = previous
