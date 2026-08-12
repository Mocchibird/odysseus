"""manage_files action=add — store chat attachments, routed by type.

The agent sees the message's attachment upload_ids (untrusted context, agent
mode) and `manage_files add` ingests an upload owner-scoped — routing
images/videos to the Gallery (optional album), PDFs/EPUBs to Books, and
everything else to the Files store. The app-store write guard refuses raw
writes into those stores and teaches manage_files so the agent recovers in-turn.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _run(args, owner="kim"):
    from src import tool_implementations as ti
    return asyncio.run(ti.do_manage_files(json.dumps(args), owner=owner))


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_add_requires_upload_id():
    r = _run({"action": "add"})
    assert r["exit_code"] == 1
    assert "upload_id" in r["error"]


def test_action_aliases_store_save_mean_add():
    for action in ("store", "save", "ingest", "upload"):
        r = _run({"action": action})
        assert "upload_id" in r.get("error", ""), action


def test_add_routes_document_to_file_store_with_deferred_extraction():
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    tmp.write(b"hello")
    tmp.close()
    info = {"id": "up1.txt", "path": tmp.name, "name": "notes.txt", "mime": "text/plain"}
    calls = {"ingest": None, "owner": None, "extract": 0}

    def fake_ingest(owner, **kw):
        calls["ingest"] = kw
        calls["owner"] = owner
        return {"id": "f1", "filename": kw["filename"], "tags": [], "ai_tags": "", "excerpt": ""}

    def fake_extract(owner, file_id):
        calls["extract"] += 1
        return {"id": file_id, "excerpt": "text", "filename": "f"}

    async def main():
        from src import tool_implementations as ti
        with patch("src.upload_handler.UploadHandler.resolve_upload", return_value=info), \
             patch("src.file_store.ingest", side_effect=fake_ingest), \
             patch("src.file_store.extract_and_index", side_effect=fake_extract), \
             patch("src.file_store.generate_ai_tags", return_value=None):
            r = await ti.do_manage_files(
                json.dumps({"action": "add", "upload_id": "up1.txt", "filename": "My Notes"}), owner="kim")
            assert r["exit_code"] == 0, r
            assert calls["ingest"]["extract"] is False        # returns fast, defers OCR
            assert calls["ingest"]["filename"] == "My Notes.txt"  # friendly title keeps ext
            assert calls["ingest"]["source"] == "chat"
            assert calls["owner"] == "kim"
            assert "Files" in r["output"]
            await asyncio.sleep(0.05)
        assert calls["extract"] == 1

    try:
        asyncio.run(main())
    finally:
        os.unlink(tmp.name)


def test_add_routes_image_to_gallery_with_album():
    info = {"id": "up2.png", "path": "/tmp/x.png", "name": "shot.png", "mime": "image/png"}
    captured = {}

    def fake_ingest_upload(owner, upload_id, *, album=None, tags="", title=""):
        captured.update(owner=owner, upload_id=upload_id, album=album)
        return {"id": "img1", "filename": "abc.png", "album_id": "alb1"}

    with patch("src.upload_handler.UploadHandler.resolve_upload", return_value=info), \
         patch("src.gallery_ingest.ingest_upload", side_effect=fake_ingest_upload):
        r = _run({"action": "add", "upload_id": "up2.png", "album": "The Witness"})
    assert r["exit_code"] == 0, r
    assert captured["album"] == "The Witness"
    assert captured["upload_id"] == "up2.png"
    assert "album 'The Witness'" in r["output"]



def test_rename_action():
    with patch("src.file_store.rename", return_value={"id": "f1", "filename": "new.txt"}) as rn:
        r = _run({"action": "rename", "id": "f1", "filename": "new.txt"})
    assert r["exit_code"] == 0
    assert r["file"]["filename"] == "new.txt"
    assert rn.call_args.args[2] == "new.txt"  # (owner, file_id, new_name)


def test_add_is_owner_scoped():
    with patch("src.upload_handler.UploadHandler.resolve_upload", return_value=None) as rp:
        r = _run({"action": "add", "upload_id": "someone-elses.png"}, owner="kim")
    assert r["exit_code"] == 1
    assert rp.call_args.kwargs.get("owner") == "kim"


# ── App-store write guard ────────────────────────────────────────────────────

def test_write_guard_refuses_files_store_with_teaching_error():
    from src.file_store import _files_dir
    from src.tool_execution import app_store_write_guard
    bad = os.path.realpath(os.path.join(_files_dir(), "someone", "Stub.md"))
    msg = app_store_write_guard(bad)
    assert msg and "manage_files" in msg and "add" in msg


def test_write_guard_covers_all_app_stores_but_not_plain_data():
    from src.constants import CHROMA_DIR, DATA_DIR, UPLOAD_DIR, GENERATED_IMAGES_DIR
    from src.tool_execution import app_store_write_guard
    for store in (CHROMA_DIR, UPLOAD_DIR, GENERATED_IMAGES_DIR):
        p = os.path.realpath(os.path.join(str(store), "x.bin"))
        assert app_store_write_guard(p), store
    ok = os.path.realpath(os.path.join(str(DATA_DIR), "report.md"))
    assert app_store_write_guard(ok) is None


def test_write_file_tool_end_to_end_refusal():
    from src.agent_tools.filesystem_tools import WriteFileTool
    from src.file_store import _files_dir
    bad = os.path.join(_files_dir(), "owner", "Stub.md")
    r = asyncio.run(WriteFileTool().execute(f"{bad}\n# stub", {"workspace": None}))
    assert r["exit_code"] == 1
    assert "manage_files" in r["error"]


# ── Agent docs steer storing attachments to manage_files ─────────────────────

def test_agent_docs_teach_storing_attachments():
    # Fork tool schemas/descriptions live in *_fork.py modules and are merged
    # into the upstream registries at import (see docs/fork-additive-policy.md).
    # Assert on the assembled runtime registries so the guard is independent of
    # where/how the schema dicts are formatted.
    assert '"action":"add"' in _read("src/agent_loop.py")
    import src.agent_tools  # noqa: F401  (warms the import chain in order)
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    mf = next(s["function"] for s in FUNCTION_TOOL_SCHEMAS
              if s.get("function", {}).get("name") == "manage_files")
    props = mf["parameters"]["properties"]
    assert props["action"]["enum"] == ["add", "edit", "append", "retag", "autotag", "delete"]
    assert "upload_id" in props
    assert "STORE a user-attached/uploaded file" in BUILTIN_TOOL_DESCRIPTIONS["manage_files"]


def test_file_tool_docs_steer_away_from_user_content():
    idx = _read("src/tool_index.py") + _read("src/tool_index_fork.py")
    assert "NEVER for saving user content" in idx
    assert "manage_files (action=add" in idx
    assert "NEVER use this to save user content" in _read("src/agent_loop.py")
