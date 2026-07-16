"""
gallery_ingest.py — add an uploaded file to the Gallery (Iris-side).

Lets the agent file a chat-attached image/video into the Gallery — optionally
into a named album (find-or-create) — mirroring POST /api/gallery/upload but
keyed by an upload_id. So "save this screenshot to my <game> album" works:
resolve the upload, dedupe per owner, copy the bytes into the gallery store,
ensure the album exists, and insert the GalleryImage row.
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}
_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}

# Fallback when the upload's stored NAME has no usable extension (e.g. a pasted
# PC screenshot or an iPhone share saved without one). The direct gallery upload
# defaults a missing extension to a sane value instead of rejecting; ingest used
# to default to "" and then reject/mis-store → broken/blank gallery thumbnail.
_MIME_TO_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/webp": "webp", "image/gif": "gif",
    "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
    "video/x-matroska": "mkv",
}


def _pick_ext(name: str, mime: str = "") -> str:
    """Choose a storage extension for an upload: prefer a known extension on the
    name, else derive from the mime type. Returns "" if neither yields a
    supported image/video extension (caller rejects)."""
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    if ext in _VIDEO_EXTS or ext in _IMAGE_EXTS:
        return ext
    return _MIME_TO_EXT.get((mime or "").split(";")[0].strip().lower(), "")


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
    ext = _pick_ext(original, info.get("mime") or "")
    if ext not in _VIDEO_EXTS and ext not in _IMAGE_EXTS:
        raise ValueError(f"Not an image/video (.{ext or '?'}) — use manage_files for other types")
    is_video = ext in _VIDEO_EXTS

    # Hash by streaming off disk — do NOT read the whole file into RAM. A
    # gallery ingest can be a multi-GB video (the direct /api/gallery/upload
    # route was rewritten to stream for exactly this reason); buffering the
    # whole upload here OOM'd the single worker on the resource-light box.
    from src import content_extract
    file_hash = content_extract.sha256_file(src_path)

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
        dest_path = os.path.join(GENERATED_IMAGES_DIR, filename)
        shutil.copyfile(src_path, dest_path)   # chunked copy — flat memory
        file_size = os.path.getsize(dest_path)
        # Make H.264 videos iOS-decodable (lossless level-cap + faststart);
        # best-effort, leaves the file unchanged on any failure.
        if is_video:
            try:
                from src.video_normalize import normalize_in_place
                changed, _ = normalize_in_place(dest_path, ext)
                if changed:
                    file_size = os.path.getsize(dest_path)
            except Exception as _ve:
                logger.warning("Gallery ingest video normalize skipped: %s", _ve)

        exif = {}
        if not is_video:
            # Images are small enough to read for EXIF; videos (the OOM risk)
            # are never read into memory. Read the copied file (== source bytes).
            try:
                from routes.gallery_helpers import _extract_exif
                with open(dest_path, "rb") as _img:
                    exif = _extract_exif(_img.read()) or {}
            except Exception:
                exif = {}

        album_id = _find_or_create_album(db, owner, album) if album else None
        label = (title or "").strip() or (original.rsplit(".", 1)[0] if "." in original else original)
        img_id = str(uuid.uuid4())
        db.add(GalleryImage(
            id=img_id, filename=filename, prompt=label, model="imported", owner=owner,
            media_type="video" if is_video else "image",
            tags=(tags or "").strip(), album_id=album_id, file_hash=file_hash,
            file_size=file_size, width=exif.get("width"), height=exif.get("height"),
            taken_at=exif.get("taken_at"), camera_make=exif.get("camera_make"),
            camera_model=exif.get("camera_model"), gps_lat=exif.get("gps_lat"),
            gps_lng=exif.get("gps_lng"),
        ))
        db.commit()
        return {"id": img_id, "filename": filename, "album_id": album_id,
                "media_type": "video" if is_video else "image"}
    finally:
        db.close()
