"""GB-scale gallery uploads: chunked streaming + hard-timeout exemption.

gallery_upload used to buffer the WHOLE body in RAM (read_upload_limited) and
ran under the 45s request hard-timeout — so multi-GB videos either 413'd
(100 MB cap), OOM-threatened the shared box, or died mid-transfer as
"network" errors. These tests pin the fix:
- stream_upload_to_path copies in 1 MiB chunks with a correct rolling SHA-256,
  enforces the cap with a 413, and never leaves a partial file behind;
- gallery_upload uses the streaming helper (not the whole-body buffer);
- /api/gallery/upload (and download-zip) are exempt from the request
  hard-timeout so long transfers aren't killed at 45s.
"""
import asyncio
import hashlib
import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from src.upload_limits import UPLOAD_STREAM_CHUNK, stream_upload_to_path

REPO = Path(__file__).resolve().parent.parent


def _upload_of(data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename="clip.mp4")


def test_stream_writes_bytes_and_hashes_correctly(tmp_path):
    # 2.5 chunks so the loop runs multi-iteration with a ragged tail.
    data = bytes(range(256)) * ((UPLOAD_STREAM_CHUNK * 5 // 2) // 256)
    dest = tmp_path / "out.mp4"
    digest, total = asyncio.run(
        stream_upload_to_path(_upload_of(data), dest, limit=len(data) + 1)
    )
    assert total == len(data)
    assert digest == hashlib.sha256(data).hexdigest()
    assert dest.read_bytes() == data


def test_stream_over_limit_raises_413_and_removes_partial(tmp_path):
    data = b"x" * (UPLOAD_STREAM_CHUNK * 2)
    dest = tmp_path / "out.mp4"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            stream_upload_to_path(_upload_of(data), dest, limit=UPLOAD_STREAM_CHUNK)
        )
    assert exc.value.status_code == 413
    assert not dest.exists(), "partial file must be cleaned up on overflow"


def test_gallery_upload_streams_instead_of_buffering():
    src = (REPO / "routes" / "gallery_routes.py").read_text(encoding="utf-8")
    start = src.index("async def gallery_upload")
    end = src.index("@router.post", start + 1)
    body = src[start:end]
    assert "stream_upload_to_path(" in body
    # The whole-body buffer must not come back for the main upload path.
    assert "read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES" not in body


def test_gallery_upload_exempt_from_hard_timeout():
    src = (REPO / "app.py").read_text(encoding="utf-8")
    start = src.index("_TIMEOUT_EXEMPT_PREFIXES")
    block = src[start:src.index("\n)", start)]
    assert '"/api/gallery/upload"' in block
    assert '"/api/gallery/download-zip"' in block
