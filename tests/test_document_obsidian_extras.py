"""Obsidian-like document extras (fork): the `/api/documents/titles` list that
backs the `[[` autocomplete, and the `/api/document/{id}/related` See-also feed.

Handlers are invoked directly with a fake request — the same direct-closure
pattern the other document route tests use (no middleware spin-up). RAG is
stubbed so the related endpoint's shaping logic (self-exclusion, dedup, snippet)
is covered without a live vector store.
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
import src.content_rag as content_rag
from core.database import Document

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


def _doc(doc_id, title, owner, *, archived=False, content="body"):
    return Document(
        id=doc_id, title=title, language="markdown", current_content=content,
        version_count=1, is_active=True, archived=archived, owner=owner,
    )


def _seed():
    alice_a = str(uuid.uuid4())
    alice_archived = str(uuid.uuid4())
    bob = str(uuid.uuid4())
    db = _TS()
    try:
        db.query(Document).delete()  # clean slate (temp DB persists across tests in this module)
        db.add(_doc(alice_a, "Alpha Notes", "alice", content="alpha topic body"))
        db.add(_doc(alice_archived, "Archived One", "alice", archived=True))
        db.add(_doc(bob, "Bob Secret", "bob"))
        db.commit()
        return alice_a, alice_archived, bob
    finally:
        db.close()


@pytest.mark.asyncio
async def test_titles_lists_only_owner_active_unarchived_docs():
    previous = _bind_test_db()
    try:
        titles_ep = _endpoint("GET", "/api/documents/titles")
        alice_a, _alice_archived, bob = _seed()
        res = await titles_ep(_req("alice"))
        titles = {t["title"] for t in res["titles"]}
        ids = {t["id"] for t in res["titles"]}
        assert "Alpha Notes" in titles
        assert "Bob Secret" not in titles       # owner-scoped
        assert "Archived One" not in titles      # archived excluded
        assert alice_a in ids and bob not in ids
        assert res["count"] == len(res["titles"])
        assert all(set(t.keys()) == {"id", "title"} for t in res["titles"])
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_related_excludes_self_dedups_and_shapes(monkeypatch):
    previous = _bind_test_db()
    try:
        related_ep = _endpoint("GET", "/api/document/{doc_id}/related")
        alice_a, _alice_archived, bob = _seed()
        # Fake RAG hits: the doc itself, a duplicate of another, and that other —
        # the endpoint must drop self, dedup by kb_id, and carry title/kind/snippet.
        fake = [
            {"kb_id": alice_a, "filename": "Alpha Notes", "kind": "document", "text": "self"},
            {"kb_id": bob, "filename": "Bob Secret", "kind": "document", "text": "  bob   body   here  "},
            {"kb_id": bob, "filename": "Bob Secret", "kind": "document", "text": "dup"},
        ]
        captured = {}

        def _fake_search(owner, q, k=5, kinds=None):
            captured["owner"], captured["kinds"] = owner, kinds
            return list(fake)

        monkeypatch.setattr(content_rag, "semantic_search", _fake_search)
        res = await related_ep(_req("alice"), alice_a, k=6)
        rel = res["related"]
        ids = [r["id"] for r in rel]
        assert alice_a not in ids                 # self excluded
        assert ids.count(bob) == 1                # deduped
        assert rel[0]["title"] == "Bob Secret"
        assert rel[0]["kind"] == "document"
        assert rel[0]["snippet"] == "bob body here"   # whitespace-collapsed
        assert captured["owner"] == "alice"           # owner-scoped query
        assert captured["kinds"] == ["document"]
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_related_empty_when_rag_cold(monkeypatch):
    # With RAG cold AND no title/link/session neighbours, the feed is empty.
    # ("Alpha Notes" has no siblings; "Archived One" is archived, "Bob Secret"
    # is another owner's — neither is a candidate.)
    previous = _bind_test_db()
    try:
        related_ep = _endpoint("GET", "/api/document/{doc_id}/related")
        alice_a, _archived, _bob = _seed()
        monkeypatch.setattr(content_rag, "semantic_search", lambda *a, **k: [])
        res = await related_ep(_req("alice"), alice_a, k=6)
        assert res == {"related": []}
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_related_surfaces_title_series_without_rag(monkeypatch):
    """The reported bug: a lesson in a SERIES must surface its siblings even when
    RAG is cold or the docs were never indexed — on pure title similarity."""
    previous = _bind_test_db()
    try:
        related_ep = _endpoint("GET", "/api/document/{doc_id}/related")
        l06, l07, l08, other = (str(uuid.uuid4()) for _ in range(4))
        db = _TS()
        try:
            db.query(Document).delete()
            db.add(_doc(l06, "Japanese A1.2 Lesson 06", "alice"))
            db.add(_doc(l07, "Japanese A1.2 Lesson 07", "alice"))
            db.add(_doc(l08, "Japanese A1.2 Lesson 08", "alice"))
            db.add(_doc(other, "TileLang Ascend Notes", "alice"))
            db.commit()
        finally:
            db.close()
        monkeypatch.setattr(content_rag, "semantic_search", lambda *a, **k: [])  # RAG cold
        res = await related_ep(_req("alice"), l07, k=6)
        ids = [r["id"] for r in res["related"]]
        assert l06 in ids and l08 in ids            # siblings surface without RAG
        assert l07 not in ids                        # self excluded
        assert other not in ids                      # unrelated title not pulled in
        by_id = {r["id"]: r for r in res["related"]}
        assert by_id[l06]["reason"] in ("series", "topic")
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_related_includes_wikilinks_and_backlinks(monkeypatch):
    """Manual override: outgoing ``[[links]]`` and backlinks rank at the top,
    independent of RAG."""
    previous = _bind_test_db()
    try:
        related_ep = _endpoint("GET", "/api/document/{doc_id}/related")
        hub, target, backref = (str(uuid.uuid4()) for _ in range(3))
        db = _TS()
        try:
            db.query(Document).delete()
            db.add(_doc(hub, "Grammar Hub", "alice", content="See [[Particle Wa]] for the topic particle."))
            db.add(_doc(target, "Particle Wa", "alice", content="The topic particle wa."))
            db.add(_doc(backref, "Lesson Recap", "alice", content="A recap of [[Grammar Hub]]."))
            db.commit()
        finally:
            db.close()
        monkeypatch.setattr(content_rag, "semantic_search", lambda *a, **k: [])
        res = await related_ep(_req("alice"), hub, k=6)
        rel = {r["id"]: r for r in res["related"]}
        assert target in rel                         # outgoing [[Particle Wa]]
        assert backref in rel                        # backlink from "Lesson Recap"
        assert rel[target]["reason"] == "linked"
        assert rel[backref]["reason"] == "linked"
    finally:
        droutes.SessionLocal = previous


def test_series_key_parses_trailing_number():
    assert droutes._series_key("Japanese A1.2 Lesson 22") == ("japanese a1.2 lesson", 22)
    assert droutes._series_key("Lesson 7") == ("lesson", 7)
    assert droutes._series_key("Chapter   12  ") == ("chapter", 12)
    assert droutes._series_key("No number here") == (None, None)
    assert droutes._series_key("") == (None, None)


@pytest.mark.asyncio
async def test_related_orders_series_siblings_by_numeric_proximity(monkeypatch):
    """For a lesson in a series, the nearest siblings rank first and in order:
    Lesson 22 -> 21 then 23 (distance 1), ahead of 20 (distance 2). Title
    similarity scores all three identically, so this guards the numeric-proximity
    tiebreak (without it the two shown fell to DB insertion order)."""
    previous = _bind_test_db()
    try:
        related_ep = _endpoint("GET", "/api/document/{doc_id}/related")
        l20, l21, l22, l23 = (str(uuid.uuid4()) for _ in range(4))
        db = _TS()
        try:
            db.query(Document).delete()
            # Insert in a deliberately non-ascending order so a correct result
            # can't come from DB insertion order alone.
            db.add(_doc(l20, "Japanese A1.2 Lesson 20", "alice"))
            db.add(_doc(l23, "Japanese A1.2 Lesson 23", "alice"))
            db.add(_doc(l22, "Japanese A1.2 Lesson 22", "alice"))
            db.add(_doc(l21, "Japanese A1.2 Lesson 21", "alice"))
            db.commit()
        finally:
            db.close()
        monkeypatch.setattr(content_rag, "semantic_search", lambda *a, **k: [])  # RAG cold
        res = await related_ep(_req("alice"), l22, k=6)
        ids = [r["id"] for r in res["related"]]
        # The "Most relevant" strip is items[:2] in the UI — adjacent, in order.
        assert ids[:2] == [l21, l23]
        # The distance-2 sibling ranks below both adjacents.
        assert ids.index(l20) > ids.index(l23)
    finally:
        droutes.SessionLocal = previous


def test_obsidian_extras_frontend_wiring_present():
    """Guard the front-end hooks so a refactor can't silently drop them."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    markdown = (root / "static" / "js" / "markdown.js").read_text(encoding="utf-8")
    document = (root / "static" / "js" / "document.js").read_text(encoding="utf-8")
    fork_css = (root / "static" / "fork.css").read_text(encoding="utf-8")

    # Renderer: `![[name]]` -> gallery-pending chip + the exported async resolver.
    assert "_galleryEmbed" in markdown
    assert "md-gallery-pending" in markdown
    assert "resolveGalleryEmbeds" in markdown
    assert "/api/gallery/library?search=" in markdown

    # document.js: gallery resolve hook, related panel, and `[[` autocomplete.
    assert "resolveGalleryEmbeds(preview)" in document
    assert "_renderRelatedNotes" in document
    assert "/api/document/${docId}/related" in document
    assert "_wikiACUpdate" in document and "_wikiACKeydown" in document
    assert "/api/documents/titles" in document
    assert "doc-wikilink-ac" in document
    # the cache-poison guard on the titles fetch (empty/failure must not stick)
    assert "_wikiTitlesPromise = null" in document

    # fork.css: styles for all three (kept out of style.css for upstream alignment).
    for sel in (".md-gallery-embed", ".doc-related-top", ".doc-related-foot", ".doc-wikilink-ac"):
        assert sel in fork_css


