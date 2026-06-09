"""Functional tests for the native knowledge base (src/knowledge_base.py).

Covers ingest + text extraction, content-hash dedupe, keyword + tag search,
owner-scoping, tag editing, and delete — against the conftest in-memory SQLite.
RAG indexing is stubbed (no ChromaDB needed); the ingest path must record + be
searchable regardless of RAG availability.
"""
import pytest

pytest.importorskip("sqlalchemy")

from core.database import Base, engine  # noqa: E402
from src import knowledge_base as kb  # noqa: E402


@pytest.fixture(autouse=True)
def _tables_and_no_rag(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    # Don't touch a real vector store, and keep KB file copies in a temp dir
    # (never write into the repo's data/ during tests).
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: None)
    monkeypatch.setattr("src.knowledge_base._data_dir", lambda: str(tmp_path / "kbdata"))
    yield


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_ingest_extracts_text_and_records(tmp_path):
    path = _write(tmp_path, "huawei.md", "# Huawei Ascend\nThe Ascend P7 specs and notes.")
    rec = kb.ingest("kb-ingest", file_path=path, filename="huawei.md", mime="text/markdown")
    assert rec["filename"] == "huawei.md"
    assert rec["owner"] == "kb-ingest"
    assert rec["sha256"]
    assert "Ascend" in rec["excerpt"]
    assert rec["source"] == "upload"
    assert rec["indexed"] is False  # RAG stubbed out -> not indexed, but still recorded


def test_ingest_dedupes_by_content_hash(tmp_path):
    p1 = _write(tmp_path, "a.md", "same content here")
    r1 = kb.ingest("kb-dedupe", file_path=p1, filename="a.md")
    # identical bytes under a different name -> same row, no duplicate
    p2 = _write(tmp_path, "b.md", "same content here")
    r2 = kb.ingest("kb-dedupe", file_path=p2, filename="b.md")
    assert r1["id"] == r2["id"]
    assert len(kb.search("kb-dedupe", q="same content")) == 1


def test_search_by_query_and_tags_is_owner_scoped(tmp_path):
    p1 = _write(tmp_path, "huawei.md", "Huawei Ascend phone review")
    p2 = _write(tmp_path, "apple.md", "Apple iPhone review")
    kb.ingest("kb-search-a", file_path=p1, filename="huawei.md", tags="phones, china")
    kb.ingest("kb-search-a", file_path=p2, filename="apple.md", tags="phones, usa")
    kb.ingest("kb-search-b", file_path=p1, filename="huawei.md")  # another owner's copy

    # q matches extracted text (case-insensitive)
    assert {h["filename"] for h in kb.search("kb-search-a", q="ascend")} == {"huawei.md"}
    # tag filter narrows results
    assert {h["filename"] for h in kb.search("kb-search-a", tags=["china"])} == {"huawei.md"}
    # both match "review"; owner-scoped (the other owner's copy is excluded)
    hits = kb.search("kb-search-a", q="review")
    assert {h["filename"] for h in hits} == {"huawei.md", "apple.md"}
    assert all(h["owner"] == "kb-search-a" for h in hits)


def test_set_and_list_tags_owner_scoped(tmp_path):
    path = _write(tmp_path, "x.md", "content x")
    rec = kb.ingest("kb-tags", file_path=path, filename="x.md", tags="a, b")
    updated = kb.set_tags("kb-tags", rec["id"], "c, d, c")  # dedupes
    assert updated["tags"] == ["c", "d"]
    assert kb.set_tags("kb-other", rec["id"], "hacked") is None  # can't tag another owner's file
    assert set(kb.list_tags("kb-tags")) >= {"c", "d"}


def test_delete_is_owner_scoped(tmp_path):
    path = _write(tmp_path, "x.md", "deletable content")
    rec = kb.ingest("kb-del", file_path=path, filename="x.md")
    assert kb.delete("kb-other", rec["id"]) is False
    assert kb.delete("kb-del", rec["id"]) is True
    assert kb.search("kb-del", q="deletable") == []


def test_get_returns_full_text_owner_scoped(tmp_path):
    path = _write(tmp_path, "doc.md", "FULL BODY " * 100)  # ~1000 chars
    rec = kb.ingest("kb-get", file_path=path, filename="doc.md")
    full = kb.get("kb-get", rec["id"])
    assert full is not None
    assert len(full["text"]) > len(rec["excerpt"])  # full text, not just the excerpt
    assert full["upload_id"] is None  # ingested by path here; upload_id set via the route
    assert kb.get("kb-other", rec["id"]) is None  # owner-scoped


def test_knowledge_routes_expose_expected_paths():
    """The deterministic (non-LLM) file API the user uses to find + open files."""
    from routes.knowledge_routes import setup_knowledge_routes

    class _UH:
        def resolve_upload(self, *a, **k):
            return None

    paths = {r.path for r in setup_knowledge_routes(_UH()).routes}
    assert "/api/knowledge" in paths
    assert "/api/knowledge/tags" in paths
    assert "/api/knowledge/{kb_id}" in paths
    assert "/api/knowledge/{kb_id}/tags" in paths


