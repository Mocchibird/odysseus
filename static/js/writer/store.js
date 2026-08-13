// static/js/writer/store.js
//
// FORK-ONLY. Document persistence for the writer surface.
//
// Talks to the EXISTING document API — no new endpoints, no schema change:
//   POST  /api/document           {title, language, content}  -> create
//   GET   /api/document/{id}                                   -> read
//   PUT   /api/document/{id}      {content}                    -> save body
//   PATCH /api/document/{id}      {title}                      -> rename
//   GET   /api/documents/titles                                -> {id,title} list
//
// Autosave is safe against version spam by the server's own design: an identical
// body returns early without writing, and a save landing within
// VERSION_COALESCE_SECONDS (60) updates the latest user version in place rather
// than appending a new one. So a debounced PUT costs at most one version a minute.

const API = '';                 // same-origin
const SAVE_DEBOUNCE_MS = 1200;  // quiet period after the last keystroke
const LAST_DOC_KEY = 'odysseus.writer.lastDocId';

/** Save state, surfaced in the header so a failed save is never silent. */
export const State = {
  IDLE: 'idle',
  DIRTY: 'unsaved',
  SAVING: 'saving',
  SAVED: 'saved',
  ERROR: 'error',
};

let _docId = null;
let _lastSaved = null;     // last content the server acknowledged
let _timer = null;
let _inFlight = false;
let _pendingWhileInFlight = false;
let _onState = () => {};
let _getContent = () => '';

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

export async function list() {
  const data = await _json('/api/documents/titles', { method: 'GET' });
  // The endpoint returns {titles:[{id,title}]}; tolerate a bare array too.
  return Array.isArray(data) ? data : (data.titles || data.documents || []);
}

export async function create(title = 'Untitled') {
  // language:'markdown' so the plain editor and export treat the body correctly.
  const doc = await _json('/api/document', {
    method: 'POST',
    body: JSON.stringify({ title, language: 'markdown', content: '' }),
  });
  _docId = doc.id;
  _lastSaved = doc.current_content ?? '';
  _remember(_docId);
  return doc;
}

export async function load(id) {
  const doc = await _json(`/api/document/${encodeURIComponent(id)}`, { method: 'GET' });
  _docId = doc.id;
  _lastSaved = doc.current_content ?? '';
  _remember(_docId);
  return doc;
}

export async function rename(title) {
  if (!_docId) return null;
  return _json(`/api/document/${encodeURIComponent(_docId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

function _remember(id) {
  try { localStorage.setItem(LAST_DOC_KEY, id || ''); } catch (_) { /* private mode */ }
}

export function lastDocId() {
  try { return localStorage.getItem(LAST_DOC_KEY) || null; } catch (_) { return null; }
}

/**
 * Write the current content now. Returns true if a write actually happened.
 *
 * Overlapping saves are serialised: a change made while a PUT is in flight sets a
 * flag and re-runs afterwards, so the last keystroke always wins and we never
 * have two writes racing for the same document.
 */
export async function saveNow() {
  if (!_docId) return false;
  if (_inFlight) { _pendingWhileInFlight = true; return false; }

  const content = _getContent();
  if (content === _lastSaved) { _onState(State.SAVED); return false; }

  _inFlight = true;
  _onState(State.SAVING);
  try {
    const doc = await _json(`/api/document/${encodeURIComponent(_docId)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
    // Trust the server's echo, not our local copy: it may coerce the body (e.g.
    // the email-document path rewrites envelopes), and saving our version as
    // "last saved" would then make every later diff look dirty forever.
    _lastSaved = doc.current_content ?? content;
    _onState(State.SAVED);
    return true;
  } catch (err) {
    console.warn('[writer] save failed:', err && err.message);
    _onState(State.ERROR, err);
    return false;
  } finally {
    _inFlight = false;
    if (_pendingWhileInFlight) {
      _pendingWhileInFlight = false;
      queueMicrotask(() => { saveNow(); });
    }
  }
}

/** Mark dirty and schedule a debounced save. Cheap — safe on every keystroke. */
export function touch() {
  if (!_docId) return;
  if (_getContent() === _lastSaved) return;   // undo back to saved state
  _onState(State.DIRTY);
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(() => { _timer = null; saveNow(); }, SAVE_DEBOUNCE_MS);
}

/** Flush a pending save immediately (close, tab hide, navigation). */
export function flush() {
  if (_timer) { clearTimeout(_timer); _timer = null; }
  return saveNow();
}

export function isDirty() {
  return !!_docId && _getContent() !== _lastSaved;
}

/**
 * @param {() => string} getContent  reads the editor's markdown
 * @param {(state: string, err?: Error) => void} onState  status reporter
 */
export function configure({ getContent, onState }) {
  if (getContent) _getContent = getContent;
  if (onState) _onState = onState;
}

export function reset() {
  if (_timer) { clearTimeout(_timer); _timer = null; }
  _docId = null;
  _lastSaved = null;
  _pendingWhileInFlight = false;
}

export default {
  State, list, create, load, rename, saveNow, touch, flush,
  isDirty, configure, currentDocId, lastDocId, reset,
};
