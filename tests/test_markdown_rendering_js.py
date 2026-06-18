"""Regression coverage for the browser markdown renderer."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_markdown_case(markdown: str, render_expr: str = "mod.mdToHtml(input)"):
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';

        globalThis.window = { location: { origin: 'http://localhost' }, katex: null };
        globalThis.document = {
          readyState: 'loading',
          addEventListener() {},
          createElement(tag) {
            if (tag !== 'template') throw new Error(`unsupported element: ${tag}`);
            return {
              _html: '',
              content: { querySelectorAll() { return []; } },
              set innerHTML(value) { this._html = value; },
              get innerHTML() { return this._html; },
            };
          },
        };
        globalThis.MutationObserver = class { observe() {} };

        let source = fs.readFileSync('./static/js/markdown.js', 'utf8');
        source = source.replace(
          /import uiModule from ['"]\.\/ui\.js['"];/,
          ''
        );
        source = source.replace(
          /import \{ splitTableRow \} from ['"]\.\/markdown\/tableRow\.js['"];/,
          `function splitTableRow(row) {
            return (row || '').replace(/^\\s*\\|/, '').replace(/\\|\\s*$/, '').split('|').map(c => c.trim());
          }`
        );
        // markdown.js imports the emoji-shortcode helpers relatively (issue #345),
        // which a data: URL module can't resolve. Inline the REAL helpers (minus
        // their export keywords) so the renderer's shortcode pass behaves exactly
        // as it does in the browser.
        const emojiSource = fs.readFileSync('./static/js/emojiShortcodes.js', 'utf8')
          .replace(/^export default .*$/m, '')
          .replace(/export const /g, 'const ')
          .replace(/export function /g, 'function ');
        source = source.replace(
          /import \{ replaceEmojiShortcodes, hasEmojiShortcode \} from ['"]\.\/emojiShortcodes\.js['"];/,
          () => emojiSource
        );
        source = source.replace(
          /var escapeHtml = uiModule\.esc;/,
          `var escapeHtml = (value) => String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');`
        );

        const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
        const mod = await import(moduleUrl);
        const input = JSON.parse(process.argv[1]);
        console.log(JSON.stringify({ html: __RENDER_EXPR__ }));
        """
    ).replace("__RENDER_EXPR__", render_expr)
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(markdown)],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.splitlines()[-1])["html"]


def test_ordered_lists_render_as_one_unwrapped_ol(node_available):
    html = _run_markdown_case(
        "Before\n\n"
        "1. **Check against the home page** — that's the visual reference for how things should feel.\n"
        "2. **Open DevTools** and inspect the element — check fonts, colors, and spacing against this guide.\n"
        "3. **Flag it** — note the page, the section, what's wrong, and what CSS rule you suspect.\n"
        "4. **Small fixes** — if you know the fix (e.g. wrong CSS variable, wrong font), go ahead and change it in the CSS Module file.\n"
        "5. **Big changes** — Talk it through before making wide changes across many pages.\n\n"
        "After"
    )

    assert html.count("<ol>") == 1
    assert html.count("</ol>") == 1
    assert html.count("<li>") == 5
    assert "<ul>" not in html
    assert "<oli>" not in html
    assert "<uli>" not in html
    assert "<p><ol>" not in html
    assert "<p><li>" not in html
    assert "<p>Before</p>" in html
    assert "<p>After</p>" in html


def test_table_separator_row_not_rendered_as_data(node_available):
    html = _run_markdown_case("| A | B |\n|---|---|\n| 1 | 2 |")

    assert html.count("<tr>") == 2
    assert "<th" in html
    assert "<td" in html
    assert "---" not in html


def test_process_with_thinking_handles_gemma4_thought_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\ninternal reasoning<channel|>Final answer.",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" in html
    assert "internal reasoning" in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_process_with_thinking_strips_empty_gemma4_thought_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\n<channel|>Final answer.",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" not in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_process_with_thinking_unwraps_gemma4_response_channel(node_available):
    html = _run_markdown_case(
        "<|channel>thought\ninternal reasoning<channel|><|channel>response\nFinal answer.<channel|>",
        "mod.processWithThinking(input)",
    )

    assert "thinking-section" in html
    assert "internal reasoning" in html
    assert "Final answer." in html
    assert "&lt;|channel&gt;" not in html
    assert "<|channel>" not in html


def test_extract_thinking_blocks_handles_thought_tag(node_available):
    result = _run_markdown_case(
        "<thought>internal reasoning</thought>Final answer.",
        "mod.extractThinkingBlocks(input)",
    )

    assert result["thinkingBlocks"] == ["internal reasoning"]
    assert result["content"] == "Final answer."


def test_dotted_python_import_paths_are_not_autolinked(node_available):
    html = _run_markdown_case(
        "from imblearn.combine import SMOTETomek\n"
        "from sklearn.metrics import f1_score\n"
        "from sklearn.compose import ColumnTransformer\n\n"
        "See example.com/docs for normal domain autolinking."
    )

    assert "___ALLOWED_HTML_" not in html
    assert "imblearn.combine" in html
    assert "sklearn.metrics" in html
    assert "sklearn.compose" in html
    assert 'href="https://imblearn.com' not in html
    assert 'href="https://sklearn.me' not in html
    assert 'href="https://example.com/docs"' in html


