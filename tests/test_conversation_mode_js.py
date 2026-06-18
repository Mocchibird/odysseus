"""Guards for the push-to-talk voice conversation mode (fork addon).

conversationMode.js injects a launch button + a full-screen overlay that drives
the SAME chat underneath: it reuses voiceRecorder.js for STT (via a transcript
hook), submits the real #chat-form, and auto-speaks replies through
window.aiTTSManager. These are source guards — the live voice loop needs a mic +
a configured provider, which can't run headless; the wiring itself is simple, so
we assert it's connected the way the design requires.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_conversation_module_wiring():
    js = (ROOT / "static" / "js" / "conversationMode.js").read_text(encoding="utf-8")
    # Reuses the recorder + its new transcript hook (no parallel STT impl).
    assert "from './voiceRecorder.js'" in js
    assert "setTranscriptHook" in js
    # Drives the REAL chat (so history/tools/agent keep working) — not a side channel.
    assert "chat-form" in js
    assert "requestSubmit" in js
    # Auto-speaks replies while in voice mode via the shared TTS manager.
    assert "aiTTSManager" in js
    assert "autoPlay" in js
    # Injects the launch button into the composer + exposes open/close.
    assert "conv-mode-btn" in js
    assert "chat-input-right" in js
    assert "export function open" in js and "export function close" in js
    # Gates on a secure context + an enabled STT provider.
    assert "isSecureContext" in js


def test_voice_recorder_exposes_transcript_hook():
    js = (ROOT / "static" / "js" / "voiceRecorder.js").read_text(encoding="utf-8")
    assert "export function setTranscriptHook(" in js
    # Both STT paths funnel through insertTranscription, which honours the hook.
    assert "if (_transcriptHook)" in js
    assert "setTranscriptHook," in js  # present on the default export object


def test_conversation_mode_loaded_and_styled():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "/static/js/conversationMode.js" in html
    css = (ROOT / "static" / "fork.css").read_text(encoding="utf-8")
    assert ".conv-overlay" in css and ".conv-orb" in css
    # Reuses the accent var (no hardcoded colour) per the contributing rules.
    assert "var(--accent, var(--red))" in css
