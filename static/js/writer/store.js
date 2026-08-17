// static/js/writer/store.js
//
// FORK-ONLY. Document persistence for the writer surface — LOCAL FIRST.
//
// A save writes to IndexedDB and queues a server op. The local write is what the
// UI waits on: it is fast, it works with no network, and it cannot half-fail. The
// server write happens on its own slower schedule (see ./sync.js) and its failure
// is a sync problem, not a lost keystroke.
//
// TWO DEBOUNCES, deliberately:
//   LOCAL_DEBOUNCE_MS   short  — durability. Cheap: IndexedDB, off the main thread.
//   PUSH_DEBOUNCE_MS    longer — politeness. Each push is an HTTP round trip and
//                       potentially a new document version, so it waits for a real
//                       pause in typing rather than firing every second.
//
// EVERY document is created locally first, even when online, and gets a real
// server id later via sync's rekey. One code path instead of two: the rekey
// machinery has to exist for the offline case regardless, and routing every
// create through it means it is exercised constantly rather than being a rare
// path that rots. It also makes "New" instant on a slow link, which is the whole
// point of this feature.
//
// Talks to the EXISTING document API — no new endpoints, no schema change:
//   POST  /api/document           {title, language, content}  -> create
//   GET   /api/document/{id}                                   -> read
//   PUT   /api/document/{id}      {content}                    -> save body
//   PATCH /api/document/{id}      {title}                      -> rename
//   GET   /api/documents/titles                                -> {id,title} list

import db from './localdb.js';
import sync from './sync.js';

const API = '';
const LOCAL_DEBOUNCE_MS = 400;
const PUSH_DEBOUNCE_MS = 2500;
const LAST_DOC_KEY = 'odysseus.writer.lastDocId';

/**
 * Save state, surfaced in the header so a failed save is never silent.
 *
 * SAVED means "durably on this device". Whether it has reached the server is a
 * separate axis, reported by sync.state() — conflating them would either lie
 * about durability offline or nag about the network when nothing is wrong.
 */
export const State = {
  IDLE: 'idle',
  DIRTY: 'unsaved',
  SAVING: 'saving',
  SAVED: 'saved',
  ERROR: 'error',
};

let _docId = null;
let _lastLocal = null;     // content last written to IndexedDB
let _localTimer = null;
let _pushTimer = null;
let _writing = false;
let _pendingWhileWriting = false;
let _onState = () => {};
let _getContent = () => '';
let _fallback = false;     // IndexedDB unavailable -> talk straight to the server

