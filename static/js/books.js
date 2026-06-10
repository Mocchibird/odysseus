// books.js — the Books reader, a standalone tool window.
//
// A "book" IS a PDF/EPUB in the Knowledge base (one store) — this window is a
// reading VIEW over it: a library list (cover/title/progress + search), upload
// EPUB/PDF, and a reader. PDFs render as continuous-scroll PDF.js canvases via
// pdfReader.js; EPUBs render as chapters with a jump <select> and scroll-driven
// chapter streaming. Reading progress + bookmarks/highlights persist through
// /api/books/* (BookProgress/BookAnnotation, keyed by the Knowledge id). Deleting
// a book is done from the Knowledge panel — they're the same file.
//
// Modal lifecycle mirrors knowledge.js / health.js (Modals.register +
// makeToolModalDraggable, Escape/close, minimize→dock, restore). The reader
// logic was moved out of notes.js so Books is no longer a mode inside Notes.
import * as Modals from './modalManager.js';
import { makeToolModalDraggable } from './modalFullscreen.js?v=370';
import { createPdfReader } from './pdfReader.js?v=383';
import bookToolsModule from './bookTools.js?v=370';
import uiModule from './ui.js';

const API_BASE = window.location.origin;

// ── module state ─────────────────────────────────────────────────────────────
let _open = false;
let _escHandler = null;
let _searchQuery = '';
let _booksSearchTimer = null;
let _books = [];
let _booksLoading = false;
let _booksError = '';
let _pendingOpenPath = null;       // book to auto-open once the modal is up (citations)

let _bookOpenBook = null;          // the currently-open book (null = library list)
let _bookChapterIndex = 0;
let _bookUploadState = null;
let _bookSaveTimer = null;
let _bookChapterLoading = false;
let _bookAutoAdvancing = false;
let _bookKeyHandler = null;
// Page-turning was removed — books are always continuous-scroll (EPUB + PDF).
const _bookReadMode = 'scroll';
// PDFs always render as the actual PDF (text extraction still exists on the
// backend for chat/search — there's just no in-reader Text toggle).
const _bookPdfViewMode = 'pdf';
let _bookNavOpen = true;           // the chapter/page jump row is shown by default
const BOOK_CONTINUOUS_MAX_RENDERED_CHAPTERS = 5;
// Live PDF.js reader controller for the actual-PDF view (see pdfReader.js).
let _bookPdfReader = null;

function _destroyBookPdfReader() {
  if (_bookPdfReader) {
    try { _bookPdfReader.destroy(); } catch (_) {}
    _bookPdfReader = null;
  }
}

