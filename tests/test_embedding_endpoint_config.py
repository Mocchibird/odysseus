import json

import routes.embedding_routes as embedding_routes
from src.embeddings import EmbeddingClient


def test_empty_embedding_url_env_falls_back_to_default_with_scheme(monkeypatch):
    # docker compose sets EMBEDDING_URL=${EMBEDDING_URL:-} → present but empty.
    # The client must NOT use "" as the URL (httpx rejects a scheme-less/empty
    # URL with "missing an 'http://' ... protocol"); it must fall through to the
    # scheme-qualified default so the lane fails cleanly (connection refused →
    # FastEmbed) instead of on a malformed URL.
    monkeypatch.setenv("EMBEDDING_URL", "")
    client = EmbeddingClient()
    assert client.url.startswith("http://") or client.url.startswith("https://")
    assert client.url != ""


def test_explicit_embedding_url_env_is_respected(monkeypatch):
    monkeypatch.setenv("EMBEDDING_URL", "http://ollama:11434/v1/embeddings")
    assert EmbeddingClient().url == "http://ollama:11434/v1/embeddings"


def test_load_custom_endpoint_ignores_non_object_json(tmp_path, monkeypatch):
    endpoint_file = tmp_path / "embedding_endpoint.json"
    endpoint_file.write_text(json.dumps(["not", "an", "endpoint", "object"]), encoding="utf-8")
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(endpoint_file))

    assert embedding_routes._load_custom_endpoint() == {}


def test_load_custom_endpoint_keeps_object_json(tmp_path, monkeypatch):
    endpoint_file = tmp_path / "embedding_endpoint.json"
    endpoint_file.write_text(
        json.dumps({"url": "http://127.0.0.1:11434", "model": "nomic-embed-text"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(endpoint_file))

    assert embedding_routes._load_custom_endpoint() == {
        "url": "http://127.0.0.1:11434",
        "model": "nomic-embed-text",
    }
