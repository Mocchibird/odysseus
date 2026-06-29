// static/js/mdShortcuts.js
// ============================================================================
// A "/" slash-insert menu for the markdown editor — type "/" at the start of a
// line to insert a markdown block (heading, list, checklist, quote, code,
// table, divider). Original implementation of this well-known pattern.
//
// The editor ALREADY provides the other "Super"-style niceties on its textarea
// (list/checklist/quote continuation on Enter, Tab/Shift-Tab indent, and
// Cmd/Ctrl-B/I/K inline formatting — document.js:5054). We deliberately do NOT
// duplicate those; this module only adds the slash menu. While the menu is open
// it intercepts Arrow/Enter/Tab/Esc in the CAPTURE phase so they drive the menu
// instead of the editor's own keydown handler (which would otherwise minimise
// the panel on Esc or continue a list on Enter). Every mutation re-fires
// 'input' so the highlight overlay + autosave stay in sync.
// ============================================================================

import { bindMenuDismiss, topPopupZ } from './escMenuStack.js';

function _ic(paths) {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}

// Insertable blocks. `text` replaces the typed "/filter" at the line start;
// `caret` (optional) offsets the cursor into the inserted snippet.
const BLOCKS = [
  { key: 'h1',      label: 'Heading 1',     text: '# ',     icon: _ic('<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M17 12l3-2v8"/>') },
  { key: 'h2',      label: 'Heading 2',     text: '## ',    icon: _ic('<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M21 18h-4c0-2 4-3 4-6 0-1.5-1-2-2-2"/>') },
  { key: 'h3',      label: 'Heading 3',     text: '### ',   icon: _ic('<path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M17 9c1-1 4-1 4 1.5 0 1.5-2 1.5-2 1.5s2 0 2 1.5c0 2.5-3 2.5-4 1.5"/>') },
  { key: 'ul',      label: 'Bulleted list', text: '- ',     icon: _ic('<line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>') },
  { key: 'ol',      label: 'Numbered list', text: '1. ',    icon: _ic('<line x1="10" y1="6" x2="21" y2="6"/><line x1="10" y1="12" x2="21" y2="12"/><line x1="10" y1="18" x2="21" y2="18"/><path d="M4 6h1v4"/><path d="M4 10h2"/>') },
  { key: 'todo',    label: 'Checklist',     text: '- [ ] ', icon: _ic('<polyline points="3 6 4.5 7.5 7 4.5"/><polyline points="3 13 4.5 14.5 7 11.5"/><line x1="11" y1="6" x2="21" y2="6"/><line x1="11" y1="13" x2="21" y2="13"/>') },
  { key: 'quote',   label: 'Quote',         text: '> ',     icon: _ic('<path d="M6 17h3l2-4V7H5v6h3z"/><path d="M14 17h3l2-4V7h-6v6h3z"/>') },
  { key: 'code',    label: 'Code block',    text: '```\n\n```', caret: 4, icon: _ic('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>') },
  { key: 'table',   label: 'Table',         text: '| Column | Column |\n| --- | --- |\n|  |  |', caret: 2, icon: _ic('<rect x="3" y="3" width="18" height="18" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="12" y1="3" x2="12" y2="21"/>') },
  { key: 'divider', label: 'Divider',       text: '---\n',  icon: _ic('<line x1="3" y1="12" x2="21" y2="12"/>') },
];

let _state = null;   // { ta, slashPos, menu, filtered, index, close }

function _fireInput(ta) { ta.dispatchEvent(new Event('input', { bubbles: true })); }
function _lineStart(v, p) { return v.lastIndexOf('\n', p - 1) + 1; }

function _isMarkdown() {
  const sel = document.getElementById('doc-language-select');
  const lang = (sel && sel.value || 'markdown').toLowerCase();
  return lang === 'markdown' || lang === 'text' || lang === '';
}

// Caret pixel position via a mirror div that replicates the textarea's metrics.
function _caretXY(ta) {
  const cs = getComputedStyle(ta);
  const div = document.createElement('div');
  ['fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
   'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
   'borderTopWidth', 'borderLeftWidth', 'textTransform', 'tabSize'].forEach(p => { div.style[p] = cs[p]; });
  div.style.position = 'absolute';
  div.style.visibility = 'hidden';
  div.style.whiteSpace = 'pre-wrap';
  div.style.wordWrap = 'break-word';
  div.style.boxSizing = 'border-box';
  div.style.width = ta.clientWidth + 'px';
  div.textContent = ta.value.slice(0, ta.selectionStart);
  const span = document.createElement('span');
  span.textContent = '/';
  div.appendChild(span);
  document.body.appendChild(div);
  const r = ta.getBoundingClientRect();
  const lh = parseFloat(cs.lineHeight) || (parseFloat(cs.fontSize) * 1.5);
  const xy = { x: r.left + span.offsetLeft - ta.scrollLeft, y: r.top + span.offsetTop - ta.scrollTop, lineHeight: lh };
  div.remove();
  return xy;
}

