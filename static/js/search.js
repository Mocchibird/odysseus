// static/js/search.js

/**
 * Search settings management — reads active provider from admin settings.
 */

let API_BASE = '';
let _provider = 'searxng';
let _loaded = false;
let _provToken = 0;

export function init(apiBase) {
  API_BASE = apiBase;
  // Fetch provider on init so it's ready when chat needs it
  _fetchProvider();
}

async function _fetchProvider() {
  // Token guard: init() + a refresh() after an admin save can be in flight at
  // once; without this a slow earlier response could overwrite the newer
  // provider. Latest call wins.
  const tok = ++_provToken;
  try {
    const res = await fetch((API_BASE || '') + '/api/auth/settings', { credentials: 'same-origin' });
    if (!res.ok) return;
    const s = await res.json();
    if (tok !== _provToken) return;
    _provider = s.search_provider || 'searxng';
    _loaded = true;
  } catch (e) { /* keep default */ }
}

export function getCurrentProvider() {
  return _provider;
}

const _labels = {
  searxng: 'SearXNG', brave: 'Brave', duckduckgo: 'DuckDuckGo',
  google_pse: 'Google', tavily: 'Tavily', serper: 'Serper',
  disabled: 'search (disabled)',
};

export function getProviderLabel() {
  return _labels[_provider] || _provider;
}

/** Re-fetch after admin saves new settings */
export function refresh() {
  _fetchProvider();
}

const searchModule = {
  init,
  getCurrentProvider,
  getProviderLabel,
  refresh
};

export default searchModule;
