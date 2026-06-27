# Fork additive policy — keep upstream merges cheap

This is a **fork** of `pewdiepie-archdaemon/odysseus`. Every line of an upstream
file we edit in place is a line that can conflict on the next `git merge
upstream/dev`. The goal of this policy is simple:

> **Add, don't edit.** Keep upstream files byte-identical wherever possible;
> put the fork's behaviour in *new* files and *merge it in* through a single,
> stable seam.

A fork can never be 100% additive (some changes are genuinely in-place), but the
discipline below keeps the conflict surface from growing and shrinks the worst
offenders over time.

---

## The conflict surface (snapshot, 2026-06-27, vs `upstream/dev` @ ebead80)

| | files | meaning |
|---|---|---|
| **Added** (fork-only) | 86 | never conflict — the additive ideal |
| **Modified** (in place) | 186 | the entire conflict surface |
| **Deleted** | 2 | delete-vs-modify conflict risk |

Of the 154 *runtime-source* modified files, classified by an audit:

| category | count | verdict |
|---|---|---|
| TRIVIAL | 47 | leave (mostly ruff unused-import trims) |
| SURGICAL | 49 | mostly keep-HEAD; a few wrappable |
| APPENDED | 30 | **relocatable to fork modules** |
| FORK_OWNED | 16 | fork owns the file — keep HEAD, don't convert |
| CONFIG | 12 | **move to settings/env** |

The full per-file backlog is at the bottom of this doc.

---

## The patterns (in priority order)

### 1. New feature → new file
A new capability is a new `routes/<x>_routes.py` + `src/<x>_*.py` + (frontend)
`static/js/<x>.js`. Register it through the one stable seam:
- backend route: `app.include_router(setup_<x>_routes(...))` in `app.py`
- frontend module: an `import './js/<x>.js'` in `static/app.js`

This is already how the fork ships books/health/pings/today/file routes and the
86 added files. Keep doing it.

### 2. New UI on an existing screen → inject at runtime, don't edit the template
`static/js/fork-ui.js` mounts fork-only markup into **stable upstream DOM
anchors** (idempotent, degrades silently if the anchor is gone). Fork styles go
in `static/fork.css` (loaded after `style.css`). This keeps `index.html` and
`style.css` aligned with upstream. `tests/test_fork_ui_addons.py` flags a
missing anchor at merge time.

### 3. Adding to an upstream data literal → a sibling `*_fork.py` merged at import
This is the highest-leverage pattern for the recurring registry/settings
conflicts. When the fork needs to add keys/tags/schemas/routes to an upstream
**dict / set / list literal**, do **not** edit the literal (both sides edit it →
conflict every merge). Instead define the additions in a sibling fork module and
fold them in with one appended block, leaving the upstream literal byte-identical:

```python
# src/settings.py  (upstream literal stays byte-identical)
DEFAULT_SETTINGS = { ...upstream... }
_PER_USER_KEYS = { ...upstream... }

# ── Fork settings additions ──  (the only fork touch: one appended block)
from src.settings_fork import FORK_DEFAULT_SETTINGS, FORK_PER_USER_KEYS
DEFAULT_SETTINGS.update(FORK_DEFAULT_SETTINGS)
_PER_USER_KEYS |= FORK_PER_USER_KEYS
```

Rules of thumb:
- `dict` → `.update(FORK_X)`  •  `set` → `|= FORK_X`  •  `frozenset` →
  `X = X | FORK_X` (reassign; do it before anything consumes it — end of module
  is safe since consumers read at call-time)  •  `list` → `.extend(FORK_X)`  •
  class attr dict → `Cls._X = {**Cls._X, **FORK_X}` after the class.
- The sibling module must **not** import its upstream parent (circular).
- To reword an upstream entry, ship the new value in a `FORK_*_OVERRIDES` dict
  and `.update()` it in — don't edit the upstream string.
- **Tests:** assert on the *assembled runtime value* (`'key' in DEFAULT_SETTINGS`),
  not on file source — that survives relocation and is a stronger guard.

### 4. Behaviour change to an upstream function
- If it's a changed **default/constant/flag** → move it to a setting or `.env`
  (CONFIG), don't edit the literal.
- If it's genuinely interleaved logic → a thin wrapper/subclass is usually more
  fragile than the conflict. **Keep it inline, resolve toward HEAD on merge,**
  and document the canonical resolution in [upstream-merge-workflow memory].
  Only build a runtime patch layer (`src/fork_patches.py`, monkeypatch/wrap at
  startup) when the same surgical edit re-conflicts every single merge.

### 5. Don't delete upstream code you simply don't use
Deleting upstream helpers/imports creates delete-vs-modify conflicts for zero
gain. Leave them; carry the dead weight — it costs nothing and merges clean.

