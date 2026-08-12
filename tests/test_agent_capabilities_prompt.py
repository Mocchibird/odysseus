"""Iris's agent prompt advertises the full Odysseus toolkit (so it actually uses
each subsystem) and no longer mentions the removed Obsidian vault / iris-mcp."""
from src import agent_loop


def test_prompt_advertises_odysseus_subsystems():
    p = agent_loop.AGENT_SYSTEM_PROMPT
    assert "Your home: Odysseus" in p
    # A representative spread of subsystem tools Iris should know it has.
    for tool in (
        "search_files", "manage_files", "manage_memory", "manage_calendar", "manage_notes",
        "manage_tasks", "manage_health", "send_email",
        "web_search", "trigger_research", "send_ping",
    ):
        assert tool in p, f"{tool} missing from the capabilities prompt"


def test_prompt_has_no_obsidian_vault_tool():
    p = agent_loop.AGENT_SYSTEM_PROMPT
    assert "manage_iris_vault" not in p
    assert "obsidian" not in p.lower()


def test_capability_map_survives_narrow_tool_selection():
    # Even when RAG selects only a couple of tools, the high-level capability
    # map stays in the prompt so Iris remains aware of everything it can do.
    p = agent_loop._assemble_prompt({"web_search"})
    assert "Your home: Odysseus" in p
    assert "search_files" in p  # mentioned in the map even though not selected
