/**
 * fork-ui.js — fork-only UI, injected at runtime into stable upstream anchors.
 *
 * WHY: we keep index.html / style.css as close to upstream as possible so each
 * upstream sync is a fast-forward, not a conflict slog. Fork-only panels that
 * used to be inlined into index.html (and got silently dropped by an upstream
 * merge — e.g. the API Tokens panel) live HERE instead and mount themselves
 * into anchors that already exist in upstream's markup.
 *
 * Contract for each injector:
 *   - idempotent (no-op if its element already exists),
 *   - degrades silently if its anchor is missing (upstream renamed/removed it),
 *   - only mounts MARKUP; the behavior/wiring stays in the owning module
 *     (admin.js owns the token logic — loadTokens(), the create button, etc.).
 *
 * Anchors must be upstream-owned, stable ids. If one disappears after a sync,
 * the panel just won't render — the fork-ui audit test flags that at merge time.
 */

// ── API Tokens (admin → Integrations tab) ────────────────────────────────────
// admin.js already has all the token logic; it only needs this container to
// exist before refreshAll()/loadTokens() runs (which happens on admin open,
// well after this module is imported by app.js at boot).
const _TOKENS_CARD_INNER = `
  <h2><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>API Tokens</h2>
  <div class="admin-toggle-sub" style="margin-bottom:8px">Bearer tokens for external integrations (scripts, Siri Shortcuts, Codex, headless agent runs). Token value shown ONCE on create — copy it then.</div>
  <div id="adm-tokenList" style="margin-bottom:8px;"></div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
    <input type="text" id="adm-tokenName" placeholder="Token name (e.g. agent-test)" class="settings-select" style="flex:1;min-width:160px;height:32px;box-sizing:border-box;">
    <input type="text" id="adm-tokenScopes" placeholder="scopes (comma-separated, blank = chat)" class="settings-select" style="flex:2;min-width:220px;height:32px;box-sizing:border-box;" title="Allowed: chat, cookbook:read, cookbook:launch, documents:read|write, todos:read|write, email:read|draft|send, calendar:read|write, memory:read|write">
    <button class="admin-btn-add" id="adm-tokenAddBtn" style="height:32px;box-sizing:border-box;">Create token</button>
  </div>
  <div id="adm-tokenMsg" style="font-size:11px;margin-top:6px;"></div>
  <div id="adm-tokenReveal" style="display:none;margin-top:8px;padding:8px 10px;background:color-mix(in srgb, var(--accent, var(--red)) 12%, transparent);border:1px solid color-mix(in srgb, var(--accent, var(--red)) 35%, transparent);border-radius:6px;">
    <div style="font-size:11px;font-weight:600;margin-bottom:4px;">Copy now — this is the only time you'll see it:</div>
    <code id="adm-tokenValue" style="font-family:'Berkeley Mono','SF Mono','Fira Code',monospace;font-size:11px;word-break:break-all;display:block;background:var(--bg);padding:6px 8px;border-radius:4px;margin-bottom:6px;user-select:all;"></code>
    <button class="admin-btn-sm" id="adm-tokenCopyBtn">Copy</button>
  </div>`;

function _injectApiTokensPanel() {
  if (document.getElementById('adm-tokenList')) return;          // already mounted
  const intgList = document.getElementById('unified-integrations-list');
  if (!intgList) return;                                          // anchor gone → skip
  const intgCard = intgList.closest('.admin-card');
  if (!intgCard) return;
  const card = document.createElement('div');
  card.className = 'admin-card admin-only';
  card.style.marginTop = '12px';
  card.dataset.forkUi = 'api-tokens';
  card.innerHTML = _TOKENS_CARD_INNER;
  intgCard.insertAdjacentElement('afterend', card);
}

