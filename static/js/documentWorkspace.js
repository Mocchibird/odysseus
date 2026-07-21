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

import documentModule from './document.js?v=530';
import uiModule from './ui.js';
import { langIcon } from './langIcons.js';
import { attachMdShortcuts } from './mdShortcuts.js?v=478';
import { bindMenuDismiss, dismissOrRemove, topPopupZ } from './escMenuStack.js';

let API_BASE = '';
let _open = false;
let _shell = null;
let _chatHome = null;           // { parent, next } — exact restore slot for #chat-container (phase 4)
let _docs = [];                 // last-fetched document list
let _activeDocId = null;        // doc currently open in the centre editor
let _searchTimer = null;        // debounce for the search box
let _refreshTimer = null;       // debounce for documents-refresh re-fetch
let _listReqSeq = 0;            // guards against out-of-order list fetches
let _knownTags = new Set();     // user-created tag paths (incl. empty folders), persisted via /api/prefs
let _sortPrefs = {};            // { __default:'recent', '<tagPath>':'name'|'recent'|'oldest' } via /api/prefs
let _pinned = [];               // ordered pinned doc ids (display order; new pins prepend) via /api/prefs
let _pinnedSet = new Set();     // _pinned as a set for O(1) lookup
let _draggingId = null;         // doc id currently being dragged (for pinned reorder)
let _tagMenuEl = null;          // body-appended folder-actions menu (swept on every re-render)
let _fileMenuEl = null;         // body-appended per-file "…" actions menu (swept on every re-render)
let _tagInputMode = null;       // { action: 'create'|'subtag'|'rename', path } for the New-tag bar

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
// Folder with a plus — creates a new tag (folder).
const _ICON_NEWTAG = _icon('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><line x1="12" y1="10" x2="12" y2="16"/><line x1="9" y1="13" x2="15" y2="13"/>', 14);
// Floppy disk — manual save.
const _ICON_SAVE = _icon('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>', 14);
// Pin (pinned-note marker + the pin/unpin action).
const _ICON_PIN = _icon('<path d="M9 4v6l-2 4h10l-2-4V4"/><line x1="12" y1="14" x2="12" y2="21"/><line x1="8" y1="4" x2="16" y2="4"/>', 13);
// Sort (descending bars) — the sort control.
const _ICON_SORT = _icon('<line x1="4" y1="6" x2="16" y2="6"/><line x1="4" y1="12" x2="12" y2="12"/><line x1="4" y1="18" x2="8" y2="18"/>', 14);

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
      <div class="dw-tagbar">
        <button class="memory-toolbar-btn dw-newtag" id="dw-newtag" type="button" title="New tag (folder)">${_ICON_NEWTAG}<span>New tag</span></button>
        <button class="memory-toolbar-btn dw-sortbtn" id="dw-sortbtn" type="button" title="Default sort order">${_ICON_SORT}<span>Sort</span></button>
        <input type="text" id="dw-tag-input" class="memory-search-input dw-tag-input hidden" placeholder="tag name (use / to nest)" autocomplete="off" />
      </div>
      <div class="dw-list" id="dw-list" role="list"></div>
      <div class="dw-left-resizer" id="dw-left-resizer" title="Drag to resize the list" aria-hidden="true"></div>
    </div>
    <div class="dw-center" id="dw-center"></div>
    <div class="dw-right" id="dw-right">
      <div class="dw-ai-head">
        <span class="dw-ai-title">Assist</span>
        <button class="memory-item-btn dw-ai-newchat" id="dw-ai-newchat" title="New chat" aria-label="New chat">${_ICON_NEWCHAT}</button>
        <button class="memory-item-btn dw-ai-hide" id="dw-ai-hide" title="Hide assistant" aria-label="Hide assistant">${_ICON_CLOSE}</button>
      </div>
    </div>
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
  // Restore the persisted list width + wire the drag-to-resize handle. The
  // "Assist" open-button now lives in the editor footer (see _mountFooterControls).
  try { const _w = localStorage.getItem('odysseus-dw-left-w'); if (_w) el.style.setProperty('--dw-left-w', _w); } catch (_) {}
  _wireResizer();
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

  // New-tag bar: the button opens an inline input that also serves the
  // per-folder "Add subtag" / "Rename" actions (one input, mode-driven).
  el.querySelector('#dw-newtag').addEventListener('click', () => _beginTagInput('create'));
  // Default (global) sort order — per-folder overrides live in each folder's … menu.
  el.querySelector('#dw-sortbtn').addEventListener('click', (e) => _showSortMenu(e.currentTarget, null));
  const tagInput = el.querySelector('#dw-tag-input');
  tagInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); _commitTagInput(); }
    else if (e.key === 'Escape') { e.preventDefault(); _endTagInput(); }
  });
  tagInput.addEventListener('blur', () => _endTagInput());
  return el;
}

