/**
 * Health Module — habits (GitHub-style heatmap), weight & calorie tracking with
 * hand-rolled SVG charts, and a training log. Backed by /api/health/* which the
 * agent's manage_health tool shares, so chat-logged data shows up here too.
 */
import uiModule from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
let _open = false;
let _tab = 'habits';

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));
const _todayLocal = () => new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD, local tz

async function _api(path, opts = {}) {
  const res = await fetch(`${API_BASE}/api/health${path}`, {
    credentials: 'same-origin',
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    ...opts,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.status === 204 ? {} : res.json();
}

// ── SVG charts ───────────────────────────────────────────────────────────────

function _heatmapSVG(cells, { cell = 12, gap = 3 } = {}) {
  if (!cells || !cells.length) return '<div class="health-empty">No data yet.</div>';
  // Align the first column to the start of its week (Sunday) like GitHub.
  const first = new Date(cells[0].day + 'T00:00:00');
  const lead = first.getDay(); // 0=Sun
  const today = _todayLocal();
  const step = cell + gap;
  const padTop = 16, padLeft = 22;
  const grid = cells.map((c, i) => {
    const slot = lead + i;
    const col = Math.floor(slot / 7);
    const row = slot % 7;
    return { ...c, x: padLeft + col * step, y: padTop + row * step };
  });
  const cols = Math.ceil((lead + cells.length) / 7);
  const width = padLeft + cols * step + 2;
  const height = padTop + 7 * step;
  // Month labels: place at the first column whose first day is in a new month.
  let lastMonth = -1;
  const monthLabels = [];
  grid.forEach((g) => {
    if (g.row !== 0) return;
    const m = new Date(g.day + 'T00:00:00').getMonth();
    if (m !== lastMonth) {
      lastMonth = m;
      monthLabels.push(`<text x="${g.x}" y="10" class="health-hm-month">${new Date(g.day + 'T00:00:00').toLocaleString([], { month: 'short' })}</text>`);
    }
  });
  const dow = ['', 'M', '', 'W', '', 'F', ''].map((d, i) => d
    ? `<text x="0" y="${padTop + i * step + cell - 2}" class="health-hm-dow">${d}</text>` : '').join('');
  const rects = grid.map((g) => {
    const cls = `health-hm-cell${g.done ? ' done' : ''}${g.day === today ? ' today' : ''}`;
    return `<rect x="${g.x}" y="${g.y}" width="${cell}" height="${cell}" rx="2.5" class="${cls}"><title>${g.day}${g.done ? ' · done' : ''}</title></rect>`;
  }).join('');
  return `<div class="health-hm-scroll"><svg width="${width}" height="${height}" class="health-hm" role="img" aria-label="Habit completion heatmap">${monthLabels.join('')}${dow}${rects}</svg></div>`;
}

function _lineChartSVG(points, { target = null, unit = 'kg', height = 150 } = {}) {
  if (!points || points.length < 1) return '<div class="health-empty">No entries yet.</div>';
  const W = Math.max(280, Math.min(640, points.length * 36));
  const H = height, padL = 38, padR = 12, padT = 12, padB = 22;
  const xs = points.map((p) => p.day);
  const ys = points.map((p) => p.kg ?? p.value ?? 0);
  let lo = Math.min(...ys, target ?? Infinity);
  let hi = Math.max(...ys, target ?? -Infinity);
  if (lo === hi) { lo -= 1; hi += 1; }
  const span = hi - lo || 1;
  const px = (i) => padL + (points.length === 1 ? (W - padL - padR) / 2 : (i / (points.length - 1)) * (W - padL - padR));
  const py = (v) => padT + (1 - (v - lo) / span) * (H - padT - padB);
  const path = ys.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');
  const dots = ys.map((v, i) => `<circle cx="${px(i).toFixed(1)}" cy="${py(v).toFixed(1)}" r="2.6" class="health-line-dot"><title>${xs[i]}: ${v} ${unit}</title></circle>`).join('');
  const targetLine = target != null
    ? `<line x1="${padL}" y1="${py(target).toFixed(1)}" x2="${W - padR}" y2="${py(target).toFixed(1)}" class="health-target-line"/><text x="${W - padR}" y="${(py(target) - 3).toFixed(1)}" class="health-axis-label" text-anchor="end">target ${target}</text>`
    : '';
  const yTicks = [hi, (hi + lo) / 2, lo].map((v) => `<text x="2" y="${(py(v) + 3).toFixed(1)}" class="health-axis-label">${v.toFixed(1)}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="health-chart" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Trend chart">${yTicks}${targetLine}<path d="${path}" class="health-line"/>${dots}</svg>`;
}

function _barChartSVG(points, { target = null, height = 150 } = {}) {
  if (!points || !points.length) return '<div class="health-empty">No entries yet.</div>';
  const W = Math.max(280, Math.min(640, points.length * 26));
  const H = height, padL = 38, padR = 10, padT = 12, padB = 22;
  const ys = points.map((p) => p.kcal ?? p.value ?? 0);
  const hi = Math.max(...ys, target ?? 0, 1);
  const bw = (W - padL - padR) / points.length;
  const py = (v) => padT + (1 - v / hi) * (H - padT - padB);
  const bars = points.map((p, i) => {
    const v = ys[i];
    const x = padL + i * bw + bw * 0.15;
    const w = bw * 0.7;
    const y = py(v);
    const over = target && v > target;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${(H - padB - y).toFixed(1)}" rx="2" class="health-bar${over ? ' over' : ''}"><title>${p.day}: ${v} kcal</title></rect>`;
  }).join('');
  const targetLine = target
    ? `<line x1="${padL}" y1="${py(target).toFixed(1)}" x2="${W - padR}" y2="${py(target).toFixed(1)}" class="health-target-line"/>`
    : '';
  const yTicks = [hi, hi / 2, 0].map((v) => `<text x="2" y="${(py(v) + 3).toFixed(1)}" class="health-axis-label">${Math.round(v)}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="health-chart" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Calories per day">${yTicks}${targetLine}${bars}</svg>`;
}

// ── Tab renderers ─────────────────────────────────────────────────────────────

function _body() { return document.querySelector('#health-modal .modal-body'); }

function _setBusy(html) { const b = _body(); if (b) b.innerHTML = `<div class="health-loading">${html}</div>`; }

async function _renderHabits() {
  const b = _body(); if (!b) return;
  let data;
  try { data = await _api('/habits'); } catch (e) { b.innerHTML = `<div class="health-error">${esc(e.message)}</div>`; return; }
  const habits = data.habits || [];
  const cards = await Promise.all(habits.map(async (h) => {
    let hm = { days: [], total: 0, streak: h.streak };
    try { hm = await _api(`/habits/${h.id}/heatmap?days=371`); } catch (_) {}
    return `
      <div class="health-card health-habit" data-id="${h.id}">
        <div class="health-habit-head">
          <div class="health-habit-title">${h.icon ? `<span class="health-habit-icon">${esc(h.icon)}</span>` : ''}<strong>${esc(h.name)}</strong>${h.category ? `<span class="health-chip">${esc(h.category)}</span>` : ''}</div>
          <div class="health-habit-stats">
            <span class="health-streak" title="Current streak">🔥 ${h.streak}</span>
            <span class="health-30d" title="Last 30 days">${h.done_30d}/30</span>
            <button class="health-check-btn${h.done_today ? ' done' : ''}" data-check="${h.id}" title="Toggle today">${h.done_today ? '✓ Done today' : 'Mark today'}</button>
            <button class="health-icon-btn" data-del-habit="${h.id}" title="Delete habit" aria-label="Delete habit">✕</button>
          </div>
        </div>
        ${_heatmapSVG(hm.days)}
      </div>`;
  }));
  b.innerHTML = `
    <div class="health-toolbar">
      <form class="health-add-habit" id="health-add-habit">
        <input name="name" class="health-input" placeholder="New habit (e.g. Meditate)" required>
        <input name="icon" class="health-input health-input-icon" placeholder="🧘" maxlength="2">
        <button class="health-btn" type="submit">Add</button>
      </form>
    </div>
    ${cards.length ? cards.join('') : '<div class="health-empty">No habits yet — add one above to start your streak.</div>'}`;
  b.querySelector('#health-add-habit')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const name = (fd.get('name') || '').toString().trim();
    if (!name) return;
    try {
      await _api('/habits', { method: 'POST', body: JSON.stringify({ name, icon: (fd.get('icon') || '').toString().trim() }) });
      _renderHabits();
    } catch (err) { uiModule.showError?.(err.message); }
  });
  b.querySelectorAll('[data-check]').forEach((btn) => btn.addEventListener('click', async () => {
    try {
      await _api(`/habits/${btn.dataset.check}/check`, { method: 'POST', body: JSON.stringify({ day: _todayLocal() }) });
      _renderHabits();
    } catch (err) { uiModule.showError?.(err.message); }
  }));
  b.querySelectorAll('[data-del-habit]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!confirm('Delete this habit and its history?')) return;
    try { await _api(`/habits/${btn.dataset.delHabit}`, { method: 'DELETE' }); _renderHabits(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
}

async function _renderWeight() {
  const b = _body(); if (!b) return;
  let trend, profile;
  try { [trend, profile] = await Promise.all([_api('/weights/trend?days=180'), _api('/profile')]); }
  catch (e) { b.innerHTML = `<div class="health-error">${esc(e.message)}</div>`; return; }
  const target = profile.profile?.target_kg ?? null;
  const last = trend.last_kg;
  const delta = trend.delta_kg;
  b.innerHTML = `
    <div class="health-card">
      <div class="health-card-head"><strong>Weight</strong>
        ${last != null ? `<span class="health-big">${last} <span class="health-unit">kg</span></span>` : ''}
        ${delta != null ? `<span class="health-delta ${delta <= 0 ? 'down' : 'up'}">${delta > 0 ? '+' : ''}${delta} kg</span>` : ''}
      </div>
      ${_lineChartSVG(trend.series || [], { target, unit: 'kg' })}
      <form class="health-inline-form" id="health-log-weight">
        <input name="kg" type="number" step="0.1" class="health-input" placeholder="Weight (kg)" required>
        <button class="health-btn" type="submit">Log weight</button>
      </form>
    </div>
    ${(trend.series || []).length ? `<div class="health-card"><div class="health-card-head"><strong>History</strong></div>
      <div class="health-list">${(trend.series || []).slice().reverse().slice(0, 30).map((w) => `<div class="health-row"><span>${w.day}</span><span>${w.kg} kg</span></div>`).join('')}</div></div>` : ''}`;
  b.querySelector('#health-log-weight')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const kg = parseFloat(new FormData(e.target).get('kg'));
    if (!Number.isFinite(kg)) return;
    try { await _api('/weights', { method: 'POST', body: JSON.stringify({ kg }) }); _renderWeight(); }
    catch (err) { uiModule.showError?.(err.message); }
  });
}

async function _renderCalories() {
  const b = _body(); if (!b) return;
  let day, series, profile;
  try {
    [day, series, profile] = await Promise.all([
      _api(`/calories?date=${_todayLocal()}`),
      _api('/calories/series?days=14'),
      _api('/profile'),
    ]);
  } catch (e) { b.innerHTML = `<div class="health-error">${esc(e.message)}</div>`; return; }
  const target = day.target_kcal;
  const pct = target ? Math.min(100, Math.round((day.total_kcal / target) * 100)) : null;
  const meals = day.meals || [];
  const p = profile.profile || {};
  b.innerHTML = `
    <div class="health-card">
      <div class="health-card-head"><strong>Today</strong>
        <span class="health-big">${day.total_kcal} <span class="health-unit">kcal</span></span>
        ${target ? `<span class="health-chip">${day.remaining_kcal >= 0 ? day.remaining_kcal + ' left' : Math.abs(day.remaining_kcal) + ' over'} · target ${target}</span>` : '<span class="health-chip">set a goal in Profile for a target</span>'}
      </div>
      ${target ? `<div class="health-progress"><span style="width:${pct}%" class="${day.total_kcal > target ? 'over' : ''}"></span></div>` : ''}
      <div class="health-macros">
        <span>P ${day.protein_g || 0}g</span><span>C ${day.carbs_g || 0}g</span><span>F ${day.fat_g || 0}g</span>
      </div>
      <form class="health-inline-form health-meal-form" id="health-log-meal">
        <input name="description" class="health-input" placeholder="Meal" required>
        <input name="kcal" type="number" class="health-input health-input-sm" placeholder="kcal" required>
        <button class="health-btn" type="submit">Add</button>
      </form>
      <div class="health-list">${meals.length ? meals.map((m) => `<div class="health-row"><span>${esc(m.description)}</span><span>${m.kcal} kcal <button class="health-icon-btn" data-del-meal="${m.id}" aria-label="Delete">✕</button></span></div>`).join('') : '<div class="health-empty">No meals logged today.</div>'}</div>
    </div>
    <div class="health-card">
      <div class="health-card-head"><strong>Last 14 days</strong></div>
      ${_barChartSVG(series.series || [], { target })}
    </div>
    <details class="health-card health-profile">
      <summary><strong>Profile &amp; goals</strong> <span class="health-muted">drives your calorie target (TDEE)</span></summary>
      <form id="health-profile-form" class="health-profile-grid">
        <label>Height (cm)<input name="height_cm" type="number" step="0.1" class="health-input" value="${p.height_cm ?? ''}"></label>
        <label>Date of birth<input name="date_of_birth" type="date" class="health-input" value="${p.date_of_birth ?? ''}"></label>
        <label>Sex<select name="sex" class="health-input"><option value="">—</option><option value="M" ${p.sex === 'M' ? 'selected' : ''}>Male</option><option value="F" ${p.sex === 'F' ? 'selected' : ''}>Female</option></select></label>
        <label>Activity<select name="activity_level" class="health-input">
          ${['sedentary', 'lightly_active', 'moderately_active', 'very_active', 'extra_active'].map((a) => `<option value="${a}" ${(p.activity_level || 'moderately_active') === a ? 'selected' : ''}>${a.replace(/_/g, ' ')}</option>`).join('')}
        </select></label>
        <label>Target weight (kg)<input name="target_kg" type="number" step="0.1" class="health-input" value="${p.target_kg ?? ''}"></label>
        <label>Weekly loss (kg)<input name="target_weekly_loss_kg" type="number" step="0.1" class="health-input" value="${p.target_weekly_loss_kg ?? ''}"></label>
        <label>Manual kcal target<input name="daily_kcal_target" type="number" class="health-input" value="${p.daily_kcal_target ?? ''}" placeholder="auto from TDEE"></label>
        <button class="health-btn" type="submit">Save profile</button>
      </form>
    </details>`;
  b.querySelector('#health-log-meal')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const description = (fd.get('description') || '').toString().trim();
    const kcal = parseInt(fd.get('kcal'), 10);
    if (!description || !Number.isFinite(kcal)) return;
    try { await _api('/meals', { method: 'POST', body: JSON.stringify({ description, kcal }) }); _renderCalories(); }
    catch (err) { uiModule.showError?.(err.message); }
  });
  b.querySelectorAll('[data-del-meal]').forEach((btn) => btn.addEventListener('click', async () => {
    try { await _api(`/meals/${btn.dataset.delMeal}`, { method: 'DELETE' }); _renderCalories(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
  b.querySelector('#health-profile-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const num = (k) => { const v = fd.get(k); return v === '' || v == null ? null : Number(v); };
    const payload = {
      height_cm: num('height_cm'), date_of_birth: fd.get('date_of_birth') || null,
      sex: fd.get('sex') || null, activity_level: fd.get('activity_level') || null,
      target_kg: num('target_kg'), target_weekly_loss_kg: num('target_weekly_loss_kg'),
      daily_kcal_target: num('daily_kcal_target'),
    };
    try { await _api('/profile', { method: 'PUT', body: JSON.stringify(payload) }); _renderCalories(); uiModule.showToast?.('Profile saved'); }
    catch (err) { uiModule.showError?.(err.message); }
  });
}

async function _renderTraining() {
  const b = _body(); if (!b) return;
  let data;
  try { data = await _api('/training?days=60'); } catch (e) { b.innerHTML = `<div class="health-error">${esc(e.message)}</div>`; return; }
  const sessions = data.sessions || [];
  b.innerHTML = `
    <div class="health-card">
      <form class="health-inline-form health-train-form" id="health-log-train">
        <input name="kind" class="health-input" placeholder="Type (Strength, Run…)" required>
        <input name="duration_min" type="number" class="health-input health-input-sm" placeholder="min">
        <input name="rpe" type="number" min="1" max="10" class="health-input health-input-sm" placeholder="RPE">
        <button class="health-btn" type="submit">Log</button>
      </form>
      <input name="summary" id="health-train-summary" class="health-input" placeholder="Notes (optional)" form="health-log-train" style="margin-top:6px">
    </div>
    <div class="health-card"><div class="health-card-head"><strong>Recent sessions</strong></div>
      <div class="health-list">${sessions.length ? sessions.map((s) => `<div class="health-row"><span>${(s.session_at || '').slice(0, 10)} · ${esc(s.kind || 'Session')}${s.duration_min ? ' · ' + s.duration_min + 'm' : ''}${s.rpe ? ' · RPE ' + s.rpe : ''}${s.summary ? ' — ' + esc(s.summary) : ''}</span><button class="health-icon-btn" data-del-train="${s.id}" aria-label="Delete">✕</button></div>`).join('') : '<div class="health-empty">No sessions logged.</div>'}</div>
    </div>`;
  b.querySelector('#health-log-train')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const kind = (fd.get('kind') || '').toString().trim();
    if (!kind) return;
    const num = (k) => { const v = fd.get(k); return v ? Number(v) : null; };
    try {
      await _api('/training', { method: 'POST', body: JSON.stringify({ kind, duration_min: num('duration_min'), rpe: num('rpe'), summary: (document.getElementById('health-train-summary')?.value || '').trim() }) });
      _renderTraining();
    } catch (err) { uiModule.showError?.(err.message); }
  });
  b.querySelectorAll('[data-del-train]').forEach((btn) => btn.addEventListener('click', async () => {
    try { await _api(`/training/${btn.dataset.delTrain}`, { method: 'DELETE' }); _renderTraining(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
}

const _TABS = { habits: _renderHabits, weight: _renderWeight, calories: _renderCalories, training: _renderTraining };

function _switchTab(tab) {
  if (!_TABS[tab]) return;
  _tab = tab;
  document.querySelectorAll('#health-modal .health-tab').forEach((t) => {
    const on = t.dataset.tab === tab;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  _setBusy('Loading…');
  _TABS[tab]();
}

export function openHealth(tab) {
  const want = _TABS[tab] ? tab : null;
  if (_open) {
    // Already open: same view again → toggle closed; otherwise switch tabs.
    if (want && _tab === want) { closeHealth(); return; }
    if (want) _switchTab(want);
    return;
  }
  if (want) _tab = want;
  _open = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'health-modal';
  modal.innerHTML = `
    <div class="modal-content health-modal-content">
      <div class="modal-header">
        <h4><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>Health</h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="health-close">✖</button>
      </div>
      <div class="memory-tabs health-tabs" role="tablist">
        <button class="memory-tab health-tab active" data-tab="habits" role="tab" aria-selected="true">Habits</button>
        <button class="memory-tab health-tab" data-tab="weight" role="tab" aria-selected="false">Weight</button>
        <button class="memory-tab health-tab" data-tab="calories" role="tab" aria-selected="false">Calories</button>
        <button class="memory-tab health-tab" data-tab="training" role="tab" aria-selected="false">Training</button>
      </div>
      <div class="modal-body"></div>
    </div>`;
  document.body.appendChild(modal);

  modal.querySelectorAll('.health-tab').forEach((btn) => btn.addEventListener('click', () => _switchTab(btn.dataset.tab)));
  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(modal, { content, header });
  document.getElementById('health-close').addEventListener('click', closeHealth);
  modal.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal?.()) return;
    if (e.target === modal) closeHealth();
  });
  _escHandler = (e) => { if (e.key === 'Escape' && _open) closeHealth(); };
  document.addEventListener('keydown', _escHandler);

  _switchTab(_tab);
}

let _escHandler = null;

export function closeHealth() {
  if (!_open) return;
  _open = false;
  const modal = document.getElementById('health-modal');
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

export function isHealthOpen() { return _open; }

const healthModule = { openHealth, closeHealth, isHealthOpen };
export default healthModule;
