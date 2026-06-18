"""Guards for the optional local voice stack (faster-whisper STT + Kokoro TTS).

These are in-process deps (services/stt + services/tts lazy-import them), so the
durable install path is requirements-optional.txt + the Dockerfile's
INSTALL_OPTIONAL build arg — NOT the cookbook (which serves separate model
servers). Kokoro must run on a CUDA GPU when present and fall back to CPU
otherwise, so it works on a GPU box (e.g. a GTX 1080 Ti) and degrades gracefully
without one. We can't import torch/kokoro in CI, so these are source guards.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_voice_deps_in_requirements_optional():
    req = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8")
    assert "faster-whisper" in req          # local STT (already shipped)
    assert "kokoro" in req                   # local TTS (added)
    assert "soundfile" in req


def test_dockerfile_installs_optional_and_phonemizer():
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    # The opt-in build path that bakes the local voice deps into the image.
    assert "INSTALL_OPTIONAL" in df
    assert "requirements-optional.txt" in df
    # espeak-ng backs Kokoro's phonemizer for out-of-dictionary words.
    assert "espeak-ng" in df


def test_kokoro_pipeline_is_cpu_capable():
    src = (ROOT / "services" / "tts" / "tts_service.py").read_text(encoding="utf-8")
    # GPU when available, CPU otherwise — not the old hard cuda:0-only path.
    assert 'torch.device("cuda:0" if use_cuda else "cpu")' in src
    # The cuda device-context must be skipped on CPU (else it raises).
    assert "contextlib.nullcontext()" in src
    assert 'self.device.type == "cuda"' in src
    # The old "bail out entirely when no CUDA" behaviour is gone.
    assert 'logger.warning("CUDA not available for Kokoro TTS")' not in src
