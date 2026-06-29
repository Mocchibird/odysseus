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

import documentModule from './document.js?v=484';
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

// Folder/tree icons (reuse the .notes-vault-* vocabulary, StandardNotes-style).
const _FOLDER_SVG = _icon('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>', 14);
const _CHEVRON_SVG = _icon('<polyline points="9 6 15 12 9 18"/>', 13);
const _TRASH_SVG = _icon('<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>', 14);
const _RESTORE_SVG = _icon('<path d="M3 7v6h6"/><path d="M3 13a9 9 0 1 0 3-7.7L3 8"/>', 14);
const _GEN_DOC_16 = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';

let _expanded = _loadExpanded();   // Set of open folder keys (persisted)
let _trashDocs = null;             // lazy-loaded soft-deleted docs

function _loadExpanded() {
  try { return new Set(JSON.parse(localStorage.getItem('odysseus-dw-expanded') || '[]')); }
  catch (_) { return new Set(); }
}
function _persistExpanded() {
  try { localStorage.setItem('odysseus-dw-expanded', JSON.stringify([..._expanded])); } catch (_) {}
}
function _toggleFolder(key) {
  if (_expanded.has(key)) _expanded.delete(key); else _expanded.add(key);
  _persistExpanded();
  _renderList();
}

// Build a nested tag tree by splitting each tag on "/" (StandardNotes nesting).
function _buildTagTree(docs) {
  const root = { children: new Map(), docs: [], _ids: new Set() };
  const untagged = [];
  for (const doc of docs) {
    const tags = (Array.isArray(doc.tags) ? doc.tags : []).filter(t => (t || '').trim());
    if (!tags.length) { untagged.push(doc); continue; }
    for (const tag of tags) {
      const segs = tag.split('/').map(s => s.trim()).filter(Boolean);
      if (!segs.length) continue;
      let node = root, path = '';
      for (const seg of segs) {
        path = path ? path + '/' + seg : seg;
        if (!node.children.has(seg)) node.children.set(seg, { name: seg, fullPath: path, children: new Map(), docs: [], _ids: new Set() });
        node = node.children.get(seg);
      }
      if (!node._ids.has(doc.id)) { node._ids.add(doc.id); node.docs.push(doc); }
    }
  }
  return { root, untagged };
}
function _countNode(node) {
  let c = node.docs.length;
  for (const ch of node.children.values()) c += _countNode(ch);
  node.count = c;
  return c;
}

function _emptyEl(text) {
  const e = document.createElement('div');
  e.className = 'dw-empty';
  e.textContent = text;
  return e;
}

function _folderRow(name, key, count, depth, icon) {
  const f = document.createElement('div');
  f.className = 'notes-vault-folder dw-folder' + (_expanded.has(key) ? ' open' : '');
  f.style.setProperty('--vault-indent', (depth * 14) + 'px');
  f.setAttribute('role', 'button');
  f.tabIndex = 0;
  f.innerHTML = `<span class="notes-vault-folder-chevron">${_CHEVRON_SVG}</span>`
    + `<span class="notes-vault-folder-icon">${icon || _FOLDER_SVG}</span>`
    + `<span class="notes-vault-folder-name">${uiModule.esc(name)}</span>`
    + `<span class="notes-vault-folder-count">${count}</span>`;
  return f;
}

function _fileRow(doc, depth, opts = {}) {
  const row = document.createElement('div');
  row.className = 'notes-vault-file dw-file';
  row.dataset.docId = doc.id;
  row.setAttribute('role', 'button');
  row.tabIndex = 0;
  if (depth) row.style.setProperty('--vault-indent', (depth * 14) + 'px');
  if (doc.id === _activeDocId && !opts.trash) row.classList.add('active');
  const lang = (doc.language || '').toLowerCase();
  const langSvg = (lang && lang !== 'text' && lang !== 'markdown')
    ? langIcon(lang, 16, { style: 'color:currentColor;' })
    : _GEN_DOC_16;
  const rel = _relTime(doc.updated_at);
  const preview = (doc.preview || '').replace(/[#>*`_~\[\]]/g, ' ').replace(/\s+/g, ' ').trim();
  row.innerHTML =
    `<span class="notes-vault-file-icon">${langSvg}</span>`
    + `<span class="notes-vault-file-main">`
      + `<span class="notes-vault-file-title">${uiModule.esc(doc.title || 'Untitled')}</span>`
      + (preview ? `<span class="notes-vault-file-excerpt">${uiModule.esc(preview)}</span>` : '')
    + `</span>`
    + `<span class="notes-vault-file-meta">${uiModule.esc(rel)}</span>`
    + (opts.trash ? `<button type="button" class="notes-vault-file-actions dw-restore" title="Restore" aria-label="Restore">${_RESTORE_SVG}</button>` : '<span></span>');
  if (opts.trash) {
    row.querySelector('.dw-restore').addEventListener('click', (e) => { e.stopPropagation(); _restoreDoc(doc.id); });
  } else {
    row.addEventListener('click', () => _openDoc(doc));
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _openDoc(doc); } });
  }
  return row;
}

