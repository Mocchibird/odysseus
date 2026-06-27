from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_default_persona_is_user_scoped_setting():
    # Fork settings defaults/per-user keys live in settings_fork.py and merge
    # into settings.py at import (see docs/fork-additive-policy.md).
    source = ((ROOT / "src/settings.py").read_text(encoding="utf-8")
              + (ROOT / "src/settings_fork.py").read_text(encoding="utf-8"))

    assert '"default_persona": "Iris"' in source
    assert '"default_persona",' in source
    assert "_ALLOW_EMPTY_USER_KEYS" in source


def test_settings_exposes_default_persona_selector():
    # The Persona selector markup moved into the fork-ui.js runtime injector
    # (keeps index.html aligned with upstream); shipped source = both files.
    html = ((ROOT / "static/index.html").read_text(encoding="utf-8")
            + (ROOT / "static/js/fork-ui.js").read_text(encoding="utf-8"))
    settings_js = (ROOT / "static/js/settings.js").read_text(encoding="utf-8")

    assert 'id="set-defaultPersonaSelect"' in html
    assert "/api/prefs/default_persona" in settings_js
    assert "odysseus-default-persona" in settings_js
    assert "PROMPT_TEMPLATES" in settings_js


def test_new_chat_applies_default_persona():
    presets_js = (ROOT / "static/js/presets.js").read_text(encoding="utf-8")
    sessions_js = (ROOT / "static/js/sessions.js").read_text(encoding="utf-8")

    assert "export async function applyDefaultPersonaForNewChat()" in presets_js
    assert "export async function applyPersonaByName" in presets_js
    assert "applyDefaultPersonaForNewChat" in sessions_js
    assert "await _applyDefaultPersonaForPendingChat();" in sessions_js