def test_live_refresh_wiring_present():
    """Guard the live-refresh-on-change wiring: doc mutations + the agent stream
    broadcast `documents-refresh`; the Library, the `[[` titles cache, and the
    sibling windows (Notes/Tasks/Files) listen and refresh if open. Matches the
    gallery-refresh/calendar-refresh CustomEvent idiom."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    j = lambda p: (root / "static" / "js" / p).read_text(encoding="utf-8")
    document, chat = j("document.js"), j("chat.js")
    library, notes, tasks = j("documentLibrary.js"), j("notes.js"), j("tasks.js")

    # document.js: a single emit helper fired from user mutations AND the agent
    # SSE finalize, plus the titles-cache invalidation listener.
    assert "_emitDocsChanged" in document
    assert "new CustomEvent('documents-refresh'" in document
    assert document.count("_emitDocsChanged(") >= 6      # create/autocreate/inject/save/title/delete/agent
    assert "addEventListener('documents-refresh'" in document  # invalidates `[[` titles cache

    # chat.js: tool-completion -> refresh event for each content-creating tool.
    for tool in ("create_document", "manage_documents", "manage_notes", "manage_tasks", "manage_files"):
        assert tool in chat
    for evt in ("documents-refresh", "notes-refresh", "tasks-refresh", "files-refresh"):
        assert evt in chat

    # Listeners that refresh-if-open in each window.
    assert "addEventListener('documents-refresh'" in library
    assert "addEventListener('files-refresh'" in library
    assert "addEventListener('notes-refresh'" in notes
    assert "addEventListener('tasks-refresh'" in tasks
