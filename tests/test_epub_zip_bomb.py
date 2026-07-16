"""Regression: EPUB zip-entry reads are bounded (decompression-bomb guard).

The compressed-upload cap does not bound decompression (deflate ~1000x), so a
small .epub can inflate a chapter/cover to hundreds of GB and OOM the single
worker. src.epub_reader._zip_read_bytes caps each entry read.
"""
import io
import zipfile

import pytest
from fastapi import HTTPException

from src.epub_reader import _zip_read_bytes


def _zip(entries: dict) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_reads_small_entry():
    with _zip({"ch.xhtml": b"<html>hi</html>"}) as zf:
        assert _zip_read_bytes(zf, "ch.xhtml") == b"<html>hi</html>"


def test_missing_entry_raises_422():
    with _zip({"a.txt": b"x"}) as zf:
        with pytest.raises(HTTPException) as ei:
            _zip_read_bytes(zf, "nope.txt")
        assert ei.value.status_code == 422


def test_oversized_entry_rejected_413():
    # Entry larger than the (here tiny) cap must be refused, not decompressed.
    payload = b"A" * 5000
    with _zip({"big.xhtml": payload}) as zf:
        with pytest.raises(HTTPException) as ei:
            _zip_read_bytes(zf, "big.xhtml", max_bytes=1024)
        assert ei.value.status_code == 413


def test_entry_at_cap_ok():
    payload = b"B" * 1024
    with _zip({"ok.xhtml": payload}) as zf:
        assert _zip_read_bytes(zf, "ok.xhtml", max_bytes=1024) == payload