// ── helpers ──────────────────────────────────────────────────────────────────
function _esc(s) {
  return uiModule.esc
    ? uiModule.esc(s == null ? '' : s)
    : String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function _attrEsc(s) {
  return String(s == null ? '' : s)
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/`/g, '&#96;');
}
function _formatVaultSize(bytes) {
  const n = Number(bytes || 0);
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  return `${(n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}
function _vaultBasename(path) {
  const raw = String(path || '').split('/').filter(Boolean).pop() || path || 'Untitled';
  try { return decodeURIComponent(raw); } catch (_) { return raw; }
}

function _modal() { return document.getElementById('books-modal'); }
// Where the library list renders (only this is re-rendered on search/refresh, so
// the toolbar search input keeps focus). The reader renders into a separate view.
function _listScroll() { return document.querySelector('#books-modal .books-list-scroll'); }
function _uploadSlot() { return document.querySelector('#books-modal .books-upload-slot'); }
// The reader's scroll container — for EPUBs this IS the continuous scroller the
// chapter-streaming logic drives (was `#notes-pane .notes-pane-body` in notes.js).
function _readerScroller() { return document.querySelector('#books-modal .books-reader-view'); }

// ── data ─────────────────────────────────────────────────────────────────────
async function _fetchBooks() {
  _booksLoading = true;
  _booksError = '';
  try {
    const q = encodeURIComponent(_searchQuery || '');
    const res = await fetch(`${API_BASE}/api/books?q=${q}&limit=100`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _books = data.books || [];
  } catch (e) {
    _books = [];
    _booksError = e?.message || 'Failed to load books';
  } finally {
    _booksLoading = false;
  }
}

async function _openBook(path) {
  const res = await fetch(`${API_BASE}/api/books/open?path=${encodeURIComponent(path || '')}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  _bookOpenBook = data.book || null;
  if (_bookOpenBook) _bookOpenBook._chapterCache = {};
  _bookChapterIndex = Math.max(0, Number(_bookOpenBook?.progress?.chapter_index || 0));
  if (_bookOpenBook?.kind !== 'pdf' || _bookPdfViewMode === 'text') {
    await _loadBookChapter(_bookChapterIndex);
  }
  return _bookOpenBook;
}

async function _loadBookChapter(index = _bookChapterIndex) {
  if (!_bookOpenBook?.path) return null;
  const chapters = _bookOpenBook.chapters || [];
  if (!chapters.length) return null;
  const idx = Math.max(0, Math.min(Number(index || 0), chapters.length - 1));
  _bookOpenBook._chapterCache = _bookOpenBook._chapterCache || {};
  if (_bookOpenBook._chapterCache[idx]) return _bookOpenBook._chapterCache[idx];
  _bookChapterLoading = true;
  try {
    const res = await fetch(`${API_BASE}/api/books/chapter?path=${encodeURIComponent(_bookOpenBook.path)}&chapter_index=${idx}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const chapter = data.chapter || null;
    if (chapter) {
      _bookOpenBook._chapterCache[idx] = chapter;
      _bookOpenBook.chapters[idx] = { ...(_bookOpenBook.chapters[idx] || {}), ...chapter, html: chapter.html };
      return chapter;
    }
  } finally {
    _bookChapterLoading = false;
  }
  return null;
}

async function _renameBook(path, currentTitle = '') {
  const nextTitle = await uiModule.styledPrompt?.('Set the title Iris should use for this book.', {
    title: 'Rename Book',
    defaultValue: currentTitle || _vaultBasename(path),
    placeholder: 'Book title',
    confirmText: 'Save',
    maxLength: 200,
  });
  if (nextTitle === null || nextTitle === undefined) return;
  const clean = String(nextTitle || '').trim();
  if (!clean) {
    uiModule.showError?.('Title is required');
    return;
  }
  const res = await fetch(`${API_BASE}/api/books/title`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, title: clean }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  _books = _books.map(book => book.path === path ? { ...book, title: clean, custom_title: clean } : book);
  if (_bookOpenBook?.path === path) {
    _bookOpenBook.title = clean;
    _bookOpenBook.custom_title = clean;
    if (_bookOpenBook.progress) _bookOpenBook.progress.title = clean;
  }
  await _fetchBooks();
  _render();
  uiModule.showToast?.('Book title saved');
}

// ── continuous-scroll reader internals ───────────────────────────────────────
function _currentBookChapter() {
  const chapters = _bookOpenBook?.chapters || [];
  if (!chapters.length) return null;
  const idx = Math.max(0, Math.min(_bookChapterIndex, chapters.length - 1));
  return _bookOpenBook?._chapterCache?.[idx] || chapters[idx] || null;
}

function _bookUsesContinuousScroll() {
  // EPUBs render as one continuous scroll (PDFs scroll via pdfReader.js).
  return _bookOpenBook?.kind === 'epub';
}

function _renderBookChapterSection(chapter, index, label) {
  const title = chapter?.title || `${label} ${Number(index || 0) + 1}`;
  const html = chapter?.html || '<p>No readable content found.</p>';
  return `<section class="notes-book-chapter-section" data-chapter-index="${Number(index || 0)}">
    <h2>${_esc(title)}</h2>
    <div class="notes-book-html">${html}</div>
  </section>`;
}

function _bookSectionTop(body, section) {
  if (!body || !section) return 0;
  const bodyRect = body.getBoundingClientRect();
  const sectionRect = section.getBoundingClientRect();
  return body.scrollTop + sectionRect.top - bodyRect.top;
}

function _bookSectionReadableHeight(body, section) {
  if (!body || !section) return 1;
  const viewportAllowance = Math.max(80, Math.min(body.clientHeight * 0.65, body.clientHeight - 80));
  return Math.max(1, section.offsetHeight - viewportAllowance);
}

function _bookStreamGapPx(stream) {
  if (!stream) return 0;
  const styles = getComputedStyle(stream);
  const gap = parseFloat(styles.rowGap || styles.gap || '0');
  return Number.isFinite(gap) ? gap : 0;
}

function _bookChapterScrollPercent() {
  const body = _readerScroller();
  if (_bookUsesContinuousScroll()) {
    const section = body?.querySelector(`.notes-book-chapter-section[data-chapter-index="${_bookChapterIndex}"]`);
    if (body && section) {
      const sectionTop = _bookSectionTop(body, section);
      const pct = ((body.scrollTop - sectionTop) / _bookSectionReadableHeight(body, section)) * 100;
      return Math.max(0, Math.min(100, pct));
    }
  }
  const page = body?.querySelector('.notes-book-page');
  const scroller = _bookReadMode === 'page' && page ? page : body;
  if (scroller && scroller.scrollHeight > scroller.clientHeight) {
    return (scroller.scrollTop / Math.max(1, scroller.scrollHeight - scroller.clientHeight)) * 100;
  }
  return 0;
}

function _updateBookVisibleChapterFromScroll() {
  if (!_bookUsesContinuousScroll()) return;
  const body = _readerScroller();
  const sections = Array.from(body?.querySelectorAll('.notes-book-chapter-section[data-chapter-index]') || []);
  if (!body || !sections.length) return;
  const bodyRect = body.getBoundingClientRect();
  const anchorY = bodyRect.top + Math.min(Math.max(body.clientHeight * 0.24, 120), 260);
  let bestIndex = _bookChapterIndex;
  let bestDistance = Infinity;
  for (const section of sections) {
    const rect = section.getBoundingClientRect();
    if (rect.bottom <= bodyRect.top + 64) continue;
    const distance = rect.top <= anchorY && rect.bottom >= anchorY
      ? 0
      : Math.min(Math.abs(rect.top - anchorY), Math.abs(rect.bottom - anchorY));
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = Number(section.dataset.chapterIndex || 0);
    }
  }
  if (!Number.isFinite(bestIndex) || bestIndex === _bookChapterIndex) return;
  _bookChapterIndex = bestIndex;
  const select = body.querySelector('.notes-book-select');
  if (select) select.value = String(bestIndex);
  const chapter = _currentBookChapter();
  if (_bookOpenBook?.progress) {
    _bookOpenBook.progress.chapter_index = bestIndex;
    _bookOpenBook.progress.chapter_title = chapter?.title || '';
  }
  const line = body.querySelector('.notes-epub-progress-line span');
  if (line) line.style.width = `${_bookChapterScrollPercent()}%`;
}

function _trimBookContinuousStream() {
  if (!_bookUsesContinuousScroll()) return;
  const body = _readerScroller();
  const stream = body?.querySelector('.notes-book-stream');
  if (!body || !stream) return;
  const sections = Array.from(stream.querySelectorAll('.notes-book-chapter-section[data-chapter-index]'));
  if (sections.length <= BOOK_CONTINUOUS_MAX_RENDERED_CHAPTERS) return;

  const keepBefore = 2;
  const keepAfter = Math.max(1, BOOK_CONTINUOUS_MAX_RENDERED_CHAPTERS - keepBefore - 1);
  const minKeep = Math.max(0, _bookChapterIndex - keepBefore);
  const maxKeep = _bookChapterIndex + keepAfter;
  const gap = _bookStreamGapPx(stream);
  let removedAbove = 0;

  for (const section of sections) {
    const idx = Number(section.dataset.chapterIndex || 0);
    if (idx >= minKeep && idx <= maxKeep) continue;
    const sectionTop = _bookSectionTop(body, section);
    const sectionHeight = section.getBoundingClientRect().height + gap;
    if (sectionTop < body.scrollTop) removedAbove += sectionHeight;
    section.remove();
  }
  if (removedAbove > 0) body.scrollTop = Math.max(0, body.scrollTop - removedAbove);

  const remaining = Array.from(stream.querySelectorAll('.notes-book-chapter-section[data-chapter-index]'));
  if (remaining.length) {
    stream.dataset.streamStart = String(Number(remaining[0].dataset.chapterIndex || 0));
    stream.dataset.streamEnd = String(Number(remaining[remaining.length - 1].dataset.chapterIndex || 0));
  }

  const cache = _bookOpenBook?._chapterCache || {};
  const cacheMin = Math.max(0, _bookChapterIndex - 2);
  const cacheMax = _bookChapterIndex + 3;
  for (const key of Object.keys(cache)) {
    const idx = Number(key);
    if (!Number.isFinite(idx) || (idx >= cacheMin && idx <= cacheMax)) continue;
    delete cache[key];
    if (_bookOpenBook?.chapters?.[idx]) delete _bookOpenBook.chapters[idx].html;
  }
}

async function _appendNextBookChapterIfNeeded() {
  if (!_bookUsesContinuousScroll() || _bookChapterLoading || _bookAutoAdvancing) return;
  const body = _readerScroller();
  const stream = body?.querySelector('.notes-book-stream');
  const chapters = _bookOpenBook?.chapters || [];
  if (!body || !stream || !chapters.length) return;
  const remaining = body.scrollHeight - (body.scrollTop + body.clientHeight);
  if (remaining > Math.max(700, body.clientHeight * 1.35)) return;
  const currentEnd = Number(stream.dataset.streamEnd || _bookChapterIndex);
  const nextIndex = currentEnd + 1;
  if (nextIndex >= chapters.length) return;
  _bookAutoAdvancing = true;
  const loading = document.createElement('div');
  loading.className = 'notes-book-stream-loading';
  loading.textContent = 'Loading next chapter...';
  stream.appendChild(loading);
  try {
    const next = await _loadBookChapter(nextIndex);
    loading.remove();
    stream.insertAdjacentHTML('beforeend', _renderBookChapterSection(next || chapters[nextIndex], nextIndex, 'Chapter'));
    stream.dataset.streamEnd = String(nextIndex);
    _trimBookContinuousStream();
    requestAnimationFrame(() => _appendNextBookChapterIfNeeded());
  } catch (err) {
    loading.textContent = err?.message || 'Failed to load next chapter';
  } finally {
    _bookAutoAdvancing = false;
  }
}

async function _prependPrevBookChapterIfNeeded() {
  if (!_bookUsesContinuousScroll() || _bookChapterLoading || _bookAutoAdvancing) return;
  const body = _readerScroller();
  const stream = body?.querySelector('.notes-book-stream');
  const chapters = _bookOpenBook?.chapters || [];
  if (!body || !stream || !chapters.length) return;
  // Only pull in the previous chapter when the reader is near the very top, so
  // backward continuous reading flows the same way forward reading does.
  if (body.scrollTop > Math.max(160, body.clientHeight * 0.5)) return;
  const currentStart = Number(stream.dataset.streamStart || _bookChapterIndex);
  const prevIndex = currentStart - 1;
  if (prevIndex < 0) return;
  _bookAutoAdvancing = true;
  try {
    const prev = await _loadBookChapter(prevIndex);
    const firstSection = stream.querySelector('.notes-book-chapter-section');
    const heightBefore = body.scrollHeight;
    if (firstSection) {
      firstSection.insertAdjacentHTML('beforebegin', _renderBookChapterSection(prev || chapters[prevIndex], prevIndex, 'Chapter'));
    } else {
      stream.insertAdjacentHTML('afterbegin', _renderBookChapterSection(prev || chapters[prevIndex], prevIndex, 'Chapter'));
    }
    stream.dataset.streamStart = String(prevIndex);
    // Keep the reader visually anchored: the newly inserted chapter grows the
    // content above the viewport, so push scrollTop down by exactly that much.
    const added = body.scrollHeight - heightBefore;
    if (added > 0) body.scrollTop += added;
  } catch (_) {
    /* leave the stream as-is; Prev still works */
  } finally {
    _bookAutoAdvancing = false;
  }
}

async function _saveBookProgressNow(scrollPercent = null) {
  if (!_bookOpenBook) return;
  const chapter = _currentBookChapter();
  let pct = Number(scrollPercent);
  if (!Number.isFinite(pct)) {
    pct = _bookChapterScrollPercent();
  }
  try {
    const res = await fetch(`${API_BASE}/api/books/progress`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: _bookOpenBook.path,
        kind: _bookOpenBook.kind || '',
        title: _bookOpenBook.title || '',
        author: _bookOpenBook.author || '',
        chapter_index: _bookChapterIndex,
        chapter_title: chapter?.title || '',
        scroll_percent: pct,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) _bookOpenBook.progress = data.progress || _bookOpenBook.progress || {};
  } catch {}
}

function _scheduleBookProgressSave(scrollPercent = null) {
  if (_bookSaveTimer) clearTimeout(_bookSaveTimer);
  _bookSaveTimer = setTimeout(() => _saveBookProgressNow(scrollPercent), 700);
}

async function _turnBookPage(direction = 1) {
  if (!_bookOpenBook?.chapters?.length) return;
  const dir = Number(direction || 0) < 0 ? -1 : 1;
  if (_bookReadMode === 'scroll') {
    await _setBookChapter(_bookChapterIndex + dir);
    return;
  }
  const page = _readerScroller()?.querySelector('.notes-book-page');
  if (!page) {
    await _setBookChapter(_bookChapterIndex + dir);
    return;
  }
  const maxScroll = Math.max(0, page.scrollHeight - page.clientHeight);
  const step = Math.max(160, Math.floor(page.clientHeight * 0.86));
  if (dir > 0) {
    if (page.scrollTop >= maxScroll - 8) {
      if (_bookChapterIndex >= _bookOpenBook.chapters.length - 1) return;
      await _setBookChapter(_bookChapterIndex + 1);
    } else {
      page.scrollTo({ top: Math.min(maxScroll, page.scrollTop + step), behavior: 'smooth' });
      _scheduleBookProgressSave();
    }
  } else if (page.scrollTop <= 8) {
    if (_bookChapterIndex <= 0) return;
    await _setBookChapter(_bookChapterIndex - 1);
  } else {
    page.scrollTo({ top: Math.max(0, page.scrollTop - step), behavior: 'smooth' });
    _scheduleBookProgressSave();
  }
}

async function _setBookChapter(index, restoreProgress = false) {
  if (!_bookOpenBook?.chapters?.length) return;
  _bookChapterIndex = Math.min(Math.max(Number(index || 0), 0), _bookOpenBook.chapters.length - 1);
  if (_bookOpenBook.kind === 'pdf') {
    // Jump within the live continuous-scroll PDF (no full re-render — keeps the
    // already-rasterized canvases). Falls back to a rebuild if not ready yet.
    if (_bookPdfReader) {
      _bookPdfReader.goToPage(_bookChapterIndex + 1, { behavior: 'smooth' });
      _scheduleBookProgressSave();
    } else {
      _render();
    }
    return;
  }
  _render();
  await _loadBookChapter(_bookChapterIndex);
  _render();
  requestAnimationFrame(() => {
    const body = _readerScroller();
    const page = body?.querySelector('.notes-book-page');
    const pct = restoreProgress ? Number(_bookOpenBook?.progress?.scroll_percent || 0) : 0;
    const targetScroll = (node) => {
      if (!node) return 0;
      return (Math.max(1, node.scrollHeight - node.clientHeight) * pct) / 100;
    };
    if (_bookUsesContinuousScroll() && body) {
      const section = body.querySelector(`.notes-book-chapter-section[data-chapter-index="${_bookChapterIndex}"]`);
      if (section) {
        const sectionTop = _bookSectionTop(body, section);
        body.scrollTop = restoreProgress && pct > 0
          ? Math.max(0, sectionTop + (_bookSectionReadableHeight(body, section) * pct) / 100)
          : Math.max(0, sectionTop);
      } else {
        body.scrollTop = 0;
      }
    } else if (page) {
      page.scrollTop = restoreProgress && pct > 0 && _bookReadMode === 'page' ? targetScroll(page) : 0;
    }
    if (!_bookUsesContinuousScroll() && body && restoreProgress && pct > 0 && _bookReadMode === 'scroll') {
      body.scrollTop = targetScroll(body);
    } else if (!_bookUsesContinuousScroll() && body) {
      body.scrollTop = 0;
    }
    _saveBookProgressNow(pct);
  });
}

// ── upload ───────────────────────────────────────────────────────────────────
function _renderUploadProgress() {
  const slot = _uploadSlot();
  if (!slot) return;
  if (!_bookUploadState) { slot.innerHTML = ''; return; }
  const s = _bookUploadState;
  slot.innerHTML = `
    <div class="notes-book-upload-progress${s.indeterminate ? ' indeterminate' : ''}${s.error ? ' error' : ''}">
      <div class="notes-book-upload-top">
        <span>${_esc(s.label || 'Uploading')}</span>
        <span>${s.active ? `${Math.round(s.percent || 0)}%` : ''}</span>
      </div>
      <div class="notes-book-upload-track"><span style="width:${Math.max(2, Math.min(100, s.percent || 0))}%"></span></div>
      ${s.detail ? `<div class="notes-book-upload-detail">${_esc(s.detail)}</div>` : ''}
    </div>`;
}

function _uploadBookFile(file) {
  _bookUploadState = {
    active: true,
    percent: 0,
    label: `Uploading ${file.name}`,
    detail: _formatVaultSize(file.size || 0),
  };
  _renderUploadProgress();
  const form = new FormData();
  form.append('file', file);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/api/books/upload`);
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const pct = Math.max(0, Math.min(100, (event.loaded / event.total) * 100));
        _bookUploadState = {
          active: true,
          percent: pct,
          label: `Uploading ${file.name}`,
          detail: `${_formatVaultSize(event.loaded)} / ${_formatVaultSize(event.total)}`,
        };
      } else {
        _bookUploadState = { ..._bookUploadState, indeterminate: true };
      }
      _renderUploadProgress();
    };
    xhr.onload = async () => {
      const payload = xhr.response || {};
      if (xhr.status >= 200 && xhr.status < 300 && payload.ok !== false) {
        _bookUploadState = {
          active: false,
          percent: 100,
          label: `${payload.file?.title || file.name} saved`,
          detail: payload.indexing ? 'Indexing in the background' : 'Ready',
        };
        _renderUploadProgress();
        await _fetchBooks();
        if (!_bookOpenBook) _renderListInto();
        resolve(payload);
      } else {
        reject(new Error(payload.detail || payload.error || 'Upload failed'));
      }
    };
    xhr.onerror = () => reject(new Error('Upload failed'));
    xhr.onabort = () => reject(new Error('Upload cancelled'));
    xhr.send(form);
  });
}

function _scheduleBooksSearch() {
  if (_booksSearchTimer) clearTimeout(_booksSearchTimer);
  _booksSearchTimer = setTimeout(async () => {
    if (!_open || _bookOpenBook) return;  // searching only applies to the library list
    await _fetchBooks();
    _renderListInto();
  }, 180);
}

// ── reader render ────────────────────────────────────────────────────────────
function _wireBookTools(body, { supportsSelection } = {}) {
  const root = body.querySelector('.notes-book-reader');
  if (!root) return;
  bookToolsModule.wire({
    root,
    contentEl: supportsSelection ? root.querySelector('.notes-book-content') : null,
    book: _bookOpenBook,
    supportsSelection: !!supportsSelection,
    getChapterIndex: () => _bookChapterIndex || 0,
    getChapterTitle: () => _currentBookChapter()?.title || '',
    getScrollPercent: () => { try { return _bookChapterScrollPercent(); } catch (_) { return 0; } },
    gotoChapter: (i) => _setBookChapter(i),
  });
}

function _renderBookReader(body, baseHtml) {
  // Tear down any live PDF.js reader before we rebuild the DOM (chapter jump,
  // re-render). The PDF branch below recreates it when needed.
  _destroyBookPdfReader();
  bookToolsModule.cleanup();
  const book = _bookOpenBook;
  const chapters = book?.chapters || [];
  const chapter = _currentBookChapter();
  const idx = _bookChapterIndex || 0;
  const isPdf = book?.kind === 'pdf';
  const label = book?.kind === 'pdf' ? 'Page' : 'Chapter';
  const savedIndex = Number(book?.progress?.chapter_index || 0);
  const progressPct = savedIndex === idx ? Number(book?.progress?.scroll_percent || 0) : 0;
  const options = chapters.map((ch, i) => `<option value="${i}" ${i === idx ? 'selected' : ''}>${i + 1}. ${_esc(ch.title || `${label} ${i + 1}`)}</option>`).join('');
  const loadingHtml = '<p class="notes-book-loading">Loading this section...</p>';
  const contentHtml = _bookChapterLoading && !chapter?.html ? loadingHtml : (chapter?.html || '<p>No readable content found.</p>');
  const continuousScroll = _bookUsesContinuousScroll();
  const pageHtml = continuousScroll
    ? (_bookChapterLoading && !chapter?.html ? loadingHtml : _renderBookChapterSection(chapter, idx, label))
    : `<h2>${_esc(chapter?.title || `${label} ${idx + 1}`)}</h2><div class="notes-book-html">${contentHtml}</div>`;
  const navOpen = _bookNavOpen;
  const hasNav = chapters.length > 1;
  // A list icon that toggles the collapsible chapter/page nav row.
  const navToggleHtml = hasNav ? `<button type="button" class="notes-book-tool notes-book-nav-toggle${navOpen ? ' active' : ''}" title="${label}s" aria-label="${label} navigation" aria-expanded="${navOpen}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        </button>` : '';
  // Page-turning removed: EPUB + PDF both render as continuous scroll — no
  // scroll/page mode toggle and no Prev/Next buttons, just the chapter/page jump.
  const navRowHtml = hasNav ? `<div class="notes-book-nav-row">
        <select class="notes-select-trigger notes-book-select" aria-label="Jump to ${label.toLowerCase()}">${options}</select>
      </div>` : '';
  const openLinkHtml = isPdf ? `<a class="notes-book-tool notes-book-pdf-open" href="${_attrEsc(`${API_BASE}/api/books/file?path=${encodeURIComponent(book?.path || '')}`)}" target="_blank" rel="noopener" title="Open the PDF in a new tab" aria-label="Open in a new tab">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </a>` : '';
  // One compact header line: back + title + (nav toggle) + reader tools + open/rename.
  // The chapter/page nav lives in a collapsible row beneath it (navRowHtml).
  const headHtml = `
    <div class="notes-book-chrome">
    <div class="notes-vault-reader-head notes-book-head">
      <button type="button" class="notes-vault-back" title="Back to books">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
      </button>
      <div class="notes-vault-reader-title">
        <strong>${_esc(book?.title || book?.path || (isPdf ? 'PDF' : 'Book'))}</strong>
        ${book?.author ? `<span>${_esc(book.author)}</span>` : ''}
      </div>
      <div class="notes-book-head-tools">
        ${navToggleHtml}
        ${bookToolsModule.toolbarHtml()}
        ${openLinkHtml}
        <button type="button" class="notes-book-tool notes-book-reader-edit" data-path="${_attrEsc(book?.path || '')}" data-title="${_attrEsc(book?.title || '')}" title="Rename book" aria-label="Rename book">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
        </button>
      </div>
    </div>
    ${navRowHtml}
    </div>`;
  if (isPdf) {
    // Continuous-scroll PDF via pdfReader.js (PDF.js → canvas), so multi-page
    // PDFs scroll consistently on desktop AND mobile — the native <iframe> viewer
    // was unreliable on mobile. Same chrome + chapter/page jump as EPUBs.
    const fileUrl = `${API_BASE}/api/books/file?path=${encodeURIComponent(book?.path || '')}`;
    const startPct = Math.max(0, Math.min(progressPct, 100));
    body.innerHTML = baseHtml + `<div class="notes-book-reader notes-book-reader-pdf${navOpen ? ' notes-book-nav-open' : ''}">
      ${headHtml}
      <article class="notes-book-content notes-book-content-pdf">
        <div class="notes-epub-progress-line"><span style="width:${startPct}%"></span></div>
        <div class="notes-book-pdf-viewer"></div>
      </article>
    </div>`;
    _wireBookReaderHead(body);
    _wireBookTools(body, { supportsSelection: false });
    const pdfContainer = body.querySelector('.notes-book-pdf-viewer');
    const wantPath = book?.path || '';
    if (pdfContainer) {
      createPdfReader(pdfContainer, {
        url: fileUrl,
        initialPage: (_bookChapterIndex || 0) + 1,
        onPageChange: (p) => {
          _bookChapterIndex = Math.max(0, p - 1);
          const sel = body.querySelector('.notes-book-select');
          if (sel && String(sel.value) !== String(_bookChapterIndex)) sel.value = String(_bookChapterIndex);
        },
        onProgress: (pct) => {
          const line = body.querySelector('.notes-epub-progress-line span');
          if (line) line.style.width = `${Math.max(0, Math.min(pct, 100))}%`;
          _scheduleBookProgressSave(pct);
        },
      }).then((reader) => {
        // If the user navigated away (or switched books) before PDF.js loaded,
        // discard this reader instead of leaking it.
        if (!document.body.contains(pdfContainer) || _bookOpenBook?.path !== wantPath) {
          try { reader.destroy(); } catch (_) {}
          return;
        }
        _bookPdfReader = reader;
      }).catch((err) => {
        // PDF.js failed to load/parse — fall back to the browser's native viewer
        // so the PDF is at least openable.
        if (!document.body.contains(pdfContainer)) return;
        console.warn('pdfReader failed; falling back to native iframe:', err);
        pdfContainer.innerHTML = `<iframe class="notes-book-pdf-frame" src="${_attrEsc(fileUrl + '#view=FitH')}" title="${_attrEsc(book?.title || 'PDF')}"></iframe>`;
      });
    }
    return;
  }
  body.innerHTML = baseHtml + `<div class="notes-book-reader notes-book-reader-${_attrEsc(_bookReadMode)}${navOpen ? ' notes-book-nav-open' : ''}">
    ${headHtml}
    <article class="notes-book-content notes-book-content-${_attrEsc(_bookReadMode)}${continuousScroll ? ' notes-book-content-continuous' : ''}">
      <div class="notes-epub-progress-line"><span style="width:${Math.max(0, Math.min(progressPct, 100))}%"></span></div>
      <div class="notes-book-page${continuousScroll ? ' notes-book-stream' : ''}" tabindex="0" ${continuousScroll ? `data-stream-start="${idx}" data-stream-end="${idx}"` : ''}>${pageHtml}</div>
    </article>
  </div>`;
  _wireBookReaderHead(body);
  if (body._notesBookScrollHandler) body.removeEventListener('scroll', body._notesBookScrollHandler);
  const page = body.querySelector('.notes-book-page');
  body._notesBookScrollHandler = () => {
    if (_bookUsesContinuousScroll()) {
      _updateBookVisibleChapterFromScroll();
      const line = body.querySelector('.notes-epub-progress-line span');
      if (line) line.style.width = `${_bookChapterScrollPercent()}%`;
      _trimBookContinuousStream();
      _scheduleBookProgressSave();
      _appendNextBookChapterIfNeeded();
      _prependPrevBookChapterIfNeeded();
      return;
    }
    _scheduleBookProgressSave();
  };
  const scrollNode = _bookReadMode === 'page' && page ? page : body;
  scrollNode.addEventListener('scroll', body._notesBookScrollHandler, { passive: true });
  if (continuousScroll) requestAnimationFrame(() => _appendNextBookChapterIfNeeded());
  if (_bookKeyHandler) document.removeEventListener('keydown', _bookKeyHandler);
  _bookKeyHandler = (e) => {
    if (!_bookOpenBook || !_open) return;
    const target = e.target;
    const tag = target?.tagName || '';
    if (target?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(tag)) return;
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      _turnBookPage(1);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      _turnBookPage(-1);
    }
  };
  document.addEventListener('keydown', _bookKeyHandler);
  if (page) {
    let touchStartX = 0;
    let touchStartY = 0;
    page.addEventListener('touchstart', (e) => {
      const touch = e.touches?.[0];
      if (!touch) return;
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
    }, { passive: true });
    page.addEventListener('touchend', (e) => {
      if (_bookReadMode !== 'page') return;
      const touch = e.changedTouches?.[0];
      if (!touch) return;
      const dx = touch.clientX - touchStartX;
      const dy = touch.clientY - touchStartY;
      if (Math.abs(dx) < 52 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
      _turnBookPage(dx < 0 ? 1 : -1);
    }, { passive: true });
  }
  _wireBookTools(body, { supportsSelection: true });
}

// Shared wiring for the reader header (both PDF + EPUB): back, rename, and the
// collapsible chapter/page nav toggle + the chapter/page jump <select>.
function _wireBookReaderHead(body) {
  const book = _bookOpenBook;
  const reader = body.querySelector('.notes-book-reader');
  body.querySelector('.notes-vault-back')?.addEventListener('click', () => {
    _saveBookProgressNow(book?.kind === 'pdf' ? 0 : undefined);
    if (_bookKeyHandler) { document.removeEventListener('keydown', _bookKeyHandler); _bookKeyHandler = null; }
    _destroyBookPdfReader();
    bookToolsModule.cleanup();
    _bookOpenBook = null;
    _render();
    // Refresh the library so the just-read book shows updated progress.
    _fetchBooks().then(() => { if (!_bookOpenBook) _renderListInto(); });
  });
  body.querySelector('.notes-book-reader-edit')?.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await _renameBook(e.currentTarget.dataset.path || book?.path || '', e.currentTarget.dataset.title || book?.title || '');
    } catch (err) {
      uiModule.showError?.(err?.message || 'Failed to rename book');
    }
  });
  // Expand/collapse the nav row in place (no full re-render); persist the state.
  body.querySelector('.notes-book-nav-toggle')?.addEventListener('click', (e) => {
    _bookNavOpen = !_bookNavOpen;
    reader?.classList.toggle('notes-book-nav-open', _bookNavOpen);
    e.currentTarget.classList.toggle('active', _bookNavOpen);
    e.currentTarget.setAttribute('aria-expanded', String(_bookNavOpen));
  });
  body.querySelector('.notes-book-select')?.addEventListener('change', (e) => _setBookChapter(e.target.value));
}

