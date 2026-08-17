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
    """Two writes racing for one document can land out of order.

    The store must queue a save requested while one is in flight and re-run it
    afterwards, so the last keystroke wins.
    """
    src = _read("static/js/writer/store.js")
    assert "_writing" in src and "_pendingWhileWriting" in src


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


def test_writer_is_reachable_from_the_expanded_sidebar():
    """The icon rail only exists while the sidebar is COLLAPSED.

    A rail-only entry meant that with the sidebar expanded — how the app runs by
    default — the writer had no visible entry point at all, and could only be
    reached by typing #writer into the URL.
    """
    src = _read("static/js/fork-ui.js")
    assert "tool-writer-btn" in src, "the expanded sidebar needs its own Writer entry"
    assert "_injectWriterSidebarItem" in src
    assert "tool-library-btn" in src, "anchor the entry to a stable upstream row"
    # And the rail entry stays, for when the sidebar IS collapsed.
    assert "rail-writer" in src


def test_row_menus_remove_themselves_on_close():
    """bindMenuDismiss invokes the callback; it does NOT touch the DOM.

    The documented idiom is bindMenuDismiss(popup, () => popup.remove()). Omitting
    the remove leaked a menu element into <body> on every open, and the next menu
    opened behind the stale one.
    """
    src = _read("static/js/writer/menus.js")
    assert "menu.remove()" in src, "onClose must remove the menu node"


def test_row_menu_outside_predicate_reads_the_event():
    """isOutside is called with the EVENT, not the target element."""
    src = _read("static/js/writer/menus.js")
    assert "ev.target" in src, "isOutside receives an event; use ev.target"


def test_row_menus_reuse_the_apps_dismiss_stack():
    """Escape and click-away should behave like every other popover in the app."""
    src = _read("static/js/writer/menus.js")
    assert "escMenuStack" in src
    assert "bindMenuDismiss" in src and "topPopupZ" in src


def test_action_button_does_not_trigger_the_row():
    """The '…' sits inside a row whose click opens/expands it."""
    src = _read("static/js/writer/menus.js")
    assert "stopPropagation" in src


def test_deleting_the_open_document_switches_away():
    """The server refuses edits to a trashed document.

    Staying on it would turn every keystroke into a failed save, so deleting the
    open document must move the editor to a fresh one.
    """
    src = _read("static/js/writer/writer.js")
    assert "onDeleted" in src
    assert "if (id === store.currentDocId()) newDocument();" in src


def test_duplicate_carries_tags_so_the_copy_stays_in_its_folder():
    src = _read("static/js/writer/outline.js")
    dup = src.split("async function duplicateDoc", 1)[1].split("\nasync function", 1)[0]
    assert "tags" in dup, "a copy should land in the same folder as its original"
    # The library row carries no body, so the copy has to read the document —
    # from the local cache when we hold it, otherwise over the network.
    body = src.split("async function _bodyOf", 1)[1].split("\nasync function", 1)[0]
    assert "current_content" in body, "the library row has no body; read the document"
    assert "row.stub" in body, "a metadata-only stub is not a usable body"


# ── offline mode ─────────────────────────────────────────────────────────────
#
# The rule every guard below protects: text you typed is never lost. Offline
# support multiplies the ways it could be — a stale id, a resurrected delete, a
# stub mistaken for a cached body, two devices editing at once — so each of those
# gets pinned here.


def test_a_list_stub_is_never_opened_as_a_cached_document():
    """The library list has metadata but NO body.

    Treating such a row as a cached document would show an EMPTY editor and then
    autosave that emptiness over the real text — silent data loss on the worst
    possible path (just opening a document).
    """
    store = _read("static/js/writer/store.js")
    assert "row.stub" in store, "store.load must reject a stub as a local copy"
    assert "!row.stub" in store
    db = _read("static/js/writer/localdb.js")
    merge = db.split("async function _mergeOne", 1)[1].split("\nexport ", 1)[0]
    assert "content:" not in merge, (
        "merging list metadata must never write `content` — it would overwrite a "
        "cached body with nothing"
    )


def test_a_queued_delete_is_not_resurrected_by_a_list_refresh():
    """The server still lists a document until our queued delete reaches it."""
    db = _read("static/js/writer/localdb.js")
    merge = db.split("async function _mergeOne", 1)[1].split("\nexport ", 1)[0]
    assert "cur.deleted" in merge, "a pending local delete outranks server metadata"


