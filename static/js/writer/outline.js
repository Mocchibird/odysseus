// static/js/writer/outline.js
//
// FORK-ONLY. The writer's left pane: a document list organised into nested
// tag folders, StandardNotes style.
//
// Lineage: the tag-path model, drag-to-tag and prefs keys are carried over from
// the fork's previous Documents Workspace (deleted in 33abb4d1) because that part
// was sound — it was the editor that was too weak to use. Reusing the SAME prefs
// keys (dw_known_tags, dw_sort) means folders created before the rewrite still
// show up. The chat pane, trash, per-row context menus and pin/reorder from the
// old workspace are deliberately not carried over yet.
//
// Data comes from endpoints that already exist:
//   GET  /api/documents/library?sort&offset&limit&search  -> the list (with tags)
//   POST /api/document/{id}/tags?tags=a,b                 -> retag (query param)
//   GET/PUT /api/prefs/dw_known_tags                      -> folders with no docs

import menus from './menus.js';

const API = '';
const EXPANDED_KEY = 'odysseus-dw-expanded';   // shared with the old workspace

let _docs = [];
let _knownTags = [];
let _expanded = _loadExpanded();
let _listSeq = 0;
let _search = '';
let _onOpen = () => {};
let _onDeleted = () => {};
let _onRenamed = () => {};
let _onNewInFolder = () => {};
let _activeId = null;
let _error = null;

/* ── persisted UI state ──────────────────────────────────────────────────── */

function _loadExpanded() {
  try { return new Set(JSON.parse(localStorage.getItem(EXPANDED_KEY) || '[]')); }
  catch (_) { return new Set(); }
}
function _persistExpanded() {
  try { localStorage.setItem(EXPANDED_KEY, JSON.stringify([..._expanded])); } catch (_) { /* private mode */ }
}

async function _loadKnownTags() {
  try {
    const res = await fetch(`${API}/api/prefs/dw_known_tags`, { credentials: 'same-origin' });
    if (!res.ok) return [];
    const data = await res.json();
    const v = data && (data.value ?? data.dw_known_tags ?? data);
    return Array.isArray(v) ? v.filter((t) => typeof t === 'string' && t.trim()) : [];
  } catch (_) { return []; }
}

function _saveKnownTags() {
  fetch(`${API}/api/prefs/dw_known_tags`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value: _knownTags }),
  }).catch(() => { /* folder memory is a convenience, not correctness */ });
}

/* ── the tag tree ────────────────────────────────────────────────────────── */

/** Resolve (creating as needed) the nested node for a "parent/child" tag path. */
function _ensurePath(root, fullPath) {
  const segs = String(fullPath || '').split('/').map((s) => s.trim()).filter(Boolean);
  if (!segs.length) return null;
  let node = root; let path = '';
  for (const seg of segs) {
    path = path ? `${path}/${seg}` : seg;
    if (!node.children.has(seg)) {
      node.children.set(seg, { name: seg, fullPath: path, children: new Map(), docs: [], _ids: new Set() });
    }
    node = node.children.get(seg);
  }
  return node;
}

/**
 * Build a nested tree by splitting each tag on "/" (the nesting convention).
 * Known tags are seeded too, so a folder you just created still appears before
 * any document carries it.
 */
function _buildTree(docs, { seedKnown = true } = {}) {
  const root = { children: new Map(), docs: [], _ids: new Set() };
  const untagged = [];
  for (const doc of docs) {
    const tags = (Array.isArray(doc.tags) ? doc.tags : []).filter((t) => (t || '').trim());
    if (!tags.length) { untagged.push(doc); continue; }
    for (const tag of tags) {
      const node = _ensurePath(root, tag);
      if (node && !node._ids.has(doc.id)) { node._ids.add(doc.id); node.docs.push(doc); }
    }
  }
  // Empty known folders are noise in a filtered view.
  if (seedKnown) for (const t of _knownTags) _ensurePath(root, t);
  return { root, untagged };
}

const _searching = () => _search.trim().length > 0;

function _countNode(node) {
  let c = node.docs.length;
  for (const ch of node.children.values()) c += _countNode(ch);
  node.count = c;
  return c;
}

/* ── retagging ───────────────────────────────────────────────────────────── */

async function _postTags(doc, next) {
  // NOTE: this endpoint takes tags as a QUERY param, not a JSON body.
  const res = await fetch(
    `${API}/api/document/${encodeURIComponent(doc.id)}/tags?tags=${encodeURIComponent(next.join(','))}`,
    { method: 'POST', credentials: 'same-origin' },
  );
  if (!res.ok) throw new Error(res.statusText || `HTTP ${res.status}`);
  const data = await res.json();
  doc.tags = Array.isArray(data.tags) ? data.tags : next;
  return doc.tags;
}