// ── library list render ──────────────────────────────────────────────────────
function _renderListInto() {
  const scroll = _listScroll();
  if (!scroll) return;
  _renderUploadProgress();
  if (_booksLoading) {
    scroll.innerHTML = `<div class="notes-skeleton"><div class="notes-skeleton-card"></div><div class="notes-skeleton-card short"></div><div class="notes-skeleton-card"></div></div>`;
    return;
  }
  if (_booksError) {
    scroll.innerHTML = `<div class="notes-empty-msg">${_esc(_booksError)}</div>`;
    return;
  }
  if (!_books.length) {
    scroll.innerHTML = `<div class="notes-empty-msg">${_searchQuery ? 'No books match your search.' : 'No EPUB or PDF books yet — upload one above, or add a PDF/EPUB in the Knowledge panel.'}</div>`;
    return;
  }
  let html = '<div class="notes-vault-list notes-books-list">';
  for (const book of _books) {
    const kind = book.kind === 'pdf' ? 'pdf' : 'epub';
    const progress = book.progress || {};
    const loc = progress.updated_at
      ? `${kind === 'pdf' ? 'page' : 'chapter'} ${Number(progress.chapter_index || 0) + 1}`
      : 'not started';
    const title = book.title || _vaultBasename(book.path || 'Book');
    const coverImg = kind === 'epub'
      ? `<img class="notes-book-cover-img" alt="" loading="lazy" data-cover="${_attrEsc(book.path || '')}" />`
      : '';
    html += `<div class="notes-vault-file notes-book-file notes-vault-kind-${_attrEsc(kind)}" data-path="${_attrEsc(book.path || '')}" data-title="${_attrEsc(title)}" title="${_attrEsc(book.path || '')}" role="button" tabindex="0">
      <span class="notes-book-cover${kind === 'epub' ? '' : ' no-cover'}"><span class="notes-book-cover-fallback">${_esc((book.kind || kind).toUpperCase())}</span>${coverImg}</span>
      <span class="notes-book-row-main">
        <span class="notes-book-row-top">
          <span class="notes-book-kind-pill">${_esc((book.kind || kind).toUpperCase())}</span>
          <span class="notes-book-row-title">${_esc(title)}</span>
        </span>
        <span class="notes-book-row-path">${_esc(book.path || '')}</span>
        <span class="notes-book-row-meta">${_esc(loc)} · ${_esc(_formatVaultSize(book.size))}</span>
      </span>
      <button type="button" class="notes-book-fav${book.favorite ? ' is-fav' : ''}" data-path="${_attrEsc(book.path || '')}" data-fav="${book.favorite ? '1' : '0'}" title="${book.favorite ? 'Unfavourite' : 'Favourite'}" aria-label="${book.favorite ? 'Unfavourite' : 'Favourite'}" aria-pressed="${book.favorite ? 'true' : 'false'}">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="${book.favorite ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
      </button>
      <button type="button" class="notes-book-title-edit" data-path="${_attrEsc(book.path || '')}" data-title="${_attrEsc(title)}" title="Rename book" aria-label="Rename book">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
      </button>
    </div>`;
  }
  html += '</div>';
  scroll.innerHTML = html;
  // Lazy-load EPUB covers; on 404/error fall back to the generic cover tile.
  // src is set in JS (after attaching the error handler) so the fallback is reliable.
  scroll.querySelectorAll('.notes-book-cover-img').forEach(img => {
    img.addEventListener('error', () => img.closest('.notes-book-cover')?.classList.add('no-cover'));
    img.addEventListener('load', () => img.closest('.notes-book-cover')?.classList.add('has-cover'));
    img.src = `${API_BASE}/api/books/cover?path=${encodeURIComponent(img.dataset.cover || '')}`;
  });
  scroll.querySelectorAll('.notes-book-title-edit').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        await _renameBook(btn.dataset.path || '', btn.dataset.title || '');
      } catch (err) {
        uiModule.showError?.(err?.message || 'Failed to rename book');
      }
    });
  });
  scroll.querySelectorAll('.notes-book-fav').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const path = btn.dataset.path || '';
      const next = btn.dataset.fav !== '1';
      btn.classList.add('loading');
      try {
        const res = await fetch(`${API_BASE}/api/books/favorite`, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, favorite: next }),
        });
        if (!res.ok) throw new Error();
        await _fetchBooks();
        _render();  // re-render so favourites re-sort to the top
      } catch (_) {
        btn.classList.remove('loading');
        uiModule.showError?.('Could not update favourite');
      }
    });
  });
  scroll.querySelectorAll('.notes-book-file').forEach(btn => {
    const open = async () => {
      const path = btn.dataset.path || '';
      try {
        btn.classList.add('loading');
        await _openBook(path);
        _render();
        if (_bookOpenBook?.kind !== 'pdf' || _bookPdfViewMode === 'text') {
          requestAnimationFrame(() => _setBookChapter(_bookOpenBook?.progress?.chapter_index || 0, true));
        }
      } catch (e) {
        uiModule.showError?.(e?.message || 'Failed to open book');
      } finally {
        btn.classList.remove('loading');
      }
    };
    btn.addEventListener('click', (e) => {
      if (e.target.closest('button, input, select, textarea, a')) return;
      open();
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target.closest('button, input, select, textarea, a')) return;
      e.preventDefault();
      open();
    });
  });
}