def test_unsynced_local_edits_outrank_a_background_refresh():
    """A refresh must never yank text out from under the person typing."""
    store = _read("static/js/writer/store.js")
    refresh = store.split("async function _refreshInBackground", 1)[1].split("\n/*", 1)[0]
    assert "row.dirty" in refresh, "a dirty local row must not be overwritten"
    assert "_docId" in refresh, "the user may have switched documents mid-request"


def test_conflicts_park_the_other_version_before_overwriting():
    """Order is the whole safety property here.

    Our text wins in place, but the server's divergent version must be saved
    FIRST — if parking fails we have to abort and keep the op queued, or the other
    device's writing is gone.
    """
    src = _read("static/js/writer/sync.js")
    content = src.split("async function _runContent", 1)[1].split("\nasync function", 1)[0]
    assert content.index("_parkConflict") < content.index("_putContent("), (
        "park the server's version before overwriting it"
    )
    assert "await _parkConflict" in content, "a failed park must abort the write"
    assert "serverMoved" in content and "base_content" in content, (
        "a real conflict needs a three-way comparison, not just 'they differ'"
    )


def test_sync_trusts_the_server_echo_as_the_new_base():
    """The server can coerce a body; recording ours would fake a conflict forever."""
    src = _read("static/js/writer/sync.js")
    assert "saved.current_content ?? ours" in src


def test_unsynced_text_for_a_vanished_document_is_recovered_not_dropped():
    """404 means deleted or trashed elsewhere. We still hold text for it.

    Re-creating it in place would resurrect something deliberately deleted, so it
    lands in a clearly named new document instead — but it is never just dropped.
    """
    src = _read("static/js/writer/sync.js")
    assert "_recoverOrphan" in src
    recover = src.split("async function _recoverOrphan", 1)[1].split("\nasync function", 1)[0]
    assert "recovered" in recover, "the recovered copy should say what it is"
    assert "_onNotice" in recover, "silently recovering is nearly as bad as losing it"


def test_create_rekeys_before_dropping_folded_ops():
    """Dropping first would strand the ops that still need the new id."""
    src = _read("static/js/writer/sync.js")
    create = src.split("async function _runCreate", 1)[1].split("\nasync function", 1)[0]
    assert create.index("rekeyDoc") < create.index("dropOpsOfType"), (
        "rekey first, or queued tags/delete ops point at an id that no longer exists"
    )
    # Typing continues while the POST is in flight; that text has to be re-queued.
    assert "nowContent" in create and "enqueue" in create, (
        "content typed during the create must still be pushed"
    )


def test_sync_rereads_each_op_before_running_it():
    """Running an op can drop or repoint others, so the snapshot goes stale."""
    src = _read("static/js/writer/sync.js")
    assert "db.getOp(stale.seq)" in src, "re-read each op instead of trusting the snapshot"


def test_sync_preserves_per_document_ordering_without_wedging_the_queue():
    """A failed op must not let a later op for the SAME document overtake it —
    and must not stop unrelated documents from syncing."""
    src = _read("static/js/writer/sync.js")
    drain = src.split("async function _drain", 1)[1].split("\n/**", 1)[0]
    assert "blocked" in drain and "continue" in drain


def test_sync_takes_a_cross_tab_lock():
    """Two tabs draining the same queue would double-write."""
    src = _read("static/js/writer/sync.js")
    assert "navigator.locks" in src
    assert "ifAvailable" in src, "the second tab should skip, not queue behind the first"


def test_sync_stops_the_pass_when_not_signed_in():
    """Every remaining op would fail the same way; keep the queue for after login."""
    src = _read("static/js/writer/sync.js")
    assert "401" in src and "authRequired" in src


def test_the_local_lane_degrades_to_server_only():
    """Private modes can refuse IndexedDB. That must not stop you editing."""
    db = _read("static/js/writer/localdb.js")
    assert "export async function available" in db
    store = _read("static/js/writer/store.js")
    assert "_localReady" in store and "_saveToServer" in store, (
        "with no IndexedDB the writer must fall back to talking straight to the server"
    )


def test_save_state_and_sync_state_are_separate():
    """"Saved" means durable on this device; syncing is a different question.

    Merging them would either claim a save that only reached the network, or nag
    about the network when nothing is at risk.
    """
    src = _read("static/js/writer/writer.js")
    assert "_syncStatus" in src and "_status" in src
    assert "writer-sync" in _read("static/fork.css")


