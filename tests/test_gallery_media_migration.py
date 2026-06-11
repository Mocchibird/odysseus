"""The gallery media/hidden column migration runs against an *existing* DB.

The functional tests build the schema from the SQLAlchemy models (so the new
columns are always present); this exercises the real upgrade path — an old
gallery_images/gallery_albums table that predates media_type/hidden — and
asserts the ALTER + extension backfill is correct and idempotent.
"""
import sqlite3

import core.database as cdb


def _make_legacy_db(path):
    conn = sqlite3.connect(path)
    # Old schema: no media_type, no hidden.
    conn.execute(
        "CREATE TABLE gallery_images (id TEXT PRIMARY KEY, filename TEXT, owner TEXT)"
    )
    conn.execute("CREATE TABLE gallery_albums (id TEXT PRIMARY KEY, name TEXT, owner TEXT)")
    conn.execute("INSERT INTO gallery_images VALUES ('p1', 'abc123.png', 'alice')")
    conn.execute("INSERT INTO gallery_images VALUES ('v1', 'def456.MP4', 'alice')")
    conn.execute("INSERT INTO gallery_images VALUES ('v2', 'ghi789.webm', 'alice')")
    conn.execute("INSERT INTO gallery_albums VALUES ('a1', 'Trip', 'alice')")
    conn.commit()
    conn.close()


def test_migration_adds_columns_and_backfills_video(monkeypatch, tmp_path):
    db_file = tmp_path / "legacy.db"
    _make_legacy_db(str(db_file))
    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_file}")

    cdb._migrate_add_gallery_media_columns()

    conn = sqlite3.connect(str(db_file))
    img_cols = {r[1] for r in conn.execute("PRAGMA table_info(gallery_images)")}
    assert {"media_type", "hidden"} <= img_cols
    album_cols = {r[1] for r in conn.execute("PRAGMA table_info(gallery_albums)")}
    assert "hidden" in album_cols

    rows = dict(conn.execute("SELECT id, media_type FROM gallery_images"))
    assert rows["p1"] == "image"           # default
    assert rows["v1"] == "video"           # .MP4 backfilled (case-insensitive)
    assert rows["v2"] == "video"           # .webm backfilled
    # hidden defaults to 0 / falsey for every pre-existing row
    assert all(h in (0, None) for (h,) in conn.execute("SELECT hidden FROM gallery_images"))
    conn.close()


def test_migration_is_idempotent(monkeypatch, tmp_path):
    db_file = tmp_path / "legacy.db"
    _make_legacy_db(str(db_file))
    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_file}")

    cdb._migrate_add_gallery_media_columns()
    # A user hides an image; a second migration pass must not clobber it or fail.
    conn = sqlite3.connect(str(db_file))
    conn.execute("UPDATE gallery_images SET hidden=1 WHERE id='p1'")
    conn.commit()
    conn.close()

    cdb._migrate_add_gallery_media_columns()  # second run, no-op

    conn = sqlite3.connect(str(db_file))
    hidden = dict(conn.execute("SELECT id, hidden FROM gallery_images"))
    assert hidden["p1"] == 1   # preserved, not reset
    conn.close()
