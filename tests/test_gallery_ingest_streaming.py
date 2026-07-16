"""Guard: gallery ingest streams; it must not buffer the whole upload in RAM.

A gallery ingest can be a multi-GB video. src/gallery_ingest.py used to read the
whole file into memory to hash + write it, OOM'ing the single worker (the direct
upload route was already rewritten to stream). No functional test catches a
revert to whole-file buffering, so pin the streaming primitives here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gallery_ingest_streams_not_buffers():
    src = (ROOT / "src" / "gallery_ingest.py").read_text(encoding="utf-8")
    # stream-hash off disk (64 KiB chunks) and chunked file copy — flat memory
    assert "content_extract.sha256_file(src_path)" in src
    assert "shutil.copyfile(src_path, dest_path)" in src
    assert "os.path.getsize(dest_path)" in src
    # the old whole-file buffer patterns must be gone
    assert "fh.read()" not in src
    assert "out.write(content)" not in src
    assert "file_size = len(content)" not in src
