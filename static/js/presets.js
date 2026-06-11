// static/js/presets.js

/**
 * Preset management
 */

let API_BASE = '';
let selectedPreset = null;
let presets = {};

export function loadStoredArray(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value) ? value : [];
  } catch (e) {
    return [];
  }
}

export function loadStoredObject(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch (e) {
    return {};
  }
}

// Built-in prompt templates (moved from cot_prompts.py)
export const PROMPT_TEMPLATES = [
  {
    id: 'iris',
    name: 'Iris',
    temperature: 0.9,
    isPreset: true,
    isCharacter: true,
    prompt: "You are Iris, the user's personal assistant and the companion to their Odysseus workspace. Ground what you say and do in their real systems — their files, books, notes and documents, and your persistent memory — and treat that data with care. Be warm, direct, and practical. Help the user think clearly, keep their notes and files human-browsable, and prefer durable, well-organized updates over scattered fragments. When you change a note, file, or memory, be precise about what changed; when answering from their files or your memory, mention the file or source you used."
  },
  {
    // Korean variant — becomes the default persona for new chats when the
    // user's language pref (Settings → Account → Language) is Korean.
    // Mirrors src/preset_manager.py IRIS_SYSTEM_PROMPT_KO.
    id: 'iris-ko',
    name: 'Iris-Korean',
    character_name: 'Iris',
    temperature: 0.9,
    isPreset: true,
    isCharacter: true,
    prompt: "당신은 Iris, 사용자의 개인 비서이자 Odysseus 워크스페이스의 동반자입니다. 사용자의 실제 시스템 — 업로드된 파일의 지식 베이스, 노트와 문서, 그리고 당신의 영구 메모리 — 에 근거해서 말하고 행동하며, 그 데이터를 소중히 다루세요. 따뜻하고, 솔직하고, 실용적으로 응대하세요. 사용자가 명확하게 생각하도록 돕고, 노트와 지식은 사람이 읽기 좋게 유지하며, 흩어진 조각보다 오래가고 잘 정리된 업데이트를 우선하세요. 노트, 파일, 메모리를 변경할 때는 무엇이 바뀌었는지 정확히 알리고, 지식 베이스나 메모리를 근거로 답할 때는 사용한 파일이나 출처를 언급하세요. 기본적으로 한국어로 대답하고, 사용자가 다른 언어로 쓰면 그 언어를 따르세요."
  },
  {
    // German variant — the default persona for new chats when the user's
    // language pref is German. Mirrors preset_manager.IRIS_SYSTEM_PROMPT_DE.
    id: 'iris-de',
    name: 'Iris-German',
    character_name: 'Iris',
    temperature: 0.9,
    isPreset: true,
    isCharacter: true,
    prompt: "Du bist Iris, die persönliche Assistentin des Nutzers und Begleiterin seines Odysseus-Workspace. Stütze dich bei allem, was du sagst und tust, auf seine realen Systeme — die Wissensbasis seiner hochgeladenen Dateien, seine Notizen und Dokumente und dein persistentes Gedächtnis — und behandle diese Daten mit Sorgfalt. Sei warm, direkt und pragmatisch. Hilf dem Nutzer, klar zu denken, halte Notizen und Wissen gut lesbar und bevorzuge dauerhafte, gut organisierte Aktualisierungen statt verstreuter Fragmente. Wenn du eine Notiz, Datei oder Erinnerung änderst, benenne präzise, was sich geändert hat; wenn du aus der Wissensbasis oder deinem Gedächtnis antwortest, nenne die Datei oder Quelle. Antworte standardmäßig auf Deutsch; schreibt der Nutzer in einer anderen Sprache, folge dieser Sprache."
  }
];

let userTemplates = [];
let userTemplatesLoaded = false;

/**
 * Initialize with dependencies
 */
export function init(apiBase) {
  API_BASE = apiBase;
  initCharTabs();
  initEnabledToggle();
  initNameDropdown();
  initResetButton();
  initSaveAsTemplate();
  initExpandButton();
  initPersistentChat();
  loadUserTemplates();
}

function initCharTabs() {
  document.querySelectorAll('.preset-tab[data-chartab]').forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.chartab;
      document.querySelectorAll('.preset-tab[data-chartab]').forEach(t => t.classList.toggle('active', t === tab));
      document.querySelectorAll('.preset-chartab[data-chartab-panel]').forEach(p => {
        p.style.display = p.dataset.chartabPanel === target ? '' : 'none';
      });
    });
  });
}

function initExpandButton() {
  const btn = document.getElementById('char-expand-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const nameInput = document.getElementById('custom-character-name');
    const promptInput = document.getElementById('custom-system-prompt');
    const name = nameInput ? nameInput.value.trim() : '';
    const draft = promptInput ? promptInput.value.trim() : '';
    if (!name && !draft) return;

    // Get current model from picker
    const modelLabel = document.getElementById('model-picker-label');
    const currentModel = modelLabel ? modelLabel.textContent.trim() : '';

    btn.classList.add('expanding');
    const origText = btn.innerHTML;

    // Show spinner in textarea
    const wrap = promptInput.parentElement;
    let spinner = null;
    try {
      const spinnerMod = await import('./spinner.js');
      spinner = spinnerMod.default.create('Expanding', 'center', 'wave');
      const spinEl = spinner.createElement();
      spinEl.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:2;';
      wrap.appendChild(spinEl);
      spinner.start();
      promptInput.style.opacity = '0.3';
    } catch (e) {}

    btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:-1px;margin-right:2px;"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg> Expanding...';

    try {
      const res = await fetch(`${API_BASE}/api/presets/expand`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, prompt: draft, model: currentModel }),
      });
      const data = await res.json();
      if (data.success && data.prompt && promptInput) {
        promptInput.value = data.prompt;
        promptInput.style.height = 'auto';
        promptInput.style.height = promptInput.scrollHeight + 'px';
      } else if (data.message) {
        console.error('Expand error:', data.message);
      }
    } catch (e) {
      console.error('Expand failed:', e);
    }

    // Clean up spinner
    if (spinner) { spinner.destroy(); }
    promptInput.style.opacity = '';
    btn.classList.remove('expanding');
    btn.innerHTML = origText;
  });
}

