"""The scheduled-email poller must claim a row (pending -> sending) BEFORE
sending, so two overlapping drivers (in-process poller + CLI/cron) can't send
the same email twice.

The race is made deterministic here: the mocked SMTP send re-enters the poller
mid-send (simulating the other driver's tick). With the atomic claim the row is
already 'sending', so the re-entrant pass finds nothing pending and sends once.
Without the claim it was still 'pending' until after send -> double send.
"""
import sqlite3

import pytest


@pytest.fixture
def poller_db(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_pollers as email_pollers

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_pollers, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()
    return email_pollers, db_path


def _insert_pending(db_path, sid="m1"):
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(scheduled_emails)").fetchall()]
    fields = ["id", "to_addr", "cc", "bcc", "subject", "body", "in_reply_to",
              "references_hdr", "attachments", "send_at", "created_at", "status"]
    values = [sid, "a@example.com", "", "", "Hi", "body", "", "", "[]",
              "2000-01-01T00:00:00", "2000-01-01T00:00:00", "pending"]
    if "account_id" in cols:
        fields.append("account_id"); values.append(None)
    placeholders = ",".join("?" * len(fields))
    conn.execute(
        f"INSERT INTO scheduled_emails ({','.join(fields)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    conn.close()


def _status(db_path, sid="m1"):
    return sqlite3.connect(db_path).execute(
        "SELECT status FROM scheduled_emails WHERE id=?", (sid,)
    ).fetchone()[0]


def test_claim_prevents_double_send_on_overlapping_poll(poller_db, monkeypatch):
    email_pollers, db_path = poller_db
    _insert_pending(db_path)

    sends = []

    def fake_send(cfg, frm, recipients, msg):
        sends.append(recipients)
        if len(sends) == 1:
            # Another driver ticks while we're mid-send. The row is already
            # claimed ('sending'), so this pass must send nothing.
            email_pollers._scheduled_poll_once()

    monkeypatch.setattr(email_pollers, "_get_email_config",
                        lambda *a, **k: {"from_address": "me@example.com"})
    monkeypatch.setattr(email_pollers, "_send_smtp_message", fake_send)
    # Skip the Sent-folder append (its IMAP is best-effort + already try/excepted).
    def _boom(*a, **k):
        raise RuntimeError("no imap in test")
    monkeypatch.setattr(email_pollers, "_imap", _boom)

    result = email_pollers._scheduled_poll_once()

    assert len(sends) == 1, f"email sent {len(sends)} times — claim failed"
    assert result["sent"] == ["m1"]
    assert _status(db_path) == "sent"


def test_already_sending_row_is_not_picked_up(poller_db, monkeypatch):
    email_pollers, db_path = poller_db
    _insert_pending(db_path, "m2")
    # Simulate another worker having claimed it.
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE scheduled_emails SET status='sending' WHERE id='m2'")
    conn.commit()
    conn.close()

    sends = []
    monkeypatch.setattr(email_pollers, "_send_smtp_message",
                        lambda *a, **k: sends.append(1))
    monkeypatch.setattr(email_pollers, "_get_email_config",
                        lambda *a, **k: {"from_address": "me@example.com"})

    result = email_pollers._scheduled_poll_once()
    assert sends == []
    assert result["sent"] == []
