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
