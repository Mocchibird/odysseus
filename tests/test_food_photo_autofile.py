"""Food-photo logging: robust gallery-ingest extension handling, and the
auto-file of a logged meal's photo into the "Food Journal" album."""

import pytest

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import routes.chat_routes as cr
import src.gallery_ingest as gi
import src.health_store as hs
from src.gallery_ingest import _pick_ext


def test_pick_ext_prefers_name_then_mime():
    # Known extension on the name wins (case-insensitive).
    assert _pick_ext("IMG_1234.JPG", "") == "jpg"
    assert _pick_ext("clip.mov", "video/quicktime") == "mov"
    # No usable extension (pasted screenshot) → fall back to the mime type.
    assert _pick_ext("screenshot", "image/png") == "png"
    assert _pick_ext("blob", "image/jpeg") == "jpg"
    assert _pick_ext("Screenshot 2026", "image/png; charset=binary") == "png"
    # Neither yields a supported type → "" (caller rejects).
    assert _pick_ext("noext", "") == ""
    assert _pick_ext("photo.heic", "image/heic") == ""


class _FakeUploadHandler:
    def resolve_upload(self, att_id, owner=None):
        return {"id": att_id, "name": "lunch.jpg", "mime": "image/jpeg"}

    def is_image_file(self, name, mime=None):
        return True


def test_link_meal_photo_autofiles_food_journal(monkeypatch):
    """Logging a meal with one attached photo links it to the meal AND files it
    into the Food Journal album — server-side, no model second step."""
    calls = {}
    monkeypatch.setattr(hs, "update_meal", lambda owner, mid, **kw: True)
    monkeypatch.setattr(
        gi, "ingest_upload",
        lambda owner, uid, **kw: calls.update(owner=owner, uid=uid, album=kw.get("album")) or {"id": "g1"},
    )
    res = cr._link_meal_photo("alice", {"id": 7}, ["u1"], _FakeUploadHandler())
    assert res == "u1"
    assert calls == {"owner": "alice", "uid": "u1", "album": "Food Journal"}


def test_link_meal_photo_skips_when_ambiguous(monkeypatch):
    """0 or >1 images → don't link or auto-file (avoid mis-attaching)."""
    called = {"ingest": False}
    monkeypatch.setattr(hs, "update_meal", lambda *a, **k: True)
    monkeypatch.setattr(gi, "ingest_upload", lambda *a, **k: called.update(ingest=True))

    class _TwoImg(_FakeUploadHandler):
        pass

    res = cr._link_meal_photo("alice", {"id": 7}, ["u1", "u2"], _TwoImg())
    assert res is None
    assert called["ingest"] is False
