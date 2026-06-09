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


def test_migrate_from_vault(tmp_path, monkeypatch):
    import hashlib as _h
    from core.database import SessionLocal, IrisVaultFile

    owner = "kb-mig"
    vroot = tmp_path / "vault"
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_VAULT_ROOT", str(vroot))
    # vault layout: <root>/<owner>/<rel_path>
    (vroot / owner / "notes").mkdir(parents=True)
    (vroot / owner / "notes" / "huawei.md").write_text("Huawei Ascend specs", encoding="utf-8")
    (vroot / owner / "plain.txt").write_text("just a plain note", encoding="utf-8")
    db = SessionLocal()
    try:
        for rel in ("notes/huawei.md", "plain.txt"):
            db.add(IrisVaultFile(
                id=_h.sha256(f"{owner}/{rel}".encode()).hexdigest(),
                owner=owner, rel_path=rel, title=rel,
                sha256=_h.sha256(rel.encode()).hexdigest(),
            ))
        db.commit()
    finally:
        db.close()

    res = kb.migrate_from_vault(owner)
    assert res["processed"] == 2
    assert res["total"] == 2
    # imported files are searchable + openable, tagged as a migration
    assert {h["filename"] for h in kb.search(owner, q="ascend")} == {"huawei.md"}
    assert all(h["source"] == "vault-migration" for h in kb.search(owner, q=""))
    # idempotent: re-running adds nothing (content-hash dedupe)
    res2 = kb.migrate_from_vault(owner)
    assert res2["total"] == 2
