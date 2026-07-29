"""Regression guard for the blank-chat-history bug.

sessions.js `_renderHistorySlice` renders each past message with
`markdownModule.renderContent(...)`. The module reference must be imported, or
EVERY history message throws `ReferenceError: markdownModule is not defined` and
the chat opens blank (while the session is still selected — so typing has
context, which made it especially confusing). The import had been missing.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sessions_js_imports_markdownmodule_if_it_uses_it():
    src = (ROOT / "static" / "js" / "sessions.js").read_text(encoding="utf-8")
    if "markdownModule." not in src:
        # If the render path stops using it, the import is no longer required.
        return
    assert "import markdownModule from './markdown.js'" in src, (
        "sessions.js references markdownModule but never imports it — past-chat "
        "history will render blank (ReferenceError in _renderHistorySlice)."
    )
