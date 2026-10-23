# src/tool_types_fork.py
"""Fork-only agent-tool tags.

Kept in their own import-free module and merged into ``TOOL_TAGS`` by
``src/tool_types.py`` right where that set is defined, so upstream's literal
stays byte-identical and every consumer sees the fork tags regardless of
import order (``src/tool_parsing.py`` compiles its fenced-block regex from
``TOOL_TAGS`` at import time). See docs/fork-additive-policy.md.
"""

FORK_TOOL_TAGS = {
    "send_ping", "manage_health", "search_files", "manage_files",
    "manage_gallery",
}
