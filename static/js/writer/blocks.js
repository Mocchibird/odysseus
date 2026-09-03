// static/js/writer/blocks.js
//
// FORK-ONLY. The block vocabulary and the markdown round-trip.
//
// STORAGE DECISION (deliberate, see the writer commit message):
// Markdown is the canonical form and lives in Document.current_content, the same
// field the plain editor, the RAG index, the agent's document reads, versioning
// and export already use. Lexical's editor-state JSON is NOT persisted. That
// keeps documents readable by everything else in the app — and needs no new
// column, so no core/database.py change and no migration to conflict with
// upstream.
//
// The cost is that every block must survive markdown. Where CommonMark/GFM has
// no syntax, we add a transformer with a round-trippable spelling rather than
// inventing a block that silently loses data on save.

/** Node classes the editor must be told about before any plugin can use them. */
export function nodesFor({ richText, list, code, link, table }) {
  return [
    richText.HeadingNode,
    richText.QuoteNode,
    list.ListNode,
    list.ListItemNode,
    code.CodeNode,
    code.CodeHighlightNode,   // from @lexical/code-core; see registerAll re: prism
    link.LinkNode,
    table.TableNode,
    table.TableRowNode,
    table.TableCellNode,
  ];
}

/**
 * Theme class names. Every value maps to a rule in fork.css built from existing
 * CSS variables — no new palette, no new spacing scale.
 */
export const THEME = {
  paragraph: 'writer-p',
  heading: { h1: 'writer-h1', h2: 'writer-h2', h3: 'writer-h3', h4: 'writer-h4' },
  quote: 'writer-quote',
  list: {
    ul: 'writer-ul',
    ol: 'writer-ol',
    listitem: 'writer-li',
    listitemChecked: 'writer-li-checked',
    listitemUnchecked: 'writer-li-unchecked',
    nested: { listitem: 'writer-li-nested' },
  },
  code: 'writer-codeblock',
  link: 'writer-link',
  table: 'writer-table',
  tableRow: 'writer-tr',
  tableCell: 'writer-td',
  tableCellHeader: 'writer-th',
  text: {
    bold: 'writer-bold',
    italic: 'writer-italic',
    code: 'writer-code',
    strikethrough: 'writer-strike',
    highlight: 'writer-highlight',
    underline: 'writer-underline',
  },
};

/**
 * A horizontal rule has no Lexical node in our set, but `---` is real markdown
 * and people type it. Map it onto an empty quote-less paragraph would lose it, so
 * until a decorator node exists we let the default transformers drop through and
 * keep `---` as literal text. Documented so the omission is a choice, not a bug.
 */

/**
 * Markdown transformer list. Order matters: Lexical tries element transformers in
 * sequence, so more specific patterns must precede looser ones.
 */

/* ── import normalisation ────────────────────────────────────────────────────
 * Lexical's markdown importer has three behaviours that silently change a
 * document the first time it is opened. Since markdown is the canonical stored
 * form, "changed on open" means the next autosave PERSISTS the change — so these
 * are fixed on the way in rather than left to round-trip.
 */

