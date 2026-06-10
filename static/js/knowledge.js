// knowledge.js — the Knowledge panel.
//
// The user's curated, self-hostable knowledge base: manually ADD any file
// (pdf/png/jpg/md/txt/docx…), BROWSE + deterministic keyword/tag SEARCH (no LLM),
// and OPEN the REAL file to double-check it. Iris recall (RAG) is additive and
// lives in the `search_knowledge` agent tool, which cites `#knowledge-<id>` links
// that open files here. Trust anchor: search here is exact-match, and every file
// is openable + shows the exact indexed text.
//
// Reuses Odysseus conventions: health.js modal lifecycle (Modals.register +
// makeToolModalDraggable), gallery.js upload/search/tag-chip patterns,
// markdown.js mdToHtml for .md rendering, pdfReader.js for inline PDFs.
import * as Modals from './modalManager.js';
import { makeToolModalDraggable } from './modalFullscreen.js?v=370';
import markdownModule from './markdown.js';
import { createPdfReader } from './pdfReader.js?v=383';
import uiModule from './ui.js';

const API_BASE = window.location.origin;

let _open = false;
let _escHandler = null;
let _search = '';
let _activeTags = [];
let _searchDebounce = null;
let _files = [];
let _allTags = [];
let _pdfReader = null;     // active inline PDF reader (destroy on navigate-away/close)
let _pendingOpenId = null; // file to auto-open once the list has loaded (citations)

