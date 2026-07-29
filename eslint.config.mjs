// ESLint (flat config) for the SPA's own JavaScript.
//
// Philosophy mirrors .stylelintrc.json and pyproject's Ruff setup: HIGH-SIGNAL
// REAL-BUG CHECKS ONLY, never stylistic reformatting — the codebase has its own
// conventions and this must not fight them. Rules are listed explicitly rather
// than extending eslint:recommended so nothing stylistic sneaks in on upgrade.
//
// The rule this config exists for is `no-undef`. An identifier with no binding
// is a guaranteed ReferenceError the moment its code path runs, it is invisible
// to `node --check` (the syntax job in CI), and it has shipped repeatedly:
//   - sessions.js used markdownModule without importing it     -> every past
//     chat opened BLANK
//   - chat.js called _setStoredPlan(), deleted with the plan window -> agent
//     replies truncated whenever the model ticked a plan step
//   - chat.js read try-scoped `streamingTTS`/`_isAgent` from its `catch`
//     -> the whole stream error/abort UI was dead (no interrupted marker,
//        no Continue button, no error text)
//   - modalManager.js passed an unbound `modal` -> dock-chip dragging dead
//   - emailLibrary.js used bare uiModule -> summarize/translate error toasts
//     silently never appeared
// Every one of those is a one-line fix that a linter catches for free.
import globals from 'globals';

export default [
  {
    files: ['static/app.js', 'static/js/**/*.js'],
    // The codebase carries eslint-disable comments for rules this config does not
    // enable (no-console, no-unused-expressions). They document author intent, so
    // don't nag about them being "unused" — that would be pure noise in CI.
    linterOptions: { reportUnusedDisableDirectives: 'off' },
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        // Vendored libraries loaded by <script> tags in index.html and used as
        // bare globals in places. Not app modules — real externals.
        hljs: 'readonly',
        katex: 'readonly',
        mermaid: 'readonly',
        XLSX: 'readonly',
        mammoth: 'readonly',
        pdfjsLib: 'readonly',
        Chart: 'readonly',
      },
    },
    rules: {
      'no-undef': 'error',
      // Other unambiguous-bug rules (no style opinions in this list).
      'no-dupe-keys': 'error',
      'no-dupe-args': 'error',
      'no-dupe-class-members': 'error',
      'no-unreachable': 'error',
      'no-func-assign': 'error',
      'no-obj-calls': 'error',
      'no-sparse-arrays': 'error',
      'use-isnan': 'error',
      'valid-typeof': 'error',
      'no-unsafe-negation': 'error',
      // NOT enabled: no-self-assign. `el.value = el.value` is deliberate here —
      // it re-fires the in-house colour picker's setter so the swatch paints its
      // initial value (see galleryEditor.js and editor/wire-inpaint-controls.js).
      // Flagging it would be the linter fighting a real convention.
    },
  },
];