/** Lexical nests one list level per FOUR spaces; 1-3 spaces read as level 0. */
const LIST_INDENT = 4;
const _LIST_ITEM = /^([ \t]*)([-*+]|\d+[.)])(\s)/;
const _FENCE = /^\s*(```|~~~)/;

/**
 * Re-indent list items to Lexical's 4-space unit.
 *
 * A document written with the usual 2-space nesting was FLATTENED on open (every
 * child became a top-level item) and then saved flat, destroying the outline.
 * Each distinct indent column maps to a nesting level, so 2-space, 3-space and
 * mixed files all import with their structure intact.
 *
 * Lines inside fenced code are left exactly as they are — indentation there is
 * content, not structure.
 */
function normalizeListIndent(text) {
  const lines = String(text ?? '').split('\n');
  const stack = [];          // indent widths, ascending; index = nesting level
  let fence = null;          // the fence marker we are inside, if any
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fenceMatch = line.match(_FENCE);
    if (fenceMatch) {
      if (fence && line.trim().startsWith(fence)) fence = null;
      else if (!fence) fence = fenceMatch[1];
      continue;
    }
    if (fence) continue;
    const m = line.match(_LIST_ITEM);
    if (!m) {
      // A blank line does not end a list; any other unindented text does.
      if (line.trim() === '') continue;
      if (!/^[ \t]/.test(line)) stack.length = 0;
      continue;
    }
    // Tabs count as one indent unit each, which is how editors render them here.
    const width = m[1].replace(/\t/g, ' '.repeat(LIST_INDENT)).length;
    while (stack.length && width < stack[stack.length - 1]) stack.pop();
    if (!stack.length || width > stack[stack.length - 1]) stack.push(width);
    const level = Math.max(0, stack.length - 1);
    lines[i] = ' '.repeat(level * LIST_INDENT) + line.slice(m[1].length);
  }
  return lines.join('\n');
}

/**
 * Compensate for the importer eating one leading space from the FIRST line of a
 * multi-line fenced block.
 *
 * Measured behaviour: a block with two or more content lines loses exactly one
 * leading space from its first line; single-line blocks and first lines with no
 * indent are untouched. It is cumulative — every open-and-save cycle strips
 * another space until the line is flush, quietly reindenting real code.
 *
 * This pads by one space so the imported text matches the source. If a future
 * re-vendor fixes the importer, the padding would over-correct — which is why
 * the fenced-code round-trip is asserted in tests/test_writer_isolation.py:
 * that test fails loudly instead of the drift going unnoticed.
 */
function padFencedFirstLine(text) {
  const lines = String(text ?? '').split('\n');
  let i = 0;
  while (i < lines.length) {
    const open = lines[i].match(_FENCE);
    if (!open) { i += 1; continue; }
    const marker = open[1];
    let close = -1;
    for (let j = i + 1; j < lines.length; j += 1) {
      if (lines[j].trim().startsWith(marker)) { close = j; break; }
    }
    const end = close === -1 ? lines.length : close;
    const contentCount = end - (i + 1);
    if (contentCount >= 2 && /^ /.test(lines[i + 1])) lines[i + 1] = ' ' + lines[i + 1];
    i = end + 1;
  }
  return lines.join('\n');
}

/**
 * Lower-case a checklist marker so `- [X]` imports as CHECKED.
 *
 * CHECK_LIST's regExp carries the /i flag, so `[X]` matches — but its replace
 * compares the captured marker to a lower-case "x", so a capital X imported as
 * UNCHECKED and then saved that way. Losing a tick is losing data.
 */
function normalizeCheckMarkers(text) {
  return String(text ?? '').replace(/^([ \t]*(?:[-*+]\s)\s?\[)X(\]\s)/gm, '$1x$2');
}

/**
 * CHECK_LIST with the bullet made REQUIRED.
 *
 * Lexical ships `/^(\s*)(?:[-*+]\s)?\s?(\[(\s|x)?\])\s/i` — the bullet is
 * optional, so an ordinary paragraph that merely BEGINS with "[x] " was
 * converted into a checklist item on open, and saved as one. Capture-group
 * numbering is unchanged (the bullet group was already non-capturing), so
 * Lexical's own replace still reads the right groups.
 */
function checkListRequiringBullet(md) {
  return { ...md.CHECK_LIST, regExp: /^(\s*)[-*+]\s\s?(\[(\s|x)?\])\s/i };
}

/**
 * Re-escape a leading block marker when exporting a PARAGRAPH.
 *
 * Lexical's importer honours `\#` and produces a paragraph, but its exporter
 * drops the backslash — so the file is rewritten as `# text`, and the NEXT open
 * parses that paragraph as an h1. Verified: `\# not a heading` imports as
 * [paragraph], round-trips to `# not a heading`, and reimports as [heading:h1].
 * The user's literal text silently became a heading.
 *
 * Only paragraphs are touched. A real heading/list/quote node exports through
 * its own transformer and never reaches this one.
 */
function paragraphEscapingBlockMarkers(lex) {
  const { core } = lex;
  return {
    dependencies: [core.ParagraphNode],
    export: (node, exportChildren) => {
      if (!core.$isParagraphNode(node)) return null;
      const text = exportChildren(node);
      if (typeof text !== 'string' || !text) return null;
      // Markers that would re-parse as a block if they led a line.
      return text.replace(
        /^(\s*)(#{1,6}\s|[-*+]\s|>\s?|\d+[.)]\s)/,
        (_m, ws, marker) => `${ws}\\${marker}`,
      );
    },
    // Import is Lexical's own default paragraph handling; this never matches.
    regExp: /$^/,
    replace: () => false,
    type: 'element',
  };
}

export function transformersFor(md, lex) {
  // CHECK_LIST is NOT in md.TRANSFORMERS — the default element set is only
  // heading, quote, unordered list and ordered list. Without it `- [x] task`
  // parses as an ordinary bullet whose text happens to start with "[x]", which
  // round-trips byte-identically and therefore looks correct while rendering as
  // a plain bullet. It must also come FIRST: `- [ ] ` matches UNORDERED_LIST's
  // `/^(\s*)[-*+]\s/` too, and the first matching transformer wins.
  const extra = lex && lex.core ? [paragraphEscapingBlockMarkers(lex)] : [];
  return [checkListRequiringBullet(md), ...extra, ...md.TRANSFORMERS];
}

/**
 * Register every editing behaviour. Returns one disposer.
 *
 * `registerMarkdownShortcuts` is what makes the surface feel like Super rather
 * than a textarea: typing `# `, `> `, `- `, `1. `, `- [ ] ` or ``` transforms the
 * block in place as you type, and `**bold**` closes into real formatting.
 */
export function registerAll(editor, lex) {
  const { richText, list, history, markdown, utils } = lex;
  return utils.mergeRegister(
    richText.registerRichText(editor),
    list.registerList(editor),
    history.registerHistory(editor, history.createEmptyHistoryState(), 300),
    markdown.registerMarkdownShortcuts(editor, transformersFor(markdown, lex)),
  );
  // registerLink is NOT here. In 0.49 it takes a signal-backed extension config
  // (it reads `config.validateUrl.peek()`), not a plain options object, so calling
  // it standalone throws. Registering LinkNode is enough for what we need today:
  // markdown [text](url) round-trips and renders as <a class="writer-link">. The
  // TOGGLE_LINK_COMMAND wiring comes with the link UI, via LinkExtension.
}

// NO SYNTAX HIGHLIGHTING, on purpose. @lexical/code's highlighter is
// @lexical/code-prism, which side-effect-imports `prismjs` plus a global-
// registering language set — a bare non-Lexical specifier that cannot be vendored
// cleanly. We use @lexical/code-core instead: CodeNode still gives real fenced
// code blocks with the language recorded, they just render unhighlighted.
// If highlighting is wanted later, reuse the highlight.js the app ALREADY loads
// (window.hljs, see chat.js) rather than adding a second highlighter.

/** Markdown -> blocks, replacing the document. */
export function loadMarkdown(editor, lex, text, onCommitted) {
  const { markdown, core } = lex;
  const source = padFencedFirstLine(normalizeCheckMarkers(normalizeListIndent(text)));
  editor.update(() => {
    markdown.$convertFromMarkdownString(source, transformersFor(markdown, lex));
  }, {
    tag: 'history-merge',
    // onUpdate runs after the update COMMITS. Callers that need to read the
    // resulting document (to baseline the clean state, say) must use this —
    // reading straight after editor.update() can still observe the old state.
    onUpdate: () => { if (typeof onCommitted === 'function') onCommitted(); },
  });
  // The surface keeps ONE editor and ONE history stack for its whole lifetime,
  // so without this an undo after switching documents walked back PAST the load
  // and wrote the previous document's body into the current one — which autosave
  // then persisted. Loading a document is not an editing step, so the undo stack
  // starts empty for it.
  if (core && core.CLEAR_HISTORY_COMMAND) {
    editor.dispatchCommand(core.CLEAR_HISTORY_COMMAND, undefined);
  }
}

/** Blocks -> markdown. Read-only; safe to call on every autosave tick. */
export function toMarkdown(editor, lex) {
  const { markdown } = lex;
  let out = '';
  editor.getEditorState().read(() => {
    out = markdown.$convertToMarkdownString(transformersFor(markdown, lex));
  });
  return out;
}

export default {
  nodesFor, THEME, transformersFor, registerAll, loadMarkdown, toMarkdown,
  normalizeListIndent, normalizeCheckMarkers, padFencedFirstLine,
};
