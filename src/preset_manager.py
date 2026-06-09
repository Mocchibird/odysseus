import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

IRIS_SYSTEM_PROMPT = """You are Iris, Hyun-Min's personal assistant and Obsidian-vault companion.

Use the persistent Obsidian session context as your canonical operating rules for vault structure, memory, and safety. Be warm, direct, and practical. Help Hyun-Min think clearly, keep his notes human-browsable, and prefer durable, well-linked updates over scattered fragments. When acting on the vault, be precise about what changed; when answering from memory, mention the note or source you used."""


class PresetManager:
    DEFAULT_PRESETS = {
        "custom": {
            "name": "Iris",
            "character_name": "Iris",
            "temperature": 0.9,
            "max_tokens": 0,
            "system_prompt": IRIS_SYSTEM_PROMPT,
            "inject_prefix": "",
            "inject_suffix": "",
            "enabled": True,
        }
    }
    LEGACY_BUILTIN_KEYS = {"code_analyze", "brainstorm", "reason"}
    
    def __init__(self, data_dir: str):
        self.presets_file = os.path.join(data_dir, "presets.json")
        self.presets = self.load()
    
    def load(self) -> Dict[str, Any]:
        """Load presets from file, creating defaults if needed"""
        if not os.path.exists(self.presets_file):
            self.save(self.DEFAULT_PRESETS)
            return self.DEFAULT_PRESETS.copy()
        
        try:
            with open(self.presets_file, 'r', encoding="utf-8") as f:
                presets = json.load(f)
            if not isinstance(presets, dict):
                logger.error("Error loading presets: expected an object")
                return self.DEFAULT_PRESETS.copy()
            changed = False
            custom = presets.get("custom") if isinstance(presets, dict) else None
            if isinstance(custom, dict) and "enabled" not in custom:
                legacy_prompt = "You are a helpful, balanced assistant. Match your response style to the user's needs."
                if (
                    custom.get("name") == "Custom"
                    and not custom.get("character_name")
                    and custom.get("system_prompt") == legacy_prompt
                ):
                    custom["enabled"] = False
                    custom["system_prompt"] = ""
                    custom["temperature"] = 1.0
                    custom["max_tokens"] = 0
                    custom.setdefault("inject_prefix", "")
                    custom.setdefault("inject_suffix", "")
                    changed = True
            custom = presets.get("custom") if isinstance(presets, dict) else None
            if self._should_upgrade_custom_to_iris(custom):
                presets["custom"] = dict(self.DEFAULT_PRESETS["custom"])
                changed = True
            for key in self.LEGACY_BUILTIN_KEYS:
                if key in presets:
                    presets.pop(key, None)
                    changed = True
            # Heal a forward-incompatible file the same way the legacy `custom`
            # migration above does: make sure the Iris default exists without
            # clobbering user-edited custom prompts or user templates.
            if isinstance(presets, dict) and any(
                k not in presets for k in self.DEFAULT_PRESETS
            ):
                presets = {**self.DEFAULT_PRESETS, **presets}
                changed = True
            if changed:
                self.save(presets)
            return presets
        except Exception as e:
            logger.error(f"Error loading presets: {e}")
            return self.DEFAULT_PRESETS.copy()

    def _should_upgrade_custom_to_iris(self, custom: Any) -> bool:
        """Return True when custom is still the old empty/default persona."""
        if not isinstance(custom, dict):
            return True
        prompt = (custom.get("system_prompt") or "").strip()
        name = (custom.get("name") or "").strip()
        character_name = (custom.get("character_name") or "").strip()
        if custom.get("enabled") is False and not prompt and not character_name:
            return True
        if name == "Custom" and not prompt and not character_name:
            return True
        return False
    
    def save(self, presets: Dict[str, Any]) -> bool:
        """Save presets to file"""
        try:
            # Atomic write (tmp file + os.replace) so a crash or serialization
            # error mid-write can't truncate presets.json and lose every saved
            # preset. Lazy import keeps this module free of the heavy core
            # package import graph at load time.
            from core.atomic_io import atomic_write_json
            atomic_write_json(self.presets_file, presets, indent=2)
            self.presets = presets
            return True
        except Exception as e:
            logger.error(f"Error saving presets: {e}")
            return False
    
    def get(self, preset_id: str) -> Dict[str, Any]:
        """Get a specific preset"""
        return self.presets.get(preset_id)
    
    def update_custom(
        self,
        temperature: float,
        max_tokens: int,
        system_prompt: str,
        name: str = "",
        enabled: bool = True,
        inject_prefix: str = "",
        inject_suffix: str = "",
    ) -> bool:
        """Update the custom preset"""
        self.presets["custom"] = {
            "name": name or "Custom",
            "character_name": name,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system_prompt": system_prompt,
            "inject_prefix": inject_prefix,
            "inject_suffix": inject_suffix,
            "enabled": enabled,
        }
        return self.save(self.presets)
    
    def get_all(self) -> Dict[str, Any]:
        """Get all presets"""
        return self.presets.copy()

    def get_user_templates(self) -> list:
        """Get user-saved character templates."""
        return self.presets.get("user_templates", [])

    def save_user_template(self, template: dict) -> bool:
        """Save a new user template or update existing by id."""
        templates = self.presets.get("user_templates", [])
        # Update existing if same id
        existing = next((i for i, t in enumerate(templates) if t.get("id") == template.get("id")), None)
        if existing is not None:
            templates[existing] = template
        else:
            templates.append(template)
        self.presets["user_templates"] = templates
        return self.save(self.presets)

    def delete_user_template(self, template_id: str) -> bool:
        """Delete a user template by id."""
        templates = self.presets.get("user_templates", [])
        self.presets["user_templates"] = [t for t in templates if t.get("id") != template_id]
        return self.save(self.presets)

    def get_group_presets(self) -> list:
        """Get saved group chat presets."""
        return self.presets.get("group_presets", [])

    def save_group_presets(self, groups: list) -> bool:
        """Save group chat presets."""
        self.presets["group_presets"] = groups
        return self.save(self.presets)
