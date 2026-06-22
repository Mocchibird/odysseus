// static/js/markdown.js

/**
 * Markdown rendering and content processing utilities
 */

import uiModule from './ui.js';
import { splitTableRow } from './markdown/tableRow.js';
import { replaceEmojiShortcodes, hasEmojiShortcode } from './emojiShortcodes.js';

var escapeHtml = uiModule.esc;

function safeLinkUrl(rawUrl) {
  const url = String(rawUrl || '').trim();
  if (url.startsWith('#')) {
    return /^#[A-Za-z0-9_-]*$/.test(url) ? url : '';
  }
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch (_) {
    return '';
  }
  return '';
}

function linkHtml(text, url) {
  const safeUrl = safeLinkUrl(url);
  const safeText = escapeHtml(text);
  if (!safeUrl) return safeText;
  if (safeUrl.startsWith('#')) {
    return `<a href="${safeUrl}" class="chat-link">${safeText}</a>`;
  }
  return `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${safeText}</a>`;
}

function imageHtml(alt, url, title) {
  const safeUrl = safeLinkUrl(url);
  if (!safeUrl || safeUrl.startsWith('#')) return escapeHtml(alt || '');
  const safeAlt = escapeHtml(alt || '');
  const safeTitle = title ? ` title="${escapeHtml(title)}"` : '';
  return `<img src="${escapeHtml(safeUrl)}" alt="${safeAlt}"${safeTitle} loading="lazy" decoding="async">`;
}

function _isModelEndpointUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || ''), window.location.origin);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
    const path = parsed.pathname.replace(/\/+$/, '');
    return path === '/v1';
  } catch (_) {
    return false;
  }
}

/**
 * Sanitize the raw-HTML fragments that mdToHtml deliberately preserves from
 * the source text — <details> blocks (collapsible agent output) and <a> tags
 * (emitted by the markdown link pass). Those fragments are later restored
 * verbatim into innerHTML, so without scrubbing them a model — or any content
 * routed through here — could smuggle in an `<img onerror=...>`, an
 * `<a href="javascript:...">`, an `onmouseover=` handler, etc. and execute
 * script in the authenticated page (DOM XSS).
 *
 * Parsing into a <template> is inert: assigning to template.innerHTML neither
 * fetches resources nor runs scripts, so we can walk the resulting tree,
 * drop script-capable elements, and strip event-handler attributes and
 * dangerous URL schemes before the (now safe) fragment is handed back.
 */
const _ALLOWED_HTML_BAD_TAGS = new Set([
  'SCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META',
  'STYLE', 'BASE', 'FORM', 'NOSCRIPT', 'TEMPLATE',
  // Foreign-content roots. SVG/MathML have their own parser rules and are a
  // classic mutation-XSS vehicle — e.g. an SVG-namespaced <script>, whose
  // `tagName` is the lower-case 'script' and would slip a name check that
  // assumed HTML's upper-casing. They aren't needed in the <details>/<a>
  // fragments we preserve, so drop the whole subtree.
  'SVG', 'MATH',
]);
const _ALLOWED_HTML_URL_ATTRS = new Set([
  'href', 'src', 'srcset', 'xlink:href', 'action', 'formaction', 'background', 'poster',
]);

function _compactUrlSchemeValue(value) {
  return String(value || '').replace(/[\u0000-\u0020\u007f-\u009f]+/g, '').toLowerCase();
}

function _isDangerousUrl(value) {
  return /^(javascript|vbscript|data):/.test(_compactUrlSchemeValue(value));
}

function _isDangerousSrcset(value) {
  return String(value || '').split(',').some(candidate => _isDangerousUrl(candidate));
}

function _cleanAllowedHtmlOnce(htmlString) {
  const tpl = document.createElement('template');
  tpl.innerHTML = htmlString;
  for (const el of Array.from(tpl.content.querySelectorAll('*'))) {
    // Upper-case the tag for comparison: HTML tagNames are upper-case, but
    // SVG/MathML elements preserve their original (lower/camel) case, so a
    // raw `Set.has(el.tagName)` would miss e.g. a namespaced <script>.
    if (_ALLOWED_HTML_BAD_TAGS.has(el.tagName.toUpperCase())) {
      el.remove();
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      // Drop every inline event handler (onerror, onclick, onmouseover, ...)
      // and srcdoc (a frame-less script vector).
      if (name.startsWith('on') || name === 'srcdoc') {
        el.removeAttribute(attr.name);
        continue;
      }
      if (name === 'style') {
        const value = _compactUrlSchemeValue(attr.value);
        if (/javascript:|vbscript:|data:|expression\(/.test(value)) {
          el.removeAttribute(attr.name);
        }
        continue;
      }
      // Neutralize javascript:/vbscript:/data: in URL-bearing attributes.
      // Strip control/space chars first so e.g. "java\tscript:" can't slip by.
      if (_ALLOWED_HTML_URL_ATTRS.has(name)) {
        if (name === 'srcset' ? _isDangerousSrcset(attr.value) : _isDangerousUrl(attr.value)) {
          el.removeAttribute(attr.name);
        }
      }
    }
  }
  return tpl.innerHTML;
}

function sanitizeAllowedHtml(html) {
  const raw = String(html == null ? '' : html);
  // Non-browser context (e.g. a future SSR/Node import): fail closed by
  // escaping rather than trusting the markup.
  if (typeof document === 'undefined') return escapeHtml(raw);

  // Sanitize to a fixpoint. Re-parsing the serialized output can mutate the
  // tree (the basis of mutation-XSS), so re-clean until it stops changing.
  let out = raw;
  for (let i = 0; i < 4; i++) {
    const next = _cleanAllowedHtmlOnce(out);
    if (next === out) break;
    out = next;
  }
  return out;
}

function decodeHtmlishText(value) {
  return String(value || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_m, n) => {
      const cp = Number(n);
      return Number.isFinite(cp) ? String.fromCodePoint(cp) : '';
    })
    .replace(/&#x([0-9a-f]+);/gi, (_m, n) => {
      const cp = parseInt(n, 16);
      return Number.isFinite(cp) ? String.fromCodePoint(cp) : '';
    });
}

