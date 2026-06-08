/**
 * Shared window setup for centered .modal tool windows. Gives Health / Habits /
 * Pings / Today the SAME behavior as the rest of Odysseus instead of each
 * rolling its own makeWindowDraggable call:
 *   - drag the header to the top edge → maximize (Aero Snap)
 *   - drag away from the top while maximized → restore
 *   - drag to the left/right edge → dock
 *
 * This is the email-library window pattern (static/js/emailLibrary.js
 * `_makeDraggable`), factored out so it can be reused verbatim.
 */
import { makeWindowDraggable } from './windowDrag.js';

export function makeToolModalDraggable(modal, { fsClass = 'modal-fullscreen' } = {}) {
  if (!modal) return;
  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  if (!content || !header) return;

  const enterFullscreen = () => {
    if (modal.classList.contains(fsClass)) return;
    modal.classList.add(fsClass);
    Object.assign(content.style, {
      position: 'fixed', left: '0', top: '0', right: '0', bottom: '0',
      width: '100vw', maxWidth: '100vw', height: '100vh', maxHeight: '100vh',
      borderRadius: '0', transform: 'none', margin: '0',
    });
  };
  const exitFullscreen = () => {
    if (!modal.classList.contains(fsClass)) return;
    modal.classList.remove(fsClass);
    // Clear the inline fullscreen styles → fall back to the centered CSS layout.
    Object.assign(content.style, {
      position: '', left: '', top: '', right: '', bottom: '',
      width: '', maxWidth: '', height: '', maxHeight: '', borderRadius: '',
      transform: '', margin: '',
    });
  };

  makeWindowDraggable(modal, {
    content,
    header,
    fsClass,
    enableLeftDock: true,   // dock to either edge, like the email/notes windows
    onEnterFullscreen: enterFullscreen,
    onExitFullscreen: exitFullscreen,
  });
}

const modalFullscreenModule = { makeToolModalDraggable };
export default modalFullscreenModule;
