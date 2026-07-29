"""Guard against unbound `*Module` references in the SPA's ES modules.

This bug class ships silently broken UI and no other test catches it:

  * sessions.js called `markdownModule.renderContent(...)` without importing it,
    so EVERY past chat opened BLANK ("ReferenceError: markdownModule is not
    defined" in _renderHistorySlice) while the session stayed selected — so
    typing still had context, which made it especially confusing.
  * slashCommands.js used `markdownModule` in typewriterReply's renderMarkdown
    branch with no import.
  * emailLibrary.js used `uiModule` (download-all toast, summarize/translate
    error paths) while importing only NAMED exports from ui.js.

Rule enforced here: if a module uses an identifier as `X.` / `X?.` but the file
declares no BINDING for it — no import, no local declaration, no function
parameter, no catch binding, no `window.X` assignment — the reference is a
guaranteed ReferenceError when that path runs.

Binding detection is deliberately explicit rather than "the name appears
somewhere else": `if (uiModule) uiModule.showError()` mentions the name in a
non-usage position yet still throws, and that is exactly the emailLibrary.js bug.
Real parameter lists (the dependency-injection style in static/js/editor/*.js,
e.g. `export function wireImport({ container, uiModule })`) do count as bindings.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_JS = ROOT / "static" / "js"

# Strip comments and string/template literals so matches inside them don't count.
_COMMENT_BLOCK = re.compile(r"/\*[\s\S]*?\*/")
_COMMENT_LINE = re.compile(r"(^|[^:])//[^\n]*")
_TEMPLATE = re.compile(r"`(?:\\[\s\S]|[^`\\])*`")
_SQ = re.compile(r"'(?:\\.|[^'\\])*'")
_DQ = re.compile(r'"(?:\\.|[^"\\])*"')

# `foo.` or `foo?.` where foo is not itself a property access (not preceded by . or \w)
_USAGE = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\??\.")


def _strip(src: str) -> str:
    src = _COMMENT_BLOCK.sub(" ", src)
    src = _COMMENT_LINE.sub(r"\1 ", src)
    src = _TEMPLATE.sub("``", src)
    src = _SQ.sub("''", src)
    return _DQ.sub('""', src)


def _is_bound(name: str, raw: str) -> bool:
    """True if `name` has any real binding in this module."""
    n = re.escape(name)
    patterns = (
        rf"import\s+{n}\b",                        # import X from / import X, {{...}}
        rf"import\s*\*\s*as\s+{n}\b",              # import * as X
        rf"import\s*\{{[^}}]*\b{n}\b[^}}]*\}}",    # import { X } / { a as X }
        rf"\b(?:const|let|var)\s+{n}\b",           # declaration
        rf"\b(?:const|let|var)\s*[\{{\[][^;]*\b{n}\b[^;]*[\}}\]]\s*=",  # destructured decl
        rf"\b(?:function|class)\s+{n}\b",          # named function/class
        rf"catch\s*\(\s*{n}\s*\)",                 # catch binding
        rf"window\.{n}\s*=",                       # assigned global
    )
    if any(re.search(p, raw) for p in patterns):
        return True
    # Function PARAMETER lists only — a paren group that is a signature, i.e.
    # preceded by `function [name]` or followed by `=>` / a function body `{`.
    # This accepts `function wireImport({ container, uiModule })` but rejects
    # `if (uiModule)`, which is a condition, not a binding.
    for m in re.finditer(r"\(([^()]*)\)", raw):
        params = m.group(1)
        if not re.search(rf"(?<![.\w$]){n}\b", params):
            continue
        before = raw[max(0, m.start() - 40):m.start()]
        after = raw[m.end():m.end() + 4]
        if re.search(r"\bfunction\s*[\w$]*\s*$", before) or re.match(r"\s*(=>|\{)", after):
            return True
    return False


def _unbound_module_refs(src: str):
    """`*Module` identifiers used as `X.`/`X?.` with no binding in the file."""
    code = _strip(src)
    used = {m.group(1) for m in _USAGE.finditer(code) if m.group(1).endswith("Module")}
    return {name for name in used if not _is_bound(name, src)}


def test_no_unbound_module_references_in_spa_modules():
    failures = []
    for path in sorted(STATIC_JS.rglob("*.js")):
        if "/lib/" in path.as_posix() or path.name.endswith(".min.js"):
            continue
        offenders = _unbound_module_refs(path.read_text(encoding="utf-8"))
        if offenders:
            rel = path.relative_to(ROOT)
            failures.append(f"{rel}: {', '.join(sorted(offenders))}")

    assert not failures, (
        "These modules reference *Module identifiers they never bind (no import, "
        "no parameter, no declaration) — the referenced code path throws "
        "ReferenceError at runtime:\n  " + "\n  ".join(failures)
    )
