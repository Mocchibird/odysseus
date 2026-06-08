"""Keep-style quick notes, stored in the vault.

Each "keep" is one markdown-with-frontmatter file under the user's own
``<vault_subdir>/Keeps/`` folder — so the notes live in the vault (indexed,
searchable, editable, and visible to Iris) rather than a separate store:

    ---
    title: Groceries
    color: "#e8c33a"
    pinned: false
    archived: false
    reminder: 2026-06-23T08:00
    tags: [ajax, groceries]
    created: 2026-06-04T21:00:00
    updated: 2026-06-04T21:00:00
    ---
    - [ ] Ajax spray
    - [ ] 1000 wood

Checklists are plain markdown task lists in the body (vault-native; Iris reads
and ticks them) — a plain note is just body text. This module is the single
source of truth for keep CRUD; the web router (iris_web/routers/keeps.py) and
the keep_* MCP tools both call it. It deliberately lives in ``_iris`` so the
MCP layer can use it without importing the web layer.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import mcp  # shared FastMCP instance (also safe to import web-side)

_SUBFOLDER = "Keeps"
_ID_RE = re.compile(r"^[a-z0-9]{6,16}$")   # ids we generate; guards path traversal


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _truthy(v: object) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def subdir_for_user(user_id: int) -> str:
    """vault_subdir for a users.id (e.g. ``users/<discord_id>``); '' if none
    (owner / single-user installs write keeps at the vault root)."""
    from _iris.core import get_vault_index  # noqa: PLC0415
    idx = get_vault_index()
    row = idx.conn.execute(
        "SELECT vault_subdir FROM users WHERE id = ?", (int(user_id),),
    ).fetchone()
    sub = (row["vault_subdir"] if row else "") or ""
    return sub.rstrip("/")


def _keeps_dir(subdir: str) -> Path:
    # Resolve against the live index's root (not get_vault_root(), which reads
    # the import-frozen iris_config.VAULT_ROOT — stale across tests). subdir
    # comes from the DB and the kid is validated, but keep a traversal guard.
    from _iris.core import get_vault_index  # noqa: PLC0415
    root = Path(get_vault_index()._root).resolve()
    d = (root / (subdir or "") / _SUBFOLDER).resolve()
    if root != d and root not in d.parents:
        raise ValueError("refusing to access keeps dir outside the vault")
    return d


def _parse(path: Path) -> Optional[dict]:
    from _iris.core import read_text, split_frontmatter  # noqa: PLC0415
    try:
        text = read_text(path)
    except Exception:
        return None
    fm, body = split_frontmatter(text)
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "id": path.stem,
        "title": str(fm.get("title") or ""),
        "color": str(fm.get("color") or ""),
        "pinned": _truthy(fm.get("pinned")),
        "archived": _truthy(fm.get("archived")),
        "reminder": str(fm.get("reminder") or ""),
        "tags": [str(t) for t in tags],
        "created": str(fm.get("created") or ""),
        "updated": str(fm.get("updated") or ""),
        "body": body.strip("\n"),
    }


def _write(subdir: str, kid: str, keep: dict) -> Path:
    from _iris.core import dump_frontmatter, _notify_index_of_write  # noqa: PLC0415
    d = _keeps_dir(subdir)
    d.mkdir(parents=True, exist_ok=True)
    fm = {
        "title": (keep.get("title") or "").replace("\n", " ").strip(),
        "color": keep.get("color") or "",
        "pinned": "true" if keep.get("pinned") else "false",
        "archived": "true" if keep.get("archived") else "false",
        "reminder": keep.get("reminder") or "",
        "tags": [str(t).strip() for t in (keep.get("tags") or []) if str(t).strip()],
        "created": keep.get("created") or _now_iso(),
        "updated": _now_iso(),
    }
    text = dump_frontmatter(fm, (keep.get("body") or "").strip("\n") + "\n")
    path = d / f"{kid}.md"
    path.write_text(text, encoding="utf-8")
    try:
        _notify_index_of_write(path, text=text)
    except Exception:
        pass
    return path


# ── public CRUD ───────────────────────────────────────────────────────────

def list_keeps(subdir: str, *, include_archived: bool = False) -> list[dict]:
    """All of the user's keeps — pinned first, then newest-updated."""
    d = _keeps_dir(subdir)
    if not d.is_dir():
        return []
    out = [k for k in (_parse(p) for p in d.glob("*.md")) if k]
    if not include_archived:
        out = [k for k in out if not k["archived"]]
    out.sort(key=lambda k: k["updated"], reverse=True)
    out.sort(key=lambda k: k["pinned"], reverse=True)
    return out


def get_keep(subdir: str, kid: str) -> Optional[dict]:
    if not _ID_RE.match(kid or ""):
        return None
    p = _keeps_dir(subdir) / f"{kid}.md"
    return _parse(p) if p.is_file() else None


