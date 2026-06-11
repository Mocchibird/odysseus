"""manage_gallery — Iris manages the Gallery (tag/rename/album/sort/hide/delete).

Operates on GalleryImage/GalleryAlbum directly, owner-scoped. list finds items +
ids; create_album makes an album; move files an item into one (creating it if
needed); tag/rename/favorite/hide/delete act on a resolved item.
"""
import asyncio
import json

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryImage, GalleryAlbum


@pytest.fixture(autouse=True)
def _gallery_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'g.db'}",
        connect_args={"check_same_thread": False}, poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    SF = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", SF)
    db = SF()
    try:
        db.add_all([
            GalleryImage(id="img1", filename="a.png", prompt="Sunset", model="imported",
                         owner="kim", media_type="image", is_active=True),
            GalleryImage(id="vid1", filename="b.mp4", prompt="Clip", model="imported",
                         owner="kim", media_type="video", is_active=True),
            GalleryImage(id="bobimg", filename="c.png", prompt="Bob pic", model="imported",
                         owner="bob", media_type="image", is_active=True),
        ])
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: None)
    yield SF


def _run(args, owner="kim"):
    from src.tool_implementations import do_manage_gallery
    return asyncio.run(do_manage_gallery(json.dumps(args), owner=owner))


def test_list_is_owner_scoped_and_media_filtered():
    r = _run({"action": "list"})
    assert r["exit_code"] == 0
    assert {i["id"] for i in r["items"]} == {"img1", "vid1"}   # not bob's
    vids = _run({"action": "list", "media_type": "video"})
    assert {i["id"] for i in vids["items"]} == {"vid1"}


def test_create_album_and_move():
    r = _run({"action": "create_album", "name": "Trips"})
    assert r["exit_code"] == 0 and r["album"]["name"] == "Trips"
    m = _run({"action": "move", "id": "img1", "album": "Trips"})
    assert m["exit_code"] == 0
    assert m["item"]["album_id"] == r["album"]["id"]   # filed into the album
    # moving to a not-yet-existing album creates it
    m2 = _run({"action": "move", "id": "vid1", "album": "Clips"})
    assert m2["item"]["album_id"]


def test_tag_rename_favorite_hide_delete(_gallery_db):
    assert _run({"action": "tag", "id": "img1", "tags": ["beach", "beach", "summer"]})["item"]["tags"] == ["beach", "summer"]
    assert _run({"action": "rename", "id": "img1", "name": "Golden Hour"})["item"]["name"] == "Golden Hour"
    assert _run({"action": "favorite", "id": "img1"})["item"]["favorite"] is True
    assert _run({"action": "hide", "id": "img1"})["item"]["hidden"] is True
    assert _run({"action": "unhide", "id": "img1"})["item"]["hidden"] is False
    # delete = soft-delete (drops out of list)
    assert _run({"action": "delete", "id": "vid1"})["exit_code"] == 0
    assert {i["id"] for i in _run({"action": "list"})["items"]} == {"img1"}


def test_resolve_by_unique_name():
    r = _run({"action": "tag", "query": "Sunset", "tags": ["dusk"]})
    assert r["exit_code"] == 0 and r["item"]["id"] == "img1"


def test_owner_scoped_item_actions():
    # kim cannot act on bob's image by id
    r = _run({"action": "rename", "id": "bobimg", "name": "hijack"}, owner="kim")
    assert r["exit_code"] == 1 and "not found" in r["error"].lower()
