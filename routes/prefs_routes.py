"""User preferences API — per-user key/value store backed by a JSON file."""
import copy
import json
import os
from typing import Optional
from fastapi import APIRouter, Request
from src.auth_helpers import get_current_user
from src.constants import USER_PREFS_FILE

PREFS_FILE = USER_PREFS_FILE

# mtime-guarded cache: /api/prefs is hit on chat init, settings open, model
# switch, etc. — re-reading + json-parsing the whole file every time (on the
# event loop) was pure waste. Re-read only when the file's mtime changes;
# _save() invalidates explicitly so a same-second write can't serve stale data.
_prefs_cache = None
_prefs_cache_mtime = None


def _load():
    """Load the raw prefs file (internal use only). Cached on mtime."""
    global _prefs_cache, _prefs_cache_mtime
    try:
        mtime = os.path.getmtime(PREFS_FILE)
    except OSError:
        _prefs_cache, _prefs_cache_mtime = None, None
        return {}
    if _prefs_cache is not None and mtime == _prefs_cache_mtime:
        # Return a deep copy — callers mutate the result before saving.
        return copy.deepcopy(_prefs_cache)
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    _prefs_cache, _prefs_cache_mtime = data, mtime
    return copy.deepcopy(data)


def _save(prefs):
    global _prefs_cache, _prefs_cache_mtime
    os.makedirs(os.path.dirname(PREFS_FILE) or ".", exist_ok=True)
    tmp = f"{PREFS_FILE}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PREFS_FILE)
    # Invalidate so the next _load() re-reads (mtime resolution can be coarse).
    _prefs_cache, _prefs_cache_mtime = None, None


def _load_for_user(user: Optional[str] = None) -> dict:
    """Load preferences for a specific user."""
    all_prefs = _load()
    if "_users" in all_prefs:
        if user is None:
            # Auth disabled — return first user's prefs for backward compat
            users = all_prefs["_users"]
            return dict(next(iter(users.values()), {}))
        return dict(all_prefs["_users"].get(user, {}))
    # Legacy flat format — return as-is
    return dict(all_prefs)


def _save_for_user(user: Optional[str], prefs: dict):
    """Save preferences for a specific user."""
    all_prefs = _load()
    if user is None:
        # Auth disabled. If the store is already multi-user (e.g. auth was
        # turned off on a deployment that previously ran multi-user), writing
        # `prefs` flat would overwrite the whole `_users` map and destroy every
        # other user's preferences. Instead write back into the same (first)
        # slot _load_for_user(None) reads from, preserving the others.
        if "_users" in all_prefs:
            users = all_prefs["_users"]
            first_key = next(iter(users), None)
            if first_key is not None:
                users[first_key] = prefs
                _save(all_prefs)
                return
        _save(prefs)
        return
    if "_users" not in all_prefs:
        all_prefs = {"_users": {}}
    all_prefs["_users"][user] = prefs
    _save(all_prefs)


def setup_prefs_routes():
    router = APIRouter(prefix="/api/prefs", tags=["preferences"])

    @router.get("")
    async def get_all_prefs(request: Request):
        user = get_current_user(request)
        return _load_for_user(user)

    @router.get("/{key}")
    async def get_pref(request: Request, key: str):
        user = get_current_user(request)
        prefs = _load_for_user(user)
        return {"key": key, "value": prefs.get(key)}

    @router.put("/{key}")
    async def set_pref(request: Request, key: str, body: dict):
        user = get_current_user(request)
        prefs = _load_for_user(user)
        prefs[key] = body.get("value")
        _save_for_user(user, prefs)
        return {"key": key, "value": prefs[key]}

    return router
