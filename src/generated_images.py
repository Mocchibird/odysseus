import os
import re
from pathlib import Path

from fastapi import HTTPException

from src.constants import GENERATED_IMAGES_DIR


GENERATED_IMAGE_DIR = Path(GENERATED_IMAGES_DIR)
GENERATED_IMAGE_RE = re.compile(
    r"^[a-f0-9]{8,64}\.(png|jpg|jpeg|webp|gif|mp4|mov|webm|mkv|m4v)$"
)
GENERATED_IMAGE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    # Already-compressed media (JPEG/PNG/WebP/MP4) — opt out of GZipMiddleware,
    # which would otherwise burn CPU re-deflating incompressible bytes on every
    # cold fetch (Starlette skips responses that carry a Content-Encoding).
    "Content-Encoding": "identity",
}


def resolve_generated_image_path(filename: str) -> Path:
    if not isinstance(filename, str) or not GENERATED_IMAGE_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    root = GENERATED_IMAGE_DIR.resolve()
    path = (GENERATED_IMAGE_DIR / filename).resolve()
    try:
        if os.path.commonpath([str(root), str(path)]) != str(root):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return path
