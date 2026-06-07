from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_iris_vault_routes_are_registered():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    routes = (ROOT / "routes" / "iris_vault_routes.py").read_text(encoding="utf-8")

    assert "setup_iris_vault_routes" in app
    assert 'prefix="/api/iris-vault"' in routes
    assert '"/upload"' in routes
    assert '"/reindex"' in routes
    assert '"/sort-inbox"' in routes


def test_iris_vault_model_and_owner_folder_policy_exist():
    db = (ROOT / "core" / "database.py").read_text(encoding="utf-8")
    service = (ROOT / "src" / "iris_vault.py").read_text(encoding="utf-8")

    assert "class IrisVaultFile" in db
    assert '__tablename__ = "iris_vault_files"' in db
    assert "ODYSSEUS_OBSIDIAN_VAULT_ROOT" in service
    assert "def owner_folder_name" in service
    assert "IrisVaultFile.owner == owner_key" in service


def test_iris_vault_uses_chromadb_semantic_side_index():
    service = (ROOT / "src" / "iris_vault.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    compact_readme = " ".join(readme.split())
    env = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert 'VECTOR_COLLECTION_NAME = "iris_vault"' in service
    assert "get_chroma_client" in service
    assert "get_embedding_client" in service
    assert "def _index_vector_chunks" in service
    assert "def _vector_search" in service
    assert "ODYSSEUS_IRIS_VAULT_VECTOR_INDEX" in service
    assert "item[\"vector_score\"]" in service
    assert "collection `iris_vault`" in compact_readme
    assert "ODYSSEUS_IRIS_VAULT_VECTOR_INDEX=1" in env
    assert "ODYSSEUS_IRIS_AUTO_SORT_INBOX=1" in env


def test_agent_tool_registration_for_iris_vault():
    agent_tools = (ROOT / "src" / "agent_tools.py").read_text(encoding="utf-8")
    schemas = (ROOT / "src" / "tool_schemas.py").read_text(encoding="utf-8")
    execution = (ROOT / "src" / "tool_execution.py").read_text(encoding="utf-8")
    index = (ROOT / "src" / "tool_index.py").read_text(encoding="utf-8")

    assert '"manage_iris_vault"' in agent_tools
    assert '"name": "manage_iris_vault"' in schemas
    assert '"sort_inbox"' in schemas
    assert "do_manage_iris_vault" in execution
    assert "manage_iris_vault" in index
    assert "sort_inbox" in index
