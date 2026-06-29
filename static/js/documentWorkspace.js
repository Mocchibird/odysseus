// static/js/documentWorkspace.js
// ============================================
// Documents Workspace — a full-window, 3-pane view (list | editor | chat) for
// working across many documents without the Library modal round-trip.
//
// Heavy reuse, no parallel components:
//   - LEFT   : documentLibrary.fetchLibraryDocuments + createWorkspaceListItem
//   - CENTER : the real editor — documentModule.openPanel({ workspace, mountTarget })
//   - RIGHT  : the real chat surface — #chat-container is *relocated* here while
//              the workspace is open (never cloned), so the existing
//              doc-scoped agent (active_doc_id) edits the open document.
// Opened from the Documents rail button and the /workspace deep link.
// ============================================

let API_BASE = '';
let _open = false;
let _shell = null;
let _chatHome = null;           // { parent, next } — exact restore slot for #chat-container

// ---- shell ----------------------------------------------------------------

function _icon(paths, w = 16) {
  return `<svg width="${w}" height="${w}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}
const _ICON_CLOSE = _icon('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>');
const _ICON_CHAT = _icon('<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>');

function _buildShell() {
  if (_shell) return _shell;
  const el = document.createElement('div');
  el.id = 'doc-workspace';
  el.className = 'hidden';
  el.innerHTML = `
    <div class="dw-left">
      <div class="dw-left-head">
        <input type="text" id="dw-search" class="memory-search-input" placeholder="Search documents…" autocomplete="off" />
        <button class="icon-rail-btn dw-new-btn" id="dw-new" title="New document" aria-label="New document">${_icon('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>')}</button>
      </div>
      <div class="dw-list" id="dw-list" role="list"></div>
    </div>
    <div class="dw-center" id="dw-center"></div>
    <div class="dw-right" id="dw-right"></div>
    <button class="icon-rail-btn dw-chat-toggle" id="dw-chat-toggle" title="Toggle chat (assist with this document)" aria-label="Toggle chat">${_ICON_CHAT}</button>
    <button class="icon-rail-btn dw-close" id="dw-close" title="Close workspace" aria-label="Close workspace">${_ICON_CLOSE}</button>
    <div class="dw-mobile-switch" role="tablist" aria-label="Workspace panes">
      <button class="dw-mtab" data-pane="left">List</button>
      <button class="dw-mtab" data-pane="center">Editor</button>
      <button class="dw-mtab" data-pane="right">Chat</button>
    </div>`;
  document.body.appendChild(el);
  _shell = el;

  el.querySelector('#dw-close').addEventListener('click', () => closeWorkspace());
  el.querySelector('#dw-chat-toggle').addEventListener('click', () => {
    el.classList.toggle('dw-chat-collapsed');
  });
  el.querySelectorAll('.dw-mtab').forEach(btn => {
    btn.addEventListener('click', () => _setMobilePane(btn.dataset.pane));
  });
  return el;
}

function _setMobilePane(pane) {
  if (!_shell) return;
  _shell.setAttribute('data-pane', pane);
  _shell.querySelectorAll('.dw-mtab').forEach(b => b.classList.toggle('active', b.dataset.pane === pane));
}

// ---- public API -----------------------------------------------------------

export function init(apiBase) { API_BASE = apiBase || ''; }

export function openWorkspace() {
  _buildShell();
  if (_open) { _shell.classList.remove('hidden'); return; }
  _open = true;
  _shell.classList.remove('hidden');
  document.body.classList.add('doc-workspace-open');
  _setMobilePane('left');
  // Panes are wired in subsequent build phases (editor mount, list, chat relocate).
}

export function closeWorkspace() {
  if (!_open || !_shell) return;
  _open = false;
  _shell.classList.add('hidden');
  document.body.classList.remove('doc-workspace-open');
}

export function isOpen() { return _open; }

const documentWorkspaceModule = { init, openWorkspace, closeWorkspace, isOpen };
export default documentWorkspaceModule;
if (typeof window !== 'undefined') window.documentWorkspaceModule = documentWorkspaceModule;
