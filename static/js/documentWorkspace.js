// static/js/documentWorkspace.js
// ============================================
// Documents Workspace — a full-window, 3-pane view (list | editor | chat) for
// working across many documents without the Library modal round-trip.
//
// Heavy reuse, no parallel components:
//   - LEFT   : a light list reusing the library card vocabulary
//              (.doclib-card.memory-item) + the shared /api/documents/library
//   - CENTER : the real editor — documentModule.openInWorkspace(id, mountTarget)
//   - RIGHT  : the real chat surface — #chat-container is *relocated* here while
//              the workspace is open (never cloned), so the existing
//              doc-scoped agent (active_doc_id) edits the open document.
// Opened from the Documents rail button and the /workspace deep link.
// ============================================

import documentModule from './document.js?v=474';
import sessionModule from './sessions.js';
import uiModule from './ui.js';
import { langIcon } from './langIcons.js';
import { attachMdShortcuts } from './mdShortcuts.js?v=478';

let API_BASE = '';
let _open = false;
let _shell = null;
let _chatHome = null;           // { parent, next } — exact restore slot for #chat-container (phase 4)
let _docs = [];                 // last-fetched document list
let _activeDocId = null;        // doc currently open in the centre editor
let _searchTimer = null;        // debounce for the search box
let _refreshTimer = null;       // debounce for documents-refresh re-fetch
let _listReqSeq = 0;            // guards against out-of-order list fetches

// ---- icons ----------------------------------------------------------------