// Top-level render: library list vs. reader (mirrors knowledge.js list/detail).
function _render() {
  const modal = _modal();
  if (!modal) return;
  const listView = modal.querySelector('.books-list-view');
  const readerView = _readerScroller();
  if (!listView || !readerView) return;
  if (_bookOpenBook) {
    listView.style.display = 'none';
    readerView.style.display = '';
    _renderBookReader(readerView, '');
  } else {
    _destroyBookPdfReader();
    bookToolsModule.cleanup();
    readerView.style.display = 'none';
    readerView.innerHTML = '';
    listView.style.display = '';
    _renderListInto();
  }
}

// ── toolbar wiring (search + upload) ─────────────────────────────────────────
function _wireToolbar() {
  const modal = _modal();
  if (!modal) return;
  const search = modal.querySelector('.books-search');
  if (search && !search.dataset._wired) {
    search.dataset._wired = '1';
    search.addEventListener('input', () => {
      _searchQuery = search.value.trim().toLowerCase();
      _scheduleBooksSearch();
    });
  }
  const fileInput = modal.querySelector('.books-file-input');
  const uploadBtn = modal.querySelector('.books-upload-btn');
  if (fileInput) fileInput.accept = '.epub,.pdf,application/epub+zip,application/pdf';
  if (uploadBtn && !uploadBtn.dataset._wired) {
    uploadBtn.dataset._wired = '1';
    uploadBtn.addEventListener('click', () => fileInput?.click());
  }
  if (fileInput && !fileInput.dataset._wired) {
    fileInput.dataset._wired = '1';
    fileInput.addEventListener('change', async () => {
      try {
        for (const file of Array.from(fileInput.files || [])) {
          await _uploadBookFile(file);
        }
        uiModule.showToast?.('Uploaded to books');
      } catch (e) {
        _bookUploadState = {
          active: false,
          percent: 0,
          label: 'Upload failed',
          detail: e?.message || 'Upload failed',
          error: true,
        };
        _renderUploadProgress();
        uiModule.showError?.(e?.message || 'Upload failed');
      } finally {
        fileInput.value = '';
      }
    });
  }
}

