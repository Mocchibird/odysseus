from services.stt.stt_service import STTService
from services.tts.tts_service import TTSService


def test_tts_disabled_toggle_blocks_synthesis(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    calls = {"endpoint": 0}

    monkeypatch.setattr(service, "_load_settings", lambda: {
        "tts_enabled": False,
        "tts_provider": "endpoint:voice-endpoint",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1",
    })

    def fake_endpoint(*args, **kwargs):
        calls["endpoint"] += 1
        return b"audio"

    monkeypatch.setattr(service, "_synthesize_api", fake_endpoint)

    assert service.available is False
    assert service.synthesize("hello") is None
    assert calls == {"endpoint": 0}


def test_stt_disabled_toggle_blocks_transcription(monkeypatch):
    service = STTService()
    calls = {"endpoint": 0}

    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": False,
        "stt_provider": "endpoint:transcribe-endpoint",
        "stt_model": "whisper-1",
        "stt_language": "",
    })

    def fake_endpoint(*args, **kwargs):
        calls["endpoint"] += 1
        return "transcript"

    monkeypatch.setattr(service, "_transcribe_api", fake_endpoint)

    assert service.available is False
    assert service.transcribe(b"audio") is None
    assert calls == {"endpoint": 0}