/** Drop a document on a folder to add that tag. `null` clears all tags. */
async function assignTag(docId, tagPath) {
  const doc = _docs.find((d) => d.id === docId);
  if (!doc) return;
  const cur = Array.isArray(doc.tags) ? doc.tags.slice() : [];
  let next;
  if (tagPath === null) {
    if (!cur.length) return;                                                 // already untagged
    next = [];
  } else {
    if (cur.some((t) => t.toLowerCase() === tagPath.toLowerCase())) return;  // already tagged
    next = [...cur, tagPath];
  }
  try {
    await _postTags(doc, next);
    render();
  } catch (e) {
    console.error('[writer] assign tag failed', e);
  }
}

function _makeDropTarget(el, tagPath) {
  el.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    el.classList.add('drag-over');
  });
  el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    el.classList.remove('drag-over');
    let id = '';
    try { id = e.dataTransfer.getData('text/plain'); } catch (_) { /* denied */ }
    if (id) assignTag(id, tagPath);
  });
}

/* ── folder CRUD ─────────────────────────────────────────────────────────── */

function _normPath(p) {
  return String(p || '').split('/').map((s) => s.trim()).filter(Boolean).join('/');
}

export function createTag(path) {
  const clean = _normPath(path);
  if (!clean) return false;
  if (!_knownTags.some((t) => t.toLowerCase() === clean.toLowerCase())) {
    _knownTags.push(clean);
    _saveKnownTags();
  }
  // Reveal the new folder and every ancestor.
  const segs = clean.split('/');
  for (let i = 1; i <= segs.length; i += 1) _expanded.add(segs.slice(0, i).join('/'));
  _persistExpanded();
  render();
  return true;
}

/** Rename a folder: rewrite the prefix on every document that carries it. */
export async function renameTag(oldPath, newPath) {
  const from = _normPath(oldPath);
  const to = _normPath(newPath);
  if (!from || !to || from === to) return;

  const rewrite = (t) => (t === from || t.startsWith(`${from}/`) ? to + t.slice(from.length) : t);
  const touched = _docs.filter((d) => (d.tags || []).some((t) => t === from || t.startsWith(`${from}/`)));
  for (const doc of touched) {
    const next = [...new Set((doc.tags || []).map(rewrite))];
    try { await _postTags(doc, next); } catch (e) { console.error('[writer] rename tag failed', e); }
  }
  _knownTags = [...new Set(_knownTags.map(rewrite))];
  _saveKnownTags();
  render();
}

/** Delete a folder: drop the tag (and its descendants) from every document. */
export async function deleteTag(path) {
  const from = _normPath(path);
  if (!from) return;
  const hit = (t) => t === from || t.startsWith(`${from}/`);
  const touched = _docs.filter((d) => (d.tags || []).some(hit));
  for (const doc of touched) {
    try { await _postTags(doc, (doc.tags || []).filter((t) => !hit(t))); }
    catch (e) { console.error('[writer] delete tag failed', e); }
  }
  _knownTags = _knownTags.filter((t) => !hit(t));
  _saveKnownTags();
  render();
}

/* ── row actions ─────────────────────────────────────────────────────────── */

async function _ui() {
  try { return await import('../ui.js'); } catch (_) { return null; }
}

async function _prompt(title, defaultValue, placeholder) {
  const ui = await _ui();
  if (ui && ui.styledPrompt) return ui.styledPrompt(title, { defaultValue, placeholder });
  return window.prompt(title, defaultValue);
}

async function _confirm(message, { danger = false } = {}) {
  const ui = await _ui();
  if (ui && ui.styledConfirm) return ui.styledConfirm(message, { confirmText: 'Delete', danger });
  return window.confirm(message);
}

async function _toast(msg) {
  const ui = await _ui();
  if (ui && ui.showToast) ui.showToast(msg);
}

async function renameDoc(doc) {
  const next = await _prompt('Rename document', doc.title || '', 'Title');
  const title = (next || '').trim();
  if (!title || title === doc.title) return;
  const res = await fetch(`${API}/api/document/${encodeURIComponent(doc.id)}`, {
    method: 'PATCH',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) { _toast('Rename failed'); return; }
  doc.title = title;
  render();
  _onRenamed(doc.id, title);
}

async function duplicateDoc(doc) {
  // Read the body first: the library row carries metadata, not content.
  const src = await (await fetch(`${API}/api/document/${encodeURIComponent(doc.id)}`, { credentials: 'same-origin' })).json();
  const made = await (await fetch(`${API}/api/document`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: `${src.title || doc.title || 'Untitled'} (copy)`,
      language: src.language || 'markdown',
      content: src.current_content != null ? src.current_content : '',
    }),
  })).json();
  // Carry the tags over so the copy lands in the same folder.
  const tags = Array.isArray(doc.tags) ? doc.tags : [];
  if (made.id && tags.length) {
    await fetch(`${API}/api/document/${made.id}/tags?tags=${encodeURIComponent(tags.join(','))}`,
      { method: 'POST', credentials: 'same-origin' });
  }
  await load();
  _toast('Duplicated');
}