/**
 * Init slider value displays
 */
function initEnabledToggle() {
  const tempSlider = document.getElementById('custom-temperature');
  const tempValue = document.getElementById('temp-value');
  const tokensSlider = document.getElementById('custom-max-tokens');
  const tokensValue = document.getElementById('tokens-value');

  if (tempSlider && tempValue) {
    tempSlider.addEventListener('input', () => {
      tempValue.textContent = parseFloat(tempSlider.value).toFixed(1);
    });
  }
  if (tokensSlider && tokensValue) {
    tokensSlider.addEventListener('input', () => {
      const v = parseInt(tokensSlider.value);
      tokensValue.textContent = v > 8192 ? 'No limit' : v.toLocaleString();
    });
  }
}

/**
 * Character select dropdown — pick saved characters or "New character..."
 */
function initNameDropdown() {
  const select = document.getElementById('char-template-select');
  const delBtn = document.getElementById('char-delete-template-btn');
  if (!select) return;

  // + New button — clear form for new character
  const newBtn = document.getElementById('char-new-btn');
  if (newBtn) {
    newBtn.addEventListener('click', () => {
      select.value = '__default__';
      select.dispatchEvent(new Event('change'));
      const nameInput = document.getElementById('custom-character-name');
      if (nameInput) { nameInput.value = ''; nameInput.focus(); }
    });
  }

  select.addEventListener('change', () => {
    const val = select.value;
    if (!val || val === '__default__') {
      // "Default" or "New character..." — reset all fields
      const nameInput = document.getElementById('custom-character-name');
      const promptInput = document.getElementById('custom-system-prompt');
      const tempInput = document.getElementById('custom-temperature');
      const tempValue = document.getElementById('temp-value');
      const tokensInput = document.getElementById('custom-max-tokens');
      const tokensValue = document.getElementById('tokens-value');
      if (nameInput) nameInput.value = '';
      if (promptInput) promptInput.value = '';
      const nameRow = document.getElementById('char-name-row');
      if (nameRow) nameRow.style.display = '';
      if (tempInput) { tempInput.value = 1.0; if (tempValue) tempValue.textContent = '1.0'; tempInput.dispatchEvent(new Event('input')); }
      if (tokensInput) { tokensInput.value = 8448; if (tokensValue) tokensValue.textContent = 'No limit'; tokensInput.dispatchEvent(new Event('input')); }
      if (delBtn) delBtn.style.display = 'none';
      return;
    }
    // Load the selected template
    const nameInput = document.getElementById('custom-character-name');
    const isSaved = userTemplates.find(t => t.name === val);
    const builtin = PROMPT_TEMPLATES.find(t => t.name === val);
    const hasName = isSaved || (builtin && builtin.isCharacter && !builtin.noName);
    if (nameInput) nameInput.value = hasName ? val : '';
    const nameRow = document.getElementById('char-name-row');
    if (nameRow) nameRow.style.display = (builtin && builtin.noName) ? 'none' : '';
    _tryLoadTemplate(val);
    const isPreset = builtin && builtin.isPreset;
    if (delBtn) delBtn.style.display = (isSaved || (builtin && !isPreset)) ? '' : 'none';
  });

  // Delete template button — confirms, then removes template + character memories
  if (delBtn) {
    delBtn.addEventListener('click', async () => {
      const charName = select.value;
      if (!charName || charName === '__default__') return;
      const match = userTemplates.find(t => t.name === charName);
      const isBuiltin = PROMPT_TEMPLATES.some(t => t.name === charName);
      if (!await window.styledConfirm(`Delete "${charName}"?\n\nThis will remove the persona and all its memories.`, { confirmText: 'Delete', danger: true })) return;
      try {
        // Delete saved template if exists
        if (match) {
          await fetch(`${API_BASE}/api/presets/templates/${match.id}`, { method: 'DELETE' });
        }
        // Hide built-in preset
        if (isBuiltin) {
          const hidden = loadStoredArray('odysseus-hidden-presets');
          if (!hidden.includes(charName)) hidden.push(charName);
          localStorage.setItem('odysseus-hidden-presets', JSON.stringify(hidden));
        }
        // Deactivate if this was the active character
        if (presets.custom && presets.custom.character_name === charName) {
          selectedPreset = null;
          presets.custom = { ...presets.custom, character_name: '', system_prompt: '', enabled: false };
          const charIndicator = document.getElementById('character-indicator-btn');
          if (charIndicator) { charIndicator.style.display = 'none'; charIndicator.classList.remove('active'); }
          const miniBtn = document.getElementById('overflow-preset-btn');
          if (miniBtn) miniBtn.classList.remove('active');
        }
        await loadUserTemplates();
        select.value = '__default__';
        select.dispatchEvent(new Event('change'));
        setTimeout(() => { _syncCharIndicator(); }, 0);
      } catch (e) { console.error('Delete character failed:', e); }
    });
  }
}

