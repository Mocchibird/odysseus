// static/js/voiceRecorder.js

/**
 * Voice recording with optional Speech-to-Text transcription.
 *
 * STT providers:
 *   "disabled"       — record audio as file attachment (original behavior)
 *   "azure"          — send recording to server /api/stt/transcribe (Azure)
 *   "elevenlabs"     — send recording to server /api/stt/transcribe (Scribe)
 *   "endpoint:<id>"  — send recording to server /api/stt/transcribe (API)
 * Every transcribing provider runs server-side; the browser only captures audio.
 */

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingStartTime = null;
let recordingInterval = null;

// Voice-activity detection (hands-free auto-stop). Holds the Web Audio graph +
// polling timer while a VAD-enabled recording is active; null otherwise.
let _vad = null;

// Cached STT provider — refreshed on settings change
let _sttProvider = 'disabled';

// ── Silence detection (VAD) ──
// When conversation mode passes {vad:true}, watch the mic's RMS energy and
// auto-stop the recording once the user goes quiet for a beat AFTER speaking —
// so they don't have to tap to send. Manual stopRecording() still works.
const _VAD_SPEECH_RMS = 0.025;   // normalized RMS above this = speech
const _VAD_SILENCE_MS = 1100;    // continuous quiet after speech before auto-stop
const _VAD_MAX_MS = 30000;       // hard cap so a stuck mic can't record forever

function _teardownVad() {
  if (!_vad) return;
  try { if (_vad.timer) clearInterval(_vad.timer); } catch (e) {}
  try { if (_vad.source) _vad.source.disconnect(); } catch (e) {}
  try { if (_vad.analyser) _vad.analyser.disconnect(); } catch (e) {}
  try { if (_vad.ctx && _vad.ctx.state !== 'closed') _vad.ctx.close(); } catch (e) {}
  _vad = null;
}

function _startSilenceDetection(stream, onAutoStop) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;  // no Web Audio → stay push-to-talk (manual stop only)
  try {
    const ctx = new Ctx();
    // Track the context immediately so _teardownVad() can close it even if a
    // later setup call (createMediaStreamSource, etc.) throws.
    _vad = { ctx, source: null, analyser: null, timer: null };
    if (ctx.state === 'suspended') { try { ctx.resume(); } catch (e) {} }
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.8;
    source.connect(analyser);
    _vad.source = source;
    _vad.analyser = analyser;
    const buf = new Uint8Array(analyser.fftSize);
    const startedAt = Date.now();
    let hasSpoken = false;
    let silenceSince = 0;

    const timer = setInterval(() => {
      if (!_vad || !isRecording) return;
      analyser.getByteTimeDomainData(buf);
      let sumSq = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sumSq += v * v;
      }
      const rms = Math.sqrt(sumSq / buf.length);
      const now = Date.now();

      if (rms > _VAD_SPEECH_RMS) {
        hasSpoken = true;
        silenceSince = 0;
      } else if (hasSpoken) {
        if (!silenceSince) silenceSince = now;
        else if (now - silenceSince > _VAD_SILENCE_MS) {
          // Quiet long enough after speech → auto-send this turn.
          _teardownVad();
          try { if (typeof onAutoStop === 'function') onAutoStop(); } catch (e) {}
          stopRecording();
          return;
        }
      }
      // Safety cap: never hold the mic open indefinitely.
      if (now - startedAt > _VAD_MAX_MS && hasSpoken) {
        _teardownVad();
        try { if (typeof onAutoStop === 'function') onAutoStop(); } catch (e) {}
        stopRecording();
      }
    }, 100);

    _vad.timer = timer;
  } catch (e) {
    console.warn('VAD setup failed; falling back to manual stop:', e);
    _teardownVad();
  }
}

/**
 * Fetch current STT provider from server settings
 */
async function refreshSttProvider() {
  try {
    const res = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    if (res.ok) {
      const stats = await res.json();
      _sttProvider = stats.provider || 'disabled';
      // Notify the send button to update its icon
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    }
  } catch (e) {
    console.warn('Failed to fetch STT stats:', e);
  }
}

/**
 * Format seconds as MM:SS
 */
function formatTime(seconds) {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = (seconds % 60).toString().padStart(2, '0');
  return `${mins}:${secs}`;
}

/**
 * Reset UI state after recording ends
 */
