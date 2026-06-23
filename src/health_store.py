"""Owner-scoped data access for the native Health / Habits / Training feature.

Single source of truth shared by the REST routes (routes/health_routes.py) and
the agent MCP server (mcp_servers/health_server.py), so the UI and the assistant
read and write exactly the same rows. Everything is keyed by the username string
(``owner``), matching Notes/Calendar — empty string means single-user mode.

Dates are plain ``YYYY-MM-DD`` strings; callers that care about "today" should
pass the client's local date so habit check-ins land on the right day regardless
of server timezone. Event timestamps logged "now" (meal ``eaten_at``, weight
``measured_at``, training ``session_at`` via ``_now_iso``) are stored in
**local wall-clock** time, not UTC, so their date prefix buckets into the same
day as ``date.today()`` and the client's local ``day`` (see ``_now_iso``).
"""
from __future__ import annotations

import csv
import io
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from core.database import (
    SessionLocal,
    Habit,
    HabitLog,
    Meal,
    WeightEntry,
    HealthProfile,
    TrainingSession,
)

ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "lightly_active": 1.375,
    "moderately_active": 1.55,
    "very_active": 1.725,
    "extra_active": 1.9,
}

# Calories per kg of body fat — used to turn a weekly-loss goal into a daily deficit.
KCAL_PER_KG = 7700.0


@contextmanager
def _session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _owner(owner: Optional[str]) -> str:
    return owner or ""


def _today(day: Optional[str] = None) -> str:
    return day or date.today().isoformat()


def _now_iso() -> str:
    # Local wall-clock time, NOT UTC. Meals/weights/training are bucketed into a
    # day by comparing this timestamp's date prefix against date.today() (server-
    # local) and the client's local `day` (the UI sends its _todayLocal()). A UTC
    # timestamp here put entries logged near midnight into the wrong day — e.g. a
    # meal eaten at 21:00 local (already next-day in UTC) vanished from "today"'s
    # calories. Storing local keeps eaten_at in the same frame as the day window.
    return datetime.now().replace(microsecond=0).isoformat()


# ── Habits ──────────────────────────────────────────────────────────────────

def _habit_dict(h: Habit) -> Dict[str, Any]:
    return {
        "id": h.id,
        "name": h.name,
        "category": h.category or "",
        "cadence": h.cadence or "daily",
        "cadence_n": h.cadence_n,
        "target_time": h.target_time or "",
        "color": h.color or "",
        "icon": h.icon or "",
        "description": h.description or "",
        "status": h.status or "active",
        "sort_order": h.sort_order or 0,
    }


