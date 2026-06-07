"""resolve_ntfy_integration: pick the ntfy connection scoped to a topic.

Covers the multi-user "one connection + token per topic" setup: each ntfy
connection is tagged with the topic its token can publish to, and a send for a
given topic must use that connection (not just the first one configured).
"""

from src.ntfy_client import find_ntfy_integration, resolve_ntfy_integration


def _conns():
    return [
        {
            "id": "aaa",
            "preset": "ntfy",
            "base_url": "https://ntfy.example.com",
            "api_key": "tok-main",
            "ntfy_topic": "Iris-Reminders",
            "enabled": True,
        },
        {
            "id": "bbb",
            "preset": "ntfy",
            "base_url": "https://ntfy.example.com",
            "api_key": "tok-eqira",
            "ntfy_topic": "Iris-Reminders-Eqira",
            "enabled": True,
        },
        # Unrelated + disabled connections must be ignored.
        {"id": "ccc", "preset": "miniflux", "base_url": "https://rss", "enabled": True},
        {"id": "ddd", "preset": "ntfy", "base_url": "https://x", "enabled": False},
    ]


def test_topic_selects_matching_connection():
    got = resolve_ntfy_integration(_conns(), topic="Iris-Reminders-Eqira")
    assert got and got["id"] == "bbb" and got["api_key"] == "tok-eqira"


def test_topic_match_is_case_and_space_insensitive():
    got = resolve_ntfy_integration(_conns(), topic="  iris-reminders-eqira ")
    assert got and got["id"] == "bbb"


def test_explicit_integration_id_wins():
    got = resolve_ntfy_integration(_conns(), topic="Iris-Reminders", integration_id="bbb")
    assert got and got["id"] == "bbb"


def test_unknown_topic_falls_back_to_first_enabled_ntfy():
    got = resolve_ntfy_integration(_conns(), topic="Nope")
    assert got and got["id"] == "aaa"


def test_no_topic_returns_first_enabled_ntfy():
    got = resolve_ntfy_integration(_conns())
    assert got and got["id"] == "aaa"


def test_disabled_and_non_ntfy_excluded():
    only = [
        {"id": "ddd", "preset": "ntfy", "base_url": "https://x", "enabled": False},
        {"id": "ccc", "preset": "miniflux", "base_url": "https://rss", "enabled": True},
    ]
    assert resolve_ntfy_integration(only, topic="anything") is None


def test_find_ntfy_integration_back_compat_returns_first():
    assert find_ntfy_integration(_conns())["id"] == "aaa"
