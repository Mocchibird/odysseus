"""
video_normalize.py — make uploaded H.264 videos iOS-decodable.

iOS Safari's hardware H.264 decoder tops out at **Level 5.2** and needs the
`moov` atom at the **front** of the file (faststart) for progressive HTTP
playback. Phones, screen recorders and especially VR180/360 rigs routinely
export Level 6.0 / moov-at-end files: they play fine on desktop (more capable
decoders) but throw `MediaError code=4` (MEDIA_ERR_SRC_NOT_SUPPORTED) on iOS —
the video never loads a single frame, which breaks both the plain `<video>`
thumbnail and the 360 viewer's WebGL texture.

The fix is a **lossless stream copy** (no re-encode, runs in seconds even for
multi-GB files): cap the H.264 SPS level flag to 5.2 and move `moov` to the
front. A Level-6.0 stream whose actual macroblock throughput already fits 5.2
(the common case — encoders over-tag) becomes genuinely 5.2-compliant; nothing
is recompressed, so there is zero quality loss.

Scope: only the MP4/MOV family + H.264 is handled — that is what the gallery's
`<video>` and the 360 viewer target. Other codecs (HEVC/VP9/AV1) or containers
(WebM/MKV) are left untouched, since "fixing" them would mean a slow full
re-encode; iOS wouldn't play WebM anyway. Best-effort throughout: any probe or
ffmpeg failure leaves the original file as-is and never breaks an upload.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# iOS H.264 hardware-decode ceiling. ffprobe reports level as an integer where
# 52 == H.264 Level 5.2, 60 == Level 6.0, etc.
_IOS_MAX_H264_LEVEL = 52

# Containers where moov/faststart applies and iOS plays H.264.
_NORMALIZABLE_EXTS = {"mp4", "mov", "m4v"}

_FFPROBE = shutil.which("ffprobe")
_FFMPEG = shutil.which("ffmpeg")


def _probe_video(path: Path) -> Optional[dict]:
    """Return {'codec': str, 'level': int} for the first video stream, or None."""
    if not _FFPROBE:
        return None
    try:
        out = subprocess.run(
            [_FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,level",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        streams = (json.loads(out.stdout or "{}").get("streams") or [])
        if not streams:
            return None
        s = streams[0]
        try:
            level = int(s.get("level"))
        except (TypeError, ValueError):
            level = -1
        return {"codec": (s.get("codec_name") or "").lower(), "level": level}
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        logger.debug("video_normalize: probe failed for %s: %s", path, e)
        return None


def _moov_before_mdat(path: Path) -> Optional[bool]:
    """Scan only the top-level MP4 box headers (fast even for GB files).

    Returns True if `moov` appears before `mdat` (faststart), False if `mdat`
    comes first, None if it can't be determined.
    """
    try:
        with open(path, "rb") as f:
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                size = int.from_bytes(hdr[0:4], "big")
                typ = hdr[4:8]
                if typ == b"moov":
                    return True
                if typ == b"mdat":
                    return False
                if size == 1:
                    # 64-bit largesize follows the 8-byte header.
                    large = f.read(8)
                    if len(large) < 8:
                        return None
                    size = int.from_bytes(large, "big")
                    if size < 16:
                        return None
                    f.seek(size - 16, os.SEEK_CUR)
                elif size == 0:
                    # Box runs to EOF (typically mdat) — moov not seen yet.
                    return False
                else:
                    if size < 8:
                        return None
                    f.seek(size - 8, os.SEEK_CUR)
    except OSError as e:
        logger.debug("video_normalize: moov scan failed for %s: %s", path, e)
        return None


def needs_ios_fix(path: Path, ext: str) -> Tuple[bool, dict]:
    """Decide whether `path` needs an iOS-decodability remux.

    Returns (needs_fix, info) where info carries the probe results + the two
    specific reasons (level_fix / faststart) so the caller can build the
    matching ffmpeg command and log what it did.
    """
    info = {"codec": None, "level": None, "level_fix": False, "faststart": False}
    if ext not in _NORMALIZABLE_EXTS or not _FFPROBE or not _FFMPEG:
        return False, info
    probe = _probe_video(path)
    if not probe:
        return False, info
    info["codec"] = probe["codec"]
    info["level"] = probe["level"]
    # Level cap only applies to H.264 (the level field is codec-specific).
    if probe["codec"] == "h264" and probe["level"] > _IOS_MAX_H264_LEVEL:
        info["level_fix"] = True
    # Faststart helps every container in this family, regardless of codec.
    if _moov_before_mdat(path) is False:
        info["faststart"] = True
    return (info["level_fix"] or info["faststart"]), info


def normalize_in_place(path, ext: str) -> Tuple[bool, dict]:
    """If `path` is an iOS-incompatible MP4/MOV, rewrite it losslessly in place.

    Returns (changed, info). Never raises — a failure leaves the original file
    untouched and returns changed=False.
    """
    path = Path(path)
    needs, info = needs_ios_fix(path, ext)
    if not needs:
        return False, info

    # Keep the real extension so ffmpeg infers the muxer (mp4/mov); the leading
    # dot keeps it from ever matching a served-filename pattern, and it sits in
    # the same directory so os.replace() below is atomic.
    out_tmp = path.with_name(f".v360fix-{uuid.uuid4().hex[:12]}.{ext}")
    cmd = [_FFMPEG, "-y", "-loglevel", "error", "-i", str(path),
           "-map", "0", "-c", "copy", "-movflags", "+faststart"]
    if info["level_fix"]:
        # Patch the SPS level_idc to 5.2 without touching the coded picture data.
        cmd += ["-bsf:v", f"h264_metadata=level={_IOS_MAX_H264_LEVEL // 10}.{_IOS_MAX_H264_LEVEL % 10}"]
    cmd += [str(out_tmp)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode != 0 or not out_tmp.exists() or out_tmp.stat().st_size == 0:
            logger.warning("video_normalize: ffmpeg remux failed for %s (rc=%s): %s",
                           path.name, res.returncode, (res.stderr or "")[:300])
            _safe_unlink(out_tmp)
            return False, info
        os.replace(out_tmp, path)
        logger.info("video_normalize: remuxed %s for iOS (level_fix=%s faststart=%s, "
                    "codec=%s level=%s)", path.name, info["level_fix"],
                    info["faststart"], info["codec"], info["level"])
        info["new_size"] = path.stat().st_size
        return True, info
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("video_normalize: remux error for %s: %s", path.name, e)
        _safe_unlink(out_tmp)
        return False, info


def _safe_unlink(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass
