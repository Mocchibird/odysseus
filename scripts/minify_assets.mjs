// Build-time minifier for the app's own JS/CSS. Run from the repo root during
// the Docker image build (see Dockerfile) — NOT a dev requirement; the source
// on disk stays un-minified so it's still readable/debuggable.
//
// Uses esbuild's transform API per file (NOT bundling), so every module's
// import/export graph and specifier strings (including the `?v=` cache-busting
// queries) are preserved exactly — only whitespace/local identifiers shrink.
// static/lib is skipped: those are vendored, already-minified third-party
// bundles (pdf.worker, three, xlsx, html2pdf…).
import * as esbuild from 'esbuild';
import { readFileSync, writeFileSync, readdirSync } from 'fs';
import { join } from 'path';

function walkJs(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'lib') continue;            // vendored, already minified
      walkJs(p, out);
    } else if (entry.name.endsWith('.js') && !entry.name.endsWith('.min.js')) {
      out.push(p);
    }
  }
  return out;
}

const KB = (n) => (n / 1024).toFixed(0) + 'KB';

const jsFiles = [...walkJs('static/js'), 'static/app.js'];
let jsBefore = 0, jsAfter = 0;
for (const f of jsFiles) {
  const src = readFileSync(f, 'utf8');
  let out;
  try {
    // format:'esm' is required so esbuild allows top-level `await` (some
    // modules use it); these files are all loaded as ES modules anyway.
    out = await esbuild.transform(src, { loader: 'js', minify: true, legalComments: 'none', format: 'esm' });
  } catch (e) {
    throw new Error(`minify failed for ${f}: ${e.message}`);
  }
  writeFileSync(f, out.code);
  jsBefore += src.length;
  jsAfter += out.code.length;
}

let cssBefore = 0, cssAfter = 0;
for (const c of ['static/style.css', 'static/fork.css']) {
  const src = readFileSync(c, 'utf8');
  const out = await esbuild.transform(src, { loader: 'css', minify: true });
  writeFileSync(c, out.code);
  cssBefore += src.length;
  cssAfter += out.code.length;
}

console.log(
  `minified ${jsFiles.length} JS files: ${KB(jsBefore)} -> ${KB(jsAfter)}; ` +
  `CSS: ${KB(cssBefore)} -> ${KB(cssAfter)}`,
);