// ---- helpers ----------------------------------------------------------------

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _fmtSize(bytes) {
  const b = Number(bytes) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function _ext(rec) {
  const m = (rec.filename || '').match(/\.([a-z0-9]{1,6})$/i);
  return m ? m[1].toUpperCase() : '';
}

// What inline viewer to use for a file (mime first, then extension).
function _kindOf(rec) {
  const mime = (rec.mime || '').toLowerCase();
  const name = (rec.filename || '').toLowerCase();
  if (mime.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|svg)$/.test(name)) return 'image';
  if (mime === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
  if (mime === 'text/markdown' || /\.(md|markdown)$/.test(name)) return 'md';
  if (mime.startsWith('text/') || /\.(txt|log|csv|tsv|json|ya?ml|ini|conf|xml|html?)$/.test(name)) return 'text';
  return 'other';
}

function _body() { return document.querySelector('#knowledge-modal .kb-body'); }

// ---- data -------------------------------------------------------------------

async function _fetchFiles() {
  const params = new URLSearchParams();
  if (_search) params.set('q', _search);
  if (_activeTags.length) params.set('tags', _activeTags.join(','));
  try {
    const res = await fetch(`${API_BASE}/api/knowledge?${params}`, { credentials: 'same-origin' });
    const data = await res.json();
    _files = Array.isArray(data.files) ? data.files : [];
  } catch (_) { _files = []; }
}

async function _fetchTags() {
  try {
    const res = await fetch(`${API_BASE}/api/knowledge/tags`, { credentials: 'same-origin' });
    const data = await res.json();
    _allTags = Array.isArray(data.tags) ? data.tags : [];
  } catch (_) { _allTags = []; }
}

async function _refresh() {
  await Promise.all([_fetchFiles(), _fetchTags()]);
  _renderList();
}

// ---- list view --------------------------------------------------------------

function _renderTagsRow() {
  const row = _body()?.querySelector('.kb-tags-row');
  if (!row) return;
  if (!_allTags.length) { row.style.display = 'none'; row.innerHTML = ''; return; }
  row.style.display = '';
  row.innerHTML = _allTags.map(t => {
    const on = _activeTags.includes(t);
    return `<button class="memory-cat-chip kb-tag-chip${on ? ' active' : ''}" data-tag="${_esc(t)}">${_esc(t)}</button>`;
  }).join('');
}

function _cardHtml(rec) {
  const tags = [...(rec.tags || []), ...(rec.ai_tags || [])].slice(0, 6);
  const tagHtml = tags.map(t => `<span class="kb-chip">${_esc(t)}</span>`).join('');
  const badge = _ext(rec) || (_kindOf(rec) === 'image' ? 'IMG' : 'FILE');
  return `
    <div class="kb-card" data-id="${_esc(rec.id)}">
      <div class="kb-card-badge kb-kind-${_kindOf(rec)}">${_esc(badge)}</div>
      <div class="kb-card-main">
        <div class="kb-card-name" title="${_esc(rec.filename)}">${_esc(rec.filename)}</div>
        ${rec.excerpt ? `<div class="kb-card-excerpt">${_esc(rec.excerpt)}</div>` : ''}
        ${tagHtml ? `<div class="kb-card-tags">${tagHtml}</div>` : ''}
      </div>
    </div>`;
}

function _renderList() {
  const body = _body();
  if (!body) return;
  body.querySelector('.kb-detail-view').style.display = 'none';
  body.querySelector('.kb-list-view').style.display = '';
  if (_pdfReader) { try { _pdfReader.destroy(); } catch (_) {} _pdfReader = null; }

  _renderTagsRow();
  const cards = body.querySelector('.kb-cards');
  const empty = body.querySelector('.kb-empty');
  if (!_files.length) {
    cards.innerHTML = '';
    empty.style.display = '';
    empty.innerHTML = (_search || _activeTags.length)
      ? 'No files match your search.'
      : 'No files yet. Add the files you want Iris to know about — they stay searchable and openable here.';
  } else {
    empty.style.display = 'none';
    cards.innerHTML = _files.map(_cardHtml).join('');
  }
}

// ---- detail view (open + verify the real file) ------------------------------

async function _openDetail(id) {
  const body = _body();
  if (!body) return;
  const view = body.querySelector('.kb-detail-view');
  view.innerHTML = '<div class="kb-loading">Loading…</div>';
  body.querySelector('.kb-list-view').style.display = 'none';
  view.style.display = '';

  let rec;
  try {
    const res = await fetch(`${API_BASE}/api/knowledge/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('not found');
    rec = await res.json();
  } catch (_) {
    uiModule.showToast && uiModule.showToast('File not found');
    _renderList();
    return;
  }

  const kind = _kindOf(rec);
  const userTags = rec.tags || [];
  const aiTags = rec.ai_tags || [];
  const meta = [_ext(rec), _fmtSize(rec.file_size), rec.indexed ? 'indexed for Iris' : 'not indexed']
    .filter(Boolean).join(' · ');

  view.innerHTML = `
    <div class="kb-detail-head">
      <button class="memory-toolbar-btn kb-back" title="Back"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg> Back</button>
      <div class="kb-detail-title" title="${_esc(rec.filename)}">${_esc(rec.filename)}</div>
      <a class="kb-open-orig" href="${_esc(rec.url || '#')}" target="_blank" rel="noopener" ${rec.has_file ? '' : 'style="display:none"'}>Open original <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>
      <button class="admin-btn-delete kb-delete" title="Delete from knowledge base">Delete</button>
    </div>
    <div class="kb-detail-meta">${_esc(meta)}</div>
    <div class="kb-tags-edit">
      <div class="kb-tags-current">
        ${userTags.map(t => `<span class="kb-chip editable" data-tag="${_esc(t)}">${_esc(t)}<button class="kb-chip-x" data-tag="${_esc(t)}" title="Remove">×</button></span>`).join('')}
        ${aiTags.map(t => `<span class="kb-chip ai" title="auto-tag">${_esc(t)}</span>`).join('')}
      </div>
      <input class="memory-search-input kb-tag-input" placeholder="Add tag + Enter">
      <button class="memory-toolbar-btn kb-suggest-tags" type="button" title="AI-generate topical tags from the text"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>Suggest tags</button>
    </div>
    <div class="kb-viewer"></div>
    <details class="kb-indexed">
      <summary>Indexed text — exactly what Iris searches (${(rec.text || '').length} chars)</summary>
      <div class="kb-indexed-actions">
        <button class="memory-toolbar-btn kb-edit-text" type="button"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>Edit text</button>
        <span class="kb-edit-hint">${(kind === 'md' || kind === 'text') ? 'Edits the file content.' : 'Edits the searchable text only — the original file is left unchanged.'}</span>
      </div>
      <pre class="kb-indexed-pre">${_esc(rec.text || '(no text extracted)')}</pre>
    </details>`;

  // ---- inline viewer by type ----
  const viewer = view.querySelector('.kb-viewer');
  if (kind === 'image') {
    viewer.innerHTML = `<img class="kb-img" src="${_esc(rec.url)}" alt="${_esc(rec.filename)}">`;
  } else if (kind === 'md') {
    viewer.className = 'kb-viewer kb-md';
    viewer.innerHTML = markdownModule.mdToHtml(rec.text || '');
  } else if (kind === 'text') {
    viewer.innerHTML = `<pre class="kb-text">${_esc(rec.text || '(empty)')}</pre>`;
  } else if (kind === 'pdf') {
    const pc = document.createElement('div');
    pc.className = 'kb-pdf-viewer';
    viewer.appendChild(pc);
    createPdfReader(pc, { url: rec.url }).then((reader) => {
      // bail if the user navigated away while it loaded
      if (!document.body.contains(pc)) { try { reader.destroy(); } catch (_) {} return; }
      _pdfReader = reader;
    }).catch(() => {
      viewer.innerHTML = `<div class="kb-noview">Couldn't render this PDF. <a href="${_esc(rec.url)}" target="_blank" rel="noopener">Open it <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a></div>`;
    });
  } else {
    viewer.innerHTML = rec.has_file
      ? `<div class="kb-noview">No inline preview for this file type. <a href="${_esc(rec.url)}" target="_blank" rel="noopener">Open original <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a></div>`
      : '<div class="kb-noview">No stored file.</div>';
  }

  // ---- wire detail controls ----
  view.querySelector('.kb-back').addEventListener('click', () => _renderList());
  view.querySelector('.kb-delete').addEventListener('click', () => _deleteFile(rec));
  const addInput = view.querySelector('.kb-tag-input');
  addInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const raw = addInput.value.trim().replace(/^#/, '');
    if (!raw) return;
    const next = [...userTags];
    raw.split(',').map(s => s.trim()).filter(Boolean).forEach(t => { if (!next.includes(t)) next.push(t); });
    _setTags(rec, next);
  });
  view.querySelectorAll('.kb-chip-x').forEach(x => {
    x.addEventListener('click', () => _setTags(rec, userTags.filter(t => t !== x.dataset.tag)));
  });
  view.querySelector('.kb-suggest-tags')?.addEventListener('click', (e) => _suggestTags(rec, e.currentTarget));
  view.querySelector('.kb-edit-text')?.addEventListener('click', () => _beginEditText(view, rec));
}

