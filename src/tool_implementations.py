"""
tool_implementations.py

Extracted tool implementation functions (do_* and helpers) from agent_tools.py.
These handle the actual execution logic for each tool type.
"""

import logging
from typing import Dict, Optional

from src.tool_utils import get_mcp_manager  # noqa: F401  re-exported: tests patch src.tool_implementations.get_mcp_manager

# System-domain tools were extracted to src/tools/system.py (slice 1,
# #4082/#4071); the admin manage_* tools live in src/agent_tools/admin_tools
# after the upstream registry migration (#3629). Re-imported here so this
# module stays a working facade.
from src.tools.system import (  # noqa: F401
    do_manage_skills, _skill_dump, do_manage_tasks,
    do_api_call, do_app_api,
    _APP_API_BLOCKLIST_PREFIXES, _APP_API_BLOCKLIST_METHOD_PATH,
)
# Admin manage_* tools (endpoints/mcp/webhooks/tokens/settings) live in
# src/agent_tools/admin_tools after the upstream registry migration (#3629).
# Re-exported lazily via __getattr__: src.agent_tools.__init__ imports this
# facade at top level, so a eager `from src.agent_tools.admin_tools import`
# here would re-enter the partially-initialized agent_tools package (circular).
_ADMIN_TOOL_SYMBOLS = (
    "do_manage_endpoints", "do_manage_mcp", "do_manage_webhooks",
    "do_manage_tokens", "do_manage_settings",
    "_MCP_DENIED_COMMANDS", "_validate_mcp_command", "_mcp_allowed_commands",
)


