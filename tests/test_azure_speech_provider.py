"""Guards for the Azure AI Speech provider (cheap/free-tier TTS + STT).

Azure is a distinct key+region API (not OpenAI-compatible), one resource serves
both TTS + STT, and it's implemented over REST (httpx) in both services. STT
transcodes the browser's webm to PCM WAV (ffmpeg) because Azure's short-audio
REST endpoint wants PCM. We can't hit Azure in CI (needs a key), so these assert
the wiring is present + correct.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_settings_have_azure_speech_keys():
    src = (ROOT / "src" / "settings.py").read_text(encoding="utf-8")
    assert '"azure_speech_key"' in src
    assert '"azure_speech_region"' in src


def test_tts_service_has_azure_provider():
    src = (ROOT / "services" / "tts" / "tts_service.py").read_text(encoding="utf-8")
    assert "_synthesize_azure" in src
    assert 'provider == "azure"' in src
    assert "tts.speech.microsoft.com/cognitiveservices/v1" in src
    assert "Ocp-Apim-Subscription-Key" in src
    assert "application/ssml+xml" in src


def test_stt_service_has_azure_provider():
    src = (ROOT / "services" / "stt" / "stt_service.py").read_text(encoding="utf-8")
    assert "_transcribe_azure" in src
    assert 'provider == "azure"' in src
    assert "stt.speech.microsoft.com" in src
    # webm -> PCM wav transcode (Azure short-audio REST needs PCM).
    assert "_to_wav16k" in src
    assert "ffmpeg" in src


def test_frontend_exposes_azure_tts_and_wiring():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    # azure option in BOTH provider selects + the key/region inputs.
    assert html.count('<option value="azure"') == 2
    assert "set-ttsAzureKey" in html and "set-ttsAzureRegion" in html
    js = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    assert "azure_speech_key" in js and "azure_speech_region" in js
    assert "set-ttsAzureRow" in js
    # the mic recorder must send azure recordings to the server transcriber.
    vr = (ROOT / "static" / "js" / "voiceRecorder.js").read_text(encoding="utf-8")
    assert "provider === 'azure'" in vr
