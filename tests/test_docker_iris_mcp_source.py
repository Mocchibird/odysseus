from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_supports_iris_mcp_deps_and_mounts():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements-iris-mcp.txt").read_text(encoding="utf-8")

    assert "INSTALL_IRIS_MCP_DEPS" in dockerfile
    assert "requirements-iris-mcp.txt" in dockerfile
    assert "sqlite3" in dockerfile
    assert "tzdata" in dockerfile
    assert '"/vault"' in dockerfile
    assert '"/claude-auth"' not in dockerfile
    assert "matplotlib" in requirements
    assert "recurring-ical-events" in requirements


def test_compose_wires_iris_vault_and_mcp_defaults():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "ODYSSEUS_OBSIDIAN_VAULT_DIR" in compose
    assert "${ODYSSEUS_OBSIDIAN_VAULT_DIR:?Set ODYSSEUS_OBSIDIAN_VAULT_DIR" in compose
    assert "${ODYSSEUS_IRIS_MCP_REPO_DIR:-../obsidian-iris-mcp}:/opt/obsidian-iris-mcp:ro" in compose
    assert "ODYSSEUS_OBSIDIAN_MCP_SCRIPT=${ODYSSEUS_OBSIDIAN_MCP_SCRIPT:-/opt/obsidian-iris-mcp/obsidian_memory_mcp.py}" in compose
    assert "ODYSSEUS_OBSIDIAN_VAULT_ROOT=${ODYSSEUS_OBSIDIAN_VAULT_ROOT:-/vault}" in compose
    assert "IRIS_VAULT_ROOT=${IRIS_VAULT_ROOT:-/vault}" in compose
    assert "INSTALL_IRIS_MCP_DEPS: ${INSTALL_IRIS_MCP_DEPS:-true}" in compose


def test_entrypoint_keeps_vault_host_owned():
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "Do not" in entrypoint and "chown /vault" in entrypoint
    assert "/claude-auth" not in entrypoint
