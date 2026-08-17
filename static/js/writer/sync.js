// static/js/writer/sync.js
//
// FORK-ONLY. Replays the local outbox to the server when the network is there.
//
// THE RULE THIS MODULE EXISTS TO KEEP: never lose text you typed. Everything
// below follows from that, including the parts that look over-careful.
//
// Ordering. Ops replay in insertion order, and a document whose op fails is
// SKIPPED for the rest of the pass rather than having its later ops applied out
// of order. Other documents keep going — one unreachable document must not wedge
// the queue.
//
// Local ids. A document written offline has no server id, so it gets
// 'local:<uuid>' and a queued 'create'. On success the row is rekeyed and every
// queued op repointed (localdb.rekeyDoc does both in one transaction).
//
// Conflicts. `updated_at` from the server is the detector. We store the value we
// last saw along with the body it went with (base_content). At flush time:
//   * server body already equals ours          -> nothing to do, just reconcile
//   * server has not moved since our base      -> plain write, no conflict
//   * server moved AND its body differs from
//     our base -> both sides edited            -> REAL conflict
// For a real conflict our text wins in place — it is what you were just working
// on and expect to find — and the server's divergent version is saved as a
// separate "(conflict)" document under a Conflicts folder. Neither side is
// discarded. Silently picking a winner is the one outcome this feature must not
// have.
//
// Cross-tab. Two tabs flushing the same queue would double-PUT, so the pass
// takes a Web Lock when the browser has one. `ifAvailable` means the second tab
// skips rather than queues — the first tab is already draining it.

import db from './localdb.js';

const API = '';
const CONFLICT_TAG = 'Conflicts';
const POLL_MS = 30000;          // while the surface is open
const BACKOFF_MIN_MS = 5000;
const BACKOFF_MAX_MS = 300000;  // 5 min — a server that is down stays down a while
const LOCK_NAME = 'odysseus-writer-sync';

let _syncing = false;
let _flushAgain = false;      // a flush was requested while a pass was running
let _flushAgainForce = false; // ...and whether that request wanted to skip backoff
let _started = false;
let _timer = null;
let _backoff = 0;
let _nextAttemptAt = 0;
let _authRequired = false;
let _lastError = null;
let _queued = 0;

let _onState = () => {};
let _onRekey = () => {};
let _onListChanged = () => {};
let _onNotice = () => {};

export function configure({ onState, onRekey, onListChanged, onNotice } = {}) {
  if (onState) _onState = onState;
  if (onRekey) _onRekey = onRekey;
  if (onListChanged) _onListChanged = onListChanged;
  if (onNotice) _onNotice = onNotice;
}

/* ── state reporting ─────────────────────────────────────────────────────── */

export function state() {
  return {
    online: navigator.onLine !== false,
    syncing: _syncing,
    queued: _queued,
    authRequired: _authRequired,
    lastError: _lastError,
  };
}

async function _publish() {
  _queued = await db.outboxCount();
  try { _onState(state()); } catch (e) { console.warn('[writer] sync listener threw', e); }
}

/** Recount and re-publish — call after anything touches the outbox. */
export const refresh = _publish;

/* ── HTTP ────────────────────────────────────────────────────────────────── */

class HttpError extends Error {
  constructor(status, message) { super(message || `HTTP ${status}`); this.status = status; }
}

/** A fetch that rejects with a TypeError is the browser telling us it is offline. */
const isOffline = (err) => err instanceof TypeError;

async function _api(path, opts = {}) {
  const res = await fetch(API + path, {
    credentials: 'same-origin',
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    ...opts,
  });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.detail || ''; } catch (_) { /* non-JSON */ }
    throw new HttpError(res.status, detail);
  }
  // 204-style bodies would blow up .json(); every endpoint we call returns JSON.
  return res.json().catch(() => ({}));
}

const _getDoc = (id) => _api(`/api/document/${encodeURIComponent(id)}`);

