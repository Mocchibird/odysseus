"""The writer's markdown import normalisers, exercised for real in Node.

Markdown is the canonical stored form for writer documents, so anything the
importer changes on open is PERSISTED by the next autosave. Each normaliser here
exists because a real document was being silently rewritten:

  * 2-space nested lists were flattened to one level, destroying the outline.
  * `- [X]` (capital) imported as UNCHECKED — a lost tick.
  * The first line of a multi-line fenced block lost one leading space per
    open-and-save cycle, creeping real code leftwards.

These are pure string functions, so they run without a DOM. The behaviours that
need the live editor (the CHECK_LIST bullet requirement and paragraph escaping)
are asserted structurally in test_writer_isolation.py.
"""
import json
import shutil
import subprocess

import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(cases: dict) -> dict:
    """Import blocks.js in Node and apply the normalisers to each case."""
    script = """
    import blocks from './static/js/writer/blocks.js';
    const cases = %s;
    const out = {};
    for (const [k, v] of Object.entries(cases)) {
      out[k] = blocks.padFencedFirstLine(
        blocks.normalizeCheckMarkers(blocks.normalizeListIndent(v)),
      );
    }
    process.stdout.write(JSON.stringify(out));
    """ % json.dumps(cases)
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout)


def test_two_space_nesting_becomes_lexical_four_space_levels():
    """Lexical nests one level per FOUR spaces; 1-3 spaces read as level 0, so a
    conventionally-indented file was imported flat and then saved flat."""
    got = _run({
        "two": "- top\n  - child\n    - grand",
        "three": "- top\n   - child",
        "one": "- top\n - child",
        "tab": "- top\n\t- child",
    })
    assert got["two"] == "- top\n    - child\n        - grand"
    assert got["three"] == "- top\n    - child"
    assert got["one"] == "- top\n    - child"
    assert got["tab"] == "- top\n    - child"


def test_already_four_space_nesting_is_untouched():
    got = _run({"x": "- top\n    - child\n        - grand"})
    assert got["x"] == "- top\n    - child\n        - grand"


def test_normalisation_is_idempotent():
    """Applying it twice must not drift — otherwise every open shifts the file."""
    once = _run({"x": "- top\n  - child\n    - grand"})["x"]
    twice = _run({"x": once})["x"]
    assert once == twice


def test_indentation_inside_fenced_code_is_content_not_structure():
    """Re-indenting inside a fence would rewrite the user's code."""
    got = _run({
        "fence": "```\n- top\n  - child\n```",
        "tilde": "~~~\n- top\n  - child\n~~~",
        "after": "```\n  x\n```\n\n- top\n  - child",
    })
    assert got["fence"] == "```\n- top\n  - child\n```"
    assert got["tilde"] == "~~~\n- top\n  - child\n~~~"
    # The list AFTER the fence still normalises.
    assert got["after"].endswith("- top\n    - child")


def test_capital_x_checklist_marker_keeps_its_tick():
    got = _run({
        "upper": "- [X] done",
        "lower": "- [x] done",
        "empty": "- [ ] todo",
        "nested": "- [x] a\n  - [X] b",
    })
    assert got["upper"] == "- [x] done"
    assert got["lower"] == "- [x] done"
    assert got["empty"] == "- [ ] todo"
    assert got["nested"].endswith("- [x] b")


def test_fenced_first_line_keeps_its_indentation():
    """A multi-line block lost exactly one leading space from its first line,
    cumulatively — so code crept left on every open."""
    got = _run({
        "multi": "```js\n  a();\nb();\n```",
        "single": "```js\n  only();\n```",
        "flush": "```js\na();\nb();\n```",
        "deep": "```py\n    def f():\n        pass\n```",
    })
    # Padded by one space to survive the importer's off-by-one.
    assert got["multi"] == "```js\n   a();\nb();\n```"
    # A single content line is not affected by the importer, so it is not padded.
    assert got["single"] == "```js\n  only();\n```"
    assert got["flush"] == "```js\na();\nb();\n```"
    assert got["deep"] == "```py\n     def f():\n        pass\n```"


def test_plain_prose_is_never_rewritten():
    got = _run({
        "prose": "Just a sentence.\n\nAnd another one.",
        "heading": "# Title\n\nBody text.",
        "quote": "> quoted line",
    })
    assert got["prose"] == "Just a sentence.\n\nAnd another one."
    assert got["heading"] == "# Title\n\nBody text."
    assert got["quote"] == "> quoted line"