function _setMobilePane(pane) {
  if (!_shell) return;
  _shell.setAttribute('data-pane', pane);
  _shell.querySelectorAll('.dw-mtab').forEach(b => b.classList.toggle('active', b.dataset.pane === pane));
}

// Drag-to-resize the left list: the handle on the list's right edge sets the
// --dw-left-w grid column width (clamped + persisted). Desktop only.
function _wireResizer() {
  const rez = _shell.querySelector('#dw-left-resizer');
  if (!rez) return;
  const onMove = (e) => {
    let w = e.clientX - _shell.getBoundingClientRect().left;
    w = Math.max(200, Math.min(560, w));
    _shell.style.setProperty('--dw-left-w', w + 'px');
  };
  const onUp = () => {
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onUp);
    document.body.classList.remove('dw-resizing');
    try { localStorage.setItem('odysseus-dw-left-w', _shell.style.getPropertyValue('--dw-left-w')); } catch (_) {}
  };
  rez.addEventListener('pointerdown', (e) => {
    if (window.innerWidth <= 768) return;   // single-column on mobile — no resize
    e.preventDefault();
    document.body.classList.add('dw-resizing');
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
  });
}

// Save the open document (manual save) — mirrors Cmd/Ctrl+S; documentModule
// shows its own "Document saved" toast.
function _saveDoc() {
  try { documentModule.saveDocument(); } catch (e) { console.error('Workspace: save failed', e); }
}