function _icon(paths, w = 16) {
  return `<svg width="${w}" height="${w}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}
const _ICON_CLOSE = _icon('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>');
const _ICON_CHAT = _icon('<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>');
// Back arrow — exits the workspace.
const _ICON_BACK = _icon('<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>');
// Chevron — reveals the app sidebar as a left drawer over the document list.
const _ICON_CHEVRON = _icon('<polyline points="9 18 15 12 9 6"/>');
// Chat bubble with a plus — starts a NEW chat.
const _ICON_NEWCHAT = _icon('<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/><line x1="12" y1="8.5" x2="12" y2="14.5"/><line x1="9" y1="11.5" x2="15" y2="11.5"/>');
// Generic document glyph — mirrors the library card's fallback icon.
const _GEN_DOC_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;opacity:0.4;flex-shrink:0;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';

// ---- shell ----------------------------------------------------------------

function _buildShell() {
  if (_shell) return _shell;
  const el = document.createElement('div');
  el.id = 'doc-workspace';
  // AI helper starts hidden — it slides in only when toggled.
  el.className = 'hidden dw-chat-collapsed';
  el.innerHTML = `
    <div class="dw-left">
      <div class="dw-left-head">
        <button class="icon-rail-btn dw-back" id="dw-back" title="Exit workspace" aria-label="Exit workspace">${_ICON_BACK}</button>
        <button class="icon-rail-btn dw-nav-toggle" id="dw-nav-toggle" title="Show sidebar" aria-label="Show sidebar">${_ICON_CHEVRON}</button>
        <input type="text" id="dw-search" class="memory-search-input" placeholder="Search documents…" autocomplete="off" />
        <button class="icon-rail-btn dw-new-btn" id="dw-new" title="New document" aria-label="New document">${_icon('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>')}</button>
      </div>
      <div class="dw-list" id="dw-list" role="list"></div>
    </div>
    <div class="dw-center" id="dw-center"></div>
    <div class="dw-right" id="dw-right">
      <div class="dw-ai-head">
        <span class="dw-ai-title">Assist</span>
        <button class="memory-item-btn dw-ai-newchat" id="dw-ai-newchat" title="New chat" aria-label="New chat">${_ICON_NEWCHAT}</button>
        <button class="memory-item-btn dw-ai-hide" id="dw-ai-hide" title="Hide assistant" aria-label="Hide assistant">${_ICON_CLOSE}</button>
      </div>
    </div>
    <button class="icon-rail-btn dw-chat-toggle" id="dw-chat-toggle" title="Assistant (ask about this document)" aria-label="Open assistant">${_ICON_CHAT}</button>
    <button class="icon-rail-btn dw-close" id="dw-close" title="Exit workspace" aria-label="Exit workspace">${_ICON_CLOSE}</button>
    <div class="dw-mobile-switch" role="tablist" aria-label="Workspace panes">
      <button class="dw-mtab" data-pane="left">List</button>
      <button class="dw-mtab" data-pane="center">Editor</button>
      <button class="dw-mtab" data-pane="right">Chat</button>
    </div>`;
  document.body.appendChild(el);
  _shell = el;

  // Exit the workspace (desktop = the list-head back arrow; mobile = the
  // top-right close). Both leave the document & chat intact.
  el.querySelector('#dw-back').addEventListener('click', () => closeWorkspace());
  el.querySelector('#dw-close').addEventListener('click', () => closeWorkspace());
  // Chevron reveals the app sidebar as a left drawer over the list.
  el.querySelector('#dw-nav-toggle').addEventListener('click', () => _toggleNav());
  // The floating bubble OPENS the assistant; the panel's own × HIDES it.
  el.querySelector('#dw-chat-toggle').addEventListener('click', () => el.classList.remove('dw-chat-collapsed'));
  el.querySelector('#dw-ai-hide').addEventListener('click', () => {
    if (window.innerWidth <= 768) _setMobilePane('center');   // mobile: back to the editor tab
    else el.classList.add('dw-chat-collapsed');               // desktop: collapse the panel only
  });
  // New chat — reuse the app's real new-chat action (preferred-model aware),
  // never closes the document. The doc stays bound via active_doc_id.
  el.querySelector('#dw-ai-newchat').addEventListener('click', () => {
    const btn = document.querySelector('#rail-new-session, #sidebar-new-chat-btn, #mobile-new-chat-btn');
    if (btn) btn.click();
    if (window.innerWidth <= 768) _setMobilePane('right');
  });
  el.querySelectorAll('.dw-mtab').forEach(btn => {
    btn.addEventListener('click', () => _setMobilePane(btn.dataset.pane));
  });

  const searchEl = el.querySelector('#dw-search');
  searchEl.addEventListener('input', () => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => _loadList(searchEl.value.trim()), 200);
  });
  el.querySelector('#dw-new').addEventListener('click', () => _newDoc());
  return el;
}

function _setMobilePane(pane) {
  if (!_shell) return;
  _shell.setAttribute('data-pane', pane);
  _shell.querySelectorAll('.dw-mtab').forEach(b => b.classList.toggle('active', b.dataset.pane === pane));
}

function _currentSearch() {
  const s = _shell && _shell.querySelector('#dw-search');
  return s ? s.value.trim() : '';
}

// ---- sidebar drawer -------------------------------------------------------
// The workspace is full-window (the app icon-rail is hidden). The chevron
// reveals the REAL app sidebar (its expanded form) as a left drawer over the
// document list — un-hide #sidebar so syncRailSide shows rail+sidebar, lift
// them above the workspace via body.dw-nav-open, with a dismissable backdrop.

let _navBackdrop = null;

function _openNav() {
  _wireNavClose();
  const sb = document.getElementById('sidebar');
  if (sb) { sb.classList.remove('hidden'); try { window.syncRailSide && window.syncRailSide(); } catch (_) {} }
  document.body.classList.add('dw-nav-open');
  if (!_navBackdrop) {
    _navBackdrop = document.createElement('div');
    _navBackdrop.id = 'dw-nav-backdrop';
    _navBackdrop.addEventListener('click', _closeNav);
    document.body.appendChild(_navBackdrop);
  }
  _navBackdrop.style.display = 'block';
}

function _closeNav() {
  const sb = document.getElementById('sidebar');
  // Re-collapse to the rail-hidden state the workspace runs in.
  if (sb) { sb.classList.add('hidden'); try { window.syncRailSide && window.syncRailSide(); } catch (_) {} }
  document.body.classList.remove('dw-nav-open');
  if (_navBackdrop) _navBackdrop.style.display = 'none';
}

function _toggleNav() {
  if (document.body.classList.contains('dw-nav-open')) _closeNav();
  else _openNav();
}

// While the drawer is open, a click on a sidebar/rail action that opens a
// DIFFERENT full-window view must leave the workspace (otherwise that view
// renders hidden behind it). Overlay actions (search/theme/settings/user) and
// new-chat render above or inside the workspace, so those just close the
// drawer. Capture phase → runs before the app's own handler.
let _navClickWired = false;
function _wireNavClose() {
  if (_navClickWired) return;
  _navClickWired = true;
  document.addEventListener('click', (e) => {
    if (!_open || !document.body.classList.contains('dw-nav-open')) return;
    const btn = e.target.closest && e.target.closest('#sidebar button, #sidebar a, #sidebar [role="button"], #icon-rail button');
    if (!btn) return;
    const label = (btn.getAttribute('title') || btn.textContent || '').trim().toLowerCase();
    if (/search|theme|settings|^user|new chat/.test(label)) { _closeNav(); return; }
    closeWorkspace();   // navigate away to a full-window view
  }, true);
}

// ---- list -----------------------------------------------------------------

function _relTime(iso) {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
  return new Date(t).toLocaleDateString();
}

function _createRow(doc) {
  const row = document.createElement('div');
  row.className = 'doclib-card memory-item dw-row';
  row.dataset.docId = doc.id;
  row.setAttribute('role', 'button');
  row.tabIndex = 0;
  if (doc.id === _activeDocId) row.classList.add('active');

  const content = document.createElement('div');
  content.className = 'dw-row-content';

  const titleEl = document.createElement('div');
  titleEl.className = 'memory-item-title';
  const langSvg = (doc.language && doc.language !== 'text')
    ? langIcon(doc.language, 12, { style: 'vertical-align:-2px;margin-right:4px;opacity:0.55;flex-shrink:0;color:currentColor;' })
    : _GEN_DOC_ICON;
  titleEl.innerHTML = langSvg + uiModule.esc(doc.title || 'Untitled');
  content.appendChild(titleEl);

  const meta = document.createElement('div');
  meta.className = 'memory-item-meta';
  const pieces = [];
  if (doc.session_name) pieces.push(`<span>${uiModule.esc(doc.session_name)}</span>`);
  if (doc.language && doc.language !== 'text') pieces.push(`<span>${uiModule.esc(doc.language)}</span>`);
  const rel = _relTime(doc.updated_at);
  if (rel) pieces.push(`<span>${uiModule.esc(rel)}</span>`);
  meta.innerHTML = pieces.join('<span style="opacity:0.5;">·</span>');
  content.appendChild(meta);

  row.appendChild(content);
  row.addEventListener('click', () => _openDoc(doc));
  row.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _openDoc(doc); }
  });
  return row;
}

function _renderList() {
  const list = _shell && _shell.querySelector('#dw-list');
  if (!list) return;
  list.innerHTML = '';
  if (!_docs.length) {
    const empty = document.createElement('div');
    empty.className = 'dw-empty';
    empty.textContent = _currentSearch() ? 'No documents match.' : 'No documents yet.';
    list.appendChild(empty);
    return;
  }
  const frag = document.createDocumentFragment();
  for (const doc of _docs) frag.appendChild(_createRow(doc));
  list.appendChild(frag);
}

function _highlightActive() {
  if (!_shell) return;
  _shell.querySelectorAll('.dw-row').forEach(r => {
    r.classList.toggle('active', r.dataset.docId === _activeDocId);
  });
}

async function _loadList(search) {
  const seq = ++_listReqSeq;
  const params = new URLSearchParams({ sort: 'recent', offset: '0', limit: '50' });
  if (search) params.set('search', search);
  try {
    const res = await fetch(`${API_BASE}/api/documents/library?${params}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    if (seq !== _listReqSeq) return;  // a newer request superseded this one
    _docs = Array.isArray(data.documents) ? data.documents : [];
    _renderList();
  } catch (e) {
    if (seq !== _listReqSeq) return;
    console.error('Workspace: failed to load documents', e);
  }
}

