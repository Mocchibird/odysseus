// static/js/writer/standalone.js
//
// FORK-ONLY. Boots the writing surface on its own page (static/writer.html),
// without the SPA around it.
//
// Everything here is the small amount of work index.html normally does for the
// app: paint the saved theme before first frame, register the service worker,
// then hand over. It must stay small — the point of the standalone page is that
// almost nothing runs before the editor appears.

const BOOT = () => document.getElementById('writer-boot');

/**
 * Apply the saved theme's colours straight from localStorage.
 *
 * index.html does this in an inline <script> so the first paint is already
 * themed. A static page gets no CSP nonce, so an inline script would be blocked
 * (see the comment in writer.html) — this module is the earliest we can run, and
 * it runs before the editor mounts, which is early enough to avoid a flash.
 *
 * Deliberately NOT importing theme.js: it is a large module that expects the
 * app's settings DOM. These few variables are all a bare editor needs.
 */
function _paintTheme() {
  try {
    const saved = JSON.parse(localStorage.getItem('odysseus-theme') || 'null');
    const c = saved && saved.colors;
    if (!c) return;
    const s = document.documentElement.style;
    for (const key of ['bg', 'fg', 'panel', 'border', 'red', 'green', 'yellow', 'blue', 'purple', 'cyan']) {
      if (c[key]) s.setProperty(`--${key}`, c[key]);
    }
    if (c.red) s.setProperty('--brand-color', c.red);
    if (c.bg) {
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', c.bg);
    }
  } catch (_) { /* corrupt or unavailable storage — the CSS defaults stand */ }
}

/**
 * Register the app's service worker.
 *
 * NOTE ON SCOPE: the script lives at /static/sw.js and the response carries no
 * Service-Worker-Allowed header, so its scope is /static/ — which covers this
 * page and every asset it loads. That is exactly what makes this page work
 * offline. Do not "fix" this by moving the registration; the offline behaviour
 * here depends on the page being inside the worker's scope.
 */
function _registerWorker() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/static/sw.js')
    .catch((e) => console.debug('[writer] SW register failed', e));
}

function _fail(message) {
  const boot = BOOT();
  if (!boot) return;
  boot.hidden = false;
  boot.textContent = message;
}

async function main() {
  _paintTheme();
  _registerWorker();

  let writer;
  try {
    // Relative, un-versioned: the module graph resolves as siblings, the same way
    // it does inside the SPA, so there is only ONE copy of the writer's code.
    writer = (await import('./writer.js')).default;
  } catch (err) {
    console.error('[writer] standalone boot failed', err);
    _fail('Could not load the writer. Reload while connected to fetch it once.');
    return;
  }

  // Own routing, same as inside the app: #writer=<id> opens a specific document,
  // bare #writer (or nothing) resumes the last one.
  const hash = String(location.hash || '');
  const match = /^#writer(?:=(.+))?$/.exec(hash);
  const docId = match && match[1] ? decodeURIComponent(match[1]) : null;

  try {
    await writer.open(docId);
  } catch (err) {
    console.error('[writer] open failed', err);
    _fail('The writer could not open a document. Check the console for details.');
    return;
  }
  window.addEventListener('hashchange', () => {
    const m = /^#writer(?:=(.+))?$/.exec(String(location.hash || ''));
    writer.open(m && m[1] ? decodeURIComponent(m[1]) : null);
  });

  const boot = BOOT();
  if (boot) boot.hidden = true;

  // On this page there is no app behind the surface to close back to, so "Close"
  // becomes the way back to Odysseus proper. Flush first: navigating away from a
  // standalone page is the one exit that has no other save opportunity.
  const closeBtn = document.getElementById('writer-close');
  if (closeBtn) {
    closeBtn.title = 'Back to Odysseus';
    closeBtn.textContent = 'Odysseus';
    const back = closeBtn.cloneNode(true);         // drop writer.js's close handler
    closeBtn.replaceWith(back);
    back.addEventListener('click', async () => {
      try { await writer.store.flush(); } catch (_) { /* go anyway; it is in IndexedDB */ }
      location.href = '/';
    });
  }
  document.body.classList.add('writer-standalone');
}

main();
