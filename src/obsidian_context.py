"""Shared Obsidian-backed context for every Odysseus session."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_RELATIVE_PATH = "00_Index/assistant_rules.md"
DEFAULT_CONTEXT_MAX_CHARS = 60000

_cache_key: tuple[str, float, int, int] | None = None
_cache_value: str = ""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def _vault_root() -> Optional[Path]:
    raw = (
        os.environ.get("ODYSSEUS_OBSIDIAN_VAULT_ROOT")
        or os.environ.get("IRIS_VAULT_ROOT")
        or os.environ.get("OBSIDIAN_VAULT_PATH")
        or os.environ.get("VAULT_ROOT")
        or ""
    ).strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve(strict=False)


def obsidian_context_path() -> Optional[Path]:
    root = _vault_root()
    if root is None:
        return None
    rel = (
        os.environ.get("ODYSSEUS_OBSIDIAN_CONTEXT_PATH")
        or DEFAULT_CONTEXT_RELATIVE_PATH
    ).strip()
    candidate = (root / rel).expanduser().resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        logger.warning("Ignoring Obsidian context path outside vault: %s", candidate)
        return None
    return candidate


def _context_relative_path() -> str:
    return (
        os.environ.get("ODYSSEUS_OBSIDIAN_CONTEXT_PATH")
        or DEFAULT_CONTEXT_RELATIVE_PATH
    ).strip()


def load_obsidian_session_context() -> str:
    """Read the persistent user context note for prompt injection.

    The file is user-authored session guidance, so callers place it in the
    trusted system context. Missing/unconfigured files are a quiet no-op.
    """
    global _cache_key, _cache_value

    path = obsidian_context_path()
    if path is None or not path.is_file():
        return ""

    max_chars = _env_int("ODYSSEUS_OBSIDIAN_CONTEXT_MAX_CHARS", DEFAULT_CONTEXT_MAX_CHARS)
    if max_chars <= 0:
        return ""

    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime, stat.st_size, max_chars)
        if _cache_key == key:
            return _cache_value
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        logger.warning("Failed to read Obsidian session context: %s", exc)
        return ""

    if len(text) > max_chars:
        text = (
            text[:max_chars].rstrip()
            + f"\n\n[Obsidian session context truncated at {max_chars} characters.]"
        )

    if text:
        text = (
            "## Persistent Obsidian Session Context\n"
            f"Source: {path.name} ({_context_relative_path()})\n\n"
            f"{text}"
        )

    _cache_key = key
    _cache_value = text
    return text
