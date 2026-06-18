# services/tts/tts_service.py
"""Multi-provider TTS service — dispatches to Microsoft Edge TTS (free), Azure
AI Speech, ElevenLabs, or an OpenAI-compatible endpoint."""

import logging
import hashlib
import httpx
from pathlib import Path
from typing import Optional, Dict, Any

from src.constants import TTS_CACHE_DIR

logger = logging.getLogger(__name__)


def _safe_speed(value, default: float = 1.0) -> float:
    """Parse the stored tts_speed defensively. The settings layer tolerates
    corrupt/agent-written config, so a non-numeric or empty value (e.g. an agent
    setting "speech speed" = "fast", or a hand-edited settings.json) must not
    crash synthesis or the stats endpoint with a ValueError."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return default
    return speed if speed > 0 else default


class TTSService:
    """Multi-provider TTS service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no TTS
      "edge"            — Microsoft Edge TTS (Azure neural voices, free, no key)
      "azure"           — Azure AI Speech (key + region)
      "elevenlabs"      — ElevenLabs (api key + voice id)
      "endpoint:<id>"   — OpenAI-compatible /audio/speech via ModelEndpoint
    """

    def __init__(self, cache_dir: str = TTS_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "tts_enabled": saved.get("tts_enabled", True),
            "tts_provider": saved.get("tts_provider", "disabled"),
            "tts_model": saved.get("tts_model", "tts-1"),
            "tts_voice": saved.get("tts_voice", "alloy"),
            "tts_speed": saved.get("tts_speed", "1"),
            "azure_speech_key": saved.get("azure_speech_key", ""),
            "azure_speech_region": saved.get("azure_speech_region", ""),
            "elevenlabs_api_key": saved.get("elevenlabs_api_key", ""),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return False
        provider = settings["tts_provider"]
        if provider == "disabled":
            return False
        if provider == "edge":
            return True  # free, no key; errors surface at synthesis time
        if provider == "azure":
            return bool(settings.get("azure_speech_key") and settings.get("azure_speech_region"))
        if provider == "elevenlabs":
            return bool(settings.get("elevenlabs_api_key"))
        if provider.startswith("endpoint:"):
            return True  # assume reachable; errors surface at synthesis time
        return False

    # ── Cache ──

    def _cache_key(self, text: str, provider: str, model: str, voice: str, speed: float = 1.0) -> str:
        raw = f"{provider}|{model}|{voice}|{speed}|{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[bytes]:
        for ext in (".mp3", ".wav"):
            path = self.cache_dir / f"{key}{ext}"
            if path.exists():
                return path.read_bytes()
        return None

    def _put_cache(self, key: str, data: bytes):
        ext = ".mp3" if (len(data) >= 3 and (data[:3] == b'ID3' or (data[0] == 0xff and (data[1] & 0xe0) == 0xe0))) else ".wav"
        (self.cache_dir / f"{key}{ext}").write_bytes(data)

    def clear_cache(self):
        count = 0
        for f in self.cache_dir.glob("*.*"):
            f.unlink()
            count += 1
        logger.info(f"Cleared {count} cached TTS files")

    # ── Microsoft Edge TTS (free) ──

    def _synthesize_edge(self, text: str, voice: str, speed: float) -> Optional[bytes]:
        """Microsoft Edge TTS — free Azure neural voices, no API key. The
        edge-tts package is async, but synthesize() runs inside the route's
        running event loop, so we drive it on a fresh loop in a worker thread."""
        v = (voice or "").strip()
        if "Neural" not in v:                       # OpenAI-style names won't work
            v = "en-US-AvaNeural"
        rate_pct = int(round((speed - 1.0) * 100))  # 1.0->+0%, 1.5->+50%
        rate = f"{'+' if rate_pct >= 0 else ''}{rate_pct}%"

        def _run() -> Optional[bytes]:
            import asyncio
            try:
                import edge_tts
            except ImportError:
                logger.error("Edge TTS: edge-tts not installed (pip install edge-tts)")
                return None

            async def _go():
                communicate = edge_tts.Communicate(text, v, rate=rate)
                buf = bytearray()
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio" and chunk.get("data"):
                        buf.extend(chunk["data"])
                return bytes(buf)

            return asyncio.run(_go())

        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as ex:
                data = ex.submit(_run).result(timeout=60)
            if not data:
                logger.error("Edge TTS: empty audio")
                return None
            logger.info(f"Edge TTS: {len(data)} bytes (voice={v})")
            return data
        except Exception as e:
            logger.error(f"Edge TTS error: {e}")
            return None

    # ── Azure AI Speech ──

    def _synthesize_azure(self, text: str, voice: str, speed: float, settings: dict) -> Optional[bytes]:
        key = (settings.get("azure_speech_key") or "").strip()
        region = (settings.get("azure_speech_region") or "").strip()
        if not key or not region:
            logger.error("Azure TTS: azure_speech_key / azure_speech_region not set")
            return None
        v = (voice or "").strip()
        if "Neural" not in v:                       # OpenAI-style names won't work
            v = "en-US-AriaNeural"
        parts = v.split("-")
        lang = "-".join(parts[:2]) if len(parts) >= 2 else "en-US"
        rate_pct = int(round((speed - 1.0) * 100))           # 1.0->0%, 1.5->+50%
        rate = f"{'+' if rate_pct >= 0 else ''}{rate_pct}%"
        esc = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ssml = (
            f"<speak version='1.0' xml:lang='{lang}'>"
            f"<voice name='{v}'><prosody rate='{rate}'>{esc}</prosody></voice></speak>"
        )
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
            "User-Agent": "Odysseus",
        }
        try:
            r = httpx.post(url, headers=headers, content=ssml.encode("utf-8"), timeout=60)
            if r.status_code != 200:
                logger.error(f"Azure TTS failed: {r.status_code} {r.text[:200]}")
                return None
            return r.content
        except Exception as e:
            logger.error(f"Azure TTS error: {e}")
            return None

    # ── ElevenLabs ──

    def _synthesize_elevenlabs(self, text: str, voice: str, model: str, settings: dict) -> Optional[bytes]:
        key = (settings.get("elevenlabs_api_key") or "").strip()
        if not key:
            logger.error("ElevenLabs TTS: elevenlabs_api_key not set")
            return None
        voice_id = (voice or "").strip() or "21m00Tcm4TlvDq8ikWAM"   # "Rachel" default
        model_id = model if (model or "").startswith("eleven") else "eleven_multilingual_v2"
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code != 200:
                logger.error(f"ElevenLabs TTS failed: {r.status_code} {r.text[:200]}")
                return None
            logger.info(f"ElevenLabs TTS: {len(r.content)} bytes (voice={voice_id})")
            return r.content
        except Exception as e:
            logger.error(f"ElevenLabs TTS error: {e}")
            return None

    # ── API endpoint ──

    def _synthesize_api(self, text: str, endpoint_id: str, model: str, voice: str, speed: float = 1.0) -> Optional[bytes]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"TTS endpoint {endpoint_id} not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key = ep.api_key
        finally:
            db.close()

        url = base_url + "/audio/speech"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }

        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
            logger.info(f"API TTS: {len(r.content)} bytes from {base_url}")
            return r.content
        except Exception as e:
            logger.error(f"API TTS synthesis failed: {e}")
            return None

    # ── Public interface ──

    def synthesize(self, text: str, use_cache: bool = True) -> Optional[bytes]:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return None
        provider = settings["tts_provider"]
        model = settings["tts_model"]
        voice = settings["tts_voice"]
        speed = _safe_speed(settings.get("tts_speed", "1"))

        if provider == "disabled":
            return None

        if len(text) > 5000:
            text = text[:5000]

        if use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            cached = self._get_cached(key)
            if cached:
                logger.info(f"TTS cache hit ({len(text)} chars)")
                return cached

        audio_data = None

        if provider == "edge":
            audio_data = self._synthesize_edge(text, voice, speed)
        elif provider == "azure":
            audio_data = self._synthesize_azure(text, voice, speed, settings)
        elif provider == "elevenlabs":
            audio_data = self._synthesize_elevenlabs(text, voice, model, settings)
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            audio_data = self._synthesize_api(text, endpoint_id, model, voice, speed)
        else:
            logger.error(f"Unknown TTS provider: {provider}")
            return None

        if audio_data and use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            self._put_cache(key, audio_data)

        return audio_data

    def synthesize_to_base64(self, text: str) -> Optional[str]:
        import base64
        audio = self.synthesize(text)
        if audio:
            return base64.b64encode(audio).decode("utf-8")
        return None

    def set_voice(self, voice: str):
        """Legacy no-op — voice is now managed via admin settings."""

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["tts_provider"]
        tts_enabled = settings.get("tts_enabled", True)

        cache_files = list(self.cache_dir.glob("*.wav")) + list(self.cache_dir.glob("*.mp3"))
        cache_size = sum(f.stat().st_size for f in cache_files)

        is_available = self.available and tts_enabled
        stats = {
            "available": is_available,
            "ready": is_available,
            "provider": provider,
            "model": settings["tts_model"],
            "voice": settings["tts_voice"],
            "speed": _safe_speed(settings.get("tts_speed", "1")),
            "cache_entries": len(cache_files),
            "cache_size_mb": round(cache_size / (1024 * 1024), 2),
        }

        if provider == "edge":
            stats["model"] = "Microsoft Edge TTS (free)"
        elif provider == "azure":
            stats["model"] = "Azure AI Speech"
        elif provider == "elevenlabs":
            stats["model"] = "ElevenLabs"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


# Module-level singleton
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