// ---- right (real chat surface, relocated) ---------------------------------

// Move the single live #chat-container into the right pane (never cloned), so
// the existing doc-scoped agent (active_doc_id) can edit the open document.
function _relocateChat() {
  const chat = document.getElementById('chat-container');
  const right = _shell && _shell.querySelector('#dw-right');
  if (!chat || !right || chat.parentElement === right) return;
  _chatHome = { parent: chat.parentElement, next: chat.nextElementSibling };
  right.appendChild(chat);
}

// Put #chat-container back exactly where it was so the normal app view is intact.
function _restoreChat() {
  const chat = document.getElementById('chat-container');
  if (!chat || !_chatHome) return;
  const { parent, next } = _chatHome;
  if (next && next.parentElement === parent) parent.insertBefore(chat, next);
  else parent.appendChild(chat);
  _chatHome = null;
}

// ---- centre (editor) ------------------------------------------------------

async function _openDoc(doc) {
  if (!doc || !doc.id) return;
  _activeDocId = doc.id;
  _highlightActive();
  const center = _shell.querySelector('#dw-center');
  try {
    await documentModule.openInWorkspace(doc.id, center);
    // Markdown niceties (list continuation / Tab indent / slash menu) on the
    // editor textarea — idempotent, gated to markdown docs internally.
    const ta = document.getElementById('doc-editor-textarea');
    if (ta) attachMdShortcuts(ta);
    // Repaint the side chat to this document's own conversation when it has
    // one. The doc-scoped agent still works either way (active_doc_id is sent
    // whenever the editor is open), so a doc without a session is fine.
    if (doc.session_id && sessionModule.getCurrentSessionId() !== doc.session_id) {
      try { sessionModule.selectSession(doc.session_id); } catch (_) {}
    }
  } catch (e) {
    console.error('Workspace: failed to open document', e);
  }
  if (window.innerWidth <= 768) _setMobilePane('center');
}