// Lightweight inline edit of a file's text (NOT a second document editor — heavy
// authoring belongs in Library/Documents). For .md/.txt this rewrites the stored
// file; for PDFs/images it corrects the searchable extracted text.
function _beginEditText(view, rec) {
  const det = view.querySelector('.kb-indexed');
  if (!det || det.querySelector('.kb-edit-wrap')) return;  // already editing
  det.open = true;
  const pre = det.querySelector('.kb-indexed-pre');
  const editBtn = det.querySelector('.kb-edit-text');
  const wrap = document.createElement('div');
  wrap.className = 'kb-edit-wrap';
  wrap.innerHTML = `
    <textarea class="kb-edit-area" spellcheck="false"></textarea>
    <div class="kb-edit-controls">
      <button class="kb-edit-save" type="button">Save changes</button>
      <button class="kb-edit-cancel" type="button">Cancel</button>
    </div>`;
  wrap.querySelector('.kb-edit-area').value = rec.text || '';
  if (pre) pre.style.display = 'none';
  if (editBtn) editBtn.style.display = 'none';
  det.appendChild(wrap);
  const ta = wrap.querySelector('.kb-edit-area');
  ta.focus();
  wrap.querySelector('.kb-edit-cancel').addEventListener('click', () => {
    wrap.remove();
    if (pre) pre.style.display = '';
    if (editBtn) editBtn.style.display = '';
  });
  wrap.querySelector('.kb-edit-save').addEventListener('click', () => _saveText(rec, ta.value));
}

