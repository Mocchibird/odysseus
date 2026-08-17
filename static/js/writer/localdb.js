// static/js/writer/localdb.js
//
// FORK-ONLY. The writer's local database: an IndexedDB mirror of the documents
// you have opened, plus an outbox of edits that have not reached the server yet.
//
// WHY IndexedDB and not localStorage: document bodies are unbounded, and
// localStorage is a synchronous ~5 MB store shared with the rest of the app.
// Blocking the main thread on every keystroke-debounce to serialise a long
// document is exactly the lag this feature exists to remove.
//
// DEGRADES SOFT. Private-browsing modes and locked-down profiles can refuse
// IndexedDB outright. Every function here resolves rather than throwing, and
// `available()` reports whether the local lane came up. When it did not, the
// writer falls back to talking straight to the server — the behaviour it had
// before offline support existed. An unavailable local cache must never be the
// reason you cannot edit a document.
//
// SHAPES
//   docs   keyPath 'id'
//     id            server id, or 'local:<uuid>' before the doc has ever synced
//     title         string
//     content       markdown (canonical, same as Document.current_content)
//     tags          string[]
//     updated_at    the server's updated_at as of our last successful sync, or
//                   null for a doc that has never synced. This is the conflict
//                   detector: if the server's value has moved on since, someone
//                   else wrote to this document while we were away.
//     base_content  the body the server had at `updated_at`. Lets us tell a real
//                   three-way conflict from a change only one side made.
//     dirty         1 while local content has not been acknowledged (0/1, not a
//                   boolean — IndexedDB cannot index booleans)
//     localOnly     1 until the server has assigned this document a real id
//     deleted       1 once deleted locally, until the delete is acknowledged
//     touchedAt     ms epoch of the last local edit; orders the list offline
//     stub          1 for a row built from the LIBRARY LIST, which carries
//                   metadata but no body. A stub is not a cached document and
//                   must never be opened as one: `content` is absent, so serving
//                   it to the editor would show an empty document and then sync
//                   that emptiness over the real one. store.load() treats a stub
//                   as "not held locally" and fetches the body.
//
//   outbox keyPath 'seq' (autoIncrement)
//     seq, docId, op ('create'|'content'|'title'|'tags'|'delete'), payload,
//     tries, lastError, at
//
//   meta   keyPath 'k'  — small scalars (last full refresh, schema notes)

const DB_NAME = 'odysseus-writer';
const DB_VERSION = 1;

const DOCS = 'docs';
const OUTBOX = 'outbox';
const META = 'meta';

let _db = null;
let _openFailed = false;
let _opening = null;

/** Promise-wrap an IDBRequest. */
function _req(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

/**
 * Resolve when the transaction COMMITS, not when the last request succeeds.
 * A request can succeed and the transaction still abort (quota, for one), and a
 * caller that trusted the request would then report a save that never landed.
 */
function _committed(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('transaction aborted'));
  });
}

export function open() {
  if (_db) return Promise.resolve(_db);
  if (_openFailed) return Promise.resolve(null);
  if (_opening) return _opening;

  _opening = new Promise((resolve) => {
    let request;
    try {
      request = indexedDB.open(DB_NAME, DB_VERSION);
    } catch (_) {
      // Throws synchronously when storage is disabled entirely.
      _openFailed = true;
      resolve(null);
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(DOCS)) {
        const docs = db.createObjectStore(DOCS, { keyPath: 'id' });
        docs.createIndex('dirty', 'dirty');
        docs.createIndex('touchedAt', 'touchedAt');
      }
      if (!db.objectStoreNames.contains(OUTBOX)) {
        const out = db.createObjectStore(OUTBOX, { keyPath: 'seq', autoIncrement: true });
        out.createIndex('docId', 'docId');
      }
      if (!db.objectStoreNames.contains(META)) db.createObjectStore(META, { keyPath: 'k' });
    };
    request.onsuccess = () => {
      _db = request.result;
      // A second tab running a newer version needs us out of the way, and our
      // handle is dead once that happens.
      _db.onversionchange = () => { try { _db.close(); } catch (_) { /* already gone */ } _db = null; };
      resolve(_db);
    };
    request.onerror = () => { _openFailed = true; resolve(null); };
    request.onblocked = () => { _openFailed = true; resolve(null); };
  }).finally(() => { _opening = null; });

  return _opening;
}

/** True once the local lane is known to work. Callers gate the offline path on it. */
export async function available() {
  return !!(await open());
}