def test_the_offline_shell_is_warmed_from_the_page_not_precached():
    """sw.js deliberately does not precache the editor (~490 KB).

    Warming its cache from the page keeps that decision AND keeps sw.js — an
    upstream file — free of writer entries.
    """
    sync_src = _read("static/js/writer/sync.js")
    assert "warmShell" in sync_src
    assert "caches.keys()" in sync_src, "find the worker's live cache, don't guess its name"
    assert "odysseus-v" in sync_src, "the cache name pattern is the coupling point"
    sw = _read("static/sw.js")
    for needle in ("writer.html", "writer/localdb.js", "writer/sync.js", "writer/standalone.js"):
        assert needle not in sw, f"sw.js must not reference {needle!r}"


def test_standalone_page_has_no_inline_script():
    """Static files get no {{CSP_NONCE}} substitution.

    script-src is 'self' plus a nonce, so an inline <script> in a statically
    served page is silently blocked and the page just never boots.
    """
    import re

    # Strip comments first — the file's own commentary explains this rule and
    # mentions the tag, which a naive scan reads as a violation.
    html = re.sub(r"<!--.*?-->", "", _read("static/writer.html"), flags=re.DOTALL)

    for m in re.finditer(r"<script\b([^>]*)>", html):
        assert "src=" in m.group(1), (
            "writer.html must not contain an inline <script> — it is served by "
            "StaticFiles, which does not substitute the CSP nonce"
        )
    assert "{{CSP_NONCE}}" not in html, "a static file never gets the nonce substituted"


def test_standalone_page_versions_match_index_html():
    """A versioned URL is served `immutable` and skips revalidation.

    If these drift from index.html the standalone page fetches its own copies of
    style.css/fork.css instead of reusing the ones already cached.
    """
    import re

    def tokens(rel, name):
        return set(re.findall(rf"{re.escape(name)}\?v=(\d+)", _read(rel)))

    for asset in ("style.css", "fork.css"):
        assert tokens("static/writer.html", asset) == tokens("static/index.html", asset), (
            f"{asset} version in writer.html must match index.html"
        )


def test_standalone_page_is_not_referenced_by_upstream_files():
    """It is a fork-only entry; upstream must not learn about it."""
    for rel in ("static/index.html", "static/app.js", "static/sw.js"):
        assert "writer.html" not in _read(rel), f"{rel} must not reference writer.html"


def test_standalone_boot_reuses_the_same_writer_modules():
    """Two copies of the editor would mean two IndexedDB writers and split state."""
    src = _read("static/js/writer/standalone.js")
    assert "'./writer.js'" in src, "import the shared module, don't fork the surface"
    assert "serviceWorker" in src, "the standalone page registers the worker itself"


def test_sync_claims_the_pass_before_its_first_await():
    """Found in the browser: two flushes both passed the `_syncing` guard.

    The flag used to be set after `await db.available()`, so a second flush could
    slip through while the first was still awaiting. The Web Lock kept them from
    double-writing, but the loser's pass became a silent no-op that recorded no
    retry — so a just-enqueued op sat unsent until the next 30s poll.
    """
    src = _read("static/js/writer/sync.js")
    body = src.split("export async function flush(", 1)[1].split("\n}", 1)[0]
    claim = body.index("_syncing = true")
    first_await = body.index("await ")
    assert claim < first_await, "claim _syncing synchronously, before any await"


def test_a_flush_requested_during_a_pass_is_not_dropped():
    """An op enqueued mid-pass is not in that pass's snapshot."""
    src = _read("static/js/writer/sync.js")
    assert "_flushAgain" in src
    # And the deferred request keeps its own `force`: an `online` event downgraded
    # to a backoff-gated retry is the same silent delay all over again.
    assert "_flushAgainForce" in src


def test_user_initiated_pushes_bypass_the_retry_backoff():
    """The backoff is global.

    One document whose op keeps failing must not hold back every other document's
    first sync — creating a document would otherwise sit unsynced for the length of
    an unrelated failure's backoff.
    """
    src = _read("static/js/writer/store.js")
    assert "_schedulePush(0, { force: true })" in src, (
        "direct user actions should push past the backoff"
    )
    # Typing-driven pushes stay unforced — they repeat constantly.
    assert "_schedulePush();" in src


def test_offline_list_shows_local_documents_rather_than_an_error():
    """A document created offline exists ONLY locally.

    Replacing the list with a load error would hide the user's newest work.
    """
    src = _read("static/js/writer/outline.js")
    assert "db.allDocs()" in src, "paint from the local mirror first"
    assert "mergeListRows" in src, "the server list folds into the mirror, it does not replace it"
    assert "_offline" in src and "writer-list-note" in src
