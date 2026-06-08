"""Tool registry — importing this package registers every @mcp.tool() with the FastMCP server.

Adding a new tool module:
    1. Create _iris/tools/<name>.py
    2. Import the shared mcp instance: ``from .. import mcp``
    3. Define your @mcp.tool() functions

That's it. This file auto-discovers every sibling module via pkgutil and imports
it, which triggers the @mcp.tool() decorators at import time. No registry edit
needed when adding tools.

Skipped: names starting with ``_`` (treated as private/internal).
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys


_AUTOLOAD_SKIP = set()  # add names here if a module ever needs to be excluded


# Named groups → the tool modules they contain. Used by the IRIS_TOOL_GROUPS
# env gate so an embedding host (e.g. Odysseus) can load only a subset —
# "vault,anime,warranties" — and leave Discord and other surfaces out without
# touching this repo. Unset = load everything (default, backward compatible).
_TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "vault": (
        "notes", "files", "search", "semantic", "sqlite", "links",
        "import_export", "analysis", "keeps", "people", "tasks",
    ),
    "calendar": ("calendar", "routines"),
    "health": ("health", "habits", "training", "charts"),
    "anime": ("anime",),
    "vocab": ("vocab",),
    "warranties": ("warranties",),
    "discord": ("discord",),
    "web": ("web",),
    "voice": ("voice",),
    "users": ("users",),
}


def _allowed_modules() -> set[str] | None:
    """Resolve the module allow-list from env. Returns None to load all.

    - IRIS_TOOL_GROUPS: comma list of group names (see _TOOL_GROUPS).
    - IRIS_TOOL_MODULES: comma list of exact module names (escape hatch, unions
      with the groups). Lets a host add a one-off module not in any group.
    Either/both may be set; neither set → None (load everything).
    """
    groups_raw = os.environ.get("IRIS_TOOL_GROUPS", "").strip()
    modules_raw = os.environ.get("IRIS_TOOL_MODULES", "").strip()
    if not groups_raw and not modules_raw:
        return None

    allowed: set[str] = set()
    for g in groups_raw.split(","):
        g = g.strip().lower()
        if not g:
            continue
        if g in _TOOL_GROUPS:
            allowed.update(_TOOL_GROUPS[g])
        else:
            print(f"[iris tools] unknown IRIS_TOOL_GROUPS entry: {g!r}", file=sys.stderr)
    for m in modules_raw.split(","):
        m = m.strip()
        if m:
            allowed.add(m)
    return allowed


def _autoload_tools() -> list[str]:
    allowed = _allowed_modules()
    loaded: list[str] = []
    skipped: list[str] = []
    for mod_info in pkgutil.iter_modules(__path__):  # noqa: F821 — pkg __path__
        name = mod_info.name
        if name.startswith("_") or name in _AUTOLOAD_SKIP:
            continue
        if allowed is not None and name not in allowed:
            skipped.append(name)
            continue
        importlib.import_module(f"{__name__}.{name}")
        loaded.append(name)
    if allowed is not None:
        print(
            f"[iris tools] loaded {len(loaded)} module(s): {','.join(sorted(loaded))}"
            f" | skipped {len(skipped)}: {','.join(sorted(skipped))}",
            file=sys.stderr,
        )
    return loaded


_LOADED_TOOL_MODULES = _autoload_tools()
