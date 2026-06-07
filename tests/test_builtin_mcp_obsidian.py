import importlib
import os
import sys
import types
from pathlib import Path


def _load_builtin_mcp(monkeypatch):
    core = types.ModuleType("core")
    platform_compat = types.ModuleType("core.platform_compat")
    platform_compat.IS_WINDOWS = False
    platform_compat.which_tool = lambda _name: None
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.platform_compat", platform_compat)
    sys.modules.pop("src.builtin_mcp", None)
    return importlib.import_module("src.builtin_mcp")


def test_obsidian_mcp_config_absent_without_script(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    monkeypatch.delenv("ODYSSEUS_OBSIDIAN_MCP_SCRIPT", raising=False)

    assert builtin_mcp._obsidian_mcp_config_from_env() is None


def test_obsidian_mcp_config_uses_script_vault_and_pythonpath(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    script = tmp_path / "obsidian_memory_mcp.py"
    script.write_text("print('server')\n", encoding="utf-8")
    vault = tmp_path / "Vault"
    vault.mkdir()

    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_MCP_SCRIPT", str(script))
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_VAULT_ROOT", str(vault))
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_MCP_COMMAND", "/opt/iris/bin/python")
    monkeypatch.setenv("PYTHONPATH", "/existing")

    cfg = builtin_mcp._obsidian_mcp_config_from_env()

    assert cfg["server_id"] == "iris_obsidian"
    assert cfg["name"] == "Iris: Obsidian Vault"
    assert cfg["command"] == "/opt/iris/bin/python"
    assert cfg["args"] == [str(script.resolve())]
    assert cfg["env"]["IRIS_VAULT_ROOT"] == str(vault)
    assert cfg["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(Path(script).parent.resolve()),
        "/existing",
    ]
