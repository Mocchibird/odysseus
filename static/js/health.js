/**
 * Health Module — habits (GitHub-style heatmap), weight & calorie tracking with
 * hand-rolled SVG charts, and a training log. Backed by /api/health/* which the
 * agent's manage_health tool shares, so chat-logged data shows up here too.
 */
import uiModule from './ui.js';
import { makeToolModalDraggable } from './modalFullscreen.js?v=370';
import * as Modals from './modalManager.js';

const API_BASE = window.location.origin;
let _open = false;
let _habitsOpen = false;
let _tab = 'calories';
const _editingHabits = new Set();  // habit ids (as strings) currently in inline-edit mode

const esc = uiModule.esc;  // reuse the canonical HTML-escape helper

// Inline SVG icons — the app bans Unicode emoji in UI; these match the
// monochrome stroke style used across static/index.html.
const _I = {
  edit: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
  del: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  check: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  flame: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>',
  camera: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>',
  download: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  upload: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  chevron: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
};
const _todayLocal = () => new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD, local tz
const _yesterdayLocal = () => { const d = new Date(); d.setDate(d.getDate() - 1); return d.toLocaleDateString('en-CA'); };

// A logged meal's macros line (skips macros that weren't recorded).
function _macroLine(m) {
  const fmt = (v) => (v == null || v === '' ? null : Math.round(Number(v) * 10) / 10);
  const parts = [];
  const p = fmt(m.protein_g); if (p != null) parts.push(`P ${p}g`);
  const c = fmt(m.carbs_g);   if (c != null) parts.push(`C ${c}g`);
  const f = fmt(m.fat_g);     if (f != null) parts.push(`F ${f}g`);
  const s = fmt(m.sugar_g);   if (s != null) parts.push(`Sugar ${s}g`);
  return parts.length ? parts.join(' · ') : 'No macros recorded for this entry.';
}

// A meal row: click to expand its macros; pencil to edit; X to delete.
function _mealRowHtml(m) {
  const v = (x) => (x == null ? '' : x);
  const pid = m.photo_upload_id;
  const thumb = pid ? `<img class="health-meal-thumb" src="${API_BASE}/api/upload/${pid}?thumb=1" alt="" loading="lazy">` : '';
  return `<div class="health-meal${pid ? ' has-photo' : ''}" data-meal-id="${m.id}">
    <div class="health-row health-meal-head" role="button" tabindex="0" data-expand-meal="${m.id}" aria-expanded="false">
      <span class="health-meal-desc">${thumb}<span class="health-meal-caret">${_I.chevron}</span>${esc(m.description)}</span>
      <span class="health-meal-right">${m.kcal} kcal
        <button class="memory-item-btn health-icon-btn" data-edit-meal="${m.id}" title="Edit entry" aria-label="Edit meal">${_I.edit}</button>
        <button class="memory-item-btn delete health-icon-btn" data-del-meal="${m.id}" title="Delete entry" aria-label="Delete meal">${_I.del}</button>
      </span>
    </div>
    <div class="health-meal-detail" hidden>
      ${pid ? `<div class="health-meal-photo">
        <a href="${API_BASE}/api/upload/${pid}" target="_blank" rel="noopener" title="Open full photo"><img src="${API_BASE}/api/upload/${pid}?thumb=1" alt="Meal photo" loading="lazy"></a>
        <button type="button" class="admin-btn-sm" data-remove-meal-photo="${m.id}">Remove photo</button>
      </div>` : ''}
      <div class="health-meal-macros">${_macroLine(m)}${m.notes ? ` — ${esc(m.notes)}` : ''}</div>
      <form class="health-inline-form health-meal-edit" data-edit-meal-form="${m.id}" hidden>
        <input name="description" value="${esc(m.description)}" placeholder="Meal" aria-label="Description">
        <input name="kcal" type="number" min="0" class="health-input-sm" value="${v(m.kcal)}" placeholder="kcal" required aria-label="kcal">
        <input name="protein_g" type="number" step="0.1" min="0" class="health-input-sm" value="${v(m.protein_g)}" placeholder="protein g" aria-label="protein">
        <input name="carbs_g" type="number" step="0.1" min="0" class="health-input-sm" value="${v(m.carbs_g)}" placeholder="carbs g" aria-label="carbs">
        <input name="fat_g" type="number" step="0.1" min="0" class="health-input-sm" value="${v(m.fat_g)}" placeholder="fat g" aria-label="fat">
        <input name="sugar_g" type="number" step="0.1" min="0" class="health-input-sm" value="${v(m.sugar_g)}" placeholder="sugar g" aria-label="sugar">
        <button class="admin-btn-add" type="submit">Save</button>
        <button type="button" class="admin-btn-sm" data-cancel-edit-meal="${m.id}">Cancel</button>
      </form>
    </div>
  </div>`;
}

