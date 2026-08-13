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
export function transformersFor(md) {
  // CHECK_LIST is NOT in md.TRANSFORMERS — the default element set is only
  // heading, quote, unordered list and ordered list. Without it `- [x] task`
  // parses as an ordinary bullet whose text happens to start with "[x]", which
  // round-trips byte-identically and therefore looks correct while rendering as
  // a plain bullet. It must also come FIRST: `- [ ] ` matches UNORDERED_LIST's
  // `/^(\s*)[-*+]\s/` too, and the first matching transformer wins.
  return [md.CHECK_LIST, ...md.TRANSFORMERS];
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
    markdown.registerMarkdownShortcuts(editor, transformersFor(markdown)),
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
export function loadMarkdown(editor, lex, text) {
  const { markdown } = lex;
  editor.update(() => {
    markdown.$convertFromMarkdownString(String(text ?? ''), transformersFor(markdown));
  });
}

/** Blocks -> markdown. Read-only; safe to call on every autosave tick. */
export function toMarkdown(editor, lex) {
  const { markdown } = lex;
  let out = '';
  editor.getEditorState().read(() => {
    out = markdown.$convertToMarkdownString(transformersFor(markdown));
  });
  return out;
}

export default { nodesFor, THEME, transformersFor, registerAll, loadMarkdown, toMarkdown };
