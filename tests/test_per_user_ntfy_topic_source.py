from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ntfy_integration_test_uses_user_topic():
    source = (ROOT / "routes" / "auth_routes.py").read_text(encoding="utf-8")

    assert "get_user_setting" in source
    assert '"reminder_ntfy_topic"' in source
    assert 'user or ""' in source
    assert "quote(topic, safe='')" in source


def test_reminder_test_saves_visible_topic_before_sending():
    source = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    test_button_block = source[source.index("// Test button"):source.index("      } catch (e) {", source.index("// Test button"))]

    assert "await save({" in test_button_block
    assert "reminder_channel: channelSel.value" in test_button_block
    assert "reminder_ntfy_topic:" in test_button_block
    assert "/api/notes/fire-reminder" in test_button_block
    assert test_button_block.index("await save({") < test_button_block.index("/api/notes/fire-reminder")


def test_background_delivery_check_uses_user_channel():
    source = (ROOT / "src" / "builtin_actions.py").read_text(encoding="utf-8")

    assert "_get_user_setting(" in source
    assert '"reminder_channel"' in source
    assert "owner or \"\"" in source