def __getattr__(name):
    if name in _ADMIN_TOOL_SYMBOLS:
        from src.agent_tools import admin_tools
        return getattr(admin_tools, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
# Cookbook (model serving) domain extracted to src/tools/cookbook.py
# (slice 1, #4082/#4071). Re-imported here so this module stays a working
# facade. cookbook.py pulls `_internal_headers` / `_INTERNAL_BASE` back
# function-locally from this facade (which re-exports them from _common).
from src.tools.cookbook import (  # noqa: F401
    do_download_model, do_serve_model, do_list_served_models,
    do_stop_served_model, do_tail_serve_output, do_list_downloads,
    do_cancel_download, do_search_hf_models, do_adopt_served_model,
    do_list_cookbook_servers, do_list_serve_presets, do_serve_preset,
    do_list_cached_models,
    _cookbook_servers, _resolve_cookbook_host, _cookbook_env_for_host,
    _infer_serve_port, _infer_serve_host, _ensure_served_endpoint,
    _cookbook_register_task, _cookbook_apply_retry_suggestion,
    _scan_running_model_processes, _cookbook_kill_session,
    _MODEL_PROCESS_PATTERNS,
    _string_arg, _validate_cookbook_ssh_target,
)
# Search domain extracted to src/tools/search.py (slice 1, #4082/#4071).
# Re-imported here so this module stays a working facade.
from src.tools.search import do_search_chats  # noqa: F401
# Notes domain extracted to src/tools/notes.py (slice 1, #4082/#4071).
from src.tools.notes import do_manage_notes  # noqa: F401
# Calendar domain extracted to src/tools/calendar.py (slice 1, #4082/#4071).
from src.tools.calendar import do_manage_calendar  # noqa: F401
# Image domain extracted to src/tools/image.py (slice 1, #4082/#4071).
from src.tools.image import do_edit_image  # noqa: F401
# Research domain extracted to src/tools/research.py (slice 1, #4082/#4071).
from src.tools.research import do_manage_research, do_trigger_research  # noqa: F401
# Contacts domain extracted to src/tools/contacts.py (slice 1, #4082/#4071).
from src.tools.contacts import do_resolve_contact, do_manage_contact  # noqa: F401
# Vault domain extracted to src/tools/vault.py (slice 1, #4082/#4071).
from src.tools.vault import (  # noqa: F401
    _load_vault_config, _run_bw,
    do_vault_search, do_vault_get, do_vault_unlock,
)
# Shared helpers live in src/tools/_common.py. Re-exported here so the
# function-local `from src.tool_implementations import _INTERNAL_BASE` (and
# friends) used by domain files still resolve through this facade.
from src.tools._common import _parse_tool_args, _INTERNAL_BASE, _internal_headers  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Active email state
# ---------------------------------------------------------------------------

# When the user has an email reader window open, the frontend tells the
# backend about it on each chat submit. Email tools can resolve "this email"
# without guessing a UID. Cleared between requests by chat_routes.
_active_email_ref: Optional[Dict[str, str]] = None


def set_active_email(uid: Optional[str], folder: Optional[str] = None, account: Optional[str] = None,
                     subject: Optional[str] = None, sender: Optional[str] = None) -> None:
    """Stash the email currently open in the UI. None clears it."""
    global _active_email_ref
    if not uid:
        _active_email_ref = None
        return
    _active_email_ref = {
        "uid": str(uid),
        "folder": str(folder or "INBOX"),
        "account": str(account or ""),
        "subject": str(subject or ""),
        "from": str(sender or ""),
    }


def get_active_email() -> Optional[Dict[str, str]]:
    return _active_email_ref


def clear_active_email() -> None:
    global _active_email_ref
    _active_email_ref = None


# ===========================================================================
# Fork net-new tools (not present upstream)
# ===========================================================================
#
# These do_* tools are unique to this fork and were NOT extracted into the
# upstream src/tools/ package by PR #4423. They remain defined here so they
# stay importable as `from src.tool_implementations import do_manage_gallery`
# (etc.) — src/tool_execution.py and src/agent_tools/__init__ import them from
# this module. Most of their dependencies are imported lazily inside each
# function (kept as-is to minimize merge risk); the few module-level names
# they need beyond the shim's imports are declared here.
import asyncio  # noqa: E402  (do_manage_files uses asyncio.to_thread/create_task)
from typing import Any  # noqa: E402  (do_manage_health annotates Dict[str, Any])

# Citation anchor map used by do_search_files to link each hit to an openable
# source the user can verify.
_CITE_ANCHOR = {"file": "file", "book": "book", "document": "document",
                "image": "gallery", "knowledge": "file"}


async def do_search_files(content: str, owner: Optional[str] = None) -> Dict:
    """Search the user's content — Files (uploaded docs), Books (PDF/EPUB), and
    authored Documents — by keyword/tag AND semantic recall.

    Combines DETERMINISTIC keyword+tag matching (the user's verifiable path) with
    semantic (RAG) recall across every store, and returns each hit with its
    filename + id so the answer can CITE the source the user can open and verify.
    """
    import json as _json
    from src import file_store as _fs, book_store as _bs, content_rag as _rag

    try:
        try:
            args = _json.loads(content) if content and content.strip().startswith("{") else {"query": content}
        except Exception:
            args = {"query": content}
        query = str(args.get("query") or args.get("q") or "").strip()
        tags = args.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        try:
            limit = int(args.get("limit") or 12)
        except (TypeError, ValueError):
            limit = 12
        if not query and not tags:
            return {"error": "Provide a 'query' (and/or 'tags') to search your files.", "exit_code": 1}

        results, seen = [], set()

        def _add(kid, name, excerpt, tag_list, kind):
            if not kid or kid in seen:
                return
            seen.add(kid)
            results.append({"id": kid, "filename": name, "excerpt": excerpt or "",
                            "tags": tag_list or [], "kind": kind})

        # Deterministic keyword + tag match over Files and Books.
        for f in _fs.search(owner, q=query, tags=tags or [], limit=limit):
            _add(f.get("id"), f.get("filename") or f.get("id"), f.get("excerpt"), f.get("tags"), "file")
        for b in _bs.list_books(owner, query=query, limit=limit):
            _add(b.get("id"), b.get("filename") or b.get("title"), b.get("excerpt"), b.get("tags"), "book")

        # Semantic recall (RAG) across every store — fold in extra hits.
        if query:
            for h in _rag.semantic_search(owner, query, k=8):
                _add(h.get("kb_id"), h.get("filename") or h.get("kb_id"),
                     (h.get("text") or "").strip().replace("\n", " ")[:240],
                     [], h.get("kind") or "file")

        if not results:
            label = query or ", ".join(tags or [])
            return {"output": f"No files matched '{label}'.", "exit_code": 0, "files": []}

        lines = [
            f"Found {len(results)} file(s). CITE the source so the user can open and "
            f"verify the original — link each as [filename](#<kind>-<id>):"
        ]
        files = []
        for f in results[:limit]:
            kid, name = f["id"], f["filename"] or f["id"]
            anchor = _CITE_ANCHOR.get(f.get("kind") or "file", "file")
            excerpt = (f.get("excerpt") or "").strip().replace("\n", " ")[:240]
            tag_str = ", ".join(f.get("tags") or []) or "—"
            lines.append(f"• [{name}](#{anchor}-{kid}) — tags: {tag_str}\n  {excerpt}")
            files.append({"id": kid, "filename": name, "kind": f.get("kind"), "tags": f.get("tags") or []})
        return {"output": "\n".join(lines), "exit_code": 0, "files": files}
    except Exception as e:
        logger.error(f"search_files error: {e}")
        return {"error": f"search_files failed: {e}", "exit_code": 1}

async def do_manage_files(content: str, owner: Optional[str] = None) -> Dict:
    """MANAGE the user's content stores. ADD a chat-attached/uploaded file by its
    upload_id — routed by type: images/videos go to the Gallery (optionally into
    a named `album`), PDFs/EPUBs to Books, everything else to the Files store.
    For Files items you can also correct/replace text (edit), append, set tags
    (retag), AI-generate tags (autotag), or delete. Find files first with
    search_files to get an id."""
    from src import file_store as _fs

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments for manage_files.", "exit_code": 1}

    action = (args.get("action") or "").replace("-", "_").strip().lower()
    _aliases = {
        "update": "edit", "replace": "edit", "set_text": "edit", "edit_text": "edit",
        "add_text": "append", "tag": "retag", "set_tags": "retag", "tags": "retag",
        "suggest_tags": "autotag", "auto_tag": "autotag", "remove": "delete",
        "store": "add", "save": "add", "ingest": "add", "upload": "add",
    }
    _aliases["label"] = "rename"
    action = _aliases.get(action, action)
    if action not in {"add", "edit", "append", "retag", "autotag", "rename", "delete"}:
        return {"error": "manage_files action must be one of: add, edit, append, retag, "
                         "autotag, rename, delete. (To read or find files, use search_files.)",
                "exit_code": 1}

    if action == "add":
        upload_id = str(args.get("upload_id") or args.get("attachment_id") or "").strip()
        if not upload_id:
            return {"error": "add requires 'upload_id' — the id of an uploaded/attached file "
                             "(listed in the [user attachments] context of the message). To add "
                             "plain text to an EXISTING file use append.", "exit_code": 1}
        try:
            from src.constants import BASE_DIR, UPLOAD_DIR
            from src.upload_handler import UploadHandler
            info = UploadHandler(BASE_DIR, UPLOAD_DIR).resolve_upload(upload_id, owner=owner)
            if not info or not info.get("path"):
                return {"error": f"Upload '{upload_id}' not found (or not accessible).", "exit_code": 1}
            tags = args.get("tags") or ""
            if isinstance(tags, list):
                tags = ",".join(str(t).strip() for t in tags if str(t).strip())
            original = str(info.get("name") or info.get("original_name") or upload_id)
            filename = str(args.get("filename") or args.get("title") or "").strip() or original
            if "." not in filename and "." in original:
                filename = f"{filename}.{original.rsplit('.', 1)[1]}"
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

            # Route by type: media -> Gallery (optional album), books -> Books,
            # everything else -> the Files store.
            if ext in {"png", "jpg", "jpeg", "webp", "gif", "mp4", "mov", "webm", "mkv", "m4v"}:
                from src import gallery_ingest
                album = str(args.get("album") or "").strip() or None
                res = await asyncio.to_thread(
                    gallery_ingest.ingest_upload, owner, upload_id,
                    album=album, tags=str(tags), title=str(args.get("title") or ""),
                )
                where = f" into album '{album}'" if album else " to the gallery"
                dup = " (already there)" if res.get("duplicate") else ""
                return {"output": f"Added '{filename}'{where}{dup}.", "exit_code": 0,
                        "file": {"id": res.get("id"), "filename": filename, "album_id": res.get("album_id")}}

            if ext in {"pdf", "epub"}:
                from src import book_store
                with open(info["path"], "rb") as fh:
                    data = fh.read()
                rec = await asyncio.to_thread(
                    book_store.add_book, owner, filename, data, mime=info.get("mime") or "",
                )
                return {"output": f"Added '{rec.get('filename')}' to your Books.", "exit_code": 0,
                        "file": {"id": rec.get("id"), "filename": rec.get("filename")}}

            # Files store — store the row FAST (no extraction); extract + index +
            # auto-tag in the background so a slow OCR never 504s the chat request.
            rec = await asyncio.to_thread(
                _fs.ingest, owner, file_path=info["path"], filename=filename,
                mime=info.get("mime"), upload_id=upload_id, source="chat", tags=tags, extract=False,
            )
            if not rec or not rec.get("id"):
                return {"error": "Failed to add the file.", "exit_code": 1}
            fid = rec["id"]
            already = bool((rec.get("excerpt") or "").strip())
            wants_autotag = not str(tags).strip() and not rec.get("ai_tags")

            async def _finish():
                try:
                    r2 = None
                    if not already:
                        r2 = await asyncio.to_thread(_fs.extract_and_index, owner, fid)
                    if wants_autotag and ((r2 or rec).get("excerpt") or "").strip():
                        await asyncio.to_thread(_fs.generate_ai_tags, owner, fid)
                except Exception as _bg_e:
                    logger.warning(f"manage_files add: background indexing failed: {_bg_e}")

            if not already or wants_autotag:
                asyncio.create_task(_finish())
            note = (" Text extraction and indexing are finishing in the background." if not already
                    else " It is stored, indexed, and searchable.")
            return {"output": f"Added '{rec.get('filename')}' to your Files.{note}", "exit_code": 0,
                    "file": {"id": fid, "filename": rec.get("filename"), "tags": rec.get("tags")}}
        except Exception as e:
            return {"error": f"manage_files add failed: {e}", "exit_code": 1}

    try:
        # Resolve the target Files item — explicit id, else a unique filename match.
        fid = str(args.get("id") or args.get("file_id") or args.get("kb_id") or "").strip()
        if not fid:
            query = str(args.get("query") or args.get("filename") or args.get("file") or "").strip()
            if not query:
                return {"error": "Provide the file 'id' (from search_files) or a "
                                 "'query'/'filename' to identify the file.", "exit_code": 1}
            matches = _fs.search(owner, q=query, limit=10)
            exact = [m for m in matches if (m.get("filename") or "").lower() == query.lower()]
            cands = exact or matches
            if not cands:
                return {"error": f"No file matched '{query}'.", "exit_code": 1}
            if len(cands) > 1 and not exact:
                listing = "; ".join(f"{m.get('filename')} (id {(m.get('id') or '')[:8]}…)" for m in cands[:6])
                return {"error": f"'{query}' matched {len(cands)} files — say which by id: {listing}",
                        "exit_code": 1}
            fid = cands[0].get("id")

        if action == "edit":
            text = args.get("text")
            if text is None:
                return {"error": "edit requires 'text' (the new full content).", "exit_code": 1}
            rec = _fs.update_text(owner, fid, str(text), filename=args.get("filename"))
            if not rec:
                return {"error": "File not found.", "exit_code": 1}
            return {"output": f"Updated '{rec.get('filename')}' ({len(str(text))} chars) and re-indexed it.",
                    "exit_code": 0, "file": {"id": rec.get("id"), "filename": rec.get("filename")}}

        if action == "append":
            text = str(args.get("text") or "").strip()
            if not text:
                return {"error": "append requires 'text' to add.", "exit_code": 1}
            rec = _fs.append_text(owner, fid, text)
            if not rec:
                return {"error": "File not found.", "exit_code": 1}
            return {"output": f"Appended to '{rec.get('filename')}' and re-indexed it.",
                    "exit_code": 0, "file": {"id": rec.get("id"), "filename": rec.get("filename")}}

        if action == "retag":
            rec = _fs.set_tags(owner, fid, args.get("tags") or "")
            if not rec:
                return {"error": "File not found.", "exit_code": 1}
            return {"output": f"Set tags on '{rec.get('filename')}': "
                              f"{', '.join(rec.get('tags') or []) or '(none)'}.",
                    "exit_code": 0,
                    "file": {"id": rec.get("id"), "filename": rec.get("filename"), "tags": rec.get("tags")}}

        if action == "autotag":
            rec = _fs.generate_ai_tags(owner, fid)
            if not rec:
                return {"error": "File not found.", "exit_code": 1}
            return {"output": f"AI tags for '{rec.get('filename')}': "
                              f"{', '.join(rec.get('ai_tags') or []) or '(none generated)'}.",
                    "exit_code": 0,
                    "file": {"id": rec.get("id"), "filename": rec.get("filename"), "ai_tags": rec.get("ai_tags")}}

        if action == "rename":
            new_name = str(args.get("filename") or args.get("name") or args.get("title") or "").strip()
            if not new_name:
                return {"error": "rename requires 'filename' (the new name).", "exit_code": 1}
            rec = _fs.rename(owner, fid, new_name)
            if not rec:
                return {"error": "File not found.", "exit_code": 1}
            return {"output": f"Renamed to '{rec.get('filename')}'.", "exit_code": 0,
                    "file": {"id": rec.get("id"), "filename": rec.get("filename")}}

        # delete
        rec = _fs.get(owner, fid)
        name = (rec or {}).get("filename") or fid
        if not _fs.delete(owner, fid):
            return {"error": "File not found.", "exit_code": 1}
        return {"output": f"Deleted '{name}' from your Files.", "exit_code": 0}
    except Exception as e:
        logger.error(f"manage_files error: {e}")
        return {"error": f"manage_files failed: {e}", "exit_code": 1}

async def do_manage_gallery(content: str, owner: Optional[str] = None) -> Dict:
    """MANAGE the user's Gallery (photos + videos): tag, rename, favorite/hide,
    delete, create albums, and file media into them ("sort"). Find items with
    action=list (by album/tag/recent). Identify an item by id (preferred, e.g.
    from a manage_files add result or a list) or a unique name/keyword."""
    from core.database import SessionLocal, GalleryImage, GalleryAlbum

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments for manage_gallery.", "exit_code": 1}

    action = (args.get("action") or "").replace("-", "_").strip().lower()
    _aliases = {
        "tags": "tag", "set_tags": "tag", "retag": "tag", "label": "rename",
        "title": "rename", "star": "favorite", "unstar": "unfavorite",
        "album": "create_album", "new_album": "create_album",
        "sort": "move", "organize": "move", "add_to_album": "move", "file": "move",
        "remove": "delete", "search": "list", "find": "list",
    }
    action = _aliases.get(action, action)
    _valid = {"list", "tag", "rename", "create_album", "move", "favorite",
              "unfavorite", "hide", "unhide", "delete"}
    if action not in _valid:
        return {"error": f"manage_gallery action must be one of: {', '.join(sorted(_valid))}.",
                "exit_code": 1}

    def _img_dict(im):
        return {"id": im.id, "name": im.prompt or im.filename, "media_type": im.media_type or "image",
                "tags": [t.strip() for t in (im.tags or "").split(",") if t.strip()],
                "album_id": im.album_id, "favorite": bool(im.favorite), "hidden": bool(im.hidden)}

    def _find_or_create_album(db, name):
        name = (name or "").strip()
        if not name:
            return None
        import uuid as _uuid
        q = db.query(GalleryAlbum).filter(GalleryAlbum.name == name)
        if owner is not None:
            q = q.filter(GalleryAlbum.owner == owner)
        a = q.first()
        if a:
            return a.id
        a = GalleryAlbum(id=str(_uuid.uuid4()), name=name, owner=owner)
        db.add(a)
        db.flush()
        return a.id

    db = SessionLocal()
    try:
        if action == "create_album":
            name = str(args.get("name") or args.get("album") or "").strip()
            if not name:
                return {"error": "create_album requires 'name'.", "exit_code": 1}
            aid = _find_or_create_album(db, name)
            db.commit()
            return {"output": f"Album '{name}' is ready.", "exit_code": 0, "album": {"id": aid, "name": name}}

        if action == "list":
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            if owner is not None:
                q = q.filter(GalleryImage.owner == owner)
            mt = (args.get("media_type") or "").strip().lower()
            if mt in ("image", "video"):
                q = q.filter(GalleryImage.media_type == mt)
            term = str(args.get("query") or args.get("q") or args.get("tag") or "").strip()
            if term:
                like = f"%{term}%"
                q = q.filter(GalleryImage.prompt.ilike(like) | GalleryImage.tags.ilike(like)
                             | GalleryImage.ai_tags.ilike(like))
            album = str(args.get("album") or "").strip()
            if album:
                aq = db.query(GalleryAlbum).filter(GalleryAlbum.name == album)
                if owner is not None:
                    aq = aq.filter(GalleryAlbum.owner == owner)
                a = aq.first()
                q = q.filter(GalleryImage.album_id == (a.id if a else "__none__"))
            rows = q.order_by(GalleryImage.created_at.desc()).limit(30).all()
            return {"output": f"{len(rows)} item(s).", "exit_code": 0,
                    "items": [_img_dict(im) for im in rows]}

        # Item actions — resolve the target image (id preferred, else unique name/keyword).
        img_id = str(args.get("id") or args.get("image_id") or "").strip()
        img = None
        if img_id:
            img = db.query(GalleryImage).filter(GalleryImage.id == img_id).first()
            if img and owner is not None and img.owner != owner:
                img = None
        else:
            term = str(args.get("query") or args.get("name") or args.get("filename") or "").strip()
            if not term:
                return {"error": "Provide the item 'id' (from list) or a 'query'/'name' to identify it.",
                        "exit_code": 1}
            mq = db.query(GalleryImage).filter(GalleryImage.is_active == True)
            if owner is not None:
                mq = mq.filter(GalleryImage.owner == owner)
            like = f"%{term}%"
            cands = mq.filter(GalleryImage.prompt.ilike(like) | GalleryImage.tags.ilike(like)).limit(10).all()
            if not cands:
                return {"error": f"No photo/video matched '{term}'.", "exit_code": 1}
            if len(cands) > 1:
                listing = "; ".join(f"{(c.prompt or c.filename)} (id {c.id[:8]}…)" for c in cands[:6])
                return {"error": f"'{term}' matched {len(cands)} items — say which by id: {listing}",
                        "exit_code": 1}
            img = cands[0]
        if not img:
            return {"error": "Gallery item not found.", "exit_code": 1}

        if action == "tag":
            tags = args.get("tags") or args.get("tag") or ""
            if isinstance(tags, list):
                tags = ", ".join(str(t).strip() for t in tags if str(t).strip())
            seen, clean = set(), []
            for t in str(tags).split(","):
                t = t.strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    clean.append(t)
            img.tags = ", ".join(clean)
        elif action == "rename":
            new_name = str(args.get("name") or args.get("filename") or args.get("title") or "").strip()
            if not new_name:
                return {"error": "rename requires 'name'.", "exit_code": 1}
            img.prompt = new_name[:300]
        elif action == "move":
            album = str(args.get("album") or args.get("name") or "").strip()
            img.album_id = _find_or_create_album(db, album) if album else None
        elif action == "favorite":
            img.favorite = True
        elif action == "unfavorite":
            img.favorite = False
        elif action == "hide":
            img.hidden = True
        elif action == "unhide":
            img.hidden = False
        elif action == "delete":
            img.is_active = False

        db.commit()
        db.refresh(img)
        verb = {"tag": "Tagged", "rename": "Renamed", "move": "Filed", "favorite": "Favorited",
                "unfavorite": "Unfavorited", "hide": "Hid", "unhide": "Unhid", "delete": "Deleted"}[action]
        return {"output": f"{verb} '{img.prompt or img.filename}'.", "exit_code": 0, "item": _img_dict(img)}
    except Exception as e:
        db.rollback()
        logger.error(f"manage_gallery error: {e}")
        return {"error": f"manage_gallery failed: {e}", "exit_code": 1}
    finally:
        db.close()

async def do_send_ping(content: str, owner: Optional[str] = None) -> Dict:
    """Send an immediate ntfy notification using the saved ntfy integration."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        args = {"message": content}

    from src.integrations import load_integrations
    from src.ntfy_client import resolve_ntfy_integration, send_ntfy_notification
    from src.settings import get_user_setting, load_settings

    message = str(args.get("message") or args.get("body") or args.get("text") or "").strip()
    title = str(args.get("title") or "Iris").strip() or "Iris"
    if not message:
        return {"error": "message is required", "exit_code": 1}
    if len(message) > 3800:
        message = message[:3800] + "\n... (truncated)"

    settings = load_settings()
    topic = str(
        args.get("topic")
        or get_user_setting("reminder_ntfy_topic", owner or "", settings.get("reminder_ntfy_topic"))
        or "Reminders"
    ).strip()
    priority = str(args.get("priority") or "high").strip() or "high"
    tags = str(args.get("tags") or "bell").strip()

    integration = resolve_ntfy_integration(
        load_integrations(),
        topic=topic,
        integration_id=get_user_setting("reminder_ntfy_integration_id", owner or "", "") or None,
    )
    if not integration:
        return {
            "error": "No enabled ntfy integration found. Configure Settings -> Integrations -> ntfy first.",
            "exit_code": 1,
        }

    result = await send_ntfy_notification(
        integration,
        topic,
        message,
        title=title,
        priority=priority,
        tags=tags,
    )
    # Mirror the ping into the durable feed so it's not just an ephemeral push.
    if result.get("exit_code") == 0:
        try:
            from src import pings_store
            pings_store.create(owner, title, message, kind="ping", source="ntfy")
        except Exception:
            pass
    return result

async def do_manage_health(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_health tool calls: log meals/weight, check habits, query.

    Backed by src/health_store.py (the same store the Health panel UI uses), so
    anything logged here shows up in the UI for the same user and vice versa.
    Actions: create_habit, update_habit (rename / set emoji-icon / category /
    color / cadence on an existing habit), delete_habit, check_habit (pass a
    past `date` like yesterday to backfill), list_habits, habit_heatmap,
    log_meal, update_meal / delete_meal (fix a logged entry by meal_id — get the
    id from action=calories), log_weight, log_training, calories, weight_trend,
    set_profile, summary.
    """
    from src import health_store as hs

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "").replace("-", "_").strip().lower()
    _ALIASES = {
        "meal": "log_meal", "add_meal": "log_meal", "eat": "log_meal",
        "weight": "log_weight", "add_weight": "log_weight",
        "training": "log_training", "workout": "log_training", "log_workout": "log_training",
        "habit": "check_habit", "mark_habit": "check_habit", "complete_habit": "check_habit",
        "add_habit": "create_habit", "new_habit": "create_habit", "make_habit": "create_habit",
        "rename_habit": "update_habit", "edit_habit": "update_habit", "change_habit": "update_habit",
        "set_habit": "update_habit", "set_emoji": "update_habit", "set_icon": "update_habit",
        "delete_habit": "delete_habit", "remove_habit": "delete_habit", "archive_habit": "delete_habit",
        "habits": "list_habits",
        "daily_calories": "calories", "kcal": "calories",
        "weight_progress": "weight_trend",
        "profile": "set_profile", "set_goal": "set_profile", "goal": "set_profile",
    }
    action = _ALIASES.get(action, action)
    ow = owner or ""

    def _resolve_habit_id(value) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
        name = str(value).strip().lower()
        for h in hs.list_habits(ow, include_archived=True):
            if h["name"].strip().lower() == name:
                return h["id"]
        return None

    try:
        if action == "log_meal":
            # Description is optional — default to a generic label rather than
            # erroring, so "log 500 kcal" works without a name.
            desc = str(args.get("description") or args.get("food") or args.get("name") or "").strip() or "Meal"
            meal = hs.log_meal(
                ow, desc, args.get("kcal") or args.get("calories") or 0,
                protein_g=args.get("protein_g"), carbs_g=args.get("carbs_g"),
                fat_g=args.get("fat_g"), sugar_g=args.get("sugar_g"),
                notes=args.get("notes") or "", source="agent",
                photo_upload_id=args.get("photo_upload_id"),
            )
            day = hs.daily_calories(ow)
            tgt = f" (today: {day['total_kcal']} kcal{', target ' + str(day['target_kcal']) if day.get('target_kcal') else ''})"
            return {"output": f"Logged {meal['description']} — {meal['kcal']} kcal{tgt}.", "meal": meal, "exit_code": 0}

        if action in ("update_meal", "edit_meal", "delete_meal"):
            # Fix a logged meal (Iris can mis-estimate). meal_id comes from the
            # `calories` action (each meal in `meals` has an id) or a log_meal result.
            try:
                mid = int(args.get("meal_id") or args.get("id"))
            except (TypeError, ValueError):
                return {"error": f"{action} needs a numeric meal_id — get it from action=calories (each meal has an id).", "exit_code": 1}
            if action == "delete_meal":
                ok = hs.delete_meal(ow, mid)
                return {"output": ("Deleted meal." if ok else "Meal not found."), "exit_code": 0 if ok else 1}
            fields = {k: args.get(k) for k in ("description", "kcal", "protein_g", "carbs_g", "fat_g", "sugar_g", "notes", "photo_upload_id")
                      if args.get(k) is not None}
            if not fields:
                return {"error": "Nothing to update — pass description/kcal/protein_g/carbs_g/fat_g/sugar_g/notes.", "exit_code": 1}
            meal = hs.update_meal(ow, mid, **fields)
            if meal is None:
                return {"error": "Meal not found.", "exit_code": 1}
            return {"output": f"Updated meal #{meal['id']}: {meal['description']} — {meal['kcal']} kcal.", "meal": meal, "exit_code": 0}

        if action == "log_weight":
            kg = args.get("kg") or args.get("weight")
            if kg is None:
                return {"error": "kg is required", "exit_code": 1}
            entry = hs.log_weight(ow, kg, notes=args.get("notes") or "")
            trend = hs.weight_trend(ow)
            d = trend.get("delta_kg")
            extra = f" ({'+' if (d or 0) > 0 else ''}{d} kg over {trend.get('count', 1)} readings)" if d is not None else ""
            return {"output": f"Logged weight {entry['kg']} kg{extra}.", "weight": entry, "exit_code": 0}

        if action == "log_training":
            kind = str(args.get("kind") or args.get("type") or "").strip()
            sess = hs.log_training(
                ow, kind, duration_min=args.get("duration_min"),
                rpe=args.get("rpe"), kcal_burned=args.get("kcal_burned") or args.get("calories_burned"),
                summary=args.get("summary") or "",
            )
            burned = sess.get("kcal_burned")
            extra = f" (~{burned} kcal burned)" if burned else ""
            return {"output": f"Logged training: {kind or 'session'}{extra}.", "session": sess, "exit_code": 0}

        if action == "create_habit":
            name = str(args.get("name") or args.get("habit") or "").strip()
            if not name:
                return {"error": "habit name is required", "exit_code": 1}
            try:
                habit = hs.create_habit(
                    ow, name,
                    icon=args.get("icon") or "", category=args.get("category") or "",
                    cadence=args.get("cadence") or "daily",
                )
            except ValueError as e:
                return {"error": str(e), "exit_code": 1}
            return {"output": f"Created habit “{habit['name']}”.", "habit": habit, "exit_code": 0}

        if action == "update_habit":
            # Rename and/or restyle an EXISTING habit (emoji, category, color,
            # cadence). Target: prefer an explicit habit/habit_id; if only
            # 'name' is given it identifies the target — unless a new_name was
            # also passed, in which case 'name' is treated as the new name.
            target_ref = args.get("habit") if args.get("habit") is not None else args.get("habit_id")
            rename_to = args.get("new_name") or args.get("rename_to") or args.get("rename")
            if target_ref is None:
                target_ref = args.get("name")
            else:
                rename_to = rename_to or args.get("name")
            hid = _resolve_habit_id(target_ref)
            if hid is None:
                return {"error": "Unknown habit. Pass the existing habit name or id, plus new_name and/or icon/category/color/cadence to change.", "exit_code": 1}
            fields: Dict[str, Any] = {}
            if rename_to:
                fields["name"] = str(rename_to).strip()
            for k in ("icon", "category", "cadence", "color", "target_time", "description"):
                v = args.get(k)
                if v is not None:
                    fields[k] = v.strip() if isinstance(v, str) else v
            if not fields:
                return {"error": "Nothing to change. Pass new_name and/or icon (emoji), category, color, cadence.", "exit_code": 1}
            habit = hs.update_habit(ow, hid, **fields)
            if habit is None:
                return {"error": "Habit not found", "exit_code": 1}
            changed = ", ".join(f"{k}→{v!r}" for k, v in fields.items())
            return {"output": f"Updated habit “{habit['name']}” ({changed}).", "habit": habit, "exit_code": 0}

        if action == "delete_habit":
            hid = _resolve_habit_id(args.get("habit") or args.get("habit_id") or args.get("name"))
            if hid is None:
                return {"error": "Unknown habit. Pass an existing habit name or id to delete.", "exit_code": 1}
            ok = hs.delete_habit(ow, hid)
            return {"output": "Habit deleted." if ok else "Habit not found.", "deleted": ok, "exit_code": 0 if ok else 1}

        if action == "check_habit":
            hid = _resolve_habit_id(args.get("habit") or args.get("habit_id") or args.get("name"))
            if hid is None:
                return {"error": "Unknown habit. Create it first (action=create_habit) or pass an existing name/id.", "exit_code": 1}
            res = hs.set_habit_day(ow, hid, day=args.get("day"), done=args.get("done"))
            if res is None:
                return {"error": "Habit not found", "exit_code": 1}
            return {"output": f"Habit marked {'done' if res['done'] else 'not done'} for {res['day']}.", **res, "exit_code": 0}

        if action == "list_habits":
            habits = hs.list_habits(ow)
            lines = [f"- {h['name']}: {'✓ today' if h['done_today'] else 'pending'}, streak {h['streak']}" for h in habits]
            return {"output": "Habits:\n" + ("\n".join(lines) if lines else "(none)"), "habits": habits, "exit_code": 0}

        if action == "habit_heatmap":
            hid = _resolve_habit_id(args.get("habit") or args.get("habit_id") or args.get("name"))
            if hid is None:
                return {"error": "Unknown habit. Pass habit name or id.", "exit_code": 1}
            hm = hs.habit_heatmap(ow, hid, days=args.get("days") or 365)
            return {"output": f"{hm['total']} completions, current streak {hm['streak']} days.", "heatmap": hm, "exit_code": 0}

        if action == "calories":
            day = hs.daily_calories(ow, day=args.get("date") or args.get("day"))
            rem = f", {day['remaining_kcal']} remaining" if day.get("remaining_kcal") is not None else ""
            lines = [f"{day['day']}: {day['total_kcal']} kcal from {day['meal_count']} meals{rem}."]
            # Enumerate meals WITH their id so Iris can update_meal/delete_meal a wrong entry.
            for m in (day.get("meals") or []):
                macros = " ".join(
                    f"{lbl}{round(m[k])}g" for lbl, k in (("P", "protein_g"), ("C", "carbs_g"), ("F", "fat_g"))
                    if m.get(k) is not None
                )
                lines.append(f"  #{m['id']} {m['description']} — {m['kcal']} kcal{(' · ' + macros) if macros else ''}")
            return {"output": "\n".join(lines), **day, "exit_code": 0}

        if action == "weight_trend":
            trend = hs.weight_trend(ow, days=args.get("days") or 90)
            if not trend.get("count"):
                return {"output": "No weight entries yet.", **trend, "exit_code": 0}
            return {"output": f"{trend['first_kg']} → {trend['last_kg']} kg ({'+' if trend['delta_kg'] > 0 else ''}{trend['delta_kg']} kg).", **trend, "exit_code": 0}

        if action == "set_profile":
            fields = {k: args.get(k) for k in (
                "height_cm", "date_of_birth", "sex", "activity_level",
                "target_kg", "target_weekly_loss_kg", "daily_kcal_target",
            ) if args.get(k) is not None}
            if not fields:
                return {"error": "Pass at least one profile field (height_cm, date_of_birth, sex, activity_level, target_kg, target_weekly_loss_kg, daily_kcal_target).", "exit_code": 1}
            hs.set_profile(ow, **fields)
            t = hs.tdee(ow)
            tgt = f" Daily target: {t['target_kcal']} kcal." if t.get("target_kcal") else ""
            return {"output": f"Health profile updated.{tgt}", "profile": hs.get_profile(ow), "tdee": t, "exit_code": 0}

        if action == "summary":
            return {
                "output": "Health summary.",
                "habits": hs.list_habits(ow),
                "calories": hs.daily_calories(ow),
                "weight": hs.weight_trend(ow, days=90),
                "tdee": hs.tdee(ow),
                "exit_code": 0,
            }

        return {"error": f"Unknown action '{action}'. Use create_habit, update_habit, delete_habit, check_habit, list_habits, habit_heatmap, log_meal, log_weight, log_training, calories, weight_trend, set_profile, or summary.", "exit_code": 1}
    except ValueError as e:
        return {"error": str(e), "exit_code": 1}
    except Exception as e:
        return {"error": f"manage_health failed: {e}", "exit_code": 1}

