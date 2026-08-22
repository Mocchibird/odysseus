"""Verify src.tool_utils has no project imports beyond a tiny allowlist.

If someone adds an import from src.settings, src.database, or any other
project module inside tool_utils.py, the circular import that this module
exists to break will silently return a partially-initialized module.
This test catches that statically.

src.tool_output_spill is allowed because it is import-equivalent to src.constants:
it pulls in stdlib plus src.constants and nothing else, so it cannot participate
in the cycle. That is not taken on trust — the second test below enforces it, so
the invariant stays intact instead of being traded away for the allowlist entry.
"""

import ast
import pathlib

import pytest


def _project_imports(rel: str) -> list[str]:
    """Every `from src.* import ...` module in a file, at any nesting depth."""
    tree = ast.parse(pathlib.Path(rel).read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name.startswith("src."))
    return found


@pytest.mark.parametrize(
    "rel,allowed",
    [
        ("src/tool_utils.py", {"src.constants", "src.tool_output_spill"}),
        # The allowlist entry above is only safe while this holds.
        ("src/tool_output_spill.py", {"src.constants"}),
    ],
)
def test_no_unexpected_project_imports(rel, allowed):
    illegal = sorted(set(_project_imports(rel)) - allowed)
    assert not illegal, f"Illegal project import(s) in {rel}: {illegal}"
