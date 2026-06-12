"""Static-asset serving with version-aware cache headers.

Kept dependency-light (only Starlette's StaticFiles) so it can be imported in
tests without dragging in the full app import chain.
"""

from starlette.staticfiles import StaticFiles


class RevalidatingStatic(StaticFiles):
    """Serve static assets with cache headers keyed on whether the request
    URL carries a `?v=` version token.

    - Versioned requests (`/static/app.js?v=414`): the token IS the cache key —
      a deploy that changes the file also bumps the token at every reference
      site (the repo's established discipline), so the bytes for a given URL
      never change. Serve them `immutable` so the browser (and any proxy/CDN)
      never revalidates: this removes ~20 render-blocking conditional requests
      per page load (including the megabyte-class style.css), each of which
      would otherwise traverse the full middleware stack.
    - Bare requests (`/static/js/ui.js`): no token, so force REVALIDATION on
      every load (`no-cache` keeps cached bytes but requires a conditional
      request; unchanged files still 304 cheaply via ETag/Last-Modified).
      This is what lets un-versioned modules update on a normal reload."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html")):
            versioned = b"v=" in (scope.get("query_string") or b"")
            resp.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if versioned else "no-cache"
            )
        return resp
