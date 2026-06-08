/**
 * Pings & Reminders feed — a durable, read-only inbox of what Iris surfaced
 * (reminders, ntfy pings, briefs, task results). Each card can branch into a
 * chat session seeded with the ping's context (so Iris can pull the source).
 * Consolidates the old scattered notifications into one place.
 */
import uiModule from './ui.js';
import { makeToolModalDraggable } from './modalFullscreen.js?v=370';
import { selectSession } from './sessions.js';
import * as Modals from './modalManager.js';

const API_BASE = window.location.origin;
let _open = false;
let _unreadOnly = false;
let _pings = [];

const esc = uiModule.esc;  // reuse the canonical HTML-escape helper

function _relTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`;
  return d.toLocaleDateString();
}

const _KIND_LABEL = { reminder: 'Reminder', ping: 'Ping', brief: 'Brief', task: 'Task', email: 'Email' };

async function _api(path, opts = {}) {
  const res = await fetch(`${API_BASE}/api/pings${path}`, {
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

// Rail badge — single consolidated unread indicator.
export async function refreshUnreadBadge() {
  try {
    const { count } = await _api('/unread-count');
    const btn = document.getElementById('rail-pings');
    if (!btn) return;
    let dot = btn.querySelector('.rail-ping-badge');
    if (count > 0) {
      if (!dot) {
        dot = document.createElement('span');
        dot.className = 'rail-ping-badge';
        btn.appendChild(dot);
      }
      dot.textContent = count > 99 ? '99+' : String(count);
    } else if (dot) {
      dot.remove();
    }
  } catch (_) {}
}

function _render() {
  const body = document.querySelector('#pings-modal .modal-body');
  if (!body) return;
  const shown = _unreadOnly ? _pings.filter((p) => !p.read) : _pings;
  if (!shown.length) {
    body.innerHTML = `<div class="pings-empty">${_unreadOnly ? 'No unread pings.' : 'Nothing here yet. Reminders, briefs, and pings from Iris will show up here.'}</div>`;
    return;
  }
  body.innerHTML = shown.map((p) => `
    <div class="ping-card${p.read ? '' : ' unread'}" data-id="${p.id}">
      <div class="ping-card-head">
        <span class="ping-kind ping-kind-${esc(p.kind)}">${esc(_KIND_LABEL[p.kind] || p.kind)}</span>
        <span class="ping-title">${esc(p.title)}</span>
        ${p.keep ? '<span class="ping-keep-flag" title="Kept">★</span>' : ''}
        <span class="ping-time">${esc(_relTime(p.created_at))}</span>
      </div>
      ${p.body ? `<div class="ping-body">${esc(p.body)}</div>` : ''}
      <div class="ping-actions">
        <button class="ping-btn ping-branch" data-id="${p.id}">${p.session_id ? 'Open chat' : 'Discuss'}</button>
        <button class="ping-btn-sub" data-keep="${p.id}">${p.keep ? 'Unkeep' : 'Keep'}</button>
        <button class="ping-btn-sub" data-read="${p.id}">${p.read ? 'Unread' : 'Read'}</button>
        <button class="ping-btn-sub ping-del" data-del="${p.id}" aria-label="Delete">✕</button>
      </div>
    </div>`).join('');

  body.querySelectorAll('.ping-branch').forEach((b) => b.addEventListener('click', () => _branch(b.dataset.id)));
  body.querySelectorAll('[data-keep]').forEach((b) => b.addEventListener('click', () => _toggle(b.dataset.keep, 'keep')));
  body.querySelectorAll('[data-read]').forEach((b) => b.addEventListener('click', () => _toggle(b.dataset.read, 'read')));
  body.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => _del(b.dataset.del)));
}

async function _load() {
  const body = document.querySelector('#pings-modal .modal-body');
  if (body) body.innerHTML = '<div class="pings-empty">Loading…</div>';
  try {
    _pings = (await _api('')).pings || [];
    _render();
  } catch (e) {
    if (body) body.innerHTML = `<div class="pings-empty">${esc(e.message)}</div>`;
  }
}

async function _toggle(id, field) {
  const p = _pings.find((x) => x.id === id);
  if (!p) return;
  try {
    await _api(`/${id}`, { method: 'PUT', body: JSON.stringify({ [field]: !p[field] }) });
    p[field] = !p[field];
    _render();
    if (field === 'read') refreshUnreadBadge();
  } catch (e) { uiModule.showError?.(e.message); }
}

async function _del(id) {
  try {
    await _api(`/${id}`, { method: 'DELETE' });
    _pings = _pings.filter((x) => x.id !== id);
    _render();
    refreshUnreadBadge();
  } catch (e) { uiModule.showError?.(e.message); }
}

function _seedPrompt(p) {
  const kind = _KIND_LABEL[p.kind] || p.kind || 'item';
  let s = `About this ${kind.toLowerCase()} — "${p.title}":\n\n${p.body || ''}`.trim();
  if (p.source_ref) s += `\n\n(Source: ${p.source_ref} — pull it if you need the details.)`;
  return s;
}

// Branch a chat from a ping: reuse an existing session, else create one seeded
// with the ping context, then open the chat with the composer pre-filled.
async function _branch(id) {
  const p = _pings.find((x) => x.id === id);
  if (!p) return;
  try {
    let sid = p.session_id;
    if (!sid) {
      const dc = await (await fetch(`${API_BASE}/api/default-chat`, { credentials: 'same-origin' })).json();
      if (!dc.endpoint_url || !dc.model) { uiModule.showError?.('No default chat model configured'); return; }
      const fd = new FormData();
      fd.append('name', `Re: ${(p.title || 'ping').slice(0, 40)}`);
      fd.append('endpoint_url', dc.endpoint_url);
      fd.append('model', dc.model);
      if (dc.endpoint_id) fd.append('endpoint_id', dc.endpoint_id);
      fd.append('skip_validation', 'true');
      const res = await fetch(`${API_BASE}/api/session`, { method: 'POST', credentials: 'same-origin', body: fd });
      if (!res.ok) { uiModule.showError?.('Could not create chat'); return; }
      sid = (await res.json()).id;
      await _api(`/${id}`, { method: 'PUT', body: JSON.stringify({ session_id: sid }) }).catch(() => {});
      p.session_id = sid;
    }
    // Mark read on branch, close the feed, open the chat.
    if (!p.read) { _api(`/${id}`, { method: 'PUT', body: JSON.stringify({ read: true }) }).catch(() => {}); p.read = true; refreshUnreadBadge(); }
    closePings();
    await selectSession(sid);
    // Pre-fill the composer with the ping context (user reviews + sends).
    requestAnimationFrame(() => {
      const ta = document.getElementById('message');
      if (ta && !ta.value.trim()) {
        ta.value = _seedPrompt(p);
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        ta.focus();
      }
    });
  } catch (e) { uiModule.showError?.(e.message); }
}

export function openPings() {
  if (Modals.isRegistered('pings-modal') && Modals.isMinimized('pings-modal')) {
    Modals.restore('pings-modal');
    return;
  }
  if (_open) return;
  _open = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'pings-modal';
  modal.innerHTML = `
    <div class="modal-content pings-modal-content">
      <div class="modal-header">
        <h4><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>Pings &amp; Reminders</h4>
        <span style="flex:1"></span>
        <button class="ping-filter-toggle" id="pings-unread-toggle" title="Show unread only">Unread</button>
        <button class="ping-filter-toggle" id="pings-readall">Mark all read</button>
        <button class="close-btn" id="pings-close">✖</button>
      </div>
      <div class="modal-body"></div>
    </div>`;
  document.body.appendChild(modal);

  makeToolModalDraggable(modal);
  Modals.register('pings-modal', {
    railBtnId: 'rail-pings',
    sidebarBtnId: 'tool-pings-btn',
    closeFn: () => _doClosePings(),
    restoreFn: () => {},
    label: 'Pings',
    icon: 'M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0',
  });
  try { Modals.injectMinimizeButton(modal, 'pings-modal'); } catch (_) {}
  document.getElementById('pings-close').addEventListener('click', closePings);
  const unreadBtn = document.getElementById('pings-unread-toggle');
  unreadBtn.addEventListener('click', () => {
    _unreadOnly = !_unreadOnly;
    unreadBtn.classList.toggle('active', _unreadOnly);
    _render();
  });
  document.getElementById('pings-readall').addEventListener('click', async () => {
    try { await _api('/read-all', { method: 'POST' }); _pings.forEach((p) => { p.read = true; }); _render(); refreshUnreadBadge(); }
    catch (e) { uiModule.showError?.(e.message); }
  });
  modal.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal?.()) return;
    if (e.target === modal) closePings();
  });
  _escHandler = (e) => { if (e.key === 'Escape' && _open) closePings(); };
  document.addEventListener('keydown', _escHandler);

  _load();
}

let _escHandler = null;

function _doClosePings() {
  _open = false;
  const modal = document.getElementById('pings-modal');
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

export function closePings() {
  if (!_open && !Modals.isMinimized('pings-modal')) return;
  if (Modals.isRegistered('pings-modal')) Modals.close('pings-modal');
  else _doClosePings();
}

export function isPingsOpen() {
  if (Modals.isMinimized('pings-modal')) return false;
  return _open;
}

const pingsModule = { openPings, closePings, isPingsOpen, refreshUnreadBadge };
export default pingsModule;