function _tryLoadTemplate(name) {
  if (!name) return;
  // Check user templates first, then built-in
  let tmpl = userTemplates.find(t => t.name === name);
  if (!tmpl) {
    const builtin = PROMPT_TEMPLATES.find(t => t.name === name);
    if (builtin) {
      // Built-in: load prompt + temperature, clear name (styles, not characters)
      const promptInput = document.getElementById('custom-system-prompt');
      const tempInput = document.getElementById('custom-temperature');
      const tempValue = document.getElementById('temp-value');
      if (promptInput) promptInput.value = builtin.prompt;
      if (tempInput && builtin.temperature != null) {
        tempInput.value = builtin.temperature;
        if (tempValue) tempValue.textContent = parseFloat(builtin.temperature).toFixed(1);
        tempInput.dispatchEvent(new Event('input'));
      }
      return;
    }
    return;
  }
  const promptInput = document.getElementById('custom-system-prompt');
  const tempInput = document.getElementById('custom-temperature');
  const tempValue = document.getElementById('temp-value');
  const tokensInput = document.getElementById('custom-max-tokens');
  const tokensValue = document.getElementById('tokens-value');
  if (promptInput) promptInput.value = tmpl.system_prompt || '';
  if (tempInput) {
    tempInput.value = tmpl.temperature ?? 1.0;
    if (tempValue) tempValue.textContent = parseFloat(tempInput.value).toFixed(1);
    tempInput.dispatchEvent(new Event('input'));
  }
  if (tokensInput) {
    const v = tmpl.max_tokens || 0;
    tokensInput.value = v === 0 ? 8448 : v;
    if (tokensValue) tokensValue.textContent = (v === 0 || v > 8192) ? 'No limit' : v.toLocaleString();
    tokensInput.dispatchEvent(new Event('input'));
  }
  const delBtn = document.getElementById('char-delete-template-btn');
  if (delBtn) delBtn.style.display = '';
}

function _populateCharSelect() {
  const select = document.getElementById('char-template-select');
  if (!select) return;
  const currentVal = select.value;
  select.innerHTML = '<option value="__default__">Default (no persona)</option>';

  const savedNames = new Set(userTemplates.map(t => t.name));
  if (userTemplates.length) {
    const group = document.createElement('optgroup');
    group.label = 'Saved';
    userTemplates.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.name;
      opt.textContent = t.name;
      group.appendChild(opt);
    });
    select.appendChild(group);
  }

  const hiddenPresets = loadStoredArray('odysseus-hidden-presets');
  const builtins = PROMPT_TEMPLATES.filter(t => !savedNames.has(t.name) && !hiddenPresets.includes(t.name));
  if (builtins.length) {
    const group = document.createElement('optgroup');
    group.label = 'Presets';
    builtins.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.name;
      opt.textContent = t.name;
      group.appendChild(opt);
    });
    select.appendChild(group);
  }
  // Restore selection if it still exists
  if (currentVal) select.value = currentVal;
}

/**
 * Init reset button — clears all character fields
 */
function initResetButton() {
  const btn = document.getElementById('reset-character-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    // Just reset the form to default — no confirmation needed
    const charSelect = document.getElementById('char-template-select');
    if (charSelect) {
      charSelect.value = '__default__';
      charSelect.dispatchEvent(new Event('change'));
    }
    // Deactivate character
    selectedPreset = null;
    _syncCharIndicator();
  });
}

/**
 * Load user templates from server and populate datalist
 */
async function loadUserTemplates() {
  try {
    const res = await fetch(`${API_BASE}/api/presets/templates`);
    if (res.ok) {
      userTemplates = await res.json();
    } else {
      userTemplates = [];
    }
  } catch (e) {
    userTemplates = [];
  }
  userTemplatesLoaded = true;
  _populateCharSelect();
}

async function ensureUserTemplatesLoaded() {
  if (!userTemplatesLoaded) await loadUserTemplates();
}

function _templateForPersona(name) {
  const value = String(name || '').trim();
  if (!value) return null;
  return PROMPT_TEMPLATES.find(t => t.name === value || t.id === value)
    || userTemplates.find(t => t.name === value || t.id === value)
    || null;
}

function _configFromPersonaTemplate(name, tmpl) {
  const charName = (tmpl.character_name || tmpl.name || name || '').trim();
  return {
    name: charName,
    character_name: charName,
    temperature: Number.isFinite(Number(tmpl.temperature)) ? Number(tmpl.temperature) : 1.0,
    max_tokens: Number.isFinite(Number(tmpl.max_tokens)) ? Number(tmpl.max_tokens) : 0,
    system_prompt: tmpl.system_prompt || tmpl.prompt || '',
    inject_prefix: '',
    inject_suffix: '',
    enabled: true,
  };
}

async function _persistCustomPersonaConfig(config) {
  try {
    await fetch(`${API_BASE}/api/presets/custom`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: config.character_name || config.name || '',
        enabled: config.enabled !== false,
        temperature: config.temperature,
        max_tokens: config.max_tokens,
        system_prompt: config.system_prompt || '',
        inject_prefix: config.inject_prefix || '',
        inject_suffix: config.inject_suffix || '',
      }),
    });
  } catch (_) {}
}

