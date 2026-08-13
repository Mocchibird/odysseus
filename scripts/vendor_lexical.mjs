#!/usr/bin/env node
/**
 * Vendor Lexical's production ESM builds into static/vendor/lexical/.
 *
 * WHY THIS EXISTS
 * Odysseus ships native ES modules with no bundler and no build step, and the
 * fork wants to keep it that way. Lexical publishes real .mjs builds, but its
 * leaf modules import BARE specifiers ("lexical", "@lexical/utils", ...) which a
 * browser cannot resolve on its own. The usual fixes both cost something we do
 * not want to pay:
 *
 *   - an <script type="importmap"> in index.html  -> an upstream file to merge
 *   - a bundler                                    -> a build step
 *
 * So instead we copy each package's *.prod.mjs to one flat directory and rewrite
 * every bare specifier to a relative sibling path. The result loads directly in
 * the browser, needs no import map, and touches no upstream file.
 *
 * The vendored output is committed. Re-run this only to change the Lexical
 * version, then re-run the fork's UI verification.
 *
 *   node scripts/vendor_lexical.mjs            # default LEXICAL_VERSION below
 *   LEXICAL_VERSION=0.50.0 node scripts/vendor_lexical.mjs
 */

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, readdirSync, rmSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const LEXICAL_VERSION = process.env.LEXICAL_VERSION || '0.49.0';

// The packages the writer surface actually imports. Transitive @lexical/*
// dependencies are discovered automatically, so this list only needs the direct
// ones. @lexical/react is deliberately absent: it is the React binding and the
// only part of Lexical that needs a framework.
const DIRECT = [
  'lexical',
  '@lexical/rich-text',
  '@lexical/list',
  '@lexical/code',
  '@lexical/link',
  '@lexical/table',
  '@lexical/markdown',
  '@lexical/utils',
  '@lexical/selection',
  '@lexical/html',
  '@lexical/clipboard',
  '@lexical/dragon',
];

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = join(REPO, 'static', 'vendor', 'lexical');

const log = (...a) => console.log(' ', ...a);

// ── 1. install into a throwaway dir ─────────────────────────────────────────
const work = mkdtempSync(join(tmpdir(), 'lexvendor-'));
writeFileSync(join(work, 'package.json'), JSON.stringify({ name: 'lexvendor', private: true, version: '0.0.0' }));
log(`installing lexical@${LEXICAL_VERSION} into ${work}`);
execFileSync('npm', ['install', '--silent', '--no-fund', '--no-audit',
  ...DIRECT.map((p) => `${p}@${LEXICAL_VERSION}`)], { cwd: work, stdio: 'inherit' });

// ── 2. map every installed @lexical package to its production ESM build ─────
const modules = join(work, 'node_modules');
const pkgDirs = ['lexical', ...readdirSync(join(modules, '@lexical')).map((d) => `@lexical/${d}`)];

/** package name -> { file: absolute source path, flat: flat output filename } */
const entries = new Map();
for (const name of pkgDirs) {
  const manifest = JSON.parse(readFileSync(join(modules, name, 'package.json'), 'utf8'));
  const imp = manifest.exports?.['.']?.import;
  const rel = imp && (imp.production || imp.default);
  if (!rel) {
    // @lexical/internal only exposes subpaths and is imported by dev builds only.
    log(`skip ${name} (no "." ESM export)`);
    continue;
  }
  const file = join(modules, name, rel);
  entries.set(name, { file, flat: basename(rel) });
}

// ── 3. copy, rewriting bare specifiers to relative siblings ─────────────────
if (existsSync(OUT)) rmSync(OUT, { recursive: true });
mkdirSync(OUT, { recursive: true });

// Longest name first so "@lexical/code-core" is not clobbered by "@lexical/code".
const names = [...entries.keys()].sort((a, b) => b.length - a.length);
let rewritten = 0;

for (const [name, { file, flat }] of entries) {
  let src = readFileSync(file, 'utf8');
  for (const dep of names) {
    if (dep === name) continue;
    // Minified builds emit  from"pkg"  as well as  from 'pkg'.
    const pattern = new RegExp(`(from\\s*|import\\s*\\(\\s*)(["'])${dep.replace('/', '\\/')}\\2`, 'g');
    src = src.replace(pattern, (_m, kw, q) => {
      rewritten += 1;
      return `${kw}${q}./${entries.get(dep).flat}${q}`;
    });
  }
  writeFileSync(join(OUT, flat), src);
}
log(`wrote ${entries.size} modules, rewrote ${rewritten} specifiers -> static/vendor/lexical/`);

// ── 4. refuse to ship anything a browser cannot resolve ─────────────────────
const BARE = /(?:from\s*|import\s*\(\s*)["'](?!\.{1,2}\/)([^"']+)["']/g;
const bad = [];
for (const f of readdirSync(OUT)) {
  const src = readFileSync(join(OUT, f), 'utf8');
  for (const m of src.matchAll(BARE)) bad.push(`${f}: ${m[1]}`);
}
if (bad.length) {
  console.error('\n  UNRESOLVED bare specifiers — a browser cannot load these:');
  for (const b of bad) console.error(`    ${b}`);
  console.error('  Add the missing package to DIRECT, or extend the rewrite.');
  process.exit(1);
}
log('verified: no bare specifiers remain');

// ── 5. record what was vendored, for the next person ───────────────────────
writeFileSync(join(OUT, 'VENDORED.md'),
  `# Vendored Lexical ${LEXICAL_VERSION}\n\n` +
  `Generated by \`scripts/vendor_lexical.mjs\` — do not hand-edit.\n\n` +
  `Production ESM builds with bare specifiers rewritten to relative siblings, so\n` +
  `they load natively in the browser with no import map and no bundler.\n\n` +
  `| module | file |\n|---|---|\n` +
  [...entries].map(([n, v]) => `| \`${n}\` | \`${v.flat}\` |`).join('\n') + '\n\n' +
  `\`@lexical/react\` is intentionally NOT vendored: it is the React binding.\n`);

rmSync(work, { recursive: true, force: true });
log('done');
