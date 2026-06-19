# routes/stt_routes.py
"""STT API routes — multi-provider (Azure, ElevenLabs Scribe, API endpoint)."""

from fastapi import APIRouter, HTTPException, UploadFile, File
import logging

from src.upload_limits import read_upload_limited, STT_MAX_AUDIO_BYTES

logger = logging.getLogger(__name__)


def setup_stt_routes(stt_service):
    """Setup STT routes with the provided STT service"""
    router = APIRouter(prefix="/api/stt", tags=["stt"])

    @router.get("/stats")
    async def get_stt_stats():
        """Get STT service statistics"""
        try:
            return stt_service.get_stats()
        except Exception as e:
            logger.error(f"Failed to get STT stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/models")
    async def list_stt_models():
        """Available models for the configured STT endpoint (settings dropdown).
        Returns {models: []} when the provider can't enumerate — UI keeps free-text."""
        try:
            return {"models": stt_service.list_models()}
        except Exception as e:
            logger.warning(f"Failed to list STT models: {e}")
            return {"models": []}

    @router.post("/transcribe")
    async def transcribe_audio(file: UploadFile = File(...)):
        """Transcribe uploaded audio file to text"""
        try:
            if not stt_service.available:
                raise HTTPException(
                    status_code=503,
                    detail={"message": "STT service not available — enable a provider in Settings"}
                )

            audio_bytes = await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")
            if not audio_bytes:
                raise HTTPException(status_code=400, detail={"message": "Empty audio file"})

            text = stt_service.transcribe(audio_bytes)
            if text is None:
                raise HTTPException(
                    status_code=500,
                    detail={"message": "Transcription failed"}
                )

            return {"text": text}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"message": f"Transcription failed: {str(e)}"}
            )

    return router
