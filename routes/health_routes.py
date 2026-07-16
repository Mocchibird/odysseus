"""REST API for the native Health / Habits / Training feature.

Thin owner-scoped wrapper over src/health_store.py. The same store backs the
agent MCP server (mcp_servers/health_server.py), so the UI and the assistant
share one set of rows.
"""
import asyncio
import json
import os
import re
import tempfile

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from src.auth_helpers import require_user
from src import health_store as hs


def _parse_meal_json(text: str):
    """Pull a meal estimate out of a vision model's reply (which may wrap JSON
    in prose/markdown)."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    def _num(*keys):
        for k in keys:
            v = obj.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None

    desc = str(obj.get("description") or obj.get("name") or obj.get("food") or "").strip()
    kcal = _num("kcal", "calories")
    if not desc and kcal is None:
        return None
    return {
        "description": desc or "Meal",
        "kcal": int(round(kcal)) if kcal is not None else 0,
        "protein_g": _num("protein_g", "protein"),
        "carbs_g": _num("carbs_g", "carbs", "carbohydrates"),
        "fat_g": _num("fat_g", "fat"),
    }


def setup_health_routes(upload_handler=None):
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
        fields = {k: body.get(k) for k in
                  ("category", "cadence", "cadence_n", "target_time", "color", "icon", "description")}
        try:
            habit = await asyncio.to_thread(hs.create_habit, owner, body.get("name", ""), **fields)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "habit": habit}

    @router.put("/habits/{habit_id}")
    async def update_habit(habit_id: int, request: Request):
        owner = _owner(request)
        body = await request.json()
        habit = await asyncio.to_thread(hs.update_habit, owner, habit_id, **body)
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
        res = await asyncio.to_thread(
            hs.set_habit_day, owner, habit_id,
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

    @router.post("/estimate-meal")
    async def estimate_meal(request: Request, file: UploadFile = File(...)):
        """Estimate a meal's calories/macros from a photo via the vision model.
        Returns an estimate for the user to confirm — does not auto-log."""
        owner = _owner(request)  # gate anonymous
        data = await file.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "Image too large (max 8MB)")
        suffix = os.path.splitext(file.filename or "meal.jpg")[1] or ".jpg"
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(data)
                tmp = tf.name
            from src.document_processor import analyze_image_with_vl_result
            prompt = (
                "You are a nutrition estimator. Estimate the food in this photo for the whole "
                "portion shown. Reply with ONLY a compact JSON object and nothing else: "
                '{"description": "<short dish name>", "kcal": <integer>, '
                '"protein_g": <number>, "carbs_g": <number>, "fat_g": <number>}.'
            )
            res = await asyncio.to_thread(analyze_image_with_vl_result, tmp, owner=owner, prompt=prompt)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        text = (res.get("text") or "").strip()
        if text.startswith("[") and ("model" in text.lower() or "vision" in text.lower()):
            raise HTTPException(503, text.strip("[]"))  # vision disabled / not configured
        est = _parse_meal_json(text)
        if not est:
            raise HTTPException(422, "Couldn't read an estimate from the photo — try a clearer shot or log it manually.")
        # Persist the photo so it can be associated with the meal when the user
        # confirms (the UI sends this id back in POST /meals). Best-effort: a save
        # failure must not lose the estimate the user is waiting on.
        photo_upload_id = None
        if upload_handler is not None:
            try:
                file.file.seek(0)
                meta = upload_handler.save_upload(
                    file, getattr(request.client, "host", "") or "", owner=owner, source="health",
                )
                photo_upload_id = meta.get("id")
            except Exception:
                photo_upload_id = None
        return {"ok": True, "estimate": est, "model": res.get("model", ""), "photo_upload_id": photo_upload_id}

    @router.post("/meals")
    async def log_meal(request: Request):
        owner = _owner(request)
        body = await request.json()
        fields = {k: body.get(k) for k in ("eaten_at", "protein_g", "carbs_g", "fat_g", "sugar_g", "source", "notes", "photo_upload_id")}
        meal = await asyncio.to_thread(hs.log_meal, owner, body.get("description", ""), body.get("kcal", 0), **fields)
        return {"ok": True, "meal": meal}

    @router.put("/meals/{meal_id}")
    async def update_meal(meal_id: int, request: Request):
        owner = _owner(request)
        body = await request.json()
        fields = {k: body[k] for k in ("description", "kcal", "protein_g", "carbs_g", "fat_g", "sugar_g", "eaten_at", "notes", "photo_upload_id") if k in body}
        meal = await asyncio.to_thread(hs.update_meal, owner, meal_id, **fields)
        if meal is None:
            raise HTTPException(404, "Meal not found")
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
            entry = await asyncio.to_thread(
                hs.log_weight, owner, body.get("kg"),
                measured_at=body.get("measured_at"), notes=body.get("notes", ""),
            )
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e) or "kg is required")
        return {"ok": True, "weight": entry}

    @router.put("/weights/{entry_id}")
    async def update_weight(entry_id: int, request: Request):
        owner = _owner(request)
        body = await request.json()
        fields = {k: body[k] for k in ("kg", "measured_at", "notes") if k in body}
        entry = await asyncio.to_thread(hs.update_weight, owner, entry_id, **fields)
        if entry is None:
            raise HTTPException(404, "Weight entry not found")
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
        profile = await asyncio.to_thread(hs.set_profile, owner, **body)
        tdee = await asyncio.to_thread(hs.tdee, owner)
        return {"ok": True, "profile": profile, "tdee": tdee}

    # ── Training ─────────────────────────────────────────────────────────────
    @router.get("/training")
    def list_training(request: Request, days: int = 30):
        return {"sessions": hs.list_training(_owner(request), days=days)}

    @router.post("/training")
    async def log_training(request: Request):
        owner = _owner(request)
        body = await request.json()
        fields = {k: body.get(k) for k in ("session_at", "duration_min", "rpe", "kcal_burned", "summary")}
        session = await asyncio.to_thread(hs.log_training, owner, body.get("kind", ""), **fields)
        return {"ok": True, "session": session}

    @router.put("/training/{session_id}")
    async def update_training(session_id: int, request: Request):
        owner = _owner(request)
        body = await request.json()
        fields = {k: body[k] for k in ("kind", "duration_min", "rpe", "kcal_burned", "summary", "session_at") if k in body}
        session = await asyncio.to_thread(hs.update_training, owner, session_id, **fields)
        if session is None:
            raise HTTPException(404, "Training session not found")
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
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, "CSV too large (max 5MB)")
        text = raw.decode("utf-8", "replace") if raw else ""
        try:
            # Off-thread: import_csv parses every row and opens a SQLite session
            # per row — blocking the event loop for the whole import otherwise.
            result = await asyncio.to_thread(hs.import_csv, owner, kind, text)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, **result}

    return router
