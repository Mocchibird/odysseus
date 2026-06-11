/**
 * UI language layer — translates the rendered interface to the user's
 * per-user `language` pref (Settings → Account → Language; en/ko/de).
 *
 * Gettext-style: dictionary keys are the EXACT English strings the UI
 * renders (static/js/i18n/<lang>.js). An initial pass translates the parsed
 * DOM; a MutationObserver translates everything modules render afterwards —
 * no per-module wiring needed. Exact-match only, so anything not in the
 * dictionary (chat content, user data, parameterized strings) is left
 * untouched, and re-running is idempotent (translated text no longer
 * matches a key).
 *
 * Adding a language: create static/js/i18n/<code>.js (default-export the
 * dictionary), add the code to SUPPORTED below + the backend
 * src/i18n.py SUPPORTED_LANGUAGES + the Settings <select> option.
 */

const LS_KEY = 'odysseus-ui-lang';
const SUPPORTED = ['en', 'ko', 'de'];

const ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
// Never translate inside these: chat messages + editors are user content;
// script/style/code are not UI text.
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'CODE', 'PRE', 'TEXTAREA', 'NOSCRIPT']);
const SKIP_SELECTOR = '#chat-history, .doc-editor-pane, [contenteditable="true"]';

let _dict = null; // active dictionary (null = English / disabled)

function _normLang(value) {
  const code = String(value || '').trim().toLowerCase();
  return SUPPORTED.includes(code) ? code : 'en';
}

export function uiLang() {
  return _normLang(localStorage.getItem(LS_KEY));
}

/** Translate one exact UI string (modules may call this for dynamic text). */
export function t(text) {
  if (!_dict) return text;
  return _dict[text] || text;
}

function _translateTextNode(node) {
  const raw = node.nodeValue;
  if (!raw) return;
  const trimmed = raw.trim();
  if (!trimmed) return;
  const replacement = _dict[trimmed];
  if (replacement === undefined) return;
  // Preserve surrounding whitespace (indentation inside markup). Never write
  // an unchanged value — setting nodeValue fires the observer even when the
  // text is identical, which would loop the microtask queue forever.
  const next = raw.replace(trimmed, replacement);
  if (next !== raw) node.nodeValue = next;
}

// Text CONTENT inside these is user data / code — never touched. Attributes
// (placeholder, title, aria-label) are still UI chrome even on a TEXTAREA,
// so they get the laxer check.
function _skipText(el) {
  if (!el) return false;
  if (SKIP_TAGS.has(el.tagName)) return true;
  return !!el.closest(SKIP_SELECTOR);
}

function _skipAttrs(el) {
  return !!(el && el.closest && el.closest(SKIP_SELECTOR));
}

function _translateAttrs(el) {
  for (const attr of ATTRS) {
    const val = el.getAttribute && el.getAttribute(attr);
    if (!val) continue;
    const replacement = _dict[val.trim()];
    // Same guard as text nodes: identical setAttribute still fires the
    // observer -> infinite loop. Only write real changes.
    if (replacement !== undefined && replacement !== val) el.setAttribute(attr, replacement);
  }
}

function _translateTree(root) {
  if (root.nodeType === Node.TEXT_NODE) {
    if (!_skipText(root.parentElement)) _translateTextNode(root);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE || _skipAttrs(root)) return;
  _translateAttrs(root);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode(node) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        return _skipText(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const attrEls = root.querySelectorAll(
    ATTRS.map((a) => `[${CSS.escape(a)}]`).join(','),
  );
  attrEls.forEach((el) => { if (!_skipAttrs(el)) _translateAttrs(el); });
  let node;
  while ((node = walker.nextNode())) _translateTextNode(node);
}

function _observe() {
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'childList') {
        m.addedNodes.forEach((n) => _translateTree(n));
      } else if (m.type === 'characterData') {
        const el = m.target.parentElement;
        if (!_skipText(el)) _translateTextNode(m.target);
      } else if (m.type === 'attributes') {
        const el = m.target;
        if (el.nodeType === Node.ELEMENT_NODE && !_skipAttrs(el)) _translateAttrs(el);
      }
    }
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ATTRS,
  });
}

// Reconcile the fast localStorage mirror with the server pref (cross-device).
// One guarded reload when they disagree, so the right dictionary loads.
async function _syncFromServer() {
  try {
    const res = await fetch('/api/prefs/language', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    const server = _normLang(data && data.value);
    if (server !== uiLang()) {
      localStorage.setItem(LS_KEY, server);
      if (!sessionStorage.getItem('odysseus-ui-lang-reloaded')) {
        sessionStorage.setItem('odysseus-ui-lang-reloaded', '1');
        window.location.reload();
      }
    } else {
      sessionStorage.removeItem('odysseus-ui-lang-reloaded');
    }
  } catch (_) { /* offline boot — mirror wins */ }
}

const _lang = uiLang();
if (_lang !== 'en') {
  try {
    // ?v versions the dictionaries like every other module import — bump it
    // (and this file's own ?v in app.js) when a dictionary changes.
    const mod = await import(`./i18n/${_lang}.js?v=402`);
    _dict = mod.default || null;
  } catch (e) {
    console.warn('i18n: failed to load dictionary', _lang, e);
    _dict = null;
  }
  if (_dict) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => _translateTree(document.body), { once: true });
    } else {
      _translateTree(document.body);
    }
    _observe();
  }
}
_syncFromServer();

const i18nModule = { t, uiLang };
export default i18nModule;