async function _saveText(rec, text) {
  try {
    const res = await fetch(`${API_BASE}/api/knowledge/${encodeURIComponent(rec.id)}`, {
      method: 'PUT', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error();
    uiModule.showToast && uiModule.showToast('Saved + re-indexed');
    _openDetail(rec.id);  // re-render with the updated text/preview
  } catch (_) {
    uiModule.showToast && uiModule.showToast('Could not save changes');
  }
}

async function _suggestTags(rec, btn) {
  if (btn) btn.disabled = true;  // :disabled dims it; keep the SVG icon + label intact
  try {
    const res = await fetch(`${API_BASE}/api/knowledge/${encodeURIComponent(rec.id)}/autotag`, {
      method: 'POST', credentials: 'same-origin',
    });
    if (!res.ok) throw new Error();
    await _fetchTags();
    _openDetail(rec.id);  // re-render to show the new AI tags
  } catch (_) {
    uiModule.showToast && uiModule.showToast('Could not suggest tags');
    if (btn) btn.disabled = false;
  }
}

async function _setTags(rec, tagsArray) {
  try {
    const res = await fetch(`${API_BASE}/api/knowledge/${encodeURIComponent(rec.id)}/tags`, {
      method: 'PUT', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags: tagsArray.join(', ') }),
    });
    if (!res.ok) throw new Error();
    await _fetchTags();
    _openDetail(rec.id); // re-render detail with updated tags
  } catch (_) {
    uiModule.showToast && uiModule.showToast('Could not update tags');
  }
}