async function _loadDefaultPersonaPreference() {
  let value;
  try {
    const res = await fetch(`${API_BASE}/api/prefs/default_persona`, { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      if (data && Object.prototype.hasOwnProperty.call(data, 'value')) {
        value = data.value;
      }
    }
  } catch (_) {}

  if (value === undefined) {
    try {
      const res = await fetch(`${API_BASE}/api/auth/settings`, { credentials: 'same-origin' });
      if (res.ok) {
        const settings = await res.json();
        if (settings && Object.prototype.hasOwnProperty.call(settings, 'default_persona')) {
          value = settings.default_persona;
        }
      }
    } catch (_) {}
  }

  if (value === undefined || value === null) value = 'Iris';
  value = String(value);
  // The shipped default ('Iris') follows the user's language preference —
  // Korean/German users get the matching Iris variant for new chats. An
  // explicitly chosen persona (any other value) is always respected verbatim.
  if (value === 'Iris') {
    const variants = { ko: 'Iris-Korean', de: 'Iris-German' };
    try {
      const res = await fetch(`${API_BASE}/api/prefs/language`, { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        const mapped = data && variants[data.value];
        if (mapped) return mapped;
      }
    } catch (_) {}
  }
  return value;
}

export async function applyPersonaByName(name, options = {}) {
  const personaName = String(name == null ? '' : name).trim();
  if (!personaName) {
    deactivateCharacter();
    return true;
  }

  let tmpl = _templateForPersona(personaName);
  if (!tmpl) {
    await ensureUserTemplatesLoaded();
    tmpl = _templateForPersona(personaName);
  }
  if (!tmpl) return false;

  const config = _configFromPersonaTemplate(personaName, tmpl);
  presets.custom = { ...(presets.custom || {}), ...config };
  selectedPreset = 'custom';
  const miniBtn = document.getElementById('overflow-preset-btn');
  if (miniBtn) miniBtn.classList.add('active');
  _syncCharIndicator();

  if (options.persist !== false) {
    await _persistCustomPersonaConfig(config);
  }
  return true;
}

export async function applyDefaultPersonaForNewChat() {
  let cached = 'Iris';
  try {
    const stored = localStorage.getItem('odysseus-default-persona');
    if (stored !== null) cached = stored;
  } catch (_) {}

  await applyPersonaByName(cached, { persist: false });

  const preferred = await _loadDefaultPersonaPreference();
  try {
    localStorage.setItem('odysseus-default-persona', preferred);
  } catch (_) {}
  if (preferred !== cached) {
    return applyPersonaByName(preferred);
  }
  return true;
}


/**
 * Init "Save as Character" button
 */
/**
 * "Create Persistent Chat" button — creates a favorited session for the current character
 */
function initPersistentChat() {
  const btn = document.getElementById('create-persistent-chat-btn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    const nameInput = document.getElementById('custom-character-name');
    const charName = nameInput ? nameInput.value.trim() : '';
    if (!charName) return;

    try {
      // Get current model info from session module
      const sessionModule = (await import('./sessions.js'));
      const sessions = sessionModule.getSessions();
      const current = sessions.find(s => s.id === sessionModule.getCurrentSessionId());

      // Create new session
      const fd = new FormData();
      fd.append('name', charName);
      if (current) {
        fd.append('endpoint_url', current.endpoint_url || '');
        fd.append('model', current.model || '');
        fd.append('skip_validation', 'true');
      }
      const res = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: fd });
      if (!res.ok) throw new Error('Failed to create session');
      const data = await res.json();
      const sessionId = data.session_id || data.id;

      // Favorite it
      const favFd = new FormData();
      favFd.append('important', true);
      await fetch(`${API_BASE}/api/session/${sessionId}/important`, { method: 'POST', body: favFd });

      // Save session → character mapping so it restores on switch
      const charSessions = loadStoredObject('odysseus-char-sessions');
      charSessions[sessionId] = charName;
      localStorage.setItem('odysseus-char-sessions', JSON.stringify(charSessions));

      // Close modal, reload sessions, switch to the new chat
      const modal = document.getElementById('custom-preset-modal');
      if (modal) modal.classList.add('hidden');
      await sessionModule.loadSessions();
      await sessionModule.selectSession(sessionId);

      btn.textContent = 'Created!';
      setTimeout(() => { btn.textContent = 'Create Persistent Chat'; }, 1500);
    } catch (e) {
      console.error('Failed to create persistent chat:', e);
      btn.textContent = 'Error';
      setTimeout(() => { btn.textContent = 'Create Persistent Chat'; }, 2000);
    }
  });
}

function initSaveAsTemplate() {
  const btn = document.getElementById('save-as-template-btn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    const nameInput = document.getElementById('custom-character-name');
    const promptInput = document.getElementById('custom-system-prompt');
    const tempInput = document.getElementById('custom-temperature');
    const tokensInput = document.getElementById('custom-max-tokens');

    let name = nameInput ? nameInput.value.trim() : '';
    if (!name) {
      name = prompt('Enter a name for this persona:');
      if (!name || !name.trim()) return;
      name = name.trim();
      if (nameInput) nameInput.value = name;
    }

    const _rawTk = tokensInput ? parseInt(tokensInput.value) : 0;
    const template = {
      id: '',
      name: name,
      system_prompt: promptInput ? promptInput.value : '',
      temperature: tempInput ? parseFloat(tempInput.value) : 1.0,
      max_tokens: _rawTk > 8192 ? 0 : _rawTk,
    };

    try {
      const res = await fetch(`${API_BASE}/api/presets/templates`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(template),
      });
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const data = await res.json();
      if (data.success) {
        await loadUserTemplates();
        btn.textContent = 'Saved!';
        setTimeout(() => { btn.textContent = 'Save as Template'; }, 1500);
      } else {
        btn.textContent = 'Error';
        setTimeout(() => { btn.textContent = 'Save as Template'; }, 2000);
      }
    } catch (e) {
      console.error('Failed to save template:', e);
      btn.textContent = 'Restart server';
      btn.style.color = 'var(--color-error)';
      setTimeout(() => { btn.textContent = 'Save as Template'; btn.style.color = ''; }, 3000);
    }
  });
}

/**
 * Load presets from server
 */