// ── modal lifecycle (mirrors knowledge.js / health.js) ───────────────────────
async function _refresh() {
  await _fetchBooks();
  if (!_bookOpenBook) _renderListInto();
}

export async function openBooksPanel(initialPath = '') {
  _pendingOpenPath = initialPath || null;
  // Minimized → restore in place (consistent with knowledge/health/etc.).
  if (Modals.isRegistered('books-modal') && Modals.isMinimized('books-modal')) {
    Modals.restore('books-modal');
    if (_pendingOpenPath) { _openAndShow(_pendingOpenPath); _pendingOpenPath = null; }
    return;
  }
  if (_open) {
    if (_pendingOpenPath) { _openAndShow(_pendingOpenPath); _pendingOpenPath = null; }
    return;
  }
  _open = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'books-modal';
  modal.innerHTML = `
    <div class="modal-content books-modal-content">
      <div class="modal-header">
        <h4>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
          </svg>Books
        </h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="books-close">✖</button>
      </div>
      <div class="modal-body books-modal-body">
        <div class="books-list-view">
          <div class="books-toolbar">
            <input class="books-search memory-search-input" type="text" placeholder="Search books…" autocomplete="off" />
            <button class="notes-select-trigger books-upload-btn" type="button" title="Upload EPUB or PDF">Upload EPUB/PDF</button>
            <input type="file" class="books-file-input" multiple style="display:none" />
          </div>
          <div class="books-upload-slot"></div>
          <div class="books-list-scroll"></div>
        </div>
        <div class="books-reader-view" style="display:none"></div>
      </div>
    </div>`;
  document.body.appendChild(modal);

  // Register with the Modals manager so Books gets the same minimize→dock,
  // restore and rail/sidebar badge behavior as every other tool window.
  Modals.register('books-modal', {
    railBtnId: 'rail-books',
    sidebarBtnId: 'tool-books-btn',
    closeFn: () => _doClose(),
    restoreFn: () => {},
    label: 'Books',
    icon: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z',
  });
  try { Modals.injectMinimizeButton(modal, 'books-modal'); } catch (_) {}

  document.getElementById('books-close').addEventListener('click', closeBooks);
  makeToolModalDraggable(modal);
  modal.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal?.()) return;
    if (e.target === modal) closeBooks();
  });
  _escHandler = (e) => { if (e.key === 'Escape' && _open) closeBooks(); };
  document.addEventListener('keydown', _escHandler);

  _wireToolbar();
  _booksLoading = true;
  _render();                  // instant skeleton frame
  await _refresh();           // fetch the library
  if (_pendingOpenPath) {
    await _openAndShow(_pendingOpenPath);
    _pendingOpenPath = null;
  }
}