// Mount the workspace's "Save" + "Assist" controls into the editor's bottom
// action footer (right of the Copy/Export split), reusing the footer's own
// button vocabulary. Idempotent — safe to call on every doc open.
function _mountFooterControls() {
  const footer = document.getElementById('doc-actions-footer');
  if (!footer) return;
  const split = footer.querySelector('.email-send-split');
  let save = footer.querySelector('#dw-footer-save');
  if (!save) {
    save = document.createElement('button');
    save.type = 'button';
    save.id = 'dw-footer-save';
    save.className = 'doc-action-icon-btn dw-footer-ctl';
    save.title = 'Save (Ctrl/Cmd+S)';
    save.innerHTML = `${_ICON_SAVE}<span>Save</span>`;
    save.addEventListener('click', _saveDoc);
  }
  let assist = footer.querySelector('#dw-footer-assist');
  if (!assist) {
    assist = document.createElement('button');
    assist.type = 'button';
    assist.id = 'dw-footer-assist';
    assist.className = 'doc-action-icon-btn dw-footer-ctl dw-footer-assist';
    assist.title = 'Open the assistant';
    assist.innerHTML = _ICON_CHAT;   // icon only — the bubble is self-explanatory
    assist.setAttribute('aria-label', 'Open the assistant');
    assist.addEventListener('click', () => {
      if (!_shell) return;
      _shell.classList.remove('dw-chat-collapsed');
      if (window.innerWidth <= 768) _setMobilePane('right');
    });
  }
  // Re-assert position on every call (the footer's own reorder runs first):
  // Save immediately LEFT of Copy, Assist immediately RIGHT of it.
  if (split) { split.before(save); split.after(assist); }
  else { footer.appendChild(save); footer.appendChild(assist); }
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
const _DOTS_SVG = _icon('<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>', 14);
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

// Resolve (creating as needed) the nested node for a "parent/child" tag path.
function _ensurePath(root, fullPath) {
  const segs = String(fullPath || '').split('/').map(s => s.trim()).filter(Boolean);
  if (!segs.length) return null;
  let node = root, path = '';
  for (const seg of segs) {
    path = path ? path + '/' + seg : seg;
    if (!node.children.has(seg)) node.children.set(seg, { name: seg, fullPath: path, children: new Map(), docs: [], _ids: new Set() });
    node = node.children.get(seg);
  }
  return node;
}

// Build a nested tag tree by splitting each tag on "/" (StandardNotes nesting).
// Known (user-created) tags are seeded too, so an empty folder you just made
// still shows up before any document carries it.
function _buildTagTree(docs) {
  const root = { children: new Map(), docs: [], _ids: new Set() };
  const untagged = [];
  for (const doc of docs) {
    const tags = (Array.isArray(doc.tags) ? doc.tags : []).filter(t => (t || '').trim());
    if (!tags.length) { untagged.push(doc); continue; }
    for (const tag of tags) {
      const node = _ensurePath(root, tag);
      if (node && !node._ids.has(doc.id)) { node._ids.add(doc.id); node.docs.push(doc); }
    }
  }
  for (const t of _knownTags) _ensurePath(root, t);
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
  row.title = doc.title || 'Untitled';   // full title on hover (rows truncate)
  row.setAttribute('role', 'button');
  row.tabIndex = 0;
  if (depth) row.style.setProperty('--vault-indent', (depth * 14) + 'px');
  if (doc.id === _activeDocId && !opts.trash) row.classList.add('active');
  const pinned = !opts.trash && _pinnedSet.has(doc.id);
  if (pinned) row.classList.add('dw-pinned');
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
    + `<span class="notes-vault-file-meta">${pinned ? `<span class="dw-pin-mark" title="Pinned">${_ICON_PIN}</span>` : ''}${uiModule.esc(rel)}</span>`
    + (opts.trash
        ? `<button type="button" class="notes-vault-file-actions dw-restore" title="Restore" aria-label="Restore">${_RESTORE_SVG}</button>`
        : `<button type="button" class="notes-vault-file-actions dw-file-actions" title="Actions" aria-label="Document actions">${_DOTS_SVG}</button>`);
  if (opts.trash) {
    row.querySelector('.dw-restore').addEventListener('click', (e) => { e.stopPropagation(); _restoreDoc(doc.id); });
  } else {
    // The row opens the doc; the … button + its menu must not bubble into that.
    row.addEventListener('click', (e) => { if (e.target.closest('.dw-file-actions')) return; _openDoc(doc); });
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _openDoc(doc); } });
    row.querySelector('.dw-file-actions').addEventListener('click', (e) => { e.stopPropagation(); _showFileMenu(e.currentTarget, doc); });
    // Drag a note onto a tag folder (or "Untagged") to (re)assign its tags;
    // drag a PINNED note onto another pinned note to reorder the pins.
    row.draggable = true;
    row.addEventListener('dragstart', (e) => {
      _draggingId = doc.id;
      try { e.dataTransfer.setData('text/plain', doc.id); } catch (_) {}
      e.dataTransfer.effectAllowed = 'copyMove';   // copy = tag-assign, move = reorder
      row.classList.add('dragging');
      if (_shell) _shell.classList.add('dw-dragging');
    });
    row.addEventListener('dragend', () => {
      _draggingId = null;
      row.classList.remove('dragging');
      if (_shell) {
        _shell.classList.remove('dw-dragging');
        _shell.querySelectorAll('.drag-over, .dw-reorder-over').forEach(el => el.classList.remove('drag-over', 'dw-reorder-over'));
      }
    });
    // Pinned rows double as reorder drop targets: dropping another pinned note
    // here moves it directly above this one in the pinned order.
    if (pinned) {
      row.addEventListener('dragover', (e) => {
        if (!_draggingId || _draggingId === doc.id || !_pinnedSet.has(_draggingId)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        row.classList.add('dw-reorder-over');
      });
      row.addEventListener('dragleave', () => row.classList.remove('dw-reorder-over'));
      row.addEventListener('drop', (e) => {
        if (!_draggingId || !_pinnedSet.has(_draggingId)) return;
        e.preventDefault(); e.stopPropagation();
        row.classList.remove('dw-reorder-over');
        _reorderPinned(_draggingId, doc.id);
      });
    }
  }
  return row;
}

function _renderNode(parent, node, depth) {
  const key = 't:' + node.fullPath;
  const folder = _folderRow(node.name, key, node.count, depth);
  _makeDropTarget(folder, node.fullPath);    // drop a note here → tag it with this path
  _attachFolderActions(folder, node);        // hover … menu: add subtag / rename / delete
  folder.addEventListener('click', (e) => { if (e.target.closest('.dw-folder-actions')) return; _toggleFolder(key); });
  folder.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _toggleFolder(key); } });
  parent.appendChild(folder);
  if (!_expanded.has(key)) return;
  for (const ch of [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name))) _renderNode(parent, ch, depth + 1);
  for (const doc of _sortDocs(node.docs, node.fullPath)) parent.appendChild(_fileRow(doc, depth + 1));
}

