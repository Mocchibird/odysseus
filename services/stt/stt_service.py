# services/stt/stt_service.py
"""Multi-provider Speech-to-Text service — dispatches to Azure AI Speech,
ElevenLabs Scribe, or an OpenAI-compatible endpoint."""

import io
import logging
import httpx
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class STTService:
    """Multi-provider STT service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no STT
      "azure"           — Azure AI Speech (key + region)
      "elevenlabs"      — ElevenLabs Scribe (api key)
      "endpoint:<id>"   — OpenAI-compatible /audio/transcriptions via ModelEndpoint
    """

    def __init__(self):
        pass

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "stt_enabled": saved.get("stt_enabled", False),
            "stt_provider": saved.get("stt_provider", "disabled"),
            "stt_model": saved.get("stt_model", "base"),
            "stt_language": saved.get("stt_language", ""),
            "azure_speech_key": saved.get("azure_speech_key", ""),
            "azure_speech_region": saved.get("azure_speech_region", ""),
            "elevenlabs_api_key": saved.get("elevenlabs_api_key", ""),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return False
        provider = settings["stt_provider"]
        if provider == "disabled":
            return False
        if provider == "azure":
            return bool(settings.get("azure_speech_key") and settings.get("azure_speech_region"))
        if provider == "elevenlabs":
            return bool(settings.get("elevenlabs_api_key"))
        if provider.startswith("endpoint:"):
            return True  # assume reachable
        return False

    # ── API endpoint ──

    def _transcribe_api(self, audio_bytes: bytes, endpoint_id: str, model: str, language: str = "") -> Optional[str]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"STT endpoint {endpoint_id} not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key = ep.api_key
        finally:
            db.close()

        url = base_url + "/audio/transcriptions"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        files = {"file": ("audio.webm", io.BytesIO(audio_bytes), "audio/webm")}
        data = {"model": model or "whisper-1"}
        if language:
            data["language"] = language

        try:
            r = httpx.post(url, headers=headers, files=files, data=data, timeout=60)
            r.raise_for_status()
            result = r.json()
            text = result.get("text", "")
            logger.info(f"API STT: {len(text)} chars from {base_url}")
            return text
        except Exception as e:
            logger.error(f"API STT transcription failed: {e}")
            return None

    # ── Azure AI Speech ──

    def _to_wav16k(self, audio_bytes: bytes) -> Optional[bytes]:
        """Transcode the browser's webm/opus to 16kHz mono PCM WAV via ffmpeg —
        Azure's short-audio REST endpoint wants PCM, not webm."""
        import subprocess
        try:
            p = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                 "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                input=audio_bytes, capture_output=True, timeout=30,
            )
            if p.returncode == 0 and p.stdout:
                return p.stdout
            logger.error(f"Azure STT: ffmpeg transcode failed: {p.stderr[:200]!r}")
        except Exception as e:
            logger.error(f"Azure STT: ffmpeg transcode error: {e}")
        return None

    def _transcribe_azure(self, audio_bytes: bytes, language: str, settings: dict) -> Optional[str]:
        key = (settings.get("azure_speech_key") or "").strip()
        region = (settings.get("azure_speech_region") or "").strip()
        if not key or not region:
            logger.error("Azure STT: azure_speech_key / azure_speech_region not set")
            return None
        wav = self._to_wav16k(audio_bytes)
        if not wav:
            return None
        lang = (language or "").strip() or "en-US"   # Azure needs a BCP-47 locale
        url = (f"https://{region}.stt.speech.microsoft.com/speech/recognition/"
               f"conversation/cognitiveservices/v1?language={lang}")
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
            "Accept": "application/json",
        }
        try:
            r = httpx.post(url, headers=headers, content=wav, timeout=60)
            if r.status_code != 200:
                logger.error(f"Azure STT failed: {r.status_code} {r.text[:200]}")
                return None
            data = r.json()
            if data.get("RecognitionStatus") == "Success":
                text = data.get("DisplayText", "")
                logger.info(f"Azure STT: {len(text)} chars")
                return text
            logger.info(f"Azure STT: no speech ({data.get('RecognitionStatus')})")
            return ""
        except Exception as e:
            logger.error(f"Azure STT error: {e}")
            return None

    # ── ElevenLabs Scribe ──

    def _transcribe_elevenlabs(self, audio_bytes: bytes, language: str, settings: dict) -> Optional[str]:
        key = (settings.get("elevenlabs_api_key") or "").strip()
        if not key:
            logger.error("ElevenLabs STT: elevenlabs_api_key not set")
            return None
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {"xi-api-key": key}
        files = {"file": ("audio.webm", io.BytesIO(audio_bytes), "audio/webm")}
        data = {"model_id": "scribe_v1"}
        if language:
            data["language_code"] = language
        try:
            r = httpx.post(url, headers=headers, files=files, data=data, timeout=120)
            if r.status_code != 200:
                logger.error(f"ElevenLabs STT failed: {r.status_code} {r.text[:200]}")
                return None
            text = r.json().get("text", "")
            logger.info(f"ElevenLabs STT: {len(text)} chars")
            return text
        except Exception as e:
            logger.error(f"ElevenLabs STT error: {e}")
            return None

    # ── Public interface ──

    def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return None
        provider = settings["stt_provider"]
        model = settings["stt_model"]
        language = settings.get("stt_language", "")

        if provider == "disabled":
            return None

        if provider == "azure":
            return self._transcribe_azure(audio_bytes, language, settings)
        elif provider == "elevenlabs":
            return self._transcribe_elevenlabs(audio_bytes, language, settings)
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            return self._transcribe_api(audio_bytes, endpoint_id, model, language)
        else:
            logger.error(f"Unknown STT provider: {provider}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["stt_provider"]
        stt_enabled = settings.get("stt_enabled", False)
        # If toggle is off, report as disabled
        effective_provider = provider if stt_enabled else "disabled"

        stats = {
            "available": self.available and stt_enabled,
            "provider": effective_provider,
            "model": settings["stt_model"],
            "language": settings.get("stt_language", ""),
        }

        if provider == "azure":
            stats["model"] = "Azure AI Speech"
        elif provider == "elevenlabs":
            stats["model"] = "ElevenLabs Scribe"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


# Module-level singleton
_stt_service = None

def get_stt_service() -> STTService:
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
