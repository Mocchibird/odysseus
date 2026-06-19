"""Guards for the voice provider set after the Edge/ElevenLabs refactor.

TTS providers are now: disabled / edge (free, no key) / azure / elevenlabs /
endpoint. STT providers are: disabled / azure / elevenlabs / endpoint. The
browser (Web Speech) and local (Kokoro TTS, faster-whisper STT) providers were
removed — local in-process models were a build/runtime hassle (blis wheel
failures, GPU/CPU plumbing). Live calls need API keys so these assert wiring.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TTS_SRC = (ROOT / "services" / "tts" / "tts_service.py").read_text(encoding="utf-8")
STT_SRC = (ROOT / "services" / "stt" / "stt_service.py").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
VR_JS = (ROOT / "static" / "js" / "voiceRecorder.js").read_text(encoding="utf-8")
TTS_JS = (ROOT / "static" / "js" / "tts-ai.js").read_text(encoding="utf-8")


def test_edge_tts_is_the_bundled_optional_dep():
    req = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8")
    active = [l.strip() for l in req.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert "edge-tts" in active
    # The removed local stacks must not creep back into the optional install.
    assert "faster-whisper" not in active
    assert "kokoro" not in active
    assert "soundfile" not in active


def test_dockerfile_drops_kokoro_phonemizer_keeps_ffmpeg():
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "espeak-ng" not in df          # Kokoro-only, gone
    assert "ffmpeg" in df                  # still needed: Azure STT webm->PCM


def test_tts_service_providers():
    assert "_synthesize_edge" in TTS_SRC
    assert "_synthesize_elevenlabs" in TTS_SRC
    assert "_synthesize_azure" in TTS_SRC
    assert 'provider == "edge"' in TTS_SRC
    assert 'provider == "elevenlabs"' in TTS_SRC
    # edge-tts is async; it must run off the route's running loop in a thread.
    assert "edge_tts" in TTS_SRC and "ThreadPoolExecutor" in TTS_SRC
    assert "api.elevenlabs.io/v1/text-to-speech" in TTS_SRC
    # Removed providers are gone.
    assert "_KokoroPipeline" not in TTS_SRC
    assert "_get_kokoro" not in TTS_SRC
    assert 'provider == "browser"' not in TTS_SRC
    assert 'provider == "local"' not in TTS_SRC


def test_stt_service_providers():
    assert "_transcribe_elevenlabs" in STT_SRC
    assert "_transcribe_azure" in STT_SRC
    assert "api.elevenlabs.io/v1/speech-to-text" in STT_SRC
    assert "scribe_v1" in STT_SRC
    # Removed providers are gone.
    assert "_get_whisper" not in STT_SRC
    assert "_transcribe_local" not in STT_SRC
    assert "faster_whisper" not in STT_SRC
    assert 'provider == "browser"' not in STT_SRC
    assert 'provider == "local"' not in STT_SRC


def test_endpoint_stt_transcodes_to_wav():
    # A served Whisper endpoint gets a clean 16kHz mono PCM WAV, not raw webm/opus
    # (raw opus → silence → bogus "nn" language detection / gibberish).
    assert 'files = {"file": ("audio.wav"' in STT_SRC
    # _transcribe_api reuses the ffmpeg transcode helper before posting.
    assert "_to_wav16k(audio_bytes)" in STT_SRC


def test_add_models_offers_stt_tts_types():
    ADMIN_JS = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    # The "Add a local model server" type selector offers STT + TTS, not just LLM/Image.
    sel = HTML.split('id="adm-epLocalType"')[1].split("</select>")[0]
    assert '<option value="tts"' in sel
    assert '<option value="stt"' in sel
    # Added-Models list renders a badge for tts/stt endpoints.
    assert "ep.model_type === 'tts'" in ADMIN_JS and "ep.model_type === 'stt'" in ADMIN_JS
    # The voice dropdowns prefer type-matched endpoints (with a show-all fallback).
    assert "model_type === 'tts'" in SETTINGS_JS
    assert "model_type === 'stt'" in SETTINGS_JS
    # Backend column doc lists the new types.
    db = (ROOT / "core" / "database.py").read_text(encoding="utf-8")
    assert '"tts"' in db.split("model_type = Column")[1].split("\n")[0]


def test_endpoint_tts_uses_free_text_model_voice():
    # Endpoint TTS must use free-text model/voice (Kokoro needs "kokoro"/"af_heart",
    # not the OpenAI tts-1/alloy presets that made synthesis fail).
    assert "function getVoice() { return voiceInput.value; }" in SETTINGS_JS
    assert "af_heart" in SETTINGS_JS   # Kokoro prefill


def test_admin_global_settings_hidden_from_non_admins():
    fu = (ROOT / "static" / "js" / "fork-ui.js").read_text(encoding="utf-8")
    assert "_markAdminOnlySettings" in fu
    # Admin-global cards gated (voice config + email safety + whole Search tab).
    for anchor in ("set-ttsProviderSelect", "set-sttProviderSelect",
                   "set-agentEmailConfirm", "set-searchProvider", "set-researchMaxTokens"):
        assert anchor in fu, anchor
    assert 'data-settings-tab="search"' in fu
    # Per-user upstream cards (Vision / Image — in _PER_USER_KEYS) must NOT be
    # referenced by fork-ui, so they're never hidden from normal users. (Persona
    # is also per-user but fork-ui *injects* its row, so it's legitimately present.)
    for peruser in ("set-visionEnabledToggle", "set-imgEnabledToggle"):
        assert peruser not in fu, peruser


def test_voice_listing_endpoints_and_datalists():
    tts_routes = (ROOT / "routes" / "tts_routes.py").read_text(encoding="utf-8")
    stt_routes = (ROOT / "routes" / "stt_routes.py").read_text(encoding="utf-8")
    # Backend list endpoints + service methods that feed the dropdowns.
    assert '@router.get("/voices")' in tts_routes
    assert '@router.get("/models")' in stt_routes
    assert "def list_voices" in TTS_SRC
    assert "def list_models" in STT_SRC
    # <datalist> suggestions on the voice + STT model fields (free-text preserved).
    assert 'list="set-ttsVoiceOptions"' in HTML and '<datalist id="set-ttsVoiceOptions">' in HTML
    assert 'list="set-sttModelOptions"' in HTML and '<datalist id="set-sttModelOptions">' in HTML
    # Frontend fetches the lists.
    assert "/api/tts/voices" in SETTINGS_JS
    assert "/api/stt/models" in SETTINGS_JS


def test_voice_endpoints_excluded_from_chat_models():
    # /api/models feeds the chat + agent picker and the allow-list — a Whisper/
    # Kokoro endpoint's model ids must NOT leak into those (image stays).
    src = (ROOT / "routes" / "model_routes.py").read_text(encoding="utf-8")
    assert 'if ep_model_type in ("stt", "tts")' in src
    # The API add-form type selector (injected by fork-ui.js) offers TTS + STT too.
    fu = (ROOT / "static" / "js" / "fork-ui.js").read_text(encoding="utf-8")
    assert '<option value="tts">TTS</option>' in fu
    assert '<option value="stt">STT</option>' in fu


def test_settings_have_elevenlabs_key():
    src = (ROOT / "src" / "settings.py").read_text(encoding="utf-8")
    assert '"elevenlabs_api_key"' in src


def test_frontend_provider_options():
    # TTS select: edge + azure + elevenlabs (no browser/local).
    assert '<option value="edge"' in HTML
    assert HTML.count('<option value="azure"') == 2      # one per select
    assert HTML.count('<option value="elevenlabs"') == 2
    # The removed voice options (labels are unique to the TTS/STT selects).
    assert "Browser (built-in)" not in HTML
    assert "Kokoro" not in HTML
    assert "faster-whisper" not in HTML
    assert "set-ttsElevenKey" in HTML
    # ElevenLabs key persisted from the settings UI.
    assert "elevenlabs_api_key" in SETTINGS_JS


def test_frontend_drops_browser_voice_paths():
    # The mic recorder only captures audio; transcription is server-side.
    assert "SpeechRecognition" not in VR_JS
    assert "provider === 'elevenlabs'" in VR_JS
    assert "provider === 'azure'" in VR_JS
    # The read-aloud manager no longer has a Web Speech path.
    assert "speechSynthesis" not in TTS_JS
    assert "useBrowserTTS" not in TTS_JS


def test_elevenlabs_defaults_to_flash():
    # Flash v2.5 is the cheapest ElevenLabs model; it's the default unless the
    # user picks another in the UI (tts_model starting with "eleven").
    assert "eleven_flash_v2_5" in TTS_SRC
    assert "eleven_multilingual_v2" not in TTS_SRC.split("# Flash v2.5")[0]  # not the default branch
    # UI lets the user choose the model.
    assert "set-ttsElevenModel" in HTML and "set-ttsElevenModel" in SETTINGS_JS


def test_tts_auto_language_voice_switch():
    import importlib
    mod = importlib.import_module("services.tts.tts_service")
    dom = mod._dominant_lang
    # Non-Latin scripts are detected; Latin/empty stays None (keep configured voice).
    assert dom("こんにちは、元気ですか") == "ja"
    assert dom("안녕하세요 반갑습니다") == "ko"
    assert dom("Hello, how are you?") is None
    assert dom("") is None
    # A mostly-English sentence with one foreign word keeps the configured voice.
    assert dom("The word 猫 means cat") is None

    svc = mod.TTSService(cache_dir="/tmp/_ttscv_lang")
    # en voice + Japanese text → a Japanese voice; English text → unchanged.
    assert svc._resolve_voice_for_text("こんにちは", "en-US-AvaNeural") == "ja-JP-NanamiNeural"
    assert svc._resolve_voice_for_text("Hello there", "en-US-AvaNeural") == "en-US-AvaNeural"
    # Configured voice already in the right language → left as-is.
    assert svc._resolve_voice_for_text("안녕", "ko-KR-InJoonNeural") == "ko-KR-InJoonNeural"