export async function loadPresets(showError) {
  try {
    const res = await fetch(`${API_BASE}/api/presets`);
    presets = await res.json();

    const custom = presets.custom;
    if (custom && custom.enabled === undefined) {
      const legacyPrompt = "You are a helpful, balanced assistant. Match your response style to the user's needs.";
      if (
        custom.name === 'Custom'
        && !custom.character_name
        && custom.system_prompt === legacyPrompt
      ) {
        custom.enabled = false;
        custom.system_prompt = '';
        custom.temperature = 1.0;
        custom.max_tokens = 0;
        custom.inject_prefix = custom.inject_prefix || '';
        custom.inject_suffix = custom.inject_suffix || '';
      }
    }

    // Auto-activate custom preset if enabled and has content
    if (custom && custom.enabled !== false && (custom.character_name || custom.system_prompt)) {
      selectedPreset = 'custom';
      const miniBtn = document.getElementById('overflow-preset-btn');
      if (miniBtn) miniBtn.classList.add('active');
    }
    setTimeout(() => { _syncCharIndicator(); }, 0);
  } catch (error) {
    console.error('Failed to load presets:', error);
    if (showError) {
      showError('Failed to load presets');
    }
  }
}

/**
 * Set active preset
 */
export function setActivePreset(presetId) {
  document.querySelectorAll('.preset-btn').forEach(btn => {
    btn.classList.remove('active');
  });

  if (presetId) {
    selectedPreset = presetId;
    const btn = document.getElementById(`preset-${presetId}-btn`);
    if (btn) {
      btn.classList.add('active');
    }
  } else {
    selectedPreset = null;
  }
}

/**
 * Open custom preset modal
 */
export function openCustomPresetModal() {
  const modal = document.getElementById('custom-preset-modal');
  if (!modal) return;

  const savedConfig = presets.custom || {
    character_name: "",
    temperature: 1.0,
    max_tokens: 0,
    system_prompt: ""
  };

  const nameInput = document.getElementById('custom-character-name');
  const tempInput = document.getElementById('custom-temperature');
  const tokensInput = document.getElementById('custom-max-tokens');
  const promptInput = document.getElementById('custom-system-prompt');

  if (nameInput) nameInput.value = savedConfig.character_name || '';
  // Sync select dropdown to current character
  const charSelect = document.getElementById('char-template-select');
  if (charSelect) {
    const charName = savedConfig.character_name || '';
    if (charName) {
      charSelect.value = charName;
      // If current name isn't in the list, fall back to "New character..." with name filled in
      if (charSelect.value !== charName) charSelect.value = '';
    } else {
      charSelect.value = '__default__';
    }
  }
  if (tempInput) {
    tempInput.value = savedConfig.temperature;
    const tv = document.getElementById('temp-value');
    if (tv) tv.textContent = parseFloat(savedConfig.temperature).toFixed(1);
  }
  if (tokensInput) {
    const saved = savedConfig.max_tokens || 0;
    tokensInput.value = saved === 0 ? 8448 : saved;
    const tkv = document.getElementById('tokens-value');
    if (tkv) tkv.textContent = (saved === 0 || saved > 8192) ? 'No limit' : parseInt(saved).toLocaleString();
  }
  if (promptInput) promptInput.value = savedConfig.system_prompt || '';

  // Load inject fields
  const prefixInput = document.getElementById('inject-prefix');
  const suffixInput = document.getElementById('inject-suffix');
  if (prefixInput) prefixInput.value = savedConfig.inject_prefix || '';
  if (suffixInput) suffixInput.value = savedConfig.inject_suffix || '';

  // Track initial state to detect changes for dynamic button label
  const _snapshot = {
    name: nameInput ? nameInput.value : '',
    prompt: promptInput ? promptInput.value : '',
    temp: tempInput ? tempInput.value : '1',
    tokens: tokensInput ? tokensInput.value : '8448',
  };
  function _updateStartBtn() {
    const btn = document.getElementById('save-custom-preset');
    const resetBtn = document.getElementById('reset-character-btn');
    if (!btn) return;
    const changed = (nameInput && nameInput.value !== _snapshot.name)
      || (promptInput && promptInput.value !== _snapshot.prompt)
      || (tempInput && tempInput.value !== _snapshot.temp)
      || (tokensInput && tokensInput.value !== _snapshot.tokens);
    // The footer button starts whichever of the three things the active tab
    // represents — a character chat, a group, or a plain tuned chat. Label
    // it so the action is obvious instead of a generic "Start".
    const activeTab = document.querySelector('.preset-tab.active')?.dataset.chartab || 'inject';
    let label;
    if (activeTab === 'group') {
      label = 'Start Group';
    } else if (activeTab === 'inject') {
      // Inject tab = a plain tuned "prompt" chat (prefix/suffix + temp/tokens),
      // no persona.
      label = 'Start Prompt';
    } else {
      // Character/persona tab. "Save & " prefix when the user edited a template,
      // so it's clear the edit is being saved on start.
      label = changed ? 'Save & Start Persona' : 'Start Persona';
    }
    btn.textContent = label;
    // Show a "Cancel" button next to Start when the active tab's feature is
    // currently ON, so the user can turn it off here instead of hunting the
    // tiny X on the chat bar.
    const cancelBtn = document.getElementById('cancel-custom-preset');
    if (cancelBtn) {
      const groupOn = !!(window.groupModule && window.groupModule.isActive && window.groupModule.isActive());
      const featOn = activeTab === 'group' ? groupOn : !!(presets.custom && presets.custom.enabled);
      cancelBtn.style.display = featOn ? '' : 'none';
      cancelBtn.textContent = activeTab === 'group' ? 'Cancel group' : 'Cancel';
    }
    // Reset only makes sense on the character tab (it resets the persona).
    if (resetBtn) resetBtn.style.display = (changed && activeTab === 'character') ? '' : 'none';
  }
  [nameInput, promptInput, tempInput, tokensInput].forEach(el => {
    if (el) el.addEventListener('input', _updateStartBtn);
  });
  // Re-label the Start button when the user switches tabs. Rebind the fresh
  // closure each time the modal opens (removing any stale one) so the label
  // logic always reads this open's snapshot/inputs.
  document.querySelectorAll('.preset-tab[data-chartab]').forEach(tab => {
    if (tab._startLabelSync) tab.removeEventListener('click', tab._startLabelSync);
    tab._startLabelSync = _updateStartBtn;
    tab.addEventListener('click', _updateStartBtn);
  });
  // Wire the "Cancel" button once — turn off the active tab's feature + close.
  const _cancelBtn = document.getElementById('cancel-custom-preset');
  if (_cancelBtn && !_cancelBtn._wired) {
    _cancelBtn._wired = true;
    _cancelBtn.addEventListener('click', () => {
      const t = document.querySelector('.preset-tab.active')?.dataset.chartab || 'inject';
      if (t === 'group') {
        try { if (window.groupModule && window.groupModule.stopGroup) window.groupModule.stopGroup(); } catch {}
        if (window._syncGroupIndicator) window._syncGroupIndicator(false);
      } else {
        deactivateCharacter();
        try {
          fetch(`${API_BASE}/api/presets/custom`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...(presets.custom || {}), name: (presets.custom && presets.custom.character_name) || '', enabled: false }),
          }).catch(() => {});
        } catch {}
      }
      const m = document.getElementById('custom-preset-modal');
      if (m) m.classList.add('hidden');
    });
  }
  // When selecting a template, update snapshot so it counts as "unchanged"
  if (charSelect) charSelect.addEventListener('change', () => setTimeout(() => {
    _snapshot.name = nameInput ? nameInput.value : '';
    _snapshot.prompt = promptInput ? promptInput.value : '';
    _snapshot.temp = tempInput ? tempInput.value : '1';
    _snapshot.tokens = tokensInput ? tokensInput.value : '8448';
    _updateStartBtn();
  }, 50));
  _updateStartBtn();

  function _syncCharRows() {
    const hasName = nameInput && nameInput.value.trim();
    const delBtn = document.getElementById('char-delete-template-btn');
    if (delBtn) delBtn.style.display = userTemplates.find(t => t.name === (nameInput ? nameInput.value.trim() : '')) ? '' : 'none';
    const persistBtn = document.getElementById('create-persistent-chat-btn');
    if (persistBtn) persistBtn.style.display = hasName ? '' : 'none';
  }

  _syncCharRows();
  if (nameInput && !nameInput._syncWired) {
    nameInput._syncWired = true;
    nameInput.addEventListener('input', _syncCharRows);
  }

  // Persistent chat: lock character identity (dropdown, name) but allow style/temp/memory edits
  const isPersistent = !!window._persistentChatSession;
  const lockNotice = document.getElementById('char-lock-notice');
  const resetBtn = document.getElementById('reset-character-btn');
  const newBtn = document.getElementById('char-new-btn');
  const persistBtn = document.getElementById('create-persistent-chat-btn');
  const delBtn2 = document.getElementById('char-delete-template-btn');

  if (isPersistent) {
    if (charSelect) charSelect.disabled = true;
    if (nameInput) nameInput.readOnly = true;
    if (resetBtn) resetBtn.style.display = 'none';
    if (newBtn) newBtn.style.display = 'none';
    if (persistBtn) persistBtn.style.display = 'none';
    if (delBtn2) delBtn2.style.display = 'none';
    if (!lockNotice) {
      const notice = document.createElement('div');
      notice.id = 'char-lock-notice';
      notice.style.cssText = 'font-size:11px;color:var(--color-muted);text-align:center;padding:6px;margin-bottom:8px;border:1px dashed var(--border);border-radius:6px;';
      notice.textContent = 'Persistent chat — persona is locked. Style, temperature, and memory can still be changed.';
      modal.querySelector('.modal-body').prepend(notice);
    }
  } else {
    if (lockNotice) lockNotice.remove();
    if (charSelect) charSelect.disabled = false;
    if (nameInput) nameInput.readOnly = false;
    if (resetBtn) resetBtn.style.display = '';
    if (newBtn) newBtn.style.display = '';
  }

  modal.classList.remove('hidden');
}

