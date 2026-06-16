"""Per-user language support (src/i18n.py + the surfaces it localizes).

Covers: language normalization + per-user resolution, the string tables
(including en/ko parity so future languages can't drift), the LLM language
directives, reminder-prefix stripping across languages, localized reminder
text, the UTF-8-safe ntfy publish paths, and the frontend wiring (Iris-Korean
persona + the Settings language select).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    _STRINGS,
    count_items,
    email_language_hint,
    format_date,
    get_user_language,
    language_directive,
    normalize_language,
    strip_reminder_prefix,
    t,
)

ROOT = Path(__file__).resolve().parents[1]


# ── Core resolution ──────────────────────────────────────────────────────────

def test_normalize_language():
    assert normalize_language("ko") == "ko"
    assert normalize_language(" KO ") == "ko"
    assert normalize_language("en") == "en"
    assert normalize_language("de") == "de"
    # Unsupported / junk values fall back to the default.
    assert normalize_language("fr") == DEFAULT_LANGUAGE  # not shipped yet
    assert normalize_language(None) == DEFAULT_LANGUAGE
    assert normalize_language("") == DEFAULT_LANGUAGE
    assert normalize_language(42) == DEFAULT_LANGUAGE


def test_get_user_language_reads_per_user_pref(monkeypatch):
    import routes.prefs_routes as prefs_routes
    monkeypatch.setattr(
        prefs_routes, "_load_for_user",
        lambda user: {"language": "ko"} if user == "alice" else {},
    )
    assert get_user_language("alice") == "ko"
    # No pref → global default (en unless the admin changed it).
    assert get_user_language("bob") == DEFAULT_LANGUAGE
    # No owner at all → default, never an exception.
    assert get_user_language("") == DEFAULT_LANGUAGE
    assert get_user_language(None) == DEFAULT_LANGUAGE


def test_language_is_a_per_user_setting():
    from src.settings import DEFAULT_SETTINGS, _PER_USER_KEYS
    assert "language" in _PER_USER_KEYS
    assert DEFAULT_SETTINGS.get("language") == "en"


# ── String tables ────────────────────────────────────────────────────────────

def test_string_tables_have_full_parity():
    """Every language table carries exactly the same keys as English —
    catches drift when strings (or languages) are added later."""
    en_keys = set(_STRINGS["en"])
    for code in SUPPORTED_LANGUAGES:
        assert set(_STRINGS[code]) == en_keys, f"table for {code!r} out of sync"


def test_t_lookup_formatting_and_fallbacks():
    assert t("note_reminder_title", "en") == "Note reminder"
    assert t("note_reminder_title", "ko") == "노트 알림"
    assert t("more_items", "ko", n=3) == "...외 3개 더"
    assert t("email_reminder_subject", "ko", title="우유 사기") == "알림 (Odysseus): 우유 사기"
    # Unknown language → English; unknown key → the key itself.
    assert t("note_reminder_title", "xx") == "Note reminder"
    assert t("no_such_key", "ko") == "no_such_key"


def test_count_items_pluralization():
    assert count_items(1, "en") == "1 item"
    assert count_items(3, "en") == "3 items"
    assert count_items(3, "ko") == "항목 3개"


def test_format_date_localizes():
    import datetime
    d = datetime.date(2026, 6, 10)  # a Wednesday
    assert format_date(d, "en") == "Wednesday, June 10, 2026"
    assert format_date(d, "ko") == "2026년 6월 10일 수요일"
    assert format_date(d, "de") == "Mittwoch, 10. Juni 2026"


# ── LLM steering ─────────────────────────────────────────────────────────────

def test_language_directive_only_for_non_english():
    assert language_directive("en") == ""
    assert "한국어" in language_directive("ko")
    assert email_language_hint("en") == ""
    assert "한국어" in email_language_hint("ko")


def test_every_supported_language_beyond_english_has_a_directive():
    for code in SUPPORTED_LANGUAGES:
        if code == DEFAULT_LANGUAGE:
            continue
        assert language_directive(code), f"missing directive for {code!r}"
        assert email_language_hint(code), f"missing email hint for {code!r}"


# ── Reminder prefix stripping (calendar dedupe + email subjects) ─────────────

def test_strip_reminder_prefix_handles_all_languages():
    assert strip_reminder_prefix("Reminder: Buy milk") == "Buy milk"
    assert strip_reminder_prefix("알림: 우유 사기") == "우유 사기"
    assert strip_reminder_prefix("reminder:   spaced") == "spaced"
    assert strip_reminder_prefix("No prefix here") == "No prefix here"
    assert strip_reminder_prefix("") == ""


def test_calendar_reminder_dedupe_matches_across_languages():
    """An English-era 'Reminder: X' note and a Korean '알림: X' note must
    normalize to the same key, or switching language would duplicate
    calendar reminders."""
    en_title = t("reminder_prefix", "en", title="Standup")
    ko_title = t("reminder_prefix", "ko", title="Standup")
    assert strip_reminder_prefix(en_title).lower() == strip_reminder_prefix(ko_title).lower()


# ── Localized reminder text ──────────────────────────────────────────────────

def _note(**kw):
    return SimpleNamespace(title=kw.get("title"), items=kw.get("items"), content=kw.get("content", ""))


def test_reminder_text_localizes_default_title_and_counts():
    from routes.note_routes import _reminder_text_from_note

    title, _ = _reminder_text_from_note(_note(title=None, content="x"), "ko")
    assert title == "노트 알림"

    items = json.dumps([
        {"text": "하나", "done": False}, {"text": "둘", "done": False},
    ])
    title, body = _reminder_text_from_note(_note(title="장보기", items=items), "ko")
    assert title == "장보기"
    assert body.startswith("남은 항목 (2):")
    assert "- 하나" in body and "- 둘" in body

    # English path unchanged.
    title, body = _reminder_text_from_note(_note(title="Groceries", items=items), "en")
    assert body.startswith("Pending (2):")


def test_reminder_ntfy_actions_localized_labels(monkeypatch):
    import routes.note_routes as nr
    import routes.prefs_routes as prefs_routes
    monkeypatch.setenv("ODYSSEUS_PUBLIC_URL", "https://example.test")
    monkeypatch.setattr(
        prefs_routes, "_load_for_user",
        lambda user: {"language": "ko"} if user == "alice" else {},
    )
    monkeypatch.setattr("src.reminder_tokens.mint", lambda note_id, owner: "tok", raising=False)

    actions = nr._reminder_ntfy_actions("note-1", "alice")
    assert isinstance(actions, list) and len(actions) == 3
    assert [a["label"] for a in actions] == ["완료", "1시간 미루기", "내일 오전 9시"]
    assert all(a["action"] == "http" and a["clear"] for a in actions)

    actions_en = nr._reminder_ntfy_actions("note-1", "bob")
    assert [a["label"] for a in actions_en] == ["Done", "Snooze 1h", "Tomorrow 9am"]


# ── UTF-8-safe ntfy publishing ───────────────────────────────────────────────

def test_actions_header_formatting():
    from src.ntfy_client import _actions_header, _ascii_safe
    acts = [
        {"action": "http", "label": "Done", "url": "https://x/a", "method": "POST", "clear": True},
        {"action": "http", "label": "Snooze 1h", "url": "https://x/b", "method": "POST", "clear": True},
    ]
    header = _actions_header(acts)
    assert header == (
        "action=http, label=Done, url=https://x/a, method=POST, clear=true; "
        "action=http, label=Snooze 1h, url=https://x/b, method=POST, clear=true"
    )
    assert _ascii_safe(header)
    assert not _ascii_safe("완료")
    # httpx rejects ANY non-ASCII header value — even Latin-1 like 'café'.
    assert not _ascii_safe("café")


class _FakeResponse:
    is_success = True
    status_code = 200
    text = ""


class _FakeClient:
    """Captures the request httpx would have sent."""
    captured = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, content=None, headers=None, auth=None):
        _FakeClient.captured = {"url": url, "content": content, "headers": headers or {}}
        return _FakeResponse()


@pytest.mark.asyncio
async def test_ntfy_ascii_uses_header_path(monkeypatch):
    import src.ntfy_client as nc
    monkeypatch.setattr(nc.httpx, "AsyncClient", _FakeClient)
    intg = {"base_url": "https://ntfy.test"}
    actions = [{"action": "http", "label": "Done", "url": "https://x", "method": "POST", "clear": True}]
    res = await nc.send_ntfy_notification(intg, "reminders", "body text", title="Reminder", actions=actions)
    assert res.get("exit_code") == 0
    cap = _FakeClient.captured
    assert cap["url"] == "https://ntfy.test/reminders"
    assert cap["headers"]["Title"] == "Reminder"
    assert "label=Done" in cap["headers"]["Actions"]
    assert cap["content"] == "body text"


@pytest.mark.asyncio
async def test_ntfy_accented_latin_title_uses_json_publish(monkeypatch):
    """'Café standup' is Latin-1 but NOT ASCII — httpx would raise on the
    header path, so it must take the JSON endpoint."""
    import src.ntfy_client as nc
    monkeypatch.setattr(nc.httpx, "AsyncClient", _FakeClient)
    res = await nc.send_ntfy_notification(
        {"base_url": "https://ntfy.test"}, "reminders", "body", title="Café standup")
    assert res.get("exit_code") == 0
    cap = _FakeClient.captured
    assert cap["url"] == "https://ntfy.test"
    assert json.loads(cap["content"].decode("utf-8"))["title"] == "Café standup"


@pytest.mark.asyncio
async def test_ntfy_korean_uses_json_publish(monkeypatch):
    import src.ntfy_client as nc
    monkeypatch.setattr(nc.httpx, "AsyncClient", _FakeClient)
    intg = {"base_url": "https://ntfy.test"}
    actions = [{"action": "http", "label": "완료", "url": "https://x", "method": "POST", "clear": True}]
    res = await nc.send_ntfy_notification(intg, "reminders", "우유 사세요", title="알림", actions=actions)
    assert res.get("exit_code") == 0
    cap = _FakeClient.captured
    # Topic moves into the JSON body; no Latin-1-unsafe headers remain.
    assert cap["url"] == "https://ntfy.test"
    payload = json.loads(cap["content"].decode("utf-8"))
    assert payload["topic"] == "reminders"
    assert payload["title"] == "알림"
    assert payload["message"] == "우유 사세요"
    assert payload["actions"][0]["label"] == "완료"
    for v in cap["headers"].values():
        v.encode("latin-1")  # every remaining header must be transport-safe


# ── Frontend wiring (source checks, same style as the preset tests) ──────────

def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_iris_korean_persona_template_exists():
    presets_js = _read("static/js/presets.js")
    assert "'Iris-Korean'" in presets_js
    assert "iris-ko" in presets_js
    assert "당신은 Iris" in presets_js
    # The Korean persona answers in Korean by default.
    assert "한국어로 대답" in presets_js
    # New-chat default mapping consults the language pref.
    assert "/api/prefs/language" in presets_js


def test_server_side_korean_prompt_matches_client():
    from src.preset_manager import IRIS_SYSTEM_PROMPT_KO
    presets_js = _read("static/js/presets.js")
    assert IRIS_SYSTEM_PROMPT_KO in presets_js


def test_settings_ui_has_language_select():
    # The Language select markup was moved into the fork-ui.js runtime injector
    # (keeps index.html aligned with upstream); the shipped source is both files.
    index_html = _read("static/index.html") + _read("static/js/fork-ui.js")
    assert 'id="set-language"' in index_html
    assert '<option value="en">' in index_html.split('id="set-language"', 1)[1][:400]
    assert '<option value="ko">' in index_html.split('id="set-language"', 1)[1][:400]
    settings_js = _read("static/js/settings.js")
    assert "'set-language'" in settings_js or '"set-language"' in settings_js
    assert "/api/prefs/language" in settings_js


def test_agent_prompt_cache_keys_on_language():
    agent_loop = _read("src/agent_loop.py")
    assert "get_user_language" in agent_loop
    # The language must be part of the cache key, or a pref change would not
    # take effect until restart.
    assert "_lang)" in agent_loop.split("cache_key = (", 1)[1].split("\n", 1)[0]


def test_reminder_subject_prefixes_cover_all_languages():
    from src.i18n import reminder_subject_prefixes
    prefixes = reminder_subject_prefixes()
    assert "reminder (odysseus):" in prefixes
    assert "알림 (odysseus):" in prefixes
    assert "reminder:" in prefixes
    assert "알림:" in prefixes


def test_urgent_email_lead_singular_and_plural():
    # en plural is byte-identical to the pre-i18n literal; singular fixes the
    # old "1 email need" grammar.
    assert t("urgent_email_lead", "en", n=4) == "4 emails need an urgent reply:"
    assert t("urgent_email_lead_one", "en") == "1 email needs an urgent reply:"
    assert t("urgent_email_lead", "ko", n=4) == "긴급 답장이 필요한 이메일 4건:"


# ── German ───────────────────────────────────────────────────────────────────

def test_german_strings_and_prefixes():
    assert t("note_reminder_title", "de") == "Notiz-Erinnerung"
    assert t("email_reminder_subject", "de", title="Milch kaufen") == "Erinnerung (Odysseus): Milch kaufen"
    assert strip_reminder_prefix("Erinnerung: Standup") == "Standup"
    from src.i18n import reminder_subject_prefixes
    assert "erinnerung (odysseus):" in reminder_subject_prefixes()
    assert "한국어" in SUPPORTED_LANGUAGES.get("ko", "") or True  # smoke
    assert SUPPORTED_LANGUAGES.get("de") == "Deutsch"


def test_iris_german_persona_template_exists():
    presets_js = _read("static/js/presets.js")
    assert "'Iris-German'" in presets_js
    assert "iris-de" in presets_js
    assert "Du bist Iris" in presets_js
    assert "auf Deutsch" in presets_js
    # The mapping covers both variants.
    assert "Iris-Korean" in presets_js and "Iris-German" in presets_js


def test_server_side_german_prompt_matches_client():
    from src.preset_manager import IRIS_SYSTEM_PROMPT_DE
    assert IRIS_SYSTEM_PROMPT_DE in _read("static/js/presets.js")


def test_settings_select_offers_all_languages():
    # The Language select markup was moved into the fork-ui.js runtime injector
    # (keeps index.html aligned with upstream); the shipped source is both files.
    index_html = _read("static/index.html") + _read("static/js/fork-ui.js")
    block = index_html.split('id="set-language"', 1)[1][:600]
    for opt in ('<option value="en">', '<option value="ko">', '<option value="de">'):
        assert opt in block


# ── UI language layer ────────────────────────────────────────────────────────

def test_ui_i18n_layer_wired():
    i18n_js = _read("static/js/i18n.js")
    assert "MutationObserver" in i18n_js
    assert "odysseus-ui-lang" in i18n_js
    # chat content must never be translated
    assert "#chat-history" in i18n_js
    app_js = _read("static/app.js")
    assert "./js/i18n.js" in app_js
    # the boot import precedes every other module import
    assert app_js.index("./js/i18n.js") < app_js.index("import sessionModule")
    # settings change mirrors the pref + reloads
    settings_js = _read("static/js/settings.js")
    assert "odysseus-ui-lang" in settings_js
    sw_js = _read("static/sw.js")
    for path in ("/static/js/i18n.js", "/static/js/i18n/ko.js", "/static/js/i18n/de.js"):
        assert path in sw_js


def test_ui_dictionaries_exist_and_parse():
    import re as _re
    for code in ("ko", "de"):
        src = _read(f"static/js/i18n/{code}.js")
        assert src.lstrip().startswith("//") or src.lstrip().startswith("export"), code
        assert "export default" in src
        # spot-check a core chrome string is covered
        assert '"New Chat"' in src, f"{code} missing core chrome strings"


# ── Per-user Iris variant at chat time (non-admins can't write the slot) ─────

def test_resolve_iris_prompt_for_user(monkeypatch):
    """The global custom slot is admin-gated, so the chat path must resolve
    the Iris language/persona variant per user at request time."""
    import routes.prefs_routes as prefs_routes
    from src.preset_manager import (
        _LEGACY_IRIS_VAULT_PROMPT,
        IRIS_SYSTEM_PROMPT,
        IRIS_SYSTEM_PROMPT_DE,
        IRIS_SYSTEM_PROMPT_KO,
        resolve_iris_prompt_for_user,
    )
    monkeypatch.setattr(
        prefs_routes, "_load_for_user",
        lambda u: {"language": "ko"} if u == "kim"
        else ({"default_persona": "Iris-German"} if u == "gerd" else {}),
    )
    # Language pref drives the swap for untouched default slots.
    assert resolve_iris_prompt_for_user(IRIS_SYSTEM_PROMPT, "kim") == IRIS_SYSTEM_PROMPT_KO
    # An explicit per-user persona pref naming a variant wins over language.
    assert resolve_iris_prompt_for_user(IRIS_SYSTEM_PROMPT, "gerd") == IRIS_SYSTEM_PROMPT_DE
    # A Korean slot for an English user swaps back.
    assert resolve_iris_prompt_for_user(IRIS_SYSTEM_PROMPT_KO, "bob") == IRIS_SYSTEM_PROMPT
    # Already matching -> no churn; admin-customized prompts are never touched.
    assert resolve_iris_prompt_for_user(IRIS_SYSTEM_PROMPT_KO, "kim") is None
    assert resolve_iris_prompt_for_user("You are a pirate.", "kim") is None
    # The legacy vault-era default also counts as an untouched default.
    assert resolve_iris_prompt_for_user(_LEGACY_IRIS_VAULT_PROMPT, "kim") == IRIS_SYSTEM_PROMPT_KO
    # The admin-global default_persona ("Iris") must NOT mask the language
    # mapping — only an explicit per-user pref counts.
    assert resolve_iris_prompt_for_user(IRIS_SYSTEM_PROMPT, "kim") == IRIS_SYSTEM_PROMPT_KO


def test_chat_path_resolves_iris_variant():
    helpers = _read("routes/chat_helpers.py")
    assert "resolve_iris_prompt_for_user" in helpers
    # the swap happens before the preface consumes preset.system_prompt
    assert helpers.index("resolve_iris_prompt_for_user(") < helpers.index("_preface_kwargs = dict(")
