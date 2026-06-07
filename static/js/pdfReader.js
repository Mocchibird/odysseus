// pdfReader.js — lightweight continuous-scroll PDF renderer built on the
// vendored PDF.js (static/lib/pdf.min.mjs). Renders the *actual* PDF (vector
// pages on canvas) rather than the browser's native viewer, so it looks and
// scrolls consistently on desktop and mobile and lives inside the app chrome.
//
// Usage:
//   const reader = await createPdfReader(container, {
//     url, initialPage, onPageChange(p,total), onProgress(percent),
//   });
//   reader.goToPage(3); reader.destroy();
//
// Design notes:
//  • Pages get placeholder boxes sized from each page's own aspect ratio so the
//    scrollbar is accurate before any pixels are rasterized.
//  • Only pages near the viewport are rasterized (IntersectionObserver); pages
//    that scroll far away release their canvas, so a 600-page PDF stays light.
//  • Rasterization is done at devicePixelRatio (capped) for crispness, and
//    re-done on width changes (ResizeObserver, debounced).

let _pdfjsPromise = null;

function _loadPdfjs() {
  if (!_pdfjsPromise) {
    _pdfjsPromise = import('/static/lib/pdf.min.mjs').then((mod) => {
      const pdfjs = mod.default && mod.default.getDocument ? mod.default : mod;
      try {
        pdfjs.GlobalWorkerOptions.workerSrc = '/static/lib/pdf.worker.min.mjs';
      } catch (_) {}
      return pdfjs;
    });
  }
  return _pdfjsPromise;
}

// Cap so a high-DPR phone doesn't allocate a monster canvas for a large page.
const MAX_CANVAS_PX = 2400;

