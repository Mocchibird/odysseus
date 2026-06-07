// Compatibility shim.
// Books now lives as a tab inside the Notes pane. Keep this module path so any
// cached page or old import opens the new tab instead of the removed modal UI.

async function _notesModule() {
  if (window.notesModule?.openBooksPanel) return window.notesModule;
  const mod = await import('./notes.js?v=335');
  return mod.default || window.notesModule;
}

export async function openBooks(initialPath = '') {
  try { document.getElementById('books-modal')?.remove(); } catch (_) {}
  const notes = await _notesModule();
  return notes?.openBooksPanel?.(initialPath);
}

export async function toggleBooks() {
  return openBooks();
}

export function closeBooks() {
  try { document.getElementById('books-modal')?.remove(); } catch (_) {}
  return window.notesModule?.closeNotes?.();
}

export function isBooksOpen() {
  return !!document.querySelector('#notes-pane.notes-pane-books');
}

const api = { openBooks, closeBooks, isBooksOpen, toggleBooks };
window.booksModule = api;
export default api;