const _putContent = (id, content) => _api(`/api/document/${encodeURIComponent(id)}`, {
  method: 'PUT', body: JSON.stringify({ content }),
});

const _patchTitle = (id, title) => _api(`/api/document/${encodeURIComponent(id)}`, {
  method: 'PATCH', body: JSON.stringify({ title }),
});

const _postTags = (id, tags) => _api(
  `/api/document/${encodeURIComponent(id)}/tags?tags=${encodeURIComponent((tags || []).join(','))}`,
  { method: 'POST' },
);

const _delete = (id) => _api(`/api/document/${encodeURIComponent(id)}`, { method: 'DELETE' });

/**
 * Create a document server-side.
 *
 * NOTE: this endpoint has a dedupe branch that can return an EXISTING row rather
 * than a new one — but only for email-language documents created inside a chat
 * session. We never send session_id, so that branch cannot fire here. Do not
 * start sending session_id without revisiting this: two local documents mapping
 * onto one server row would silently merge them.
 */
const _create = (title, content) => _api('/api/document', {
  method: 'POST',
  body: JSON.stringify({ title: title || 'Untitled', language: 'markdown', content: content || '' }),
});

/* ── conflict handling ───────────────────────────────────────────────────── */

function _stamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Park the server's divergent version somewhere findable. Best-effort: if this
 * fails we must NOT go on to overwrite, or the other side's text is gone.
 */
async function _parkConflict(row, serverDoc) {
  const made = await _create(`${row.title || 'Untitled'} (conflict ${_stamp()})`, serverDoc.current_content || '');
  if (made && made.id) {
    // Best-effort: the copy is already safe, the folder is only for finding it.
    try { await _postTags(made.id, [CONFLICT_TAG]); } catch (_) { /* tag is cosmetic */ }
  }
  return made;
}

/* ── the ops ─────────────────────────────────────────────────────────────── */

/**
 * A document the server no longer has (deleted or trashed elsewhere) but that we
 * still hold unsynced text for. Re-creating it in place would resurrect
 * something you may have deliberately deleted, so the text lands in a clearly
 * named new document instead and the original stays gone.
 */
async function _recoverOrphan(row) {
  if (!row || !row.content || !row.content.trim()) return false;
  const made = await _create(`${row.title || 'Untitled'} (recovered ${_stamp()})`, row.content);
  if (made && made.id) {
    _onNotice(`“${row.title || 'Untitled'}” no longer exists on the server — your unsynced text was saved as “${made.title}”.`);
    return true;
  }
  return false;
}

async function _runCreate(op) {
  const row = await db.getDoc(op.docId);
  if (!row || row.deleted) {
    // Created and deleted while offline — it never needs to exist server-side.
    await db.dropOpsFor(op.docId);
    await db.removeDoc(op.docId);
    return;
  }
  // Send the CURRENT local title/body, not whatever the op was queued with.
  const sentTitle = row.title;
  const sentContent = row.content ?? '';
  const made = await _create(sentTitle, sentContent);

  // Rekey FIRST so every queued op for this document (tags, a later delete)
  // follows it to the real id. Only then drop the content/title ops, whose
  // values the create already carried — replaying those would be a redundant
  // write and an extra version. Dropping before the rekey would strand the ops
  // we actually still need.
  await db.rekeyDoc(op.docId, made.id);
  await db.dropOpsOfType(made.id, ['content', 'title'], op.seq);

  // The user can keep typing while the POST is in flight. Whatever the row holds
  // NOW is the truth; anything the create did not carry still has to be pushed,
  // or that text would sit local-only forever with nothing queued to send it.
  const onServer = made.current_content ?? sentContent;
  const after = await db.getDoc(made.id);
  const nowContent = after ? (after.content ?? '') : sentContent;
  const nowTitle = after ? after.title : sentTitle;

  await db.patchDoc(made.id, {
    updated_at: made.updated_at || null,
    base_content: onServer,
    dirty: nowContent !== onServer ? 1 : 0,
    localOnly: 0,
  });
  _onRekey(op.docId, made.id);
  if (nowContent !== onServer) await db.enqueue(made.id, 'content', null);
  if (nowTitle && nowTitle !== (made.title || sentTitle)) await db.enqueue(made.id, 'title', nowTitle);
  _onListChanged();
}

