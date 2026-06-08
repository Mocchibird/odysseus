/**
 * Reader enhancements for the Books panel: in-book full-text search, bookmarks,
 * highlights, an AI "explain selection" popover, and read-aloud (TTS). Kept out
 * of notes.js (already huge) — notes.js renders the toolbar via toolbarHtml()
 * and hands us a context (book + current-location getters + a gotoChapter fn)
 * through wire(); we own all the transient overlays and selection handling.
 */
import uiModule from './ui.js';

const API = window.location.origin;

let _ctx = null;             // { root, contentEl, book, getChapterIndex, getChapterTitle, getScrollPercent, gotoChapter, supportsSelection }
let _overlays = [];          // transient DOM nodes to remove on cleanup
let _docHandlers = [];       // [type, fn, opts] document-level listeners to detach
let _cleanupFns = [];        // arbitrary teardown callbacks (e.g. element listeners)
let _selPopover = null;

const esc = uiModule.esc;  // reuse the canonical HTML-escape helper

async function _api(path, opts = {}) {
  const res = await fetch(`${API}/api/books${path}`, {
    credentials: 'same-origin',
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    ...opts,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function _track(node) { if (node) _overlays.push(node); return node; }
function _onDoc(type, fn, opts) { document.addEventListener(type, fn, opts); _docHandlers.push([type, fn, opts]); }

function _label() { return _ctx?.book?.kind === 'pdf' ? 'page' : 'chapter'; }

// ---- Public: the toolbar markup notes.js drops into the reader head ---------
export function toolbarHtml() {
  const I = (p) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
  return `<div class="notes-book-tools" role="group" aria-label="Reader tools">
    <button type="button" class="notes-book-tool" data-book-tool="search" title="Search in this book">${I('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>')}</button>
    <button type="button" class="notes-book-tool" data-book-tool="bookmark" title="Bookmark this spot">${I('<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>')}</button>
    <button type="button" class="notes-book-tool" data-book-tool="annotations" title="Bookmarks &amp; highlights">${I('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/><path d="M9 7h6"/>')}</button>
    <button type="button" class="notes-book-tool" data-book-tool="readaloud" title="Read this ${_label()} aloud">${I('<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>')}</button>
  </div>`;
}

// ---- Public: wire the freshly-rendered reader -------------------------------
export function wire(ctx) {
  cleanup();
  _ctx = ctx;
  if (!ctx || !ctx.root) return;
  ctx.root.querySelectorAll('[data-book-tool]').forEach((btn) => {
    btn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); _onTool(btn.dataset.bookTool, btn); });
  });
  _reflectReadAloud();
  if (ctx.contentEl && ctx.supportsSelection !== false) _setupSelection(ctx.contentEl);
}

export function cleanup() {
  _hideSelPopover();
  _overlays.forEach((n) => { try { n.remove(); } catch (_) {} });
  _overlays = [];
  _docHandlers.forEach(([t, fn, opts]) => document.removeEventListener(t, fn, opts));
  _docHandlers = [];
  _cleanupFns.forEach((fn) => { try { fn(); } catch (_) {} });
  _cleanupFns = [];
}

// ---- Tool dispatch ----------------------------------------------------------
function _onTool(tool, btn) {
  if (tool === 'search') return _openSearch();
  if (tool === 'bookmark') return _addBookmark(btn);
  if (tool === 'annotations') return _openAnnotations();
  if (tool === 'readaloud') return _toggleReadAloud(btn);
}

async function _addBookmark(btn) {
  if (!_ctx) return;
  try {
    await _api('/annotations', {
      method: 'POST',
      body: JSON.stringify({
        path: _ctx.book?.path || '',
        type: 'bookmark',
        chapter_index: _ctx.getChapterIndex?.() || 0,
        chapter_title: _ctx.getChapterTitle?.() || '',
        scroll_percent: _ctx.getScrollPercent?.() || 0,
      }),
    });
    if (btn) { btn.classList.add('flash'); setTimeout(() => btn.classList.remove('flash'), 600); }
    uiModule.showToast?.(`Bookmarked ${_label()} ${(_ctx.getChapterIndex?.() || 0) + 1}`);
  } catch (e) { uiModule.showError?.(e.message); }
}

