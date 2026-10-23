// Text-to-speech and speech-to-text settings panels.
// Extracted verbatim from static/js/settings.js. Behaviour is unchanged: the
// functions moved, their bodies did not. settings.js imports them and calls
// them from the same places, so load order and init sequence are untouched.

import { byId as el } from './dom.js';
import { postSettings as _postSettings } from './api.js';

export async function initTtsSettings() {
  var provSel = el('set-ttsProviderSelect');
  var modelSelect = el('set-ttsModelSelect');
  var modelInput = el('set-ttsModelInput');
  var voiceSelect = el('set-ttsVoiceSelect');
  var voiceInput = el('set-ttsVoiceInput');
  var _savedTtsVoice = '';   // the user's saved voice, used to seed the dropdown
  var modelRow = el('set-ttsModelRow');
  var voiceRow = el('set-ttsVoiceRow');
  var speedSelect = el('set-ttsSpeedSelect');
  var speedRow = el('set-ttsSpeedRow');
  var ttsMsg = el('set-ttsSettingsMsg');
  var ttsEnabledToggle = el('set-ttsEnabledToggle');
  var ttsConfigWrap = provSel ? provSel.closest('div[style*="flex-direction"]') : null;
  var azureRow = el('set-ttsAzureRow');
  var azureKey = el('set-ttsAzureKey');
  var azureRegion = el('set-ttsAzureRegion');
  var elevenRow = el('set-ttsElevenRow');
  var elevenKey = el('set-ttsElevenKey');
  var elevenModel = el('set-ttsElevenModel');
  var edgeNote = el('set-ttsEdgeNote');

  function isEndpoint() { return provSel.value.startsWith('endpoint:'); }
  function getModel() {
    if (provSel.value === 'elevenlabs' && elevenModel) return elevenModel.value;
    return modelInput.value;   // free-text for endpoints (Kokoro/Piper/OpenAI) too
  }
  // Voice uses a themed <select> populated from the provider's voices (consistent
  // with the app's other dropdowns); it falls back to the free-text input only
  // when the provider can't enumerate. loadTtsVoiceOptions() owns which is shown.
  function getVoice() {
    return voiceSelect.style.display !== 'none' ? voiceSelect.value : voiceInput.value;
  }

  function updateVisibility() {
    var prov = provSel.value;
    modelRow.style.display = prov.startsWith('endpoint:') ? 'flex' : 'none';
    voiceRow.style.display = prov === 'disabled' ? 'none' : 'flex';
    speedRow.style.display = prov === 'disabled' ? 'none' : 'flex';
    if (azureRow) azureRow.style.display = prov === 'azure' ? 'flex' : 'none';
    if (elevenRow) elevenRow.style.display = prov === 'elevenlabs' ? 'flex' : 'none';
    if (edgeNote) edgeNote.style.display = prov === 'edge' ? 'block' : 'none';
    // Model stays free-text (a local endpoint uses its own name, e.g. "kokoro").
    modelSelect.style.display = 'none'; modelInput.style.display = '';
    // Voice select-vs-input visibility is owned by loadTtsVoiceOptions(); here we
    // only hide both when TTS is disabled.
    if (prov === 'disabled') { voiceSelect.style.display = 'none'; voiceInput.style.display = 'none'; }
  }

  try {
    var epRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    var endpoints = await epRes.json();
    var _enabledTts = (endpoints || []).filter(function(ep) { return ep.is_enabled; });
    // If any endpoint is tagged model_type 'tts', list only those (clean); else
    // list all enabled endpoints so untagged Kokoro/Piper servers still appear.
    var _anyTts = _enabledTts.some(function(ep) { return ep.model_type === 'tts'; });
    var _seenTts = {};
    _enabledTts.forEach(function(ep) {
      if (_anyTts && ep.model_type !== 'tts') return;
      // Collapse the same server added more than once so the dropdown doesn't
      // list duplicates (delete the extra rows in Added Models).
      var k = (ep.base_url || '') + '|' + (ep.name || '');
      if (_seenTts[k]) return;
      _seenTts[k] = 1;
      var opt = document.createElement('option'); opt.value = 'endpoint:' + ep.id; opt.textContent = ep.name + ' (API)'; provSel.appendChild(opt);
    });
  } catch (e) { console.warn('Failed to load endpoints for TTS', e); }

  try {
    var settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await settingsRes.json();
    if (settings.tts_provider) provSel.value = settings.tts_provider;
    if (settings.tts_model) { modelSelect.value = settings.tts_model; modelInput.value = settings.tts_model; }
    if (elevenModel && (settings.tts_model || '').indexOf('eleven') === 0) elevenModel.value = settings.tts_model;
    if (settings.tts_voice) { voiceSelect.value = settings.tts_voice; voiceInput.value = settings.tts_voice; _savedTtsVoice = settings.tts_voice; }
    if (settings.tts_speed) { speedSelect.value = settings.tts_speed; }
    if (azureKey && settings.azure_speech_key) azureKey.value = settings.azure_speech_key;
    if (azureRegion && settings.azure_speech_region) azureRegion.value = settings.azure_speech_region;
    if (elevenKey && settings.elevenlabs_api_key) elevenKey.value = settings.elevenlabs_api_key;
    if (ttsEnabledToggle) ttsEnabledToggle.checked = settings.tts_enabled !== false;
  } catch (e) { console.warn('Failed to load TTS settings', e); }

  function syncTtsDisabled() {
    var off = ttsEnabledToggle && !ttsEnabledToggle.checked;
    var card = ttsEnabledToggle ? ttsEnabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (ttsConfigWrap) ttsConfigWrap.style.pointerEvents = off ? 'none' : '';
  }
  syncTtsDisabled();
  updateVisibility();

  async function saveTTS() {
    try {
      await _postSettings({ tts_enabled: ttsEnabledToggle ? ttsEnabledToggle.checked : true, tts_provider: provSel.value, tts_model: getModel() || 'tts-1', tts_voice: getVoice() || 'alloy', tts_speed: speedSelect.value || '1', azure_speech_key: azureKey ? azureKey.value.trim() : '', azure_speech_region: azureRegion ? azureRegion.value.trim() : '', elevenlabs_api_key: elevenKey ? elevenKey.value.trim() : '' });
      ttsMsg.textContent = 'Saved'; ttsMsg.style.color = 'var(--fg)'; setTimeout(() => { ttsMsg.textContent = ''; }, 2000);
      if (window.aiTTSManager) window.aiTTSManager.checkAvailability();
    } catch (e) { ttsMsg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; ttsMsg.style.color = 'var(--red)'; }
  }

  async function saveAndClearCache() {
    await saveTTS();
    fetch('/api/tts/clear-cache', { method: 'POST', credentials: 'same-origin' }).catch(function(){});
  }

  // Populate the Voice <select> from the configured provider (Edge / Azure /
  // ElevenLabs voices, or a Kokoro endpoint's /audio/voices) — a themed dropdown
  // like the rest of the app. If the provider can't enumerate (no key / not
  // installed / unreachable), fall back to the free-text input. `want` is the
  // voice to select (defaults to the current value). Reads server-saved settings,
  // so call AFTER save.
  async function loadTtsVoiceOptions(want) {
    if (want == null) want = getVoice();
    var voices = [];
    try {
      var res = await fetch('/api/tts/voices', { credentials: 'same-origin' });
      var data = await res.json();
      voices = (data && data.voices) || [];
    } catch (e) { voices = []; }
    if (provSel.value === 'disabled') return;   // updateVisibility hid both
    if (voices.length) {
      voiceSelect.innerHTML = '';
      var found = false;
      voices.forEach(function(v) {
        if (!v || !v.value) return;
        var o = document.createElement('option');
        o.value = v.value; o.textContent = v.label || v.value;
        voiceSelect.appendChild(o);
        if (v.value === want) found = true;
      });
      if (want && !found) {   // keep a saved/custom voice selectable
        var o = document.createElement('option');
        o.value = want; o.textContent = want;
        voiceSelect.insertBefore(o, voiceSelect.firstChild);
      }
      voiceSelect.value = want || (voiceSelect.options[0] && voiceSelect.options[0].value) || '';
      voiceSelect.style.display = ''; voiceInput.style.display = 'none';
    } else {
      voiceInput.value = want || '';
      voiceInput.style.display = ''; voiceSelect.style.display = 'none';
    }
  }

  // Each provider has its own voice namespace (an Edge voice name isn't a valid
  // ElevenLabs voice id), so a provider change resets to that provider's default.
  function _defaultVoiceFor(prov, label) {
    if (prov === 'edge') return 'en-US-AvaNeural';
    if (prov === 'azure') return 'en-US-AriaNeural';
    if (prov === 'elevenlabs') return '21m00Tcm4TlvDq8ikWAM';
    if (prov.startsWith('endpoint:') && /kokoro/i.test(label || '')) return 'af_heart';
    return '';   // unknown endpoint → let the fetched list pick the first voice
  }
  provSel.addEventListener('change', function() {
    var prov = provSel.value;
    var label = (provSel.options[provSel.selectedIndex] || {}).textContent || '';
    if (isEndpoint()) {
      modelInput.placeholder = 'model (e.g. kokoro, tts-1)';
      if (/kokoro/i.test(label) && ['tts-1', 'tts-1-hd', 'gpt-4o-mini-tts', ''].indexOf(modelInput.value || '') !== -1) {
        modelInput.value = 'kokoro';
      }
    }
    var want = _defaultVoiceFor(prov, label);   // reset to the new provider's default
    updateVisibility();
    // Persist the new provider first (the voice list is fetched for the SAVED
    // provider), then repopulate the dropdown and persist the resolved voice.
    saveTTS().then(function() { return loadTtsVoiceOptions(want); }).then(saveTTS);
  });
  // Provider/keys changed → the available voices change too, so refresh the list.
  function _saveKeyAndReloadVoices() { return saveAndClearCache().then(function() { return loadTtsVoiceOptions(); }); }
  modelSelect.addEventListener('change', saveAndClearCache);
  modelInput.addEventListener('change', saveTTS);
  if (elevenModel) elevenModel.addEventListener('change', saveAndClearCache);
  voiceSelect.addEventListener('change', saveAndClearCache);
  voiceInput.addEventListener('change', saveTTS);
  if (azureKey) azureKey.addEventListener('change', _saveKeyAndReloadVoices);
  if (azureRegion) azureRegion.addEventListener('change', _saveKeyAndReloadVoices);
  if (elevenKey) elevenKey.addEventListener('change', _saveKeyAndReloadVoices);
  speedSelect.addEventListener('change', saveAndClearCache);
  if (ttsEnabledToggle) ttsEnabledToggle.addEventListener('change', function() { syncTtsDisabled(); saveTTS(); });

  loadTtsVoiceOptions(_savedTtsVoice);   // populate the voice dropdown for the saved provider

  // Preview / test button
  var previewBtn = el('set-ttsPreviewBtn');
  if (previewBtn) {
    var previewAudio = null;
    var previewPlaying = false;
    function resetPreview() { previewPlaying = false; previewBtn.textContent = 'Preview'; previewBtn.style.borderColor = ''; }

    previewBtn.addEventListener('click', async function() {
      if (previewPlaying) {
        if (previewAudio) { previewAudio.pause(); previewAudio = null; }
        resetPreview(); return;
      }
      var prov = provSel.value;
      if (prov === 'disabled') {
        ttsMsg.textContent = 'Select a provider first'; ttsMsg.style.color = 'var(--red, #e55)';
        setTimeout(function() { ttsMsg.textContent = ''; }, 2000); return;
      }
      var testText = 'Hello, this is a test of text to speech.';
      previewPlaying = true; previewBtn.textContent = 'Loading...';
      try {
        var res = await fetch('/api/tts/synthesize', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: testText, format: 'audio' })
        });
        if (!res.ok) { var err = await res.json().catch(function() { return {}; }); throw new Error(err.detail?.message || 'Synthesis failed'); }
        var blob = await res.blob();
        var url = URL.createObjectURL(blob);
        previewAudio = new Audio(url);
        previewBtn.textContent = 'Stop'; previewBtn.style.borderColor = 'var(--red, #e55)';
        await new Promise(function(resolve, reject) {
          previewAudio.onended = function() { URL.revokeObjectURL(url); previewAudio = null; resolve(); };
          previewAudio.onerror = function() { URL.revokeObjectURL(url); previewAudio = null; reject(new Error('Playback failed')); };
          previewAudio.play().catch(reject);
        });
      } catch (e) {
        ttsMsg.textContent = 'Preview failed: ' + e.message; ttsMsg.style.color = 'var(--red, #e55)';
        setTimeout(function() { ttsMsg.textContent = ''; }, 3000);
      } finally {
        resetPreview();
      }
    });
  }
}

