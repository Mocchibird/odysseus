// static/js/writer/menus.js
//
// FORK-ONLY. The "…" action menus on document and folder rows.
//
// A body-appended popover rather than a menu nested in the row: the list is a
// scroller with overflow hidden, so an in-flow menu would be clipped by it.
// Positioning flips above the anchor when there is no room below, which matters
// for the last rows in a long list.
//
// Dismissal reuses the app's own escMenuStack so Escape and click-away behave
// exactly like every other popover in Odysseus, and so nested menus unwind in
// the right order.

import { bindMenuDismiss, dismissOrRemove, topPopupZ } from '../escMenuStack.js';

let _open = null;   // the live menu element, if any

export function closeMenu() {
  if (_open) { dismissOrRemove(_open); _open = null; }
}

function _position(menu, anchor) {
  const r = anchor.getBoundingClientRect();
  menu.style.position = 'fixed';
  menu.style.zIndex = String(topPopupZ());
  const mh = menu.offsetHeight || 0;
  const mw = menu.offsetWidth || 170;
  const vh = window.innerHeight;
  const vw = window.innerWidth;
  let top;
  if (vh - r.bottom >= mh + 8) top = r.bottom + 4;        // fits below
  else if (r.top >= mh + 8) top = r.top - mh - 4;         // flip above
  else top = vh - mh - 8;                                 // fits neither: pin
  const left = Math.min(r.left, vw - mw - 8);
  menu.style.top = `${Math.round(Math.max(8, top))}px`;
  menu.style.left = `${Math.round(Math.max(8, left))}px`;
}

/**
 * @param {HTMLElement} anchor  the "…" button
 * @param {Array<{label: string, danger?: boolean, run: () => any} | 'sep'>} items
 */
export function openMenu(anchor, items) {
  closeMenu();
  const menu = document.createElement('div');
  menu.className = 'writer-menu';
  for (const item of items) {
    if (item === 'sep') {
      const sep = document.createElement('div');
      sep.className = 'writer-menu-sep';
      menu.appendChild(sep);
      continue;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'writer-menu-item' + (item.danger ? ' danger' : '');
    btn.textContent = item.label;
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      closeMenu();
      try { await item.run(); } catch (e) { console.error('[writer] menu action failed', e); }
    });
    menu.appendChild(btn);
  }
  document.body.appendChild(menu);
  _position(menu, anchor);          // measured only after it is in the DOM
  // onClose is where the node is REMOVED — bindMenuDismiss only invokes the
  // callback, it does not touch the DOM itself (see its own docs). Leaving the
  // removal out leaks a menu into <body> on every open.
  // isOutside receives the EVENT, not the target.
  bindMenuDismiss(
    menu,
    () => { menu.remove(); _open = null; },
    (ev) => !menu.contains(ev.target) && !anchor.contains(ev.target),
  );
  _open = menu;
  return menu;
}

/** The "…" button itself, so rows build it the same way. */
export function actionButton(title, onOpen) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'writer-row-actions';
  b.title = title;
  b.setAttribute('aria-label', title);
  b.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">'
    + '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>';
  b.addEventListener('click', (ev) => {
    ev.stopPropagation();          // never let it open/expand the row underneath
    ev.preventDefault();
    onOpen(b);
  });
  return b;
}

export default { openMenu, closeMenu, actionButton };