async function _runContent(op) {
  const row = await db.getDoc(op.docId);
  if (!row) return;                                   // row gone; nothing to write

  let server;
  try {
    server = await _getDoc(op.docId);
  } catch (err) {
    if (err.status === 404) {
      await _recoverOrphan(row);
      await db.dropOpsFor(op.docId);
      await db.removeDoc(op.docId);
      _onListChanged();
      return;
    }
    throw err;
  }

  const ours = row.content ?? '';
  const theirs = server.current_content ?? '';

  if (theirs === ours) {
    // Someone (probably an earlier pass, or this same edit from another tab)
    // already wrote our text. Reconcile and drop the op.
    await db.patchDoc(op.docId, { updated_at: server.updated_at || null, base_content: theirs, dirty: 0 });
    return;
  }

  const base = row.base_content ?? '';
  const serverMoved = !!row.updated_at && server.updated_at !== row.updated_at;
  if (serverMoved && theirs !== base) {
    // Both sides changed since our base. Park theirs BEFORE overwriting — if
    // parking fails we throw, the op stays queued, and nothing is lost.
    await _parkConflict(row, server);
    _onNotice(`“${row.title || 'Untitled'}” changed in two places. Your version was kept; the other is saved as a conflict copy.`);
    _onListChanged();
  }

  const saved = await _putContent(op.docId, ours);
  // Trust the echo: the server can coerce a body (the email-document path
  // rewrites envelopes), and storing our copy as the base would make every
  // later comparison look like a conflict forever.
  await db.patchDoc(op.docId, {
    updated_at: saved.updated_at || null,
    base_content: saved.current_content ?? ours,
    dirty: 0,
  });
}

async function _runTitle(op) {
  try {
    const saved = await _patchTitle(op.docId, op.payload);
    await db.patchDoc(op.docId, { updated_at: saved.updated_at || null });
  } catch (err) {
    // Metadata for a document that is gone — nothing to preserve, drop it.
    if (err.status === 404) return;
    throw err;
  }
}

async function _runTags(op) {
  if (db.isLocalId(op.docId)) {
    // A tags op should have been repointed by the create's rekey. Reaching here
    // means it was queued against an id that had already been rekeyed away, and
    // POSTing would just 404 — which this function treats as "already gone" and
    // would silently drop the folder assignment. Say so instead.
    console.warn('[writer] tags op still points at a local id — folder not applied:', op.docId);
    return;
  }
  try {
    await _postTags(op.docId, op.payload || []);
    _onListChanged();
  } catch (err) {
    if (err.status === 404) return;
    throw err;
  }
}

async function _runDelete(op) {
  if (db.isLocalId(op.docId)) {
    // Never existed server-side.
    await db.removeDoc(op.docId);
    return;
  }
  try {
    await _delete(op.docId);
  } catch (err) {
    if (err.status !== 404) throw err;   // already gone is success
  }
  await db.removeDoc(op.docId);
  _onListChanged();
}

const RUNNERS = {
  create: _runCreate,
  content: _runContent,
  title: _runTitle,
  tags: _runTags,
  delete: _runDelete,
};

/* ── the pass ────────────────────────────────────────────────────────────── */