// ── Endpoint "Type" selector (admin → Add Models form) ───────────────────────
// Fork-only: lets you mark an added endpoint as Image-generation / TTS / STT
// (model_type) rather than a chat LLM. admin.js reads #adm-epType when building
// the add-endpoint request (guarded — absent = defaults to llm). TTS/STT
// endpoints are kept out of the chat model picker server-side (model_type filter
// in _fetch_models) and surface in the AI-Defaults voice provider selects.
// Upstream's redesigned Add-Models form never had it, so we inject it next to
// the Add button in the upstream-stable #adm-epApiKey-row.
function _injectEndpointTypeSelect() {
  if (document.getElementById('adm-epType')) return;             // already mounted
  const row = document.getElementById('adm-epApiKey-row');
  if (!row) return;                                              // anchor gone → skip
  const addBtn = document.getElementById('adm-epAddBtn');
  const label = document.createElement('label');
  label.dataset.forkUi = 'ep-type';
  label.style.cssText = 'display:inline-flex;align-items:center;gap:4px;font-size:11px;opacity:0.7;flex-shrink:0;';
  label.innerHTML = 'Type:<select id="adm-epType" style="height:32px;padding:4px 6px;flex-shrink:0;box-sizing:border-box;"><option value="llm" selected>LLM</option><option value="image">Image</option><option value="tts">TTS</option><option value="stt">STT</option></select>';
  if (addBtn) row.insertBefore(label, addBtn);                  // sits before "Add"
  else row.appendChild(label);
}

// ── Rail tool launchers (icon rail) ──────────────────────────────────────────
// Fork-only tools today/books/health/habits/pings. The click wiring lives in
// app.js (_railToolMap delegates rail-X → tool-X-btn) and runs at boot, after
// this module is imported, so the buttons exist when it wires. We mount them at
// the upstream-stable anchors rail-documents (today, books) and rail-tasks
// (health, habits, pings), preserving the original DOM order.
const _RAIL = {
  'rail-today':  ['Today',  '<path d="M12 2v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="M20 12h2"/><path d="m17.66 6.34 1.41-1.41"/><path d="M22 18H2"/><path d="M16 18a4 4 0 0 0-8 0"/><path d="M2 12h2"/>'],
  'rail-health': ['Health', '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'],
  'rail-habits': ['Habits', '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'],
  'rail-pings':  ['Pings & Reminders', '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>'],
  'rail-writer': ['Writer', '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>'],
};
function _railBtn(id) {
  const [title, paths] = _RAIL[id];
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'icon-rail-btn'; b.id = id; b.title = title;
  b.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>';
  return b;
}
function _injectRailTools() {
  // After rail-documents → today, books (insert in reverse so DOM order is today, books).
  const docs = document.getElementById('rail-documents');
  if (docs && !document.getElementById('rail-today')) {
    docs.insertAdjacentElement('afterend', _railBtn('rail-today'));
  }
  if (docs && !document.getElementById('rail-writer')) {
    const b = _railBtn('rail-writer');
    // The writer is lazy: 488 KB of vendored Lexical only loads on first open.
    b.addEventListener('click', () => { location.hash = '#writer'; });
    docs.insertAdjacentElement('afterend', b);
  }
  // After rail-tasks → health, habits, pings (insert in reverse for that order).
  const tasks = document.getElementById('rail-tasks');
  if (tasks && !document.getElementById('rail-health')) {
    tasks.insertAdjacentElement('afterend', _railBtn('rail-pings'));
    tasks.insertAdjacentElement('afterend', _railBtn('rail-habits'));
    tasks.insertAdjacentElement('afterend', _railBtn('rail-health'));
  }
}

