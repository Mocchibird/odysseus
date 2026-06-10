"""Per-user language support for assistant output + notification text.

The per-user pref is ``language`` (whitelisted in src.settings._PER_USER_KEYS,
admin-global default in DEFAULT_SETTINGS). Resolve it with
``get_user_language(owner)``; localize fixed strings with ``t(key, lang)``
and steer LLM output with ``language_directive(lang)``.

Adding a language (e.g. German later) is pure data:
  1. add the code to SUPPORTED_LANGUAGES,
  2. add a directive to _DIRECTIVES (and _EMAIL_HINTS),
  3. fill its table in _STRINGS (missing keys fall back to English),
  4. add an Iris persona variant in static/js/presets.js and an
     <option> in the Settings language select (static/index.html).
"""
from __future__ import annotations

from typing import Any

SUPPORTED_LANGUAGES = {
    "en": "English",
    "ko": "한국어",
}

DEFAULT_LANGUAGE = "en"


def normalize_language(value: Any) -> str:
    """Coerce a stored pref value to a supported language code."""
    code = str(value or "").strip().lower()
    return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def get_user_language(owner: Any = "") -> str:
    """The caller's preferred language (per-user pref → global → English)."""
    try:
        from src.settings import get_setting, get_user_setting
        return normalize_language(
            get_user_setting("language", str(owner or ""), get_setting("language", DEFAULT_LANGUAGE))
        )
    except Exception:
        return DEFAULT_LANGUAGE


# ── LLM steering ─────────────────────────────────────────────────────────────

# Injected as/into a system message for chat, agent runs, and other LLM calls
# whose output the user reads. English is the model default — no directive.
_DIRECTIVES = {
    "ko": (
        "Respond in Korean (한국어) by default. If the user writes in a "
        "different language, follow the user's language instead."
    ),
}

# Email drafts should match the thread being replied to, not blindly switch.
_EMAIL_HINTS = {
    "ko": (
        "The user's preferred language is Korean (한국어). Match the language "
        "of the email you are replying to; when the thread's language is "
        "unclear, write in Korean."
    ),
}


def language_directive(lang: str) -> str:
    return _DIRECTIVES.get(normalize_language(lang), "")


def email_language_hint(lang: str) -> str:
    return _EMAIL_HINTS.get(normalize_language(lang), "")