def test_spoiler_and_cloze_syntax_render_as_reveal_controls(node_available):
    html = _run_markdown_case(
        "Use ||spoiler text||, >!reddit spoiler!<, "
        "{{hidden answer}}, and {{c1::cloze answer::first hint}}."
    )

    assert html.count('<button type="button" class="quiz-reveal') == 4
    assert html.count('aria-expanded="false"') == 4
    assert 'class="quiz-reveal quiz-spoiler"' in html
    assert 'data-hidden-label="Spoiler"' in html
    assert 'data-hidden-label="Hint: first hint"' in html
    assert '<span class="quiz-reveal-text">spoiler text</span>' in html
    assert '<span class="quiz-reveal-text">reddit spoiler</span>' in html
    assert '<span class="quiz-reveal-text">hidden answer</span>' in html
    assert '<span class="quiz-reveal-text">cloze answer</span>' in html


def test_spoiler_syntax_inside_inline_code_stays_literal(node_available):
    html = _run_markdown_case("Literal `||not hidden||` and shown ||hidden||.")

    assert "<code>||not hidden||</code>" in html
    assert html.count('class="quiz-reveal quiz-spoiler"') == 1
    assert '<span class="quiz-reveal-text">hidden</span>' in html


def test_accidental_skill_pseudo_call_renders_as_hidden_answer(node_available):
    html = _run_markdown_case(
        "quiz-spoiler-markdown: **C) Saul**\n\n"
        "`quiz-spoiler-markdown: **B) David**`\n\n"
        "Literal prose keeps `quiz-spoiler-markdown: **A) Example**` as code."
    )

    assert html.count('<button type="button" class="quiz-reveal"') == 2
    assert 'data-hidden-label="Answer"' in html
    assert '<span class="quiz-reveal-text">C) Saul</span>' in html
    assert '<span class="quiz-reveal-text">B) David</span>' in html
    assert '<code>quiz-spoiler-markdown: **A) Example**</code>' in html


def test_raw_quiz_reveal_button_with_skill_pseudo_call_is_repaired(node_available):
    html = _run_markdown_case(
        '<button type="button" class="quiz-reveal" aria-label="Reveal hidden answer">'
        'quiz-spoiler-markdown: **B) Die Erschaffung des Lichts**'
        '</button>'
    )

    assert html.count('<button type="button" class="quiz-reveal"') == 1
    assert 'quiz-spoiler-markdown' not in html
    assert 'data-hidden-label="Answer"' in html
    assert '<span class="quiz-reveal-text">B) Die Erschaffung des Lichts</span>' in html


def test_callout_info_renders_with_title_and_icon(node_available):
    html = _run_markdown_case("> [!info] Heads up\n> body line one\n> body line two")

    assert 'class="md-callout md-callout-note"' in html
    assert 'class="md-callout-title"' in html
    assert "<span>Heads up</span>" in html
    assert "<svg" in html  # icon
    assert "<p>body line one</p>" in html
    assert "<p>body line two</p>" in html
    # Not rendered as a plain blockquote.
    assert "<blockquote>" not in html


def test_callout_type_aliases_map_to_color_classes(node_available):
    assert 'md-callout-warn' in _run_markdown_case("> [!warning] w")
    assert 'md-callout-danger' in _run_markdown_case("> [!danger] d")
    assert 'md-callout-tip' in _run_markdown_case("> [!tip] t")
    assert 'md-callout-quote' in _run_markdown_case("> [!quote] q")
    # Default title comes from the type when none is given.
    assert "<span>Warning</span>" in _run_markdown_case("> [!warning]")


def test_callout_foldable_renders_details(node_available):
    collapsed = _run_markdown_case("> [!note]- Click to expand\n> hidden body")
    assert "<details class=\"md-callout md-callout-note\"" in collapsed
    assert " open>" not in collapsed  # '-' => collapsed
    assert "<summary class=\"md-callout-title\">" in collapsed

    expanded = _run_markdown_case("> [!note]+ Open by default\n> shown body")
    assert "<details class=\"md-callout md-callout-note\" open>" in expanded


def test_plain_blockquote_still_works(node_available):
    html = _run_markdown_case("> just a quote\n> second line")
    assert "<blockquote>" in html
    assert "md-callout" not in html
    assert "<p>just a quote</p>" in html


def test_table_with_links_in_cells_still_renders(node_available):
    # Regression: a markdown link in ANY cell used to kill the whole table —
    # the link's <a> became an ___ALLOWED_HTML_ placeholder and the table guard
    # bailed on it. With a |---| separator present, inline links are allowed.
    html = _run_markdown_case(
        "Date | Milestone |\n"
        "|------|-----------|\n"
        "| 2026-03-28 | Benchmarks ([PR#698](https://github.com/tile-ai/tilelang-ascend/pull/698))|\n"
        "| 2025-09-29 | Open source release |"
    )
    assert "<table" in html
    assert html.count("<tr>") == 3                 # header + 2 data rows (separator skipped)
    assert "<th" in html and "<td" in html
    assert "<a " in html                            # the link survived as a real anchor
    assert "tilelang-ascend/pull/698" in html
    assert "| 2026-03-28" not in html               # NOT left as raw markdown text


def test_fenced_code_with_info_string_after_language(node_available):
    # Regression: "```python title=foo.py" failed the old `(\w+)?\n` fence regex,
    # so the block fell through and rendered as raw markdown (the `# comment`
    # line became an <h1>). The first info-string token is the language.
    html = _run_markdown_case(
        "```python title=kernel_to_dump.py\n"
        "# Define kernel\n"
        "kernel = matmul(K, N, M)\n"
        "```\n\n"
        "Then run:\n\n"
        "```bash\n"
        "python kernel_to_dump.py >> out.cpp\n"
        "```"
    )
    assert 'class="language-python"' in html         # block recognised + highlighted
    assert 'class="language-bash"' in html
    assert "<h1" not in html                          # the `# Define kernel` is code, not a heading
    assert "title=kernel_to_dump.py" not in html      # info-string metadata not leaked as text
    assert "Then run:" in html
