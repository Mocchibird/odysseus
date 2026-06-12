"""Small helpers for route-local upload size caps."""

import asyncio
import hashlib
import os

from fastapi import HTTPException, UploadFile

DEFAULT_CHAT_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
CHAT_UPLOAD_MAX_BYTES_ENV = "ODYSSEUS_CHAT_UPLOAD_MAX_BYTES"


def format_byte_limit(limit: int) -> str:
    if limit % (1024 * 1024) == 0:
        return f"{limit // (1024 * 1024)} MB"
    if limit % 1024 == 0:
        return f"{limit // 1024} KB"
    return f"{limit} bytes"


def read_byte_limit_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer byte count") from exc
    if limit < 1:
        raise ValueError(f"{name} must be greater than 0")
    return limit


def get_chat_upload_max_bytes() -> int:
    return read_byte_limit_env(CHAT_UPLOAD_MAX_BYTES_ENV, DEFAULT_CHAT_UPLOAD_MAX_BYTES)


# Per-route upload byte-limits, single-sourced here (issue #3364). Each is
# validated + env-overridable via read_byte_limit_env: set the matching
# ODYSSEUS_*_MAX_BYTES env var to an integer byte count to tune it; an invalid
# value fails fast at import rather than crashing mid-request. Defaults match
# the prior per-route values, so behavior is unchanged unless an env var is set.
# 10 GiB: the gallery accepts full-length camera/screen-recording videos, which
# reach into the gigabytes. Large uploads stream straight to disk
# (stream_request_to_path below) — flat memory, one write, no /tmp spool — so
# the cap protects disk space, not RAM. Tune via the env var. NOTE: if a reverse
# proxy fronts the app (nginx client_max_body_size, Cloudflare), its body cap
# must be >= this or the proxy rejects the upload before it reaches the app.
GALLERY_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES", 10 * 1024 * 1024 * 1024
)
GALLERY_TRANSFORM_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES", 25 * 1024 * 1024
)
MEMORY_IMPORT_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_MEMORY_IMPORT_MAX_BYTES", 10 * 1024 * 1024
)
PERSONAL_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_PERSONAL_UPLOAD_MAX_BYTES", 25 * 1024 * 1024
)
EMAIL_COMPOSE_UPLOAD_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_EMAIL_COMPOSE_UPLOAD_MAX_BYTES", 25 * 1024 * 1024
)
STT_MAX_AUDIO_BYTES = read_byte_limit_env(
    "ODYSSEUS_STT_MAX_AUDIO_BYTES", 25 * 1024 * 1024
)
ICS_MAX_BYTES = read_byte_limit_env(
    "ODYSSEUS_ICS_MAX_BYTES", 10 * 1024 * 1024
)


async def read_upload_limited(upload: UploadFile, limit: int, label: str = "Upload") -> bytes:
    """Read an UploadFile with a hard byte cap.

    Buffers the WHOLE body in memory — fine for small uploads (attachments,
    covers, replacements), an OOM hazard for multi-GB media. Anything that
    accepts large files should use stream_upload_to_path instead.
    """
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"{label} exceeds {format_byte_limit(limit)} limit",
        )
    return data


UPLOAD_STREAM_CHUNK = 1024 * 1024  # 1 MiB per read — flat memory at any file size


async def stream_upload_to_path(upload: UploadFile, dest_path, limit: int,
                                label: str = "Upload") -> tuple:
    """Stream an UploadFile to ``dest_path`` in chunks with a hard byte cap.

    Returns ``(sha256_hex, total_bytes)``. Memory stays ~one chunk regardless
    of file size, so multi-GB videos upload without ballooning RSS the way
    read_upload_limited's whole-body buffer would.

    The entire copy runs in ONE worker thread: by the time a handler runs,
    Starlette has fully parsed the multipart body into ``upload.file`` (a
    SpooledTemporaryFile, rolled to disk past its small spool), so synchronous
    reads from it are safe and keep the event loop completely untouched.

    On overflow a 413 is raised; on overflow or any error the partial
    destination file is removed.
    """

    def _copy():
        hasher = hashlib.sha256()
        total = 0
        try:
            upload.file.seek(0)
            with open(dest_path, "wb") as out:
                while True:
                    chunk = upload.file.read(UPLOAD_STREAM_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"{label} exceeds {format_byte_limit(limit)} limit",
                        )
                    hasher.update(chunk)
                    out.write(chunk)
        except BaseException:
            try:
                os.unlink(dest_path)
            except OSError:
                pass
            raise
        return hasher.hexdigest(), total

    return await asyncio.to_thread(_copy)


async def stream_request_to_path(request, dest_path, limit: int, label: str = "Upload") -> tuple:
    """Stream a RAW request body straight to ``dest_path`` in chunks, hashing as
    we go, with a hard byte cap. Returns ``(sha256_hex, total_bytes)``.

    Unlike a multipart form parse (which spools the whole body to a temp file in
    the system temp dir, then gets copied again), this writes the bytes EXACTLY
    once, directly into the destination (kept on the final file's filesystem so
    the caller's os.replace is atomic). Memory stays at ~one network chunk
    regardless of size — this is the path for multi-GB uploads, where the
    spool-then-copy approach means double the disk I/O and a temp dir big enough
    to hold the whole file. The destination is removed on overflow or any error.

    `request` is any object exposing an async `stream()` yielding bytes (a
    Starlette Request). Per-chunk writes are small (network-sized) and run
    between `await`s, so the event loop stays responsive for other requests.
    """
    hasher = hashlib.sha256()
    total = 0
    try:
        with open(dest_path, "wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{label} exceeds {format_byte_limit(limit)} limit",
                    )
                hasher.update(chunk)
                out.write(chunk)
    except BaseException:
        try:
            os.unlink(dest_path)
        except OSError:
            pass
        raise
    return hasher.hexdigest(), total