// ── Settings rows/cards (admin Settings modal) ───────────────────────────────
// Fork-only settings UI. The wiring stays in settings.js — it runs lazily inside
// settingsModule.open() → initAll() (which re-queries idempotently on every
// open), so mounting the markup once at import time is enough; the listeners
// attach on first open. All anchors are upstream-stable. (The reminder ntfy-topic
// enhancements are NOT here: they edit an existing upstream row in place, so they
// stay inlined as unavoidable divergence.)
function _afterAnchor(anchorId, html, closestSel) {
  const a = document.getElementById(anchorId);
  if (!a) return null;
  const ref = closestSel ? a.closest(closestSel) : a;
  if (!ref) return null;
  const tpl = document.createElement('template');
  tpl.innerHTML = html.trim();
  const node = tpl.content.firstElementChild;
  if (node) ref.insertAdjacentElement('afterend', node);
  return node;
}
function _injectSettingsRows() {
  // Persona dropdown — after the default Model row in the General card.
  if (!document.getElementById('set-defaultPersonaSelect')) {
    _afterAnchor('set-defaultModelSelect',
      '<div class="settings-row"><label class="settings-label">Persona</label><select id="set-defaultPersonaSelect" class="settings-select"><option value="">No persona</option></select></div>',
      '.settings-row');
  }
  // "Chat & Agent models for users" allow-list card — after the General card.
  if (!document.getElementById('set-chatAllowedModels')) {
    _afterAnchor('set-defaultChatMsg',
      '<div class="admin-card"><h2><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>Chat &amp; Agent models for users</h2>' +
      '<div class="admin-toggle-sub" style="margin-bottom:8px">Pick which models non-admin users may choose for chat &amp; agent. Leave all unchecked = no restriction (every enabled model shows). Models you enable only for image / research / email keep working for those roles — they just won\'t appear in users\' chat picker. You (admin) are never restricted.</div>' +
      '<div class="settings-col"><div id="set-chatAllowedModels" class="settings-allowlist"></div>' +
      '<div style="display:flex;align-items:center;gap:10px;margin-top:4px;"><button type="button" class="admin-btn-sm" id="set-chatAllowedSave">Save allowed models</button>' +
      '<span id="set-chatAllowedMsg" style="font-size:11px;color:color-mix(in srgb, var(--fg) 45%, transparent);"></span></div></div></div>',
      '.admin-card');
  }
  // Language card — after the Two-Factor Authentication card (Account panel).
  if (!document.getElementById('set-language')) {
    _afterAnchor('settings-2fa-card',
      '<div class="admin-card"><h2><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>Language</h2>' +
      '<div class="settings-row" style="align-items:center;"><div class="admin-toggle-sub" style="margin:0;flex:1;">Iris replies, reminders, and briefs use this language. New chats default to the matching Iris persona.</div>' +
      '<select id="set-language" class="settings-select" style="width:auto;flex-shrink:0;"><option value="en">English</option><option value="ko">한국어 (Korean)</option><option value="de">Deutsch (German)</option></select></div></div>');
  }
  // Quiet-hours toggle + range rows — after the reminder channel hint.
  if (!document.getElementById('set-quiet-hours-enabled')) {
    const toggle = _afterAnchor('set-reminder-channel-hint',
      '<div class="settings-row" style="margin-top:6px"><label class="settings-label" for="set-quiet-hours-enabled">Quiet hours</label>' +
      '<label style="display:flex;align-items:center;gap:8px;flex:1;cursor:pointer;"><input type="checkbox" id="set-quiet-hours-enabled" />' +
      '<span style="font-size:12px;opacity:0.7;">Don\'t push reminders overnight — they still appear in Pings</span></label></div>');
    if (toggle) {
      toggle.insertAdjacentHTML('afterend',
        '<div id="set-quiet-hours-row" class="settings-row" style="display:none"><label class="settings-label">From / to</label>' +
        '<div style="display:flex;gap:8px;align-items:center;flex:1;"><input id="set-quiet-hours-start" class="settings-select" type="time" style="flex:0 0 auto;width:auto;" />' +
        '<span style="opacity:0.6;">to</span><input id="set-quiet-hours-end" class="settings-select" type="time" style="flex:0 0 auto;width:auto;" /></div></div>');
    }
  }
}