export async function createPdfReader(container, opts = {}) {
  const {
    url,
    data,
    initialPage = 1,
    onPageChange = null,
    onProgress = null,
    gap = 14,
  } = opts;

  const pdfjs = await _loadPdfjs();
  const loadingTask = data
    ? pdfjs.getDocument({ data })
    : pdfjs.getDocument({ url, disableAutoFetch: true, disableStream: false });
  const doc = await loadingTask.promise;
  const numPages = doc.numPages;

  let destroyed = false;
  const pageProxies = new Array(numPages + 1).fill(null); // 1-indexed
  const pageEls = []; // 0-indexed -> wrapper element
  const renderTasks = new Map(); // pageNum -> RenderTask
  const rendered = new Set(); // pageNums currently rasterized
  let currentPage = Math.min(Math.max(1, initialPage | 0), numPages);

  container.classList.add('pdfjs-reader');
  container.innerHTML = '';
  container.style.setProperty('--pdfjs-gap', `${gap}px`);

  // Build placeholder boxes. Default aspect from page 1; corrected per-page as
  // each page's real viewport is fetched on demand.
  let defaultRatio = 1.414; // A4-ish fallback
  try {
    const p1 = await doc.getPage(1);
    pageProxies[1] = p1;
    const vp = p1.getViewport({ scale: 1 });
    defaultRatio = vp.height / vp.width;
  } catch (_) {}
  if (destroyed) { try { doc.destroy(); } catch (_) {} return _noopReader(); }

  for (let i = 1; i <= numPages; i++) {
    const wrap = document.createElement('div');
    wrap.className = 'pdfjs-page';
    wrap.dataset.page = String(i);
    wrap.style.aspectRatio = `1 / ${defaultRatio}`;
    const num = document.createElement('div');
    num.className = 'pdfjs-page-num';
    num.textContent = String(i);
    wrap.appendChild(num);
    container.appendChild(wrap);
    pageEls.push(wrap);
  }

  function _contentWidth() {
    const cs = getComputedStyle(container);
    const pad = parseFloat(cs.paddingLeft || '0') + parseFloat(cs.paddingRight || '0');
    return Math.max(120, container.clientWidth - pad);
  }

  async function _getPage(num) {
    if (pageProxies[num]) return pageProxies[num];
    const p = await doc.getPage(num);
    pageProxies[num] = p;
    return p;
  }

  function _pageError(wrap, num, msg) {
    if (!wrap || destroyed) return;
    let el = wrap.querySelector('.pdfjs-page-err');
    if (!el) { el = document.createElement('div'); el.className = 'pdfjs-page-err'; wrap.appendChild(el); }
    el.textContent = `Page ${num}: ${msg}`;
  }

  async function _renderPage(num) {
    if (destroyed || rendered.has(num) || renderTasks.has(num)) return;
    const wrap = pageEls[num - 1];
    if (!wrap) return;
    let page;
    try { page = await _getPage(num); }
    catch (e) { _pageError(wrap, num, 'failed to load — ' + (e && e.message || e)); return; }
    if (destroyed) return;
    try {
      const base = page.getViewport({ scale: 1 });
      // Correct the placeholder aspect ratio to this page's real ratio.
      wrap.style.aspectRatio = `1 / ${base.height / base.width}`;
      // Guard against a 0-width container (pre-layout): fall back to a sane width.
      const cssW = Math.max(200, _contentWidth() || (wrap.clientWidth || 0) || 600);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      let scale = (cssW / base.width) * dpr;
      if (base.width * scale > MAX_CANVAS_PX) scale = MAX_CANVAS_PX / base.width;
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement('canvas');
      canvas.className = 'pdfjs-canvas';
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      canvas.style.width = '100%';
      canvas.style.height = 'auto';
      const ctx = canvas.getContext('2d', { alpha: false });
      if (!ctx) { _pageError(wrap, num, 'no 2D canvas context'); return; }
      const task = page.render({ canvasContext: ctx, viewport });
      renderTasks.set(num, task);
      await task.promise;
      if (destroyed) return;
      wrap.querySelectorAll('.pdfjs-canvas, .pdfjs-page-err').forEach((n) => n.remove());
      wrap.appendChild(canvas);
      rendered.add(num);
    } catch (e) {
      // RenderingCancelledException when scrolled away mid-render is expected.
      if (e && (e.name === 'RenderingCancelledException')) return;
      _pageError(wrap, num, (e && e.message) || String(e));
    } finally {
      renderTasks.delete(num);
    }
  }

  function _unrenderPage(num) {
    const task = renderTasks.get(num);
    if (task) { try { task.cancel(); } catch (_) {} renderTasks.delete(num); }
    if (!rendered.has(num)) return;
    const wrap = pageEls[num - 1];
    const canvas = wrap?.querySelector('.pdfjs-canvas');
    if (canvas) canvas.remove();
    rendered.delete(num);
  }

  // Render pages within ~one viewport of the visible area; release the rest.
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const num = Number(e.target.dataset.page || 0);
      if (!num) continue;
      if (e.isIntersecting) _renderPage(num);
      else if (!rendered.has(num)) { /* not yet rendered, nothing to free */ }
    }
  }, { root: container, rootMargin: '150% 0px 150% 0px', threshold: 0.01 });
  pageEls.forEach((el) => io.observe(el));

  // Free canvases that drift far from the viewport to bound memory.
  function _gcFarPages() {
    const top = container.scrollTop;
    const vh = container.clientHeight;
    for (const num of Array.from(rendered)) {
      const wrap = pageEls[num - 1];
      if (!wrap) continue;
      const wTop = wrap.offsetTop;
      const wBot = wTop + wrap.offsetHeight;
      if (wBot < top - vh * 2 || wTop > top + vh * 3) _unrenderPage(num);
    }
  }

  function _activePage() {
    const mid = container.scrollTop + container.clientHeight * 0.35;
    let best = currentPage;
    let bestDist = Infinity;
    for (let i = 0; i < pageEls.length; i++) {
      const el = pageEls[i];
      const top = el.offsetTop;
      const bot = top + el.offsetHeight;
      const d = (mid >= top && mid <= bot) ? 0 : Math.min(Math.abs(top - mid), Math.abs(bot - mid));
      if (d < bestDist) { bestDist = d; best = i + 1; }
      if (d === 0) break;
    }
    return best;
  }

  let _raf = 0;
  function _onScroll() {
    if (_raf) return;
    _raf = requestAnimationFrame(() => {
      _raf = 0;
      if (destroyed) return;
      _gcFarPages();
      const max = Math.max(1, container.scrollHeight - container.clientHeight);
      const pct = Math.max(0, Math.min(100, (container.scrollTop / max) * 100));
      if (onProgress) onProgress(pct);
      const ap = _activePage();
      if (ap !== currentPage) {
        currentPage = ap;
        if (onPageChange) onPageChange(ap, numPages);
      }
    });
  }
  container.addEventListener('scroll', _onScroll, { passive: true });

  // Re-rasterize visible pages when the panel width changes.
  let _resizeTimer = 0;
  const ro = new ResizeObserver(() => {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => {
      if (destroyed) return;
      const toRedraw = Array.from(rendered);
      toRedraw.forEach(_unrenderPage);
      toRedraw.forEach((n) => _renderPage(n));
    }, 180);
  });
  ro.observe(container);

  function goToPage(num, { behavior = 'auto' } = {}) {
    const n = Math.min(Math.max(1, num | 0), numPages);
    const el = pageEls[n - 1];
    if (!el) return;
    currentPage = n;
    _renderPage(n);
    el.scrollIntoView({ behavior, block: 'start' });
  }

  function destroy() {
    destroyed = true;
    container.removeEventListener('scroll', _onScroll);
    try { io.disconnect(); } catch (_) {}
    try { ro.disconnect(); } catch (_) {}
    renderTasks.forEach((t) => { try { t.cancel(); } catch (_) {} });
    renderTasks.clear();
    rendered.clear();
    try { doc.destroy(); } catch (_) {}
    try { loadingTask.destroy?.(); } catch (_) {}
    container.innerHTML = '';
    container.classList.remove('pdfjs-reader');
  }

  // Initial position + first paint.
  if (currentPage > 1) {
    requestAnimationFrame(() => goToPage(currentPage));
  } else {
    _renderPage(1);
    _renderPage(2);
  }
  if (onPageChange) onPageChange(currentPage, numPages);

  return { goToPage, destroy, numPages, get currentPage() { return currentPage; } };
}

function _noopReader() {
  return { goToPage() {}, destroy() {}, numPages: 0, currentPage: 1 };
}