async function _json(url, opts) {
  const res = await fetch(API + url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.detail || ''; } catch (_) { /* non-JSON body */ }
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export function currentDocId() { return _docId; }

/** The row shape the writer surface expects, from a local row. */
const _toDoc = (row) => ({
  id: row.id,
  title: row.title || 'Untitled',
  current_content: row.content ?? '',
  tags: Array.isArray(row.tags) ? row.tags : [],
  updated_at: row.updated_at || null,
});

/** Decide once whether the local lane is usable; everything branches on this. */
async function _localReady() {
  if (_fallback) return false;
  const ok = await db.available();
  if (!ok) {
    _fallback = true;
    console.warn('[writer] IndexedDB unavailable — falling back to server-only saves');
  }
  return ok;
}

export async function list() {
  const data = await _json('/api/documents/titles', { method: 'GET' });
  // The endpoint returns {titles:[{id,title}]}; tolerate a bare array too.
  return Array.isArray(data) ? data : (data.titles || data.documents || []);
}

/* ── create ──────────────────────────────────────────────────────────────── */

/**
 * Create a document. `tags` is taken here rather than applied by the caller
 * afterwards, and that is load-bearing:
 *
 * this function schedules an immediate push, so the create can reach the server
 * and REKEY the row before a caller's follow-up enqueue runs. That follow-up
 * would then queue an op against a 'local:' id that no longer exists, whose 404
 * is swallowed as "already gone" — silently losing the folder you asked for.
 * Queueing both ops here, before any push is scheduled, closes the window: sync's
 * rekey repoints them together.
 */
export async function create(title = 'Untitled', { tags = [] } = {}) {
  if (!(await _localReady())) {
    // language:'markdown' so the plain editor and export treat the body correctly.
    const doc = await _json('/api/document', {
      method: 'POST',
      body: JSON.stringify({ title, language: 'markdown', content: '' }),
    });
    _docId = doc.id;
    _lastLocal = doc.current_content ?? '';
    _remember(_docId);
    return doc;
  }

  const id = db.newLocalId();
  const row = {
    id,
    title,
    content: '',
    tags: tags.slice(),
    updated_at: null,
    base_content: '',
    dirty: 1,
    localOnly: 1,
    deleted: 0,
    stub: 0,
    touchedAt: Date.now(),
  };
  await db.putDoc(row);
  await db.enqueue(id, 'create');
  // The create payload carries title and body but NOT tags, so the folder needs
  // its own op — queued here, before the push below, for the reason above.
  if (tags.length) await db.enqueue(id, 'tags', tags.slice());
  _docId = id;
  _lastLocal = '';
  _remember(id);
  sync.refresh();
  _schedulePush(0, { force: true });
  return _toDoc(row);
}

/* ── load ────────────────────────────────────────────────────────────────── */

/**
 * Open a document. Returns as soon as we have something to show: the local copy
 * if we hold one, otherwise the server's.
 *
 * A background refresh follows, and it will NOT touch the editor while the local
 * row is dirty — unsynced text you typed always outranks the server's older copy.
 */
export async function load(id) {
  if (!(await _localReady())) {
    const doc = await _json(`/api/document/${encodeURIComponent(id)}`, { method: 'GET' });
    _docId = doc.id;
    _lastLocal = doc.current_content ?? '';
    _remember(_docId);
    return doc;
  }

  const row = await db.getDoc(id);
  // A stub carries title/tags from the library list but NO body. Opening one as
  // if it were cached would show an empty document and then sync that emptiness
  // over the real text, so a stub counts as "not held locally".
  const cached = row && !row.deleted && !row.stub && row.content !== undefined;
  if (cached) {
    _docId = row.id;
    _lastLocal = row.content ?? '';
    _remember(_docId);
    if (!db.isLocalId(id)) _refreshInBackground(id);
    return _toDoc(row);
  }

  // Not held locally — needs the network.
  if (db.isLocalId(id)) {
    const err = new Error('That document only existed on another device');
    err.status = 404;
    throw err;
  }
  if (navigator.onLine === false) {
    const err = new Error('Not available offline — open it once while connected');
    err.status = 0;
    throw err;
  }
  const doc = await _json(`/api/document/${encodeURIComponent(id)}`, { method: 'GET' });
  await db.putDoc({
    id: doc.id,
    title: doc.title || 'Untitled',
    content: doc.current_content ?? '',
    tags: Array.isArray(doc.tags) ? doc.tags : [],
    updated_at: doc.updated_at || null,
    base_content: doc.current_content ?? '',
    dirty: 0,
    localOnly: 0,
    deleted: 0,
    stub: 0,                       // body fetched — this is a real cached copy now
    touchedAt: Date.now(),
  });
  _docId = doc.id;
  _lastLocal = doc.current_content ?? '';
  _remember(_docId);
  return doc;
}

let _onServerUpdate = () => {};

/**
 * Pull the server's copy after we have already shown the local one. Only applies
 * it when the local row is clean AND the user has not started typing — otherwise
 * the refresh would yank text out from under them.
 */
async function _refreshInBackground(id) {
  try {
    const doc = await _json(`/api/document/${encodeURIComponent(id)}`, { method: 'GET' });
    const row = await db.getDoc(id);
    if (!row || row.dirty) return;                 // unsynced local text wins
    if (id !== _docId) return;                     // user moved on
    const incoming = doc.current_content ?? '';
    await db.patchDoc(id, {
      title: doc.title || row.title,
      content: incoming,
      updated_at: doc.updated_at || null,
      base_content: incoming,
      dirty: 0,
      stub: 0,
    });
    if (incoming !== (_getContent() || '')) {
      _lastLocal = incoming;
      _onServerUpdate({ ...doc, current_content: incoming });
    }
  } catch (_) {
    // Offline or a transient failure — the local copy stands.
  }
}

/* ── rename ──────────────────────────────────────────────────────────────── */

export async function rename(title) {
  if (!_docId) return null;
  if (!(await _localReady())) {
    return _json(`/api/document/${encodeURIComponent(_docId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    });
  }
  await db.patchDoc(_docId, { title, touchedAt: Date.now() });
  // A local-only document has no server row yet; its create will carry the title.
  if (!db.isLocalId(_docId)) await db.enqueue(_docId, 'title', title);
  sync.refresh();
  _schedulePush();
  return { id: _docId, title };
}

function _remember(id) {
  try { localStorage.setItem(LAST_DOC_KEY, id || ''); } catch (_) { /* private mode */ }
}

export function lastDocId() {
  try { return localStorage.getItem(LAST_DOC_KEY) || null; } catch (_) { return null; }
}

/* ── saving ──────────────────────────────────────────────────────────────── */

/**
 * Persist the current content locally now. Returns true if a write happened.
 *
 * Overlapping writes are serialised: a change made while a write is in flight
 * sets a flag and re-runs afterwards, so the last keystroke always wins.
 */
export async function saveNow() {
  if (!_docId) return false;
  if (_writing) { _pendingWhileWriting = true; return false; }

  const content = _getContent();
  if (content === _lastLocal) { _onState(State.SAVED); return false; }

  if (!(await _localReady())) return _saveToServer(content);

  _writing = true;
  try {
    await db.patchDoc(_docId, { content, dirty: 1, touchedAt: Date.now() });
    if (!db.isLocalId(_docId)) await db.enqueue(_docId, 'content', null);
    _lastLocal = content;
    _onState(State.SAVED);
    sync.refresh();
    _schedulePush();
    return true;
  } catch (err) {
    console.warn('[writer] local save failed:', err && err.message);
    _onState(State.ERROR, err);
    return false;
  } finally {
    _writing = false;
    if (_pendingWhileWriting) {
      _pendingWhileWriting = false;
      queueMicrotask(() => { saveNow(); });
    }
  }
}

/** The no-IndexedDB path: the original behaviour, straight to the server. */
async function _saveToServer(content) {
  _writing = true;
  _onState(State.SAVING);
  try {
    const doc = await _json(`/api/document/${encodeURIComponent(_docId)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
    // Trust the server's echo, not our local copy: it may coerce the body (e.g.
    // the email-document path rewrites envelopes), and saving our version as
    // "last saved" would then make every later diff look dirty forever.
    _lastLocal = doc.current_content ?? content;
    _onState(State.SAVED);
    return true;
  } catch (err) {
    console.warn('[writer] save failed:', err && err.message);
    _onState(State.ERROR, err);
    return false;
  } finally {
    _writing = false;
    if (_pendingWhileWriting) {
      _pendingWhileWriting = false;
      queueMicrotask(() => { saveNow(); });
    }
  }
}

/** Mark dirty and schedule a local save. Cheap — safe on every keystroke. */
export function touch() {
  if (!_docId) return;
  if (_getContent() === _lastLocal) return;   // undo back to saved state
  _onState(State.DIRTY);
  if (_localTimer) clearTimeout(_localTimer);
  _localTimer = setTimeout(() => { _localTimer = null; saveNow(); }, LOCAL_DEBOUNCE_MS);
}

/**
 * Ask sync to push, after a real pause in typing.
 *
 * `force` bypasses sync's retry backoff, and DIRECT USER ACTIONS use it. The
 * backoff is global — one document whose op keeps failing would otherwise hold
 * back every other document's first sync, so creating a document could sit
 * unsynced for the length of an unrelated failure's backoff. Typing-driven
 * pushes stay unforced: they repeat constantly and should respect the backoff.
 */
function _schedulePush(delay = PUSH_DEBOUNCE_MS, { force = false } = {}) {
  if (_pushTimer) clearTimeout(_pushTimer);
  _pushTimer = setTimeout(() => { _pushTimer = null; sync.flush({ force }); }, delay);
}

/**
 * Flush a pending save immediately (close, tab hide, navigation).
 *
 * The local write is awaited — that is the one that must not be lost. The server
 * push is only kicked off: on pagehide there is no time for a round trip, and
 * the queue survives in IndexedDB either way.
 */
export async function flush() {
  if (_localTimer) { clearTimeout(_localTimer); _localTimer = null; }
  const wrote = await saveNow();
  if (_pushTimer) { clearTimeout(_pushTimer); _pushTimer = null; }
  sync.flush();
  return wrote;
}

export function isDirty() {
  return !!_docId && _getContent() !== _lastLocal;
}

/** Delete locally and queue the server delete. */
export async function remove(id) {
  const target = id || _docId;
  if (!target) return false;
  if (!(await _localReady())) {
    await _json(`/api/document/${encodeURIComponent(target)}`, { method: 'DELETE' });
    return true;
  }
  await db.patchDoc(target, { deleted: 1, touchedAt: Date.now() });
  await db.enqueue(target, 'delete');
  sync.refresh();
  _schedulePush(0, { force: true });
  return true;
}

/**
 * A local id became a server id. Adopt it so the next save writes to the right
 * row instead of a document that no longer exists.
 */
export function adoptId(oldId, newId) {
  if (_docId === oldId) {
    _docId = newId;
    _remember(newId);
  }
}

/**
 * @param {() => string} getContent  reads the editor's markdown
 * @param {(state: string, err?: Error) => void} onState  status reporter
 * @param {(doc: object) => void} onServerUpdate  a background refresh brought
 *        newer content for the open document
 */
export function configure({ getContent, onState, onServerUpdate }) {
  if (getContent) _getContent = getContent;
  if (onState) _onState = onState;
  if (onServerUpdate) _onServerUpdate = onServerUpdate;
}

export function reset() {
  if (_localTimer) { clearTimeout(_localTimer); _localTimer = null; }
  if (_pushTimer) { clearTimeout(_pushTimer); _pushTimer = null; }
  _docId = null;
  _lastLocal = null;
  _pendingWhileWriting = false;
}

export default {
  State, list, create, load, rename, saveNow, touch, flush, remove,
  isDirty, configure, currentDocId, lastDocId, reset, adoptId,
};