function _renderNode(parent, node, depth) {
  const key = 't:' + node.fullPath;
  const folder = _folderRow(node.name, key, node.count, depth);
  folder.addEventListener('click', () => _toggleFolder(key));
  folder.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _toggleFolder(key); } });
  parent.appendChild(folder);
  if (!_expanded.has(key)) return;
  for (const ch of [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name))) _renderNode(parent, ch, depth + 1);
  for (const doc of node.docs) parent.appendChild(_fileRow(doc, depth + 1));
}

function _renderList() {
  const list = _shell && _shell.querySelector('#dw-list');
  if (!list) return;
  list.innerHTML = '';
  list.className = 'dw-list notes-vault-list notes-vault-tree';
  const q = _currentSearch();

  // While searching, show a flat list of matches (folders only get in the way).
  if (q) {
    if (!_docs.length) { list.appendChild(_emptyEl('No documents match.')); return; }
    const frag = document.createDocumentFragment();
    for (const doc of _docs) frag.appendChild(_fileRow(doc, 0));
    list.appendChild(frag);
    return;
  }

  const frag = document.createDocumentFragment();
  const { root, untagged } = _buildTagTree(_docs);
  for (const ch of root.children.values()) _countNode(ch);
  if (!_docs.length) frag.appendChild(_emptyEl('No documents yet.'));
  for (const node of [...root.children.values()].sort((a, b) => a.name.localeCompare(b.name))) _renderNode(frag, node, 0);

  // Untagged group (after the tag folders)
  if (untagged.length) {
    const key = 'untagged';
    const f = _folderRow('Untagged', key, untagged.length, 0);
    f.addEventListener('click', () => _toggleFolder(key));
    frag.appendChild(f);
    if (_expanded.has(key)) for (const doc of untagged) frag.appendChild(_fileRow(doc, 1));
  }

  // Trash group (lazy-loaded soft-deleted docs, with per-row Restore)
  const tkey = 'trash';
  const tf = _folderRow('Trash', tkey, _trashDocs ? _trashDocs.length : '·', 0, _TRASH_SVG);
  tf.addEventListener('click', () => _toggleTrash());
  frag.appendChild(tf);
  if (_expanded.has(tkey)) {
    if (_trashDocs === null) frag.appendChild(_emptyEl('Loading…'));
    else if (!_trashDocs.length) frag.appendChild(_emptyEl('Trash is empty.'));
    else for (const doc of _trashDocs) frag.appendChild(_fileRow(doc, 1, { trash: true }));
  }

  list.appendChild(frag);
}

function _toggleTrash() {
  const opening = !_expanded.has('trash');
  if (opening) _expanded.add('trash'); else _expanded.delete('trash');
  _persistExpanded();
  _renderList();
  if (opening && _trashDocs === null) _loadTrash().then(() => _renderList());
}

async function _loadTrash() {
  try {
    const res = await fetch(`${API_BASE}/api/documents/trash`, { credentials: 'same-origin' });
    _trashDocs = res.ok ? ((await res.json()).documents || []) : [];
  } catch (_) { _trashDocs = []; }
}

async function _restoreDoc(id) {
  try {
    const res = await fetch(`${API_BASE}/api/document/${id}/restore`, { method: 'POST', credentials: 'same-origin' });
    if (!res.ok) throw new Error(res.statusText);
    if (uiModule) uiModule.showToast('Document restored');
    _trashDocs = null;                  // force a fresh trash list
    if (_expanded.has('trash')) await _loadTrash();
    await _loadList(_currentSearch());  // the restored doc reappears in the tree
  } catch (e) {
    console.error('Workspace: restore failed', e);
    if (uiModule) uiModule.showError('Restore failed');
  }
}

function _highlightActive() {
  if (!_shell) return;
  _shell.querySelectorAll('.dw-file').forEach(r => {
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
    // The open document binds to whatever chat is current via active_doc_id
    // (a fresh one if the workspace was just opened — see openWorkspace), so we
    // deliberately DON'T switch the side chat to the doc's old session here.
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

export async function openWorkspace(docId) {
  const wasClosed = !_open;
  _buildShell();
  _shell.classList.remove('hidden');
  if (!_open) {
    _open = true;
    document.body.classList.add('doc-workspace-open');
    _setMobilePane('left');
  }
  // Opening the workspace from a closed state starts a FRESH chat (with the
  // configured default model). The document opened below then binds to it via
  // active_doc_id — so each time you enter the workspace you get a clean
  // assistant for the doc, instead of the doc's stale prior conversation.
  if (wasClosed) { try { await window.__odysseusStartDefaultChat?.(); } catch (_) {} }
  _relocateChat();
  await _loadList(_currentSearch());
  if (docId) {
    // Explicit doc (e.g. opened from the Library) — open it in the centre.
    _openDoc(_docs.find(d => d.id === docId) || { id: docId });
  } else if (!_activeDocId && _docs.length && window.innerWidth > 768) {
    // Desktop shows all three panes at once, so pre-open the most recent document
    // (the centre isn't blank on entry). On mobile the user lands on the list and
    // taps to open — keeping the list as the entry point for fast switching.
    _openDoc(_docs[0]);
  } else {
    _highlightActive();
  }
}

export function closeWorkspace() {
  if (!_open || !_shell) return;
  _open = false;
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
