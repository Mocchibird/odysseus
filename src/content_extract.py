"""
content_extract.py — shared text extraction + tag helpers for content stores.

Books, the Files store (and the legacy Knowledge base while it is retired) all
turn an uploaded file into searchable text the same way through this module, so
the extraction logic lives in exactly one place. Reuses, never reinvents:
  • pdf / office / plain text -> src.personal_docs
  • image OCR / caption        -> src.document_processor.analyze_image_with_vl
  • topical auto-tags          -> the Utility model (src.llm_core fallback chain)
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".xls", ".epub"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".log",
             ".html", ".htm", ".rst", ".yaml", ".yml", ".tsv"}
# Binary types markitdown can't turn into text — skip extraction (the file is
# still stored + taggable, just has no searchable text) to avoid noisy
# "filetype not supported" warnings and wasted conversion attempts.
SKIP_EXTS = {
    ".ogg", ".mp3", ".wav", ".m4a", ".flac", ".aac", ".opus", ".wma",  # audio
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv",           # video
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2",              # archives
    ".bin", ".exe", ".dmg", ".iso", ".so", ".dll",                     # binaries
}


def sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def extract_text(file_path: str, filename: str = "", mime: str = "",
                 owner: Optional[str] = None) -> str:
    """Best-effort text extraction by file type. Images go through vision
    OCR/captioning so they're searchable too. Always returns a string ("" when
    nothing could be extracted — the file is still stored + taggable)."""
    ext = os.path.splitext(filename or file_path)[1].lower()
    mime = (mime or "").lower()
    if ext in SKIP_EXTS or mime.startswith(("audio/", "video/")):
        return ""  # binary/media — stored + taggable, but no text to extract
    try:
        if ext in PDF_EXTS or mime == "application/pdf":
            from src.personal_docs import extract_pdf_text
            return extract_pdf_text(file_path) or ""
        if ext in IMAGE_EXTS or mime.startswith("image/"):
            try:
                from src.document_processor import analyze_image_with_vl
                return (analyze_image_with_vl(file_path, owner=owner) or "").strip()
            except Exception as e:
                logger.debug("content_extract: image OCR failed for %s: %s", filename, e)
                return ""
        if ext in TEXT_EXTS or mime.startswith("text/"):
            from src.personal_docs import read_text_file
            return read_text_file(file_path) or ""
        if ext in OFFICE_EXTS:
            from src.personal_docs import extract_office_text
            return extract_office_text(file_path) or ""
        # Unknown type: try the office/markitdown path, else give up gracefully.
        from src.personal_docs import extract_office_text
        return extract_office_text(file_path) or ""
    except Exception as e:
        logger.warning("content_extract: text extraction failed for %s: %s", filename, e)
        return ""


def norm_tags(tags) -> str:
    """Normalize tags to a de-duplicated, comma-separated string."""
    if isinstance(tags, (list, tuple)):
        parts = [str(t).strip() for t in tags]
    else:
        parts = [t.strip() for t in str(tags or "").split(",")]
    seen, out = set(), []
    for t in parts:
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            out.append(t)
    return ", ".join(out)


def split_tags(value) -> List[str]:
    return [t.strip() for t in str(value or "").split(",") if t.strip()]


def generate_tags_via_llm(text: str, owner: Optional[str]) -> List[str]:
    """Ask the configured Utility model for a few lowercase topical tags for a
    document's text. Returns [] on any failure so auto-tagging never blocks
    ingest/edit."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates
        from src.llm_core import llm_call_with_fallback

        url, model, headers = resolve_endpoint("utility", owner=owner)
        candidates = [(url, model, headers)] + resolve_utility_fallback_candidates(owner=owner)
        prompt = (
            "Read the document below and output 3-8 short, lowercase topical tags "
            "that capture what it is ABOUT (single words or 2-3 word phrases; no "
            "sentences). Return ONLY a comma-separated list, nothing else.\n\n"
            f"---\n{text[:6000]}\n---"
        )
        out = llm_call_with_fallback(
            candidates, [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=120,
        ) or ""
        seen, clean = set(), []
        for raw in out.replace("\n", ",").split(","):
            t = raw.strip().strip("#.-•*").strip().lower()
            if t and len(t) <= 40 and t not in seen:
                seen.add(t)
                clean.append(t)
            if len(clean) >= 8:
                break
        return clean
    except Exception as e:
        logger.debug("content_extract: ai-tag generation failed: %s", e)
        return []
