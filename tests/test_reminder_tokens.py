"""Security tests for ntfy reminder-action tokens (src/reminder_tokens.py).

The token is the credential for the auth-exempt /api/notes/reminder-action
endpoint, so forgery/tamper/expiry rejection is security-critical.
"""
import pytest

pytest.importorskip("cryptography")
try:
    from src import reminder_tokens as rt
except Exception:  # pragma: no cover - heavy import chain missing locally
    pytest.skip("reminder_tokens deps unavailable", allow_module_level=True)


def test_roundtrip():
    assert rt.verify(rt.mint("note123", "alice")) == ("note123", "alice")
    assert rt.verify(rt.mint("n2", "bob")) == ("n2", "bob")


def test_plaintext_forgery_rejected():
    # A forged plaintext token (not encrypted with our key) must be refused —
    # secret_storage.decrypt passes plaintext through, so verify() must gate on
    # is_encrypted first.
    assert rt.verify("rta1|note123|alice|9999999999") is None


def test_garbage_and_tamper_rejected():
    assert rt.verify("") is None
    assert rt.verify("notatoken") is None
    good = rt.mint("n", "alice")
    assert rt.verify(good[:-3] + "zzz") is None


def test_expiry_enforced():
    assert rt.verify(rt.mint("n", "alice", ttl_seconds=-10)) is None
