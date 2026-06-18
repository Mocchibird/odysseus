// static/js/conversationMode.js
//
// Fork addon: push-to-talk voice "conversation mode" — a full-screen overlay
// that drives the SAME chat underneath. Tap the orb to speak, tap again to send;
// Odysseus's reply auto-speaks; tap to speak the next turn. The chat keeps
// running normally beneath the overlay (we submit the real #chat-form), so
// history, tools, RAG, agent mode all keep working.
//
// Provider-agnostic: STT is whatever voiceRecorder.js is configured for
// (Azure / ElevenLabs / endpoint), TTS is window.aiTTSManager
// (Edge / Azure / ElevenLabs / endpoint). No chat.js changes — completion is read
// from the send button's streaming state + the TTS manager's isPlaying flag.
//
// Loaded bare from index.html (no ?v, self-revalidates). Mic capture requires a
// secure context (HTTPS or localhost).

import { startRecording, stopRecording, getIsRecording, setTranscriptHook } from './voiceRecorder.js';
import voiceRecorder from './voiceRecorder.js';

let _overlay = null;
let _open = false;
let _state = 'idle';        // idle | listening | processing
let _poll = null;
let _prevAutoPlay = false;
let _sentThisTurn = false;
let _turnStartedAt = 0;

const _STATUS = {
  idle: 'Tap to speak',
  listening: 'Listening… tap to send',
  transcribing: 'Transcribing…',
  thinking: 'Thinking…',
  speaking: 'Speaking… tap to skip',
};

function _tts() { return window.aiTTSManager || null; }
function _sttEnabled() {
  const p = voiceRecorder && voiceRecorder._sttProvider;
  return !!(p && p !== 'disabled');
}
function _sendBtn() { return document.querySelector('.send-btn'); }
function _isStreaming() { const b = _sendBtn(); return !!(b && b.dataset && b.dataset.mode === 'streaming'); }
function _isSpeaking() { const t = _tts(); return !!(t && t.isPlaying); }

// ── launch button (injected into the composer's right-side controls) ──
function _injectButton() {
  if (document.getElementById('conv-mode-btn')) return;
  const right = document.querySelector('.chat-input-right');
  if (!right) return;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'conv-mode-btn';
  btn.className = 'conv-mode-btn';
  btn.title = 'Voice conversation';
  btn.setAttribute('aria-label', 'Voice conversation');
  btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
  btn.addEventListener('click', open);
  right.insertBefore(btn, right.firstChild);
}

// ── overlay ──
function _buildOverlay() {
  const o = document.createElement('div');
  o.id = 'conv-overlay';
  o.className = 'conv-overlay hidden';
  o.dataset.state = 'idle';
  o.innerHTML =
    '<button type="button" class="conv-close" id="conv-close" title="Close (Esc)" aria-label="Close voice conversation">' +
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>' +
    '</button>' +
    '<div class="conv-body">' +
      '<button type="button" class="conv-orb" id="conv-orb" aria-label="Tap to speak">' +
        '<span class="conv-orb-ring" aria-hidden="true"></span>' +
        '<svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>' +
      '</button>' +
      '<div class="conv-status" id="conv-status">Tap to speak</div>' +
      '<div class="conv-transcript" id="conv-transcript"></div>' +
    '</div>';
  document.body.appendChild(o);
  o.querySelector('#conv-close').addEventListener('click', close);
  o.querySelector('#conv-orb').addEventListener('click', _onOrbTap);
  return o;
}

function _status(text) {
  const st = _overlay && _overlay.querySelector('#conv-status');
  if (st && text != null) st.textContent = text;
}
function _setState(s, text) {
  _state = s;
  if (_overlay) _overlay.dataset.state = s;
  _status(text);
}
function _showTranscript(text) {
  const el = _overlay && _overlay.querySelector('#conv-transcript');
  if (el) el.textContent = text ? '“' + text + '”' : '';
}

