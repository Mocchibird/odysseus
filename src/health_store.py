"""Owner-scoped data access for the native Health / Habits / Training feature.

Single source of truth shared by the REST routes (routes/health_routes.py) and
the agent MCP server (mcp_servers/health_server.py), so the UI and the assistant
read and write exactly the same rows. Everything is keyed by the username string
(``owner``), matching Notes/Calendar — empty string means single-user mode.

Dates are plain ``YYYY-MM-DD`` strings; callers that care about "today" should
pass the client's local date so habit check-ins land on the right day regardless
of server timezone.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
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
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
        out = []
        for h in habits:
            d = _habit_dict(h)
            done_days = {
                row.day
                for row in db.query(HabitLog.day)
                .filter(HabitLog.habit_id == h.id, HabitLog.done == True)  # noqa: E712
                .all()
            }
            d["done_today"] = today in done_days
            d["streak"] = _streak_from_days(done_days, today)
            d["done_30d"] = sum(
                1 for ds in done_days if ds >= (date.today() - timedelta(days=30)).isoformat()
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


def habit_streak(owner: str, habit_id: int) -> int:
    owner = _owner(owner)
    with _session() as db:
        if not _owned_habit(db, owner, habit_id):
            return 0
        done_days = {
            r.day for r in db.query(HabitLog.day)
            .filter(HabitLog.habit_id == habit_id, HabitLog.done == True)  # noqa: E712
            .all()
        }
    return _streak_from_days(done_days, _today())


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
        "source": m.source or "manual",
        "notes": m.notes or "",
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
            source=fields.get("source") or "manual",
            notes=(fields.get("notes") or "").strip(),
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


def daily_calories(owner: str, day: Optional[str] = None) -> Dict[str, Any]:
    owner = _owner(owner)
    day = _today(day)
    meals = list_meals(owner, day=day)
    total = sum(int(m["kcal"] or 0) for m in meals)
    macro = lambda k: round(sum(float(m[k] or 0) for m in meals), 1)  # noqa: E731
    target = tdee(owner).get("target_kcal")
    return {
        "day": day,
        "total_kcal": total,
        "meal_count": len(meals),
        "protein_g": macro("protein_g"),
        "carbs_g": macro("carbs_g"),
        "fat_g": macro("fat_g"),
        "target_kcal": target,
        "remaining_kcal": (target - total) if target else None,
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


def weight_trend(owner: str, days: int = 90) -> Dict[str, Any]:
    series = list_weights(owner, days=days)
    if not series:
        return {"count": 0, "series": []}
    first, last = series[0], series[-1]
    profile = get_profile(owner)
    return {
        "count": len(series),
        "first_kg": first["kg"],
        "last_kg": last["kg"],
        "delta_kg": round(last["kg"] - first["kg"], 2),
        "target_kg": profile.get("target_kg"),
        "series": [{"day": (w["measured_at"] or "")[:10], "kg": w["kg"]} for w in series],
    }


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
        "summary": t.summary or "",
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
            summary=(fields.get("summary") or "").strip(),
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
