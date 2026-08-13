// static/js/writer/writer.js
//
// FORK-ONLY. The block writing surface, built on vendored Lexical.
//
// ISOLATION CONTRACT — read before editing:
//   * Everything this feature needs lives under static/js/writer/ and
//     static/vendor/lexical/, plus rules in fork.css. No upstream file is
//     modified by this feature beyond the single dynamic import in fork-ui.js.
//   * Lexical is imported from the vendored copy by RELATIVE path. Do not add an
//     import map (that would mean editing index.html, an upstream file).
//   * Routing is our own hashchange listener, not app.js's route table.
//   * Persistence uses the EXISTING document API — no new endpoints, no schema
//     change. See ./store.js.
//   * Not precached by sw.js on purpose: ~490 KB of editor only loads when the
//     surface is first opened.
//
// ./blocks.js owns the block vocabulary and the markdown round-trip; ./store.js
// owns loading and autosaving. The slash menu and tag tree land in later phases.

import blocks from './blocks.js';
import store from './store.js';

const V = '../../vendor/lexical';

let _editor = null;      // Lexical editor instance, created on first open
let _lexical = null;     // resolved module namespace bundle
let _loading = false;    // suppress autosave while we populate the editor
let _wired = false;      // page-level listeners attached once

/** Load the vendored Lexical modules. Dynamic so nothing costs anything until open. */
async function _loadLexical() {
  if (_lexical) return _lexical;
  const [core, richText, list, code, link, table, markdown, history, utils] = await Promise.all([
    import(`${V}/Lexical.prod.mjs`),
    import(`${V}/LexicalRichText.prod.mjs`),
    import(`${V}/LexicalList.prod.mjs`),
    import(`${V}/LexicalCodeCore.prod.mjs`),
    import(`${V}/LexicalLink.prod.mjs`),
    import(`${V}/LexicalTable.prod.mjs`),
    import(`${V}/LexicalMarkdown.prod.mjs`),
    import(`${V}/LexicalHistory.prod.mjs`),
    import(`${V}/LexicalUtils.prod.mjs`),
  ]);
  _lexical = { core, richText, list, code, link, table, markdown, history, utils };
  return _lexical;
}

/** The shell. Reuses existing Odysseus class vocabulary — no new design language. */
function _buildShell() {
  let el = document.getElementById('writer-surface');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'writer-surface';
  el.setAttribute('hidden', '');
  el.innerHTML = `
    <div class="writer-head">
      <input type="text" id="writer-title" class="writer-title-input"
             placeholder="Untitled" spellcheck="false" autocomplete="off">
      <span class="writer-status" id="writer-status"></span>
      <button type="button" class="memory-toolbar-btn" id="writer-new" title="New document">New</button>
      <button type="button" class="memory-toolbar-btn" id="writer-close" title="Close">Close</button>
    </div>
    <div class="writer-body">
      <div class="writer-editor" id="writer-editor" contenteditable="true"
           role="textbox" aria-multiline="true" spellcheck="true"></div>
    </div>`;
  document.body.appendChild(el);

  el.querySelector('#writer-close').addEventListener('click', () => close());
  el.querySelector('#writer-new').addEventListener('click', () => newDocument());

  const title = el.querySelector('#writer-title');
  // Rename on blur/Enter rather than per keystroke: the title is metadata, and a
  // PATCH per character would be noise.
  const commitTitle = () => {
    const v = title.value.trim();
    if (v) store.rename(v).catch((e) => console.warn('[writer] rename failed:', e && e.message));
  };
  title.addEventListener('blur', commitTitle);
  title.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); title.blur(); }
  });
  return el;
}

/** Save status, shown in the header so a failed save is never silent. */
function _status(state, err) {
  const el = document.getElementById('writer-status');
  if (el) {
    el.textContent = state === store.State.ERROR
      ? `save failed — ${err && err.message ? err.message : 'retrying on next edit'}`
      : state;
    el.classList.toggle('writer-status-error', state === store.State.ERROR);
  }
  window.__writer = { ...(window.__writer || {}), state, docId: store.currentDocId() };
}

async function _mountEditor(host) {
  const lex = await _loadLexical();

  // Every node a plugin or transformer may create must be declared up front, or
  // Lexical throws when one first appears.
  const editor = lex.core.createEditor({
    namespace: 'odysseus-writer',
    nodes: blocks.nodesFor(lex),
    onError: (err) => console.error('[writer] lexical:', err),
    theme: blocks.THEME,
  });

  editor.setRootElement(host);
  const disposeBlocks = blocks.registerAll(editor, lex);

  // Autosave trigger. Selection-only updates carry no dirty nodes, so filtering
  // on them keeps mere cursor movement from marking the document unsaved.
  const disposeSave = editor.registerUpdateListener(({ dirtyElements, dirtyLeaves }) => {
    if (_loading) return;
    if (dirtyElements.size === 0 && dirtyLeaves.size === 0) return;
    store.touch();
  });

  editor._odysseusDispose = () => { disposeSave(); disposeBlocks(); };
  return editor;
}

