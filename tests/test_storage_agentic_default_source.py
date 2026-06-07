from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_storage_defaults_toggle_mode_to_agent():
    source = (ROOT / "static" / "js" / "storage.js").read_text(encoding="utf-8")

    assert "export const DEFAULT_TOGGLE_STATE" in source
    assert "mode: 'agent'" in source
    assert "iris-agent-default-v1" in source


def test_init_applies_agentic_default_mode():
    source = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

    assert "Storage.applyAgenticDefaultMode();" in source
