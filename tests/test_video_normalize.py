"""Tests for src/video_normalize.py — iOS H.264 decodability remux.

The decision logic + MP4 box scanner are tested without ffmpeg (mocked), so
these run in CI regardless of whether ffmpeg is installed. A real round-trip
faststart test runs only when ffmpeg/ffprobe are present.
"""
import shutil
import struct

import pytest

from src import video_normalize as vn


def _box(size: int, typ: bytes) -> bytes:
    # 4-byte big-endian size + 4-byte type + (size-8) bytes of filler.
    return struct.pack(">I", size) + typ + b"\x00" * max(0, size - 8)


def _write(path, data: bytes):
    path.write_bytes(data)
    return str(path)


def test_moov_before_mdat_faststart(tmp_path):
    p = tmp_path / "fs.mp4"
    _write(p, _box(16, b"ftyp") + _box(16, b"moov") + _box(16, b"mdat"))
    assert vn._moov_before_mdat(p) is True


def test_moov_before_mdat_not_faststart(tmp_path):
    p = tmp_path / "nofs.mp4"
    _write(p, _box(16, b"ftyp") + _box(32, b"mdat") + _box(16, b"moov"))
    assert vn._moov_before_mdat(p) is False


def test_moov_scan_handles_zero_size_box_runs_to_eof(tmp_path):
    # size==0 means "this box runs to EOF" (typical mdat) — moov not seen yet.
    p = tmp_path / "zero.mp4"
    _write(p, _box(16, b"ftyp") + struct.pack(">I", 0) + b"mdat" + b"\x00" * 32)
    assert vn._moov_before_mdat(p) is False


def test_needs_ios_fix_skips_non_normalizable_ext(tmp_path):
    p = tmp_path / "clip.webm"
    p.write_bytes(b"\x00" * 64)
    needs, info = vn.needs_ios_fix(p, "webm")
    assert needs is False


def test_needs_ios_fix_skips_when_tools_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(vn, "_FFPROBE", None)
    monkeypatch.setattr(vn, "_FFMPEG", None)
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 64)
    needs, info = vn.needs_ios_fix(p, "mp4")
    assert needs is False


def test_needs_ios_fix_level_and_faststart(tmp_path, monkeypatch):
    monkeypatch.setattr(vn, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(vn, "_FFMPEG", "/usr/bin/ffmpeg")
    monkeypatch.setattr(vn, "_probe_video", lambda p: {"codec": "h264", "level": 60})
    monkeypatch.setattr(vn, "_moov_before_mdat", lambda p: False)
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 64)
    needs, info = vn.needs_ios_fix(p, "mp4")
    assert needs is True
    assert info["level_fix"] is True
    assert info["faststart"] is True


def test_needs_ios_fix_compliant_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(vn, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(vn, "_FFMPEG", "/usr/bin/ffmpeg")
    monkeypatch.setattr(vn, "_probe_video", lambda p: {"codec": "h264", "level": 51})
    monkeypatch.setattr(vn, "_moov_before_mdat", lambda p: True)
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 64)
    needs, info = vn.needs_ios_fix(p, "mp4")
    assert needs is False
    assert info["level_fix"] is False
    assert info["faststart"] is False


def test_non_h264_codec_only_gets_faststart_not_level_fix(tmp_path, monkeypatch):
    # HEVC at a high level must NOT trigger the H.264 level bitstream filter.
    monkeypatch.setattr(vn, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(vn, "_FFMPEG", "/usr/bin/ffmpeg")
    monkeypatch.setattr(vn, "_probe_video", lambda p: {"codec": "hevc", "level": 180})
    monkeypatch.setattr(vn, "_moov_before_mdat", lambda p: False)
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 64)
    needs, info = vn.needs_ios_fix(p, "mp4")
    assert needs is True          # faststart still applies
    assert info["level_fix"] is False


def test_normalize_in_place_noop_when_not_needed(tmp_path, monkeypatch):
    monkeypatch.setattr(vn, "needs_ios_fix", lambda path, ext: (False, {"codec": "h264"}))
    p = tmp_path / "clip.mp4"
    original = b"ORIGINAL-BYTES" * 8
    p.write_bytes(original)
    changed, info = vn.normalize_in_place(p, "mp4")
    assert changed is False
    assert p.read_bytes() == original


def test_normalize_in_place_survives_ffmpeg_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(vn, "_FFMPEG", "/usr/bin/ffmpeg")
    monkeypatch.setattr(vn, "needs_ios_fix",
                        lambda path, ext: (True, {"level_fix": True, "faststart": True,
                                                  "codec": "h264", "level": 60}))

    class _Res:
        returncode = 1
        stderr = "boom"
    monkeypatch.setattr(vn.subprocess, "run", lambda *a, **k: _Res())
    p = tmp_path / "clip.mp4"
    original = b"ORIGINAL" * 16
    p.write_bytes(original)
    changed, info = vn.normalize_in_place(p, "mp4")
    assert changed is False
    assert p.read_bytes() == original  # untouched
    # no temp left behind
    assert not list(tmp_path.glob(".v360fix-*"))


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="ffmpeg/ffprobe not installed")
def test_real_faststart_roundtrip(tmp_path):
    import subprocess
    src = tmp_path / "clip.mp4"
    # Tiny H.264 clip with moov at the END (no faststart).
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=1:size=320x240:rate=15",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, timeout=120,
    )
    assert vn._moov_before_mdat(src) is False  # default ffmpeg = moov at end
    changed, info = vn.normalize_in_place(src, "mp4")
    assert changed is True
    assert info["faststart"] is True
    assert vn._moov_before_mdat(src) is True   # moved to front
    assert not list(tmp_path.glob(".v360fix-*"))