def list_habits(owner: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    """Habits with today's done flag, current streak, and a 30-day count."""
    owner = _owner(owner)
    today = _today()
    with _session() as db:
        q = db.query(Habit).filter(Habit.owner == owner)
        if not include_archived:
            q = q.filter(Habit.status == "active")
        habits = q.order_by(Habit.sort_order.asc(), Habit.id.asc()).all()
        # Batch-load all completed-log days for these habits in ONE query (was
        # N+1: a HabitLog query per habit). No date bound — _streak_from_days
        # walks history back indefinitely, so it needs the full set.
        habit_ids = [h.id for h in habits]
        days_by_habit: Dict[int, set] = {hid: set() for hid in habit_ids}
        if habit_ids:
            for habit_id, day in (
                db.query(HabitLog.habit_id, HabitLog.day)
                .filter(HabitLog.habit_id.in_(habit_ids), HabitLog.done == True)  # noqa: E712
                .all()
            ):
                days_by_habit[habit_id].add(day)
        out = []
        for h in habits:
            d = _habit_dict(h)
            done_days = days_by_habit[h.id]
            d["done_today"] = today in done_days
            d["done_yesterday"] = (date.fromisoformat(today) - timedelta(days=1)).isoformat() in done_days
            d["streak"] = _streak_from_days(done_days, today)
            d["done_30d"] = sum(
                1 for ds in done_days if ds >= (date.today() - timedelta(days=30)).isoformat()
            )
            d["done_7d"] = sum(
                1 for ds in done_days if ds >= (date.today() - timedelta(days=6)).isoformat()
            )
            out.append(d)
        return out


def create_habit(owner: str, name: str, **fields) -> Dict[str, Any]:
    owner = _owner(owner)
    name = (name or "").strip()
    if not name:
        raise ValueError("habit name is required")
    with _session() as db:
        max_order = (
            db.query(Habit).filter(Habit.owner == owner).count()
        )
        h = Habit(
            owner=owner,
            name=name,
            category=(fields.get("category") or "").strip(),
            cadence=(fields.get("cadence") or "daily"),
            cadence_n=fields.get("cadence_n"),
            target_time=(fields.get("target_time") or "").strip(),
            color=(fields.get("color") or "").strip(),
            icon=(fields.get("icon") or "").strip(),
            description=(fields.get("description") or "").strip(),
            status="active",
            sort_order=max_order,
        )
        db.add(h)
        db.flush()
        return _habit_dict(h)


def update_habit(owner: str, habit_id: int, **fields) -> Optional[Dict[str, Any]]:
    owner = _owner(owner)
    with _session() as db:
        h = db.query(Habit).filter(Habit.owner == owner, Habit.id == habit_id).first()
        if not h:
            return None
        for key in ("name", "category", "cadence", "target_time", "color", "icon", "description", "status"):
            if key in fields and fields[key] is not None:
                setattr(h, key, fields[key])
        if "cadence_n" in fields:
            h.cadence_n = fields["cadence_n"]
        if "sort_order" in fields and fields["sort_order"] is not None:
            h.sort_order = int(fields["sort_order"])
        db.flush()
        return _habit_dict(h)


def delete_habit(owner: str, habit_id: int) -> bool:
    owner = _owner(owner)
    with _session() as db:
        h = db.query(Habit).filter(Habit.owner == owner, Habit.id == habit_id).first()
        if not h:
            return False
        db.delete(h)
        return True


def _owned_habit(db, owner: str, habit_id: int) -> Optional[Habit]:
    return db.query(Habit).filter(Habit.owner == owner, Habit.id == habit_id).first()


def set_habit_day(
    owner: str,
    habit_id: int,
    day: Optional[str] = None,
    done: Optional[bool] = None,
    duration_min: Optional[int] = None,
    notes: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Upsert a day's log. With ``done=None`` it toggles the current state."""
    owner = _owner(owner)
    day = _today(day)
    with _session() as db:
        if not _owned_habit(db, owner, habit_id):
            return None
        log = (
            db.query(HabitLog)
            .filter(HabitLog.habit_id == habit_id, HabitLog.day == day)
            .first()
        )
        if log is None:
            new_done = True if done is None else bool(done)
            log = HabitLog(
                habit_id=habit_id, day=day, done=new_done,
                duration_min=duration_min, notes=notes or "", created_at=datetime.utcnow(),
            )
            db.add(log)
        else:
            log.done = (not log.done) if done is None else bool(done)
            if duration_min is not None:
                log.duration_min = duration_min
            if notes is not None:
                log.notes = notes
        db.flush()
        return {"habit_id": habit_id, "day": day, "done": bool(log.done)}


def _streak_from_days(done_days: set, today: str) -> int:
    """Consecutive completed days ending today or yesterday."""
    if not done_days:
        return 0
    cur = date.fromisoformat(today)
    # Allow the streak to "survive" today not being marked yet.
    if cur.isoformat() not in done_days:
        cur = cur - timedelta(days=1)
    streak = 0
    while cur.isoformat() in done_days:
        streak += 1
        cur = cur - timedelta(days=1)
    return streak


def habit_heatmap(owner: str, habit_id: int, days: int = 365) -> Dict[str, Any]:
    """GitHub-style data: a flat list of {day, done} from start..today."""
    owner = _owner(owner)
    days = max(1, min(int(days or 365), 730))
    end = date.today()
    start = end - timedelta(days=days - 1)
    with _session() as db:
        if not _owned_habit(db, owner, habit_id):
            return {"habit_id": habit_id, "start": start.isoformat(), "end": end.isoformat(), "days": []}
        done_days = {
            r.day for r in db.query(HabitLog.day)
            .filter(
                HabitLog.habit_id == habit_id,
                HabitLog.done == True,  # noqa: E712
                HabitLog.day >= start.isoformat(),
            ).all()
        }
    cells = []
    cur = start
    while cur <= end:
        ds = cur.isoformat()
        cells.append({"day": ds, "done": ds in done_days})
        cur += timedelta(days=1)
    return {
        "habit_id": habit_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": sum(1 for c in cells if c["done"]),
        "streak": _streak_from_days(done_days, end.isoformat()),
        "days": cells,
    }


# ── Meals / calories ─────────────────────────────────────────────────────────

def _meal_dict(m: Meal) -> Dict[str, Any]:
    return {
        "id": m.id,
        "eaten_at": m.eaten_at,
        "description": m.description,
        "kcal": m.kcal or 0,
        "protein_g": m.protein_g,
        "carbs_g": m.carbs_g,
        "fat_g": m.fat_g,
        "sugar_g": m.sugar_g,
        "source": m.source or "manual",
        "notes": m.notes or "",
        "photo_upload_id": m.photo_upload_id or None,
    }


def log_meal(owner: str, description: str, kcal: int, **fields) -> Dict[str, Any]:
    owner = _owner(owner)
    with _session() as db:
        m = Meal(
            owner=owner,
            eaten_at=fields.get("eaten_at") or _now_iso(),
            description=(description or "").strip(),
            kcal=int(kcal or 0),
            protein_g=fields.get("protein_g"),
            carbs_g=fields.get("carbs_g"),
            fat_g=fields.get("fat_g"),
            sugar_g=fields.get("sugar_g"),
            source=fields.get("source") or "manual",
            notes=(fields.get("notes") or "").strip(),
            photo_upload_id=(fields.get("photo_upload_id") or None),
        )
        db.add(m)
        db.flush()
        return _meal_dict(m)


def list_meals(owner: str, day: Optional[str] = None, days: int = 1) -> List[Dict[str, Any]]:
    owner = _owner(owner)
    with _session() as db:
        q = db.query(Meal).filter(Meal.owner == owner)
        if day:
            q = q.filter(Meal.eaten_at >= day, Meal.eaten_at < day + "T99")
        else:
            since = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
            q = q.filter(Meal.eaten_at >= since)
        return [_meal_dict(m) for m in q.order_by(Meal.eaten_at.desc()).all()]


def delete_meal(owner: str, meal_id: int) -> bool:
    owner = _owner(owner)
    with _session() as db:
        m = db.query(Meal).filter(Meal.owner == owner, Meal.id == meal_id).first()
        if not m:
            return False
        db.delete(m)
        return True


def update_meal(owner: str, meal_id: int, **fields) -> Optional[Dict[str, Any]]:
    """Edit a logged meal (owner-scoped). Only the provided, non-None fields
    change. Returns the updated meal dict, or None if the meal isn't found."""
    owner = _owner(owner)
    with _session() as db:
        m = db.query(Meal).filter(Meal.owner == owner, Meal.id == meal_id).first()
        if not m:
            return None
        if fields.get("description") is not None:
            m.description = str(fields["description"]).strip()
        if fields.get("kcal") is not None:
            m.kcal = int(fields["kcal"] or 0)
        for k in ("protein_g", "carbs_g", "fat_g", "sugar_g"):
            if k in fields:  # explicit None clears it (the edit form sends all macros)
                setattr(m, k, fields[k])
        if fields.get("eaten_at"):
            m.eaten_at = fields["eaten_at"]
        if fields.get("notes") is not None:
            m.notes = str(fields["notes"]).strip()
        if "photo_upload_id" in fields:  # truthy sets it, "" / None clears it
            pid = fields["photo_upload_id"]
            m.photo_upload_id = (str(pid).strip() or None) if pid else None
        db.flush()
        return _meal_dict(m)


def macro_targets(target_kcal: Optional[int]) -> Optional[Dict[str, int]]:
    """Default macro split from a calorie target: 30% protein / 40% carbs /
    30% fat by energy, converted to grams (4/4/9 kcal per gram)."""
    if not target_kcal:
        return None
    return {
        "protein_g": round(target_kcal * 0.30 / 4),
        "carbs_g": round(target_kcal * 0.40 / 4),
        "fat_g": round(target_kcal * 0.30 / 9),
    }


# Fraction of calories burned in training that's credited back to the day's
# eating budget. Exercise earns you some extra intake, but not 1:1 (burn
# estimates run high), so the default is half.
TRAINING_BURN_CREDIT = float(os.getenv("ODYSSEUS_TRAINING_BURN_CREDIT", "0.5") or 0.5)


def _training_burn_for_day(owner: str, day: str) -> int:
    """Sum estimated calories burned across the day's training sessions."""
    with _session() as db:
        rows = (
            db.query(TrainingSession.kcal_burned)
            .filter(
                TrainingSession.owner == owner,
                TrainingSession.session_at >= day,
                TrainingSession.session_at < day + "T99",
            )
            .all()
        )
    return sum(int(r[0] or 0) for r in rows)


def daily_calories(owner: str, day: Optional[str] = None) -> Dict[str, Any]:
    owner = _owner(owner)
    day = _today(day)
    meals = list_meals(owner, day=day)
    total = sum(int(m["kcal"] or 0) for m in meals)
    macro = lambda k: round(sum(float(m[k] or 0) for m in meals), 1)  # noqa: E731
    target = tdee(owner).get("target_kcal")
    # Credit part of today's exercise burn back to the eating budget.
    burned = _training_burn_for_day(owner, day)
    burn_credit = round(burned * TRAINING_BURN_CREDIT)
    adjusted_target = (target + burn_credit) if target else None
    return {
        "day": day,
        "total_kcal": total,
        "meal_count": len(meals),
        "protein_g": macro("protein_g"),
        "carbs_g": macro("carbs_g"),
        "fat_g": macro("fat_g"),
        "sugar_g": macro("sugar_g"),
        "target_kcal": target,
        "kcal_burned": burned,
        "burn_credit": burn_credit,
        "burn_credit_ratio": TRAINING_BURN_CREDIT,
        "adjusted_target_kcal": adjusted_target,
        "remaining_kcal": (adjusted_target - total) if adjusted_target else None,
        "macro_targets": macro_targets(target),
        "meals": meals,
    }


def calorie_series(owner: str, days: int = 14) -> List[Dict[str, Any]]:
    """Per-day calorie totals over the last ``days`` days (oldest first)."""
    owner = _owner(owner)
    days = max(1, min(int(days or 14), 365))
    start = date.today() - timedelta(days=days - 1)
    with _session() as db:
        rows = (
            db.query(Meal.eaten_at, Meal.kcal)
            .filter(Meal.owner == owner, Meal.eaten_at >= start.isoformat())
            .all()
        )
    by_day: Dict[str, int] = {}
    for eaten_at, kcal in rows:
        d = (eaten_at or "")[:10]
        by_day[d] = by_day.get(d, 0) + int(kcal or 0)
    out = []
    cur = start
    while cur <= date.today():
        ds = cur.isoformat()
        out.append({"day": ds, "kcal": by_day.get(ds, 0)})
        cur += timedelta(days=1)
    return out


# ── Weight ───────────────────────────────────────────────────────────────────

def _weight_dict(w: WeightEntry) -> Dict[str, Any]:
    return {"id": w.id, "measured_at": w.measured_at, "kg": w.kg, "notes": w.notes or "", "source": w.source or "manual"}


def log_weight(owner: str, kg: float, measured_at: Optional[str] = None, notes: str = "") -> Dict[str, Any]:
    owner = _owner(owner)
    if kg is None:
        raise ValueError("kg is required")
    with _session() as db:
        w = WeightEntry(
            owner=owner, measured_at=measured_at or _now_iso(),
            kg=float(kg), notes=(notes or "").strip(), source="manual",
        )
        db.add(w)
        db.flush()
        return _weight_dict(w)


def list_weights(owner: str, days: int = 180) -> List[Dict[str, Any]]:
    owner = _owner(owner)
    days = max(1, min(int(days or 180), 2000))
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with _session() as db:
        rows = (
            db.query(WeightEntry)
            .filter(WeightEntry.owner == owner, WeightEntry.measured_at >= since)
            .order_by(WeightEntry.measured_at.asc())
            .all()
        )
        return [_weight_dict(w) for w in rows]


def delete_weight(owner: str, entry_id: int) -> bool:
    owner = _owner(owner)
    with _session() as db:
        w = db.query(WeightEntry).filter(WeightEntry.owner == owner, WeightEntry.id == entry_id).first()
        if not w:
            return False
        db.delete(w)
        return True


def update_weight(owner: str, entry_id: int, **fields) -> Optional[Dict[str, Any]]:
    """Edit a weight entry (owner-scoped). Only the provided, non-None fields
    change. Returns the updated dict, or None if the entry isn't found."""
    owner = _owner(owner)
    with _session() as db:
        w = db.query(WeightEntry).filter(WeightEntry.owner == owner, WeightEntry.id == entry_id).first()
        if not w:
            return None
        if fields.get("kg") is not None:
            w.kg = float(fields["kg"])
        if fields.get("measured_at"):
            w.measured_at = fields["measured_at"]
        if fields.get("notes") is not None:
            w.notes = str(fields["notes"]).strip()
        db.flush()
        return _weight_dict(w)


def _linfit_slope(xs: List[float], ys: List[float]) -> Optional[float]:
    """Least-squares slope of y over x (no numpy). None if undetermined."""
    n = len(xs)
    if n < 2:
        return None
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def weight_trend(owner: str, days: int = 90) -> Dict[str, Any]:
    series = list_weights(owner, days=days)
    if not series:
        return {"count": 0, "series": []}
    first, last = series[0], series[-1]
    profile = get_profile(owner)
    pts = [{"day": (w["measured_at"] or "")[:10], "kg": w["kg"]} for w in series]
    out = {
        "count": len(series),
        "first_kg": first["kg"],
        "last_kg": last["kg"],
        "delta_kg": round(last["kg"] - first["kg"], 2),
        "target_kg": profile.get("target_kg"),
        "series": pts,
    }

    # Linear projection: fit kg over elapsed days, project ETA to the target.
    xs: List[float] = []
    ys: List[float] = []
    d0: Optional[date] = None
    for p in pts:
        try:
            d = date.fromisoformat(p["day"])
        except (ValueError, TypeError):
            continue
        if d0 is None:
            d0 = d
        xs.append((d - d0).days)
        ys.append(p["kg"])
    slope = _linfit_slope(xs, ys)  # kg/day
    if slope is not None:
        out["slope_kg_per_week"] = round(slope * 7, 3)
        target_kg = profile.get("target_kg")
        if target_kg and abs(slope) > 1e-6:
            remaining = float(target_kg) - last["kg"]
            moving_toward = (remaining < 0 and slope < 0) or (remaining > 0 and slope > 0)
            if moving_toward:
                days_to = remaining / slope
                if 0 < days_to <= 3650:
                    eta = (date.today() + timedelta(days=round(days_to))).isoformat()
                    out["projection"] = {"target_kg": float(target_kg), "eta_date": eta, "days": round(days_to)}
            elif abs(remaining) > 0.05:
                out["projection"] = {"target_kg": float(target_kg), "eta_date": None, "days": None, "off_track": True}
    return out


# ── Profile / TDEE ───────────────────────────────────────────────────────────

def _profile_dict(p: HealthProfile) -> Dict[str, Any]:
    return {
        "height_cm": p.height_cm,
        "date_of_birth": p.date_of_birth,
        "sex": p.sex,
        "activity_level": p.activity_level or "moderately_active",
        "target_kg": p.target_kg,
        "target_weekly_loss_kg": p.target_weekly_loss_kg,
        "daily_kcal_target": p.daily_kcal_target,
        "notes": p.notes or "",
    }


def get_profile(owner: str) -> Dict[str, Any]:
    owner = _owner(owner)
    with _session() as db:
        p = db.query(HealthProfile).filter(HealthProfile.owner == owner).first()
        return _profile_dict(p) if p else {
            "height_cm": None, "date_of_birth": None, "sex": None,
            "activity_level": "moderately_active", "target_kg": None,
            "target_weekly_loss_kg": None, "daily_kcal_target": None, "notes": "",
        }


def set_profile(owner: str, **fields) -> Dict[str, Any]:
    owner = _owner(owner)
    with _session() as db:
        p = db.query(HealthProfile).filter(HealthProfile.owner == owner).first()
        if p is None:
            p = HealthProfile(owner=owner)
            db.add(p)
        for key in ("height_cm", "date_of_birth", "sex", "activity_level",
                    "target_kg", "target_weekly_loss_kg", "daily_kcal_target", "notes"):
            if key in fields:
                setattr(p, key, fields[key])
        db.flush()
        return _profile_dict(p)


def _latest_weight_kg(owner: str) -> Optional[float]:
    with _session() as db:
        w = (
            db.query(WeightEntry)
            .filter(WeightEntry.owner == owner)
            .order_by(WeightEntry.measured_at.desc())
            .first()
        )
        return w.kg if w else None


def _age_from_dob(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    try:
        b = date.fromisoformat(dob[:10])
    except ValueError:
        return None
    today = date.today()
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def tdee(owner: str) -> Dict[str, Any]:
    """Mifflin-St Jeor BMR × activity factor, plus a calorie target.

    Returns {bmr, tdee, target_kcal, basis} — values are None when the profile
    lacks the inputs (height/DOB/sex/weight).
    """
    owner = _owner(owner)
    p = get_profile(owner)
    if p.get("daily_kcal_target"):
        return {"bmr": None, "tdee": None, "target_kcal": int(p["daily_kcal_target"]), "basis": "manual"}
    kg = _latest_weight_kg(owner)
    height = p.get("height_cm")
    age = _age_from_dob(p.get("date_of_birth"))
    sex = (p.get("sex") or "").upper()
    if not (kg and height and age and sex in ("M", "F")):
        return {"bmr": None, "tdee": None, "target_kcal": None, "basis": "insufficient_profile"}
    bmr = 10 * kg + 6.25 * height - 5 * age + (5 if sex == "M" else -161)
    factor = ACTIVITY_FACTORS.get(p.get("activity_level") or "moderately_active", 1.55)
    tdee_val = bmr * factor
    target = tdee_val
    if p.get("target_weekly_loss_kg"):
        target = tdee_val - (float(p["target_weekly_loss_kg"]) * KCAL_PER_KG / 7.0)
    return {
        "bmr": round(bmr),
        "tdee": round(tdee_val),
        "target_kcal": round(target),
        "basis": "computed",
    }


# ── Training ─────────────────────────────────────────────────────────────────

def _training_dict(t: TrainingSession) -> Dict[str, Any]:
    return {
        "id": t.id,
        "session_at": t.session_at,
        "kind": t.kind or "",
        "duration_min": t.duration_min,
        "rpe": t.rpe,
        "kcal_burned": t.kcal_burned,
        "summary": t.summary or "",
        "photo_upload_id": t.photo_upload_id or None,
    }


def log_training(owner: str, kind: str, **fields) -> Dict[str, Any]:
    owner = _owner(owner)
    with _session() as db:
        t = TrainingSession(
            owner=owner,
            session_at=fields.get("session_at") or _now_iso(),
            kind=(kind or "").strip(),
            duration_min=fields.get("duration_min"),
            rpe=fields.get("rpe"),
            kcal_burned=fields.get("kcal_burned"),
            summary=(fields.get("summary") or "").strip(),
            photo_upload_id=(fields.get("photo_upload_id") or None),
        )
        db.add(t)
        db.flush()
        return _training_dict(t)


def list_training(owner: str, days: int = 30) -> List[Dict[str, Any]]:
    owner = _owner(owner)
    days = max(1, min(int(days or 30), 730))
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with _session() as db:
        rows = (
            db.query(TrainingSession)
            .filter(TrainingSession.owner == owner, TrainingSession.session_at >= since)
            .order_by(TrainingSession.session_at.desc())
            .all()
        )
        return [_training_dict(t) for t in rows]


def delete_training(owner: str, session_id: int) -> bool:
    owner = _owner(owner)
    with _session() as db:
        t = db.query(TrainingSession).filter(
            TrainingSession.owner == owner, TrainingSession.id == session_id
        ).first()
        if not t:
            return False
        db.delete(t)
        return True


def update_training(owner: str, session_id: int, **fields) -> Optional[Dict[str, Any]]:
    """Edit a training session (owner-scoped). String fields change only when
    provided non-None; duration_min/rpe/kcal_burned use key-presence so the edit
    form can blank a number to clear it. Returns the dict, or None if not found."""
    owner = _owner(owner)
    with _session() as db:
        t = db.query(TrainingSession).filter(
            TrainingSession.owner == owner, TrainingSession.id == session_id
        ).first()
        if not t:
            return None
        if fields.get("kind") is not None:
            t.kind = str(fields["kind"]).strip()
        for k in ("duration_min", "rpe", "kcal_burned"):
            if k in fields:  # explicit None clears it (the edit form sends all numbers)
                setattr(t, k, fields[k])
        if fields.get("summary") is not None:
            t.summary = str(fields["summary"]).strip()
        if fields.get("session_at"):
            t.session_at = fields["session_at"]
        if "photo_upload_id" in fields:  # truthy sets it, "" / None clears it
            pid = fields["photo_upload_id"]
            t.photo_upload_id = (str(pid).strip() or None) if pid else None
        db.flush()
        return _training_dict(t)


# ── CSV export / import ───────────────────────────────────────────────────────

_CSV_FIELDS = {
    "meals": ["eaten_at", "description", "kcal", "protein_g", "carbs_g", "fat_g", "sugar_g", "notes"],
    "weights": ["measured_at", "kg", "notes"],
    "training": ["session_at", "kind", "duration_min", "rpe", "kcal_burned", "summary"],
}


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


def export_csv(owner: str, kind: str) -> str:
    kind = (kind or "").strip().lower()
    fields = _CSV_FIELDS.get(kind)
    if not fields:
        raise ValueError(f"unknown export kind: {kind}")
    if kind == "meals":
        rows = list_meals(owner, days=3650)
    elif kind == "weights":
        rows = list_weights(owner, days=3650)
    else:
        rows = list_training(owner, days=3650)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (r.get(k) if r.get(k) is not None else "") for k in fields})
    return buf.getvalue()


def import_csv(owner: str, kind: str, text: str) -> int:
    """Append rows from a CSV (matching export_csv columns). Returns count imported."""
    kind = (kind or "").strip().lower()
    if kind not in _CSV_FIELDS:
        raise ValueError(f"unknown import kind: {kind}")
    reader = csv.DictReader(io.StringIO(text or ""))
    n = 0
    for row in reader:
        try:
            if kind == "meals":
                desc = (row.get("description") or "").strip()
                if not desc and not row.get("kcal"):
                    continue
                log_meal(
                    owner, desc, _i(row.get("kcal")) or 0,
                    eaten_at=(row.get("eaten_at") or "").strip() or None,
                    protein_g=_f(row.get("protein_g")), carbs_g=_f(row.get("carbs_g")),
                    fat_g=_f(row.get("fat_g")), sugar_g=_f(row.get("sugar_g")),
                    notes=(row.get("notes") or "").strip(), source="csv",
                )
            elif kind == "weights":
                kg = _f(row.get("kg"))
                if kg is None:
                    continue
                log_weight(owner, kg, measured_at=(row.get("measured_at") or "").strip() or None,
                           notes=(row.get("notes") or "").strip())
            else:
                kind_v = (row.get("kind") or "").strip()
                if not kind_v:
                    continue
                log_training(owner, kind_v, session_at=(row.get("session_at") or "").strip() or None,
                             duration_min=_i(row.get("duration_min")), rpe=_i(row.get("rpe")),
                             kcal_burned=_i(row.get("kcal_burned")),
                             summary=(row.get("summary") or "").strip())
            n += 1
        except Exception:
            continue
    return n