async function _read(stores, fn, fallback) {
  const db = await open();
  if (!db) return fallback;
  try {
    const tx = db.transaction(stores, 'readonly');
    const out = await fn(tx);
    await _committed(tx);
    return out;
  } catch (e) {
    console.warn('[writer] local read failed:', e && e.message);
    return fallback;
  }
}

async function _write(stores, fn) {
  const db = await open();
  if (!db) return false;
  try {
    const tx = db.transaction(stores, 'readwrite');
    const out = await fn(tx);
    await _committed(tx);
    return out === undefined ? true : out;
  } catch (e) {
    console.warn('[writer] local write failed:', e && e.message);
    return false;
  }
}

/* ── documents ───────────────────────────────────────────────────────────── */

export function newLocalId() {
  const rand = (crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `local:${rand}`;
}

export const isLocalId = (id) => typeof id === 'string' && id.startsWith('local:');

export function getDoc(id) {
  if (!id) return Promise.resolve(null);
  return _read([DOCS], (tx) => _req(tx.objectStore(DOCS).get(id)), null);
}

export function putDoc(row) {
  return _write([DOCS], (tx) => _req(tx.objectStore(DOCS).put(row)));
}

/**
 * Merge fields into a stored row (or create it). Read-modify-write inside ONE
 * transaction so two debounced saves cannot interleave and lose a field.
 */
export function patchDoc(id, patch) {
  return _write([DOCS], async (tx) => {
    const store = tx.objectStore(DOCS);
    const cur = (await _req(store.get(id))) || { id };
    const next = { ...cur, ...patch, id };
    await _req(store.put(next));
    return next;
  });
}

/** Every non-deleted document we know about locally, newest local touch first. */
export function allDocs() {
  return _read([DOCS], async (tx) => {
    const rows = await _req(tx.objectStore(DOCS).getAll());
    return (rows || [])
      .filter((r) => !r.deleted)
      .sort((a, b) => (b.touchedAt || 0) - (a.touchedAt || 0));
  }, []);
}

/**
 * Upsert list metadata WITHOUT disturbing a cached body.
 *
 * The library list has no content, so this must never write `content` — a row we
 * hold the real body for has to keep it, and a row we do not stays a stub.
 */
export function mergeListRows(metas) {
  return _write([DOCS], async (tx) => {
    const store = tx.objectStore(DOCS);
    for (const meta of metas || []) await _mergeOne(store, meta);
    return true;
  });
}

export function mergeListRow(meta) {
  return _write([DOCS], (tx) => _mergeOne(tx.objectStore(DOCS), meta));
}

async function _mergeOne(store, meta) {
  const cur = await _req(store.get(meta.id));

  // A delete queued locally must survive a refresh that still lists the document
  // — the server has not processed our delete yet, and resurrecting the row here
  // would make it reappear in the list and then vanish again once sync catches up.
  if (cur && cur.deleted) return true;

  if (cur && cur.dirty) {
    // Unsynced local edits outrank server metadata for title/tags: the user may
    // have renamed or retagged offline and that change is still queued.
    await _req(store.put({ ...cur, updated_at: cur.updated_at || meta.updated_at || null }));
    return true;
  }

  const holdsBody = !!cur && cur.content !== undefined && !cur.stub;
  await _req(store.put({
    ...(cur || {}),
    id: meta.id,
    title: meta.title || (cur && cur.title) || 'Untitled',
    tags: Array.isArray(meta.tags) ? meta.tags : ((cur && cur.tags) || []),
    updated_at: meta.updated_at || null,
    deleted: 0,
    localOnly: 0,
    dirty: 0,
    // Keep a body we already hold; otherwise this stays a metadata-only stub.
    stub: holdsBody ? 0 : 1,
    touchedAt: (cur && cur.touchedAt) || Date.parse(meta.updated_at || '') || Date.now(),
  }));
  return true;
}

export function dirtyDocs() {
  return _read([DOCS], async (tx) => {
    const rows = await _req(tx.objectStore(DOCS).getAll());
    return (rows || []).filter((r) => r.dirty);
  }, []);
}

export function removeDoc(id) {
  return _write([DOCS], (tx) => _req(tx.objectStore(DOCS).delete(id)));
}

/**
 * A local-only document just got a real server id. Move the row and repoint
 * every queued op at the new id, in ONE transaction — a crash between the two
 * would otherwise leave the outbox writing to an id that no longer exists.
 */
export function rekeyDoc(oldId, newId) {
  return _write([DOCS, OUTBOX], async (tx) => {
    const docs = tx.objectStore(DOCS);
    const row = await _req(docs.get(oldId));
    if (row) {
      await _req(docs.delete(oldId));
      await _req(docs.put({ ...row, id: newId, localOnly: 0 }));
    }
    const out = tx.objectStore(OUTBOX);
    const queued = await _req(out.index('docId').getAll(oldId));
    for (const op of queued || []) await _req(out.put({ ...op, docId: newId }));
    return true;
  });
}

/* ── outbox ──────────────────────────────────────────────────────────────── */

/**
 * Queue a server-bound op.
 *
 * content/title/tags COALESCE: only the newest value per (doc, op) matters, so
 * an existing pending row is rewritten instead of appended. Without this, an
 * afternoon offline would queue hundreds of redundant PUTs for one document and
 * replay every one of them on reconnect.
 *
 * create/delete never coalesce — they are ordering events, not values.
 */
export function enqueue(docId, op, payload = null) {
  return _write([OUTBOX], async (tx) => {
    const store = tx.objectStore(OUTBOX);
    if (op === 'content' || op === 'title' || op === 'tags') {
      const mine = await _req(store.index('docId').getAll(docId));
      const existing = (mine || []).find((r) => r.op === op);
      if (existing) {
        await _req(store.put({ ...existing, payload, at: Date.now(), tries: 0, lastError: null }));
        return true;
      }
    }
    await _req(store.add({ docId, op, payload, tries: 0, lastError: null, at: Date.now() }));
    return true;
  });
}

/** Queued ops in insertion order — the order they must be replayed in. */
export function outbox() {
  return _read([OUTBOX], (tx) => _req(tx.objectStore(OUTBOX).getAll()), []);
}

/**
 * Re-read one op by seq. The sync pass works from a snapshot, and running an op
 * can drop or repoint others (a create rekeys its whole document); re-reading
 * immediately before execution is what keeps the pass from acting on a row that
 * has since changed underneath it.
 */
export function getOp(seq) {
  return _read([OUTBOX], (tx) => _req(tx.objectStore(OUTBOX).get(seq)), null);
}

export function outboxCount() {
  return _read([OUTBOX], (tx) => _req(tx.objectStore(OUTBOX).count()), 0);
}

export function dequeue(seq) {
  return _write([OUTBOX], (tx) => _req(tx.objectStore(OUTBOX).delete(seq)));
}

export function markOpFailed(seq, message) {
  return _write([OUTBOX], async (tx) => {
    const store = tx.objectStore(OUTBOX);
    const row = await _req(store.get(seq));
    if (!row) return true;
    await _req(store.put({ ...row, tries: (row.tries || 0) + 1, lastError: String(message || '') }));
    return true;
  });
}

/** Drop every op for one document (it is gone server-side, or was never created). */
export function dropOpsFor(docId) {
  return _write([OUTBOX], async (tx) => {
    const store = tx.objectStore(OUTBOX);
    const mine = await _req(store.index('docId').getAll(docId));
    for (const row of mine || []) await _req(store.delete(row.seq));
    return true;
  });
}

/**
 * Drop only certain op types for a document, optionally keeping one seq.
 * Used after a create: the create sent the document's current title and body, so
 * queued 'content'/'title' ops for it are already satisfied and replaying them
 * would be a redundant write (and an extra version).
 */
export function dropOpsOfType(docId, types, keepSeq = null) {
  const wanted = new Set(types);
  return _write([OUTBOX], async (tx) => {
    const store = tx.objectStore(OUTBOX);
    const mine = await _req(store.index('docId').getAll(docId));
    for (const row of mine || []) {
      if (row.seq === keepSeq) continue;
      if (wanted.has(row.op)) await _req(store.delete(row.seq));
    }
    return true;
  });
}

/* ── meta ────────────────────────────────────────────────────────────────── */

export async function getMeta(k, fallback = null) {
  const row = await _read([META], (tx) => _req(tx.objectStore(META).get(k)), null);
  return row ? row.v : fallback;
}

export function setMeta(k, v) {
  return _write([META], (tx) => _req(tx.objectStore(META).put({ k, v })));
}

export default {
  open, available, newLocalId, isLocalId,
  getDoc, putDoc, patchDoc, allDocs, dirtyDocs, removeDoc, rekeyDoc,
  mergeListRow, mergeListRows,
  enqueue, outbox, getOp, outboxCount, dequeue, markOpFailed, dropOpsFor, dropOpsOfType,
  getMeta, setMeta,
};