function _renderMenu() {
  if (!_state) return;
  const { menu, filtered, index } = _state;
  menu.innerHTML = '';
  filtered.forEach((b, i) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'md-slash-item' + (i === index ? ' active' : '');
    row.innerHTML = b.icon + `<span>${b.label}</span>`;
    row.addEventListener('mousedown', (ev) => { ev.preventDefault(); _apply(b); });
    row.addEventListener('mousemove', () => { if (_state) { _state.index = i; _paintActive(); } });
    menu.appendChild(row);
  });
}

function _paintActive() {
  if (!_state) return;
  [..._state.menu.children].forEach((row, i) => row.classList.toggle('active', i === _state.index));
}

function _filter() {
  if (!_state) return;
  const { ta, slashPos } = _state;
  const q = ta.value.slice(slashPos + 1, ta.selectionStart).toLowerCase();
  if (/\s/.test(q)) { _closeSlash(); return; }   // a space cancels the menu
  _state.filtered = BLOCKS.filter(b => b.label.toLowerCase().includes(q) || b.key.includes(q));
  if (!_state.filtered.length) { _closeSlash(); return; }
  _state.index = 0;
  _renderMenu();
}

function _apply(b) {
  if (!_state) return;
  const { ta, slashPos } = _state;
  ta.setRangeText(b.text, slashPos, ta.selectionStart, 'end');
  if (b.caret != null) ta.selectionStart = ta.selectionEnd = slashPos + b.caret;
  _closeSlash();
  _fireInput(ta);
  ta.focus();
}

function _openSlash(ta, slashPos) {
  _closeSlash();
  const menu = document.createElement('div');
  menu.className = 'md-slash-menu';
  menu.setAttribute('role', 'listbox');
  document.body.appendChild(menu);
  const { x, y, lineHeight } = _caretXY(ta);
  menu.style.left = Math.round(Math.max(8, x)) + 'px';
  menu.style.top = Math.round(y + lineHeight + 2) + 'px';
  menu.style.zIndex = String(topPopupZ());
  _state = { ta, slashPos, menu, filtered: BLOCKS.slice(), index: 0, close: null };
  _renderMenu();
  _state.close = bindMenuDismiss(menu, () => { if (menu.parentNode) menu.remove(); _state = null; });
}

function _closeSlash() {
  if (!_state) return;
  const close = _state.close, menu = _state.menu;
  _state = null;
  try { if (close) close(); } catch (_) {}
  if (menu && menu.parentNode) menu.remove();
}

// Capture-phase keydown: ONLY active while the menu is open, so it pre-empts the
// editor's own textarea keydown (Esc-minimise / Enter-list-continue).
function _onKeydownCapture(e) {
  if (!_state) return;
  if (e.key === 'ArrowDown') { e.preventDefault(); e.stopImmediatePropagation(); _state.index = (_state.index + 1) % _state.filtered.length; _paintActive(); return; }
  if (e.key === 'ArrowUp') { e.preventDefault(); e.stopImmediatePropagation(); _state.index = (_state.index - 1 + _state.filtered.length) % _state.filtered.length; _paintActive(); return; }
  if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); e.stopImmediatePropagation(); _apply(_state.filtered[_state.index]); return; }
  if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); _closeSlash(); return; }
  // Any other key (typing/backspace) passes through → updates the textarea →
  // 'input' re-filters or closes the menu.
}

function _onInput(ta) {
  if (_state) {
    if (ta.selectionStart <= _state.slashPos || ta.value[_state.slashPos] !== '/') { _closeSlash(); return; }
    _filter();
    return;
  }
  if (!_isMarkdown()) return;
  const pos = ta.selectionStart;
  if (pos > 0 && ta.value[pos - 1] === '/') {
    const ls = _lineStart(ta.value, pos);
    if (/^\s*$/.test(ta.value.slice(ls, pos - 1))) _openSlash(ta, pos - 1);
  }
}

/** Attach the slash-insert menu to a textarea (idempotent). */
export function attachMdShortcuts(ta) {
  if (!ta || ta.dataset.mdShortcuts === '1') return;
  ta.dataset.mdShortcuts = '1';
  ta.addEventListener('keydown', _onKeydownCapture, true);   // capture phase
  ta.addEventListener('input', () => _onInput(ta));
  ta.addEventListener('scroll', () => { if (_state) _closeSlash(); });
  ta.addEventListener('blur', () => { setTimeout(() => { if (_state) _closeSlash(); }, 150); });
}

export default { attachMdShortcuts };
