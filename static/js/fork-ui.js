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
  <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:flex-start;">
    <input type="text" id="adm-tokenName" placeholder="Token name (e.g. agent-test)" class="settings-select" style="flex:1;min-width:160px;">
    <input type="text" id="adm-tokenScopes" placeholder="scopes (comma-separated, blank = chat)" class="settings-select" style="flex:2;min-width:220px;" title="Allowed: chat, cookbook:read, cookbook:launch, documents:read|write, todos:read|write, email:read|draft|send, calendar:read|write, memory:read|write">
    <button class="admin-btn-add" id="adm-tokenAddBtn">Create token</button>
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
// Fork-only: lets you mark an added endpoint as an Image-generation endpoint
// (model_type=image) rather than a chat LLM. admin.js reads #adm-epType when
// building the add-endpoint request (guarded — absent = defaults to llm).
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
  label.innerHTML = 'Type:<select id="adm-epType" style="height:32px;padding:4px 6px;flex-shrink:0;box-sizing:border-box;"><option value="llm" selected>LLM</option><option value="image">Image</option></select>';
  if (addBtn) row.insertBefore(label, addBtn);                  // sits before "Add"
  else row.appendChild(label);
}

// Registry of injectors — add fork panels here as they're moved out of index.html.
const _INJECTORS = [_injectApiTokensPanel, _injectEndpointTypeSelect];

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