// ---- Search overlay ---------------------------------------------------------
function _openSearch() {
  if (!_ctx) return;
  _closePanels();
  const panel = _track(document.createElement('div'));
  panel.className = 'book-tool-panel book-search-panel';
  panel.innerHTML = `
    <div class="book-tool-head">
      <input type="search" class="book-search-input" placeholder="Search in this book…" autocomplete="off" />
      <button class="book-tool-close" title="Close">✕</button>
    </div>
    <div class="book-tool-body book-search-results"><div class="book-tool-hint">Type to search the whole book.</div></div>`;
  document.body.appendChild(panel);
  const input = panel.querySelector('.book-search-input');
  const results = panel.querySelector('.book-search-results');
  panel.querySelector('.book-tool-close').addEventListener('click', _closePanels);

  let timer = null;
  const run = async () => {
    const q = input.value.trim();
    if (q.length < 2) { results.innerHTML = '<div class="book-tool-hint">Type at least 2 characters.</div>'; return; }
    results.innerHTML = '<div class="book-tool-hint">Searching…</div>';
    try {
      const data = await _api(`/search?path=${encodeURIComponent(_ctx.book?.path || '')}&q=${encodeURIComponent(q)}`);
      if (!data.matches?.length) { results.innerHTML = '<div class="book-tool-hint">No matches.</div>'; return; }
      const rx = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig');
      results.innerHTML = `<div class="book-tool-meta">${data.total} match${data.total === 1 ? '' : 'es'}${data.truncated ? ' (showing first ' + data.matches.length + ')' : ''}</div>` +
        data.matches.map((m, i) => `
          <button class="book-search-hit" data-i="${i}" data-ch="${m.chapter_index}">
            <span class="book-hit-loc">${_label()} ${m.chapter_index + 1}${m.chapter_title ? ' · ' + esc(m.chapter_title) : ''}</span>
            <span class="book-hit-snip">${esc(m.snippet).replace(rx, '<mark>$1</mark>')}</span>
          </button>`).join('');
      results.querySelectorAll('.book-search-hit').forEach((b) => b.addEventListener('click', () => {
        _ctx.gotoChapter?.(Number(b.dataset.ch));
        _closePanels();
      }));
    } catch (e) { results.innerHTML = `<div class="book-tool-hint">${esc(e.message)}</div>`; }
  };
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 280); });
  _onDoc('keydown', (e) => { if (e.key === 'Escape') _closePanels(); });
  requestAnimationFrame(() => input.focus());
}