// ── Hide admin-global settings from non-admins ───────────────────────────────
// Some settings cards are admin-GLOBAL — a normal user can't change them (the
// backend rejects their POST and scrubs the secrets), so showing them blank
// fields they can't edit is just confusing. Mark those cards .admin-only so the
// existing syncAdminVisibility() hides them for non-admins (admins still see
// everything). Keyed off STABLE child ids so it survives upstream line moves.
// PER-USER cards (Default Chat / Utility / Vision / Research / Image — all in
// src/settings.py _PER_USER_KEYS) are deliberately LEFT visible.
function _markAdminOnlySettings() {
  // AI-Defaults cards that are admin-global: voice config + email safety.
  ['set-ttsProviderSelect', 'set-sttProviderSelect', 'set-agentEmailConfirm',
   // The whole Search tab: web-search provider keys + deep-research limits.
   'set-searchProvider', 'set-researchMaxTokens'].forEach(function(id) {
    var node = document.getElementById(id);
    var card = node && node.closest('.admin-card');
    if (card) card.classList.add('admin-only');
  });
  // Hide the Search tab's nav button too (both its cards are admin-only).
  var searchTab = document.querySelector('[data-settings-tab="search"]');
  if (searchTab) searchTab.classList.add('admin-only');
}

// Registry of injectors — add fork panels here as they're moved out of index.html.
// FORK: the block writing surface. Loaded dynamically and imported with a bare
// specifier so every future change lives entirely under static/js/writer/ —
// no upstream file (index.html, app.js, sw.js) is touched again by this feature.
function _initWriter() {
  import('./writer/writer.js')
    .then((m) => m.init())
    .catch((e) => console.warn('[fork-ui] writer unavailable:', e));
}

// Sidebar entry. The rail button alone is not enough: the icon rail only exists
// while the sidebar is COLLAPSED, so with the sidebar expanded — how the app runs
// by default — the writer had no visible entry point at all.
function _injectWriterSidebarItem() {
  const anchor = document.getElementById('tool-library-btn');
  if (!anchor || document.getElementById('tool-writer-btn')) return;
  const item = document.createElement('div');
  item.className = 'list-item';
  item.id = 'tool-writer-btn';
  item.innerHTML =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    + ' style="flex-shrink:0;opacity:0.5;">'
    + '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>'
    + '<span class="grow">Writer</span>'
    + '<button type="button" class="list-item-plus-btn" id="writer-new-doc-btn" title="New document">'
    + '<svg class="list-item-plus-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/>'
    + '<line x1="5" y1="12" x2="19" y2="12"/></svg>'
    + '<span class="list-item-plus-label">document</span></button>';

  item.addEventListener('click', (ev) => {
    if (ev.target.closest('#writer-new-doc-btn')) return;   // handled below
    location.hash = '#writer';
  });
  item.querySelector('#writer-new-doc-btn').addEventListener('click', (ev) => {
    ev.stopPropagation();
    location.hash = '#writer';
    // The surface mounts asynchronously on first open; wait for it before asking
    // for a new document.
    const start = Date.now();
    const tryNew = () => {
      if (window.writerModule && window.writerModule.getEditor()) window.writerModule.newDocument();
      else if (Date.now() - start < 8000) setTimeout(tryNew, 120);
    };
    tryNew();
  });
  anchor.insertAdjacentElement('afterend', item);
}

const _INJECTORS = [_injectApiTokensPanel, _injectEndpointTypeSelect, _injectRailTools, _injectSettingsRows, _markAdminOnlySettings, _injectWriterSidebarItem, _initWriter];

export function injectForkUI() {
  for (const inject of _INJECTORS) {
    try { inject(); } catch (e) { console.warn('[fork-ui] injector failed:', e); }
  }
}

// Auto-run on import. app.js (a module script) evaluates after the HTML is
// parsed, so the static settings-modal markup — and our anchors — already exist.
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectForkUI, { once: true });
  } else {
    injectForkUI();
  }
}
