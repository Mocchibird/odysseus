"""Iris Obsidian vault storage and indexing.

Files are stored under:

    <ODYSSEUS_OBSIDIAN_VAULT_ROOT>/<username>/

The SQLite index mirrors file metadata and searchable text so Iris can retrieve
user-scoped vault files without scanning the whole vault on every request.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import or_

from core.database import IrisVaultFile, SessionLocal

logger = logging.getLogger(__name__)

DEFAULT_MAX_TEXT_CHARS = 1_000_000
DEFAULT_EXCERPT_CHARS = 2_000
VECTOR_COLLECTION_NAME = "iris_vault"
VECTOR_CHUNK_CHARS = 2_400
VECTOR_CHUNK_OVERLAP = 300
VECTOR_SEARCH_FETCH_MULTIPLIER = 4
TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".org",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".csv", ".tsv",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss",
    ".sh", ".zsh", ".bash", ".sql", ".xml",
}

ATTACHMENT_DIR_BY_KIND = {
    "epub": "epubs",
    "image": "images",
    "audio": "audio",
    "video": "video",
    "pdf": "pdf",
    "document": "documents",
    "spreadsheet": "spreadsheets",
    "archive": "archives",
    "code": "code",
    "text": "text",
    "other": "other",
}

BOOK_ATTACHMENT_DIR = "40_Attachments/epubs"
INBOX_DIR_NAMES = ("90_Inbox", "Inbox")

CONTEXT_SUBDIR_KEYWORDS = {
    "food": {
        "kcal", "calorie", "meal", "food", "recipe", "restaurant", "lunch", "dinner",
        "breakfast", "snack", "cook", "cooking", "protein", "nutrition",
    },
    "health": {
        "weight", "fitness", "health", "workout", "gym", "sleep", "blood", "steps",
        "body", "medical", "doctor",
    },
    "career": {
        "cv", "resume", "job", "career", "interview", "application", "offer",
        "contract", "company", "work",
    },
    "finance": {
        "invoice", "receipt", "tax", "bank", "payment", "bill", "salary", "budget",
        "finance",
    },
    "travel": {
        "travel", "trip", "flight", "hotel", "ticket", "booking", "passport",
        "visa",
    },
    "japanese": {
        "japanese", "japan", "nihongo", "kanji", "anime", "manga",
    },
    "screenshots": {
        "screenshot", "screen shot", "screen-shot", "chart", "graph", "plot",
    },
}

BOOK_CONTEXT_KEYWORDS = {
    "book", "ebook", "e-book", "reader", "novel", "chapter", "manga", "epub",
    "read this", "reading",
}


def _clean_filename(name: str, *, default_name: str = "upload") -> str:
    raw = unicodedata.normalize("NFKC", name or "").replace("\\", "/").split("/")[-1]
    raw = raw.strip().strip(".")
    if not raw:
        raw = default_name
    stem = Path(raw).stem or default_name
    suffix = Path(raw).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_.@() -]+", "_", stem).strip(" ._-") or default_name
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix)[:20]
    return f"{stem[:140]}{suffix}"


def _attachment_kind(filename: str, mime: str = "") -> str:
    ext = Path(filename or "").suffix.lower()
    mime = (mime or mimetypes.guess_type(filename or "")[0] or "").lower()
    if ext == ".epub" or mime == "application/epub+zip":
        return "epub"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in {".doc", ".docx", ".odt", ".rtf", ".pages"}:
        return "document"
    if ext in {".xls", ".xlsx", ".ods", ".numbers"}:
        return "spreadsheet"
    if ext in {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"}:
        return "archive"
    if ext in {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".sh", ".sql", ".xml"}:
        return "code"
    if ext in TEXT_EXTENSIONS or mime.startswith("text/"):
        return "text"
    return "other"


def _semantic_context_subdir(kind: str, filename: str, context: str = "") -> str:
    if kind not in {"image", "pdf", "document", "spreadsheet", "text", "other"}:
        return ""
    haystack = f"{filename} {context}".lower()
    for subdir, keywords in CONTEXT_SUBDIR_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return subdir
    return ""


def _looks_like_book_upload(filename: str, mime: str = "", context: str = "") -> bool:
    ext = Path(filename or "").suffix.lower()
    if ext == ".epub" or (mime or "").lower() == "application/epub+zip":
        return True
    if ext != ".pdf" and (mime or "").lower() != "application/pdf":
        return False
    haystack = f"{filename} {context}".lower()
    return any(keyword in haystack for keyword in BOOK_CONTEXT_KEYWORDS)


def book_upload_rel_path(filename: str, mime: str = "") -> str:
    safe_name = _clean_filename(filename, default_name="book")
    return f"{BOOK_ATTACHMENT_DIR}/{safe_name}"


def default_upload_rel_path(filename: str, mime: str = "", *, context: str = "", source: str = "") -> str:
    """Choose a user-vault destination for uploads with no explicit path."""
    safe_name = _clean_filename(filename)
    ext = Path(safe_name).suffix.lower()
    if ext in {".md", ".markdown"}:
        return f"10_Notes/{safe_name}"
    if _looks_like_book_upload(safe_name, mime, context):
        return book_upload_rel_path(safe_name, mime)
    kind = _attachment_kind(safe_name, mime)
    folder = ATTACHMENT_DIR_BY_KIND.get(kind, "other")
    subdir = _semantic_context_subdir(kind, safe_name, context)
    if subdir:
        return f"40_Attachments/{folder}/{subdir}/{safe_name}"
    return f"40_Attachments/{folder}/{safe_name}"


def _unique_rel_path(owner: str | None, rel_path: str) -> str:
    safe = _safe_rel_path(rel_path, default_name="upload")
    base = owner_root(owner)
    target = (base / safe).resolve(strict=False)
    if not target.exists():
        return safe
    path = Path(safe)
    parent = path.parent.as_posix()
    stem = path.stem
    suffix = path.suffix
    for idx in range(2, 1000):
        candidate_name = f"{stem} {idx}{suffix}"
        candidate = candidate_name if parent == "." else f"{parent}/{candidate_name}"
        if not (base / candidate).exists():
            return candidate
    raise HTTPException(409, "Could not choose a unique vault filename")


def vault_root() -> Path:
    raw = (
        os.environ.get("ODYSSEUS_OBSIDIAN_VAULT_ROOT")
        or os.environ.get("IRIS_VAULT_ROOT")
        or os.environ.get("OBSIDIAN_VAULT_PATH")
        or os.environ.get("VAULT_ROOT")
        or ""
    ).strip()
    if not raw:
        raise HTTPException(503, "Obsidian vault root is not configured")
    root = Path(raw).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def owner_folder_name(owner: str | None) -> str:
    raw = (owner or "local").strip() or "local"
    # Keep usernames recognizable (including email-like names) while refusing
    # path separators/control characters.
    safe = re.sub(r"[^A-Za-z0-9_.@-]+", "_", raw).strip("._-")
    return safe[:120] or "local"


def owner_root(owner: str | None) -> Path:
    root = vault_root()
    path = (root / owner_folder_name(owner)).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Unsafe owner path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_rel_path(rel_path: str, *, default_name: str = "note.md") -> str:
    # Preserve the real filename (unicode, spaces, punctuation like Café.md or
    # "Notes & Ideas.md") so a listed file can be read back exactly. Traversal is
    # blocked by dropping "."/".." segments here AND the relative_to() guard in
    # resolve_owner_file; we only strip control characters.
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel:
        rel = default_name
    parts = []
    for part in rel.split("/"):
        part = re.sub(r"[\x00-\x1f\x7f]", "", part.strip())
        if not part or part in {".", ".."}:
            continue
        parts.append(part[:200])
    if not parts:
        parts = [default_name]
    return "/".join(parts)


def resolve_owner_file(owner: str | None, rel_path: str) -> Path:
    base = owner_root(owner)
    target = (base / _safe_rel_path(rel_path)).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Unsafe vault file path")
    return target


def _read_indexable_text(path: Path, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> str:
    ext = path.suffix.lower()
    mime = mimetypes.guess_type(str(path))[0] or ""
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            parts = []
            for idx, page in enumerate(reader.pages):
                if len("\n".join(parts)) >= max_chars:
                    break
                try:
                    text = (page.extract_text() or "").strip()
                except Exception:
                    text = ""
                if text:
                    parts.append(f"Page {idx + 1}\n{text}")
            return "\n\n".join(parts)[:max_chars]
        except Exception:
            return ""
    if ext == ".epub":
        try:
            with zipfile.ZipFile(path) as zf:
                parts = []
                names = [
                    name for name in zf.namelist()
                    if name.lower().endswith((".xhtml", ".html", ".htm"))
                    and not name.startswith("__MACOSX/")
                ]
                for name in names:
                    if len("\n".join(parts)) >= max_chars:
                        break
                    raw = zf.read(name).decode("utf-8", errors="replace")
                    try:
                        from bs4 import BeautifulSoup
                        text = BeautifulSoup(raw, "html.parser").get_text("\n", strip=True)
                    except Exception:
                        text = re.sub(r"<[^>]+>", " ", raw)
                    text = re.sub(r"\s+", " ", text).strip()
                    if text:
                        parts.append(text)
                return "\n\n".join(parts)[:max_chars]
        except Exception:
            return ""
    if ext not in TEXT_EXTENSIONS and not mime.startswith("text/"):
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _title_for(path: Path, content: str) -> str:
    if content:
        in_frontmatter = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                if stripped.lower().startswith("title:"):
                    title = stripped.split(":", 1)[1].strip().strip("'\"")
                    if title:
                        return title[:200]
                continue
            if re.match(r"^[A-Za-z0-9_-]+:\s*", stripped):
                continue
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()[:200] or path.stem
            if stripped:
                return stripped[:200]
    return path.stem or path.name


def _vector_enabled() -> bool:
    return os.getenv("ODYSSEUS_IRIS_VAULT_VECTOR_INDEX", "1").strip().lower() not in {"0", "false", "no", "off"}


def _vector_owner_path(owner: str, rel_path: str) -> str:
    return f"{owner}/{rel_path}"


def _vector_chunk_id(owner: str, rel_path: str, sha256: str, chunk_index: int) -> str:
    raw = f"{owner}\x00{rel_path}\x00{sha256}\x00{chunk_index}"
    return f"iris_{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _chunk_text_for_vectors(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + VECTOR_CHUNK_CHARS, text_len)
        if end < text_len:
            boundary = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if boundary > start + int(VECTOR_CHUNK_CHARS * 0.55):
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        start = max(0, end - VECTOR_CHUNK_OVERLAP)
    return chunks


def _vector_collection():
    if not _vector_enabled():
        return None
    try:
        from src.chroma_client import get_chroma_client

        return get_chroma_client().get_or_create_collection(
            name=VECTOR_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:
        logger.warning("Iris vault vector index unavailable: %s", exc)
        return None


def _vector_collection_and_embedder():
    collection = _vector_collection()
    if collection is None:
        return None, None
    try:
        from src.embeddings import get_embedding_client

        embedder = get_embedding_client()
        if embedder is None:
            raise RuntimeError("No embedding backend available")
        return collection, embedder
    except Exception as exc:
        logger.warning("Iris vault vector embeddings unavailable: %s", exc)
        return None, None


def _embed_chunks(embedder, chunks: list[str]) -> list[list[float]]:
    vecs = embedder.encode(chunks, normalize_embeddings=True)
    return [list(map(float, vec)) for vec in vecs]


def _delete_vector_chunks(owner: str, rel_path: str) -> None:
    collection = _vector_collection()
    if collection is None:
        return
    owner_path = _vector_owner_path(owner, rel_path)
    try:
        existing = collection.get(where={"owner_path": owner_path})
        ids = existing.get("ids") or []
        if ids:
            collection.delete(ids=ids)
    except Exception as exc:
        logger.warning("Iris vault vector delete failed for %s: %s", owner_path, exc)


def _index_vector_chunks(payload: dict) -> None:
    content = payload.get("content") or ""
    chunks = _chunk_text_for_vectors(content)
    owner = payload["owner"]
    rel_path = payload["rel_path"]
    _delete_vector_chunks(owner, rel_path)
    if not chunks:
        return

    collection, embedder = _vector_collection_and_embedder()
    if collection is None or embedder is None:
        return

    try:
        ids = [
            _vector_chunk_id(owner, rel_path, payload["sha256"], idx)
            for idx, _chunk in enumerate(chunks)
        ]
        metadatas = [
            {
                "owner": owner,
                "owner_path": _vector_owner_path(owner, rel_path),
                "path": rel_path,
                "title": payload.get("title") or rel_path,
                "sha256": payload["sha256"],
                "chunk": idx,
                "source": "iris_vault",
            }
            for idx, _chunk in enumerate(chunks)
        ]
        for start in range(0, len(chunks), 100):
            batch_chunks = chunks[start:start + 100]
            collection.upsert(
                ids=ids[start:start + 100],
                embeddings=_embed_chunks(embedder, batch_chunks),
                documents=batch_chunks,
                metadatas=metadatas[start:start + 100],
            )
    except Exception as exc:
        logger.warning("Iris vault vector index failed for %s/%s: %s", owner, rel_path, exc)


def _vector_search(owner: str, query: str, limit: int) -> list[dict]:
    needle = (query or "").strip()
    if not needle:
        return []
    collection, embedder = _vector_collection_and_embedder()
    if collection is None or embedder is None:
        return []
    try:
        count = collection.count()
        if count <= 0:
            return []
        query_embedding = _embed_chunks(embedder, [needle])
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=min(max(limit * VECTOR_SEARCH_FETCH_MULTIPLIER, limit), count),
            where={"owner": owner},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        logger.warning("Iris vault vector search failed for %s: %s", owner, exc)
        return []

    hits: list[dict] = []
    ids = (results.get("ids") or [[]])[0]
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    for idx, _doc_id in enumerate(ids):
        meta = metas[idx] or {}
        rel_path = meta.get("path") or ""
        if not rel_path:
            continue
        distance = float(distances[idx]) if idx < len(distances) else 1.0
        hits.append({
            "path": rel_path,
            "document": docs[idx] if idx < len(docs) else "",
            "score": round(1.0 - distance, 4),
        })
    return hits


def _index_row_payload(owner: str, rel_path: str, path: Path, *, index_content: bool = True) -> dict:
    stat = path.stat()
    content = _read_indexable_text(path) if index_content else ""
    return {
        "owner": owner,
        "rel_path": rel_path,
        "title": _title_for(path, content),
        "mime": mimetypes.guess_type(str(path))[0] or "",
        "size": int(stat.st_size),
        "sha256": _file_hash(path),
        "mtime": datetime.fromtimestamp(stat.st_mtime),
        "excerpt": content[:DEFAULT_EXCERPT_CHARS],
        "content": content,
    }


def index_file(owner: str | None, path: Path, *, index_content: bool = True) -> IrisVaultFile:
    owner_key = owner_folder_name(owner)
    base = owner_root(owner_key)
    path = path.resolve(strict=False)
    try:
        rel_path = path.relative_to(base).as_posix()
    except ValueError:
        raise HTTPException(400, "File is outside this user's vault folder")
    payload = _index_row_payload(owner_key, rel_path, path, index_content=index_content)
    db = SessionLocal()
    try:
        row = db.query(IrisVaultFile).filter(
            IrisVaultFile.owner == owner_key,
            IrisVaultFile.rel_path == rel_path,
        ).first()
        if not row:
            row = IrisVaultFile(id=hashlib.sha256(f"{owner_key}/{rel_path}".encode()).hexdigest())
            db.add(row)
        for key, value in payload.items():
            setattr(row, key, value)
        db.commit()
        db.refresh(row)
        if index_content:
            _index_vector_chunks(payload)
        return row
    finally:
        db.close()


def write_text_file(owner: str | None, rel_path: str, content: str) -> IrisVaultFile:
    target = resolve_owner_file(owner, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return index_file(owner, target)


def write_bytes_file(owner: str | None, rel_path: str, content: bytes, *, index_content: bool = True) -> IrisVaultFile:
    target = resolve_owner_file(owner, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return index_file(owner, target, index_content=index_content)


def save_uploaded_file(
    owner: str | None,
    filename: str,
    content: bytes,
    *,
    rel_path: str = "",
    mime: str = "",
    context: str = "",
    source: str = "",
    index_content: bool = True,
) -> IrisVaultFile:
    target_rel = _safe_rel_path(rel_path) if rel_path else default_upload_rel_path(
        filename,
        mime,
        context=context,
        source=source,
    )
    target_rel = _unique_rel_path(owner, target_rel)
    return write_bytes_file(owner, target_rel, content, index_content=index_content)


def _delete_index_row(owner_key: str, rel_path: str) -> None:
    _delete_vector_chunks(owner_key, rel_path)
    db = SessionLocal()
    try:
        row = db.query(IrisVaultFile).filter(
            IrisVaultFile.owner == owner_key,
            IrisVaultFile.rel_path == rel_path,
        ).first()
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def move_file(owner: str | None, source_rel_path: str, target_rel_path: str) -> dict:
    owner_key = owner_folder_name(owner)
    safe_source = _safe_rel_path(source_rel_path)
    source = resolve_owner_file(owner_key, safe_source)
    if not source.is_file():
        raise HTTPException(404, "Vault file not found")
    safe_target = _unique_rel_path(owner_key, target_rel_path)
    target = resolve_owner_file(owner_key, safe_target)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    _delete_index_row(owner_key, safe_source)
    row = index_file(owner_key, target)

    base = owner_root(owner_key)
    parent = source.parent
    while parent != base and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

    return {
        "from": safe_source,
        "to": safe_target,
        "file": row_to_dict(row),
    }


def sort_inbox(owner: str | None, *, limit: int = 200) -> list[dict]:
    owner_key = owner_folder_name(owner)
    base = owner_root(owner_key)
    moved: list[dict] = []
    max_items = max(1, min(int(limit or 200), 1000))
    for inbox_name in INBOX_DIR_NAMES:
        inbox = base / inbox_name
        if not inbox.exists():
            continue
        for path in sorted(inbox.rglob("*")):
            if len(moved) >= max_items:
                return moved
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            lowered = rel.lower()
            if "/_trash/" in lowered or lowered.endswith("/.ds_store"):
                continue
            mime = mimetypes.guess_type(str(path))[0] or ""
            context = " ".join(Path(rel).parts[:-1])
            target_rel = default_upload_rel_path(path.name, mime, context=context, source="inbox")
            if target_rel == rel:
                continue
            moved.append(move_file(owner_key, rel, target_rel))
    return moved


def read_file(owner: str | None, rel_path: str) -> dict:
    owner_key = owner_folder_name(owner)
    path = resolve_owner_file(owner_key, rel_path)
    if not path.is_file():
        raise HTTPException(404, "Vault file not found")
    row = index_file(owner_key, path)
    content = _read_indexable_text(path)
    return row_to_dict(row, include_content=True, content_override=content)


def set_index_title(owner: str | None, rel_path: str, title: str) -> dict:
    owner_key = owner_folder_name(owner)
    safe_path = _safe_rel_path(rel_path)
    path = resolve_owner_file(owner_key, safe_path)
    if not path.is_file():
        raise HTTPException(404, "Vault file not found")
    clean_title = (title or "").strip()[:200]
    if not clean_title:
        raise HTTPException(400, "Title is required")

    db = SessionLocal()
    try:
        row = db.query(IrisVaultFile).filter(
            IrisVaultFile.owner == owner_key,
            IrisVaultFile.rel_path == safe_path,
        ).first()
        if not row:
            db.close()
            indexed = index_file(owner_key, path)
            db = SessionLocal()
            row = db.query(IrisVaultFile).filter(IrisVaultFile.id == indexed.id).first()
        if not row:
            raise HTTPException(404, "Vault index row not found")
        row.title = clean_title
        db.commit()
        db.refresh(row)
        return row_to_dict(row)
    finally:
        db.close()


def delete_file(owner: str | None, rel_path: str) -> bool:
    owner_key = owner_folder_name(owner)
    path = resolve_owner_file(owner_key, rel_path)
    deleted = False
    if path.is_file():
        path.unlink()
        deleted = True
    db = SessionLocal()
    try:
        row = db.query(IrisVaultFile).filter(
            IrisVaultFile.owner == owner_key,
            IrisVaultFile.rel_path == _safe_rel_path(rel_path),
        ).first()
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    _delete_vector_chunks(owner_key, _safe_rel_path(rel_path))
    return deleted


def _iter_owner_files(owner: str | None) -> Iterable[Path]:
    base = owner_root(owner)
    for path in base.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            yield path


def reindex_owner(owner: str | None) -> int:
    owner_key = owner_folder_name(owner)
    seen: set[str] = set()
    count = 0
    for path in _iter_owner_files(owner_key):
        row = index_file(owner_key, path)
        seen.add(row.rel_path)
        count += 1
    db = SessionLocal()
    try:
        stale = db.query(IrisVaultFile).filter(IrisVaultFile.owner == owner_key).all()
        for row in stale:
            if row.rel_path not in seen:
                _delete_vector_chunks(owner_key, row.rel_path)
                db.delete(row)
        db.commit()
    finally:
        db.close()
    return count


def search(owner: str | None, query: str = "", limit: int = 20) -> list[dict]:
    owner_key = owner_folder_name(owner)
    limit = max(1, min(int(limit or 20), 100))
    db = SessionLocal()
    try:
        q = db.query(IrisVaultFile).filter(IrisVaultFile.owner == owner_key)
        needle = (query or "").strip()
        if needle:
            like = f"%{needle}%"
            q = q.filter(or_(
                IrisVaultFile.title.ilike(like),
                IrisVaultFile.rel_path.ilike(like),
                IrisVaultFile.content.ilike(like),
            ))
        rows = q.order_by(IrisVaultFile.updated_at.desc()).limit(limit).all()
        results = [row_to_dict(row) for row in rows]
        seen = {item["path"] for item in results}
        if needle and len(results) < limit:
            for hit in _vector_search(owner_key, needle, limit):
                rel_path = hit["path"]
                if rel_path in seen:
                    continue
                row = db.query(IrisVaultFile).filter(
                    IrisVaultFile.owner == owner_key,
                    IrisVaultFile.rel_path == rel_path,
                ).first()
                if not row:
                    continue
                item = row_to_dict(row)
                item["vector_score"] = hit["score"]
                if hit.get("document"):
                    item["excerpt"] = hit["document"][:DEFAULT_EXCERPT_CHARS]
                results.append(item)
                seen.add(rel_path)
                if len(results) >= limit:
                    break
        return results
    finally:
        db.close()


def list_files_fs(owner: str | None, *, limit: int = 5000) -> list[dict]:
    """List EVERY file under the owner's vault folder straight from the
    filesystem (not the search index, which is lazy + capped). This is what the
    Vault file browser uses so the full folder tree shows, regardless of what's
    been indexed. Hidden files/dirs (dotfiles, .obsidian, .ai_memory_cache) are
    skipped. Returns lightweight metadata; content is loaded on open."""
    base = owner_root(owner)
    out: list[dict] = []
    if not base.exists():
        return out
    for path in sorted(base.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(base).parts
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue  # skip dotfiles + hidden dirs (.obsidian, caches, trash)
        try:
            st = path.stat()
        except OSError:
            continue
        out.append({
            "path": "/".join(rel_parts),
            "name": path.name,
            "title": path.stem,
            "mime": mimetypes.guess_type(path.name)[0] or "",
            "size": st.st_size,
            "mtime": datetime.utcfromtimestamp(st.st_mtime).isoformat(),
            "updated_at": datetime.utcfromtimestamp(st.st_mtime).isoformat(),
            "excerpt": "",
        })
        if len(out) >= limit:
            logger.warning("list_files_fs hit the %d-file cap for owner %r", limit, owner_folder_name(owner))
            break
    return out


def row_to_dict(row: IrisVaultFile, *, include_content: bool = False, content_override: str | None = None) -> dict:
    data = {
        "id": row.id,
        "owner": row.owner,
        "path": row.rel_path,
        "title": row.title,
        "mime": row.mime or "",
        "size": row.size or 0,
        "sha256": row.sha256,
        "mtime": row.mtime.isoformat() if row.mtime else None,
        "excerpt": row.excerpt or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_content:
        data["content"] = content_override if content_override is not None else (row.content or "")
    return data
