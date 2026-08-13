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
//   * Not precached by sw.js on purpose: 488 KB of editor only loads when the
//     surface is first opened.
//
// Phase 0 scope: prove the vendored modules load and edit natively. The block
// set, slash menu, tag tree and persistence land in later phases.

const V = '../../vendor/lexical';

let _editor = null;      // Lexical editor instance, created on first open
let _lexical = null;     // resolved module namespace bundle

/** Load the vendored Lexical modules. Dynamic so nothing costs anything until open. */
async function _loadLexical() {
  if (_lexical) return _lexical;
  const [core, richText, list, utils] = await Promise.all([
    import(`${V}/Lexical.prod.mjs`),
    import(`${V}/LexicalRichText.prod.mjs`),
    import(`${V}/LexicalList.prod.mjs`),
    import(`${V}/LexicalUtils.prod.mjs`),
  ]);
  _lexical = { core, richText, list, utils };
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
      <span class="writer-title">Writer</span>
      <span class="writer-status" id="writer-status"></span>
      <button type="button" class="memory-toolbar-btn" id="writer-close" title="Close">Close</button>
    </div>
    <div class="writer-body">
      <div class="writer-editor" id="writer-editor" contenteditable="true"
           role="textbox" aria-multiline="true" spellcheck="true"></div>
    </div>`;
  document.body.appendChild(el);
  el.querySelector('#writer-close').addEventListener('click', () => close());
  return el;
}

async function _mountEditor(host) {
  const { core, richText, list, utils } = await _loadLexical();

  // registerRichText / registerList need their nodes declared up front.
  const editor = core.createEditor({
    namespace: 'odysseus-writer',
    nodes: [
      richText.HeadingNode, richText.QuoteNode,
      list.ListNode, list.ListItemNode,
    ],
    onError: (err) => console.error('[writer] lexical:', err),
    theme: {
      paragraph: 'writer-p',
      heading: { h1: 'writer-h1', h2: 'writer-h2', h3: 'writer-h3' },
      quote: 'writer-quote',
      list: { ul: 'writer-ul', ol: 'writer-ol', listitem: 'writer-li' },
      text: { bold: 'writer-bold', italic: 'writer-italic', code: 'writer-code' },
    },
  });

  editor.setRootElement(host);
  // mergeRegister keeps the teardown of every plugin in one disposer.
  const dispose = utils.mergeRegister(
    richText.registerRichText(editor),
    list.registerList(editor),
  );
  editor._odysseusDispose = dispose;
  return editor;
}

/** Report what actually loaded, so Phase 0 can be verified without guessing. */
function _report(ok, detail) {
  const s = document.getElementById('writer-status');
  if (s) s.textContent = detail;
  window.__writerPhase0 = { ok, detail, at: Date.now() };
}

export async function open() {
  const el = _buildShell();
  el.removeAttribute('hidden');
  document.body.classList.add('writer-open');
  if (!_editor) {
    try {
      _editor = await _mountEditor(el.querySelector('#writer-editor'));
      _report(true, 'lexical ready');
    } catch (err) {
      _report(false, 'failed: ' + (err && err.message));
      console.error('[writer] mount failed', err);
      return;
    }
  }
  _editor.focus();
}

export function close() {
  const el = document.getElementById('writer-surface');
  if (el) el.setAttribute('hidden', '');
  document.body.classList.remove('writer-open');
  if (location.hash === '#writer') {
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

export function isOpen() {
  const el = document.getElementById('writer-surface');
  return !!el && !el.hasAttribute('hidden');
}

/** Own routing — deliberately not registered in app.js's table. */
function _syncFromHash() {
  if (location.hash === '#writer') open();
  else if (isOpen()) close();
}

export function init() {
  window.addEventListener('hashchange', _syncFromHash);
  _syncFromHash();
}

const writerModule = { init, open, close, isOpen, getEditor, getLexical };

// Mirrors the codebase convention (window.chatModule, window.sessionModule, ...)
// so other fork modules can reach the writer without an import cycle.
if (typeof window !== 'undefined') window.writerModule = writerModule;

export default writerModule;
