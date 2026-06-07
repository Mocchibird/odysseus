"""Signed, expiring tokens for ntfy reminder action buttons (snooze/done).

The token IS the credential: /api/notes/reminder-action is auth-exempt (like the
task webhook URLs) and trusts a valid token instead of a session cookie, so a tap
from the ntfy app on the user's phone can act without logging in.

Tokens are authenticated-encrypted with the app secret (src/secret_storage,
Fernet), so they can't be forged or tampered, and carry an expiry. The token
binds the note + owner; the specific action (done/snooze) rides as an unsigned
query param because all three actions are benign operations on the user's own
note — without a valid token the endpoint rejects the request outright.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from src import secret_storage

_SEP = "|"
_NS = "rta1"  # namespace/version tag so a token can't be repurposed elsewhere
DEFAULT_TTL = 7 * 24 * 3600  # a push can linger on a phone for days

# The actions the endpoint accepts (unsigned `do=` param is validated against this).
VALID_ACTIONS = ("done", "snooze1h", "tomorrow")


def mint(note_id: str, owner: str, ttl_seconds: int = DEFAULT_TTL) -> str:
    exp = int(time.time()) + int(ttl_seconds)
    payload = _SEP.join([_NS, str(note_id), str(owner or ""), str(exp)])
    return secret_storage.encrypt(payload)


def verify(token: str) -> Optional[Tuple[str, str]]:
    """Return (note_id, owner) for a valid, unexpired token, else None.

    Rejects anything not genuinely encrypted with our key — secret_storage.decrypt
    passes plaintext through, so we MUST gate on is_encrypted first to avoid a
    forged plaintext token being accepted.
    """
    if not token or not secret_storage.is_encrypted(token):
        return None
    try:
        payload = secret_storage.decrypt(token)
    except Exception:
        return None
    if not payload:
        return None
    parts = payload.split(_SEP)
    if len(parts) != 4 or parts[0] != _NS:
        return None
    _, note_id, owner, exp = parts
    try:
        if int(exp) < int(time.time()):
            return None
    except ValueError:
        return None
    return note_id, owner