async function _deleteFile(rec) {
  if (!window.confirm(`Remove "${rec.filename}" from the knowledge base?\n\nThis deletes Odysseus's copy + its index. Your original file elsewhere is untouched.`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/knowledge/${encodeURIComponent(rec.id)}`, {
      method: 'DELETE', credentials: 'same-origin',
    });
    if (!res.ok) throw new Error();
    uiModule.showToast && uiModule.showToast('Removed from knowledge base');
    await _refresh();
  } catch (_) {
    uiModule.showToast && uiModule.showToast('Could not delete');
  }
}

// ---- upload (manually add files) --------------------------------------------

function _setUploadBar(show, text, pct) {
  const bar = _body()?.querySelector('.kb-upload-bar');
  if (!bar) return;
  bar.style.display = show ? '' : 'none';
  if (text != null) bar.querySelector('.kb-upload-status').textContent = text;
  if (pct != null) bar.querySelector('.kb-upload-progress').style.width = `${pct}%`;
}

async function _uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  let done = 0, errors = 0;
  const total = files.length;
  _setUploadBar(true, `Adding 0/${total}…`, 0);
  for (const f of files) {
    const isImg = (f.type || '').startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name);
    _setUploadBar(true, `Adding ${done + 1}/${total}: ${f.name}${isImg ? ' (reading image text — may take a minute)' : ''}`, (done / total) * 100);
    try {
      const fd = new FormData();
      fd.append('files', f);
      const up = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: fd, credentials: 'same-origin' });
      const upData = await up.json();
      const id = upData.files?.[0]?.id;
      if (!id) { errors++; done++; continue; }
      const ing = await fetch(`${API_BASE}/api/knowledge`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id: id }),
      });
      if (!ing.ok) errors++;
    } catch (_) { errors++; }
    done++;
    _setUploadBar(true, `Adding ${done}/${total}…`, (done / total) * 100);
  }
  const msg = `${total - errors} added${errors ? `, ${errors} failed` : ''}`;
  _setUploadBar(true, msg, 100);
  uiModule.showToast && uiModule.showToast(msg);
  setTimeout(() => _setUploadBar(false), 2500);
  await _refresh();
}

// ---- modal lifecycle (mirrors health.js) ------------------------------------

function _wireBody() {
  const body = _body();
  if (!body) return;
  // search (debounced)
  const search = body.querySelector('.kb-search');
  search.addEventListener('input', () => {
    clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(async () => {
      _search = search.value.trim();
      await _fetchFiles();
      _renderList();
    }, 280);
  });
  // upload
  const fileInput = body.querySelector('.kb-file-input');
  body.querySelector('.kb-upload-btn').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { _uploadFiles(fileInput.files); fileInput.value = ''; });
  // tag chips (delegated)
  body.querySelector('.kb-tags-row').addEventListener('click', async (e) => {
    const chip = e.target.closest('.kb-tag-chip');
    if (!chip) return;
    const t = chip.dataset.tag;
    _activeTags = _activeTags.includes(t) ? _activeTags.filter(x => x !== t) : [..._activeTags, t];
    await _fetchFiles();
    _renderList();
  });
  // card click → open detail (delegated)
  body.querySelector('.kb-cards').addEventListener('click', (e) => {
    const card = e.target.closest('.kb-card');
    if (card) _openDetail(card.dataset.id);
  });
  // drag-drop anywhere in the panel
  body.addEventListener('dragover', (e) => { e.preventDefault(); body.classList.add('kb-dragover'); });
  body.addEventListener('dragleave', (e) => { if (e.target === body) body.classList.remove('kb-dragover'); });
  body.addEventListener('drop', (e) => {
    e.preventDefault();
    body.classList.remove('kb-dragover');
    if (e.dataTransfer?.files?.length) _uploadFiles(e.dataTransfer.files);
  });
}

export function openKnowledge(openId) {
  _pendingOpenId = openId || null;
  if (Modals.isRegistered('knowledge-modal') && Modals.isMinimized('knowledge-modal')) {
    Modals.restore('knowledge-modal');
    if (_pendingOpenId) _openDetail(_pendingOpenId);
    return;
  }
  if (_open) {
    if (_pendingOpenId) _openDetail(_pendingOpenId);
    return;
  }
  _open = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'knowledge-modal';
  modal.innerHTML = `
    <div class="modal-content knowledge-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px;margin-right:6px;">
            <path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
          </svg>Knowledge
        </h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="knowledge-close">✖</button>
      </div>
      <div class="modal-body kb-body">
        <div class="kb-list-view">
          <div class="kb-toolbar">
            <input class="memory-search-input kb-search" type="text" placeholder="Search files + contents…">
            <button class="admin-btn-add kb-upload-btn"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Add files</button>
            <input type="file" class="kb-file-input" multiple style="display:none">
          </div>
          <div class="kb-upload-bar" style="display:none">
            <div class="kb-upload-track"><div class="kb-upload-progress"></div></div>
            <div class="kb-upload-status"></div>
          </div>
          <div class="kb-tags-row" style="display:none"></div>
          <div class="kb-cards"></div>
          <div class="kb-empty" style="display:none"></div>
        </div>
        <div class="kb-detail-view" style="display:none"></div>
      </div>
    </div>`;
  document.body.appendChild(modal);

  Modals.register('knowledge-modal', {
    railBtnId: 'rail-knowledge',
    sidebarBtnId: 'tool-knowledge-btn',
    closeFn: () => _doClose(),
    restoreFn: () => {},
    label: 'Knowledge',
    icon: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  });
  try { Modals.injectMinimizeButton(modal, 'knowledge-modal'); } catch (_) {}

  document.getElementById('knowledge-close').addEventListener('click', closeKnowledge);
  makeToolModalDraggable(modal);
  _escHandler = (e) => { if (e.key === 'Escape' && _open) closeKnowledge(); };
  document.addEventListener('keydown', _escHandler);

  _wireBody();
  _renderList(); // instant empty/loading frame
  _refresh().then(() => { if (_pendingOpenId) { _openDetail(_pendingOpenId); _pendingOpenId = null; } });
}

function _doClose() {
  _open = false;
  if (_pdfReader) { try { _pdfReader.destroy(); } catch (_) {} _pdfReader = null; }
  const modal = document.getElementById('knowledge-modal');
  if (modal) {
    const content = modal.querySelector('.modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => modal.remove(), { once: true });
      setTimeout(() => { if (modal.parentElement) modal.remove(); }, 250);
    } else { modal.remove(); }
  }
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
}

export function closeKnowledge() {
  if (!_open && !Modals.isMinimized('knowledge-modal')) return;
  if (Modals.isRegistered('knowledge-modal')) Modals.close('knowledge-modal');
  else _doClose();
}

export function isKnowledgeOpen() { return _open; }

export default { openKnowledge, closeKnowledge, isKnowledgeOpen };
