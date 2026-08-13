"""The writer surface must stay a self-contained fork module.

The whole point of this feature's design is that upstream merges never touch it.
These are guards against that eroding: the moment someone adds an import map to
index.html, a route to app.js, or a precache entry to sw.js, the writer stops
being isolated and starts costing merge conflicts on every upstream sync.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "static" / "vendor" / "lexical"
WRITER = ROOT / "static" / "js" / "writer"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_vendored_lexical_has_no_bare_specifiers():
    """A browser cannot resolve bare specifiers without an import map.

    scripts/vendor_lexical.mjs rewrites them to relative siblings; if a re-vendor
    ever skips that step the surface breaks at runtime with an opaque module
    error, so fail loudly here instead.
    """
    import re

    bare = re.compile(r"""(?:from\s*|import\s*\(\s*)["'](?!\.{1,2}/)([^"']+)["']""")
    offenders = []
    for f in sorted(VENDOR.glob("*.mjs")):
        for m in bare.finditer(f.read_text(encoding="utf-8")):
            offenders.append(f"{f.name}: {m.group(1)}")
    assert not offenders, "vendored Lexical must import only relative paths: " + "; ".join(offenders)


def test_vendored_lexical_excludes_the_react_binding():
    """@lexical/react would drag React into a codebase that has no framework."""
    names = [f.name for f in VENDOR.glob("*.mjs")]
    assert names, "no vendored Lexical modules found"
    assert not [n for n in names if "React" in n], f"React binding vendored: {names}"
    assert (VENDOR / "Lexical.prod.mjs").exists(), "Lexical core missing from the vendor dir"


def test_writer_is_reached_only_through_the_fork_seam():
    """fork-ui.js is the single entry point, so upstream files stay untouched."""
    fork_ui = _read("static/js/fork-ui.js")
    assert "writer/writer.js" in fork_ui, "fork-ui.js must be the writer's entry point"

    # The import is deliberately bare (no ?v=): iterating inside static/js/writer/
    # must never require bumping a cache key in an upstream file.
    assert "writer/writer.js?v=" not in fork_ui, (
        "import the writer with a bare specifier so its changes never need an "
        "upstream ?v bump"
    )


@pytest.mark.parametrize(
    "rel,needle,why",
    [
        ("static/index.html", "importmap", "an import map in index.html is an upstream merge conflict"),
        ("static/index.html", "vendor/lexical", "index.html must not reference the vendored editor"),
        ("static/index.html", "writer", "the writer surface is injected at runtime, not in markup"),
        ("static/sw.js", "vendor/lexical", "the editor is lazy-loaded on purpose, not precached"),
        ("static/sw.js", "writer/writer.js", "the writer must not be precached"),
    ],
)
def test_upstream_files_do_not_reference_the_writer(rel, needle, why):
    assert needle not in _read(rel), f"{rel} references {needle!r}: {why}"


def test_app_js_only_reference_is_the_pre_existing_fork_ui_import():
    """app.js must not gain a writer route; fork-ui owns the wiring."""
    app = _read("static/app.js")
    assert "fork-ui.js" in app, "the fork-ui seam import went missing"
    for needle in ("writer/writer.js", "writerModule", "#writer"):
        assert needle not in app, f"app.js must not reference {needle!r} — route from the fork module"


def test_writer_module_owns_its_routing_and_styles():
    src = _read("static/js/writer/writer.js")
    assert "hashchange" in src, "the writer registers its own route listener"
    assert "../../vendor/lexical" in src, "Lexical must load from the vendored copy by relative path"
    # Styles live in fork.css, which index.html already links — no new stylesheet.
    assert "#writer-surface" in _read("static/fork.css")


def test_checklist_transformer_is_opted_in_and_ordered_first():
    """CHECK_LIST is not in Lexical's default TRANSFORMERS.

    Without it, `- [x] task` parses as an ordinary bullet whose text begins with
    "[x]". That round-trips byte-identically — so a naive round-trip test passes —
    while rendering as a plain bullet with literal brackets. It must also precede
    UNORDERED_LIST, because `- [ ] ` matches that transformer's regex too and the
    first match wins. This shipped broken once; keep it guarded.
    """
    src = _read("static/js/writer/blocks.js")
    assert "md.CHECK_LIST" in src, "CHECK_LIST must be added explicitly"
    body = src.split("export function transformersFor", 1)[1].split("}", 1)[0]
    assert body.index("CHECK_LIST") < body.index("md.TRANSFORMERS"), (
        "CHECK_LIST must come before the default transformers, or `- [ ] ` is "
        "claimed by UNORDERED_LIST first"
    )


def test_no_prism_highlighter_is_vendored():
    """@lexical/code pulls prismjs via bare side-effect imports.

    We use @lexical/code-core instead, so code blocks work unhighlighted. If
    highlighting is added later it should reuse the highlight.js the app already
    loads, not vendor a second highlighter.
    """
    names = [f.name for f in VENDOR.glob("*.mjs")]
    assert "LexicalCodeCore.prod.mjs" in names, "code-core must be vendored for CodeNode"
    assert "LexicalCodePrism.prod.mjs" not in names, "prism highlighter must not be vendored"
    for f in VENDOR.glob("*.mjs"):
        assert "prismjs" not in f.read_text(encoding="utf-8"), f"{f.name} references prismjs"


def test_markdown_is_the_canonical_persisted_form():
    """The storage decision is load-bearing: no new column, no migration.

    Documents stay readable by the plain editor, the RAG index, agent document
    reads, versioning and export, which all consume Document.current_content.
    """
    src = _read("static/js/writer/blocks.js")
    assert "$convertToMarkdownString" in src and "$convertFromMarkdownString" in src
    # A Lexical-JSON store would mean editing core/database.py — which is exactly
    # the upstream coupling this feature is designed to avoid.
    assert "toJSON" not in src, "do not persist Lexical editor-state JSON"
    assert "current_content" in src, "the markdown target field should be documented here"


def test_undo_history_is_registered():
    """A writing surface without undo is not a writing surface."""
    src = _read("static/js/writer/blocks.js")
    assert "registerHistory" in src
    assert "LexicalHistory.prod.mjs" in _read("static/js/writer/writer.js")


def test_store_uses_only_existing_document_endpoints():
    """No new backend routes: persistence rides the document API as it is."""
    src = _read("static/js/writer/store.js")
    for path in ("/api/document", "/api/documents/titles"):
        assert path in src, f"expected the writer store to use {path}"
    # A bespoke endpoint would mean touching routes/ — i.e. upstream files.
    assert "/api/writer" not in src, "the writer must not add its own backend routes"


def test_store_serialises_overlapping_saves():
    """Two PUTs racing for one document can land out of order.

    The store must queue a save requested while one is in flight and re-run it
    afterwards, so the last keystroke wins.
    """
    src = _read("static/js/writer/store.js")
    assert "_inFlight" in src and "_pendingWhileInFlight" in src


def test_store_trusts_the_server_echo_not_the_local_copy():
    """The server may coerce the body (e.g. the email-document path).

    Recording our local text as "last saved" would then leave the document
    permanently dirty, autosaving in a loop.
    """
    src = _read("static/js/writer/store.js")
    assert "doc.current_content ?? content" in src


def test_loading_a_document_does_not_mark_it_dirty():
    """Populating the editor fires Lexical's update listener.

    Without a guard, merely opening a document would schedule a save — and every
    open would burn a version.
    """
    src = _read("static/js/writer/writer.js")
    assert "_loading" in src, "the load path must suppress autosave"
    assert "if (_loading) return;" in src


def test_pending_edits_are_flushed_when_the_tab_goes_away():
    """A debounced save must not be lost to a close or a backgrounded tab."""
    src = _read("static/js/writer/writer.js")
    assert "pagehide" in src and "visibilitychange" in src
    assert "store.flush()" in src


def test_writer_documents_declare_markdown_language():
    """language:'markdown' keeps the plain editor and export treating the body right."""
    assert "language: 'markdown'" in _read("static/js/writer/store.js")


def test_outline_pages_within_the_library_limit():
    """/api/documents/library caps `limit` at 50 and 422s above it.

    Requesting 200 returned a validation error that the catch turned into an empty
    list — indistinguishable from "you have no documents". Page instead, and bound
    the loop.
    """
    src = _read("static/js/writer/outline.js")
    assert "const PAGE = 50;" in src, "page size must respect the endpoint's cap"
    assert "PAGE_CAP" in src, "the paging loop needs a bound"
    assert "limit: String(PAGE)" in src


def test_outline_surfaces_load_failures():
    """An empty list that is really a failed request must not read as 'no documents'."""
    src = _read("static/js/writer/outline.js")
    assert "_error" in src and "writer-list-error" in src
    assert "writer-list-error" in _read("static/fork.css")


def test_outline_search_reveals_matches_in_collapsed_folders():
    """Filtering while folders are collapsed showed a folder and hid every hit."""
    src = _read("static/js/writer/outline.js")
    assert "_searching()" in src
    assert "if (!_searching() && !_expanded.has(child.fullPath)) continue;" in src


def test_tags_are_posted_as_a_query_param():
    """This endpoint takes tags in the query string, not a JSON body."""
    src = _read("static/js/writer/outline.js")
    assert "/tags?tags=" in src


def test_mobile_list_overlay_cannot_cover_the_header():
    """The overlay is positioned against .writer-panes, not the fixed surface.

    With inset:0 resolving against #writer-surface the list covered the header,
    leaving no way to dismiss it or reach the editor — a dead end on a phone.
    """
    css = _read("static/fork.css")
    panes = css.split(".writer-panes {", 1)[1].split("}", 1)[0]
    assert "position: relative" in panes, ".writer-panes must be the containing block"


def test_mobile_autoclose_does_not_clobber_the_desktop_preference():
    """Hiding the list after a pick on a phone is a convenience, not a setting."""
    src = _read("static/js/writer/writer.js")
    assert "persist: false" in src
    assert "_isNarrow()" in src


def test_outline_reuses_the_previous_workspace_prefs_keys():
    """Folders created before the rewrite should still appear.

    The deleted Documents Workspace stored the same state under these keys; reusing
    them makes the new pane inherit it instead of starting empty.
    """
    src = _read("static/js/writer/outline.js")
    assert "dw_known_tags" in src, "reuse the old workspace's folder memory"
    assert "odysseus-dw-expanded" in src, "reuse the old workspace's expansion state"