async function _newDoc() {
  const center = _shell.querySelector('#dw-center');
  try {
    // Make sure the editor is mounted in the workspace centre first, so
    // createDocument's switchToDoc lands there (not the chat-docked split).
    if (!documentModule.isWorkspaceMode() || !documentModule.isPanelOpen()) {
      await documentModule.openInWorkspace(null, center);
    }
    await documentModule.newDocument();
    _activeDocId = documentModule.getCurrentDocId();
    await _loadList(_currentSearch());
    _highlightActive();
    if (window.innerWidth <= 768) _setMobilePane('center');
  } catch (e) {
    console.error('Workspace: new document failed', e);
  }
}

// ---- public API -----------------------------------------------------------

export function init(apiBase) { API_BASE = apiBase || ''; }

export async function openWorkspace() {
  _buildShell();
  _shell.classList.remove('hidden');
  if (!_open) {
    _open = true;
    document.body.classList.add('doc-workspace-open');
    _setMobilePane('left');
  }
  _relocateChat();
  await _loadList(_currentSearch());
  // Desktop shows all three panes at once, so pre-open the most recent document
  // (the centre isn't blank on entry). On mobile the user lands on the list and
  // taps to open — keeping the list as the entry point for fast switching.
  if (!_activeDocId && _docs.length && window.innerWidth > 768) _openDoc(_docs[0]);
  else _highlightActive();
}

export function closeWorkspace() {
  if (!_open || !_shell) return;
  _open = false;
  _closeNav();   // dismiss the sidebar drawer + backdrop if it was open
  // Restore the chat to its home BEFORE hiding the shell so it's back in the
  // normal app view, then tear down the workspace editor.
  _restoreChat();
  _shell.classList.add('hidden');
  document.body.classList.remove('doc-workspace-open');
  try { documentModule.closePanel('workspace'); } catch (_) {}
  _activeDocId = null;
}

export function isOpen() { return _open; }

// Live-update the list when documents change (user edits, agent tools,
// create/delete) — debounced so autosave keystrokes don't thrash the fetch.
if (typeof window !== 'undefined') {
  window.addEventListener('documents-refresh', () => {
    if (!_open) return;
    clearTimeout(_refreshTimer);
    _refreshTimer = setTimeout(() => _loadList(_currentSearch()), 500);
  });
}

const documentWorkspaceModule = { init, openWorkspace, closeWorkspace, isOpen };
export default documentWorkspaceModule;
if (typeof window !== 'undefined') window.documentWorkspaceModule = documentWorkspaceModule;
