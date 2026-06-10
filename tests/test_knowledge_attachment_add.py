"""manage_knowledge action=add — store chat attachments in the knowledge base.

Before this, "add" silently aliased to append (which needs an EXISTING file),
so when a user attached an image and asked Iris to save it as knowledge the
agent fell back to the admin-only write_file tool and failed. Now: the agent
sees the message's attachment upload_ids (untrusted context, agent mode) and
manage_knowledge add ingests an upload owner-scoped, mirroring POST
/api/knowledge.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

FAKE_INFO = {"id": "up123.png", "path": "", "name": "image.png", "mime": "image/png"}


def _run(args, owner="kim"):
    from src import tool_implementations as ti
    return asyncio.get_event_loop().run_until_complete(
        ti.do_manage_knowledge(json.dumps(args), owner=owner)
    ) if False else asyncio.run(ti.do_manage_knowledge(json.dumps(args), owner=owner))


def test_add_requires_upload_id():
    r = _run({"action": "add"})
    assert r["exit_code"] == 1
    assert "upload_id" in r["error"]


def test_add_ingests_attachment_with_friendly_title():
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(b"fakepng")
    tmp.close()
    info = dict(FAKE_INFO, path=tmp.name)
    captured = {}

    def fake_ingest(owner, *, file_path, filename, mime, upload_id, source, tags, extract=True):
        captured.update(owner=owner, filename=filename, upload_id=upload_id,
                        source=source, tags=tags, mime=mime)
        return {"id": "kb999", "filename": filename,
                "tags": [t for t in str(tags).split(",") if t],
                "ai_tags": "x", "excerpt": "img"}

    try:
        with patch("src.upload_handler.UploadHandler.resolve_upload", return_value=info), \
             patch("src.knowledge_base.ingest", side_effect=fake_ingest):
            r = _run({"action": "add", "upload_id": "up123.png",
                      "filename": "Cloud Tree", "tags": ["the-witness", "screenshot"]})
        assert r["exit_code"] == 0, r
        # Friendly title keeps the original extension so kind detection works.
        assert captured["filename"] == "Cloud Tree.png"
        assert captured["tags"] == "the-witness,screenshot"
        assert captured["source"] == "chat"
        assert captured["upload_id"] == "up123.png"
        assert captured["owner"] == "kim"
        assert r["file"]["id"] == "kb999"
    finally:
        os.unlink(tmp.name)


def test_add_is_owner_scoped():
    """resolve_upload is called WITH the owner — a foreign upload id resolves
    to None and the add fails instead of leaking another user's file."""
    with patch("src.upload_handler.UploadHandler.resolve_upload", return_value=None) as rp:
        r = _run({"action": "add", "upload_id": "someone-elses.png"}, owner="kim")
    assert r["exit_code"] == 1
    assert rp.call_args.kwargs.get("owner") == "kim"


def test_action_aliases():
    # store/save/ingest/upload mean add...
    r = _run({"action": "store"})
    assert "upload_id" in r.get("error", "")
    r = _run({"action": "save"})
    assert "upload_id" in r.get("error", "")
    # ...while add_text still means append (needs an existing file id).
    r = _run({"action": "add_text", "text": "x"})
    assert r["exit_code"] == 1 and "id" in r["error"]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_agent_sees_attachment_upload_ids():
    helpers = _read("routes/chat_helpers.py")
    assert "attachment_meta" in helpers
    assert "user attachments on this message" in helpers
    # untrusted context (names are user data), agent mode only
    block = helpers.split("user attachments on this message")[0][-900:]
    assert "agent_mode and preprocessed.attachment_meta" in block
    assert "untrusted_context_message" in helpers


def test_agent_docs_teach_storing_attachments():
    assert '"action":"add"' in _read("src/agent_loop.py")
    schemas = _read("src/tool_schemas.py")
    assert '"add", "edit", "append", "retag", "autotag", "delete"' in schemas
    assert "upload_id" in schemas.split('"manage_knowledge"', 1)[1][:2400]
    assert "STORE a user-attached/uploaded file" in _read("src/tool_index.py")


# ── App-store write guard + deferred extraction (the Gemma/Qwen incidents) ───

def test_write_guard_refuses_knowledge_store_with_teaching_error():
    """A weaker model 'saved' knowledge by writing a stub .md straight into
    data/knowledge_files/ (no DB row, no index) — the guard refuses and the
    error teaches manage_knowledge add so the agent recovers in-turn."""
    from src.knowledge_base import _kb_files_dir
    from src.tool_execution import app_store_write_guard
    bad = os.path.realpath(os.path.join(_kb_files_dir(), "someone", "Stub.md"))
    msg = app_store_write_guard(bad)
    assert msg and "manage_knowledge" in msg and "add" in msg


def test_write_guard_covers_all_app_stores_but_not_plain_data():
    from src.constants import BOOKS_DIR, CHROMA_DIR, DATA_DIR, UPLOAD_DIR
    from src.tool_execution import app_store_write_guard
    for store in (CHROMA_DIR, UPLOAD_DIR, BOOKS_DIR):
        p = os.path.realpath(os.path.join(str(store), "x.bin"))
        assert app_store_write_guard(p), store
    # ordinary files under data/ stay writable (the agent's workspace)
    ok = os.path.realpath(os.path.join(str(DATA_DIR), "report.md"))
    assert app_store_write_guard(ok) is None


def test_write_file_tool_end_to_end_refusal():
    from src.agent_tools.filesystem_tools import WriteFileTool
    from src.knowledge_base import _kb_files_dir
    bad = os.path.join(_kb_files_dir(), "owner", "Stub.md")
    r = asyncio.run(WriteFileTool().execute(f"{bad}\n# stub", {"workspace": None}))
    assert r["exit_code"] == 1
    assert "manage_knowledge" in r["error"]


def test_add_defers_extraction_to_background():
    """The add must return fast (extract=False) — inline image OCR 504'd the
    whole chat request — and schedule extraction/indexing in the background."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(b"x")
    tmp.close()
    info = dict(FAKE_INFO, path=tmp.name)
    calls = {"ingest": None, "extract": 0}

    def fake_ingest(owner, **kw):
        calls["ingest"] = kw
        return {"id": "kb1", "filename": kw["filename"], "tags": [], "ai_tags": "", "excerpt": ""}

    def fake_extract(owner, kb_id):
        calls["extract"] += 1
        return {"id": kb_id, "excerpt": "text", "filename": "f"}

    async def main():
        from src import tool_implementations as ti
        with patch("src.upload_handler.UploadHandler.resolve_upload", return_value=info), \
             patch("src.knowledge_base.ingest", side_effect=fake_ingest), \
             patch("src.knowledge_base.extract_and_index", side_effect=fake_extract), \
             patch("src.knowledge_base.generate_ai_tags", return_value=None):
            r = await ti.do_manage_knowledge(
                json.dumps({"action": "add", "upload_id": "up123.png"}), owner="kim")
            assert r["exit_code"] == 0, r
            assert calls["ingest"]["extract"] is False
            assert "background" in r["output"]
            await asyncio.sleep(0.05)
        assert calls["extract"] == 1

    try:
        asyncio.run(main())
    finally:
        os.unlink(tmp.name)


def test_file_tool_docs_steer_away_from_user_content():
    idx = _read("src/tool_index.py")
    assert "NEVER for saving user content" in idx
    assert "manage_knowledge (action=add" in idx
    assert "NEVER use this to save user content" in _read("src/agent_loop.py")