def test_search_knowledge_tool_registered_everywhere():
    from src.agent_tools import TOOL_TAGS
    from src.tool_index import ALWAYS_AVAILABLE, ASSISTANT_ALWAYS_AVAILABLE, BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_parsing import _TOOL_NAME_MAP

    assert "search_knowledge" in TOOL_TAGS
    assert "search_knowledge" in ALWAYS_AVAILABLE
    assert "search_knowledge" in ASSISTANT_ALWAYS_AVAILABLE
    assert "search_knowledge" in BUILTIN_TOOL_DESCRIPTIONS
    assert _TOOL_NAME_MAP["kb_search"] == "search_knowledge"
    assert "search_knowledge" in {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}


def test_do_search_knowledge_returns_citable_results(tmp_path):
    import asyncio
    import json
    from src.tool_implementations import do_search_knowledge

    p = _write(tmp_path, "huawei.md", "Huawei Ascend P7 — display nits and battery specs")
    kb.ingest("kb-tool", file_path=p, filename="huawei.md", tags="phones")

    res = asyncio.run(do_search_knowledge(json.dumps({"query": "ascend"}), owner="kb-tool"))
    assert res["exit_code"] == 0
    assert "#knowledge-" in res["output"]  # cite the source so the user can open + verify
    assert any(f["filename"] == "huawei.md" for f in res["files"])

    miss = asyncio.run(do_search_knowledge(json.dumps({"query": "zzz-nomatch"}), owner="kb-tool"))
    assert miss["exit_code"] == 0 and miss["files"] == []


def test_ingest_copies_file_and_is_openable(tmp_path):
    src = _write(tmp_path, "doc.md", "openable body text")
    rec = kb.ingest("kb-copy", file_path=src, filename="doc.md")
    assert rec["has_file"] is True
    assert rec["url"] == f"/api/knowledge/{rec['id']}/raw"
    # the KB owns a copy of the bytes -> always openable, owner-scoped
    path = kb.file_abspath("kb-copy", rec["id"])
    assert path and open(path, encoding="utf-8").read() == "openable body text"
    assert kb.file_abspath("kb-other", rec["id"]) is None


def test_ingest_audio_is_stored_without_text(tmp_path):
    p = tmp_path / "voice.ogg"
    p.write_bytes(b"OggS fake audio bytes")
    rec = kb.ingest("kb-audio", file_path=str(p), filename="voice.ogg", mime="audio/ogg", tags="voicemail")
    assert rec["filename"] == "voice.ogg"
    assert rec["excerpt"] == ""        # no text extracted (no noisy markitdown attempt)
    assert rec["has_file"] is True     # still stored + openable
    # still findable by filename + tag (the deterministic path)
    assert {h["filename"] for h in kb.search("kb-audio", tags=["voicemail"])} == {"voice.ogg"}


# ---- editing (the new content-edit + auto-tag surface) --------------------

def test_update_text_rewrites_text_file_and_reindexes(tmp_path):
    path = _write(tmp_path, "note.md", "OLD body about apples")
    rec = kb.ingest("kb-edit", file_path=path, filename="note.md")
    old_sha = rec["sha256"]

    updated = kb.update_text("kb-edit", rec["id"], "NEW body about oranges")
    assert updated is not None
    assert "oranges" in updated["excerpt"] and "apples" not in updated["excerpt"]
    assert updated["sha256"] != old_sha  # content (and hash) changed

    # the KB-owned bytes on disk were rewritten -> open shows the new content
    abspath = kb.file_abspath("kb-edit", rec["id"])
    assert open(abspath, encoding="utf-8").read() == "NEW body about oranges"

    # search reflects the edit: new content found, old content gone
    assert {h["id"] for h in kb.search("kb-edit", q="oranges")} == {rec["id"]}
    assert kb.search("kb-edit", q="apples") == []


def test_update_text_is_owner_scoped(tmp_path):
    path = _write(tmp_path, "x.md", "private content")
    rec = kb.ingest("kb-edit-own", file_path=path, filename="x.md")
    assert kb.update_text("kb-other", rec["id"], "hacked") is None
    # untouched
    assert "private" in kb.get("kb-edit-own", rec["id"])["text"]


def test_append_text_adds_to_content(tmp_path):
    path = _write(tmp_path, "log.md", "line one")
    rec = kb.ingest("kb-append", file_path=path, filename="log.md")
    kb.append_text("kb-append", rec["id"], "line two")
    full = kb.get("kb-append", rec["id"])["text"]  # _to_dict carries excerpt; get() has full text
    assert "line one" in full and "line two" in full
    # empty append is a no-op (still returns the row)
    assert kb.append_text("kb-append", rec["id"], "   ")["id"] == rec["id"]