// Past-days meal history (today is shown in its own card), grouped by local day.
function _mealsHistoryHtml(meals, todayStr) {
  const byDay = {};
  (meals || []).forEach((m) => {
    const d = (m.eaten_at || '').slice(0, 10);
    if (!d || d === todayStr) return;
    (byDay[d] = byDay[d] || []).push(m);
  });
  const days = Object.keys(byDay).sort().reverse();
  if (!days.length) return '<div class="health-empty">No earlier meals logged.</div>';
  return days.map((d) => {
    const rows = byDay[d];
    const tot = rows.reduce((s, m) => s + (Number(m.kcal) || 0), 0);
    const label = new Date(d + 'T00:00:00').toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    return `<div class="health-day-group">
      <div class="health-day-head"><span>${label}</span><span class="health-muted">${tot} kcal · ${rows.length} meal${rows.length === 1 ? '' : 's'}</span></div>
      ${rows.map(_mealRowHtml).join('')}
    </div>`;
  }).join('');
}

// Editable weight entry (PUT/DELETE /weights/{id}).
function _weightRowHtml(w) {
  const d = (w.measured_at || '').slice(0, 10);
  return `<div class="health-entry" data-weight-id="${w.id}">
    <div class="health-row">
      <span>${d} · <strong>${w.kg} kg</strong>${w.notes ? ` — ${esc(w.notes)}` : ''}</span>
      <span class="health-meal-right">
        <button class="memory-item-btn health-icon-btn" data-edit-weight="${w.id}" title="Edit entry" aria-label="Edit weight">${_I.edit}</button>
        <button class="memory-item-btn delete health-icon-btn" data-del-weight="${w.id}" title="Delete entry" aria-label="Delete weight">${_I.del}</button>
      </span>
    </div>
    <form class="health-inline-form health-entry-edit" data-edit-weight-form="${w.id}" data-orig-date="${d}" hidden>
      <input name="kg" type="number" step="0.1" min="0" class="health-input-sm" value="${w.kg ?? ''}" placeholder="kg" required aria-label="kg">
      <input name="measured_at" type="date" value="${d}" aria-label="date">
      <input name="notes" value="${esc(w.notes || '')}" placeholder="notes" aria-label="notes">
      <button class="admin-btn-add" type="submit">Save</button>
      <button type="button" class="admin-btn-sm" data-cancel-edit-weight="${w.id}">Cancel</button>
    </form>
  </div>`;
}

// Editable training session (PUT/DELETE /training/{id}).
function _trainingRowHtml(s) {
  const v = (x) => (x == null ? '' : x);
  const d = (s.session_at || '').slice(0, 10);
  const line = `${d} · ${esc(s.kind || 'Session')}${s.duration_min ? ' · ' + s.duration_min + 'm' : ''}${s.rpe ? ' · RPE ' + s.rpe : ''}${s.kcal_burned ? ' · ' + s.kcal_burned + ' kcal' : ''}${s.summary ? ' — ' + esc(s.summary) : ''}`;
  return `<div class="health-entry" data-train-id="${s.id}">
    <div class="health-row">
      <span>${line}</span>
      <span class="health-meal-right">
        <button class="memory-item-btn health-icon-btn" data-edit-train="${s.id}" title="Edit entry" aria-label="Edit session">${_I.edit}</button>
        <button class="memory-item-btn delete health-icon-btn" data-del-train="${s.id}" title="Delete entry" aria-label="Delete session">${_I.del}</button>
      </span>
    </div>
    <form class="health-inline-form health-entry-edit" data-edit-train-form="${s.id}" hidden>
      <input name="kind" value="${esc(s.kind || '')}" placeholder="Type" required aria-label="kind">
      <input name="duration_min" type="number" min="0" class="health-input-sm" value="${v(s.duration_min)}" placeholder="min" aria-label="min">
      <input name="rpe" type="number" min="1" max="10" class="health-input-sm" value="${v(s.rpe)}" placeholder="RPE" aria-label="RPE">
      <input name="kcal_burned" type="number" min="0" class="health-input-sm" value="${v(s.kcal_burned)}" placeholder="kcal" aria-label="kcal">
      <input name="summary" value="${esc(s.summary || '')}" placeholder="notes" aria-label="notes">
      <button class="admin-btn-add" type="submit">Save</button>
      <button type="button" class="admin-btn-sm" data-cancel-edit-train="${s.id}">Cancel</button>
    </form>
  </div>`;
}

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

// A macro ring (donut) — consumed grams vs target, SVG only.
function _ringSVG(value, target, label, varName) {
  const r = 26, circ = 2 * Math.PI * r;
  const pct = target ? Math.max(0, Math.min(1, value / target)) : 0;
  const off = circ * (1 - pct);
  const over = target && value > target;
  return `<div class="health-ring">
    <svg viewBox="0 0 64 64" width="62" height="62" role="img" aria-label="${esc(label)} ${Math.round(value)}g">
      <circle cx="32" cy="32" r="${r}" class="health-ring-bg"/>
      <circle cx="32" cy="32" r="${r}" class="health-ring-fg${over ? ' over' : ''}" style="stroke:var(${varName});stroke-dasharray:${circ.toFixed(1)};stroke-dashoffset:${off.toFixed(1)}"/>
      <text x="32" y="31" class="health-ring-val">${Math.round(value)}</text>
      <text x="32" y="43" class="health-ring-unit">g</text>
    </svg>
    <div class="health-ring-label">${esc(label)}${target ? `<span> / ${target}g</span>` : ''}</div>
  </div>`;
}

// ── Tab renderers ─────────────────────────────────────────────────────────────

function _body() { return document.querySelector('#health-modal .modal-body'); }
function _habitsBody() { return document.querySelector('#habits-modal .modal-body'); }

