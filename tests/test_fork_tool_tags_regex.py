"""Fork tool tags must reach tool_parsing's fenced-block regex in any import order.

``src/tool_parsing.py`` compiles ``_TOOL_BLOCK_RE`` from ``TOOL_TAGS`` at import
time. Upstream moved ``TOOL_TAGS`` into the leaf module ``src/tool_types.py``;
the fork tags used to be merged in by ``src/agent_tools/__init__.py``, which is
NOT imported first on every path, so fenced calls to fork tools (```manage_health
...```) silently stopped parsing. They are now merged where the set is defined
(``src/tool_types_fork.py``). Each order runs in a fresh interpreter because the
regex is built once per process.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

IMPORT_ORDERS = [
    "import src.tool_parsing as tp",
    "import src.tool_execution; import src.tool_parsing as tp",
    "import src.agent_tools; import src.tool_parsing as tp",
]


@pytest.mark.parametrize("prelude", IMPORT_ORDERS)
def test_fork_tool_tags_are_in_the_fenced_block_regex(prelude):
    code = (
        f"{prelude}\n"
        "from src.tool_types_fork import FORK_TOOL_TAGS\n"
        "missing = sorted(t for t in FORK_TOOL_TAGS if t not in tp._TOOL_BLOCK_RE.pattern)\n"
        "print(','.join(missing))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"fork tags missing from regex: {result.stdout.strip()}"
