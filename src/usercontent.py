"""Serve user-uploaded HTML as full-capability standalone pages on a SEPARATE
origin (``ODYSSEUS_USERCONTENT_ORIGIN``), the way Deep Research reports are full
pages — but safely.

Why a separate origin + a token instead of the session cookie:
  A sibling hostname (e.g. files.example.com) never receives the app's
  host-only session cookie, so the untrusted HTML can't read the user's app
  session, cookies, or localStorage. That isolation is the whole point — but it
  also means the login cookie can't authorize the view. Access is therefore
  gated by an unguessable token embedded in the URL (the "unlisted link" model,
  same idea as the task-webhook URLs): whoever holds the link can view it.

Stateless by design: the token is ``file_id ~ HMAC(secret, file_id)`` — so there
is NO database column, NO migration, and NO reverse-lookup table. The signature
proves the id wasn't forged; the id is looked up directly. Rotating the secret
(``ODYSSEUS_USERCONTENT_SECRET`` or deleting the key file) revokes every link at
once.
"""
import hashlib
import hmac
import logging
import os
import string
from typing import Optional
from urllib.parse import urlsplit

from src.constants import DATA_DIR, USERCONTENT_ORIGIN

logger = logging.getLogger(__name__)

# Hex chars of the HMAC kept in the URL. 32 hex = 128 bits — unguessable, yet
# keeps the link short.
_SIG_LEN = 32
_SEP = "~"  # file_id is uuid4 hex (no '~'), so rpartition is unambiguous.
_HEX = set(string.hexdigits)

_secret_cache: Optional[bytes] = None
_config_warned = False


def _app_hostname() -> str:
    """The app's own public hostname, from ODYSSEUS_PUBLIC_URL (empty if unset)."""
    pub = (os.getenv("ODYSSEUS_PUBLIC_URL") or "").strip()
    if not pub:
        return ""
    return (urlsplit(pub).hostname or "").lower()


def is_enabled() -> bool:
    """True only when the content origin is a valid, ISOLATED separate origin.

    Fails CLOSED (feature disabled + a one-time error log) if the origin is
    missing, not an absolute http(s) URL, or shares the app's own host
    (ODYSSEUS_PUBLIC_URL). Serving untrusted user HTML on the app's origin would
    let it read the app session — so a misconfiguration must DISABLE the feature,
    never silently drop its only isolation boundary.
    """
    global _config_warned
    if not USERCONTENT_ORIGIN:
        return False
    parts = urlsplit(USERCONTENT_ORIGIN)
    host = (parts.hostname or "").lower()
    problem = None
    if parts.scheme not in ("http", "https") or not host:
        problem = (f"ODYSSEUS_USERCONTENT_ORIGIN must be an absolute URL like "
                   f"https://files.example.com (got {USERCONTENT_ORIGIN!r})")
    else:
        app_host = _app_hostname()
        if app_host and host == app_host:
            problem = (f"ODYSSEUS_USERCONTENT_ORIGIN host {host!r} must DIFFER from "
                       f"the app host {app_host!r} — serving untrusted HTML "
                       f"same-origin as the app is unsafe")
    if problem:
        if not _config_warned:
            logger.error("Standalone HTML pages DISABLED: %s", problem)
            _config_warned = True
        return False
    return True


def content_hostname() -> str:
    """Hostname (no port) of the configured content origin, lowercased."""
    if not USERCONTENT_ORIGIN:
        return ""
    return (urlsplit(USERCONTENT_ORIGIN).hostname or "").lower()


def _secret() -> bytes:
    """Persistent HMAC signing secret. Precedence: env override, then a key file
    under DATA_DIR generated once (so links survive restarts). Falls back to an
    in-memory key only if the file can't be written (links then reset on
    restart, which is safe, just inconvenient)."""
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache

    env = os.getenv("ODYSSEUS_USERCONTENT_SECRET")
    if env:
        _secret_cache = env.encode()
        return _secret_cache

    key_path = os.path.join(DATA_DIR, "usercontent_signing.key")
    try:
        with open(key_path, "r") as fh:
            val = fh.read().strip()
        if val:
            _secret_cache = val.encode()
            return _secret_cache
    except OSError:
        pass

    val = os.urandom(32).hex()
    try:
        # O_EXCL so two workers racing to create it don't clobber each other.
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(val)
    except FileExistsError:
        try:
            with open(key_path, "r") as fh:
                val = fh.read().strip() or val
        except OSError:
            pass
    except OSError as e:
        logger.warning("usercontent signing key not persisted (%s); links reset on restart", e)

    _secret_cache = val.encode()
    return _secret_cache


def _sig(file_id: str) -> str:
    return hmac.new(_secret(), file_id.encode(), hashlib.sha256).hexdigest()[:_SIG_LEN]


def make_token(file_id: str) -> str:
    """`file_id~sig` — the opaque, unguessable path segment for a share link."""
    return f"{file_id}{_SEP}{_sig(file_id)}"


def verify_token(token: str) -> Optional[str]:
    """Return the file_id iff the token's signature is valid, else None.
    Constant-time compare so the signature can't be brute-forced by timing."""
    if not token or _SEP not in token:
        return None
    file_id, _, sig = token.rpartition(_SEP)
    # Reject anything that isn't exactly our hex signature shape BEFORE the
    # compare: hmac.compare_digest raises TypeError on a non-ASCII str, so a
    # crafted token like "a~<non-ascii>" would otherwise 500 instead of 404.
    if not file_id or len(sig) != _SIG_LEN or any(c not in _HEX for c in sig):
        return None
    if not hmac.compare_digest(sig, _sig(file_id)):
        return None
    return file_id


def _is_html(rec: dict) -> bool:
    fn = (rec.get("filename") or "").lower()
    mime = (rec.get("mime") or "").lower()
    return fn.endswith(".html") or fn.endswith(".htm") or "html" in mime


def standalone_url(rec: dict) -> Optional[str]:
    """The full content-origin share URL for an HTML file record, or None when
    the feature is off / the file isn't HTML / it has no stored bytes."""
    if not is_enabled() or not rec:
        return None
    file_id = rec.get("id")
    if not file_id or not rec.get("has_file") or not _is_html(rec):
        return None
    return f"{USERCONTENT_ORIGIN}/f/{make_token(file_id)}"
