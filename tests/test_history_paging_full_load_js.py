"""Paged chat history must be fully loaded before index-sensitive edits.

Upstream renders only the newest history page and pages older turns in on
scroll. chat.js's edit / resend / regenerate / fork / delete still map a
bubble's DOM position to a server history index (the truncate keep_count), so
with older pages unloaded that index is short by the unloaded count and the
truncate drops real history. ``sessions.ensureFullHistoryRendered`` (fork) pulls
every remaining page through upstream's pager first, and throws - aborting the
caller - when it cannot, so a failed fetch never turns into a wrong truncate.

Drives the real functions from static/js/sessions.js in Node against a fake
#chat-history box and a fake paged /api/history.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

function extract(name) {
  const re = new RegExp('(?:export\\s+)?(?:async\\s+)?function\\s+' + name + '\\s*\\(');
  const m = re.exec(src);
  if (!m) throw new Error('function not found: ' + name);
  let i = src.indexOf('{', m.index), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(m.index, j + 1).replace(/^export\s+/, '');
  }
  throw new Error('unbalanced: ' + name);
}

async function run(scenario) {
  const total = 72, pageSize = 24;
  const fetched = [];
  const inserted = [];
  const box = {
    scrollTop: 500, scrollHeight: 1000,
    querySelector: () => null,
    insertBefore: (el) => { inserted.push(el); },
    addEventListener: () => {}, removeEventListener: () => {},
    get firstChild() { return null; },
  };
  const sandbox = {
    document: { getElementById: (id) => (id === 'chat-history' ? box : null) },
    window: {},
    currentSessionId: 's1',
    _historyPager: null,
    _historyPageLimit: () => pageSize,
    _historyUrl: (id, { limit, offset }) => ({ id, limit, offset }),
    _renderHistoryMessage: (msg) => [{ msg }],
    console: { warn: () => {} },
    fetch: async (q) => {
      fetched.push(q.offset);
      if (scenario === 'fail' && q.offset === 0) throw new Error('network down');
      const history = Array.from({ length: q.limit }, (_, k) => ({ role: k % 2 ? 'assistant' : 'user', content: 'm' + (q.offset + k) }));
      return { json: async () => ({ history, offset: q.offset, has_more_before: q.offset > 0 }) };
    },
    setTimeout,
  };
  const names = ['_clearHistoryPager', '_installHistoryPager', 'ensureFullHistoryRendered'];
  const body = names.map(extract).join('\n') +
    '\nreturn { _installHistoryPager, ensureFullHistoryRendered, pager: () => _historyPager };';
  const keys = Object.keys(sandbox).filter(k => k !== '_historyPager');
  const fns = new Function(...keys, 'let _historyPager = null;\n' + body)(...keys.map(k => sandbox[k]));

  // No pager (whole history fit in one page) -> no-op.
  await fns.ensureFullHistoryRendered();

  fns._installHistoryPager('s1', { offset: total - pageSize, limit: pageSize, has_more_before: true }, 'm');
  let threw = null;
  try { await fns.ensureFullHistoryRendered(); } catch (e) { threw = String(e.message || e); }
  const p = fns.pager();
  return { fetched, inserted: inserted.length, done: !!(p && p.done), offset: p && p.offset, threw };
}

run(process.argv[3]).then(r => console.log(JSON.stringify(r))).catch(e => { console.error(e); process.exit(1); });
"""


def _run(scenario, tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(ROOT / "static" / "js" / "sessions.js"), scenario],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not NODE, reason="node not available")
def test_every_older_page_is_loaded_before_an_index_sensitive_edit(tmp_path):
    r = _run("ok", tmp_path)
    assert r["threw"] is None
    assert r["fetched"] == [24, 0]          # walks back page by page to offset 0
    assert r["inserted"] == 48               # the two unloaded pages are now in the DOM
    assert r["done"] is True and r["offset"] == 0


@pytest.mark.skipif(not NODE, reason="node not available")
def test_a_failed_page_fetch_aborts_instead_of_truncating_wrong_rows(tmp_path):
    r = _run("fail", tmp_path)
    assert r["threw"] and "full chat history" in r["threw"]
    assert r["done"] is False


def test_chat_awaits_full_history_before_every_index_sensitive_operation():
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    sessions = (ROOT / "static" / "js" / "sessions.js").read_text(encoding="utf-8")
    assert "export async function ensureFullHistoryRendered" in sessions
    assert "  ensureFullHistoryRendered,\n" in sessions   # on the default-export object chat.js uses
    for fn in ("editUserMessage", "regenerateFrom", "forkFrom"):
        start = chat.index(f"export async function {fn}(")
        head = chat[start:start + 600]
        assert "ensureFullHistoryRendered()" in head, fn