def test_generate_ai_tags_stores_llm_tags(tmp_path, monkeypatch):
    path = _write(tmp_path, "spec.md", "Huawei Ascend P7 display and battery specifications")
    rec = kb.ingest("kb-ai", file_path=path, filename="spec.md")
    assert rec["ai_tags"] == []  # not generated at ingest in this unit (route schedules it)

    monkeypatch.setattr("src.knowledge_base._generate_tags_via_llm",
                        lambda text, owner: ["huawei", "smartphone", "specs"])
    out = kb.generate_ai_tags("kb-ai", rec["id"])
    assert out["ai_tags"] == ["huawei", "smartphone", "specs"]
    # findable by an AI tag
    assert {h["id"] for h in kb.search("kb-ai", tags=["smartphone"])} == {rec["id"]}

    # LLM returns nothing -> ai_tags left unchanged (no crash)
    monkeypatch.setattr("src.knowledge_base._generate_tags_via_llm", lambda text, owner: [])
    assert kb.generate_ai_tags("kb-ai", rec["id"])["ai_tags"] == ["huawei", "smartphone", "specs"]


def test_edit_and_delete_clean_up_rag(tmp_path, monkeypatch):
    """update_text drops stale vectors then re-adds; delete drops them — so edited/
    removed text can't resurface in recall."""
    calls = {"removed": [], "added": 0}

    class _FakeRAG:
        def add_document(self, text, meta):
            calls["added"] += 1
            return True

        def delete_by_kb_id(self, kb_id):
            calls["removed"].append(kb_id)
            return 1

    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: _FakeRAG())
    path = _write(tmp_path, "r.md", "indexed body")
    rec = kb.ingest("kb-rag", file_path=path, filename="r.md")
    assert rec["indexed"] is True  # fake RAG indexed it

    kb.update_text("kb-rag", rec["id"], "edited body")
    assert rec["id"] in calls["removed"]  # stale chunks removed before re-index

    kb.delete("kb-rag", rec["id"])
    assert calls["removed"].count(rec["id"]) >= 2  # removed again on delete


def test_manage_knowledge_tool_edit_append_retag_delete(tmp_path):
    import asyncio
    import json
    from src.tool_implementations import do_manage_knowledge

    path = _write(tmp_path, "topic.md", "first draft")
    rec = kb.ingest("kb-mk", file_path=path, filename="topic.md")

    # edit by id
    r = asyncio.run(do_manage_knowledge(json.dumps(
        {"action": "edit", "id": rec["id"], "text": "second draft"}), owner="kb-mk"))
    assert r["exit_code"] == 0
    assert kb.get("kb-mk", rec["id"])["text"] == "second draft"

    # append by query (filename)
    r = asyncio.run(do_manage_knowledge(json.dumps(
        {"action": "append", "query": "topic.md", "text": "addendum"}), owner="kb-mk"))
    assert r["exit_code"] == 0 and "addendum" in kb.get("kb-mk", rec["id"])["text"]

    # retag
    r = asyncio.run(do_manage_knowledge(json.dumps(
        {"action": "retag", "id": rec["id"], "tags": ["a", "b"]}), owner="kb-mk"))
    assert kb.get("kb-mk", rec["id"])["tags"] == ["a", "b"]

    # delete
    r = asyncio.run(do_manage_knowledge(json.dumps(
        {"action": "delete", "id": rec["id"]}), owner="kb-mk"))
    assert r["exit_code"] == 0 and kb.get("kb-mk", rec["id"]) is None

    # bad action is rejected
    bad = asyncio.run(do_manage_knowledge(json.dumps({"action": "nope", "id": "x"}), owner="kb-mk"))
    assert bad["exit_code"] == 1


def test_manage_knowledge_tool_registered_everywhere():
    from src.agent_tools import TOOL_TAGS
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_parsing import _TOOL_NAME_MAP

    assert "manage_knowledge" in TOOL_TAGS
    assert "manage_knowledge" in BUILTIN_TOOL_DESCRIPTIONS
    assert _TOOL_NAME_MAP["manage_knowledge"] == "manage_knowledge"
    assert "manage_knowledge" in {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}


def test_knowledge_routes_expose_edit_paths():
    from routes.knowledge_routes import setup_knowledge_routes

    class _UH:
        def resolve_upload(self, *a, **k):
            return None

    routes = setup_knowledge_routes(_UH()).routes
    paths = {r.path for r in routes}
    assert "/api/knowledge/{kb_id}" in paths
    assert "/api/knowledge/{kb_id}/autotag" in paths
    # the {kb_id} path now serves PUT (edit) in addition to GET
    methods = {m for r in routes if getattr(r, "path", "") == "/api/knowledge/{kb_id}" for m in (r.methods or set())}
    assert "PUT" in methods and "GET" in methods
