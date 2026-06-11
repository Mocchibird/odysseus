"""Gallery video tab + hidden-by-default.

`media_type` lets the gallery serve a Videos tab without sniffing extensions on
every row; `hidden` is the hidden-by-default flag. Listers (library + albums)
must exclude hidden items unless `show_hidden` is passed, hidden images must not
count toward an album's tally or leak into the tag facets, and owner-scoping must
still apply *before* show_hidden (so revealing hidden never crosses owners).
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GalleryAlbum, GalleryImage
import routes.gallery_routes as gallery_routes


def _make_client(monkeypatch, tmp_path, *, user=None):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gallery.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(gallery_routes, "SessionLocal", session_factory)

    db = session_factory()
    try:
        db.add_all(
            [
                GalleryAlbum(id="album-visible", name="Visible", owner="alice", hidden=False),
                GalleryAlbum(id="album-hidden", name="Secret", owner="alice", hidden=True),
                GalleryImage(
                    id="img-photo", filename=f"{uuid.uuid4().hex}.png", prompt="a photo",
                    model="m", tags="photo-tag", ai_tags="", owner="alice",
                    album_id="album-visible", is_active=True, file_size=10,
                    media_type="image", hidden=False,
                ),
                GalleryImage(
                    id="img-video", filename=f"{uuid.uuid4().hex}.mp4", prompt="a clip",
                    model="m", tags="video-tag", ai_tags="", owner="alice",
                    is_active=True, file_size=20, media_type="video", hidden=False,
                ),
                GalleryImage(
                    id="img-hidden", filename=f"{uuid.uuid4().hex}.png", prompt="hush",
                    model="m", tags="secret-tag", ai_tags="", owner="alice",
                    album_id="album-visible", is_active=True, file_size=30,
                    media_type="image", hidden=True,
                ),
                GalleryImage(
                    id="img-bob-hidden", filename=f"{uuid.uuid4().hex}.png", prompt="bob hush",
                    model="m", tags="bob-secret", ai_tags="", owner="bob",
                    is_active=True, file_size=40, media_type="image", hidden=True,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    if user is None:
        monkeypatch.setenv("AUTH_ENABLED", "false")
    else:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: user)

    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    return TestClient(app)


def _ids(payload):
    return {item["id"] for item in payload["items"]}


def test_media_type_filter_splits_photos_and_videos(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)  # single-user

    everything = client.get("/api/gallery/library").json()
    # hidden image excluded by default; photo + video remain
    assert _ids(everything) == {"img-photo", "img-video"}

    images = client.get("/api/gallery/library", params={"media_type": "image"}).json()
    assert _ids(images) == {"img-photo"}
    assert all(i["media_type"] == "image" for i in images["items"])

    videos = client.get("/api/gallery/library", params={"media_type": "video"}).json()
    assert _ids(videos) == {"img-video"}
    assert videos["items"][0]["media_type"] == "video"


def test_hidden_images_excluded_by_default(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)  # single-user

    default = client.get("/api/gallery/library").json()
    assert "img-hidden" not in _ids(default)
    # hidden image's tag must not leak into the facet list
    assert "secret-tag" not in default["tags"]

    revealed = client.get("/api/gallery/library", params={"show_hidden": "true"}).json()
    assert "img-hidden" in _ids(revealed)
    assert "secret-tag" in revealed["tags"]


def test_patch_hidden_toggles_visibility(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, user="alice")

    assert "img-photo" in _ids(client.get("/api/gallery/library").json())

    patched = client.patch("/api/gallery/img-photo", json={"hidden": True}).json()
    assert patched["hidden"] is True

    assert "img-photo" not in _ids(client.get("/api/gallery/library").json())
    assert "img-photo" in _ids(
        client.get("/api/gallery/library", params={"show_hidden": "true"}).json()
    )

    client.patch("/api/gallery/img-photo", json={"hidden": False})
    assert "img-photo" in _ids(client.get("/api/gallery/library").json())


def test_hidden_albums_and_counts(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, user="alice")

    albums = {a["id"]: a for a in client.get("/api/gallery/albums").json()["albums"]}
    assert "album-hidden" not in albums            # hidden album excluded
    assert albums["album-visible"]["count"] == 1   # img-hidden not counted

    with_hidden = {
        a["id"]: a
        for a in client.get("/api/gallery/albums", params={"show_hidden": "true"}).json()["albums"]
    }
    assert "album-hidden" in with_hidden
    assert with_hidden["album-visible"]["count"] == 2  # hidden image now counted

    # Toggle an album hidden via the album update route.
    assert client.put("/api/gallery/albums/album-visible", json={"hidden": True}).json()["ok"]
    after = {a["id"] for a in client.get("/api/gallery/albums").json()["albums"]}
    assert "album-visible" not in after


def test_show_hidden_never_crosses_owners(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, user="alice")
    revealed = client.get("/api/gallery/library", params={"show_hidden": "true"}).json()
    # Alice revealing her hidden items must never surface bob's hidden image.
    assert "img-bob-hidden" not in _ids(revealed)
    assert "img-hidden" in _ids(revealed)