export async function initSttSettings() {
  var provSel = el('set-sttProviderSelect');
  var modelSelect = el('set-sttModelSelect');
  var modelInput = el('set-sttModelInput');
  var _savedSttModel = '';   // saved STT model, used to seed the dropdown
  var modelRow = el('set-sttModelRow');
  var langRow = el('set-sttLangRow');
  var langInput = el('set-sttLangInput');
  var sttMsg = el('set-sttSettingsMsg');
  var sttEnabledToggle = el('set-sttEnabledToggle');
  var sttConfigWrap = el('set-sttConfigWrap');
  var azureNote = el('set-sttAzureNote');
  var elevenNote = el('set-sttElevenNote');
  // STT was removed from AI Defaults — bail if the UI isn't present.
  if (!provSel) return;

  function isEndpoint() { return provSel.value.startsWith('endpoint:'); }
  // Model uses a themed <select> of the endpoint's models; loadSttModelOptions()
  // owns select-vs-input (input is the fallback when the server can't enumerate).
  function getModel() {
    return modelSelect.style.display !== 'none' ? modelSelect.value : modelInput.value;
  }

  function updateVisibility() {
    var prov = provSel.value;
    var showModel = prov.startsWith('endpoint:');   // model only matters for endpoints
    var showLang = prov !== 'disabled';
    modelRow.style.display = showModel ? 'flex' : 'none';
    langRow.style.display = showLang ? 'flex' : 'none';
    if (azureNote) azureNote.style.display = prov === 'azure' ? 'block' : 'none';
    if (elevenNote) elevenNote.style.display = prov === 'elevenlabs' ? 'block' : 'none';
    if (!showModel) { modelSelect.style.display = 'none'; modelInput.style.display = 'none'; }
  }

  function syncSttDisabled() {
    var off = sttEnabledToggle && !sttEnabledToggle.checked;
    var card = sttEnabledToggle ? sttEnabledToggle.closest('.admin-card') : null;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (sttConfigWrap) sttConfigWrap.style.pointerEvents = off ? 'none' : '';
  }

  // Effective provider: if toggle is off, treat as disabled regardless of provider select
  function effectiveProvider() {
    if (sttEnabledToggle && !sttEnabledToggle.checked) return 'disabled';
    return provSel.value;
  }

  // Add API endpoints that might support STT
  try {
    var epRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    var endpoints = await epRes.json();
    var _enabledStt = (endpoints || []).filter(function(ep) { return ep.is_enabled; });
    // Prefer endpoints tagged model_type 'stt'; if none are tagged, show all so an
    // untagged Whisper server still appears.
    var _anyStt = _enabledStt.some(function(ep) { return ep.model_type === 'stt'; });
    var _seenStt = {};
    _enabledStt.forEach(function(ep) {
      if (_anyStt && ep.model_type !== 'stt') return;
      // Collapse the same server added more than once (delete extras in Added Models).
      var k = (ep.base_url || '') + '|' + (ep.name || '');
      if (_seenStt[k]) return;
      _seenStt[k] = 1;
      var opt = document.createElement('option'); opt.value = 'endpoint:' + ep.id; opt.textContent = ep.name + ' (API)'; provSel.appendChild(opt);
    });
  } catch (e) { console.warn('Failed to load endpoints for STT', e); }

  // Load saved settings
  try {
    var settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var settings = await settingsRes.json();
    if (settings.stt_provider) provSel.value = settings.stt_provider;
    if (settings.stt_model) { modelSelect.value = settings.stt_model; modelInput.value = settings.stt_model; _savedSttModel = settings.stt_model; }
    if (settings.stt_language) langInput.value = settings.stt_language;
    if (sttEnabledToggle) sttEnabledToggle.checked = settings.stt_enabled !== false;
  } catch (e) { console.warn('Failed to load STT settings', e); }

  syncSttDisabled();
  updateVisibility();

  async function saveSTT() {
    try {
      var enabled = sttEnabledToggle ? sttEnabledToggle.checked : false;
      await _postSettings({ stt_enabled: enabled, stt_provider: provSel.value, stt_model: getModel() || 'base', stt_language: langInput.value.trim() });
      sttMsg.textContent = 'Saved'; sttMsg.style.color = 'var(--fg)'; setTimeout(() => { sttMsg.textContent = ''; }, 2000);
      // Notify voiceRecorder of effective provider and update send button icon
      if (window.voiceRecorderModule) window.voiceRecorderModule._sttProvider = effectiveProvider();
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    } catch (e) { sttMsg.textContent = e.status === 403 ? 'Admin access is required to change these settings.' : 'Failed to save'; sttMsg.style.color = 'var(--red)'; }
  }

  // Populate the Model <select> from the configured STT endpoint (e.g. a
  // faster-whisper server's /v1/models) — a themed dropdown like the rest of the
  // app, with "(server default)" leaving the choice to the server. Falls back to
  // a free-text input if the server can't enumerate. `want` is the model to
  // select. Only runs for endpoint providers (the model row is hidden otherwise).
  async function loadSttModelOptions(want) {
    if (!isEndpoint()) return;
    if (want == null) want = getModel();
    var models = [];
    try {
      var res = await fetch('/api/stt/models', { credentials: 'same-origin' });
      var data = await res.json();
      models = (data && data.models) || [];
    } catch (e) { models = []; }
    if (models.length) {
      modelSelect.innerHTML = '';
      var def = document.createElement('option');
      def.value = ''; def.textContent = '(server default)';
      modelSelect.appendChild(def);
      var found = false;
      models.forEach(function(m) {
        if (!m || !m.value) return;
        var o = document.createElement('option');
        o.value = m.value; o.textContent = m.label || m.value;
        modelSelect.appendChild(o);
        if (m.value === want) found = true;
      });
      if (want && !found) {
        var o = document.createElement('option');
        o.value = want; o.textContent = want;
        modelSelect.appendChild(o);
      }
      modelSelect.value = want || '';
      modelSelect.style.display = ''; modelInput.style.display = 'none';
    } else {
      modelInput.value = want || '';
      modelInput.style.display = ''; modelSelect.style.display = 'none';
    }
  }

  provSel.addEventListener('change', function() { updateVisibility(); saveSTT().then(function() { return loadSttModelOptions(); }).then(saveSTT); });
  modelSelect.addEventListener('change', saveSTT);
  modelInput.addEventListener('change', saveSTT);
  langInput.addEventListener('change', saveSTT);
  if (sttEnabledToggle) sttEnabledToggle.addEventListener('change', function() { syncSttDisabled(); saveSTT(); });

  loadSttModelOptions(_savedSttModel);   // populate the model dropdown for the saved endpoint
}
