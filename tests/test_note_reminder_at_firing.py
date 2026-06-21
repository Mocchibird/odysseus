"""action_ping_notes: the due_date (one-shot) vs reminder_at (daily-nag) split.

Locks in the behavior added with the "Due by" / "Remind me" separation:
- due_date fires once when its moment enters the +/-90s window, then the
  25-minute reping guard keeps it quiet.
- reminder_at re-nudges at most once per local day while the note still has
  pending items, and stays silent for future or fully-checked notes.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _iso_local(dt_utc):
    """A naive local-wall-clock ISO string, the shape the notes UI stores."""
    return dt_utc.astimezone().replace(tzinfo=None).isoformat()


def _today_local_midnight_iso():
    """Local 'today' at 00:00 — unambiguously <= now on the same local calendar
    day no matter when the suite runs. Avoids a midnight flake where 'now - 5min'
    lands on the previous day and hits the later-day time-of-day gate."""
    n = datetime.now().astimezone()
    return f"{n.year:04d}-{n.month:02d}-{n.day:02d}T00:00"


def _note(**kw):
    base = dict(
        id="n1", title="Groceries", content=None, items=None,
        due_date=None, reminder_at=None, owner="alice",
        archived=False, note_type="note",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Q:
    def __init__(self, notes):
        self._notes = notes

    def filter(self, *a, **k):
        return self

    def all(self):
        return list(self._notes)


class _DB:
    def __init__(self, notes):
        self._notes = notes

    def query(self, *a, **k):
        return _Q(self._notes)

    def close(self):
        pass


def _run(notes, monkeypatch, tmp_path, owner="alice"):
    """Run action_ping_notes against fake notes; return (result, dispatch_calls)."""
    import core.database as cd
    import routes.note_routes as nr
    import src.builtin_actions as ba

    calls = []

    async def fake_dispatch(**kw):
        calls.append(kw)
        return {"ok": True}

    monkeypatch.setattr(cd, "SessionLocal", lambda: _DB(notes))
    monkeypatch.setattr(nr, "dispatch_reminder", fake_dispatch)
    monkeypatch.setattr(ba, "owner_filter", lambda q, *a, **k: q)
    monkeypatch.setattr(ba, "DATA_DIR", str(tmp_path))

    from src.builtin_actions import action_ping_notes, TaskNoop
    try:
        result = asyncio.run(action_ping_notes(owner=owner))
    except TaskNoop:
        result = None
    return result, calls


def test_reminder_at_nudges_when_overdue_with_pending(monkeypatch, tmp_path):
    rem = _today_local_midnight_iso()  # active earlier today; robust across midnight
    note = _note(
        note_type="checklist",
        items=json.dumps([{"text": "buy milk", "done": False}]),
        reminder_at=rem,
    )
    _, calls = _run([note], monkeypatch, tmp_path)
    assert len(calls) == 1, "reminder due today/overdue with a pending item should nudge"

    # Same local day -> the per-day dedupe keeps it quiet on the next tick.
    _, calls2 = _run([note], monkeypatch, tmp_path)
    assert calls2 == [], "reminder must not nudge twice in one day"


def test_reminder_at_silent_when_fully_checked(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    note = _note(
        note_type="checklist",
        items=json.dumps([{"text": "buy milk", "done": True}]),
        reminder_at=_iso_local(now - timedelta(minutes=5)),
    )
    _, calls = _run([note], monkeypatch, tmp_path)
    assert calls == [], "a fully-checked checklist should stop nudging"


def test_reminder_at_silent_when_future(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    note = _note(
        note_type="checklist",
        items=json.dumps([{"text": "buy milk", "done": False}]),
        reminder_at=_iso_local(now + timedelta(days=1)),
    )
    _, calls = _run([note], monkeypatch, tmp_path)
    assert calls == [], "a future reminder must not fire yet"


def test_due_date_fires_once(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    note = _note(due_date=_iso_local(now))  # inside the +/-90s window
    _, calls = _run([note], monkeypatch, tmp_path)
    assert len(calls) == 1, "due_date should fire once when its moment arrives"

    # The 25-minute reping guard keeps the one-shot deadline quiet afterward.
    _, calls2 = _run([note], monkeypatch, tmp_path)
    assert calls2 == [], "due_date is a one-shot ping, not a repeating nag"