function _resetRecordingUI() {
  isRecording = false;
  if (recordingInterval) {
    clearInterval(recordingInterval);
    recordingInterval = null;
  }
  // Reset send button via global callback
  const sendBtn = document.querySelector('.send-btn');
  if (sendBtn) {
    sendBtn.classList.remove('recording');
    sendBtn.dataset.mode = '';
  }
  if (window._updateSendBtnIcon) {
    setTimeout(window._updateSendBtnIcon, 50);
  }
}

/**
 * Send audio to server for transcription
 */
async function transcribeOnServer(audioBlob) {
  const formData = new FormData();
  formData.append('file', audioBlob, 'audio.webm');

  const res = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.message || 'Transcription failed');
  }

  const data = await res.json();
  return data.text || '';
}

/**
 * Insert transcribed text into the chat input
 */
// Conversation mode (or any caller) can intercept the transcript instead of
// inserting it into the composer — set via setTranscriptHook(fn) / clear with null.
let _transcriptHook = null;
export function setTranscriptHook(fn) { _transcriptHook = typeof fn === 'function' ? fn : null; }

function insertTranscription(text, showToast) {
  if (!text) return;
  if (_transcriptHook) { try { _transcriptHook(text); } catch (e) { console.warn('transcript hook failed', e); } return; }
  const input = document.getElementById('message');
  if (!input) return;

  const existing = input.value.trim();
  input.value = existing ? existing + ' ' + text : text;

  // Trigger auto-resize and icon update
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();

  if (showToast) showToast('Transcribed');
}

/**
 * Start voice recording
 */
export function startRecording(onFileCreated, showToast, showError, opts) {
  opts = opts || {};
  // Check for secure context (getUserMedia requires HTTPS or localhost)
  if (!window.isSecureContext) {
    if (showError) showError('Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.');
    _resetRecordingUI();
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    _resetRecordingUI();
    return;
  }

  audioChunks = [];
  _teardownVad();

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

      mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        _teardownVad();
        stream.getTracks().forEach(track => track.stop());

        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const provider = _sttProvider;

        if (provider === 'azure' || provider === 'elevenlabs' || provider.startsWith('endpoint:')) {
          // Show "Transcribing..." feedback
          if (showToast) showToast('Transcribing...', 5000);
          try {
            const transcript = await transcribeOnServer(audioBlob);
            if (transcript) {
              insertTranscription(transcript, showToast);
            } else {
              if (showToast) showToast('No speech detected');
            }
          } catch (e) {
            console.error('STT transcription error:', e);
            if (showError) showError('Transcription failed: ' + e.message);
            // Fallback: attach as file
            const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
            if (onFileCreated) onFileCreated(audioFile);
          }
        } else {
          // STT disabled — attach audio file
          const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
          if (onFileCreated) onFileCreated(audioFile);
        }

        _resetRecordingUI();
      };

      mediaRecorder.start();
      isRecording = true;
      recordingStartTime = new Date();

      // Hands-free: auto-stop once the user pauses after speaking.
      if (opts.vad) _startSilenceDetection(stream, opts.onAutoStop);

      if (showToast) {
        showToast('Recording...');
      }
    })
    .catch(error => {
      console.error('Microphone access error:', error);
      if (showError) {
        if (error.name === 'NotAllowedError') {
          showError('Microphone access denied. Check browser permissions.');
        } else if (error.name === 'NotFoundError') {
          showError('No microphone found.');
        } else {
          showError('Microphone error: ' + error.message);
        }
      }
      _resetRecordingUI();
    });
}

/**
 * Stop voice recording
 */
export function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    // VAD + UI reset happen in the onstop handler.
  } else {
    _teardownVad();
    _resetRecordingUI();
  }
}

/**
 * Check if currently recording
 */
export function getIsRecording() {
  return isRecording;
}

/**
 * Initialize recording state
 */
export function init() {
  isRecording = false;
  refreshSttProvider();
}

const voiceRecorderModule = {
  startRecording,
  stopRecording,
  getIsRecording,
  init,
  refreshSttProvider,
  setTranscriptHook,
  get _sttProvider() { return _sttProvider; },
  set _sttProvider(v) { _sttProvider = v; },
};

// Expose globally so settings.js can push the STT provider live when the user
// changes it (otherwise the composer mic button only updates after a reload).
if (typeof window !== 'undefined') window.voiceRecorderModule = voiceRecorderModule;

export default voiceRecorderModule;