function _renderList() {
  const list = _shell && _shell.querySelector('#dw-list');
  if (!list) return;
  _closeFolderMenu();   // a body-appended folder menu can't outlive the rows it anchors to
  _closeFileMenu();     // ditto for the per-file "…" menu
  list.innerHTML = '';
  list.className = 'dw-list notes-vault-list notes-vault-tree';
  const q = _currentSearch();

  // While searching, show a flat list of matches (folders only get in the way).
  if (q) {
    if (!_docs.length) { list.appendChild(_emptyEl('No documents match.')); return; }
    const frag = document.createDocumentFragment();
    for (const doc of _sortDocs(_docs, null)) frag.appendChild(_fileRow(doc, 0));
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
    _makeDropTarget(f, null);   // drop a note here → clear all its tags
    f.addEventListener('click', () => _toggleFolder(key));
    frag.appendChild(f);
    if (_expanded.has(key)) for (const doc of _sortDocs(untagged, null)) frag.appendChild(_fileRow(doc, 1));
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

// ---- tags: persistence / assign (drag-drop) / create / rename / delete ----

async function _loadKnownTags() {
  try {
    const res = await fetch(`${API_BASE}/api/prefs/dw_known_tags`, { credentials: 'same-origin' });
    if (res.ok) {
      const v = (await res.json()).value;
      if (Array.isArray(v)) _knownTags = new Set(v.map(t => String(t || '').trim()).filter(Boolean));
    }
  } catch (_) { /* prefs unavailable → empty folders just won't persist across reloads */ }
}
function _saveKnownTags() {
  try {
    fetch(`${API_BASE}/api/prefs/dw_known_tags`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: [..._knownTags] }),
    });
  } catch (_) { /* best-effort */ }
}

// ---- sort prefs + pinning (per-folder sort + pinned-first), persisted -------

async function _loadSortPrefs() {
  try {
    const res = await fetch(`${API_BASE}/api/prefs/dw_sort`, { credentials: 'same-origin' });
    if (res.ok) { const v = (await res.json()).value; if (v && typeof v === 'object' && !Array.isArray(v)) _sortPrefs = v; }
  } catch (_) { /* prefs unavailable → default sort */ }
}
function _saveSortPrefs() {
  try {
    fetch(`${API_BASE}/api/prefs/dw_sort`, {
      method: 'PUT', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: _sortPrefs }),
    });
  } catch (_) { /* best-effort */ }
}
async function _loadPinned() {
  try {
    const res = await fetch(`${API_BASE}/api/prefs/dw_pinned`, { credentials: 'same-origin' });
    if (res.ok) { const v = (await res.json()).value; if (Array.isArray(v)) _pinned = v.map(x => String(x || '')).filter(Boolean); }
  } catch (_) { /* prefs unavailable → nothing pinned */ }
  _pinnedSet = new Set(_pinned);
}
function _savePinned() {
  _pinnedSet = new Set(_pinned);
  try {
    fetch(`${API_BASE}/api/prefs/dw_pinned`, {
      method: 'PUT', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: _pinned }),
    });
  } catch (_) { /* best-effort */ }
}

