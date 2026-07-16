"""Smoke test: every app-owned JS file survives esbuild's minify+esm transform.

The Docker image build minifies all static JS (scripts/minify_assets.mjs). That
step only runs at build time and dev serves the un-minified source, so an
esbuild-incompatible edit (top-level await outside esm, syntax esbuild rejects)
first shows up in the production image. This runs the SAME transform read-only
(no writes) so the failure surfaces in CI / the build env instead.

Skips where node+esbuild aren't installed (e.g. the bare app container); runs in
the Docker build and any CI step that `npm install`s esbuild.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Mirrors scripts/minify_assets.mjs (walkJs + the transform opts) but never
# writes — it only asserts each file transforms without throwing.
_CHECK_JS = r"""
import * as esbuild from 'esbuild';
import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';
function walk(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { if (e.name === 'lib') continue; walk(p, out); }
    else if (e.name.endsWith('.js') && !e.name.endsWith('.min.js')) out.push(p);
  }
  return out;
}
const files = [...walk('static/js'), 'static/app.js'];
let failed = 0;
for (const f of files) {
  const src = readFileSync(f, 'utf8');
  try {
    await esbuild.transform(src, { loader: 'js', minify: true, legalComments: 'none', format: 'esm' });
  } catch (e) {
    console.error('MINIFY FAIL ' + f + ': ' + e.message);
    failed++;
  }
}
if (failed) process.exit(1);
console.log('minify-ok ' + files.length);
"""


def _node():
    return shutil.which("node")


def _esbuild_available() -> bool:
    node = _node()
    if not node:
        return False
    r = subprocess.run([node, "-e", "require.resolve('esbuild')"], cwd=str(ROOT), capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(
    not _esbuild_available(),
    reason="node+esbuild not installed here; this smoke runs in the Docker build / a CI step that installs esbuild",
)
def test_all_app_js_minifies_with_esbuild():
    node = _node()
    r = subprocess.run(
        [node, "--input-type=module", "-e", _CHECK_JS],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert r.returncode == 0, f"esbuild minify smoke failed:\n{r.stdout}\n{r.stderr}"
