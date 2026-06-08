/**
 * "Today" dashboard — a single front-page view of the day: schedule (calendar
 * events), due/overdue reminders, and habits still to check off. Read-mostly,
 * but you can tick a habit done and carry overdue reminders forward in place.
 * Aggregates GET /api/today (events + reminders + habits, owner-scoped).
 */
import uiModule from './ui.js';
import { makeToolModalDraggable } from './modalFullscreen.js?v=370';
import * as Modals from './modalManager.js';

const API_BASE = window.location.origin;
let _open = false;
let _data = null;
let _escHandler = null;

const esc = uiModule.esc;  // reuse the canonical HTML-escape helper

async function _api(path, opts = {}) {
  const res = await fetch(`${API_BASE}/api/today${path}`, {
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

// Format a raw ISO instant in the BROWSER's timezone (the user's actual tz),
// 24-hour. The server sends raw ISO precisely so this works regardless of the
// server's own timezone.
function _fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function _hasTime(iso) {
  // "YYYY-MM-DD" (date only) has no clock component.
  return typeof iso === 'string' && /\d{2}:\d{2}/.test(iso);
}

function _render() {
  const body = document.querySelector('#today-modal .modal-body');
  if (!body) return;
  if (!_data) { body.innerHTML = '<div class="today-empty">Loading…</div>'; return; }

  const { events = [], reminders = [], habits = [] } = _data;
  const overdue = reminders.filter((r) => r.overdue);
  const everythingEmpty = !events.length && !reminders.length && !habits.length;

  const eventsHtml = events.length ? events.map((e) => `
    <div class="today-item today-event">
      <span class="today-time">${esc(e.all_day ? 'all day' : _fmtTime(e.start))}</span>
      <span class="today-item-main">${esc(e.summary)}${e.location ? `<span class="today-loc"> · ${esc(e.location)}</span>` : ''}</span>
    </div>`).join('') : '<div class="today-section-empty">Nothing scheduled.</div>';

  const remindersHtml = reminders.length ? reminders.map((r) => `
    <div class="today-item today-reminder${r.overdue ? ' overdue' : ''}">
      <span class="today-dot"></span>
      <span class="today-item-main">${esc(r.title)}</span>
      <span class="today-due">${r.overdue ? 'overdue · ' : ''}${esc(_hasTime(r.due) ? _fmtTime(r.due) : (r.due || '').slice(0, 10))}</span>
    </div>`).join('') : '<div class="today-section-empty">No reminders due.</div>';

  const habitsHtml = habits.length ? habits.map((h) => `
    <button class="today-habit" data-id="${h.id}" title="Mark done for today">
      <span class="today-habit-check" aria-hidden="true"></span>
      <span class="today-habit-name">${h.icon ? esc(h.icon) + ' ' : ''}${esc(h.name)}</span>
      ${h.streak ? `<span class="today-habit-streak">${h.streak}🔥</span>` : ''}
    </button>`).join('') : '<div class="today-section-empty">All habits done. 🎉</div>';

  body.innerHTML = `
    ${everythingEmpty ? '<div class="today-allclear">You\'re all caught up. Nothing on the radar for today. ☀️</div>' : ''}
    <section class="today-section">
      <div class="today-section-head"><span class="today-section-title">Schedule</span><span class="today-count">${events.length}</span></div>
      <div class="today-list">${eventsHtml}</div>
    </section>
    <section class="today-section">
      <div class="today-section-head">
        <span class="today-section-title">Reminders</span><span class="today-count">${reminders.length}</span>
        ${overdue.length ? `<button class="today-cf-btn" id="today-carry-forward" title="Reschedule ${overdue.length} overdue to today 09:00">Carry ${overdue.length} forward</button>` : ''}
      </div>
      <div class="today-list">${remindersHtml}</div>
    </section>
    <section class="today-section">
      <div class="today-section-head"><span class="today-section-title">Habits</span><span class="today-count">${habits.length}</span></div>
      <div class="today-habits">${habitsHtml}</div>
    </section>`;

  body.querySelectorAll('.today-habit').forEach((b) => b.addEventListener('click', () => _checkHabit(b.dataset.id, b)));
  const cf = document.getElementById('today-carry-forward');
  if (cf) cf.addEventListener('click', () => _carryForward(cf));
}

async function _load() {
  const body = document.querySelector('#today-modal .modal-body');
  if (body && !_data) body.innerHTML = '<div class="today-empty">Loading…</div>';
  try {
    _data = await _api('');
    const dateEl = document.getElementById('today-date');
    if (dateEl) dateEl.textContent = _data.date || '';
    _render();
  } catch (e) {
    if (body) body.innerHTML = `<div class="today-empty">${esc(e.message)}</div>`;
  }
}

async function _checkHabit(id, btn) {
  if (btn) { btn.classList.add('checked'); btn.disabled = true; }
  try {
    await fetch(`${API_BASE}/api/health/habits/${id}/check`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ done: true }),
    });
    // Drop it from the due list after a beat so the check reads as confirmed.
    if (_data) _data.habits = (_data.habits || []).filter((h) => String(h.id) !== String(id));
    setTimeout(_render, 280);
  } catch (e) {
    if (btn) { btn.classList.remove('checked'); btn.disabled = false; }
    uiModule.showError?.(e.message);
  }
}