/** Populate the editor without tripping autosave. */
function _setContentQuietly(markdown) {
  _loading = true;
  try {
    blocks.loadMarkdown(_editor, _lexical, markdown);
  } finally {
    // The update listener runs synchronously inside loadMarkdown's editor.update,
    // so clearing after it returns is enough — but do it in a microtask as well in
    // case a future Lexical defers reconciliation.
    queueMicrotask(() => { _loading = false; });
  }
}

function _applyDoc(doc) {
  const title = document.getElementById('writer-title');
  if (title) title.value = doc.title === 'Untitled' ? '' : (doc.title || '');
  _setContentQuietly(doc.current_content ?? '');
  _status(store.State.SAVED);
}

/** Open (or reopen) the surface. `docId` wins; otherwise resume, else create. */
export async function open(docId = null) {
  const el = _buildShell();
  el.removeAttribute('hidden');
  document.body.classList.add('writer-open');

  if (!_editor) {
    try {
      _editor = await _mountEditor(el.querySelector('#writer-editor'));
      store.configure({ getContent: getMarkdown, onState: _status });
    } catch (err) {
      _status(store.State.ERROR, err);
      console.error('[writer] mount failed', err);
      return;
    }
  }
  if (!_wired) {
    _wired = true;
    // A backgrounded tab may never fire another event, so flush on hide as well
    // as unload. pagehide is the reliable one on iOS.
    window.addEventListener('pagehide', () => { store.flush(); });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') store.flush();
    });
  }

  const wanted = docId || store.currentDocId() || store.lastDocId();
  if (!store.currentDocId() || (docId && docId !== store.currentDocId())) {
    try {
      const doc = wanted ? await store.load(wanted) : await store.create();
      _applyDoc(doc);
    } catch (err) {
      // A remembered id can be stale (deleted or trashed) — fall back to a new one
      // rather than leaving the surface stuck on an error.
      if (wanted) {
        try { _applyDoc(await store.create()); } catch (e2) { _status(store.State.ERROR, e2); }
      } else {
        _status(store.State.ERROR, err);
      }
    }
  }
  _editor.focus();
}

export async function newDocument() {
  await store.flush();
  store.reset();
  try {
    const doc = await store.create();
    _applyDoc(doc);
    _editor.focus();
    return doc;
  } catch (err) {
    _status(store.State.ERROR, err);
    return null;
  }
}

export function close() {
  store.flush();
  const el = document.getElementById('writer-surface');
  if (el) el.setAttribute('hidden', '');
  document.body.classList.remove('writer-open');
  if (String(location.hash).startsWith('#writer')) {
    history.replaceState(null, '', location.pathname + location.search);
  }
}

/**
 * The live Lexical editor, or null before first open. The toolbar, slash menu and
 * persistence layers all drive the document through this handle rather than the
 * DOM, so this is the module's real API surface.
 */
export function getEditor() { return _editor; }

/** The vendored module namespaces, for callers that need node classes. */
export function getLexical() { return _lexical; }

/** Replace the document from markdown (the canonical stored form). */
export function setMarkdown(text) {
  if (!_editor) return false;
  blocks.loadMarkdown(_editor, _lexical, text);
  return true;
}

/** Serialise the document back to markdown for saving. */
export function getMarkdown() {
  if (!_editor) return '';
  return blocks.toMarkdown(_editor, _lexical);
}

export function isOpen() {
  const el = document.getElementById('writer-surface');
  return !!el && !el.hasAttribute('hidden');
}

/** `#writer` resumes the last document; `#writer=<id>` opens a specific one. */
function _docIdFromHash() {
  const m = /^#writer(?:=(.+))?$/.exec(String(location.hash || ''));
  return m ? (m[1] ? decodeURIComponent(m[1]) : null) : undefined;
}

/** Own routing — deliberately not registered in app.js's table. */
function _syncFromHash() {
  const id = _docIdFromHash();
  if (id !== undefined) open(id);
  else if (isOpen()) close();
}

export function init() {
  window.addEventListener('hashchange', _syncFromHash);
  _syncFromHash();
}

const writerModule = {
  init, open, close, isOpen, newDocument,
  getEditor, getLexical, setMarkdown, getMarkdown,
  store,
};

// Mirrors the codebase convention (window.chatModule, window.sessionModule, ...)
// so other fork modules can reach the writer without an import cycle.
if (typeof window !== 'undefined') window.writerModule = writerModule;

export default writerModule;