const _SORTS = { name: 'Name', recent: 'Recent', oldest: 'Oldest' };
// Resolve the sort for a tag path: per-folder pref → global default → 'recent'.
function _sortFor(tagPath) {
  return _sortPrefs[tagPath == null ? '__default' : tagPath] || _sortPrefs.__default || 'recent';
}
function _docCmp(key) {
  if (key === 'name') {
    return (a, b) => (a.title || 'Untitled').localeCompare(b.title || 'Untitled', undefined, { numeric: true, sensitivity: 'base' });
  }
  const ts = (d) => { const t = Date.parse(d.updated_at || d.created_at || ''); return isNaN(t) ? 0 : t; };
  if (key === 'oldest') return (a, b) => ts(a) - ts(b);
  return (a, b) => ts(b) - ts(a);   // 'recent' (newest first) — default
}
// Order a group of docs: pinned first (in _pinned order), then the rest by the
// folder's sort. Defensive — a comparator/data glitch falls back to the input
// order rather than blanking the list.
function _sortDocs(docs, tagPath) {
  try {
    const pinned = [], rest = [];
    for (const d of docs) (_pinnedSet.has(d.id) ? pinned : rest).push(d);
    pinned.sort((a, b) => _pinned.indexOf(a.id) - _pinned.indexOf(b.id));
    rest.sort(_docCmp(_sortFor(tagPath)));
    return pinned.concat(rest);
  } catch (e) { console.error('Workspace: sort failed', e); return docs; }
}
function _togglePin(doc) {
  const id = doc && doc.id;
  if (!id) return;
  if (_pinnedSet.has(id)) _pinned = _pinned.filter(x => x !== id);
  else _pinned.unshift(id);   // new pins go to the TOP of their group
  _savePinned();
  _renderList();
}
// Manual pin order: move `draggedId` to directly before `targetId`.
function _reorderPinned(draggedId, targetId) {
  if (!draggedId || draggedId === targetId) return;
  const arr = _pinned.filter(x => x !== draggedId);
  const ti = arr.indexOf(targetId);
  if (ti < 0) arr.unshift(draggedId); else arr.splice(ti, 0, draggedId);
  _pinned = arr;
  _savePinned();
  _renderList();
}
// Shared sort picker for the global Sort button (tagPath=null → __default) and
// each folder's … menu (tagPath=the folder). Reuses the _fileMenuEl sweep slot.
function _showSortMenu(anchor, tagPath) {
  _closeFileMenu(); _closeFolderMenu();
  const cur = _sortFor(tagPath);
  const menu = document.createElement('div');
  menu.className = 'dw-folder-menu dw-sort-menu';
  menu.innerHTML = Object.entries(_SORTS).map(([k, label]) =>
    `<button type="button" class="${k === cur ? 'active' : ''}" data-sort="${k}">${label}</button>`).join('');
  document.body.appendChild(menu);
  _positionMenu(menu, anchor);
  const close = bindMenuDismiss(menu, () => { try { menu.remove(); } catch (_) {} if (_fileMenuEl === menu) _fileMenuEl = null; });
  _fileMenuEl = menu;
  menu.querySelectorAll('button').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const k = b.dataset.sort;
    close();
    if (tagPath == null) _sortPrefs.__default = k; else _sortPrefs[tagPath] = k;
    _saveSortPrefs();
    _renderList();
  }));
}

// Drop a dragged note onto `el` to (re)tag it: tagPath = the folder's path, or
// null for the "Untagged" bin (which clears the note's tags).
function _makeDropTarget(el, tagPath) {
  el.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; el.classList.add('drag-over'); });
  el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    el.classList.remove('drag-over');
    let id = '';
    try { id = e.dataTransfer.getData('text/plain'); } catch (_) {}
    if (id) _assignTag(id, tagPath);
  });
}

async function _postTags(doc, next) {
  const res = await fetch(`${API_BASE}/api/document/${doc.id}/tags?tags=${encodeURIComponent(next.join(','))}`, { method: 'POST', credentials: 'same-origin' });
  if (!res.ok) throw new Error(res.statusText);
  const data = await res.json();
  doc.tags = Array.isArray(data.tags) ? data.tags : next;
  return doc.tags;
}

async function _assignTag(docId, tagPath) {
  const doc = _docs.find(d => d.id === docId);
  if (!doc) return;
  const cur = Array.isArray(doc.tags) ? doc.tags.slice() : [];
  let next;
  if (tagPath === null) {
    if (!cur.length) return;                                              // already untagged
    next = [];
  } else {
    if (cur.some(t => t.toLowerCase() === tagPath.toLowerCase())) return;  // already has this tag
    next = [...cur, tagPath];
  }
  try {
    await _postTags(doc, next);
    if (uiModule) uiModule.showToast(tagPath === null ? 'Tags cleared' : `Tagged “${doc.title || 'Untitled'}” → ${tagPath}`);
    _renderList();
  } catch (e) {
    console.error('Workspace: assign tag failed', e);
    if (uiModule) uiModule.showError('Failed to assign tag');
  }
}

// Position a body-appended menu under its anchor, but flip it ABOVE when there
// isn't room below (so a row near the screen bottom doesn't get its menu cut
// off), and clamp to the viewport on both axes. The menu must already be in the
// DOM (so offsetHeight/Width are measurable).
function _positionMenu(menu, anchor) {
  const r = anchor.getBoundingClientRect();
  menu.style.position = 'fixed';
  menu.style.zIndex = String(topPopupZ());
  const mh = menu.offsetHeight || 0;
  const mw = menu.offsetWidth || 160;
  const vh = window.innerHeight, vw = window.innerWidth;
  let top;
  if (vh - r.bottom >= mh + 8) top = r.bottom + 4;        // fits below
  else if (r.top >= mh + 8) top = r.top - mh - 4;         // flip above
  else top = vh - mh - 8;                                  // fits neither → pin to bottom
  const left = Math.min(r.left, vw - mw - 8);
  menu.style.top = `${Math.round(Math.max(8, top))}px`;
  menu.style.left = `${Math.round(Math.max(8, left))}px`;
}

