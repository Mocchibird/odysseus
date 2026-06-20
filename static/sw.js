// static/sw.js — Odysseus PWA Service Worker
// Strategy:
//   - HTML (navigation): network-first, cache fallback for offline. So a
//     rebuild/redeploy shows on the very next reload (no "stale shell until the
//     second open"). The cached shell is only used when the network is down.
//   - JS/CSS (/static/*.js|.css): network-first, cache fallback for offline.
//     (So code/style edits show up on a normal reload, no manual cache clear.)
//   - Other static assets (images/fonts/libs): cache-first with bg refresh.
//   - API / non-GET: never cached.
// Bump CACHE_NAME whenever the precache list or SW logic changes.
const CACHE_NAME = 'odysseus-v477';
// Separate, long-lived cache for book content (PDF bytes / EPUB chapters) so
// books you've opened stay readable offline AND survive app-shell version bumps
// (the activate cleanup below deliberately keeps this one).
const BOOKS_CACHE = 'odysseus-books-v1';

// Core shell precached on install so repeat opens are instant without any
// network wait. Keep this list in sync with the <script type="module"> tags
// and <link rel="stylesheet"> in index.html.
const PRECACHE = [
  '/',
  '/static/manifest.json',
  '/static/style.css?v=454',
  '/static/fork.css?v=466',
  '/static/app.js?v=466',
  '/static/js/storage.js',
  '/static/js/i18n.js',
  '/static/js/i18n/ko.js',
  '/static/js/i18n/de.js',
  '/static/js/ui.js',
  '/static/js/markdown.js',
  '/static/js/dragSort.js',
  '/static/js/sessions.js',
  '/static/js/memory.js',
  '/static/js/skills.js',
  '/static/js/tourHints.js',
  '/static/js/fileHandler.js',
  '/static/js/voiceRecorder.js',
  '/static/js/models.js',
  '/static/js/rag.js',
  '/static/js/presets.js',
  '/static/js/search.js',
  '/static/js/spinner.js',
  '/static/js/tts-ai.js',
  '/static/js/document.js?v=462',
  '/static/js/gallery.js?v=445',
  '/static/js/video360.js',
  '/static/js/chatRenderer.js',
  '/static/js/codeRunner.js',
  '/static/js/chatStream.js',
  '/static/js/chat.js?v=461',
  '/static/js/composerArrowUpRecall.js',
  '/static/js/cookbook.js',
  '/static/js/search-chat.js',
  '/static/js/compare/index.js',
  '/static/js/theme.js?v=397',
  '/static/js/censor.js',
  '/static/js/settings.js?v=449',
  '/static/js/admin.js?v=450',
  '/static/js/init.js',
  '/static/js/slashCommands.js',
  '/static/js/emailInbox.js',
  '/static/js/emailLibrary/utils.js',
  '/static/js/emailLibrary/signatureFold.js',
  '/static/js/emailLibrary/state.js',
  '/static/js/notes.js?v=460',
  '/static/js/bookTools.js?v=395',
  '/static/js/health.js?v=398',
  '/static/js/pings.js?v=396',
  '/static/js/today.js?v=422',
  '/static/js/modalFullscreen.js?v=370',
  '/static/js/pdfReader.js?v=383',
  '/static/js/tasks.js',
  '/static/js/calendar.js',
  '/static/js/calendar/utils.js',
  '/static/js/group.js',
  '/static/js/keyboard-shortcuts.js',
  '/static/js/sidebar-layout.js',
  '/static/js/section-management.js',
  '/static/lib/highlight.min.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      // addAll is atomic — if any item fails, none are cached. Use individual
      // puts so a single 404 can't block the whole install.
      Promise.all(
        PRECACHE.map(url =>
          fetch(url, { cache: 'reload' })
            .then(res => res.ok ? cache.put(url, res) : null)
            .catch(() => null)
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      // Keep the current shell cache AND the long-lived books cache; drop the rest.
      Promise.all(keys.filter(k => k !== CACHE_NAME && k !== BOOKS_CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Book content is immutable (a book's bytes/chapters don't change), so cache it
// for offline reading. Opening a book online populates this; later it's served
// from cache even with no connection. Kept in BOOKS_CACHE (survives shell bumps).
const BOOK_CONTENT = /^\/api\/(books\/(file|chapter|open)|iris-vault\/epub)$/;

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // Offline reading: stale-while-revalidate book content (returns instantly from
  // cache when present, refreshes in the background; works fully offline).
  if (e.request.method === 'GET' && BOOK_CONTENT.test(url.pathname)) {
    e.respondWith(
      caches.open(BOOKS_CACHE).then(async cache => {
        const cached = await cache.match(e.request);
        const network = fetch(e.request).then(res => {
          if (res && res.ok) cache.put(e.request, res.clone());
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Never touch other API calls or non-GET.
  if (url.pathname.startsWith('/api/') || e.request.method !== 'GET') return;

  // HTML navigation: network-FIRST for the app shell — but ONLY for the SPA
  // root. Always try the network so a rebuild/redeploy shows on the very next
  // reload; fall back to the cached shell only when offline. (Other navigations,
  // e.g. a deep-linked /static/*.html page, fall through to the handlers below;
  // otherwise every navigation was served the app index, replacing the page the
  // user actually asked for.)
  if (e.request.mode === 'navigate' && url.pathname === '/') {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put('/', copy));
        }
        return res;
      }).catch(() => caches.open(CACHE_NAME).then(cache => cache.match('/')))
    );
    return;
  }

  // JS/CSS: network-first — always try the network so code/style edits show up
  // on a normal reload; fall back to cache only when offline.
  if (url.pathname.startsWith('/static/') && /\.(js|css)(\?|$)/.test(url.pathname + url.search)) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Other static assets (images, fonts, libs): cache-first with background refresh.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        const fetching = fetch(e.request).then(res => {
          if (res && res.ok) cache.put(e.request, res.clone());
          return res;
        }).catch(() => cached);
        return cached || fetching;
      })
    );
    return;
  }
});
