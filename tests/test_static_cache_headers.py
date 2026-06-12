"""_RevalidatingStatic cache-header contract.

Versioned static asset URLs (`?v=<token>`) are immutable by design — the repo
discipline bumps the token at every reference site whenever the file changes,
so the bytes behind a given versioned URL never change and the browser must
never revalidate them. Bare URLs have no such key and must stay `no-cache`
(revalidate-every-load) so un-versioned modules update on a normal reload.
Regression guard: if either header regresses, every page load re-issues ~20
render-blocking conditional requests (versioned case) or deploys silently
serve stale modules (bare case).
"""
from starlette.applications import Starlette
from starlette.testclient import TestClient

from src.static_serving import RevalidatingStatic


def _client() -> TestClient:
    app = Starlette()
    app.mount("/static", RevalidatingStatic(directory="static"), name="static")
    return TestClient(app)


def test_versioned_js_is_immutable():
    client = _client()
    resp = client.get("/static/app.js?v=999")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_versioned_css_is_immutable():
    client = _client()
    resp = client.get("/static/style.css?v=999")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_bare_js_revalidates():
    client = _client()
    resp = client.get("/static/js/ui.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_non_source_assets_untouched():
    # Non .js/.css/.html files keep StaticFiles' default headers — the
    # override must not stamp Cache-Control on them either way.
    client = _client()
    resp = client.get("/static/manifest.json")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") != "no-cache"