async function deleteDoc(doc) {
  const ok = await _confirm(
    `Delete “${doc.title || 'Untitled'}”? It moves to Trash and can be restored from the Library.`,
    { danger: true },
  );
  if (!ok) return;
  const res = await fetch(`${API}/api/document/${encodeURIComponent(doc.id)}`, {
    method: 'DELETE', credentials: 'same-origin',
  });
  if (!res.ok) { _toast('Delete failed'); return; }
  // If the deleted document is the one open in the editor, get off it. The server
  // refuses edits to a trashed document, so leaving it open would turn every
  // keystroke into a failed save.
  _onDeleted(doc.id);
  await load();
  _toast('Moved to Trash');
}

function _fileMenuItems(doc) {
  const items = [
    { label: 'Rename', run: () => renameDoc(doc) },
    { label: 'Duplicate', run: () => duplicateDoc(doc) },
  ];
  if ((doc.tags || []).length) {
    items.push({ label: 'Remove from folders', run: () => assignTag(doc.id, null) });
  }
  items.push('sep', { label: 'Delete', danger: true, run: () => deleteDoc(doc) });
  return items;
}

function _folderMenuItems(node) {
  return [
    { label: 'New document here', run: () => _onNewInFolder(node.fullPath) },
    { label: 'New subfolder', run: async () => {
      const name = await _prompt('New subfolder', '', 'Name');
      if (name) createTag(`${node.fullPath}/${name}`);
    } },
    { label: 'Rename folder', run: async () => {
      const next = await _prompt('Rename folder', node.name, 'Name');
      if (!next) return;
      const parent = node.fullPath.split('/').slice(0, -1).join('/');
      await renameTag(node.fullPath, parent ? `${parent}/${next}` : next);
    } },
    'sep',
    { label: 'Delete folder', danger: true, run: async () => {
      const ok = await _confirm(
        `Delete the folder “${node.name}”? The documents stay — only the tag is removed.`,
        { danger: true },
      );
      if (ok) await deleteTag(node.fullPath);
    } },
  ];
}

/* ── rendering ───────────────────────────────────────────────────────────── */

function _chevron(open) {
  return `<svg class="writer-chev${open ? ' open' : ''}" width="12" height="12" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
    ><polyline points="9 18 15 12 9 6"/></svg>`;
}

function _fileRow(doc) {
  const row = document.createElement('div');
  row.className = 'writer-file' + (doc.id === _activeId ? ' active' : '');
  row.dataset.docId = doc.id;
  row.draggable = true;
  row.tabIndex = 0;
  row.title = doc.title || 'Untitled';
  // Wrap the label: the row is display:flex, so a bare text node becomes an
  // anonymous flex item and text-overflow:ellipsis never applies to it — long
  // titles get chopped mid-character at the pane edge instead of elided.
  const label = document.createElement('span');
  label.className = 'writer-file-name';
  label.textContent = doc.title || 'Untitled';
  row.appendChild(label);
  row.addEventListener('dragstart', (e) => {
    try { e.dataTransfer.setData('text/plain', doc.id); } catch (_) { /* denied */ }
    e.dataTransfer.effectAllowed = 'copy';
  });
  row.addEventListener('click', () => _onOpen(doc.id));
  row.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _onOpen(doc.id); }
  });
  row.appendChild(menus.actionButton('Document actions',
    (btn) => menus.openMenu(btn, _fileMenuItems(doc))));
  return row;
}

function _folderRow(node, depth) {
  const open = _searching() || _expanded.has(node.fullPath);
  const row = document.createElement('div');
  row.className = 'writer-folder';
  row.style.paddingLeft = `${_folderPad(depth)}px`;
  row.innerHTML = `${_chevron(open)}<span class="writer-folder-name"></span><span class="writer-folder-count"></span>`;
  row.querySelector('.writer-folder-name').textContent = node.name;
  row.querySelector('.writer-folder-count').textContent = node.count || '';
  row.addEventListener('click', () => {
    if (_expanded.has(node.fullPath)) _expanded.delete(node.fullPath);
    else _expanded.add(node.fullPath);
    _persistExpanded();
    render();
  });
  row.appendChild(menus.actionButton('Folder actions',
    (btn) => menus.openMenu(btn, _folderMenuItems(node))));
  _makeDropTarget(row, node.fullPath);
  return row;
}

