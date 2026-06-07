"""An older / partial presets.json must be healed forward on load.

The current default persona is Iris. Legacy stock prompt presets are pruned, but
user-edited custom/persona entries and user templates are preserved.
"""
import json
import os
import tempfile

from src.preset_manager import PresetManager


def _write_presets(data: dict) -> str:
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "presets.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return d


def test_empty_legacy_custom_is_upgraded_to_iris_and_stock_presets_removed():
    data_dir = _write_presets({
        "code_analyze": {"name": "Code Analyze", "temperature": 0.2,
                         "max_tokens": 8000, "system_prompt": "analyze"},
        "brainstorm": {"name": "Brainstorm", "temperature": 0.9,
                       "max_tokens": 4096, "system_prompt": "ideate"},
        "custom": {"name": "Custom", "temperature": 1.0, "max_tokens": 0,
                   "system_prompt": "", "enabled": False},
    })
    pm = PresetManager(data_dir)
    assert set(pm.presets) == {"custom"}
    assert pm.presets["custom"]["character_name"] == "Iris"
    assert pm.presets["custom"]["enabled"] is True
    with open(os.path.join(data_dir, "presets.json"), encoding="utf-8") as f:
        on_disk = json.load(f)
    assert set(on_disk) == {"custom"}
    assert on_disk["custom"]["name"] == "Iris"


def test_fill_does_not_clobber_user_edits():
    # An edited `custom` (enabled, bespoke prompt) survives the Iris migration.
    edited_custom = {
        "name": "My Persona",
        "character_name": "My Persona",
        "temperature": 0.55,
        "max_tokens": 1234,
        "system_prompt": "You are my bespoke assistant.",
        "inject_prefix": "PRE",
        "inject_suffix": "SUF",
        "enabled": True,
    }
    data_dir = _write_presets({
        "code_analyze": {"name": "Code Analyze", "temperature": 0.2,
                         "max_tokens": 8000, "system_prompt": "analyze"},
        "custom": edited_custom,
        "user_templates": [{"id": "t1", "name": "Tmpl"}],
    })
    pm = PresetManager(data_dir)
    assert "code_analyze" not in pm.presets
    assert pm.presets["custom"] == edited_custom
    assert pm.presets["user_templates"] == [{"id": "t1", "name": "Tmpl"}]


def test_complete_file_is_not_rewritten_needlessly():
    # A current Iris file is returned unchanged.
    full = {k: dict(v) for k, v in PresetManager.DEFAULT_PRESETS.items()}
    data_dir = _write_presets(full)
    pm = PresetManager(data_dir)
    assert pm.presets["custom"]["enabled"] is True
    assert pm.presets == full