function _onOrbTap() {
  if (!_open) return;
  if (_state === 'idle') {
    if (!window.isSecureContext) { _status('Microphone needs HTTPS'); return; }
    if (!_sttEnabled()) { _status('Enable Speech-to-Text in Settings → AI Defaults'); return; }
    _showTranscript('');
    startRecording(null, function () {}, function (msg) { _setState('idle', String(msg || 'Error')); });
    _setState('listening', _STATUS.listening);
  } else if (_state === 'listening') {
    stopRecording();
    _sentThisTurn = false;
    _turnStartedAt = Date.now();
    _setState('processing', _STATUS.transcribing);
  } else if (_state === 'processing' && _isSpeaking()) {
    // tap during playback = skip the rest of this reply
    try { _tts().stop(); } catch (e) {}
    _setState('idle', _STATUS.idle);
  }
}

// transcript ready → show it + send through the normal chat flow
function _onTranscript(text) {
  text = (text || '').trim();
  if (!_open) return;
  if (!text) { _setState('idle', 'No speech detected'); return; }
  _showTranscript(text);
  const input = document.getElementById('message');
  const form = document.getElementById('chat-form');
  if (!input || !form) { _setState('idle', 'Chat not ready'); return; }
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  try { form.requestSubmit(); }
  catch (e) { const b = _sendBtn(); if (b) b.click(); }
  _sentThisTurn = true;
  _turnStartedAt = Date.now();
  _setState('processing', _STATUS.thinking);
}

// poll: drive the processing sub-status + return to idle when the turn ends
function _tick() {
  if (!_open || _state !== 'processing') return;
  const streaming = _isStreaming();
  const speaking = _isSpeaking();
  if (streaming) { _status(_STATUS.thinking); return; }
  if (speaking) { _status(_STATUS.speaking); return; }
  const waited = Date.now() - _turnStartedAt;
  if (!_sentThisTurn) {
    // still transcribing (server STT) — bail out if it never produces text
    if (waited > 9000) _setState('idle', 'No speech detected');
    return;
  }
  // sent, not streaming, not speaking: grace period for autoplay TTS to start,
  // then the turn is done.
  if (waited > 2500) _setState('idle', _STATUS.idle);
}

export function open() {
  if (_open) return;
  _open = true;
  if (!_overlay) _overlay = _buildOverlay();
  _overlay.classList.remove('hidden');
  document.body.classList.add('conv-mode-active');
  const t = _tts();
  if (t) { _prevAutoPlay = t.autoPlay; t.autoPlay = true; }   // auto-speak replies while in voice mode
  setTranscriptHook(_onTranscript);
  _showTranscript('');
  _setState('idle', _sttEnabled() ? _STATUS.idle : 'Enable Speech-to-Text in Settings → AI Defaults');
  // Re-fetch the STT provider in case the user just enabled it in Settings
  // (the recorder caches it on load); refresh the status once it resolves.
  try {
    const p = voiceRecorder.refreshSttProvider && voiceRecorder.refreshSttProvider();
    if (p && p.then) p.then(() => { if (_open && _state === 'idle') _status(_sttEnabled() ? _STATUS.idle : 'Enable Speech-to-Text in Settings → AI Defaults'); });
  } catch (e) {}
  _poll = setInterval(_tick, 300);
  document.addEventListener('keydown', _onKey, true);
}

export function close() {
  if (!_open) return;
  _open = false;
  try { if (getIsRecording()) stopRecording(); } catch (e) {}
  try { _tts() && _tts().stop(); } catch (e) {}
  setTranscriptHook(null);
  const t = _tts();
  if (t) t.autoPlay = _prevAutoPlay;
  if (_poll) { clearInterval(_poll); _poll = null; }
  document.removeEventListener('keydown', _onKey, true);
  if (_overlay) _overlay.classList.add('hidden');
  document.body.classList.remove('conv-mode-active');
  _setState('idle', _STATUS.idle);
}

function _onKey(e) { if (e.key === 'Escape') { e.preventDefault(); close(); } }

function _start() { _injectButton(); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _start);
else _start();

const conversationMode = { open, close };
export default conversationMode;