/**
 * Save custom preset
 */
export async function saveCustomPreset(showToast, showError) {
  const nameInput = document.getElementById('custom-character-name');
  const tempInput = document.getElementById('custom-temperature');
  const tokensInput = document.getElementById('custom-max-tokens');
  const promptInput = document.getElementById('custom-system-prompt');

  if (!tempInput || !tokensInput || !promptInput) return;

  // This only runs for Character / Inject starts (the Group tab is handled by
  // group.js and skipped in app.js). If a group is still active from a prior
  // session, deactivate it — otherwise the chat-submit handler keeps routing
  // messages through group fan-out and a character chat "becomes a group".
  try {
    if (window.groupModule && window.groupModule.isActive()) {
      window.groupModule.stopGroup();
      if (window._syncGroupIndicator) window._syncGroupIndicator(false);
    }
  } catch (_) {}

  // Starting from the Inject tab means a plain tuned chat (prefix/suffix +
  // temp/tokens) — NOT a persona. The name/system-prompt fields live on the
  // Character tab and may still hold a previously-selected character, so
  // ignore them here or the chat would launch in-character.
  const _activeTab = document.querySelector('.preset-tab.active')?.dataset.chartab || 'character';
  const _isInjectStart = _activeTab === 'inject';

  const name = _isInjectStart ? '' : (nameInput ? nameInput.value.trim() : '');
  const temperature = parseFloat(tempInput.value);
  const rawTokens = parseInt(tokensInput.value);
  const max_tokens = rawTokens > 8192 ? 0 : rawTokens;
  const system_prompt = _isInjectStart ? '' : promptInput.value;

  const enabled = true; // always enabled when saving — deactivation happens via X/Reset

  const _prefixInput = document.getElementById('inject-prefix');
  const _suffixInput = document.getElementById('inject-suffix');

  const config = {
    name: name,
    enabled: enabled,
    temperature: Math.max(0, Math.min(2, temperature)),
    max_tokens: max_tokens,
    system_prompt: system_prompt,
    inject_prefix: _prefixInput ? _prefixInput.value : '',
    inject_suffix: _suffixInput ? _suffixInput.value : '',
  };

  try {
    const response = await fetch(`${API_BASE}/api/presets/custom`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    });

    const result = await response.json();
    if (result.success) {
      presets.custom = { ...presets.custom, ...config, character_name: name, enabled: enabled };

      // The custom preset must be the SELECTED preset for its values to reach
      // the model — chat.js only sends `preset_id` when getSelectedPreset() is
      // truthy. Activate it when there's a persona (name/prompt) OR when the
      // user has dialed in non-default tuning (temperature / max tokens) — the
      // "Inject" tab's plain-chat case. Without the tuning check, "just set
      // temp + max tokens" would silently do nothing.
      const _hasTuning = (config.temperature !== 1.0) || (config.max_tokens !== 0);
      const _hasInject = !!(config.inject_prefix || config.inject_suffix);
      const _hasContent = !!(system_prompt || name || _hasTuning || _hasInject);
      if (enabled && _hasContent) {
        selectedPreset = 'custom';
        // Turn off research — doesn't make sense with a character
        if (window._syncResearchIndicator) window._syncResearchIndicator(false);
      } else {
        selectedPreset = null;
      }

      // Update mini button state
      const miniBtn = document.getElementById('overflow-preset-btn');
      if (miniBtn) {
        miniBtn.classList.toggle('active', enabled && _hasContent);
      }

      setTimeout(() => { _syncCharIndicator(); }, 0);

      // Auto-save to templates (non-blocking) — skip built-in presets
      const _selVal = document.getElementById('char-template-select')?.value || '';
      const isBuiltinPreset = PROMPT_TEMPLATES.some(t => t.isPreset && (t.name === name || t.name === _selVal));
      const saveName = isBuiltinPreset ? null : (name || null);
      if (saveName) {
        fetch(`${API_BASE}/api/presets/templates`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id: (userTemplates.find(t => t.name === saveName) || {}).id || '',
            name: saveName, system_prompt, temperature: config.temperature, max_tokens: config.max_tokens,
          }),
        }).then(r => { if (r.ok) loadUserTemplates(); }).catch(() => {});
      }

      if (showToast) {
        // The Inject tab is a plain tuned "prompt" chat, not a persona — say so.
        showToast(_isInjectStart ? 'Prompt saved' : 'Persona saved');
      }
      const modal = document.getElementById('custom-preset-modal');
      if (modal) {
        modal.classList.add('hidden');
      }
    } else {
      if (showError) {
        showError('Failed to save custom preset');
      }
    }
  } catch (error) {
    console.error('Error saving custom preset:', error);
    if (showError) {
      showError('Failed to save custom preset');
    }
  }
}