// ---- Annotations panel ------------------------------------------------------
async function _openAnnotations() {
  if (!_ctx) return;
  _closePanels();
  const panel = _track(document.createElement('div'));
  panel.className = 'book-tool-panel book-annot-panel';
  panel.innerHTML = `
    <div class="book-tool-head">
      <strong>Bookmarks &amp; highlights</strong>
      <button class="book-tool-close" title="Close">✕</button>
    </div>
    <div class="book-tool-body book-annot-list"><div class="book-tool-hint">Loading…</div></div>`;
  document.body.appendChild(panel);
  panel.querySelector('.book-tool-close').addEventListener('click', _closePanels);
  _onDoc('keydown', (e) => { if (e.key === 'Escape') _closePanels(); });

  const list = panel.querySelector('.book-annot-list');
  const render = (items) => {
    if (!items.length) { list.innerHTML = '<div class="book-tool-hint">No bookmarks or highlights yet. Select text to highlight, or use the 🔖 button to bookmark a spot.</div>'; return; }
    items.sort((a, b) => (a.chapter_index - b.chapter_index) || ((a.created_at || '') < (b.created_at || '') ? -1 : 1));
    list.innerHTML = items.map((a) => `
      <div class="book-annot ${a.type}" data-id="${esc(a.id)}">
        <button class="book-annot-jump" data-ch="${a.chapter_index}">
          <span class="book-annot-badge">${a.type === 'highlight' ? '✎' : '🔖'}</span>
          <span class="book-annot-main">
            <span class="book-annot-loc">${_label()} ${a.chapter_index + 1}${a.chapter_title ? ' · ' + esc(a.chapter_title) : ''}</span>
            ${a.text ? `<span class="book-annot-text">${esc(a.text)}</span>` : ''}
            ${a.note ? `<span class="book-annot-note">${esc(a.note)}</span>` : ''}
          </span>
        </button>
        <button class="book-annot-del" data-id="${esc(a.id)}" title="Delete">✕</button>
      </div>`).join('');
    list.querySelectorAll('.book-annot-jump').forEach((b) => b.addEventListener('click', () => { _ctx.gotoChapter?.(Number(b.dataset.ch)); _closePanels(); }));
    list.querySelectorAll('.book-annot-del').forEach((b) => b.addEventListener('click', async () => {
      try {
        await _api(`/annotations?path=${encodeURIComponent(_ctx.book?.path || '')}&id=${encodeURIComponent(b.dataset.id)}`, { method: 'DELETE' });
        b.closest('.book-annot')?.remove();
        if (!list.querySelector('.book-annot')) render([]);
      } catch (e) { uiModule.showError?.(e.message); }
    }));
  };
  try {
    const data = await _api(`/annotations?path=${encodeURIComponent(_ctx.book?.path || '')}`);
    render(data.items || []);
  } catch (e) { list.innerHTML = `<div class="book-tool-hint">${esc(e.message)}</div>`; }
}

// ---- Read aloud -------------------------------------------------------------
async function _toggleReadAloud(btn) {
  const mgr = window.aiTTSManager;
  if (!mgr) { uiModule.showError?.('Text-to-speech is not available'); return; }
  if (mgr.isPlaying) { mgr.stop(); _reflectReadAloud(); return; }
  try {
    if (btn) btn.classList.add('loading');
    const data = await _api(`/chapter?path=${encodeURIComponent(_ctx.book?.path || '')}&chapter_index=${_ctx.getChapterIndex?.() || 0}`);
    const div = document.createElement('div');
    div.innerHTML = data.chapter?.html || '';
    const text = (div.textContent || '').trim();
    if (!text) { uiModule.showToast?.('Nothing to read on this ' + _label()); return; }
    await mgr.play(text);
  } catch (e) {
    uiModule.showError?.(e.message || 'Could not read aloud');
  } finally {
    if (btn) btn.classList.remove('loading');
    _reflectReadAloud();
  }
}

function _reflectReadAloud() {
  const btn = _ctx?.root?.querySelector('[data-book-tool="readaloud"]');
  if (btn) btn.classList.toggle('active', !!window.aiTTSManager?.isPlaying);
}

// ---- Text selection → highlight / explain / read ----------------------------
function _setupSelection(contentEl) {
  const onUp = () => setTimeout(() => _maybeShowSelPopover(contentEl), 10);
  contentEl.addEventListener('mouseup', onUp);
  contentEl.addEventListener('touchend', onUp);
  _cleanupFns.push(() => contentEl.removeEventListener('mouseup', onUp));
  _cleanupFns.push(() => contentEl.removeEventListener('touchend', onUp));
  _onDoc('selectionchange', () => { const s = window.getSelection(); if (!s || s.isCollapsed) _hideSelPopover(); });
  _onDoc('mousedown', (e) => { if (_selPopover && !_selPopover.contains(e.target)) _hideSelPopover(); });
}

