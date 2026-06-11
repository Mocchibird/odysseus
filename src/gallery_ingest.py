"""
gallery_ingest.py — add an uploaded file to the Gallery (Iris-side).

Lets the agent file a chat-attached image/video into the Gallery — optionally
into a named album (find-or-create) — mirroring POST /api/gallery/upload but
keyed by an upload_id. So "save this screenshot to my <game> album" works:
resolve the upload, dedupe per owner, copy the bytes into the gallery store,
ensure the album exists, and insert the GalleryImage row.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}
_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}


def _find_or_create_album(db, owner: Optional[str], name: str) -> Optional[str]:
    from core.database import GalleryAlbum
    name = (name or "").strip()
    if not name:
        return None
    q = db.query(GalleryAlbum).filter(GalleryAlbum.name == name)
    if owner is not None:
        q = q.filter(GalleryAlbum.owner == owner)
    existing = q.first()
    if existing:
        return existing.id
    album = GalleryAlbum(id=str(uuid.uuid4()), name=name, owner=owner)
    db.add(album)
    db.flush()
    return album.id


def ingest_upload(owner: Optional[str], upload_id: str, *, album: Optional[str] = None,
                  tags: str = "", title: str = "") -> dict:
    """Add an uploaded image/video to the Gallery. Returns a small result dict.
    Raises ValueError on a bad/missing upload or an unsupported type."""
    from core.database import SessionLocal, GalleryImage
    from src.constants import BASE_DIR, UPLOAD_DIR, GENERATED_IMAGES_DIR
    from src.upload_handler import UploadHandler

    upload_id = (upload_id or "").strip()
    if not upload_id:
        raise ValueError("upload_id required")
    info = UploadHandler(BASE_DIR, UPLOAD_DIR).resolve_upload(upload_id, owner=owner)
    if not info or not info.get("path"):
        raise ValueError(f"Upload '{upload_id}' not found")

    src_path = info["path"]
    original = str(info.get("name") or info.get("original_name") or upload_id)
    ext = (original.rsplit(".", 1)[-1].lower() if "." in original else "")
    if ext not in _VIDEO_EXTS and ext not in _IMAGE_EXTS:
        raise ValueError(f"Not an image/video (.{ext}) — use manage_files for other types")
    is_video = ext in _VIDEO_EXTS

    with open(src_path, "rb") as fh:
        content = fh.read()
    file_hash = hashlib.sha256(content).hexdigest()

    db = SessionLocal()
    try:
        dup_q = db.query(GalleryImage).filter(
            GalleryImage.file_hash == file_hash, GalleryImage.is_active == True
        )
        if owner is not None:
            dup_q = dup_q.filter(GalleryImage.owner == owner)
        existing = dup_q.first()
        if existing:
            # Already in the gallery — just (re)file it into the album if asked.
            if album:
                existing.album_id = _find_or_create_album(db, owner, album)
                db.commit()
            return {"id": existing.id, "filename": existing.filename,
                    "album_id": existing.album_id, "duplicate": True}

        os.makedirs(GENERATED_IMAGES_DIR, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.{ext}"
        with open(os.path.join(GENERATED_IMAGES_DIR, filename), "wb") as out:
            out.write(content)

        exif = {}
        if not is_video:
            try:
                from routes.gallery_helpers import _extract_exif
                exif = _extract_exif(content) or {}
            except Exception:
                exif = {}

        album_id = _find_or_create_album(db, owner, album) if album else None
        label = (title or "").strip() or (original.rsplit(".", 1)[0] if "." in original else original)
        img_id = str(uuid.uuid4())
        db.add(GalleryImage(
            id=img_id, filename=filename, prompt=label, model="imported", owner=owner,
            media_type="video" if is_video else "image",
            tags=(tags or "").strip(), album_id=album_id, file_hash=file_hash,
            file_size=len(content), width=exif.get("width"), height=exif.get("height"),
            taken_at=exif.get("taken_at"), camera_make=exif.get("camera_make"),
            camera_model=exif.get("camera_model"), gps_lat=exif.get("gps_lat"),
            gps_lng=exif.get("gps_lng"),
        ))
        db.commit()
        return {"id": img_id, "filename": filename, "album_id": album_id,
                "media_type": "video" if is_video else "image"}
    finally:
        db.close()
