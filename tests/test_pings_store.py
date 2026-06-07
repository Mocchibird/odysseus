"""Functional tests for the Pings & Reminders feed store (src/pings_store.py).

Covers owner-scoping, unread counts, keep-exempt age-based expiry, and delete,
against the conftest in-memory SQLite.
"""
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from core.database import Base, engine, SessionLocal, Ping  # noqa: E402
from src import pings_store as ps  # noqa: E402


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.create_all(bind=engine)
    yield


def test_create_list_owner_isolation():
    ps.create("p-alice", "Reminder: standup", "9am", kind="reminder", source_ref="note:abc")
    ps.create("p-alice", "Daily brief", "stuff", kind="task", status="success")
    ps.create("p-bob", "Bob ping", "x", kind="ping")
    assert len(ps.list_pings("p-alice")) == 2
    assert len(ps.list_pings("p-bob")) == 1


def test_unread_and_mark_read():
    a = ps.create("p-unread", "one", "", kind="ping")
    ps.create("p-unread", "two", "", kind="ping")
    assert ps.unread_count("p-unread") == 2
    ps.mark_read("p-unread", a["id"], True)
    assert ps.unread_count("p-unread") == 1
    ps.mark_all_read("p-unread")
    assert ps.unread_count("p-unread") == 0


def test_keep_exempt_age_expiry():
    keep = ps.create("p-exp", "Keep me", "", kind="ping")
    ps.set_keep("p-exp", keep["id"], True)
    old = ps.create("p-exp", "Old", "", kind="ping")
    db = SessionLocal()
    try:
        for pid in (keep["id"], old["id"]):
            row = db.query(Ping).filter(Ping.id == pid).first()
            row.created_at = datetime.utcnow() - timedelta(days=40)
        db.commit()
    finally:
        db.close()
    removed = ps.expire_old(days=30)
    assert removed == 1
    ids = {p["id"] for p in ps.list_pings("p-exp")}
    assert keep["id"] in ids and old["id"] not in ids


def test_delete():
    p = ps.create("p-del", "bye", "", kind="ping")
    assert ps.delete("p-del", p["id"]) is True
    assert ps.delete("p-del", "missing") is False