function _maybeShowSelPopover(contentEl) {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) { _hideSelPopover(); return; }
  const text = sel.toString().trim();
  if (text.length < 2) { _hideSelPopover(); return; }
  // Only act on selections inside the reader content.
  let node = sel.anchorNode;
  if (node && node.nodeType === 3) node = node.parentNode;
  if (!node || !contentEl.contains(node)) { _hideSelPopover(); return; }

  let rect;
  try { rect = sel.getRangeAt(0).getBoundingClientRect(); } catch (_) { return; }
  if (!rect || (!rect.width && !rect.height)) return;

  _hideSelPopover();
  const pop = document.createElement('div');
  pop.className = 'book-sel-popover';
  pop.innerHTML = `
    <button class="book-sel-btn" data-act="highlight" title="Highlight">✎ Highlight</button>
    <button class="book-sel-btn" data-act="explain" title="Explain with AI">✨ Explain</button>
    <button class="book-sel-btn" data-act="read" title="Read aloud">🔊</button>`;
  document.body.appendChild(pop);
  _selPopover = pop;
  const top = Math.max(8, rect.top - pop.offsetHeight - 8);
  const left = Math.min(Math.max(8, rect.left + rect.width / 2 - pop.offsetWidth / 2), window.innerWidth - pop.offsetWidth - 8);
  pop.style.top = `${top + window.scrollY}px`;
  pop.style.left = `${left + window.scrollX}px`;

  pop.querySelector('[data-act="highlight"]').addEventListener('click', () => _highlightSelection(text));
  pop.querySelector('[data-act="explain"]').addEventListener('click', () => { _explainSelection(text, rect); });
  pop.querySelector('[data-act="read"]').addEventListener('click', () => {
    if (window.aiTTSManager) window.aiTTSManager.play(text); else uiModule.showError?.('TTS not available');
    _hideSelPopover();
  });
}

function _hideSelPopover() { if (_selPopover) { try { _selPopover.remove(); } catch (_) {} _selPopover = null; } }

async function _highlightSelection(text) {
  try {
    await _api('/annotations', {
      method: 'POST',
      body: JSON.stringify({
        path: _ctx.book?.path || '',
        type: 'highlight',
        chapter_index: _ctx.getChapterIndex?.() || 0,
        chapter_title: _ctx.getChapterTitle?.() || '',
        text,
        scroll_percent: _ctx.getScrollPercent?.() || 0,
      }),
    });
    uiModule.showToast?.('Highlight saved');
  } catch (e) { uiModule.showError?.(e.message); }
  _hideSelPopover();
}

function _explainSelection(text, rect) {
  _hideSelPopover();
  const pop = _track(document.createElement('div'));
  pop.className = 'book-explain-popover';
  pop.innerHTML = `
    <div class="book-explain-head"><span>✨ Explain</span><button class="book-explain-close" title="Close">✕</button></div>
    <div class="book-explain-quote">${esc(text.length > 240 ? text.slice(0, 240) + '…' : text)}</div>
    <div class="book-explain-body"><span class="book-explain-spin"></span> Thinking…</div>`;
  document.body.appendChild(pop);
  const top = rect ? Math.min(rect.bottom + window.scrollY + 8, window.scrollY + window.innerHeight - 220) : window.scrollY + 80;
  const left = rect ? Math.min(Math.max(8, rect.left + window.scrollX), window.innerWidth - 340) : (window.innerWidth - 340) / 2;
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
  pop.querySelector('.book-explain-close').addEventListener('click', () => pop.remove());
  _onDoc('keydown', (e) => { if (e.key === 'Escape') pop.remove(); });

  const body = pop.querySelector('.book-explain-body');
  _api('/explain', { method: 'POST', body: JSON.stringify({ path: _ctx.book?.path || '', text, title: _ctx.book?.title || '' }) })
    .then((data) => { body.textContent = data.explanation || '(no response)'; })
    .catch((e) => { body.innerHTML = `<span class="book-explain-err">${esc(e.message)}</span>`; });
}

function _closePanels() {
  _overlays = _overlays.filter((n) => {
    if (n.classList?.contains('book-tool-panel')) { try { n.remove(); } catch (_) {} return false; }
    return true;
  });
}

const bookToolsModule = { toolbarHtml, wire, cleanup };
export default bookToolsModule;