async function _drain() {
  const snapshot = await db.outbox();
  if (!snapshot.length) { _backoff = 0; _authRequired = false; _lastError = null; return; }

  const blocked = new Set();
  let sawError = null;

  for (const stale of snapshot.sort((a, b) => a.seq - b.seq)) {
    // Re-read: running an earlier op can have dropped this one (folded into a
    // create) or repointed it at a new document id. Acting on the snapshot's
    // copy would write to an id that no longer exists.
    const op = await db.getOp(stale.seq);
    if (!op) continue;
    if (blocked.has(op.docId)) continue;               // keep per-document order
    const run = RUNNERS[op.op];
    if (!run) { await db.dequeue(op.seq); continue; }  // unknown op from an older build

    try {
      await run(op);
      await db.dequeue(op.seq);
    } catch (err) {
      blocked.add(op.docId);
      sawError = err;
      await db.markOpFailed(op.seq, err && err.message);

      if (err && (err.status === 401 || err.status === 403)) {
        // Not signed in: every other op will fail the same way. Stop the pass
        // and keep the whole queue for after the next sign-in.
        _authRequired = true;
        break;
      }
      if (isOffline(err)) break;                       // network went away mid-pass
    }
  }

  if (sawError) {
    _lastError = sawError.message || String(sawError);
    _backoff = Math.min(_backoff ? _backoff * 2 : BACKOFF_MIN_MS, BACKOFF_MAX_MS);
    _nextAttemptAt = Date.now() + _backoff;
  } else {
    _backoff = 0;
    _nextAttemptAt = 0;
    _authRequired = false;
    _lastError = null;
  }
}

/**
 * Replay the queue. `force` ignores the backoff window (for a user-initiated
 * "retry now" and for the `online` event, where waiting out a backoff computed
 * while offline would be pointless).
 */
export async function flush({ force = false } = {}) {
  if (_syncing) {
    // Don't drop it. An op enqueued during a pass is not in that pass's snapshot,
    // so returning here silently deferred it to the next periodic poll — up to
    // POLL_MS later. Remember the request and re-run once this pass ends. (Same
    // shape as store.js's _pendingWhileWriting.)
    _flushAgain = true;
    // Carry the REQUEST's force, not the running pass's. An `online` event or a
    // user-initiated create arriving mid-pass must not be downgraded to a
    // backoff-gated retry — that is the same silent delay this flag exists to fix.
    _flushAgainForce = _flushAgainForce || force;
    return false;
  }
  // Claim SYNCHRONOUSLY, before the first await. With the flag set after the
  // awaits below, a second flush could pass the guard above while the first was
  // still awaiting db.available() — both would then run _drain() concurrently.
  // The Web Lock stopped that from double-writing, but the loser's pass became a
  // silent no-op AND set no _flushAgain, so a just-enqueued op sat in the queue
  // until the next periodic poll.
  _syncing = true;
  try {
    if (!(await db.available())) return false;
    if (navigator.onLine === false) return false;
    if (!force && _nextAttemptAt && Date.now() < _nextAttemptAt) return false;
    await _publish();
    // One tab drains; the others skip rather than double-write. ifAvailable
    // returns null instead of waiting for the holder.
    if (navigator.locks && navigator.locks.request) {
      await navigator.locks.request(LOCK_NAME, { ifAvailable: true }, async (lock) => {
        if (!lock) return;
        await _drain();
      });
    } else {
      await _drain();
    }
    return true;
  } catch (e) {
    console.warn('[writer] sync pass failed:', e && e.message);
    _lastError = e && e.message ? e.message : String(e);
    return false;
  } finally {
    _syncing = false;
    await _publish();
    if (_flushAgain) {
      _flushAgain = false;
      const deferredForce = _flushAgainForce;
      _flushAgainForce = false;
      queueMicrotask(() => { flush({ force: deferredForce }); });
    }
  }
}

/* ── triggers ────────────────────────────────────────────────────────────── */

function _onOnline() { flush({ force: true }); }

function _onVisible() { if (document.visibilityState === 'visible') flush(); }

