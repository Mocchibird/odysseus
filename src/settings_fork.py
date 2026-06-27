# src/settings_fork.py
"""Fork-only settings additions.

Kept OUT of upstream's ``DEFAULT_SETTINGS`` / ``_PER_USER_KEYS`` literals in
src/settings.py so those literals stay byte-identical to upstream and merge
cleanly on every upstream sync. settings.py imports these and folds them in at
module-load time (``DEFAULT_SETTINGS.update(...)`` / ``_PER_USER_KEYS |= ...``).

This module must NOT import src.settings (would be circular). It only defines
plain literals. See docs/fork-additive-policy.md for the pattern.
"""

# New default settings the fork adds on top of upstream's DEFAULT_SETTINGS.
FORK_DEFAULT_SETTINGS = {
    # Azure AI Speech (one resource serves both TTS + STT). Used by the "azure"
    # TTS/STT providers. Free tier (F0): ~5 audio hrs/mo STT + 0.5M chars/mo TTS.
    "azure_speech_key": "",
    "azure_speech_region": "",
    # ElevenLabs (one key serves both TTS + STT-Scribe). Used by the
    # "elevenlabs" TTS/STT providers. The TTS voice is the ElevenLabs voice id.
    "elevenlabs_api_key": "",
    "default_persona": "Iris",
    # Preferred output language for the assistant + notifications (en/ko).
    # Per-user override via FORK_PER_USER_KEYS; see src/i18n.py.
    "language": "en",
    # IANA timezone for displaying stored (UTC) times in background output —
    # daily brief, evening wrap-up, reminders. Per-user override, auto-learned
    # from the browser (x-tz-name). Empty = fall back to the server's local
    # zone. See src/user_time.py.
    "timezone": "",
    # Admin-set GLOBAL allowlist of model ids that NON-ADMIN users may use for
    # chat & agent. Empty = no restriction (every enabled endpoint's models show
    # in their picker, current behavior). When non-empty: non-admins' model
    # picker shows ONLY these, and the server rejects any other model at
    # send-time. Lets the admin enable extra endpoints for backend roles
    # (image / research / email-summary) WITHOUT exposing those models in users'
    # chat picker. Admins are never restricted. Per-user `allowed_models`
    # privileges still override this for individually-restricted accounts.
    "chat_allowed_models": [],
    # Optional explicit ntfy connection (integration id). Empty = auto-pick the
    # connection whose `ntfy_topic` matches reminder_ntfy_topic, else the first.
    "reminder_ntfy_integration_id": "",
    # Quiet hours: during this window, reminders are NOT pushed (ntfy/email/
    # webhook/browser) — they still land in the Pings feed so nothing is lost.
    # Evaluated in server local time (TZ). Window may wrap midnight. Test
    # reminders always bypass it.
    "quiet_hours_enabled": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
    "reminder_email_account_id": "",
}

# Keys the fork makes per-user overridable, on top of upstream's _PER_USER_KEYS.
# Includes both fork-new keys (persona/language/timezone/quiet-hours) and some
# upstream reminder keys the fork promotes to per-user.
FORK_PER_USER_KEYS = {
    "default_persona",
    # Assistant/notification language is personal (en/ko, see src/i18n.py).
    "language",
    # Display timezone is personal, auto-learned from the browser so the daily
    # brief / reminders convert UTC to the user's local clock — and follow them
    # when travelling. See src/user_time.py.
    "timezone",
    # Reminder delivery is personal: one user may want browser-only alerts,
    # another may subscribe to a private ntfy topic (and, optionally, pin the
    # specific ntfy connection whose token is scoped to that topic).
    "reminder_channel", "reminder_llm_synthesis", "reminder_ntfy_topic",
    "reminder_ntfy_integration_id",
    "reminder_email_account_id", "reminder_email_to",
    "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end",
}

# Per-user keys whose explicit empty-string value is meaningful — get_user_setting
# returns "" for these instead of falling through to the global default.
FORK_ALLOW_EMPTY_USER_KEYS = {
    "reminder_email_account_id", "reminder_email_to",
    "reminder_ntfy_integration_id",
    "default_persona",
}