function _setBusy(html) { const b = _body(); if (b) b.innerHTML = `<div class="health-loading">${html}</div>`; }

// Habits live in their own window (#habits-modal) — render into that body.
async function _renderHabits() {
  const b = _habitsBody(); if (!b) return;
  let data;
  try { data = await _api('/habits'); } catch (e) { b.innerHTML = `<div class="health-error">${esc(e.message)}</div>`; return; }
  const habits = data.habits || [];
  const cards = await Promise.all(habits.map(async (h) => {
    let hm = { days: [], total: 0, streak: h.streak };
    try { hm = await _api(`/habits/${h.id}/heatmap?days=371`); } catch (_) {}
    const editing = _editingHabits.has(String(h.id));
    const head = editing ? `
        <form class="health-inline-form health-habit-edit" data-edit-form="${h.id}">
          <input name="name" value="${esc(h.name)}" placeholder="Habit name" required aria-label="Habit name">
          <input name="icon" class="health-input-icon" value="${esc(h.icon || '')}" placeholder="icon" maxlength="2" aria-label="Icon">
          <input name="category" class="health-input-sm" value="${esc(h.category || '')}" placeholder="Category" aria-label="Category">
          <button type="submit" class="admin-btn-add">Save</button>
          <button type="button" class="admin-btn-sm" data-cancel-edit="${h.id}">Cancel</button>
        </form>` : `
        <div class="health-habit-head">
          <div class="health-habit-title">${h.icon ? `<span class="health-habit-icon">${esc(h.icon)}</span>` : ''}<strong>${esc(h.name)}</strong>${h.category ? `<span class="health-chip">${esc(h.category)}</span>` : ''}</div>
          <div class="health-habit-stats">
            <span class="health-streak" title="Current streak">${_I.flame}${h.streak}</span>
            <span class="health-30d" title="Last 30 days">${h.done_30d}/30</span>
            <button class="memory-toolbar-btn health-check-btn${h.done_today ? ' active' : ''}" data-check="${h.id}" title="Toggle today">${h.done_today ? _I.check + 'Done today' : 'Mark today'}</button>
            <button class="memory-toolbar-btn health-check-btn health-check-yday${h.done_yesterday ? ' active' : ''}" data-check-yday="${h.id}" title="Toggle yesterday">${h.done_yesterday ? _I.check + 'Yesterday' : 'Yesterday'}</button>
            <button class="memory-item-btn health-icon-btn" data-edit-habit="${h.id}" title="Edit habit (rename, icon, category)" aria-label="Edit habit">${_I.edit}</button>
            <button class="memory-item-btn delete health-icon-btn" data-del-habit="${h.id}" title="Delete habit" aria-label="Delete habit">${_I.del}</button>
          </div>
        </div>`;
    return `
      <div class="admin-card health-habit" data-id="${h.id}">
        ${head}
        ${_heatmapSVG(hm.days)}
      </div>`;
  }));
  const weekDone = habits.reduce((s, h) => s + (h.done_7d || 0), 0);
  const weekPossible = habits.length * 7;
  const weeklyCard = habits.length ? `
    <div class="admin-card health-week-card">
      <div class="health-card-head"><strong>This week</strong><span class="health-big">${weekDone}<span class="health-unit">/${weekPossible}</span></span></div>
      <div class="health-week-rows">${habits.map((h) => `<div class="health-week-row"><span class="health-week-name">${h.icon ? esc(h.icon) + ' ' : ''}${esc(h.name)}</span><span class="health-week-dots">${[...Array(7)].map((_, i) => `<i class="${i < (h.done_7d || 0) ? 'on' : ''}"></i>`).join('')}</span><span class="health-week-num">${h.done_7d || 0}/7</span></div>`).join('')}</div>
    </div>` : '';
  b.innerHTML = `
    <div class="health-toolbar">
      <form class="health-add-habit" id="health-add-habit">
        <input name="name" placeholder="New habit (e.g. Meditate)" required>
        <input name="icon" class="health-input-icon" placeholder="icon" maxlength="2">
        <button class="admin-btn-add" type="submit">Add</button>
      </form>
    </div>
    ${weeklyCard}
    ${cards.length ? cards.join('') : '<div class="health-empty">No habits yet — add one above to start your streak.</div>'}`;
  // GitHub-style heatmap runs oldest→newest, so today is the rightmost column.
  // Scroll each heatmap to its right edge so TODAY is visible without the user
  // having to scroll across a year of history.
  b.querySelectorAll('.health-hm-scroll').forEach((el) => { el.scrollLeft = el.scrollWidth; });
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
  b.querySelectorAll('[data-check-yday]').forEach((btn) => btn.addEventListener('click', async () => {
    try {
      await _api(`/habits/${btn.dataset.checkYday}/check`, { method: 'POST', body: JSON.stringify({ day: _yesterdayLocal() }) });
      _renderHabits();
    } catch (err) { uiModule.showError?.(err.message); }
  }));
  b.querySelectorAll('[data-del-habit]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!await uiModule.styledConfirm('Delete this habit and its history?', { confirmText: 'Delete', danger: true })) return;
    try { await _api(`/habits/${btn.dataset.delHabit}`, { method: 'DELETE' }); _renderHabits(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
  // Inline edit (rename / emoji / category) — mirrors the add-habit form.
  b.querySelectorAll('[data-edit-habit]').forEach((btn) => btn.addEventListener('click', () => {
    _editingHabits.add(String(btn.dataset.editHabit));
    _renderHabits();
  }));
  b.querySelectorAll('[data-cancel-edit]').forEach((btn) => btn.addEventListener('click', () => {
    _editingHabits.delete(String(btn.dataset.cancelEdit));
    _renderHabits();
  }));
  b.querySelectorAll('[data-edit-form]').forEach((form) => form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = form.dataset.editForm;
    const fd = new FormData(form);
    const name = (fd.get('name') || '').toString().trim();
    if (!name) return;
    try {
      await _api(`/habits/${id}`, { method: 'PUT', body: JSON.stringify({
        name,
        icon: (fd.get('icon') || '').toString().trim(),
        category: (fd.get('category') || '').toString().trim(),
      }) });
      _editingHabits.delete(String(id));
      _renderHabits();
    } catch (err) { uiModule.showError?.(err.message); }
  }));
}

async function _renderWeight() {
  const b = _body(); if (!b) return;
  let trend, profile, wResp;
  try { [trend, profile, wResp] = await Promise.all([_api('/weights/trend?days=180'), _api('/profile'), _api('/weights?days=180')]); }
  catch (e) { b.innerHTML = `<div class="health-error">${esc(e.message)}</div>`; return; }
  const entries = wResp?.weights || [];
  const target = profile.profile?.target_kg ?? null;
  const last = trend.last_kg;
  const delta = trend.delta_kg;
  let projHtml = '';
  if (typeof trend.slope_kg_per_week === 'number') {
    const slope = trend.slope_kg_per_week;
    const slopeStr = `${slope > 0 ? '+' : ''}${slope} kg/wk`;
    const proj = trend.projection;
    if (proj && proj.eta_date) {
      const eta = new Date(proj.eta_date).toLocaleDateString();
      projHtml = `<div class="health-proj">On track — ~${proj.days} days to ${proj.target_kg} kg (≈ ${eta}) · ${slopeStr}</div>`;
    } else if (proj && proj.off_track) {
      projHtml = `<div class="health-proj off">Trending away from your ${proj.target_kg} kg goal · ${slopeStr}</div>`;
    } else {
      projHtml = `<div class="health-proj">Trend: ${slopeStr}${target ? ` · goal ${target} kg` : ''}</div>`;
    }
  }
  b.innerHTML = `
    <div class="admin-card">
      <div class="health-card-head"><strong>Weight</strong>
        ${last != null ? `<span class="health-big">${last} <span class="health-unit">kg</span></span>` : ''}
        ${delta != null ? `<span class="health-delta ${delta <= 0 ? 'down' : 'up'}">${delta > 0 ? '+' : ''}${delta} kg</span>` : ''}
      </div>
      ${_lineChartSVG(trend.series || [], { target, unit: 'kg' })}
      ${projHtml}
      <form class="health-inline-form" id="health-log-weight">
        <input name="kg" type="number" step="0.1" placeholder="Weight (kg)" required>
        <button class="admin-btn-add" type="submit">Log weight</button>
      </form>
    </div>
    ${entries.length ? `<div class="admin-card"><div class="health-card-head"><strong>History</strong> <span class="health-muted">tap the pencil to edit</span></div>
      <div class="health-list">${entries.slice().reverse().map(_weightRowHtml).join('')}</div></div>` : ''}`;
  b.querySelector('#health-log-weight')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const kg = parseFloat(new FormData(e.target).get('kg'));
    if (!Number.isFinite(kg)) return;
    try { await _api('/weights', { method: 'POST', body: JSON.stringify({ kg }) }); _renderWeight(); }
    catch (err) { uiModule.showError?.(err.message); }
  });
  b.querySelectorAll('[data-edit-weight]').forEach((btn) => btn.addEventListener('click', () => {
    const item = btn.closest('.health-entry');
    const form = item?.querySelector('.health-entry-edit');
    if (form) { form.removeAttribute('hidden'); item.classList.add('editing'); form.querySelector('[name="kg"]')?.focus(); }
  }));
  b.querySelectorAll('[data-cancel-edit-weight]').forEach((btn) => btn.addEventListener('click', () => {
    const item = btn.closest('.health-entry'); item?.classList.remove('editing');
    item?.querySelector('.health-entry-edit')?.setAttribute('hidden', '');
  }));
  b.querySelectorAll('[data-del-weight]').forEach((btn) => btn.addEventListener('click', async () => {
    try { await _api(`/weights/${btn.dataset.delWeight}`, { method: 'DELETE' }); _renderWeight(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
  b.querySelectorAll('[data-edit-weight-form]').forEach((form) => form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const kg = parseFloat(fd.get('kg'));
    if (!Number.isFinite(kg)) return;
    const payload = { kg, notes: (fd.get('notes') || '').toString().trim() };
    const newDate = (fd.get('measured_at') || '').toString();
    if (newDate && newDate !== form.dataset.origDate) payload.measured_at = newDate;
    try { await _api(`/weights/${form.dataset.editWeightForm}`, { method: 'PUT', body: JSON.stringify(payload) }); _renderWeight(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
}

async function _renderCalories() {
  const b = _body(); if (!b) return;
  let day, series, profile, histResp;
  try {
    [day, series, profile, histResp] = await Promise.all([
      _api(`/calories?date=${_todayLocal()}`),
      _api('/calories/series?days=14'),
      _api('/profile'),
      _api('/meals?days=14'),
    ]);
  } catch (e) { b.innerHTML = `<div class="health-error">${esc(e.message)}</div>`; return; }
  const history = histResp?.meals || [];
  const target = day.target_kcal;
  // Exercise earns back part of the burn (TRAINING_BURN_CREDIT, default 50%);
  // the budget the user eats against is the adjusted target.
  const adjTarget = day.adjusted_target_kcal || target;
  const pct = adjTarget ? Math.min(100, Math.round((day.total_kcal / adjTarget) * 100)) : null;
  const meals = day.meals || [];
  const p = profile.profile || {};
  b.innerHTML = `
    <div class="admin-card">
      <div class="health-card-head"><strong>Today</strong>
        <span class="health-big">${day.total_kcal} <span class="health-unit">kcal</span></span>
        ${adjTarget ? `<span class="health-chip">${day.remaining_kcal >= 0 ? day.remaining_kcal + ' left' : Math.abs(day.remaining_kcal) + ' over'} · target ${adjTarget}${day.burn_credit ? ` (+${day.burn_credit} training)` : ''}</span>` : '<span class="health-chip">set a goal in Profile for a target</span>'}
      </div>
      ${day.kcal_burned ? `<div class="health-muted" style="margin:-2px 0 6px;font-size:12px">${day.kcal_burned} kcal burned in training · ${day.burn_credit} credited back to today's budget</div>` : ''}
      ${adjTarget ? `<div class="health-progress"><span style="width:${pct}%" class="${day.total_kcal > adjTarget ? 'over' : ''}"></span></div>` : ''}
      ${day.macro_targets
        ? `<div class="health-rings">
             ${_ringSVG(day.protein_g || 0, day.macro_targets.protein_g, 'Protein', '--green')}
             ${_ringSVG(day.carbs_g || 0, day.macro_targets.carbs_g, 'Carbs', '--color-accent')}
             ${_ringSVG(day.fat_g || 0, day.macro_targets.fat_g, 'Fat', '--warn')}
           </div>`
        : `<div class="health-macros"><span>P ${day.protein_g || 0}g</span><span>C ${day.carbs_g || 0}g</span><span>F ${day.fat_g || 0}g</span></div>`}
      ${day.sugar_g ? `<div class="health-macros"><span>Sugar ${day.sugar_g}g</span></div>` : ''}
      <form class="health-inline-form health-meal-form" id="health-log-meal">
        <input name="description" placeholder="Meal (optional)">
        <input name="kcal" type="number" class="health-input-sm" placeholder="kcal" required>
        <button class="admin-btn-add" type="submit">Add</button>
        <label class="admin-btn-sm health-file-btn" title="Estimate from a photo">${_I.camera}<input id="health-meal-photo" type="file" accept="image/*" capture="environment" style="display:none"></label>
      </form>
      <details class="health-macros-extra">
        <summary class="health-muted">+ macros (optional)</summary>
        <div class="health-inline-form" style="margin-top:6px">
          <input name="protein_g" type="number" step="0.1" min="0" class="health-input-sm" placeholder="protein g" form="health-log-meal">
          <input name="carbs_g" type="number" step="0.1" min="0" class="health-input-sm" placeholder="carbs g" form="health-log-meal">
          <input name="fat_g" type="number" step="0.1" min="0" class="health-input-sm" placeholder="fat g" form="health-log-meal">
          <input name="sugar_g" type="number" step="0.1" min="0" class="health-input-sm" placeholder="sugar g" form="health-log-meal">
        </div>
      </details>
      <div id="health-meal-est-note" class="health-muted" style="display:none;margin:-2px 0 8px;"></div>
      <div class="health-list health-meal-list">${meals.length ? meals.map(_mealRowHtml).join('') : '<div class="health-empty">No meals logged today.</div>'}</div>
    </div>
    <div class="admin-card">
      <div class="health-card-head"><strong>Last 14 days</strong></div>
      ${_barChartSVG(series.series || [], { target })}
    </div>
    <div class="admin-card">
      <div class="health-card-head"><strong>History</strong> <span class="health-muted">earlier days — tap a meal to edit</span></div>
      <div class="health-list health-meal-list">${_mealsHistoryHtml(history, _todayLocal())}</div>
    </div>
    <div class="admin-card">
      <div class="health-card-head"><strong>Data (CSV)</strong> <span class="health-muted">export / import meals, weights, training</span></div>
      <div class="health-inline-form">
        <select id="health-csv-kind"><option value="meals">Meals</option><option value="weights">Weights</option><option value="training">Training</option></select>
        <button class="admin-btn-sm" id="health-csv-export" type="button">${_I.download} Export</button>
        <label class="admin-btn-sm health-file-btn">${_I.upload} Import<input id="health-csv-import" type="file" accept=".csv,text/csv" style="display:none"></label>
      </div>
    </div>
    <details class="health-profile">
      <summary><strong>Profile &amp; goals</strong> <span class="health-muted">drives your calorie target (TDEE)</span></summary>
      <form id="health-profile-form" class="health-profile-grid">
        <label>Height (cm)<input name="height_cm" type="number" step="0.1" value="${p.height_cm ?? ''}"></label>
        <label>Date of birth<input name="date_of_birth" type="date" value="${p.date_of_birth ?? ''}"></label>
        <label>Sex<select name="sex"><option value="">—</option><option value="M" ${p.sex === 'M' ? 'selected' : ''}>Male</option><option value="F" ${p.sex === 'F' ? 'selected' : ''}>Female</option></select></label>
        <label>Activity<select name="activity_level">
          ${['sedentary', 'lightly_active', 'moderately_active', 'very_active', 'extra_active'].map((a) => `<option value="${a}" ${(p.activity_level || 'moderately_active') === a ? 'selected' : ''}>${a.replace(/_/g, ' ')}</option>`).join('')}
        </select></label>
        <label>Target weight (kg)<input name="target_kg" type="number" step="0.1" value="${p.target_kg ?? ''}"></label>
        <label>Weekly loss (kg)<input name="target_weekly_loss_kg" type="number" step="0.1" value="${p.target_weekly_loss_kg ?? ''}"></label>
        <label>Manual kcal target<input name="daily_kcal_target" type="number" value="${p.daily_kcal_target ?? ''}" placeholder="auto from TDEE"></label>
        <button class="admin-btn-add" type="submit">Save profile</button>
      </form>
    </details>`;
  let _pendingMacros = null;  // macros from a photo estimate, sent on the next Add
  let _pendingPhotoId = null; // upload id of the estimated photo, associated on the next Add
  b.querySelector('#health-log-meal')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const description = (fd.get('description') || '').toString().trim();
    const kcal = parseInt(fd.get('kcal'), 10);
    if (!Number.isFinite(kcal)) return;  // description is optional, kcal is the core value
    const payload = { description: description || 'Meal', kcal };
    ['protein_g', 'carbs_g', 'fat_g', 'sugar_g'].forEach((k) => {
      const v = parseFloat(fd.get(k));
      if (Number.isFinite(v)) payload[k] = v;
    });
    if (_pendingMacros) Object.assign(payload, _pendingMacros, { source: 'photo' });
    if (_pendingPhotoId) payload.photo_upload_id = _pendingPhotoId;
    try { await _api('/meals', { method: 'POST', body: JSON.stringify(payload) }); _renderCalories(); }
    catch (err) { uiModule.showError?.(err.message); }
  });
  // Photo → vision estimate → pre-fill the form for the user to confirm.
  b.querySelector('#health-meal-photo')?.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const note = document.getElementById('health-meal-est-note');
    const descIn = b.querySelector('#health-log-meal [name="description"]');
    const kcalIn = b.querySelector('#health-log-meal [name="kcal"]');
    if (note) { note.style.display = ''; note.textContent = 'Estimating from photo…'; }
    try {
      const form = new FormData(); form.append('file', file);
      const res = await fetch(`${API_BASE}/api/health/estimate-meal`, { method: 'POST', credentials: 'same-origin', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Estimate failed');
      const est = data.estimate || {};
      if (descIn) descIn.value = est.description || '';
      if (kcalIn) kcalIn.value = est.kcal || '';
      _pendingMacros = {};
      ['protein_g', 'carbs_g', 'fat_g', 'sugar_g'].forEach((k) => { if (est[k] != null) _pendingMacros[k] = est[k]; });
      _pendingPhotoId = data.photo_upload_id || null;
      if (note) note.textContent = `Estimated: ${est.kcal || 0} kcal${est.protein_g != null ? ` · P${Math.round(est.protein_g)} C${Math.round(est.carbs_g || 0)} F${Math.round(est.fat_g || 0)}` : ''} — review and press Add.`;
    } catch (err) {
      if (note) note.textContent = `Couldn’t estimate: ${err.message}`;
    } finally { e.target.value = ''; }
  });
  b.querySelectorAll('[data-del-meal]').forEach((btn) => btn.addEventListener('click', async () => {
    try { await _api(`/meals/${btn.dataset.delMeal}`, { method: 'DELETE' }); _renderCalories(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
  // Expand a meal row to reveal its macros (clicking the row, not the action buttons).
  b.querySelectorAll('[data-expand-meal]').forEach((head) => {
    const toggle = (e) => {
      if (e.target.closest('.health-icon-btn')) return;  // edit/delete handle themselves
      const item = head.closest('.health-meal');
      const detail = item?.querySelector('.health-meal-detail');
      if (!detail) return;
      const opening = detail.hasAttribute('hidden');
      detail.toggleAttribute('hidden', !opening);
      head.setAttribute('aria-expanded', String(opening));
      item.classList.toggle('expanded', opening);
    };
    head.addEventListener('click', toggle);
    head.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(e); } });
  });
  // Edit button: open the detail + reveal the inline edit form.
  b.querySelectorAll('[data-edit-meal]').forEach((btn) => btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const item = btn.closest('.health-meal');
    const detail = item?.querySelector('.health-meal-detail');
    const form = item?.querySelector('.health-meal-edit');
    if (!detail || !form) return;
    detail.removeAttribute('hidden');
    form.removeAttribute('hidden');
    item.classList.add('expanded', 'editing');
    item.querySelector('[data-expand-meal]')?.setAttribute('aria-expanded', 'true');
    form.querySelector('[name="kcal"]')?.focus();
  }));
  b.querySelectorAll('[data-cancel-edit-meal]').forEach((btn) => btn.addEventListener('click', () => {
    const item = btn.closest('.health-meal');
    item?.classList.remove('editing');
    item?.querySelector('.health-meal-edit')?.setAttribute('hidden', '');
  }));
  b.querySelectorAll('[data-edit-meal-form]').forEach((form) => form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = form.dataset.editMealForm;
    const fd = new FormData(form);
    const kcal = parseInt(fd.get('kcal'), 10);
    if (!Number.isFinite(kcal)) return;
    const payload = { description: (fd.get('description') || '').toString().trim() || 'Meal', kcal };
    // Send all macros (the form is pre-filled) so blanking one clears it.
    ['protein_g', 'carbs_g', 'fat_g', 'sugar_g'].forEach((k) => {
      const raw = (fd.get(k) ?? '').toString().trim();
      const val = parseFloat(raw);
      payload[k] = raw === '' || !Number.isFinite(val) ? null : val;
    });
    try { await _api(`/meals/${id}`, { method: 'PUT', body: JSON.stringify(payload) }); _renderCalories(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
  b.querySelectorAll('[data-remove-meal-photo]').forEach((btn) => btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    try { await _api(`/meals/${btn.dataset.removeMealPhoto}`, { method: 'PUT', body: JSON.stringify({ photo_upload_id: '' }) }); _renderCalories(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
  // CSV export/import
  const csvKind = () => (document.getElementById('health-csv-kind')?.value || 'meals');
  b.querySelector('#health-csv-export')?.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = `${API_BASE}/api/health/export?kind=${encodeURIComponent(csvKind())}`;
    a.download = `health-${csvKind()}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
  });
  b.querySelector('#health-csv-import')?.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      const res = await fetch(`${API_BASE}/api/health/import?kind=${encodeURIComponent(csvKind())}`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'text/csv' }, body: text,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Import failed');
      uiModule.showToast?.(`Imported ${data.imported} row(s)`);
      _renderCalories();
    } catch (err) { uiModule.showError?.(err.message); }
    finally { e.target.value = ''; }
  });
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
    <div class="admin-card">
      <form class="health-inline-form health-train-form" id="health-log-train">
        <input name="kind" placeholder="Type (Strength, Run…)" required>
        <input name="duration_min" type="number" class="health-input-sm" placeholder="min">
        <input name="rpe" type="number" min="1" max="10" class="health-input-sm" placeholder="RPE">
        <input name="kcal_burned" type="number" min="0" class="health-input-sm" placeholder="kcal">
        <button class="admin-btn-add" type="submit">Log</button>
      </form>
      <input name="summary" id="health-train-summary" placeholder="Notes (optional)" form="health-log-train" style="margin-top:6px">
    </div>
    <div class="admin-card"><div class="health-card-head"><strong>Recent sessions</strong> <span class="health-muted">tap the pencil to edit</span></div>
      <div class="health-list">${sessions.length ? sessions.map(_trainingRowHtml).join('') : '<div class="health-empty">No sessions logged.</div>'}</div>
    </div>`;
  b.querySelector('#health-log-train')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const kind = (fd.get('kind') || '').toString().trim();
    if (!kind) return;
    const num = (k) => { const v = fd.get(k); return v ? Number(v) : null; };
    try {
      await _api('/training', { method: 'POST', body: JSON.stringify({ kind, duration_min: num('duration_min'), rpe: num('rpe'), kcal_burned: num('kcal_burned'), summary: (document.getElementById('health-train-summary')?.value || '').trim() }) });
      _renderTraining();
    } catch (err) { uiModule.showError?.(err.message); }
  });
  b.querySelectorAll('[data-del-train]').forEach((btn) => btn.addEventListener('click', async () => {
    try { await _api(`/training/${btn.dataset.delTrain}`, { method: 'DELETE' }); _renderTraining(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
  b.querySelectorAll('[data-edit-train]').forEach((btn) => btn.addEventListener('click', () => {
    const item = btn.closest('.health-entry');
    const form = item?.querySelector('.health-entry-edit');
    if (form) { form.removeAttribute('hidden'); item.classList.add('editing'); form.querySelector('[name="kind"]')?.focus(); }
  }));
  b.querySelectorAll('[data-cancel-edit-train]').forEach((btn) => btn.addEventListener('click', () => {
    const item = btn.closest('.health-entry'); item?.classList.remove('editing');
    item?.querySelector('.health-entry-edit')?.setAttribute('hidden', '');
  }));
  b.querySelectorAll('[data-edit-train-form]').forEach((form) => form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const kind = (fd.get('kind') || '').toString().trim();
    if (!kind) return;
    const numOrNull = (k) => { const raw = (fd.get(k) ?? '').toString().trim(); const n = Number(raw); return raw === '' || !Number.isFinite(n) ? null : n; };
    const payload = { kind, duration_min: numOrNull('duration_min'), rpe: numOrNull('rpe'), kcal_burned: numOrNull('kcal_burned'), summary: (fd.get('summary') || '').toString().trim() };
    try { await _api(`/training/${form.dataset.editTrainForm}`, { method: 'PUT', body: JSON.stringify(payload) }); _renderTraining(); }
    catch (err) { uiModule.showError?.(err.message); }
  }));
}

const _TABS = { weight: _renderWeight, calories: _renderCalories, training: _renderTraining };

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
  // Minimized → restore in place (consistent with gallery/calendar/etc.).
  if (Modals.isRegistered('health-modal') && Modals.isMinimized('health-modal')) {
    Modals.restore('health-modal');
    if (want) _switchTab(want);
    return;
  }
  if (_open) {
    // Already open: same view again → toggle closed; otherwise switch tabs.
    if (want && _tab === want) { closeHealth(); return; }
    if (want) _switchTab(want);
    return;
  }
  if (want) _tab = want;
  // Drop a still-animating node from a previous close so a fast reopen can't
  // leave two #health-modal nodes (getElementById would bind to the dying one).
  document.getElementById('health-modal')?.remove();
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
        <button class="memory-tab health-tab active" data-tab="calories" role="tab" aria-selected="true">Calories</button>
        <button class="memory-tab health-tab" data-tab="weight" role="tab" aria-selected="false">Weight</button>
        <button class="memory-tab health-tab" data-tab="training" role="tab" aria-selected="false">Training</button>
      </div>
      <div class="modal-body"></div>
    </div>`;
  document.body.appendChild(modal);

  modal.querySelectorAll('.health-tab').forEach((btn) => btn.addEventListener('click', () => _switchTab(btn.dataset.tab)));
  makeToolModalDraggable(modal);
  // Register with the Modals manager so Health gets the same minimize→dock,
  // restore and rail/sidebar badge behavior as every other tool window.
  Modals.register('health-modal', {
    railBtnId: 'rail-health',
    sidebarBtnId: 'tool-health-btn',
    closeFn: () => _doCloseHealth(),
    restoreFn: () => {},
    label: 'Health',
    icon: 'M22 12h-4l-3 9L9 3l-3 9H2',
  });
  try { Modals.injectMinimizeButton(modal, 'health-modal'); } catch (_) {}
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

// Actual teardown — invoked by Modals.close() via the registered closeFn.
function _doCloseHealth() {
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

export function closeHealth() {
  if (!_open && !Modals.isMinimized('health-modal')) return;
  if (Modals.isRegistered('health-modal')) Modals.close('health-modal');
  else _doCloseHealth();
}

export function isHealthOpen() {
  if (Modals.isMinimized('health-modal')) return false;
  return _open;
}

// ── Habits: its own window (separate from Health) ────────────────────────────
let _habitsEsc = null;

export function openHabits() {
  if (Modals.isRegistered('habits-modal') && Modals.isMinimized('habits-modal')) {
    Modals.restore('habits-modal');
    return;
  }
  if (_habitsOpen) { closeHabits(); return; }  // toggle from the rail
  document.getElementById('habits-modal')?.remove();  // clear a still-closing node
  _habitsOpen = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'habits-modal';
  modal.innerHTML = `
    <div class="modal-content health-modal-content">
      <div class="modal-header">
        <h4><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>Habits</h4>
        <span style="flex:1"></span>
        <button class="close-btn" id="habits-close">✖</button>
      </div>
      <div class="modal-body"><div class="health-loading">Loading…</div></div>
    </div>`;
  document.body.appendChild(modal);

  makeToolModalDraggable(modal);
  Modals.register('habits-modal', {
    railBtnId: 'rail-habits',
    sidebarBtnId: 'tool-habits-btn',
    closeFn: () => _doCloseHabits(),
    restoreFn: () => {},
    label: 'Habits',
    icon: 'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
  });
  try { Modals.injectMinimizeButton(modal, 'habits-modal'); } catch (_) {}
  document.getElementById('habits-close').addEventListener('click', closeHabits);
  modal.addEventListener('click', (e) => {
    if (uiModule.isTouchInsideModal?.()) return;
    if (e.target === modal) closeHabits();
  });
  _habitsEsc = (e) => { if (e.key === 'Escape' && _habitsOpen) closeHabits(); };
  document.addEventListener('keydown', _habitsEsc);

  _renderHabits();
}

function _doCloseHabits() {
  _habitsOpen = false;
  const modal = document.getElementById('habits-modal');
  if (modal) {
    const content = modal.querySelector('.modal-content');
    if (content) {
      content.classList.add('modal-closing');
      content.addEventListener('animationend', () => modal.remove(), { once: true });
      setTimeout(() => { if (modal.parentElement) modal.remove(); }, 250);
    } else { modal.remove(); }
  }
  if (_habitsEsc) { document.removeEventListener('keydown', _habitsEsc); _habitsEsc = null; }
}

export function closeHabits() {
  if (!_habitsOpen && !Modals.isMinimized('habits-modal')) return;
  if (Modals.isRegistered('habits-modal')) Modals.close('habits-modal');
  else _doCloseHabits();
}

export function isHabitsOpen() {
  if (Modals.isMinimized('habits-modal')) return false;
  return _habitsOpen;
}

const healthModule = { openHealth, closeHealth, isHealthOpen, openHabits, closeHabits, isHabitsOpen };
export default healthModule;