/** Wire the triggers once. Safe to call on every surface open. */
export function start() {
  if (_started) { flush(); return; }
  _started = true;
  window.addEventListener('online', _onOnline);
  document.addEventListener('visibilitychange', _onVisible);
  // A periodic pass covers the cases no event reports: a captive portal that let
  // go, a server that came back, a failed op whose backoff has expired.
  _timer = setInterval(() => { flush(); }, POLL_MS);
  flush({ force: true });
}

export function stop() {
  if (!_started) return;
  _started = false;
  window.removeEventListener('online', _onOnline);
  document.removeEventListener('visibilitychange', _onVisible);
  if (_timer) { clearInterval(_timer); _timer = null; }
}

/* ── offline shell warming ───────────────────────────────────────────────── */

// Assets the writer needs to open with no network. sw.js precaches the SPA
// shell but deliberately not the editor (~490 KB shouldn't load for people who
// never open it), so we warm them ourselves the first time it IS opened.
//
// We write into the service worker's OWN cache rather than a private one: its
// fetch handler only consults CACHE_NAME, so a private cache would never be
// read. Its activate handler deletes every cache except the current one, which
// is exactly right — after an app update these entries are dropped and re-warmed
// on the next online open.
const SHELL_ASSETS = [
  '/static/writer.html',
  '/static/js/writer/writer.js',
  '/static/js/writer/blocks.js',
  '/static/js/writer/store.js',
  '/static/js/writer/outline.js',
  '/static/js/writer/menus.js',
  '/static/js/writer/localdb.js',
  '/static/js/writer/sync.js',
  '/static/js/writer/standalone.js',
  '/static/js/escMenuStack.js',
  '/static/js/ui.js',
  '/static/vendor/lexical/Lexical.prod.mjs',
  '/static/vendor/lexical/LexicalRichText.prod.mjs',
  '/static/vendor/lexical/LexicalList.prod.mjs',
  '/static/vendor/lexical/LexicalCodeCore.prod.mjs',
  '/static/vendor/lexical/LexicalLink.prod.mjs',
  '/static/vendor/lexical/LexicalTable.prod.mjs',
  '/static/vendor/lexical/LexicalMarkdown.prod.mjs',
  '/static/vendor/lexical/LexicalHistory.prod.mjs',
  '/static/vendor/lexical/LexicalUtils.prod.mjs',
  '/static/vendor/lexical/LexicalSelection.prod.mjs',
  '/static/vendor/lexical/LexicalHtml.prod.mjs',
  '/static/vendor/lexical/LexicalClipboard.prod.mjs',
  '/static/vendor/lexical/LexicalDragon.prod.mjs',
];

/** Find the service worker's live cache without importing anything from it. */
async function _shellCache() {
  if (!('caches' in window)) return null;
  const keys = await caches.keys();
  // CACHE_NAME is 'odysseus-v<N>'; take the highest N so a stale cache mid-update
  // isn't the one we fill.
  const mine = keys
    .filter((k) => /^odysseus-v\d+$/.test(k))
    .sort((a, b) => Number(a.slice(10)) - Number(b.slice(10)));
  const name = mine[mine.length - 1];
  return name ? caches.open(name) : null;
}

export async function warmShell() {
  try {
    if (navigator.onLine === false) return false;
    const cache = await _shellCache();
    if (!cache) return false;
    await Promise.all(SHELL_ASSETS.map(async (url) => {
      try {
        // Only fetch what we don't already hold — this runs on every open.
        if (await cache.match(url)) return;
        const res = await fetch(url, { credentials: 'same-origin' });
        if (res && res.ok) await cache.put(url, res);
      } catch (_) { /* one missing asset must not fail the warm */ }
    }));
    await db.setMeta('shellWarmedAt', Date.now());
    return true;
  } catch (e) {
    console.warn('[writer] could not warm the offline shell:', e && e.message);
    return false;
  }
}

export default {
  configure, flush, start, stop, state, refresh, warmShell,
  SHELL_ASSETS, CONFLICT_TAG,
};