async function _carryForward(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Carrying…'; }
  try {
    const res = await _api('/carry-forward', { method: 'POST', body: '{}' });
    uiModule.showToast?.(res.message || 'Done', 3000);
    await _load();
  } catch (e) {
    uiModule.showError?.(e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Carry forward'; }
  }
}

export function openToday() {
  if (Modals.isRegistered('today-modal') && Modals.isMinimized('today-modal')) {
    Modals.restore('today-modal');
    return;
  }
  if (_open) { closeToday(); return; }
  _open = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'today-modal';
  modal.innerHTML = `
    <div class="modal-content today-modal-content">
      <div class="modal-header">
        <h4><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M12 2v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="M20 12h2"/><path d="m17.66 6.34 1.41-1.41"/><path d="M22 18H2"/><path d="M16 18a4 4 0 0 0-8 0"/><path d="M2 12h2"/></svg>Today</h4>
        <span class="today-date-label" id="today-date"></span>
        <span style="flex:1"></span>
        <button class="today-refresh-btn" id="today-refresh" title="Refresh">↻</button>
        <button class="close-btn" id="today-close">✖</button>
      </div>
      <div class="modal-body"></div>
    </div>`;
  document.body.appendChild(modal);

  makeToolModalDraggable(modal);
  Modals.register('today-modal', {
    railBtnId: 'rail-today',
    sidebarBtnId: 'tool-today-btn',
    closeFn: () => _doCloseToday(),
    restoreFn: () => {},
    label: 'Today',
    icon: 'M12 2v2M4.93 4.93l1.41 1.41M20 12h2M17.66 6.34l1.41-1.41M22 18H2M16 18a4 4 0 0 0-8 0M2 12h2',
  });
  try { Modals.injectMinimizeButton(modal, 'today-modal'); } catch (_) {}
  document.getElementById('today-close').addEventListener('click', closeToday);
  document.getElementById('today-refresh').addEventListener('click', () => { _data = null; _load(); });
  modal.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal?.()) return;
    if (e.target === modal) closeToday();
  });
  _escHandler = (e) => { if (e.key === 'Escape' && _open) closeToday(); };
  document.addEventListener('keydown', _escHandler);

  _load();
}

function _doCloseToday() {
  _open = false;
  const modal = document.getElementById('today-modal');
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

export function closeToday() {
  if (!_open && !Modals.isMinimized('today-modal')) return;
  if (Modals.isRegistered('today-modal')) Modals.close('today-modal');
  else _doCloseToday();
}

export function isTodayOpen() {
  if (Modals.isMinimized('today-modal')) return false;
  return _open;
}

const todayModule = { openToday, closeToday, isTodayOpen };
export default todayModule;