### 6. Adopt upstream refactors
When upstream splits/moves something (e.g. the `src/tools/` package split,
merge #13), **adopt it** and re-home the fork's additions onto the new shape.
Each adoption permanently deletes a conflict source.

---

## Done (2026-06-27)

All verified behaviour-identical (assembled structures byte-equal to before;
full pytest green). Upstream literals are now byte-identical to upstream:

| file | before | after | fork module |
|---|---|---|---|
| `src/settings.py` | 60/2 (4 hunks in churned dicts) | 16/2 (1 append) | `src/settings_fork.py` |
| `src/tool_index.py` | 81/5 | 20/0 (1 EOF append) | `src/tool_index_fork.py` |
| `src/tool_schemas.py` | 158/5 | 14/5 | `src/tool_schemas_fork.py` |
| `src/agent_tools/__init__.py` | 12/2 | 10/0 | `src/agent_tools/_fork.py` |
| `core/platform_compat.py` | 0/23 (deletion) | 0/1 (restored upstream helpers; kept only the unused-import trim) | — |
| `src/chat_processor.py` | 32/0 | 6/0 | `src/fork_chat_prompt.py` |
| `app.py` | ~200/18 | 96/18 | `src/gallery_thumbs.py` |

This eliminated the recurring "both sides add a settings key / tool tag / tool
schema / tool description / always-available entry" conflict cluster, and moved
~870 lines of fork code out of upstream files into sibling fork modules.

### Lesson: route files are NOT mechanical relocations
Relocating route handlers (`document_routes.py`, `calendar_routes.py`) was
attempted and **reverted** — they are entangled in ways pure helper moves are not:
- Their **test harnesses inject a test DB by monkeypatching the module-global
  `SessionLocal`** (e.g. `document_routes.SessionLocal = _TS`, with ~11 restore
  sites). Move a handler to another module and it reads *that* module's
  `SessionLocal`, so the injection misses it → the endpoint hits the real DB.
- They **re-export symbols** other modules/tests import from them (e.g.
  `set_user_tz_offset` actually lives in `src/user_time.py` but is imported
  `from routes.calendar_routes`), and tests patch internal helpers on the module
  (`_validate_ics_feed_url`, `_ics_block_private_ips`).

So a route relocation must ALSO update the test harness (patch the fork module's
`SessionLocal`; restore it) and re-export the symbols other code imports — a
larger, riskier change than "move the functions." Do it deliberately, not as a
batch mechanical pass.

---

## Backlog — remaining high-value conversions (by pattern)

Prioritise APPENDED + CONFIG in churned areas; they are mechanical and
behaviour-safe. Skip FORK_OWNED.

**APPENDED → relocate to a fork module + one merge/registration seam**
- `core/database.py` (492/6) → new `_migrate_*` fns + net-new model classes → `core/fork_models.py` / `core/fork_migrations.py` (keep new *columns* on upstream models inline; **bump SCHEMA_VERSION** on any added migration — see [migration-schema-version-gate]). ORM registration order is delicate — do carefully.
- `routes/document_routes.py` (478/14) → wiki-link/related/RAG helpers + 4 routes → `routes/_fork_document_extras.py`. ⚠️ needs the test-harness update (see "route files are NOT mechanical" above): `test_document_obsidian_extras.py` patches `document_routes.SessionLocal`, so relocated endpoints must read that binding or the harness must patch the fork module too.
- `routes/calendar_routes.py` (371/125) → ICS-feed helpers + `/today` → `src/ics_feeds.py` + small fork router. ⚠️ re-export `set_user_tz_offset` and keep `_validate_ics_feed_url`/`_ics_block_private_ips` patchable from `calendar_routes` (tests reference them there), and update `test_ics_links_are_persistent_subscriptions_in_source` (reads source text).
- `static/js/presets.js` → Iris persona roster → `static/js/fork/personas.js`; persona machinery → fork module
- `static/js/document.js` (534/82) → wiki-link AC / split-view / related-notes / list-continue → `static/js/docMarkdownExtras.js`
- `static/js/sessions.js` → history-virtualization layer → `static/js/sessions-history-virtual.js`
- `routes/chat_routes.py` → `_link_meal_photo`/`_link_training_photo` → `src/photo_linking.py`
- `routes/email_routes.py` + `routes/email_helpers.py` → proton-bridge presets/helpers (`_proton_bridge_preset`, `_tcp_status`, `_open_smtp_connection`, …) → fork module. NOTE: email is untestable locally (Proton Bridge) — verify after deploy.
- `static/style.css` (710/83) → fork-only rule blocks (`#health/#habits/#books/#pings/#today`, `#rail-pings`, doclib scrollbars, gallery cascade) → `static/fork.css` (already loaded after style.css); leave in-place upstream-selector tweaks (safe-area-inset, etc.)
- `src/tool_execution.py` → migrate the 6 fork tool dispatch `elif` arms onto upstream's `TOOL_HANDLERS` registry (upstream #3629/#4445); relocate `app_store_write_guard`
- `static/js/escMenuStack.js` → `topPopupZ` → `static/js/fork/zorder.js`, re-point importers

**CONFIG → settings/env/build**
- default chat mode `'chat'→'agent'` (scattered in `static/app.js`, `chat.js`, `compare/*`) → a single shared `DEFAULT_MODE` const
- `docker-compose.yml` PUID/PGID `1000→568` → `.env`
- `routes/shell_routes.py` `rembg[gpu]→rembg[cpu]` → an env default (see [cookbook-onnxruntime-cascade])
- `static/sw.js` PRECACHE array → a generated `precache-list.js` emitted by the minify build

**SURGICAL wraps (higher effort/risk — do only if they keep re-conflicting)**
- `core/middleware.py` CSP (HIGH churn, security) → subclass `SecurityHeadersMiddleware`, post-process the default CSP + add the books branch
- `src/ai_interaction.py` `_resolve_model` exact-over-partial match → fork resolver override

**FORK_OWNED — keep HEAD, do not convert (16):** `docker-compose.gpu-*.yml`
(generated — regenerate from base+overlay), `routes/note_routes.py`,
`src/builtin_actions.py`, `src/preset_manager.py`, `src/request_models.py`,
`static/index.html`, `static/js/{gallery,markdown,notes,settings,theme,tts-ai,voiceRecorder}.js`,
`services/{stt,tts}/*_service.py`.

[upstream-merge-workflow memory]: ../.. (see Claude memory: upstream-merge-workflow)
[migration-schema-version-gate]: (see Claude memory: migration-schema-version-gate)
[cookbook-onnxruntime-cascade]: (see Claude memory: cookbook-onnxruntime-cascade)