def add_keep(subdir: str, *, title: str = "", body: str = "", color: str = "",
             tags: Optional[list] = None, reminder: str = "", pinned: bool = False) -> dict:
    kid = uuid.uuid4().hex[:12]
    _write(subdir, kid, {
        "title": title, "body": body, "color": color, "tags": tags or [],
        "reminder": reminder, "pinned": pinned, "created": _now_iso(),
    })
    return get_keep(subdir, kid)


def update_keep(subdir: str, kid: str, fields: dict) -> Optional[dict]:
    cur = get_keep(subdir, kid)
    if cur is None:
        return None
    merged = dict(cur)
    for k, v in (fields or {}).items():
        if v is not None:
            merged[k] = v
    _write(subdir, kid, merged)
    return get_keep(subdir, kid)


def delete_keep(subdir: str, kid: str) -> bool:
    if not _ID_RE.match(kid or ""):
        return False
    p = _keeps_dir(subdir) / f"{kid}.md"
    if not p.is_file():
        return False
    try:
        p.unlink()
        from _iris.core import _notify_index_of_write  # noqa: PLC0415
        try:
            _notify_index_of_write(p, text=None)
        except Exception:
            pass
        return True
    except Exception:
        return False


# ── MCP tools — so Iris can read/manage keeps in chat ─────────────────────

def _speaker_subdir() -> str:
    """The current speaker's vault_subdir (falls back to the owner)."""
    from _iris.core import resolve_user_id  # noqa: PLC0415
    uid = resolve_user_id(None)
    return subdir_for_user(uid) if uid else ""


def _fmt(k: dict) -> str:
    bits = [f"[{k['id']}]", k["title"] or "(untitled)"]
    if k["pinned"]:
        bits.append("📌")
    if k["archived"]:
        bits.append("(archived)")
    if k["tags"]:
        bits.append(" ".join(f"#{t}" for t in k["tags"]))
    preview = " ".join((k["body"] or "").split())[:140]
    head = " ".join(bits)
    return f"{head}\n    {preview}" if preview else head


@mcp.tool()
def keep_list(include_archived: bool = False) -> str:
    """List the user's Keep-style quick notes (the cards in the web Notes
    board). Each note shows its [id], title, pin/tag/archive flags, and a
    short body preview. Use the id with keep_update/keep_pin/keep_remove."""
    keeps = list_keeps(_speaker_subdir(), include_archived=include_archived)
    return "\n".join(_fmt(k) for k in keeps) if keeps else "No keep notes yet."


@mcp.tool()
def keep_add(title: str = "", body: str = "", tags: str = "",
             color: str = "", reminder: str = "", pinned: bool = False) -> str:
    """Add a Keep-style quick note for the user. `body` may be plain text or a
    markdown checklist (lines like `- [ ] buy milk`). `tags` is
    comma-separated. `reminder` is an optional ISO datetime."""
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    k = add_keep(_speaker_subdir(), title=title, body=body, color=color,
                 tags=tag_list, reminder=reminder, pinned=pinned)
    return f"Added keep {_fmt(k)}"


@mcp.tool()
def keep_update(keep_id: str, title: str = "", body: str = "",
                tags: str = "", color: str = "", reminder: str = "") -> str:
    """Update a keep's content by id (from keep_list). Only the non-empty
    fields you pass are changed. For pin/archive use keep_pin / keep_archive."""
    fields: dict = {}
    if title:
        fields["title"] = title
    if body:
        fields["body"] = body
    if color:
        fields["color"] = color
    if reminder:
        fields["reminder"] = reminder
    if tags:
        fields["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if not fields:
        return "Nothing to update — pass at least one field."
    k = update_keep(_speaker_subdir(), keep_id, fields)
    return f"Updated keep {_fmt(k)}" if k else f"No keep with id {keep_id}."


@mcp.tool()
def keep_pin(keep_id: str, pinned: bool = True) -> str:
    """Pin (or unpin) a keep so it sorts to the top of the board."""
    k = update_keep(_speaker_subdir(), keep_id, {"pinned": pinned})
    if not k:
        return f"No keep with id {keep_id}."
    return f"{'Pinned' if pinned else 'Unpinned'} keep {keep_id}."


@mcp.tool()
def keep_archive(keep_id: str, archived: bool = True) -> str:
    """Archive (or restore) a keep. Archived keeps hide from the default board."""
    k = update_keep(_speaker_subdir(), keep_id, {"archived": archived})
    if not k:
        return f"No keep with id {keep_id}."
    return f"{'Archived' if archived else 'Restored'} keep {keep_id}."


@mcp.tool()
def keep_remove(keep_id: str) -> str:
    """Permanently delete a keep by id (from keep_list)."""
    ok = delete_keep(_speaker_subdir(), keep_id)
    return f"Deleted keep {keep_id}." if ok else f"No keep with id {keep_id}."