# ── Fixed-string tables ──────────────────────────────────────────────────────

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # Note/calendar reminders
        "note_reminder_title": "Note reminder",
        "reminder_fallback_title": "Reminder",
        "reminder_prefix": "Reminder: {title}",
        "pending_header": "Pending ({n}):",
        "pending_header_plain": "Pending:",
        "more_items": "...and {n} more",
        "items_count": "{n} item",
        "items_count_plural": "{n} items",
        # ntfy action buttons + tap acknowledgements
        "btn_done": "Done",
        "btn_snooze_1h": "Snooze 1h",
        "btn_tomorrow_9": "Tomorrow 9am",
        "ack_dismissed": "Reminder dismissed.",
        "ack_snoozed_1h": "Snoozed 1 hour.",
        "ack_snoozed_tomorrow": "Snoozed to tomorrow 9:00.",
        "ack_title_dismissed": "Reminder dismissed",
        "ack_title_snoozed": "Reminder snoozed",
        "note_fallback": "Note",
        # Email reminder
        "email_reminder_subject": "Reminder (Odysseus): {title}",
        # Urgent-email digest
        "urgent_email_title_one": "Urgent email",
        "urgent_email_title_many": "{n} urgent emails",
        "urgent_email_lead_one": "1 email needs an urgent reply:",
        "urgent_email_lead": "{n} emails need an urgent reply:",
        "no_subject": "(no subject)",
        "open_email": "Open email:",
        "and_n_more": "...and {n} more.",
        # Daily brief
        "brief_title": "Daily brief — {date}",
        "brief_calendar": "Calendar:",
        "brief_calendar_empty": "Calendar: nothing scheduled.",
        "brief_all_day": "all day",
        "brief_email_unread": "Email: {n} unread",
        "brief_todos": "Todos:",
        "brief_todos_empty": "Todos: none active.",
        # Evening wrap-up + carry-forward (morning_routine combines these)
        "wrapup_title": "Evening wrap-up — {date}",
        "wrapup_events": "Today you had {n} event(s).",
        "wrapup_still_open": "Still open ({n}):",
        "wrapup_no_todos": "No open todos. Nice and clear.",
        "wrapup_tomorrow": "Tomorrow:",
        "wrapup_tomorrow_empty": "Tomorrow: nothing scheduled yet.",
        "habits_line": "Habits: {done}/{total} done",
        "habits_pending": " · pending: {names}",
        "habits_all_done": " · all done",
        "checklist_fallback": "Checklist",
        "untitled": "(untitled)",
        "cf_none": "No overdue reminders to carry forward.",
        "cf_carried": "Carried {n} overdue reminder(s) forward to today 09:00:",
        "cf_more": "  ...and {n} more",
        # LLM micro-prompts whose output the user reads verbatim
        "reminder_synthesis_prompt": (
            "You are a reminder assistant. Write a single short, warm, "
            "motivating sentence (max 25 words) reminding the user about the "
            "note below. Do not add greetings, preamble, or hashtags. Output "
            "only the sentence."
        ),
        "session_title_prompt": (
            "Generate a short title (3-6 words, no quotes) for a conversation "
            "that starts with this message. Reply with ONLY the title, nothing "
            "else. Do NOT include any thinking, reasoning, or explanation — "
            "just the title."
        ),
    },
    "ko": {
        "note_reminder_title": "노트 알림",
        "reminder_fallback_title": "알림",
        "reminder_prefix": "알림: {title}",
        "pending_header": "남은 항목 ({n}):",
        "pending_header_plain": "남은 항목:",
        "more_items": "...외 {n}개 더",
        "items_count": "항목 {n}개",
        "items_count_plural": "항목 {n}개",
        "btn_done": "완료",
        "btn_snooze_1h": "1시간 미루기",
        "btn_tomorrow_9": "내일 오전 9시",
        "ack_dismissed": "알림을 해제했습니다.",
        "ack_snoozed_1h": "1시간 미뤘습니다.",
        "ack_snoozed_tomorrow": "내일 오전 9시로 미뤘습니다.",
        "ack_title_dismissed": "알림 해제됨",
        "ack_title_snoozed": "알림 미뤄짐",
        "note_fallback": "노트",
        "email_reminder_subject": "알림 (Odysseus): {title}",
        "urgent_email_title_one": "긴급 이메일",
        "urgent_email_title_many": "긴급 이메일 {n}건",
        "urgent_email_lead_one": "긴급 답장이 필요한 이메일 1건:",
        "urgent_email_lead": "긴급 답장이 필요한 이메일 {n}건:",
        "no_subject": "(제목 없음)",
        "open_email": "이메일 열기:",
        "and_n_more": "...외 {n}건 더.",
        "brief_title": "데일리 브리핑 — {date}",
        "brief_calendar": "캘린더:",
        "brief_calendar_empty": "캘린더: 오늘 일정이 없습니다.",
        "brief_all_day": "종일",
        "brief_email_unread": "이메일: 읽지 않은 메일 {n}건",
        "brief_todos": "할 일:",
        "brief_todos_empty": "할 일: 진행 중인 항목이 없습니다.",
        "wrapup_title": "이브닝 정리 — {date}",
        "wrapup_events": "오늘 일정은 {n}건이었습니다.",
        "wrapup_still_open": "아직 남은 할 일 ({n}):",
        "wrapup_no_todos": "남은 할 일이 없습니다. 깔끔합니다.",
        "wrapup_tomorrow": "내일:",
        "wrapup_tomorrow_empty": "내일: 아직 일정이 없습니다.",
        "habits_line": "습관: {total}개 중 {done}개 완료",
        "habits_pending": " · 남은 습관: {names}",
        "habits_all_done": " · 모두 완료",
        "checklist_fallback": "체크리스트",
        "untitled": "(제목 없음)",
        "cf_none": "오늘로 미룰 기한 지난 알림이 없습니다.",
        "cf_carried": "기한이 지난 알림 {n}건을 오늘 09:00로 옮겼습니다:",
        "cf_more": "  ...외 {n}건 더",
        "reminder_synthesis_prompt": (
            "당신은 리마인더 비서입니다. 아래 노트에 대해 사용자에게 상기시키는 "
            "짧고 따뜻하며 동기를 주는 문장 하나(25단어 이내)를 한국어로 "
            "작성하세요. 인사말, 서두, 해시태그는 넣지 마세요. 그 문장만 "
            "출력하세요."
        ),
        "session_title_prompt": (
            "이 메시지로 시작하는 대화의 짧은 제목(3-6단어, 따옴표 없이)을 "
            "한국어로 생성하세요. 제목만 답하고 다른 내용은 출력하지 마세요. "
            "사고 과정이나 설명 없이 제목만 출력하세요."
        ),
    },
}


def t(key: str, lang: str = DEFAULT_LANGUAGE, **fmt: Any) -> str:
    """Localized fixed string; missing keys fall back to English, then key."""
    code = normalize_language(lang)
    text = _STRINGS.get(code, {}).get(key) or _STRINGS["en"].get(key) or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError):
            return text
    return text


def count_items(n: int, lang: str = DEFAULT_LANGUAGE) -> str:
    key = "items_count" if n == 1 else "items_count_plural"
    return t(key, lang, n=n)


_KO_WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def format_date(d: Any, lang: str = DEFAULT_LANGUAGE) -> str:
    """Human date for briefs. en: 'Tuesday, June 10, 2026'; ko: '2026년 6월 10일 화요일'."""
    if normalize_language(lang) == "ko":
        return f"{d.year}년 {d.month}월 {d.day}일 {_KO_WEEKDAYS[d.weekday()]}"
    return d.strftime(f"%A, %B {d.day}, %Y")


# Every localized "Reminder:"-style prefix, for prefix-stripping/dedupe logic
# that must keep working whatever language a reminder was created in.
_REMINDER_PREFIXES = tuple(
    table["reminder_prefix"].split("{", 1)[0].strip() for table in _STRINGS.values()
)


def reminder_subject_prefixes() -> tuple[str, ...]:
    """Lowercased localized reminder email-subject + title prefixes — for
    guards that must recognize Odysseus's own reminder mail in ANY language
    (urgency-scanner feedback-loop defense, Clear-reminders sweep)."""
    out: list[str] = []
    for table in _STRINGS.values():
        for key in ("email_reminder_subject", "reminder_prefix"):
            prefix = table[key].split("{", 1)[0].strip().lower()
            if prefix:
                out.append(prefix)
    return tuple(dict.fromkeys(out))


def strip_reminder_prefix(text: str) -> str:
    """Strip a leading localized 'Reminder:'-style prefix from a title."""
    value = str(text or "").strip()
    low = value.lower()
    for prefix in _REMINDER_PREFIXES:
        p = prefix.lower()
        if low.startswith(p):
            return value[len(prefix):].strip()
    return value
