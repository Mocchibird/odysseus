from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware


def _client():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/files/{file_id}/view")
    async def files_view(file_id: str):
        return Response(b"<h1>hi</h1>", media_type="text/html; charset=utf-8")

    @app.get("/api/files/{file_id}/raw")
    async def files_raw(file_id: str):
        return Response(b"raw", media_type="text/plain")

    return TestClient(app)


def test_html_file_view_is_sandboxed_to_an_opaque_origin():
    """The Files-tab "live view" serves untrusted user HTML on the app origin.
    It MUST get a `sandbox` CSP WITHOUT allow-same-origin so the rendered page
    lands on an opaque origin and cannot read the app's cookies/localStorage or
    call same-origin APIs with the user's session."""
    response = _client().get("/api/files/abc123/view")

    csp = response.headers["Content-Security-Policy"]
    assert csp.startswith("sandbox")
    # Scripts run for a faithful live view...
    assert "allow-scripts" in csp
    # ...but the origin stays opaque — this is the whole security guarantee.
    assert "allow-same-origin" not in csp


def test_non_view_files_paths_keep_the_default_csp():
    """The sandbox exception is scoped to /view only; /raw (and every other
    files path) keeps the default frame-denied policy."""
    response = _client().get("/api/files/abc123/raw")

    csp = response.headers["Content-Security-Policy"]
    assert "sandbox" not in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["X-Frame-Options"] == "DENY"
