import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_calendar_module():
    yield
    sys.modules.pop("routes.calendar_routes", None)


def _load_calendar_routes(monkeypatch):
    sys.modules.pop("routes.calendar_routes", None)
    core = types.ModuleType("core")
    core.__path__ = []
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.CalendarCal = MagicMock()
    db.CalendarEvent = MagicMock()
    db.CalendarDeletedEvent = MagicMock()
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.database", db)
    return importlib.import_module("routes.calendar_routes")


def test_webcal_url_normalizes_to_https(monkeypatch):
    cr = _load_calendar_routes(monkeypatch)
    monkeypatch.setattr(cr, "_ics_block_private_ips", lambda: False)

    seen = {}

    def fake_check(url, *, block_private=False):
        seen["url"] = url
        seen["block_private"] = block_private
        return True, "ok"

    monkeypatch.setattr("src.url_safety.check_outbound_url", fake_check)

    assert cr._validate_ics_feed_url("webcal://example.com/private/basic.ics") == (
        "https://example.com/private/basic.ics"
    )
    assert seen == {"url": "https://example.com/private/basic.ics", "block_private": False}


def test_ics_url_rejects_non_http_scheme(monkeypatch):
    cr = _load_calendar_routes(monkeypatch)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda url, **kw: (False, "scheme must be http"))

    with pytest.raises(Exception) as exc:
        cr._validate_ics_feed_url("file:///etc/passwd")

    assert getattr(exc.value, "status_code", None) == 400
    assert "Invalid ICS URL" in str(exc.value.detail)


def test_calendar_name_from_url_prefers_filename(monkeypatch):
    cr = _load_calendar_routes(monkeypatch)
    assert cr._calendar_name_from_url("https://calendar.google.com/foo/Work_Calendar.ics") == (
        "Work Calendar"
    )


def test_calendar_import_ui_mentions_link_input():
    src = Path("static/js/calendar.js").read_text(encoding="utf-8")

    assert 'id="cal-import-url"' in src
    assert "Import link" in src
    assert "/api/calendar/import" in src
    assert "JSON.stringify({ url })" in src


def test_ics_links_are_persistent_subscriptions_in_source():
    routes = Path("routes/calendar_routes.py").read_text(encoding="utf-8")
    db = Path("core/database.py").read_text(encoding="utf-8")

    assert "source_url" in db
    assert "sync_enabled" in db
    assert "async def _sync_ics_subscriptions" in routes
    assert "persistent=True" in routes
    assert 'source="ics" if persistent else "import"' in routes
