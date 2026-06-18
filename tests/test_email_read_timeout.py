"""Guards for the email-read timeout fix.

A busy / re-syncing IMAP server (e.g. proton-bridge during a mass mailbox
cleanup) could make a message-body read hang forever, leaving the reader stuck
on an endless loading spinner. The fix bounds the read on BOTH ends:

  * backend: the /api/email/read route wraps the IMAP read in asyncio.wait_for
    and returns a clear, retryable {error} on timeout (the socket timeout only
    fires on an idle socket, so a slow drip never trips it).
  * frontend: a bounded `_emailReadFetch` helper aborts after a deadline and
    returns a synthetic {error} response, so each reader's existing `data.error`
    path shows a message and clears its spinner instead of hanging.

These are source guards — exercising a real hang would mean a multi-second
sleep; the timeout plumbing itself is simple, so we just assert it's wired.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_read_route_has_server_side_deadline():
    src = (ROOT / "routes" / "email_routes.py").read_text(encoding="utf-8")
    # The read route bounds the IMAP read so it can't hang forever.
    assert "wait_for(" in src
    assert "_read_email_sync" in src
    assert "_IMAP_TIMEOUT_SECONDS + 5" in src
    # Timeout surfaces a clear, retryable error rather than a 500 / hang.
    assert "TimeoutError" in src
    assert "took too long to load" in src


def test_frontend_has_bounded_read_fetch():
    js = (ROOT / "static" / "js" / "emailLibrary.js").read_text(encoding="utf-8")
    # The bounded helper exists and uses an AbortController deadline.
    assert "function _emailReadFetch(" in js
    assert "AbortController" in js
    assert "ctrl.abort()" in js
    # On abort/failure it returns a synthetic {error} so callers' data.error fires.
    assert "took too long to load" in js
    # And the reader open paths actually use it (not a raw fetch that can hang).
    assert "await _emailReadFetch(`${API_BASE}/api/email/read/" in js
    assert "await fetch(`${API_BASE}/api/email/read/" not in js