const _folderPad = (depth) => 6 + depth * 12;
const _filePad = (depth) => _folderPad(depth) + 14;   // one nudge past its folder

function _renderNode(node, depth, into) {
  for (const child of [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name))) {
    into.appendChild(_folderRow(child, depth));
    // A search result inside a collapsed folder is invisible, which reads as "no
    // matches". While filtering, show everything.
    if (!_searching() && !_expanded.has(child.fullPath)) continue;
    _renderNode(child, depth + 1, into);
    for (const doc of child.docs) {
      const row = _fileRow(doc);
      row.style.paddingLeft = `${_filePad(depth)}px`;
      into.appendChild(row);
    }
  }
}

export function render() {
  const list = document.getElementById('writer-list');
  if (!list) return;
  list.textContent = '';

  const q = _search.trim().toLowerCase();
  const docs = q
    ? _docs.filter((d) => (d.title || '').toLowerCase().includes(q))
    : _docs;

  const { root, untagged } = _buildTree(docs, { seedKnown: !_searching() });
  _countNode(root);
  _renderNode(root, 0, list);

  if (untagged.length) {
    const head = document.createElement('div');
    head.className = 'writer-folder writer-folder-untagged';
    head.style.paddingLeft = `${_folderPad(0)}px`;
    head.innerHTML = '<span class="writer-folder-name">Untagged</span><span class="writer-folder-count"></span>';
    head.querySelector('.writer-folder-count').textContent = untagged.length;
    // Dropping here strips every tag, mirroring the old workspace's behaviour.
    _makeDropTarget(head, null);
    list.appendChild(head);
    for (const doc of untagged) {
      const row = _fileRow(doc);
      row.style.paddingLeft = `${_filePad(0)}px`;
      list.appendChild(row);
    }
  }

  if (_error) {
    const err = document.createElement('div');
    err.className = 'writer-list-empty writer-list-error';
    err.textContent = _error;
    list.appendChild(err);
    return;
  }
  if (!list.childElementCount) {
    const empty = document.createElement('div');
    empty.className = 'writer-list-empty';
    empty.textContent = q ? 'No documents match' : 'No documents yet';
    list.appendChild(empty);
  }
}

/* ── loading ─────────────────────────────────────────────────────────────── */

// The library endpoint caps `limit` at 50 and 422s above it, so the whole list is
// paged in rather than requested in one go. PAGE_CAP bounds a pathological library
// (and any server-side paging bug) instead of looping forever.
const PAGE = 50;
const PAGE_CAP = 40;   // 2000 documents

export async function load() {
  const seq = ++_listSeq;
  try {
    const known = _knownTags.length ? _knownTags : await _loadKnownTags();
    if (seq !== _listSeq) return;

    const all = [];
    let total = null;
    for (let page = 0; page < PAGE_CAP; page += 1) {
      const params = new URLSearchParams({
        sort: 'recent', offset: String(page * PAGE), limit: String(PAGE),
      });
      const res = await fetch(`${API}/api/documents/library?${params}`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status} loading documents`);
      const data = await res.json();
      if (seq !== _listSeq) return;            // a newer request superseded this one
      const batch = Array.isArray(data.documents) ? data.documents : [];
      all.push(...batch);
      if (total === null && typeof data.total === 'number') total = data.total;
      if (batch.length < PAGE) break;
      if (total !== null && all.length >= total) break;
    }

    _knownTags = known;
    _docs = all;
    _error = null;
    render();
  } catch (e) {
    if (seq !== _listSeq) return;
    console.error('[writer] failed to load the document list', e);
    // Show it. An empty list that is actually a failed request is the worst of
    // both worlds — it reads as "you have no documents".
    _error = e && e.message ? e.message : 'could not load documents';
    render();
  }
}

export function setSearch(q) { _search = String(q || ''); render(); }

export function setActive(docId) {
  _activeId = docId || null;
  const list = document.getElementById('writer-list');
  if (!list) return;
  for (const el of list.querySelectorAll('.writer-file')) {
    el.classList.toggle('active', el.dataset.docId === _activeId);
  }
}

export function configure({ onOpen, onDeleted, onRenamed, onNewInFolder }) {
  if (onOpen) _onOpen = onOpen;
  if (onDeleted) _onDeleted = onDeleted;
  if (onRenamed) _onRenamed = onRenamed;
  if (onNewInFolder) _onNewInFolder = onNewInFolder;
}

/** Tags of the open document — the writer header shows them. */
export function tagsOf(docId) {
  const doc = _docs.find((d) => d.id === docId);
  return doc && Array.isArray(doc.tags) ? doc.tags : [];
}

export default {
  load, render, setSearch, setActive, configure, assignTag,
  createTag, renameTag, deleteTag, tagsOf,
  renameDoc, duplicateDoc, deleteDoc,
};
