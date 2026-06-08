# Vendored: obsidian-iris-mcp

This is a vendored copy of the Iris MCP server so Odysseus is self-contained and
does **not** need a sibling `../obsidian-iris-mcp` checkout mounted at runtime.

- **Source:** https://github.com/Mocchibird/obsidian-iris-mcp
- **Branch:** pr-ae-fix-dialog-modal-shim
- **Commit:** 6f9955266a68fc42cd2e31a9107e62043eb2db00
- **Vendored:** 2026-06-08

## What runs
Odysseus launches `obsidian_memory_mcp.py` as a stdio MCP subprocess
(see `src/builtin_mcp.py`) with `IRIS_TOOL_GROUPS=vault,anime,warranties`.
Only those tool groups load; every other module under `_iris/tools/` (discord,
web, voice, calendar, health, habits, training, charts, vocab, users) is present
but **never imported** — the autoloader (`_iris/tools/__init__.py`) dir-scans and
skips anything outside the allow-list. The loaded tools use only the stdlib;
their heavier siblings' deps are intentionally not all installed
(see `requirements-iris-mcp.txt`).

## Re-syncing
```
rsync -a --delete --exclude=__pycache__ --exclude='*.pyc' \
  ../obsidian-iris-mcp/_iris/ vendor/iris-mcp/_iris/
cp ../obsidian-iris-mcp/obsidian_memory_mcp.py ../obsidian-iris-mcp/iris_config.py vendor/iris-mcp/
```
Then update the commit hash above.
