"""REST API for the native Health / Habits / Training feature.

Thin owner-scoped wrapper over src/health_store.py. The same store backs the
agent MCP server (mcp_servers/health_server.py), so the UI and the assistant
share one set of rows.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.auth_helpers import require_user
from src import health_store as hs


def setup_health_routes():
    router = APIRouter(prefix="/api/health", tags=["health"])

    def _owner(request: Request) -> str:
        # Owner-scoped data: gate anonymous callers in multi-user mode.
        return require_user(request)

    # ── Habits ───────────────────────────────────────────────────────────────
    @router.get("/habits")
    def list_habits(request: Request, include_archived: bool = False):
        return {"habits": hs.list_habits(_owner(request), include_archived=include_archived)}

    @router.post("/habits")
    async def create_habit(request: Request):
        owner = _owner(request)
        body = await request.json()
        try:
            habit = hs.create_habit(owner, body.get("name", ""), **{
                k: body.get(k) for k in
                ("category", "cadence", "cadence_n", "target_time", "color", "icon", "description")
            })
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "habit": habit}

    @router.put("/habits/{habit_id}")
    async def update_habit(habit_id: int, request: Request):
        owner = _owner(request)
        body = await request.json()
        habit = hs.update_habit(owner, habit_id, **body)
        if not habit:
            raise HTTPException(404, "Habit not found")
        return {"ok": True, "habit": habit}

    @router.delete("/habits/{habit_id}")
    def delete_habit(habit_id: int, request: Request):
        if not hs.delete_habit(_owner(request), habit_id):
            raise HTTPException(404, "Habit not found")
        return {"ok": True}

    @router.post("/habits/{habit_id}/check")
    async def check_habit(habit_id: int, request: Request):
        owner = _owner(request)
        body = await request.json() if request.headers.get("content-length") else {}
        res = hs.set_habit_day(
            owner, habit_id,
            day=body.get("day"),
            done=body.get("done"),
            duration_min=body.get("duration_min"),
            notes=body.get("notes"),
        )
        if res is None:
            raise HTTPException(404, "Habit not found")
        return {"ok": True, **res}

    @router.get("/habits/{habit_id}/heatmap")
    def habit_heatmap(habit_id: int, request: Request, days: int = 365):
        return hs.habit_heatmap(_owner(request), habit_id, days=days)

    # ── Calories / meals ─────────────────────────────────────────────────────
    @router.get("/calories")
    def calories(request: Request, date: str = ""):
        return hs.daily_calories(_owner(request), day=date or None)

    @router.get("/calories/series")
    def calorie_series(request: Request, days: int = 14):
        return {"series": hs.calorie_series(_owner(request), days=days)}

    @router.get("/meals")
    def list_meals(request: Request, day: str = "", days: int = 1):
        return {"meals": hs.list_meals(_owner(request), day=day or None, days=days)}

    @router.post("/meals")
    async def log_meal(request: Request):
        owner = _owner(request)
        body = await request.json()
        meal = hs.log_meal(
            owner, body.get("description", ""), body.get("kcal", 0),
            **{k: body.get(k) for k in ("eaten_at", "protein_g", "carbs_g", "fat_g", "source", "notes")},
        )
        return {"ok": True, "meal": meal}

    @router.delete("/meals/{meal_id}")
    def delete_meal(meal_id: int, request: Request):
        if not hs.delete_meal(_owner(request), meal_id):
            raise HTTPException(404, "Meal not found")
        return {"ok": True}

    # ── Weight ───────────────────────────────────────────────────────────────
    @router.get("/weights")
    def list_weights(request: Request, days: int = 180):
        return {"weights": hs.list_weights(_owner(request), days=days)}

    @router.get("/weights/trend")
    def weight_trend(request: Request, days: int = 90):
        return hs.weight_trend(_owner(request), days=days)

    @router.post("/weights")
    async def log_weight(request: Request):
        owner = _owner(request)
        body = await request.json()
        try:
            entry = hs.log_weight(owner, body.get("kg"), measured_at=body.get("measured_at"), notes=body.get("notes", ""))
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e) or "kg is required")
        return {"ok": True, "weight": entry}

    @router.delete("/weights/{entry_id}")
    def delete_weight(entry_id: int, request: Request):
        if not hs.delete_weight(_owner(request), entry_id):
            raise HTTPException(404, "Weight entry not found")
        return {"ok": True}

    # ── Profile / TDEE ───────────────────────────────────────────────────────
    @router.get("/profile")
    def get_profile(request: Request):
        owner = _owner(request)
        return {"profile": hs.get_profile(owner), "tdee": hs.tdee(owner)}

    @router.put("/profile")
    async def set_profile(request: Request):
        owner = _owner(request)
        body = await request.json()
        profile = hs.set_profile(owner, **body)
        return {"ok": True, "profile": profile, "tdee": hs.tdee(owner)}

    # ── Training ─────────────────────────────────────────────────────────────
    @router.get("/training")
    def list_training(request: Request, days: int = 30):
        return {"sessions": hs.list_training(_owner(request), days=days)}

    @router.post("/training")
    async def log_training(request: Request):
        owner = _owner(request)
        body = await request.json()
        session = hs.log_training(owner, body.get("kind", ""), **{
            k: body.get(k) for k in ("session_at", "duration_min", "rpe", "summary")
        })
        return {"ok": True, "session": session}

    @router.delete("/training/{session_id}")
    def delete_training(session_id: int, request: Request):
        if not hs.delete_training(_owner(request), session_id):
            raise HTTPException(404, "Training session not found")
        return {"ok": True}

    # ── CSV export / import ──────────────────────────────────────────────────
    @router.get("/export")
    def export_csv(request: Request, kind: str = "meals"):
        owner = _owner(request)
        try:
            text = hs.export_csv(owner, kind)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return PlainTextResponse(
            text, media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="health-{kind}.csv"'},
        )

    @router.post("/import")
    async def import_csv(request: Request, kind: str = "meals"):
        owner = _owner(request)
        raw = await request.body()
        text = raw.decode("utf-8", "replace") if raw else ""
        try:
            n = hs.import_csv(owner, kind, text)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "imported": n}

    # ── Combined dashboard snapshot ──────────────────────────────────────────
    @router.get("/summary")
    def summary(request: Request):
        owner = _owner(request)
        return {
            "habits": hs.list_habits(owner),
            "calories": hs.daily_calories(owner),
            "weight": hs.weight_trend(owner, days=90),
            "tdee": hs.tdee(owner),
        }

    return router