async def do_manage_books(content: str, owner: Optional[str] = None) -> Dict:
    """Manage Iris's owner-scoped EPUB/PDF books and reading progress."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "list").replace("-", "_").strip().lower()
    if action in {"search", "find"}:
        action = "list"
    if action in {"open", "view", "get"}:
        action = "read"

    from src import book_reader

    try:
        if action == "list":
            query = str(args.get("query") or args.get("search") or "")
            rows = book_reader.list_books(owner, query, int(args.get("limit") or 20))
            if not rows:
                return {"response": "No EPUB/PDF books found.", "books": [], "exit_code": 0}
            lines = [f"Found {len(rows)} book(s):"]
            for row in rows:
                progress = row.get("progress") or {}
                location = ""
                if progress.get("updated_at"):
                    label = "page" if row.get("kind") == "pdf" else "chapter"
                    location = f" — last read {label} {int(progress.get('chapter_index') or 0) + 1}"
                lines.append(f"- {row.get('title') or row.get('path')} ({row.get('path')}){location}")
            return {"response": "\n".join(lines), "books": rows, "exit_code": 0}

        if action == "read":
            path = str(args.get("path") or "").strip()
            if not path:
                return {"error": "read requires path", "exit_code": 1}
            chapter_index = int(args.get("chapter_index") or args.get("page_index") or 0)
            result = book_reader.read_book_location(owner, path, chapter_index)
            book = result.get("book") or {}
            chapter = result.get("chapter") or {}
            title = book.get("title") or path
            response = f"{title}\n{chapter.get('title', '')}\n\n{chapter.get('text_excerpt') or ''}"
            return {"response": response.strip(), "book": book, "chapter": chapter, "exit_code": 0}

        if action == "progress":
            path = str(args.get("path") or "").strip()
            if not path:
                return {"error": "progress requires path", "exit_code": 1}
            progress = book_reader.save_progress(
                owner,
                path,
                chapter_index=int(args.get("chapter_index") or args.get("page_index") or 0),
                scroll_percent=float(args.get("scroll_percent") or 0),
                chapter_title=str(args.get("chapter_title") or ""),
                title=str(args.get("title") or ""),
                author=str(args.get("author") or ""),
                kind=str(args.get("kind") or ""),
            )
            return {
                "response": f"Saved reading progress for {progress.get('title') or path}.",
                "progress": progress,
                "exit_code": 0,
            }

        return {"error": "Unknown action. Use list, read, or progress.", "exit_code": 1}
    except Exception as e:
        return {"error": str(getattr(e, "detail", None) or e), "exit_code": 1}
