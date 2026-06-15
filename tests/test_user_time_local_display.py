"""Background-context timezone resolution + UTC->local event display.

These cover the path that broke the daily brief: a scheduled action runs with
no browser request, so it must resolve the user's persisted timezone and
convert stored UTC event times to the user's local clock (while leaving
floating local-time events untouched).
"""
from datetime import datetime

import pytest

from src.user_time import (
    clear_user_time_context,
    event_local_clock,
    event_local_datetime,
    get_user_timezone,
    persist_user_timezone,
    resolve_owner_tzinfo,
    set_user_tz_name,
    _safe_zone_name,
)


def teardown_function():
    clear_user_time_context()


@pytest.fixture
def temp_prefs(tmp_path, monkeypatch):
    """Point the per-user prefs store at a temp file."""
    pf = tmp_path / "user_prefs.json"
    monkeypatch.setattr("routes.prefs_routes.PREFS_FILE", str(pf))
    return pf


# ── pure converters ──────────────────────────────────────────────────────────

def test_utc_event_converts_to_local_clock():
    tz = resolve_owner_tzinfo("")  # falls back, but we pass an explicit zone
    from zoneinfo import ZoneInfo
    zurich = ZoneInfo("Europe/Zurich")
    # 17:00 UTC in summer (CEST, +2) == 19:00 local — the physio bug.
    dt = datetime(2026, 6, 15, 17, 0)
    assert event_local_clock(dt, True, zurich) == "19:00"


def test_floating_event_shown_as_stored():
    from zoneinfo import ZoneInfo
    zurich = ZoneInfo("Europe/Zurich")
    # A floating (is_utc=False) 19:00 means 19:00 wherever you are — no shift.
    dt = datetime(2026, 6, 15, 19, 0)
    assert event_local_clock(dt, False, zurich) == "19:00"


def test_utc_event_winter_offset():
    from zoneinfo import ZoneInfo
    zurich = ZoneInfo("Europe/Zurich")
    # January -> CET (+1): 17:00 UTC == 18:00 local. DST handled by zoneinfo.
    dt = datetime(2026, 1, 15, 17, 0)
    assert event_local_clock(dt, True, zurich) == "18:00"


def test_utc_event_seoul_for_travel():
    from zoneinfo import ZoneInfo
    seoul = ZoneInfo("Asia/Seoul")  # +9, no DST
    dt = datetime(2026, 6, 15, 17, 0)
    assert event_local_clock(dt, True, seoul) == "02:00"  # next-day 02:00


def test_event_local_datetime_buckets_by_local_day():
    from zoneinfo import ZoneInfo
    seoul = ZoneInfo("Asia/Seoul")
    # 17:00 UTC is already 2026-06-16 02:00 in Seoul.
    local = event_local_datetime(datetime(2026, 6, 15, 17, 0), True, seoul)
    assert local.date().isoformat() == "2026-06-16"


# ── zone validation ──────────────────────────────────────────────────────────

def test_safe_zone_name_rejects_junk_and_strips():
    assert _safe_zone_name("Europe/Zurich") == "Europe/Zurich"
    assert _safe_zone_name("Europe/Zurich\nrm -rf") == "Europe/Zurich"
    assert _safe_zone_name("Not/AZone") is None
    assert _safe_zone_name("") is None
    assert _safe_zone_name(None) is None


# ── persistence + resolution (background path) ───────────────────────────────

def test_persist_and_resolve_user_timezone(temp_prefs):
    persist_user_timezone("alice", "Europe/Zurich")
    assert get_user_timezone("alice") == "Europe/Zurich"
    tz = resolve_owner_tzinfo("alice")
    dt = datetime(2026, 6, 15, 17, 0)
    assert event_local_clock(dt, True, tz) == "19:00"


def test_persist_ignores_invalid_zone(temp_prefs):
    persist_user_timezone("bob", "Mordor/Mount-Doom")
    assert get_user_timezone("bob") is None


def test_persist_follows_travel(temp_prefs):
    persist_user_timezone("carol", "Europe/Zurich")
    assert get_user_timezone("carol") == "Europe/Zurich"
    # Browser now reports Seoul (user travelled) -> pref updates.
    persist_user_timezone("carol", "Asia/Seoul")
    assert get_user_timezone("carol") == "Asia/Seoul"


def test_resolve_falls_back_to_request_context_when_unpersisted(temp_prefs):
    # No persisted pref -> use the per-request browser zone if present.
    set_user_tz_name("Asia/Seoul")
    tz = resolve_owner_tzinfo("dave")
    assert event_local_clock(datetime(2026, 6, 15, 17, 0), True, tz) == "02:00"
