"""Obsidian-like document extras (fork): the `/api/documents/titles` list that
backs the `[[` autocomplete, plus document tagging (set/normalize, the Library
tag facet + filter, and AI tag suggestions) and the front-end wiring guards.

Handlers are invoked directly with a fake request — the same direct-closure
pattern the other document route tests use (no middleware spin-up). RAG is
stubbed where a handler queries it, so shaping logic is covered without a live
vector store.
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
async def test_set_document_tags_normalizes():
    """Comma list → de-duped (case-insensitive) + trimmed; empty clears."""
    previous = _bind_test_db()
    try:
        set_ep = _endpoint("POST", "/api/document/{doc_id}/tags")
        did = str(uuid.uuid4())
        db = _TS()
        try:
            db.query(Document).delete()
            db.add(_doc(did, "Kernel notes", "alice"))
            db.commit()
        finally:
            db.close()
        assert (await set_ep(_req("alice"), did, tags=" Tech , Kernel ,tech"))["tags"] == ["Tech", "Kernel"]
        assert (await set_ep(_req("alice"), did, tags=""))["tags"] == []
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_documents_library_tag_facet_and_filter():
    """Library lists each doc's tags, counts them (multi-valued), and filters by
    one tag with word-boundary matching (Kernel ≠ a doc tagged only Tech)."""
    previous = _bind_test_db()
    try:
        lib_ep = _endpoint("GET", "/api/documents/library")
        a, b, c = (str(uuid.uuid4()) for _ in range(3))
        db = _TS()
        try:
            db.query(Document).delete()
            d1 = _doc(a, "K1", "alice"); d1.tags = "Tech,Kernel"
            d2 = _doc(b, "K2", "alice"); d2.tags = "Tech"
            d3 = _doc(c, "Loose note", "alice")  # untagged
            db.add_all([d1, d2, d3]); db.commit()
        finally:
            db.close()
        full = await lib_ep(_req("alice"), search=None, language=None, tag=None,
                            sort="recent", offset=0, limit=20, archived=False)
        assert full["tags"].get("Tech") == 2
        assert full["tags"].get("Kernel") == 1
        by_id = {d["id"]: d for d in full["documents"]}
        assert by_id[a]["tags"] == ["Tech", "Kernel"]
        assert by_id[c]["tags"] == []
        tech = await lib_ep(_req("alice"), search=None, language=None, tag="Tech",
                            sort="recent", offset=0, limit=20, archived=False)
        assert {d["id"] for d in tech["documents"]} == {a, b}
        kernel = await lib_ep(_req("alice"), search=None, language=None, tag="Kernel",
                              sort="recent", offset=0, limit=20, archived=False)
        assert {d["id"] for d in kernel["documents"]} == {a}   # boundary match, not d2
        untagged = await lib_ep(_req("alice"), search=None, language=None, tag="Untagged",
                                sort="recent", offset=0, limit=20, archived=False)
        assert {d["id"] for d in untagged["documents"]} == {c}
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_suggest_tags_uses_model_and_neighbours(monkeypatch):
    """The suggester feeds the model the tags related notes already carry and
    returns its picks (1-3)."""
    import src.task_endpoint as te
    import src.llm_core as llm
    previous = _bind_test_db()
    try:
        ep = _endpoint("GET", "/api/document/{doc_id}/suggest-tags")
        target, n1, n2 = (str(uuid.uuid4()) for _ in range(3))
        db = _TS()
        try:
            db.query(Document).delete()
            db.add(_doc(target, "Kernel scheduler notes", "alice", content="CFS scheduler"))
            d1 = _doc(n1, "Kernel memory mgmt", "alice"); d1.tags = "Tech,Kernel"
            d2 = _doc(n2, "Linux boot", "alice"); d2.tags = "Tech"
            db.add_all([d1, d2]); db.commit()
        finally:
            db.close()
        monkeypatch.setattr(content_rag, "semantic_search", lambda *a, **k: [{"kb_id": n1}, {"kb_id": n2}])
        monkeypatch.setattr(te, "resolve_task_endpoint", lambda owner=None: ("http://x", "m", {}))
        captured = {}

        def _fake_llm(url, model, messages, **kw):
            captured["prompt"] = messages[0]["content"]
            return '{"tags": ["Tech", "Kernel"], "reason": "matches your kernel notes"}'

        monkeypatch.setattr(llm, "llm_call", _fake_llm)
        res = await ep(_req("alice"), target)
        assert res["suggestions"] == ["Tech", "Kernel"]
        assert "Kernel" in res["neighbours"]
        assert "Tech" in captured["prompt"]            # neighbour tag grounds the prompt
    finally:
        droutes.SessionLocal = previous


@pytest.mark.asyncio
async def test_suggest_tags_fallback_without_model(monkeypatch):
    """No utility model configured → graceful suggestions=[] (UI falls back to
    the manual tag editor), never an error."""
    import src.task_endpoint as te
    previous = _bind_test_db()
    try:
        ep = _endpoint("GET", "/api/document/{doc_id}/suggest-tags")
        did = str(uuid.uuid4())
        db = _TS()
        try:
            db.query(Document).delete()
            db.add(_doc(did, "Some note", "alice"))
            db.commit()
        finally:
            db.close()
        monkeypatch.setattr(content_rag, "semantic_search", lambda *a, **k: [])
        monkeypatch.setattr(te, "resolve_task_endpoint", lambda owner=None: (None, None, None))
        res = await ep(_req("alice"), did)
        assert res["suggestions"] == []
        assert "model" in res["reason"].lower()
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

    # document.js: gallery resolve hook and the `[[` autocomplete.
    assert "resolveGalleryEmbeds(preview)" in document
    assert "_wikiACUpdate" in document and "_wikiACKeydown" in document
    assert "/api/documents/titles" in document
    assert "doc-wikilink-ac" in document
    # the cache-poison guard on the titles fetch (empty/failure must not stick)
    assert "_wikiTitlesPromise = null" in document

    # fork.css: styles for both (kept out of style.css for upstream alignment).
    for sel in (".md-gallery-embed", ".doc-wikilink-ac"):
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