// ---- folder actions menu (… on hover) ------------------------------------

function _closeFolderMenu() {
  if (!_tagMenuEl) return;
  try { dismissOrRemove(_tagMenuEl); } catch (_) { try { _tagMenuEl.remove(); } catch (__) {} }
  _tagMenuEl = null;
}

function _attachFolderActions(folder, node) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'memory-item-btn dw-folder-actions';
  btn.title = 'Tag actions';
  btn.setAttribute('aria-label', 'Tag actions');
  btn.innerHTML = _icon('<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>', 14);
  btn.addEventListener('click', (e) => { e.stopPropagation(); _showFolderMenu(btn, node); });
  folder.appendChild(btn);
}

function _showFolderMenu(anchor, node) {
  _closeFolderMenu();
  const menu = document.createElement('div');
  menu.className = 'dw-folder-menu';
  menu.innerHTML =
    '<button type="button" data-act="subtag">Add subtag</button>'
    + '<button type="button" data-act="sort">Sort by…</button>'
    + '<button type="button" data-act="rename">Rename</button>'
    + '<button type="button" data-act="delete" class="dw-folder-menu-del">Delete tag</button>';
  document.body.appendChild(menu);
  _positionMenu(menu, anchor);
  const close = bindMenuDismiss(menu, () => { try { menu.remove(); } catch (_) {} if (_tagMenuEl === menu) _tagMenuEl = null; });
  _tagMenuEl = menu;
  menu.querySelectorAll('button').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const act = b.dataset.act;
    close();
    if (act === 'subtag') _beginTagInput('subtag', node.fullPath);
    else if (act === 'sort') _showSortMenu(anchor, node.fullPath);   // per-folder sort
    else if (act === 'rename') _beginTagInput('rename', node.fullPath);
    else if (act === 'delete') _deleteTag(node);
  }));
}

// ---- per-file "…" actions menu (Open / Rename / Edit tags / Duplicate / Delete) ----

function _closeFileMenu() {
  if (!_fileMenuEl) return;
  try { dismissOrRemove(_fileMenuEl); } catch (_) { try { _fileMenuEl.remove(); } catch (__) {} }
  _fileMenuEl = null;
}

function _showFileMenu(anchor, doc) {
  _closeFileMenu();
  const menu = document.createElement('div');
  menu.className = 'dw-folder-menu dw-file-menu';   // reuse the folder-menu styling
  menu.innerHTML =
    '<button type="button" data-act="open">Open</button>'
    + `<button type="button" data-act="pin">${_pinnedSet.has(doc.id) ? 'Unpin' : 'Pin'}</button>`
    + '<button type="button" data-act="rename">Rename</button>'
    + '<button type="button" data-act="tags">Edit tags</button>'
    + '<button type="button" data-act="duplicate">Duplicate</button>'
    + '<button type="button" data-act="delete" class="dw-folder-menu-del">Delete</button>';
  document.body.appendChild(menu);
  _positionMenu(menu, anchor);
  const close = bindMenuDismiss(menu, () => { try { menu.remove(); } catch (_) {} if (_fileMenuEl === menu) _fileMenuEl = null; });
  _fileMenuEl = menu;
  menu.querySelectorAll('button').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const act = b.dataset.act;
    close();
    if (act === 'open') _openDoc(doc);
    else if (act === 'pin') _togglePin(doc);
    else if (act === 'rename') _renameDoc(doc);
    else if (act === 'tags') _editDocTags(doc);
    else if (act === 'duplicate') _duplicateDoc(doc);
    else if (act === 'delete') _deleteDoc(doc);
  }));
}

async function _renameDoc(doc) {
  const next = await uiModule.styledPrompt('Rename this document.', {
    title: 'Rename', defaultValue: doc.title || '', placeholder: 'Document title', confirmText: 'Rename', maxLength: 200,
  });
  if (next === null) return;
  const title = next.trim();
  if (!title || title === doc.title) return;
  try {
    // updateTitle PATCHes the title AND updates the editor's open tab if this doc
    // is currently open there (single source of truth) — see document.js.
    await documentModule.updateTitle(doc.id, title);
    doc.title = title;
    if (uiModule) uiModule.showToast('Renamed');
    _renderList();
  } catch (e) {
    console.error('Workspace: rename failed', e);
    if (uiModule) uiModule.showError('Rename failed');
  }
}