/**
 * Get selected preset ID
 */
export function getSelectedPreset() {
  return selectedPreset;
}

/**
 * Get preset by ID
 */
export function getPreset(presetId) {
  return presets[presetId];
}

/**
 * Get all presets
 */
export function getAllPresets() {
  return presets;
}

/**
 * Get the character name (if set)
 */
export function getCharacterName() {
  if (!selectedPreset) return '';
  const custom = presets.custom;
  if (!custom || custom.enabled === false) return '';
  return custom.character_name || '';
}

/**
 * Get inject prefix/suffix (if set and preset active)
 */
export function getInject() {
  // Only inject when a preset is actually ACTIVE — mirror getCharacterName's
  // gate. Without the selectedPreset/enabled check, any text left in the
  // prefix/suffix fields got injected into every message even though the user
  // never started/activated the preset.
  if (!selectedPreset) return { prefix: '', suffix: '' };
  const custom = presets.custom;
  if (!custom || custom.enabled === false) return { prefix: '', suffix: '' };
  return {
    prefix: custom.inject_prefix || '',
    suffix: custom.inject_suffix || '',
  };
}

/**
 * Fully deactivate the character — clear preset, hide indicator, update overflow btn.
 */
export function deactivateCharacter() {
  selectedPreset = null;
  if (presets.custom) presets.custom.enabled = false;
  const charInd = document.getElementById('character-indicator-btn');
  if (charInd) { charInd.style.display = 'none'; charInd.classList.remove('active'); }
  const miniBtn = document.getElementById('overflow-preset-btn');
  if (miniBtn) miniBtn.classList.remove('active');
}

/**
 * Show/hide the memory scope bar and wire up scope switching.
 * Called after presets load and after saving character.
 */
/**
 * Copy all user memories (non-character) into the character's memory pool.
 */
