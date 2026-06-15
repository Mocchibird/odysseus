"""Scheduler timezone resolution: notification/ping TEXT renders in the owner's
persisted timezone, while SCHEDULING (compute_next_run) is left alone so a user
task's UTC scheduled_time never shifts.
"""
import types

import pytest

from src.task_scheduler import _resolve_task_timezone, _resolve_task_display_tz


def _task(owner="u1", crew_member_id=None):
    return types.SimpleNamespace(owner=owner, crew_member_id=crew_member_id)


@pytest.fixture
def temp_prefs(tmp_path, monkeypatch):
    pf = tmp_path / "user_prefs.json"
    monkeypatch.setattr("routes.prefs_routes.PREFS_FILE", str(pf))
    return pf


def test_scheduling_tz_stays_none_for_user_task(temp_prefs):
    # Even with a persisted display tz, the SCHEDULING resolver must stay None
    # for a non-crew task, or compute_next_run would reinterpret the UTC
    # scheduled_time as local and shift the task.
    from src.user_time import persist_user_timezone
    persist_user_timezone("u1", "Europe/Zurich")
    assert _resolve_task_timezone(None, _task()) is None


def test_display_tz_falls_back_to_owner_pref(temp_prefs):
    from src.user_time import persist_user_timezone
    persist_user_timezone("u1", "Europe/Zurich")
    assert _resolve_task_display_tz(None, _task()) == "Europe/Zurich"


def test_display_tz_none_when_unpersisted(temp_prefs):
    assert _resolve_task_display_tz(None, _task(owner="nobody")) is None