function cleanRevealAnswer(answer) {
  return decodeHtmlishText(answer)
    .trim()
    .replace(/^\s*quiz[-_\s]?spoiler[-_\s]?markdown\s*[:：-]\s*/i, '')
    .replace(/^(?:answer|antwort|lösung|loesung|correct\s+answer)\s*[:：-]\s*/i, '')
    .replace(/^(\*\*|__)([\s\S]{1,500})\1$/g, '$2')
    .replace(/^([*_`])([\s\S]{1,500})\1$/g, '$2')
    .trim();
}

/**
 * Check if text has unclosed think tag
 */
export function hasUnclosedThinkTag(text) {
  text = normalizeThinkingMarkup(text || '');
  const openCount =
    (text.match(/<(?:think(?:ing)?|thought)(?:\s+[^>]*)?>/gi) || []).length
    + (text.match(/<\|channel>thought/gi) || []).length;
  const closeCount =
    (text.match(/<\/(?:think(?:ing)?|thought)>/gi) || []).length
    + (text.match(/<channel\|>/gi) || []).length;
  return openCount > closeCount;
}

export function startsWithReasoningPrefix(text) {
  return /^\s*(?:thinking(?:\s+process)?\s*:|the user |i need |i should |i will |they are |the question |i can )/i.test(text || '');
}

export function normalizeThinkingMarkup(text) {
  if (!text) return text;
  let normalized = text;
  // MiniMax M-series can emit namespaced reasoning tags like
  // <mm:think>...</mm:think>. Normalize them into the shared thinking parser.
  normalized = normalized.replace(/<mm:think(\s+[^>]*)?>/gi, (_m, attrs = '') => `<think${attrs || ''}>`);
  normalized = normalized.replace(/<\/mm:think>/gi, '</think>');
  normalized = normalized.replace(/<thought(\s+[^>]*)?>/gi, (_m, attrs = '') => `<think${attrs || ''}>`);
  normalized = normalized.replace(/<\/thought>/gi, '</think>');
  normalized = normalized.replace(/<\|channel>thought\s*\n?([\s\S]*?)<channel\|>\s*/gi, (_m, content = '') => {
    const thought = String(content || '').trim();
    return thought ? `<think>${thought}</think>\n` : '';
  });
  normalized = normalized.replace(/<\|channel>response\s*\n?([\s\S]*?)<channel\|>/gi, (_m, content = '') => content || '');
  normalized = normalized.replace(/<\|channel>response\s*\n?/gi, '');
  normalized = normalized.replace(/<channel\|>/gi, '');
  return normalized;
}

function normalizePlainThinking(text) {
  if (!text) return text;
  text = normalizeThinkingMarkup(text);
  if (/<think/i.test(text)) return text;

  const trimmed = text.trimStart();
  if (!startsWithReasoningPrefix(trimmed)) return text;

  const replyStarts = [
    'Hey', 'Hi ', 'Hi!', 'Hello', 'Sure', 'Yes', 'No ', 'No,', 'Yo', 'OK',
    'Here', 'Absolutely', 'Of course', 'Great', 'Alright', 'Thanks', 'Welcome',
    'Good ', "I'm happy", "I'd be"
  ];
  const prefixRegex = /^(thinking(?:\s+process)?\s*:)\s*/i;
  const escapedReplyStarts = replyStarts.map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const boundaryRegex = new RegExp(
    `^([\\s\\S]*?)(\\n\\n(?=${escapedReplyStarts.join('|')}|I |What|Let|This |As ))[\\s\\S]*$`,
    'i'
  );
  const boundaryMatch = boundaryRegex.exec(trimmed);

  if (boundaryMatch) {
    const thinkBlock = boundaryMatch[1].replace(prefixRegex, '').trim();
    const reply = trimmed.slice(boundaryMatch[1].length).trimStart();
    if (thinkBlock && reply) return `<think>${thinkBlock}</think>\n\n${reply}`;
  }

  const lines = trimmed.split('\n');
  for (let index = 1; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;
    if (replyStarts.some((prefix) => line.startsWith(prefix))) {
      const thinkBlock = lines.slice(0, index).join('\n').replace(prefixRegex, '').trim();
      const reply = lines.slice(index).join('\n').trim();
      if (thinkBlock && reply) return `<think>${thinkBlock}</think>\n${reply}`;
    }
  }

  const withoutPrefix = trimmed.replace(prefixRegex, '');
  for (const prefix of replyStarts) {
    const rx = new RegExp(`[.!?]\\s*(${prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`);
    const match = rx.exec(withoutPrefix);
    if (match && match.index > 20) {
      const thinkBlock = withoutPrefix.slice(0, match.index + 1).trim();
      const reply = withoutPrefix.slice(match.index + 1).trim();
      if (thinkBlock && reply) return `<think>${thinkBlock}</think>\n${reply}`;
    }
  }

  return text;
}

/**
 * Extract all complete thinking blocks and remaining content
 */
export function extractThinkingBlocks(text) {
  // Handle malformed patterns: <think></think>\n...actual thinking...\n</think>
  // Some models emit an empty <think></think> then put thinking text outside,
  // closed by a second orphaned </think>.
  let normalized = normalizePlainThinking(text);
  // Collapse <think>short</think>...real thinking...</think> into one block
  // Models sometimes emit a trivial first block then continue thinking outside tags
  normalized = normalized.replace(/<think(?:ing)?(?:\s+[^>]*)?>.{0,30}<\/think(?:ing)?>\s*([\s\S]*?)<\/think(?:ing)?>/gi, (m, content) => {
    return '<think>' + content.trim() + '</think>';
  });

  // Merge consecutive <think> blocks (some models split thinking across multiple tags)
  normalized = normalized.replace(/<\/think(?:ing)?>\s*<think(?:ing)?(?:\s+[^>]*)?>/gi, '\n\n');

  // Extract thinking time attribute if present
  const timeMatch = normalized.match(/<think(?:ing)?\s+time="([\d.]+)"/i);
  const thinkingTime = timeMatch ? timeMatch[1] : null;
  // Strip time attribute for content extraction
  normalized = normalized.replace(/<think(?:ing)?\s+time="[\d.]+"/gi, '<think');

  const thinkRegex = /<think(?:ing)?(?:\s+[^>]*)?>([\s\S]*?)<\/think(?:ing)?>/gi;
  const thinkingBlocks = [];
  let match;

  // Extract all complete thinking blocks
  while ((match = thinkRegex.exec(normalized)) !== null) {
    const content = match[1].trim();
    if (content) thinkingBlocks.push(content);
  }

  // Remove all complete <think>/<thinking> blocks
  let cleanContent = normalized.replace(thinkRegex, '');

  // If there's an unclosed tag, decide between two cases:
  // (a) Stray opener at the very start with no real reply before it — typical
  //     of quantized models (MiniMax-AWQ) that emit a literal `<think>` token
  //     at the start of every reply without ever closing it. Strip just the
  //     opener and keep the body as the reply, otherwise the bubble looks
  //     blank on reload (the body was being treated as collapsed thinking).
  // (b) Cut-off mid-generation — there's already real reply text before the
  //     opener. Drop from the tag onward as before (it's truncated thinking).
  if (hasUnclosedThinkTag(normalized)) {
    const gemmaThoughtStart = cleanContent.search(/<\|channel>thought/i);
    if (gemmaThoughtStart >= 0) {
      const leakedThought = cleanContent
        .slice(gemmaThoughtStart)
        .replace(/^<\|channel>thought\s*\n?/i, '')
        .trim();
      if (gemmaThoughtStart === 0 && leakedThought) thinkingBlocks.push(leakedThought);
      cleanContent = cleanContent.slice(0, gemmaThoughtStart);
    } else {
      const strayOpener = cleanContent.match(/^\s*<think(?:ing)?(?:\s+[^>]*)?>([\s\S]*)$/i);
      if (strayOpener) {
        cleanContent = strayOpener[1];
      } else {
        cleanContent = cleanContent.replace(/<think(?:ing)?(?:\s+[^>]*)?>[\s\S]*$/gi, '');
      }
    }
  }

  // Handle orphaned </think> with no opening tag — text before it is leaked thinking
  const orphanMatch = cleanContent.match(/^([\s\S]+?)<\/think(?:ing)?>/i);
  if (orphanMatch && orphanMatch[1].trim()) {
    thinkingBlocks.push(orphanMatch[1].trim());
    cleanContent = cleanContent.slice(orphanMatch[0].length);
  }

  // Strip any remaining orphaned closing tags
  cleanContent = cleanContent.replace(/<\/think(?:ing)?>/gi, '');

  // Merge all thinking blocks into one — no reason to show multiple dropdowns
  const mergedBlocks = thinkingBlocks.length > 1
    ? [thinkingBlocks.join('\n\n')]
    : thinkingBlocks;

  return {
    thinkingBlocks: mergedBlocks,
    content: cleanContent.trim(),
    thinkingTime,
  };
}

/**
 * Create a collapsible thinking section
 */
function createThinkingSection(thinkingContent, index = 0, thinkingTime = null) {
  const id = `thinking-${Date.now()}-${index}`;
  const timeHtml = thinkingTime ? `<span style="font-size:11px;opacity:0.4;font-variant-numeric:tabular-nums;">${thinkingTime}s</span>` : '';
  return `
    <div class="thinking-section">
      <div class="thinking-header" data-thinking-id="${id}">
        <div class="thinking-header-left">
          <span>View thinking process</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;">
          ${timeHtml}
          <span class="thinking-toggle" id="${id}-toggle"></span>
        </div>
      </div>
      <div class="thinking-content" id="${id}">
        <div class="thinking-content-inner">
          ${mdToHtml(thinkingContent)}
        </div>
      </div>
    </div>
  `;
}

function createTaskCompletedMarker() {
  return `
    <div class="task-completed-marker" role="status" aria-label="Task completed">
      <span class="task-completed-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
      </span>
      <span>Task completed</span>
    </div>
  `;
}

/**
 * Process text and render with thinking sections
 */
// ── Emoji → monochrome SVG (OpenMoji-black via same-origin /api/emoji proxy) ──
// Replace colorful system/Twemoji emoji with single-color line icons tinted to
// the surrounding text color (project rule: never colorful emoji). Operates on
// rendered HTML: only touches text outside tags and skips <code>/<pre>.
const _EMOJI_RE = /\p{Extended_Pictographic}/u;
const _emojiSeg = (typeof Intl !== 'undefined' && Intl.Segmenter)
  ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;

function _emojiCodepoints(emoji) {
  // Twemoji filename rule: strip U+FE0F unless the sequence has a ZWJ (U+200D).
  const s = emoji.indexOf('‍') >= 0 ? emoji : emoji.replace(/️/g, '');
  const cps = [];
  for (const ch of s) { const c = ch.codePointAt(0); if (c) cps.push(c.toString(16)); }
  return cps.join('-');
}
function _emojiImg(emoji) {
  const code = _emojiCodepoints(emoji);
  if (!code) return emoji;
  // Monochrome line icon: the OpenMoji black SVG is used as a CSS mask filled
  // with the surrounding text color (currentColor), so emoji render as a single
  // theme-tinted line glyph — never colorful (project rule). If the proxy can't
  // supply the glyph it returns a transparent SVG, so the mask shows nothing.
  return `<span class="emoji" role="img" aria-label="${emoji}" style="--em:url('/api/emoji/${code}.svg')"></span>`;
}
function _svgifyText(text) {
  if (!_emojiSeg) return text;
  let out = '';
  for (const { segment } of _emojiSeg.segment(text)) {
    out += _EMOJI_RE.test(segment) ? _emojiImg(segment) : segment;
  }
  return out;
}
/** When "Text-only Emojis" is on, keep Unicode in HTML so deEmojify() can strip them. */
function _useSvgEmoji() {
  return typeof document === 'undefined' || !document.body?.classList.contains('text-emojis');
}

// `opts.shortcodes` (default true) controls the issue-#345 `:name:` → emoji
// expansion. Chat passes it through as true; document/email body renderers pass
// false so author-typed `:shortcode:` text stays literal (see mdToHtml callers).
// The Unicode-emoji → monochrome-SVG pass always runs regardless, so a real 😀
// in a document still renders as the themed line icon as it always has.
export function svgifyEmoji(html, opts) {
  if (!_useSvgEmoji() || !html) return html;
  const allowShortcodes = !opts || opts.shortcodes !== false;
  // Two reasons to walk the HTML: real Unicode emoji to turn into SVG icons,
  // or `:shortcode:` text the model emitted instead of an emoji (issue #345).
  const hasUnicode = _EMOJI_RE.test(html);
  const hasShortcode = allowShortcodes && hasEmojiShortcode(html);
  if (!hasUnicode && !hasShortcode) return html;
  const parts = html.split(/(<[^>]*>)/);   // odd indices = tags
  let codeDepth = 0;
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      const t = parts[i].toLowerCase();
      if (/^<(pre|code)[\s>]/.test(t)) codeDepth++;
      else if (/^<\/(pre|code)\s*>/.test(t)) codeDepth = Math.max(0, codeDepth - 1);
      continue;
    }
    if (codeDepth !== 0) continue;
    let seg = parts[i];
    // Expand shortcodes to Unicode first, then both they and any pre-existing
    // Unicode emoji get rendered as the same monochrome line icons below.
    if (hasShortcode) seg = replaceEmojiShortcodes(seg);
    if (_EMOJI_RE.test(seg)) seg = _svgifyText(seg);
    parts[i] = seg;
  }
  return parts.join('');
}
/**
 * Generic collapsible section that reuses the thinking-dropdown styling and its
 * delegated toggle (any `.thinking-header[data-thinking-id]`). The label drives
 * the "View <label>" / "Hide <label>" text via data-label. Used e.g. for the
 * vision-model image description on a user's photo message.
 */
export function createCollapsible(contentMarkdown, label = 'details') {
  const id = `collapse-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  const safeLabel = escapeHtml(label);
  return `
    <div class="thinking-section">
      <div class="thinking-header" data-thinking-id="${id}">
        <div class="thinking-header-left"><span data-label="${safeLabel}">View ${safeLabel}</span></div>
        <div style="display:flex;align-items:center;gap:6px;"><span class="thinking-toggle" id="${id}-toggle"></span></div>
      </div>
      <div class="thinking-content" id="${id}"><div class="thinking-content-inner">${mdToHtml(contentMarkdown)}</div></div>
    </div>`;
}

export function processWithThinking(text) {
  const { thinkingBlocks, content, thinkingTime } = extractThinkingBlocks(text);

  let html = '';
  let visibleContent = content || '';
  const doneOnly = /^\s*\[DONE\]\s*$/i.test(visibleContent);
  const hadTrailingDone = !doneOnly && /(?:^|\n)\s*\[DONE\]\s*$/i.test(visibleContent);

  // Add thinking sections (collapsed by default)
  thinkingBlocks.forEach((block, index) => {
    html += createThinkingSection(block, index, thinkingTime);
  });

  // Add the actual content
  if (doneOnly) {
    html += createTaskCompletedMarker();
  } else {
    if (hadTrailingDone) visibleContent = visibleContent.replace(/\n?\s*\[DONE\]\s*$/i, '').trimEnd();
    if (visibleContent) html += mdToHtml(visibleContent);
    if (hadTrailingDone) html += createTaskCompletedMarker();
  }

  return _useSvgEmoji() ? svgifyEmoji(html) : html;
}

/**
 * Convert markdown to HTML
 */
// ---- Callout (admonition) metadata for `> [!type]` blocks ----
const _coIco = (paths) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const _CO_INFO = _coIco('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>');
const _CO_PENCIL = _coIco('<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>');
const _CO_FLAME = _coIco('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>');
const _CO_CHECK = _coIco('<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>');
const _CO_QUESTION = _coIco('<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>');
const _CO_WARN = _coIco('<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>');
const _CO_X = _coIco('<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>');
const _CO_ZAP = _coIco('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>');
const _CO_BUG = _coIco('<rect x="8" y="6" width="8" height="14" rx="4"/><path d="M19 8l-3 2"/><path d="M5 8l3 2"/><path d="M19 16l-3-2"/><path d="M5 16l3-2"/>');
const _CO_QUOTE = _coIco('<path d="M10 11H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v6c0 2-1 3-3 4"/><path d="M20 11h-4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v6c0 2-1 3-3 4"/>');
const _CO_LIST = _coIco('<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>');
// type -> { cls (drives accent color, see .md-callout-* CSS), label, icon }
const _CALLOUTS = {
  note: { cls: 'note', label: 'Note', icon: _CO_PENCIL },
  info: { cls: 'note', label: 'Info', icon: _CO_INFO },
  todo: { cls: 'note', label: 'Todo', icon: _CO_LIST },
  abstract: { cls: 'note', label: 'Abstract', icon: _CO_LIST },
  summary: { cls: 'note', label: 'Summary', icon: _CO_LIST },
  tldr: { cls: 'note', label: 'TL;DR', icon: _CO_LIST },
  example: { cls: 'note', label: 'Example', icon: _CO_LIST },
  tip: { cls: 'tip', label: 'Tip', icon: _CO_FLAME },
  hint: { cls: 'tip', label: 'Hint', icon: _CO_FLAME },
  important: { cls: 'tip', label: 'Important', icon: _CO_FLAME },
  success: { cls: 'tip', label: 'Success', icon: _CO_CHECK },
  check: { cls: 'tip', label: 'Success', icon: _CO_CHECK },
  done: { cls: 'tip', label: 'Done', icon: _CO_CHECK },
  question: { cls: 'warn', label: 'Question', icon: _CO_QUESTION },
  help: { cls: 'warn', label: 'Help', icon: _CO_QUESTION },
  faq: { cls: 'warn', label: 'FAQ', icon: _CO_QUESTION },
  warning: { cls: 'warn', label: 'Warning', icon: _CO_WARN },
  caution: { cls: 'warn', label: 'Caution', icon: _CO_WARN },
  attention: { cls: 'warn', label: 'Attention', icon: _CO_WARN },
  failure: { cls: 'danger', label: 'Failure', icon: _CO_X },
  fail: { cls: 'danger', label: 'Failure', icon: _CO_X },
  missing: { cls: 'danger', label: 'Missing', icon: _CO_X },
  danger: { cls: 'danger', label: 'Danger', icon: _CO_ZAP },
  error: { cls: 'danger', label: 'Error', icon: _CO_ZAP },
  bug: { cls: 'danger', label: 'Bug', icon: _CO_BUG },
  quote: { cls: 'quote', label: 'Quote', icon: _CO_QUOTE },
  cite: { cls: 'quote', label: 'Quote', icon: _CO_QUOTE },
};
function _calloutMeta(type) {
  return _CALLOUTS[type] || { cls: 'note', label: type.charAt(0).toUpperCase() + type.slice(1), icon: _CO_INFO };
}

export function mdToHtml(src, opts) {
  const allowedHtmlBlocks = [];
  const codeBlocks = [];
  const inlineCodeBlocks = [];
  const mermaidBlocks = [];
  let s = (src ?? '');

  // Extract fenced code blocks before any markdown/HTML preservation passes.
  // Otherwise placeholders from the allowed-HTML sanitizer (e.g.
  // ___ALLOWED_HTML_0___) can leak into quoted HTML/JS samples, because the
  // placeholder gets captured as literal code content and never restored inside
  // the final <pre><code> block.
  s = s.replace(/```([^\n]*)\n([\s\S]*?)```/g, (_, info, code) => {
    // The first token of the info string is the language; any remainder
    // (e.g. "```python title=foo.py") is metadata we don't render. The old
    // `(\w+)?\n` form failed to match when anything followed the language, so
    // the whole block fell through and rendered as raw markdown.
    const lang = String(info || '').trim().split(/\s+/)[0] || '';
    const cleaned = code
      .replace(/\r\n/g, '\n')
      .replace(/[ \t]+$/gm, '')
      .replace(/^\s*\n+/, '')
      .replace(/\n+\s*$/g, '');

    // Mermaid diagrams: render as diagram instead of code block
    if (lang && lang.toLowerCase() === 'mermaid') {
      const mermaidId = 'mermaid-' + Date.now() + '-' + mermaidBlocks.length;
      const raw = cleaned.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
      const placeholder = `___MERMAID_BLOCK_${mermaidBlocks.length}___`;
      mermaidBlocks.push(`<div class="mermaid-container"><pre class="mermaid" id="${mermaidId}">${escapeHtml(raw)}</pre></div>`);
      return placeholder;
    }

    const escaped = cleaned.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    const placeholder = `___CODE_BLOCK_${codeBlocks.length}___`;

    const langClass = lang ? ` class="language-${lang}"` : '';
    const runnableLangs = ['python','py','javascript','js','html','bash','sh','shell','zsh'];
    const runBtn = (lang && runnableLangs.includes(lang.toLowerCase()))
      ? `<button type="button" class="run-code" data-code="${escapeHtml(escaped)}" data-lang="${lang}" title="Run code"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>`
      : '';
    const editBtn = `<button type="button" class="edit-code" title="Edit"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>`;
    codeBlocks.push(`<pre><code${langClass} data-lang="${lang || ''}">${escapeHtml(escaped)}</code>${runBtn}${editBtn}<button type="button" class="copy-code" data-code="${escapeHtml(escaped)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></pre>`);

    return placeholder;
  });

  const pushReveal = (answer, options = {}) => {
    const text = cleanRevealAnswer(answer);
    if (!text) return '';
    const kind = options.kind || 'hidden';
    const labelText = String(options.label || '').trim() || (kind === 'spoiler' ? 'Spoiler' : 'Reveal');
    const revealLabel = kind === 'spoiler' ? 'Reveal spoiler' : 'Reveal hidden answer';
    const hideLabel = kind === 'spoiler' ? 'Hide spoiler' : 'Hide hidden answer';
    const hint = String(options.hint || '').trim();
    const visibleLabel = hint ? `Hint: ${hint}` : labelText;
    const classes = ['quiz-reveal', kind === 'spoiler' ? 'quiz-spoiler' : ''].filter(Boolean).join(' ');
    const placeholder = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    allowedHtmlBlocks.push(
      `<button type="button" class="${classes}" aria-label="${escapeHtml(revealLabel)}" aria-expanded="false" data-hidden-label="${escapeHtml(visibleLabel)}" data-reveal-label="${escapeHtml(revealLabel)}" data-hide-label="${escapeHtml(hideLabel)}"><span class="quiz-reveal-text">${escapeHtml(text)}</span></button>`
    );
    return placeholder;
  };

  const preserveQuizRevealButton = (match, inner) => {
    const openTag = (String(match || '').match(/^<button\b[^>]*>/i) || [''])[0];
    const kind = /\bquiz-spoiler\b/i.test(openTag) ? 'spoiler' : 'hidden';
    const placeholder = pushReveal(inner, { kind, label: kind === 'spoiler' ? 'Spoiler' : 'Answer' });
    return placeholder || '';
  };

  // Compatibility for the failure mode where the agent writes the skill name
  // as a visible pseudo-call instead of using the actual Iris reveal syntax:
  //   quiz-spoiler-markdown: **C) Saul**
  // Some models even wrap that whole line in backticks, so run this before
  // inline-code protection. Restrict to a standalone line to keep prose
  // examples like "Use `quiz-spoiler-markdown: ...`" literal.
  s = s.replace(
    /(^|\n)\s*`?\s*quiz[-_\s]?spoiler[-_\s]?markdown\s*:\s*([^`\n]{1,500})\s*`?\s*(?=\n|$)/gi,
    (match, prefix, answer) => `${prefix}${pushReveal(answer, { kind: 'hidden', label: 'Answer' }) || match}`,
  );

  // Protect inline code before spoiler/quiz/link passes. Otherwise examples
  // like `||spoiler||` get converted instead of rendered literally.
  s = s.replace(/`([^`\n]+?)`/g, (_match, code) => {
    const placeholder = `___INLINE_CODE_${inlineCodeBlocks.length}___`;
    inlineCodeBlocks.push(`<code>${escapeHtml(code)}</code>`);
    return placeholder;
  });

  // Compatibility for an even messier model output: raw HTML reveal buttons
  // whose visible text is still the skill pseudo-call. Regenerate the button
  // from its inner text so the app owns the markup and the answer is hidden.
  s = s.replace(
    /<button\b(?=[^>]*\bclass\s*=\s*(?:"[^"]*\bquiz-reveal\b[^"]*"|'[^']*\bquiz-reveal\b[^']*'|[^\s>]*\bquiz-reveal\b[^\s>]*))[^>]*>([\s\S]{0,1200}?)<\/button>/gi,
    preserveQuizRevealButton,
  );

  // Repair common ways the agent mangles the entity-anchor convention
  // (`[Name](#kind-<id>)`). Models reliably get the single-link case
  // right but slip into other formats when listing many in a table.
  // These regexes upgrade the broken forms to proper markdown links so
  // the standard `[text](url)` handler below picks them up.
  const ANCHOR_KIND = '(?:session|document|note|image|email|event|task|skill|research)';
  // Case A: `[Name] [#kind-id]` — agent put the URL in brackets, often
  // in a table cell next to the label. Pair them.
  s = s.replace(
    new RegExp(`\\[([^\\]\\n]+?)\\]\\s*\\[#(${ANCHOR_KIND}-[A-Za-z0-9_-]+)\\]`, 'g'),
    '[$1](#$2)',
  );
  // Case B: bare `[#kind-id]` with no preceding label — give it a
  // generic "→ open" link text so it still renders as a button.
  s = s.replace(
    new RegExp(`\\[#(${ANCHOR_KIND}-[A-Za-z0-9_-]+)\\]`, 'g'),
    '[→ open](#$1)',
  );
  // Case C: bare `#kind-id` in plain text — only when it's word-
  // boundary delimited and NOT already inside a markdown link or
  // anchor syntax. Use a lookbehind for `](` or `[` to skip those.
  s = s.replace(
    new RegExp(`(^|[^\\[(])#(${ANCHOR_KIND}-[A-Za-z0-9_-]+)\\b`, 'g'),
    '$1[#$2](#$2)',
  );

  // Obsidian-style wiki-links + image embeds. A wiki-link is resolved to a
  // document on CLICK (see the .wiki-link handler in document.js), so the
  // target can be renamed later and we avoid a fetch per render. Emitted as a
  // stashed allowed-HTML block (escaped) so it survives the escape/sanitize
  // passes. The page/alias are escaped, so no injection.
  const _wikiLink = (display, page) => {
    const ph = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    allowedHtmlBlocks.push(`<a href="#" class="chat-link wiki-link" data-wikilink="${escapeHtml(page)}">${escapeHtml(display)}</a>`);
    return ph;
  };
  const _mdImage = (alt, url, title) => {
    const safe = safeLinkUrl(url);
    if (!safe) return null;
    const ph = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    const safeTitle = title ? ` title="${escapeHtml(title)}"` : '';
    allowedHtmlBlocks.push(`<img class="md-img" src="${escapeHtml(safe)}" alt="${escapeHtml(alt || '')}"${safeTitle} loading="lazy" decoding="async">`);
    return ph;
  };
  // A gallery embed by human NAME (e.g. `![[beach sunset]]`). The renderer is
  // synchronous and can't hit the gallery API mid-render, so emit a graceful
  // inline chip (image icon + name) carrying the name in data-gallery-name; a
  // consumer that supports it (the document preview) calls resolveGalleryEmbeds
  // afterwards to swap a match in. Where no resolver runs (e.g. chat) the chip
  // just shows the name — never a broken image.
  const _galleryEmbed = (name) => {
    const ph = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    const esc = escapeHtml(name);
    allowedHtmlBlocks.push(
      `<span class="md-gallery-embed md-gallery-pending" data-gallery-name="${esc}">` +
      `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.5-3.5a2 2 0 0 0-2.8 0L5 21"/></svg>` +
      `<span class="md-gallery-name">${esc}</span></span>`
    );
    return ph;
  };
  // `![[name]]` Obsidian embed — MUST run before the `[[name]]` wiki-link pass
  // (else the inner `[[name]]` is consumed, leaving a stray `!`) and before
  // flashcards. An explicit /api path or http(s) URL is used as-is; a stored
  // content-hash filename hits the serving path directly; anything else is a
  // human gallery NAME → resolved against the gallery after render.
  s = s.replace(/!\[\[([^\[\]\n|]+?)\]\]/g, (match, name) => {
    name = name.trim();
    if (/^(https?:|\/)/i.test(name)) return _mdImage(name, name) || _galleryEmbed(name);
    if (/^[a-f0-9]{8,64}\.(png|jpe?g|gif|webp|svg|avif|bmp|mp4|mov|webm|mkv|m4v)$/i.test(name)) {
      return _mdImage(name, '/api/generated-image/' + name) || _galleryEmbed(name);
    }
    return _galleryEmbed(name);
  });
  // `[[Page]]` / `[[Page|alias]]` wiki-links. Excludes `[[a::b]]` (flashcards,
  // handled next) by bailing when the inner text contains `::`.
  s = s.replace(/\[\[([^\[\]\n]+?)\]\]/g, (match, inner) => {
    if (inner.includes('::')) return match;
    const pipe = inner.indexOf('|');
    const page = (pipe >= 0 ? inner.slice(0, pipe) : inner).trim();
    const alias = (pipe >= 0 ? inner.slice(pipe + 1) : inner).trim();
    return page ? _wikiLink(alias || page, page) : match;
  });

  // Lightweight flashcard syntax: [[front::back]] renders as a click-to-flip
  // card. Code blocks have already been extracted, so examples inside fenced
  // or inline code are left untouched.
  s = s.replace(/\[\[([^\[\]\n]{1,240})::([^\[\]\n]{1,400})\]\]/g, (_match, front, back) => {
    const placeholder = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    allowedHtmlBlocks.push(`<button type="button" class="quiz-flashcard" data-back="${escapeHtml(back.trim())}" aria-label="Reveal flashcard answer"><span>${escapeHtml(front.trim())}</span></button>`);
    return placeholder;
  });

  // Lightweight spoiler and quiz/cloze syntax. These render as click-to-
  // reveal pills:
  //   ||spoiler||, >!spoiler!<, <spoiler>spoiler</spoiler>
  //   {{hidden answer}}, {{c1::hidden answer}}, {{c1::hidden answer::hint}}
  s = s.replace(/\|\|([^|\n]{1,500})\|\|/g, (match, answer) => {
    return pushReveal(answer, { kind: 'spoiler', label: 'Spoiler' }) || match;
  });
  s = s.replace(/>!([^!\n]{1,500})!</g, (match, answer) => {
    return pushReveal(answer, { kind: 'spoiler', label: 'Spoiler' }) || match;
  });
  s = s.replace(/<spoiler>([^<>\n]{1,500})<\/spoiler>/gi, (match, answer) => {
    return pushReveal(answer, { kind: 'spoiler', label: 'Spoiler' }) || match;
  });
  s = s.replace(/\{\{c\d+::([^{}\n]+?)(?:::([^{}\n]{1,120}))?\}\}/gi, (match, answer, hint) => {
    return pushReveal(answer, { kind: 'hidden', label: 'Reveal', hint }) || match;
  });
  s = s.replace(/\{\{([^{}\n]{1,300})\}\}/g, (match, answer) => {
    return pushReveal(answer, { kind: 'hidden', label: 'Reveal' }) || match;
  });

  // Images ![alt](url) — MUST run before the [text](url) link pass so the
  // image isn't turned into a plain link. URL gated by safeLinkUrl (http(s) +
  // same-origin relative, e.g. /api/generated-image/… or /api/files/…/raw);
  // unsafe → plain alt text (no broken <img>). The optional "title" is
  // captured and forwarded (upstream improvement); alt is constrained to a
  // single line so it doesn't run across newlines.
  s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g, (match, alt, url, title) => {
    return _mdImage(alt, url, title) || escapeHtml(alt);
  });

  // Convert markdown links [text](url) to clickable links
  // Internal #hash links navigate in-page; external links open in new tab
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
    return linkHtml(text, url);
  });

  // Autolink bare URLs (http/https). Skips URLs already inside <a> tags
  // (placed by markdown link replacement above) and URLs in backticks.
  s = s.replace(
    /(^|[\s(<])(https?:\/\/[^\s<>"'`\]]+[^\s<>"'`\].,;:!?])/g,
    (match, prefix, url) => `${prefix}${linkHtml(url, url)}`
  );

  // Autolink scheme-less domains the model often emits as plain text
  // (e.g. "techcrunch.com/ai", "perplexity.ai", "www.wired.com"). The TLD
  // allowlist keeps it from matching file names / versions ("package.json",
  // "node.js", "v1.2.3"); the required start/[\s(<] prefix means domains
  // already inside an http link (preceded by "//") or an email ("@") are
  // skipped. Require the TLD to end at a real domain boundary so dotted code
  // identifiers like `sklearn.metrics` do not link `sklearn.me` and leave
  // placeholder fragments in the remaining text.
  s = s.replace(
    /(^|[\s(<])((?:www\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|ai|co|dev|app|gov|edu|news|info|tech|xyz|me)(?=$|[\/\s<>"'`\]).,;:!?])(?:\/[^\s<>"'`\])]*)?)/gi,
    (match, prefix, domain) => {
      const trail = (domain.match(/[.,;:!?)]+$/) || [''])[0];
      const core = trail ? domain.slice(0, -trail.length) : domain;
      return `${prefix}${linkHtml(core, 'https://' + core)}${trail}`;
    }
  );

  // Extract <details>...</details> blocks and replace with placeholders
  // Default to open so agent output is visible
  s = s.replace(/<details>([\s\S]*?)<\/details>/gi, (match) => {
    const placeholder = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    allowedHtmlBlocks.push(sanitizeAllowedHtml(match.replace(/<details>/i, '<details open>')));
    return placeholder;
  });

  // ALSO preserve <a>/<img> tags the same way (they're now in the HTML from
  // markdown conversion)
  s = s.replace(/<(?:a\s+[^>]*>.*?<\/a|img\s+[^>]*?)>/gi, (match) => {
    const placeholder = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
    allowedHtmlBlocks.push(sanitizeAllowedHtml(match));
    return placeholder;
  });

  // Now escape everything else
  s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  s = s.replace(/\n{3,}/g, '\n\n');

  // KaTeX math rendering (after code blocks are extracted, so math in code is safe)
  const mathBlocks = [];
  if (window.katex) {
    // Display math: \[ ... \]  — GPT-style delimiter (gpt-5.x, Claude, etc.).
    // Handle before $$/$ so all common delimiters render.
    s = s.replace(/\\\[([\s\S]*?)\\\]/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: true, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
    // Inline math: \( ... \)  — GPT-style inline delimiter. Single-line only
    // ([^\n]) so a stray escaped paren in prose can't swallow across lines.
    s = s.replace(/\\\(([^\n]*?)\\\)/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: false, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
    // Display math: $$...$$
    s = s.replace(/\$\$([\s\S]*?)\$\$/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: true, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
    // Inline math: $...$  (not preceded/followed by $ or digit, not spanning multiple lines)
    s = s.replace(/(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)/g, (match, math) => {
      try {
        const raw = math.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const placeholder = `___MATH_BLOCK_${mathBlocks.length}___`;
        mathBlocks.push(katex.renderToString(raw.trim(), { displayMode: false, throwOnError: false }));
        return placeholder;
      } catch (e) { return match; }
    });
  }

  // Handle pipe tables
  s = s.replace(/(?:^|\n)([^\n]*\|[^\n]*\|[^\n]*)(?:\n([^\n]*\|[^\n]*\|[^\n]*))*/g, (table) => {
    const rows = table.trim().split('\n');
    if (rows.length < 2) return table;

    // A genuine markdown table has a `|---|---|` rule on its 2nd line. When it
    // does, inline placeholders in cells (links, images, wiki-links — all
    // stashed as ___ALLOWED_HTML_) are legitimate content, so DON'T bail on
    // them; a single link in a cell used to kill the whole table. A fenced
    // code block must never be tablified. Without a separator the block is
    // likely code / raw HTML that merely contains pipes — keep bailing then.
    const hasSeparator = /^[\s|:\-]+$/.test(rows[1]) && rows[1].includes('-');
    if (table.includes('___CODE_BLOCK_')) return table;
    if (!hasSeparator && table.includes('___ALLOWED_HTML_')) return table;

    let html = '<table style="border-collapse: collapse; width: 100%; margin: 10px 0;">';

    rows.forEach((row, idx) => {
      if (idx === 1 && /^[\s|:\-]+$/.test(row)) {
        html += '<tbody>';
        return;
      }
      const cells = splitTableRow(row);
      if (cells.length === 0) return;

      html += '<tr>';

      cells.forEach(cell => {
        const tag = idx === 0 ? 'th' : 'td';
        html += `<${tag} style="padding: 8px; text-align: left; border-bottom: 1px solid var(--border);">${cell.trim()}</${tag}>`;
      });

      html += '</tr>';
    });

    html += '</tbody></table>';
    return html;
  });

  // Horizontal rules (must come before bold/italic to avoid * conflicts)
  s = s.replace(/^(?:---|\*\*\*|___)\s*$/gm, '<hr>');

  // Bold, italic, strikethrough
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  // Headers
  s = s.replace(/^###### (.*)$/gm, '<h6>$1</h6>')
       .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
       .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
       .replace(/^### (.*)$/gm, '<h3>$1</h3>')
       .replace(/^## (.*)$/gm, '<h2>$1</h2>')
       .replace(/^# (.*)$/gm, '<h1>$1</h1>');

  // Lists — indentation-aware (nested) parser. Replaces the old flat per-line
  // regexes so `  - sub` / `  - [ ]` actually NEST in the reader. Runs on the
  // already-escaped string; an item's `content` may carry ___…___ placeholders
  // and <strong>/<a> from earlier passes, so it is passed through untouched
  // (never re-escaped). Emits final <ul>/<ol>/<li> with nested lists placed
  // INSIDE the parent <li> (proper HTML). Task items keep the exact markup the
  // task CSS expects. A blank line inside a list is a continuation (it does not
  // split the list); only a non-blank, non-list line ends it.
  s = (function _parseLists(src) {
    const lines = src.split('\n');
    const out = [];
    const stack = [];                                   // frames: {indent, type, openLi}
    const tabW = (ws) => ws.replace(/\t/g, '    ').length;  // a tab counts as 4 cols
    const top = () => stack[stack.length - 1];
    const closeLi = () => { if (stack.length && top().openLi) { out.push('</li>'); top().openLi = false; } };
    const closeList = () => { closeLi(); out.push(`</${stack.pop().type}>`); };
    const classify = (line) => {
      let m;
      if ((m = /^([ \t]*)(?:[-*] )\[([ xX])\] (.*)$/.exec(line))) return { type: 'ul', indent: tabW(m[1]), task: m[2], content: m[3] };
      if ((m = /^([ \t]*)(?:[-*] )(.*)$/.exec(line)))             return { type: 'ul', indent: tabW(m[1]), task: null, content: m[2] };
      if ((m = /^([ \t]*)(\d+)\. (.*)$/.exec(line)))              return { type: 'ol', indent: tabW(m[1]), task: null, content: m[3] };
      return null;
    };
    const liOpen = (item) => {
      if (item.task !== null) {
        const done = item.task.toLowerCase() === 'x';
        return `<li class="task-item${done ? ' task-done' : ''}"><span class="task-check" aria-hidden="true"></span><span class="task-text">${item.content}</span>`;
      }
      return `<li>${item.content}`;
    };
    for (const line of lines) {
      const item = classify(line);
      if (!item) {
        if (line.trim() === '' && stack.length) { out.push(line); continue; }  // blank → continuation
        while (stack.length) closeList();
        out.push(line);
        continue;
      }
      while (stack.length && item.indent < top().indent) closeList();          // dedent
      if (!stack.length || item.indent > top().indent) {                       // indent → nest inside the open <li>
        stack.push({ indent: item.indent, type: item.type, openLi: false });
        out.push(`<${item.type}>`);
      } else {                                                                 // same level
        closeLi();
        if (top().type !== item.type) { closeList(); stack.push({ indent: item.indent, type: item.type, openLi: false }); out.push(`<${item.type}>`); }
      }
      out.push(liOpen(item));
      top().openLi = true;
    }
    while (stack.length) closeList();
    return out.join('\n');
  })(s);

  // Blockquotes + callouts (Obsidian/GitHub admonitions: `> [!info] …`).
  // `>` was escaped to `&gt;` above. Mark each quoted line (allow a bare
  // `&gt;` blank line inside a quote so multi-paragraph quotes/callouts group).
  s = s.replace(/^&gt;\s?(.*)$/gm, '<bq>$1</bq>');
  s = s.replace(/(?:^|\n)(<bq>[\s\S]*?)(?=\n(?!<bq>)|$)/g, (m) => {
    const lines = m.trim().split('\n').map((l) => l.replace(/^<bq>([\s\S]*)<\/bq>$/, '$1'));
    // Callout? First line is `[!TYPE]`, optional fold marker (-/+), optional title.
    const cm = (lines[0] || '').match(/^\s*\[!(\w+)\]([+-]?)\s*(.*)$/);
    if (cm) {
      const meta = _calloutMeta(cm[1].toLowerCase());
      const fold = cm[2];                       // '' static | '-' collapsed | '+' expanded
      const title = (cm[3] || '').trim() || meta.label;
      const body = lines.slice(1).map((l) => (l.trim() ? `<p>${l}</p>` : '')).join('');
      const titleHtml = `${meta.icon}<span>${title}</span>`;
      const bodyHtml = body ? `<div class="md-callout-body">${body}</div>` : '';
      const html = fold
        ? `<details class="md-callout md-callout-${meta.cls}"${fold === '+' ? ' open' : ''}><summary class="md-callout-title">${titleHtml}</summary>${bodyHtml}</details>`
        : `<div class="md-callout md-callout-${meta.cls}"><div class="md-callout-title">${titleHtml}</div>${bodyHtml}</div>`;
      // Stash as an allowed-HTML block so the paragraph-wrap pass below (which
      // would otherwise wrap a bare <div>/<details> line in <p>) skips it.
      const ph = `___ALLOWED_HTML_${allowedHtmlBlocks.length}___`;
      allowedHtmlBlocks.push(html);
      return `\n${ph}`;
    }
    return `<blockquote>${lines.map((l) => `<p>${l}</p>`).join('')}</blockquote>`;
  });

  // Paragraphs - but NOT for code block placeholders or allowed HTML
  s = s.replace(/^(?!<h\d|<ul>|<ol>|<li|<oli>|<\/li>|<\/ul>|<\/ol>|<pre>|<blockquote>|<bq>|<hr>|___CODE_BLOCK_|___ALLOWED_HTML_|___MATH_BLOCK_|___MERMAID_BLOCK_)([^\n]+)$/gm, '<p>$1</p>');

  // Line breaks within paragraphs
  s = s.replace(/<p>([\s\S]*?)<\/p>/g, (match, content) => {
    if (content.includes('___CODE_BLOCK_') || content.includes('___ALLOWED_HTML_') || content.includes('___MATH_BLOCK_') || content.includes('___MERMAID_BLOCK_')) return match;
    const withLineBreaks = content.replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>');
    return `<p>${withLineBreaks}</p>`;
  });

  // Remove empty paragraphs
  s = s.replace(/<p><\/p>/g, '');

  // CRITICAL: Restore allowed HTML blocks first
  allowedHtmlBlocks.forEach((block, index) => {
    s = s.replace(`___ALLOWED_HTML_${index}___`, block);
  });

  // Restore math blocks
  mathBlocks.forEach((block, index) => {
    s = s.replace(`___MATH_BLOCK_${index}___`, block);
  });

  // Restore mermaid diagram blocks
  mermaidBlocks.forEach((block, index) => {
    s = s.replace(`___MERMAID_BLOCK_${index}___`, block);
  });

  // Restore inline code after paragraph/list/header processing, so the code
  // element can live naturally inside whichever block contains it.
  inlineCodeBlocks.forEach((block, index) => {
    s = s.replace(`___INLINE_CODE_${index}___`, block);
  });

  // CRITICAL: Restore code blocks at the end
  codeBlocks.forEach((block, index) => {
    s = s.replace(`___CODE_BLOCK_${index}___`, block);
  });

  return _useSvgEmoji() ? svgifyEmoji(s, opts) : s;
}

/**
 * Reduce excessive whitespace outside of code blocks
 */
export function squashOutsideCode(s) {
  if (!s) return "";
  const parts = String(s).split(/```/);
  for (let i = 0; i < parts.length; i += 2) {
    parts[i] = parts[i]
      .replace(/\r\n/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n');
  }
  return parts.join('```');
}

/**
 * Render content that may be text or array of content blocks
 */
export function renderContent(content) {
  if (Array.isArray(content)) {
    const texts = [];
    for (const blk of content) {
      if (blk.type === 'text') texts.push(blk.text);
      else if (blk.type === 'image_url') texts.push('[image]');
    }
    return texts.join('\n');
  }
  return content;
}

/**
 * Initialize any unprocessed Mermaid diagrams in a container (or whole document)
 */
export function renderMermaid(container) {
  if (!window.mermaid) return;
  initMermaid();
  const target = container || document;
  const pending = target.querySelectorAll('pre.mermaid:not([data-processed])');
  if (pending.length === 0) return;
  try {
    window.mermaid.run({ nodes: pending });
  } catch (e) {
    console.warn('Mermaid render error:', e);
  }
}

/**
 * Resolve `![[name]]` gallery embeds (emitted as `.md-gallery-pending` chips by
 * mdToHtml) against the gallery BY NAME. For each pending chip under `root`,
 * search the gallery; on a match swap the chip for an <img>, on no match mark it
 * `.md-gallery-missing` so it reads as "no image: name" rather than vanishing.
 * Best-effort + idempotent (only touches `.md-gallery-pending`); each distinct
 * name is fetched once per call. Front-end only — call after the HTML is in DOM.
 */
export async function resolveGalleryEmbeds(root) {
  const scope = root || document;
  if (!scope.querySelectorAll) return;
  const pend = scope.querySelectorAll('.md-gallery-pending[data-gallery-name]');
  if (!pend.length) return;
  const cache = new Map();
  const lookup = (name) => {
    if (!cache.has(name)) {
      cache.set(name, fetch(`/api/gallery/library?search=${encodeURIComponent(name)}&limit=1`, { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : null)
        .then(d => (d && Array.isArray(d.items) && d.items[0]) ? d.items[0] : null)
        .catch(() => null));
    }
    return cache.get(name);
  };
  await Promise.all(Array.from(pend).map(async (el) => {
    el.classList.remove('md-gallery-pending');
    const name = el.getAttribute('data-gallery-name') || '';
    const hit = name ? await lookup(name) : null;
    let url = hit && (hit.url || (hit.filename ? '/api/generated-image/' + hit.filename : ''));
    if (url && !/^\/(api|static)\//.test(url)) url = '';  // same-origin api/static paths only
    if (url) {
      const img = document.createElement('img');
      img.className = 'md-img';
      img.src = url;
      img.alt = name;
      img.loading = 'lazy';
      el.replaceWith(img);
    } else {
      el.classList.add('md-gallery-missing');
      el.title = `No gallery image matches "${name}"`;
    }
  }));
}

const markdownModule = {
  escapeHtml,
  mdToHtml,
  squashOutsideCode,
  renderContent,
  processWithThinking,
  createCollapsible,
  hasUnclosedThinkTag,
  extractThinkingBlocks,
  normalizeThinkingMarkup,
  startsWithReasoningPrefix,
  renderMermaid,
  resolveGalleryEmbeds
};

export default markdownModule;

// Mermaid is loaded async so it cannot delay the app shell.
function initMermaid() {
  if (!window.mermaid || window.__odysseusMermaidReady) return;
  window.mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
  window.__odysseusMermaidReady = true;
}
window.odysseusInitMermaid = initMermaid;
initMermaid();

// Thinking folds are ALWAYS collapsed when (re)rendered — Claude/ChatGPT
// style. Expansion is a transient per-render peek, never persisted: the old
// content-hash localStorage re-expander made every previously-peeked thought
// come back open on each reload, which read as "thinking prints expanded".
try { localStorage.removeItem('odysseus-thinking-expanded'); } catch {}
function _setThinkingExpanded(content, toggle, header, expanded) {
  if (!content || !toggle) return;
  content.classList.toggle('expanded', expanded);
  toggle.classList.toggle('expanded', expanded);
  const label_el = header?.querySelector('.thinking-header-left span');
  if (label_el) {
    const label = label_el.dataset.label || 'thinking process';
    label_el.textContent = expanded ? `Hide ${label}` : `View ${label}`;
  }
}

// Delegated click handler for thinking toggle (CSP-safe, no inline onclick)
document.addEventListener('click', function(e) {
  const quiz = e.target.closest('.quiz-reveal');
  if (quiz) {
    const revealed = quiz.classList.toggle('revealed');
    quiz.setAttribute('aria-expanded', revealed ? 'true' : 'false');
    quiz.setAttribute('aria-label', revealed ? (quiz.dataset.hideLabel || 'Hide hidden answer') : (quiz.dataset.revealLabel || 'Reveal hidden answer'));
    return;
  }
  const card = e.target.closest('.quiz-flashcard');
  if (card) {
    const revealed = card.classList.toggle('revealed');
    const front = card.querySelector('span');
    if (front) {
      if (!card.dataset.front) card.dataset.front = front.textContent || '';
      front.textContent = revealed ? (card.dataset.back || '') : (card.dataset.front || '');
    }
    card.setAttribute('aria-label', revealed ? 'Hide flashcard answer' : 'Reveal flashcard answer');
    return;
  }
  const header = e.target.closest('.thinking-header[data-thinking-id]');
  if (!header) return;
  const id = header.dataset.thinkingId;
  const content = document.getElementById(id);
  const toggle = document.getElementById(id + '-toggle');
  if (!content || !toggle) return;

  const willExpand = !content.classList.contains('expanded');
  _setThinkingExpanded(content, toggle, header, willExpand);
});

function _endpointNameFromUrl(url) {
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.host || parsed.hostname || 'Model endpoint';
  } catch (_) {
    return 'Model endpoint';
  }
}

function _appendEndpointAddButtons(root) {
  if (!root || !root.querySelectorAll) return;
  const anchors = root.matches?.('a[href]')
    ? [root]
    : [...root.querySelectorAll('a[href]')];
  for (const anchor of anchors) {
    if (anchor.dataset.endpointAddChecked === '1') continue;
    anchor.dataset.endpointAddChecked = '1';
    const href = anchor.getAttribute('href') || '';
    if (!_isModelEndpointUrl(href)) continue;
    if (anchor.nextElementSibling?.classList?.contains('model-endpoint-add-btn')) continue;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'model-endpoint-add-btn';
    btn.dataset.endpointUrl = new URL(href, window.location.origin).href.replace(/\/+$/, '');
    btn.title = 'Add this OpenAI-compatible endpoint to the model picker';
    btn.innerHTML = '<span aria-hidden="true">+</span><span>Add to model picker</span>';
    anchor.insertAdjacentElement('afterend', btn);
  }
}

async function _registerEndpointFromButton(btn) {
  const baseUrl = String(btn?.dataset?.endpointUrl || '').trim();
  if (!baseUrl || !_isModelEndpointUrl(baseUrl)) return;
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span aria-hidden="true">...</span><span>Adding</span>';
  try {
    const existingRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    if (existingRes.ok) {
      const endpoints = await existingRes.json();
      const existing = Array.isArray(endpoints)
        ? endpoints.find((ep) => String(ep.base_url || '').replace(/\/+$/, '') === baseUrl)
        : null;
      if (existing) {
        btn.classList.add('added');
        btn.innerHTML = '<span aria-hidden="true">✓</span><span>Already added</span>';
        window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated', { detail: { baseUrl } }));
        if (window.modelsModule?.refreshModels) window.modelsModule.refreshModels(true);
        if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
        uiModule.showToast?.(`Already in model picker: ${existing.name || _endpointNameFromUrl(baseUrl)}`);
        return;
      }
    }

    const parsed = new URL(baseUrl, window.location.origin);
    const fd = new FormData();
    fd.append('base_url', baseUrl);
    fd.append('name', _endpointNameFromUrl(baseUrl));
    fd.append('model_type', 'llm');
    fd.append('endpoint_kind', 'auto');
    fd.append('skip_probe', 'true');
    if (/^(localhost|127\.0\.0\.1|0\.0\.0\.0)$/i.test(parsed.hostname)) {
      fd.append('container_local', 'true');
    }
    const res = await fetch('/api/model-endpoints', {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? ': ' + body.slice(0, 160) : ''}`);
    }
    btn.classList.add('added');
    btn.innerHTML = '<span aria-hidden="true">✓</span><span>Added</span>';
    window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated', { detail: { baseUrl } }));
    if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
    if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
    uiModule.showToast?.(`Model endpoint added: ${_endpointNameFromUrl(baseUrl)}`);
  } catch (err) {
    btn.disabled = false;
    btn.innerHTML = original;
    uiModule.showError?.(`Add endpoint failed: ${err.message || err}`);
  }
}

(function _watchModelEndpointLinks() {
  if (window._modelEndpointLinkWatcherWired) return;
  window._modelEndpointLinkWatcherWired = true;

  document.addEventListener('click', (e) => {
    const btn = e.target.closest?.('.model-endpoint-add-btn');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    _registerEndpointFromButton(btn);
  });

  const start = () => {
    const root = document.body;
    if (!root) return;
    _appendEndpointAddButtons(root);
    new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1) _appendEndpointAddButtons(node);
        }
      }
    }).observe(root, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
