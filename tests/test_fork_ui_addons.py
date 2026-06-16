"""Guards the fork's runtime-injected UI (static/js/fork-ui.js) against drift.

The fork keeps index.html aligned with upstream by mounting fork-only panels at
runtime into upstream-owned anchor ids, instead of inlining markup into
index.html (see the odysseus-addon-strategy memory). Two ways that silently
breaks after an upstream sync:

  1. An upstream merge renames/removes an *anchor* id → the injector finds
     nothing, no-ops, and the panel just never appears.
  2. Someone deletes an injector (or its markup) while the *owning* module
     (admin.js) still reads the ids via el()/getElementById() → those calls
     return null and the feature is silently dead.

Case 2 is the exact regression that motivated fork-ui.js: the 10th upstream
merge took upstream's index.html, dropping the inlined API-Tokens panel while
admin.js still referenced #adm-tokenList — so the whole panel vanished with no
error. These pure-static checks (no browser/node) fail the build if either
recurs.

When you move a new fork-only panel into a fork-ui.js injector, add the ids its
owning module reads to FORK_CRITICAL_IDS below.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORK_UI = ROOT / "static" / "js" / "fork-ui.js"
INDEX = ROOT / "static" / "index.html"
ADMIN = ROOT / "static" / "js" / "admin.js"

# Fork-only element ids that a fork JS module reads but that live ONLY because
# they're mounted (in index.html or, preferably, a fork-ui.js injector). If a
# merge drops the markup, these go null and the feature dies silently.
FORK_CRITICAL_IDS = {
    # API Tokens panel — logic in admin.js, markup injected by fork-ui.js.
    "adm-tokenList", "adm-tokenName", "adm-tokenScopes", "adm-tokenAddBtn",
    "adm-tokenMsg", "adm-tokenReveal", "adm-tokenValue", "adm-tokenCopyBtn",
    # Endpoint LLM/Image Type selector in the Add-Models form.
    "adm-epType",
    # Rail tool launchers (wiring in app.js _railToolMap).
    "rail-today", "rail-books", "rail-health", "rail-habits", "rail-pings",
    # Settings rows/cards (wiring stays lazy in settings.js).
    "set-defaultPersonaSelect", "set-chatAllowedModels", "set-language",
    "set-quiet-hours-enabled", "set-quiet-hours-row",
}

_RE_ID_CREATED = re.compile(r'id=["\']([A-Za-z][\w-]*)["\']')
_RE_ID_READ = re.compile(r'(?:\bel|getElementById)\(\s*[\'"]([A-Za-z][\w-]*)[\'"]')
# fork-ui.js also creates ids dynamically: via `el.id = 'x'` and via object-literal
# maps keyed by id (e.g. _RAIL = {'rail-today': [...]}) whose keys are set with
# `b.id = key`. Count those as "created" so they aren't mistaken for anchors.
_RE_ID_ASSIGNED = re.compile(r'\.id\s*=\s*[\'"]([A-Za-z][\w-]*)[\'"]')
_RE_ID_MAPKEY = re.compile(r'[\'"]([a-z]+-[\w-]+)[\'"]\s*:')


def _created(text: str) -> set:
    return set(_RE_ID_CREATED.findall(text))


def _created_by_fork_ui(text: str) -> set:
    """Ids fork-ui.js mounts — markup ids + `.id=` assignments + id-map keys."""
    return (set(_RE_ID_CREATED.findall(text))
            | set(_RE_ID_ASSIGNED.findall(text))
            | set(_RE_ID_MAPKEY.findall(text)))


def _read(text: str) -> set:
    return set(_RE_ID_READ.findall(text))


def test_fork_ui_exists():
    assert FORK_UI.is_file(), "static/js/fork-ui.js (the fork UI injector) is missing"


def test_fork_critical_ids_are_mounted_somewhere():
    """Every fork-critical id must be created in index.html OR fork-ui.js."""
    sources = _created(INDEX.read_text(encoding="utf-8")) | _created_by_fork_ui(
        FORK_UI.read_text(encoding="utf-8")
    )
    missing = sorted(i for i in FORK_CRITICAL_IDS if i not in sources)
    assert not missing, (
        "Fork-critical UI id(s) are referenced by JS but mounted nowhere "
        "(neither index.html nor a fork-ui.js injector) — an upstream merge "
        f"likely dropped the markup: {missing}. Re-add them to a fork-ui.js "
        "injector (preferred) so index.html stays aligned with upstream."
    )


def test_fork_ui_anchors_exist_in_index_html():
    """Every upstream anchor fork-ui.js mounts into must still exist in index.html."""
    fu = FORK_UI.read_text(encoding="utf-8")
    mounted = _created_by_fork_ui(fu)   # ids fork-ui.js creates (markup + .id= + id-map keys)
    # Anchors come from getElementById() lookups AND _afterAnchor('anchor', …) calls.
    looked_up = _read(fu) | set(re.findall(r"_afterAnchor\(\s*['\"]([A-Za-z][\w-]*)['\"]", fu))
    anchors = looked_up - mounted       # → external, upstream-owned anchors
    index_ids = _created(INDEX.read_text(encoding="utf-8"))
    missing = sorted(a for a in anchors if a not in index_ids)
    assert not missing, (
        "fork-ui.js mounts into anchor id(s) that no longer exist in index.html "
        f"— upstream likely renamed/removed them, so the panels won't mount: "
        f"{missing}. Point the injector(s) at the new upstream anchor."
    )


def test_admin_token_ids_match_fork_ui():
    """admin.js (owner of token logic) and fork-ui.js (markup) must agree on ids."""
    admin_token_refs = {i for i in _read(ADMIN.read_text(encoding="utf-8"))
                        if i.startswith("adm-token")}
    fork_mounts = _created(FORK_UI.read_text(encoding="utf-8"))
    missing = sorted(i for i in admin_token_refs if i not in fork_mounts)
    assert not missing, (
        "admin.js reads token element id(s) that fork-ui.js does not mount: "
        f"{missing}. The panel would render but the wiring would miss them."
    )
