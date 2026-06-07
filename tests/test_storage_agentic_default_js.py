import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_toggle_state_defaults_to_agent_and_migrates_legacy_chat_once():
    values = _node_eval(
        """
        const store = new Map();
        globalThis.localStorage = {
          getItem(key) { return store.has(key) ? store.get(key) : null; },
          setItem(key, value) { store.set(key, value); },
          removeItem(key) { store.delete(key); }
        };
        const Storage = await import('./static/js/storage.js');

        const emptyDefault = Storage.loadToggleState().mode;
        store.set(Storage.KEYS.TOGGLES, JSON.stringify({ mode: 'chat', web_chat: true }));
        const migrated = Storage.applyAgenticDefaultMode();
        const storedAfterMigration = JSON.parse(store.get(Storage.KEYS.TOGGLES));

        storedAfterMigration.mode = 'chat';
        store.set(Storage.KEYS.TOGGLES, JSON.stringify(storedAfterMigration));
        const explicitChat = Storage.applyAgenticDefaultMode();

        console.log(JSON.stringify({
          emptyDefault,
          migratedMode: migrated.mode,
          preservedWebChat: storedAfterMigration.web_chat,
          explicitChatMode: explicitChat.mode
        }));
        """
    )

    assert values == {
        "emptyDefault": "agent",
        "migratedMode": "agent",
        "preservedWebChat": True,
        "explicitChatMode": "chat",
    }
