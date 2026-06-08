"""Functional tests for the native Health/Habits/Training store.

Exercises owner-scoping, habit streak/heatmap, calorie rollups, weight trend,
and the Mifflin-St Jeor TDEE math against the conftest in-memory SQLite.
"""
from datetime import date, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from core.database import Base, engine  # noqa: E402
from src import health_store as hs  # noqa: E402


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.create_all(bind=engine)
    yield


def test_habit_create_toggle_streak_heatmap():
    owner = "alice-habits"
    h = hs.create_habit(owner, "Meditate", icon="🧘")
    hid = h["id"]
    today = date.today().isoformat()
    yest = (date.today() - timedelta(days=1)).isoformat()

    hs.set_habit_day(owner, hid, day=yest, done=True)
    assert hs.set_habit_day(owner, hid, day=today)["done"] is True  # toggle on

    listed = hs.list_habits(owner)
    assert listed[0]["done_today"] is True
    assert listed[0]["streak"] == 2

    hm = hs.habit_heatmap(owner, hid, days=30)
    assert hm["total"] == 2
    assert len(hm["days"]) == 30

    assert hs.set_habit_day(owner, hid, day=today)["done"] is False  # toggle off


def test_owner_isolation():
    hs.create_habit("iso-a", "A only")
    hs.create_habit("iso-b", "B only")
    assert [h["name"] for h in hs.list_habits("iso-a")] == ["A only"]
    assert [h["name"] for h in hs.list_habits("iso-b")] == ["B only"]


def test_meals_and_daily_calories():
    owner = "alice-meals"
    hs.log_meal(owner, "Oatmeal", 350, protein_g=12)
    hs.log_meal(owner, "Salad", 450, protein_g=20)
    cal = hs.daily_calories(owner)
    assert cal["total_kcal"] == 800
    assert cal["meal_count"] == 2
    assert cal["protein_g"] == 32.0
    series = hs.calorie_series(owner, days=7)
    assert len(series) == 7
    assert series[-1]["kcal"] == 800


def test_weight_trend():
    owner = "alice-weight"
    today = date.today().isoformat()
    hs.log_weight(owner, 80.0, measured_at=(date.today() - timedelta(days=10)).isoformat() + "T08:00:00")
    hs.log_weight(owner, 79.0, measured_at=today + "T08:00:00")
    tr = hs.weight_trend(owner, days=30)
    assert tr["count"] == 2
    assert tr["delta_kg"] == -1.0
    assert len(tr["series"]) == 2


def test_tdee_computed_and_manual_override():
    owner = "alice-tdee"
    hs.log_weight(owner, 79.0)
    hs.set_profile(
        owner, height_cm=180, date_of_birth="1995-01-01", sex="M",
        activity_level="moderately_active", target_weekly_loss_kg=0.5,
    )
    t = hs.tdee(owner)
    assert t["basis"] == "computed"
    assert 2600 < t["tdee"] < 2850          # BMR×1.55 with the above inputs
    assert t["target_kcal"] < t["tdee"]      # weekly-loss deficit applied

    hs.set_profile(owner, daily_kcal_target=2000)
    assert hs.tdee(owner)["target_kcal"] == 2000


def test_training_log():
    owner = "alice-train"
    hs.log_training(owner, "Strength", duration_min=60, rpe=8, summary="Squats")
    sessions = hs.list_training(owner)
    assert len(sessions) == 1
    assert sessions[0]["kind"] == "Strength"


def test_macro_targets_and_daily():
    owner = "alice-macro"
    hs.set_profile(owner, daily_kcal_target=2000)
    cal = hs.daily_calories(owner)
    assert cal["macro_targets"] == {"protein_g": 150, "carbs_g": 200, "fat_g": 67}


def test_weight_projection():
    owner = "alice-proj"
    hs.set_profile(owner, target_kg=78.0)
    hs.log_weight(owner, 82.0, measured_at=(date.today() - timedelta(days=20)).isoformat() + "T08:00:00")
    hs.log_weight(owner, 80.0, measured_at=date.today().isoformat() + "T08:00:00")
    tr = hs.weight_trend(owner, days=60)
    assert tr["slope_kg_per_week"] < 0
    assert tr.get("projection") and tr["projection"]["eta_date"]


def test_done_7d():
    owner = "alice-7d"
    h = hs.create_habit(owner, "Read")
    hs.set_habit_day(owner, h["id"], day=date.today().isoformat(), done=True)
    hs.set_habit_day(owner, h["id"], day=(date.today() - timedelta(days=2)).isoformat(), done=True)
    assert hs.list_habits(owner)[0]["done_7d"] == 2


def test_csv_export_import():
    owner = "alice-csv"
    hs.log_meal(owner, "Eggs", 200, protein_g=14)
    text = hs.export_csv(owner, "meals")
    assert "Eggs" in text and "kcal" in text
    n = hs.import_csv(owner, "weights", "measured_at,kg,notes\n2026-01-01T08:00:00,79.5,morning\n")
    assert n == 1
    assert any(w["kg"] == 79.5 for w in hs.list_weights(owner, days=4000))