async function _mergeUserMemories(charName) {
  try {
    const res = await fetch(`${API_BASE}/api/memory`);
    const data = await res.json();
    const userMems = (data.memory || []).filter(m => !m.character);
    if (!userMems.length) return;
    for (const m of userMems) {
      await fetch(`${API_BASE}/api/memory/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: m.text, category: m.category || 'fact', source: 'user', character: charName }),
      });
    }
  } catch (e) {
    console.error('Failed to merge memories:', e);
  }
}

function _reloadMemoryList() {
  import('./memory.js').then(m => {
    if (m.renderMemoryList) m.renderMemoryList();
    if (m.updateMemoryCount) m.updateMemoryCount();
  }).catch(() => {});
}

/**
 * Show/hide the character indicator pill in the chat input bar.
 */
function _syncCharIndicator() {
  const btn = document.getElementById('character-indicator-btn');
  const nameSpan = document.getElementById('character-indicator-name');
  const iconEl = document.getElementById('char-indicator-icon');
  if (!btn) return;
  const custom = presets.custom;
  const enabled = custom?.enabled !== false;
  const hasChar = enabled && !!custom?.character_name;
  // "Inject mode": custom preset is active for plain tuning / inject only —
  // no persona. Detected from the custom config so it survives a reload.
  const _t = parseFloat(custom?.temperature);
  const _hasTuning = (!isNaN(_t) && _t !== 1.0) || (!!custom?.max_tokens && custom.max_tokens !== 0);
  const _hasInject = !!(custom?.inject_prefix || custom?.inject_suffix);
  const injectActive = enabled && !custom?.character_name && (_hasTuning || _hasInject);
  // Icon path sets for the indicator chip.
  const _AVATAR = '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>';
  const _SYRINGE = '<path d="m18 2 4 4"/><path d="m17 7 3-3"/><path d="M19 9 8.7 19.3c-1 1-2.5 1-3.4 0l-.6-.6c-1-1-1-2.5 0-3.4L15 5"/><path d="m9 11 4 4"/><path d="m5 19-3 3"/><path d="m14 4 6 6"/>';
  if (hasChar || injectActive) {
    btn.style.display = '';
    btn.classList.add('active');
    if (hasChar) {
      if (iconEl) iconEl.innerHTML = _AVATAR;
      if (nameSpan) nameSpan.textContent = custom.character_name;
      btn.title = `Persona: ${custom.character_name} — click to configure`;
    } else {
      // Inject/tuning chat — syringe tag labeled "Prompt" to match the
      // window identity, no persona name.
      if (iconEl) iconEl.innerHTML = _SYRINGE;
      if (nameSpan) nameSpan.textContent = 'Prompt';
      btn.title = 'Custom settings active — click to configure';
    }
    // Hide X in persistent chats
    const xIcon = btn.querySelector('.tool-indicator-x');
    if (xIcon) xIcon.style.display = window._persistentChatSession ? 'none' : '';
    if (!btn._wired) {
      btn._wired = true;
      btn.addEventListener('click', (e) => {
        // If clicking the X, deactivate character
        if (e.target.closest('.tool-indicator-x')) {
          if (window._persistentChatSession) return; // locked in persistent chat
          selectedPreset = null;
          presets.custom = { ...presets.custom, enabled: false };
          btn.style.display = 'none';
          btn.classList.remove('active');
          const miniBtn = document.getElementById('overflow-preset-btn');
          if (miniBtn) miniBtn.classList.remove('active');
          // Save disabled state to backend
          fetch(`${API_BASE}/api/presets/custom`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...presets.custom, name: presets.custom.character_name || '', enabled: false }),
          }).catch(() => {});
          return;
        }
        if (typeof openCustomPresetModal === 'function') openCustomPresetModal();
      });
    }
  } else {
    btn.style.display = 'none';
    btn.classList.remove('active');
  }
}

/**
 * Called on every session switch. Handles persistent chat character lock.
 * - Entering a persistent chat: activate its character
 * - Leaving a persistent chat: deactivate the character
 * - Non-persistent chats: leave character state as-is
 */
let _prevSessionId = null;

export function onSessionSwitch(sessionId) {
  const charSessions = loadStoredObject('odysseus-char-sessions');

  // Leaving a persistent chat — deactivate for this switch only
  if (window._persistentChatSession) {
    selectedPreset = null;
    window._persistentChatSession = null;
    _syncCharIndicator();
  }

  _prevSessionId = sessionId;

  // Clean up stale entries (deleted sessions)
  // If sessionId doesn't exist in the session list, remove its mapping
  const charName = charSessions[sessionId];
  if (charName) {
    // Find the template (saved or built-in)
    const tmpl = userTemplates.find(t => t.name === charName)
      || PROMPT_TEMPLATES.find(t => t.name === charName);
    if (tmpl) {
      presets.custom = {
        ...presets.custom,
        character_name: charName,
        system_prompt: tmpl.system_prompt || tmpl.prompt || '',
        temperature: tmpl.temperature ?? 1.0,
        max_tokens: tmpl.max_tokens || 0,
        enabled: true,
      };
      selectedPreset = 'custom';
    }
    _syncCharIndicator();
    // Mark this as a locked persistent chat
    window._persistentChatSession = sessionId;
  } else {
    window._persistentChatSession = null;
  }
}

/**
 * Check if the current session is a persistent (locked) character chat.
 */
export function isPersistentChat() {
  return !!window._persistentChatSession;
}

/**
 * Remove a session from persistent chat mappings (call when session is deleted).
 */
export function removePersistentChat(sessionId) {
  const charSessions = loadStoredObject('odysseus-char-sessions');
  if (charSessions[sessionId]) {
    delete charSessions[sessionId];
    localStorage.setItem('odysseus-char-sessions', JSON.stringify(charSessions));
  }
  // If we were in that persistent chat, fully clear state
  if (window._persistentChatSession === sessionId) {
    window._persistentChatSession = null;
    selectedPreset = null;
    _syncCharIndicator();
  }
}

const presetsModule = {
  init,
  loadPresets,
  setActivePreset,
  openCustomPresetModal,
  saveCustomPreset,
  getSelectedPreset,
  getPreset,
  getAllPresets,
  getCharacterName,
  onSessionSwitch,
  isPersistentChat,
  removePersistentChat,
  deactivateCharacter,
  applyPersonaByName,
  applyDefaultPersonaForNewChat,
  getInject
};

export default presetsModule;