// Open a specific book (by knowledge path) and surface its reader — used for the
// citation deep-link (#book-…) path and re-entry while already open.
async function _openAndShow(path) {
  _booksLoading = true;
  _bookOpenBook = null;
  _render();
  try {
    await _openBook(path);
    await _fetchBooks();
  } catch (e) {
    uiModule.showError?.(e?.message || 'Failed to open book');
  } finally {
    _booksLoading = false;
  }
  _render();
  if (_bookOpenBook && (_bookOpenBook.kind !== 'pdf' || _bookPdfViewMode === 'text')) {
    requestAnimationFrame(() => _setBookChapter(_bookOpenBook.progress?.chapter_index || 0, true));
  }
}

// Actual teardown — invoked by Modals.close() via the registered closeFn.
function _doClose() {
  _open = false;
  _bookOpenBook = null;
  _destroyBookPdfReader();
  bookToolsModule.cleanup();
  if (_bookKeyHandler) { document.removeEventListener('keydown', _bookKeyHandler); _bookKeyHandler = null; }
  if (_bookSaveTimer) { clearTimeout(_bookSaveTimer); _bookSaveTimer = null; }
  const modal = document.getElementById('books-modal');
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

export function closeBooks() {
  if (!_open && !Modals.isMinimized('books-modal')) return;
  if (Modals.isRegistered('books-modal')) Modals.close('books-modal');
  else _doClose();
}

export function isBooksOpen() {
  if (Modals.isMinimized('books-modal')) return false;
  return _open;
}

export function toggleBooks() {
  if (isBooksOpen()) closeBooks();
  else openBooksPanel();
}

// Back-compat alias: older callers / cached pages used openBooks().
export function openBooks(initialPath = '') { return openBooksPanel(initialPath); }

const booksModule = { openBooksPanel, openBooks, closeBooks, isBooksOpen, toggleBooks };
window.booksModule = booksModule;
export default booksModule;
