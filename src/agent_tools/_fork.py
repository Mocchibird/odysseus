# src/agent_tools/_fork.py
"""Fork-only agent-tool tags, merged into TOOL_TAGS by __init__.py so upstream's
literal stays byte-identical. See docs/fork-additive-policy.md.
"""

FORK_TOOL_TAGS = {
    "send_ping", "manage_health", "search_files", "manage_files",
    "manage_gallery", "manage_books",
}
