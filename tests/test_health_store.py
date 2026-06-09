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


def test_update_meal_edits_fields_and_clears_macros():
    owner = "alice-edit"
    m = hs.log_meal(owner, "Lnch", 600, protein_g=20, carbs_g=50)
    mid = m["id"]
    upd = hs.update_meal(owner, mid, description="Lunch", kcal=550, protein_g=30, carbs_g=None)
    assert upd is not None
    assert upd["description"] == "Lunch" and upd["kcal"] == 550
    assert upd["protein_g"] == 30
    assert upd["carbs_g"] is None  # passing the key with None clears it (edit form blanks a macro)
    assert hs.daily_calories(owner)["total_kcal"] == 550  # totals reflect the edit
    # owner-scoped + missing
    assert hs.update_meal("someone-else", mid, kcal=1) is None
    assert hs.update_meal(owner, 999999, kcal=1) is None


def test_update_meal_leaves_omitted_macros_untouched():
    owner = "alice-edit2"
    m = hs.log_meal(owner, "Snack", 200, protein_g=5, fat_g=8)
    upd = hs.update_meal(owner, m["id"], kcal=250)  # macros not passed → unchanged
    assert upd["kcal"] == 250 and upd["protein_g"] == 5 and upd["fat_g"] == 8


def test_list_habits_reports_done_yesterday():
    owner = "alice-yday"
    h = hs.create_habit(owner, "Stretch")
    yest = (date.today() - timedelta(days=1)).isoformat()
    hs.set_habit_day(owner, h["id"], day=yest, done=True)
    listed = hs.list_habits(owner)[0]
    assert listed["done_yesterday"] is True
    assert listed["done_today"] is False


def test_manage_health_tool_update_and_delete_meal():
    """The manage_health tool can fix a logged meal (so Iris can correct itself):
    log → calories lists it with its #id → update_meal → delete_meal."""
    import asyncio
    import json
    from src.tool_implementations import do_manage_health

    owner = "tool-edit"
    logged = asyncio.run(do_manage_health(json.dumps({"action": "log_meal", "description": "Pizza", "kcal": 800}), owner=owner))
    mid = logged["meal"]["id"]
    # calories enumerates the meal WITH its id (so Iris can target it)
    cals = asyncio.run(do_manage_health(json.dumps({"action": "calories"}), owner=owner))
    assert f"#{mid}" in cals["output"]
    # update
    upd = asyncio.run(do_manage_health(json.dumps({"action": "update_meal", "meal_id": mid, "kcal": 700}), owner=owner))
    assert upd["exit_code"] == 0 and upd["meal"]["kcal"] == 700
    # needs an id
    assert asyncio.run(do_manage_health(json.dumps({"action": "update_meal"}), owner=owner))["exit_code"] == 1
    # delete
    assert asyncio.run(do_manage_health(json.dumps({"action": "delete_meal", "meal_id": mid}), owner=owner))["exit_code"] == 0
    assert hs.daily_calories(owner)["total_kcal"] == 0


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
