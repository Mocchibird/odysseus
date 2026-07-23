"""Tests for the standalone user-content HTML pages feature (src/usercontent.py
+ the /f/{token} route's security headers).

The end-to-end route (serving, host redirect, auth-exemption) is exercised in
the browser preview against a real server; here we lock the security-critical,
purely-unit-testable pieces: the signed-token scheme and the middleware CSP.
"""
import importlib

import pytest


@pytest.fixture
def uc(monkeypatch):
    """src.usercontent with a fixed origin + secret and a clean secret cache."""
    monkeypatch.setenv("ODYSSEUS_USERCONTENT_SECRET", "unit-test-secret")
    import src.usercontent as usercontent
    importlib.reload(usercontent)
    monkeypatch.setattr(usercontent, "USERCONTENT_ORIGIN", "https://files.example.com")
    usercontent._secret_cache = None
    return usercontent


def test_token_round_trip(uc):
    tok = uc.make_token("abc123")
    assert uc.verify_token(tok) == "abc123"


def test_token_rejects_tampered_signature(uc):
    tok = uc.make_token("abc123")
    file_id, _, _sig = tok.rpartition("~")
    forged = f"{file_id}~{'0' * 32}"
    assert forged != tok
    assert uc.verify_token(forged) is None


def test_token_rejects_swapped_id(uc):
    """A signature is bound to its file id — you can't reuse it for another id."""
    sig = uc.make_token("abc123").rpartition("~")[2]
    assert uc.verify_token(f"deadbeef~{sig}") is None


def test_token_rejects_garbage(uc):
    for bad in ("", "nope", "~", "a~", "~b", "no-separator-here"):
        assert uc.verify_token(bad) is None


def test_token_rejects_non_ascii_signature_without_raising(uc):
    """Regression: hmac.compare_digest raises TypeError on a non-ASCII str, which
    would 500 the /f/ route. verify_token must reject it (return None) first."""
    assert uc.verify_token("abc123~éé") is None          # non-ASCII sig
    assert uc.verify_token("abc123~" + "z" * 32) is None           # right length, non-hex
    assert uc.verify_token("abc123~deadbeef") is None              # hex but too short


def test_disabled_for_scheme_less_origin(uc, monkeypatch):
    """A bare host with no scheme can't yield a usable origin — fail CLOSED."""
    monkeypatch.setattr(uc, "USERCONTENT_ORIGIN", "files.example.com")
    uc._config_warned = False
    assert uc.is_enabled() is False


def test_disabled_when_content_host_equals_app_host(uc, monkeypatch):
    """The whole isolation model requires a DIFFERENT host than the app; if the
    content origin matches ODYSSEUS_PUBLIC_URL's host, the feature must disable."""
    monkeypatch.setattr(uc, "USERCONTENT_ORIGIN", "https://app.example.com")
    monkeypatch.setenv("ODYSSEUS_PUBLIC_URL", "https://app.example.com")
    uc._config_warned = False
    assert uc.is_enabled() is False


def test_enabled_when_content_host_differs_from_app_host(uc, monkeypatch):
    monkeypatch.setattr(uc, "USERCONTENT_ORIGIN", "https://files.example.com")
    monkeypatch.setenv("ODYSSEUS_PUBLIC_URL", "https://app.example.com")
    uc._config_warned = False
    assert uc.is_enabled() is True


def test_standalone_url_only_for_html_with_bytes(uc):
    html = {"id": "abc123", "filename": "page.html", "has_file": True}
    url = uc.standalone_url(html)
    assert url and url.startswith("https://files.example.com/f/abc123~")
    # Not HTML → no standalone page.
    assert uc.standalone_url({"id": "abc123", "filename": "notes.txt", "has_file": True}) is None
    # No stored bytes → nothing to serve.
    assert uc.standalone_url({"id": "abc123", "filename": "page.html", "has_file": False}) is None


def test_standalone_url_none_when_feature_disabled(uc, monkeypatch):
    monkeypatch.setattr(uc, "USERCONTENT_ORIGIN", "")
    assert uc.is_enabled() is False
    assert uc.standalone_url({"id": "abc123", "filename": "page.html", "has_file": True}) is None


def test_content_hostname_strips_scheme_and_port(uc, monkeypatch):
    monkeypatch.setattr(uc, "USERCONTENT_ORIGIN", "https://files.example.com:8443")
    assert uc.content_hostname() == "files.example.com"


# ---- middleware CSP branch -------------------------------------------------

def _headers_client():
    from fastapi import FastAPI
    from fastapi.responses import Response
    from fastapi.testclient import TestClient
    from core.middleware import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/f/{token}")
    async def f(token: str):
        return Response(b"<h1>hi</h1>", media_type="text/html; charset=utf-8")

    @app.get("/plain")
    async def plain():
        return {"ok": True}

    return TestClient(app)


def test_usercontent_page_is_not_sandboxed_or_app_csp():
    """A /f/ standalone page is isolated by its own origin, so it must NOT get
    the sandbox (that would break localStorage) nor the app's restrictive
    default CSP (that would block its scripts/resources)."""
    resp = _headers_client().get("/f/abc123~deadbeef")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "sandbox" not in csp
    assert "default-src 'self'" not in csp
    # And it isn't frame-denied (the default), so it can be embedded if wanted.
    assert resp.headers.get("X-Frame-Options") is None


def test_default_route_still_frame_denied():
    resp = _headers_client().get("/plain")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
