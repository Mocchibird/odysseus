import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

IRIS_SYSTEM_PROMPT = """You are Iris, the user's personal assistant and the companion to their Odysseus workspace.

Ground what you say and do in their real systems — their files, books, notes and documents, and your persistent memory — and treat that data with care. Be warm, direct, and practical. Help the user think clearly, keep their notes and files human-browsable, and prefer durable, well-organized updates over scattered fragments. When you change a note, file, or memory, be precise about what changed; when answering from their files or your memory, mention the file or source you used."""

# Korean variant of the default persona ("Iris-Korean" in the client's
# PROMPT_TEMPLATES) — kept here as the server-side canonical copy. The
# per-user `language` pref (src/i18n.py) makes this the default persona for
# new chats when the user has chosen Korean.
IRIS_SYSTEM_PROMPT_KO = "당신은 Iris, 사용자의 개인 비서이자 Odysseus 워크스페이스의 동반자입니다. 사용자의 실제 시스템 — 업로드된 파일의 지식 베이스, 노트와 문서, 그리고 당신의 영구 메모리 — 에 근거해서 말하고 행동하며, 그 데이터를 소중히 다루세요. 따뜻하고, 솔직하고, 실용적으로 응대하세요. 사용자가 명확하게 생각하도록 돕고, 노트와 지식은 사람이 읽기 좋게 유지하며, 흩어진 조각보다 오래가고 잘 정리된 업데이트를 우선하세요. 노트, 파일, 메모리를 변경할 때는 무엇이 바뀌었는지 정확히 알리고, 지식 베이스나 메모리를 근거로 답할 때는 사용한 파일이나 출처를 언급하세요. 기본적으로 한국어로 대답하고, 사용자가 다른 언어로 쓰면 그 언어를 따르세요."

# German variant ("Iris-German" in the client's PROMPT_TEMPLATES).
IRIS_SYSTEM_PROMPT_DE = "Du bist Iris, die persönliche Assistentin des Nutzers und Begleiterin seines Odysseus-Workspace. Stütze dich bei allem, was du sagst und tust, auf seine realen Systeme — die Wissensbasis seiner hochgeladenen Dateien, seine Notizen und Dokumente und dein persistentes Gedächtnis — und behandle diese Daten mit Sorgfalt. Sei warm, direkt und pragmatisch. Hilf dem Nutzer, klar zu denken, halte Notizen und Wissen gut lesbar und bevorzuge dauerhafte, gut organisierte Aktualisierungen statt verstreuter Fragmente. Wenn du eine Notiz, Datei oder Erinnerung änderst, benenne präzise, was sich geändert hat; wenn du aus der Wissensbasis oder deinem Gedächtnis antwortest, nenne die Datei oder Quelle. Antworte standardmäßig auf Deutsch; schreibt der Nutzer in einer anderen Sprache, folge dieser Sprache."

# All shipped Iris defaults by persona name. The resolver below swaps between
# them PER USER at chat time: the global custom slot is admin-gated
# (POST /api/presets/custom -> require_admin), so a non-admin could never
# write their language's Iris prompt into it.
IRIS_VARIANTS = {
    "Iris": IRIS_SYSTEM_PROMPT,
    "Iris-Korean": IRIS_SYSTEM_PROMPT_KO,
    "Iris-German": IRIS_SYSTEM_PROMPT_DE,
}
_LANG_TO_VARIANT = {"en": "Iris", "ko": "Iris-Korean", "de": "Iris-German"}


def resolve_iris_prompt_for_user(stored_prompt, owner) -> "str | None":
    """Per-user variant of the DEFAULT Iris prompt.

    Only acts when the stored custom-slot prompt is an UNTOUCHED known Iris
    default (any language) — an admin-customized persona is never overridden.
    The variant is the user's explicit per-user `default_persona` pref when it
    names a shipped Iris variant, else the variant matching their language
    pref. Returns the replacement prompt, or None to leave the slot's prompt.
    """
    current = (stored_prompt or "").strip()
    known = set(IRIS_VARIANTS.values()) | {_LEGACY_IRIS_VAULT_PROMPT}
    if current not in known:
        return None
    # Only an EXPLICIT per-user persona choice counts here — get_user_setting
    # would fall back to the admin-global default ("Iris"), which must not
    # mask the language mapping.
    try:
        from routes.prefs_routes import _load_for_user
        persona = str((_load_for_user(str(owner or "")) or {}).get("default_persona") or "").strip()
    except Exception:
        persona = ""
    if persona in IRIS_VARIANTS:
        want = IRIS_VARIANTS[persona]
    else:
        try:
            from src.i18n import get_user_language
            want = IRIS_VARIANTS[_LANG_TO_VARIANT.get(get_user_language(owner), "Iris")]
        except Exception:
            return None
    return want if want != current else None


# The default that shipped while the Obsidian vault integration still existed.
# load() swaps it for IRIS_SYSTEM_PROMPT (exact match only) so persisted
# presets.json files stop instructing the model about removed systems, while
# user-edited prompts are never touched.
_LEGACY_IRIS_VAULT_PROMPT = """You are Iris, Hyun-Min's personal assistant and Obsidian-vault companion.

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
            custom = presets.get("custom") if isinstance(presets, dict) else None
            if isinstance(custom, dict):
                _p = (custom.get("system_prompt") or "").strip()
                # Heal ANY vault-era vintage, not just the byte-exact shipped
                # prompt — deployed copies drifted (whitespace, older/newer
                # wordings), and a prompt instructing the model to operate an
                # Obsidian vault is broken regardless: the integration was
                # removed. Marker = both terms present; a deliberately custom
                # prompt about the (nonexistent) vault has no use either.
                _low = _p.lower()
                if _p == _LEGACY_IRIS_VAULT_PROMPT or ("obsidian" in _low and "vault" in _low):
                    custom["system_prompt"] = IRIS_SYSTEM_PROMPT
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