async function _editDocTags(doc) {
  const cur = Array.isArray(doc.tags) ? doc.tags.join(', ') : '';
  const next = await uiModule.styledPrompt('Comma-separated tags (use "/" to nest, e.g. Tech/Kernel).', {
    title: 'Edit tags', defaultValue: cur, placeholder: 'tag1, tag2', confirmText: 'Save', maxLength: 500,
  });
  if (next === null) return;
  const tags = next.split(',').map(t => t.trim()).filter(Boolean);
  try {
    await _postTags(doc, tags);
    if (uiModule) uiModule.showToast(tags.length ? `Tagged: ${tags.join(', ')}` : 'Tags cleared');
    _renderList();
  } catch (e) {
    console.error('Workspace: edit tags failed', e);
    if (uiModule) uiModule.showError('Failed to set tags');
  }
}

// No server clone endpoint — duplicate = GET the source, POST a fresh doc, then
// copy its tags, then refresh the list.
async function _duplicateDoc(doc) {
  try {
    const res = await fetch(`${API_BASE}/api/document/${doc.id}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(res.statusText);
    const src = await res.json();
    const body = {
      title: `${src.title || doc.title || 'Untitled'} (copy)`,
      language: src.language || doc.language || undefined,
      content: src.current_content != null ? src.current_content : (src.content || ''),
    };
    const cr = await fetch(`${API_BASE}/api/document`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!cr.ok) throw new Error(cr.statusText);
    const made = await cr.json();
    const tags = Array.isArray(doc.tags) ? doc.tags : [];
    if (made.id && tags.length) {
      await fetch(`${API_BASE}/api/document/${made.id}/tags?tags=${encodeURIComponent(tags.join(','))}`, { method: 'POST', credentials: 'same-origin' });
    }
    if (uiModule) uiModule.showToast('Duplicated');
    await _loadList(_currentSearch());
  } catch (e) {
    console.error('Workspace: duplicate failed', e);
    if (uiModule) uiModule.showError('Duplicate failed');
  }
}

// Soft-delete (recoverable from the Trash folder) — mirrors the per-row restore flow.
async function _deleteDoc(doc) {
  const ok = await uiModule.styledConfirm(`Delete “${doc.title || 'Untitled'}”? It moves to Trash and can be restored.`, { confirmText: 'Delete', danger: true });
  if (!ok) return;
  try {
    const res = await fetch(`${API_BASE}/api/document/${doc.id}`, { method: 'DELETE', credentials: 'same-origin' });
    if (!res.ok) throw new Error(res.statusText);
    // Close the editor tab if this doc is open — otherwise it lingers as a live,
    // editable tab whose autosave would silently resurrect the just-trashed doc.
    try { documentModule.closeTab(doc.id); } catch (_) {}
    if (uiModule) uiModule.showToast('Moved to Trash');
    _trashDocs = null;                                   // invalidate the trash cache
    if (_expanded.has('trash')) await _loadTrash();
    await _loadList(_currentSearch());
  } catch (e) {
    console.error('Workspace: delete failed', e);
    if (uiModule) uiModule.showError('Delete failed');
  }
}

// ---- New-tag bar (create / add-subtag / rename share one inline input) ----

function _beginTagInput(action, path) {
  if (!_shell) return;
  const input = _shell.querySelector('#dw-tag-input');
  if (!input) return;
  _tagInputMode = { action, path: path || '' };
  if (action === 'subtag') { input.value = ''; input.placeholder = `new subtag under “${path}”`; }
  else if (action === 'rename') { input.value = (path.split('/').pop() || ''); input.placeholder = `rename “${path}”`; }
  else { input.value = ''; input.placeholder = 'tag name (use / to nest)'; }
  input.classList.remove('hidden');
  input.focus();
  input.select();
}

function _endTagInput() {
  if (_shell) {
    const input = _shell.querySelector('#dw-tag-input');
    if (input) { input.classList.add('hidden'); input.value = ''; input.placeholder = 'tag name (use / to nest)'; }
  }
  _tagInputMode = null;
}

function _commitTagInput() {
  if (!_shell) return;
  const input = _shell.querySelector('#dw-tag-input');
  const raw = input ? (input.value || '').trim() : '';
  const mode = _tagInputMode;
  _endTagInput();
  if (!raw || !mode) return;
  if (mode.action === 'create') _createTag(raw);
  else if (mode.action === 'subtag') _createTag(mode.path + '/' + raw);
  else if (mode.action === 'rename') _renameTag(mode.path, raw);
}

function _normPath(p) {
  return String(p || '').split('/').map(s => s.trim()).filter(Boolean).join('/');
}

function _createTag(path) {
  const clean = _normPath(path);
  if (!clean) return;
  _knownTags.add(clean);
  // Expand every ancestor so the new (possibly nested) folder is visible.
  let p = '';
  for (const seg of clean.split('/')) { p = p ? p + '/' + seg : seg; _expanded.add('t:' + p); }
  _persistExpanded();
  _saveKnownTags();
  _renderList();
}

// Rename a tag's LEAF segment → re-prefix that tag (and its subtags) on every
// doc that carries it, plus the known-tags list.
async function _renameTag(oldPath, newLeaf) {
  const leaf = _normPath(newLeaf);
  if (!leaf) return;
  const parts = oldPath.split('/');
  parts.splice(parts.length - 1, 1, ...leaf.split('/'));
  const newPath = _normPath(parts.join('/'));
  if (!newPath || newPath === oldPath) return;
  const _re = (t) => (t === oldPath || t.startsWith(oldPath + '/')) ? newPath + t.slice(oldPath.length) : t;
  _knownTags = new Set([..._knownTags].map(_re));
  _saveKnownTags();
  const affected = _docs.filter(d => (d.tags || []).some(t => t === oldPath || t.startsWith(oldPath + '/')));
  try {
    // Bounded-concurrency fan-out instead of one-at-a-time awaits: a tag shared
    // by many docs was N sequential round-trips (seconds of blank UI). Chunks
    // of 8 keep the server from an unbounded burst while collapsing wall-clock.
    for (let i = 0; i < affected.length; i += 8) {
      await Promise.all(affected.slice(i, i + 8).map(d => _postTags(d, (d.tags || []).map(_re))));
    }
    if (uiModule) uiModule.showToast(`Renamed → ${newPath}`);
  } catch (e) {
    console.error('Workspace: rename tag failed', e);
    if (uiModule) uiModule.showError('Rename failed');
  }
  await _loadList(_currentSearch());
}

// Delete a tag (and its subtags): strip it from every doc + the known list.
// The documents themselves are untouched — only the tag is removed.
async function _deleteTag(node) {
  const path = node.fullPath;
  const n = node.count || 0;
  const msg = n
    ? `Delete tag “${path}”? It will be removed from ${n} document${n === 1 ? '' : 's'} (the documents stay).`
    : `Delete empty tag “${path}”?`;
  if (!window.confirm(msg)) return;
  const _hit = (t) => t === path || t.startsWith(path + '/');
  _knownTags = new Set([..._knownTags].filter(t => !_hit(t)));
  _saveKnownTags();
  const affected = _docs.filter(d => (d.tags || []).some(_hit));
  try {
    // Bounded-concurrency fan-out (chunks of 8) instead of serial awaits.
    for (let i = 0; i < affected.length; i += 8) {
      await Promise.all(affected.slice(i, i + 8).map(d => _postTags(d, (d.tags || []).filter(t => !_hit(t)))));
    }
    if (uiModule) uiModule.showToast(`Deleted tag “${path}”`);
  } catch (e) {
    console.error('Workspace: delete tag failed', e);
    if (uiModule) uiModule.showError('Delete failed');
  }
  for (const k of [..._expanded]) if (k === 't:' + path || k.startsWith('t:' + path + '/')) _expanded.delete(k);
  _persistExpanded();
  await _loadList(_currentSearch());
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
    _mountFooterControls();   // Save + Assist in the editor's bottom action bar
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
    _mountFooterControls();
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
  await Promise.all([_loadKnownTags(), _loadSortPrefs(), _loadPinned()]);
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
  _closeFolderMenu();
  _closeFileMenu();
  _endTagInput();
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
  // The editor's tab bar can switch the active doc (tab click / close); keep the
  // left-list highlight in sync with whichever doc the editor is now showing.
  window.addEventListener('document-switched', (e) => {
    if (!_open) return;
    const id = e && e.detail && e.detail.docId;
    if (id && id !== _activeDocId) { _activeDocId = id; _highlightActive(); }
  });
}

const documentWorkspaceModule = { init, openWorkspace, closeWorkspace, isOpen };
export default documentWorkspaceModule;
if (typeof window !== 'undefined') window.documentWorkspaceModule = documentWorkspaceModule;
