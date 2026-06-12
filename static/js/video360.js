/**
 * video360.js — dependency-free WebGL 360°/VR video viewer for the gallery.
 *
 * Renders a <video> as an equirectangular panorama via a single fullscreen-quad
 * fragment shader (per-pixel ray -> longitude/latitude -> texture sample). No
 * sphere geometry, no three.js — keeps it tiny and avoids the UV-seam artifacts
 * of a tessellated sphere.
 *
 * Layouts (manual toggle, since there's no reliable metadata for projection):
 *   - mono : full-frame equirectangular
 *   - sbs  : side-by-side stereo  -> shows the LEFT eye (u in [0, .5])
 *   - tb   : top-bottom  stereo   -> shows the TOP  eye (v in [0, .5])
 * Plus a 180° toggle (front hemisphere only; outside = black).
 *
 * On a flat screen a stereo file can't show true 3D, so we render one eye as a
 * normal pannable 360 view — drag to look, wheel to zoom, click to play/pause.
 *
 * The raw <video> keeps playing underneath (audio + frame source); the canvas
 * just sits on top. Toggling 360 off removes the canvas and restores native
 * controls. Only one viewer is ever live (WebGL contexts are a scarce resource),
 * so attach() detaches the previous one.
 */

let _active = null;

export function detach() {
  if (_active) { _active.destroy(); _active = null; }
}

export function attach(video, frame, opts) {
  detach();
  if (!video || !frame) return;
  try { _active = new Viewer360(video, frame, opts || {}); _active.detectAndMaybeShow(); }
  catch (e) { console.warn('video360 attach failed:', e); _active = null; }
}

const VS = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() { v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

const FS = `
precision highp float;
varying vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_yaw;
uniform float u_pitch;
uniform float u_fov;     // vertical field of view (radians)
uniform float u_aspect;  // canvas width / height
uniform int   u_layout;  // 0 mono, 1 side-by-side (left eye), 2 top-bottom (top eye)
uniform float u_half;    // 1.0 = 180°, 0.0 = 360°
uniform float u_flip;    // 1.0 = flip texture vertically (FLIP_Y compensation)
const float PI = 3.14159265358979;

void main() {
  float t = tan(u_fov * 0.5);
  // Camera-space ray for this pixel (looking down -Z).
  vec3 dir = normalize(vec3((v_uv.x * 2.0 - 1.0) * t * u_aspect,
                            (v_uv.y * 2.0 - 1.0) * t,
                            -1.0));
  // Pitch about X, then yaw about Y.
  float cp = cos(u_pitch), sp = sin(u_pitch);
  dir = vec3(dir.x, cp * dir.y - sp * dir.z, sp * dir.y + cp * dir.z);
  float cy = cos(u_yaw), sy = sin(u_yaw);
  dir = vec3(cy * dir.x + sy * dir.z, dir.y, -sy * dir.x + cy * dir.z);

  float lon = atan(dir.x, -dir.z);             // -PI .. PI
  float lat = asin(clamp(dir.y, -1.0, 1.0));   // -PI/2 .. PI/2

  float u, v;
  v = 0.5 - lat / PI;                          // 0 = up
  if (u_half > 0.5) {
    u = lon / PI + 0.5;                         // front hemisphere -> [0,1]
    if (u < 0.0 || u > 1.0) { gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }
  } else {
    u = fract(lon / (2.0 * PI) + 0.5);          // wrap the 360 seam
  }
  if (u_flip > 0.5) v = 1.0 - v;
  if (u_layout == 1) { u = u * 0.5; }            // side-by-side -> left eye
  else if (u_layout == 2) { v = (u_flip > 0.5) ? (0.5 + v * 0.5) : (v * 0.5); } // top-bottom -> top eye
  gl_FragColor = texture2D(u_tex, vec2(u, v));
}
`;

class Viewer360 {
  constructor(video, frame, opts) {
    this.video = video;
    this.frame = frame;
    this.opts = opts || {};
    this.enabled = false;
    this.layout = 'mono';
    this.is180 = false;
    this.yaw = 0;
    this.pitch = 0;
    this.fov = 75 * Math.PI / 180;
    this.flipY = false;
    this.raf = 0;
    this.canvas = null;
    this.gl = null;
    this.tex = null;
    this.lastVideoTime = -1;
    this._destroyed = false;
    this._onResize = () => this._resize();
    this._onFsChange = () => this._syncFullscreenBtn();
  }

  // Decide whether this is actually a 360 video; only then reveal the toggle.
  // Keeps the control off normal flat videos. Sets this.layout from the
  // detected stereo packing so the user doesn't have to pick mono/SBS/TB.
  async detectAndMaybeShow() {
    let det;
    try { det = await this._detect(); }
    catch (e) { det = { is360: false }; }
    if (this._destroyed) return;
    if (!det.is360) return;            // ordinary video -> no 360 UI
    this.layout = det.layout || 'mono';
    if (det.is180) this.is180 = true;
    this._buildControls();
  }

  async _detect() {
    const name = String(this.opts.name || '');
    const url = this.video.currentSrc || this.video.src || this.opts.url || '';
    // 1) Spherical-video metadata — the authoritative signal (also gives the
    //    stereo packing). Best-effort: a single small head range request; if the
    //    file isn't faststart we fall through to the heuristics below.
    try {
      const meta = await _fetchSpherical(url);
      if (meta && meta.spherical) {
        return { is360: true, layout: meta.stereo || 'mono', is180: meta.is180 };
      }
    } catch (e) { /* range/CORS/edge — fall through */ }
    // 2) Filename hints.
    const nameHit = /(^|[^a-z])(360|vr180|vr360|equirect(angular)?|insta360|gopromax|panoramic|spherical|monoscopic)([^a-z]|$)|_(tb|ou|lr|sbs)([^a-z]|$)/i.test(name);
    if (nameHit) {
      const layout = /(_lr|_sbs|left.?right|side.?by.?side)/i.test(name) ? 'sbs'
        : /(_tb|_ou|top.?bottom|over.?under)/i.test(name) ? 'tb' : 'mono';
      return { is360: true, layout, is180: /(^|[^0-9])180([^0-9]|$)|vr180/i.test(name) };
    }
    // 3) Aspect ratio of the decoded frame (equirect mono = 2:1, SBS = 4:1).
    const ar = await this._aspect();
    if (ar) {
      if (Math.abs(ar - 2) < 0.08) return { is360: true, layout: 'mono' };
      if (Math.abs(ar - 4) < 0.2) return { is360: true, layout: 'sbs' };
    }
    return { is360: false };
  }

  _aspect() {
    const v = this.video;
    if (v.videoWidth && v.videoHeight) return Promise.resolve(v.videoWidth / v.videoHeight);
    return new Promise((res) => {
      const done = () => { v.removeEventListener('loadedmetadata', done); res(v.videoWidth && v.videoHeight ? v.videoWidth / v.videoHeight : 0); };
      v.addEventListener('loadedmetadata', done);
      setTimeout(done, 4000); // don't hang forever on a stalled load
    });
  }

  // ---- control bar (reuses existing widget classes + theme vars) ----
  _buildControls() {
    const bar = document.createElement('div');
    bar.className = 'video360-bar';
    bar.style.cssText = 'position:absolute;top:8px;left:50%;transform:translateX(-50%);z-index:3;'
      + 'display:flex;align-items:center;gap:6px;padding:4px 6px;'
      + 'background:color-mix(in srgb, var(--panel) 88%, transparent);'
      + 'border:1px solid var(--border);border-radius:8px;';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'memory-toolbar-btn';
    toggle.title = 'Toggle 360° / VR view';
    toggle.innerHTML = _ICON_360 + '<span style="margin-left:4px">360°</span>';
    toggle.addEventListener('click', () => this._setEnabled(!this.enabled));
    this.toggleBtn = toggle;

    const opts = document.createElement('div');
    opts.style.cssText = 'display:none;align-items:center;gap:6px;';
    this.optsWrap = opts;

    const sel = document.createElement('select');
    sel.className = 'gallery-tag-input';
    sel.style.cssText = 'padding:3px 6px;height:28px;width:auto;';
    sel.title = 'Stereo layout';
    sel.innerHTML = '<option value="mono">Mono</option>'
      + '<option value="sbs">Side-by-side (L/R)</option>'
      + '<option value="tb">Top-bottom</option>';
    sel.addEventListener('change', () => { this.layout = sel.value; });
    sel.value = this.layout;                       // preselect the detected packing
    opts.appendChild(sel);

    const half = document.createElement('button');
    half.type = 'button';
    half.className = 'memory-toolbar-btn';
    half.title = 'Toggle 180° (front hemisphere) vs full 360°';
    half.textContent = '180°';
    half.classList.toggle('active', this.is180);
    half.addEventListener('click', () => {
      this.is180 = !this.is180;
      half.classList.toggle('active', this.is180);
    });
    opts.appendChild(half);

    const fs = document.createElement('button');
    fs.type = 'button';
    fs.className = 'memory-toolbar-btn';
    fs.title = 'Fullscreen';
    fs.innerHTML = _ICON_EXPAND;
    fs.addEventListener('click', () => this._toggleFullscreen());
    this.fsBtn = fs;

    bar.appendChild(toggle);
    bar.appendChild(opts);
    bar.appendChild(fs);
    if (getComputedStyle(this.frame).position === 'static') this.frame.style.position = 'relative';
    this.frame.appendChild(bar);
    this.bar = bar;
    document.addEventListener('fullscreenchange', this._onFsChange);
    document.addEventListener('webkitfullscreenchange', this._onFsChange);
  }

  _toggleFullscreen() {
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fsEl === this.frame) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      try { Promise.resolve(exit.call(document)).catch(() => {}); } catch (e) { /* noop */ }
    } else {
      const req = this.frame.requestFullscreen || this.frame.webkitRequestFullscreen;
      if (req) {
        this.frame.dataset._bg = this.frame.style.background || '';
        this.frame.style.background = '#000';
        try { Promise.resolve(req.call(this.frame)).catch(() => { this.frame.style.background = this.frame.dataset._bg || ''; }); }
        catch (e) { this.frame.style.background = this.frame.dataset._bg || ''; }
      }
    }
  }

  _syncFullscreenBtn() {
    const on = (document.fullscreenElement || document.webkitFullscreenElement) === this.frame;
    if (this.fsBtn) { this.fsBtn.innerHTML = on ? _ICON_COMPRESS : _ICON_EXPAND; this.fsBtn.classList.toggle('active', on); }
    if (!on && this.frame) this.frame.style.background = this.frame.dataset._bg || '';
    this._resize();
  }

  _setEnabled(on) {
    if (on === this.enabled) return;
    this.enabled = on;
    this.toggleBtn.classList.toggle('active', on);
    this.optsWrap.style.display = on ? 'flex' : 'none';
    if (on) this._enable(); else this._disable();
  }

  _enable() {
    const c = document.createElement('canvas');
    c.className = 'video360-canvas';
    c.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;z-index:1;cursor:grab;touch-action:none;';
    this.frame.appendChild(c);
    this.canvas = c;
    const gl = c.getContext('webgl', { alpha: false, antialias: true, preserveDrawingBuffer: false })
      || c.getContext('experimental-webgl');
    if (!gl) { console.warn('WebGL unavailable — 360 view disabled'); this._setEnabled(false); return; }
    this.gl = gl;
    this._initGL();
    this._bindPointer();
    this.frame.classList.add('video360-on');
    this._resize();
    this._ro = ('ResizeObserver' in window) ? new ResizeObserver(this._onResize) : null;
    if (this._ro) this._ro.observe(this.frame);
    else window.addEventListener('resize', this._onResize);
    const loop = () => { this.raf = requestAnimationFrame(loop); this._render(); };
    this.raf = requestAnimationFrame(loop);
  }

  _disable() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this._ro) { this._ro.disconnect(); this._ro = null; }
    else window.removeEventListener('resize', this._onResize);
    if (this.gl) {
      const lose = this.gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    }
    this.gl = null; this.tex = null; this.prog = null;
    if (this.canvas) { this.canvas.remove(); this.canvas = null; }
    this.frame.classList.remove('video360-on');
  }

  destroy() {
    this._destroyed = true;
    this._disable();
    document.removeEventListener('fullscreenchange', this._onFsChange);
    document.removeEventListener('webkitfullscreenchange', this._onFsChange);
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fsEl === this.frame) { try { (document.exitFullscreen || document.webkitExitFullscreen).call(document); } catch (e) { /* noop */ } }
    if (this.bar) this.bar.remove();
    this.bar = null;
  }

  _initGL() {
    const gl = this.gl;
    const vs = _compile(gl, gl.VERTEX_SHADER, VS);
    const fs = _compile(gl, gl.FRAGMENT_SHADER, FS);
    const prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || 'link failed');
    gl.useProgram(prog);
    this.prog = prog;

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'a_pos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    this.u = {
      tex: gl.getUniformLocation(prog, 'u_tex'),
      yaw: gl.getUniformLocation(prog, 'u_yaw'),
      pitch: gl.getUniformLocation(prog, 'u_pitch'),
      fov: gl.getUniformLocation(prog, 'u_fov'),
      aspect: gl.getUniformLocation(prog, 'u_aspect'),
      layout: gl.getUniformLocation(prog, 'u_layout'),
      half: gl.getUniformLocation(prog, 'u_half'),
      flip: gl.getUniformLocation(prog, 'u_flip'),
    };

    this.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.uniform1i(this.u.tex, 0);
  }

  _resize() {
    if (!this.canvas || !this.gl) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(this.frame.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.frame.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w; this.canvas.height = h;
      this.gl.viewport(0, 0, w, h);
    }
  }

  _render() {
    const gl = this.gl, v = this.video;
    if (!gl) return;
    // Self-correct sizing every frame — covers a frame that had no layout when
    // 360 was first enabled (a ResizeObserver can miss the 0->N transition).
    // Cheap: _resize only touches the canvas/GL when the size actually changes.
    this._resize();
    // Upload the current video frame only when it advances (cheap idle).
    if (v && v.readyState >= 2 && v.videoWidth) {
      if (v.currentTime !== this.lastVideoTime) {
        this.lastVideoTime = v.currentTime;
        gl.bindTexture(gl.TEXTURE_2D, this.tex);
        try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, v); }
        catch (e) { /* transient (e.g. not yet decodable) */ }
      }
    }
    gl.uniform1f(this.u.yaw, this.yaw);
    gl.uniform1f(this.u.pitch, this.pitch);
    gl.uniform1f(this.u.fov, this.fov);
    gl.uniform1f(this.u.aspect, this.canvas.width / this.canvas.height);
    gl.uniform1i(this.u.layout, this.layout === 'sbs' ? 1 : this.layout === 'tb' ? 2 : 0);
    gl.uniform1f(this.u.half, this.is180 ? 1 : 0);
    gl.uniform1f(this.u.flip, this.flipY ? 1 : 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  _bindPointer() {
    const c = this.canvas;
    let dragging = false, lx = 0, ly = 0, moved = 0;
    const down = (e) => { dragging = true; moved = 0; lx = e.clientX; ly = e.clientY; c.style.cursor = 'grabbing'; c.setPointerCapture && c.setPointerCapture(e.pointerId); };
    const move = (e) => {
      if (!dragging) return;
      const dx = e.clientX - lx, dy = e.clientY - ly;
      lx = e.clientX; ly = e.clientY; moved += Math.abs(dx) + Math.abs(dy);
      const k = this.fov / this.canvas.clientHeight; // pixels -> radians (zoom-aware)
      this.yaw -= dx * k;
      this.pitch = Math.max(-Math.PI / 2 + 0.01, Math.min(Math.PI / 2 - 0.01, this.pitch - dy * k));
    };
    const up = () => {
      if (dragging && moved < 6) { if (this.video.paused) this.video.play().catch(() => {}); else this.video.pause(); }
      dragging = false; c.style.cursor = 'grab';
    };
    const wheel = (e) => { e.preventDefault(); this.fov = Math.max(0.5, Math.min(1.9, this.fov + (e.deltaY > 0 ? 0.05 : -0.05))); };
    c.addEventListener('pointerdown', down);
    c.addEventListener('pointermove', move);
    c.addEventListener('pointerup', up);
    c.addEventListener('pointerleave', up);
    c.addEventListener('wheel', wheel, { passive: false });
  }
}

function _compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src); gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) || 'shader compile failed');
  return sh;
}

// Read the head of an MP4 and look for Google spatial-media markers (the
// spherical-video v1 XML "Spherical" token, or the v2 'sv3d'/'st3d' boxes).
// A single bounded Range request — if the server ignores Range (200, not 206)
// we bail rather than pull a whole multi-GB file.
async function _fetchSpherical(url) {
  if (!url) return null;
  const res = await fetch(url, { headers: { Range: 'bytes=0-524287' }, credentials: 'same-origin' });
  if (res.status !== 206) { try { res.body && res.body.cancel(); } catch (e) { /* noop */ } return null; }
  const buf = new Uint8Array(await res.arrayBuffer());
  return _scanSpherical(buf);
}

function _scanSpherical(b) {
  const find = (s) => {
    const n = s.length;
    outer: for (let i = 0; i + n <= b.length; i++) {
      for (let j = 0; j < n; j++) if (b[i + j] !== s.charCodeAt(j)) continue outer;
      return i;
    }
    return -1;
  };
  const hasSv3d = find('sv3d') >= 0;
  const hasXml = find('Spherical') >= 0;            // v1 metadata XML token
  if (!hasSv3d && !hasXml) return { spherical: false };
  let stereo = 'mono';
  const st3d = find('st3d');                         // v2 stereo box
  if (st3d >= 0 && st3d + 8 < b.length) {
    // box = [size:4][type 'st3d':4][version+flags:4][stereo_mode:1]
    const mode = b[st3d + 8];
    stereo = mode === 1 ? 'tb' : mode === 2 ? 'sbs' : 'mono';
  } else if (hasXml) {
    const sm = find('StereoMode');
    if (sm >= 0) {
      const slice = String.fromCharCode.apply(null, b.subarray(sm, Math.min(sm + 64, b.length))).toLowerCase();
      stereo = /top-?bottom/.test(slice) ? 'tb' : /left-?right/.test(slice) ? 'sbs' : 'mono';
    }
  }
  return { spherical: true, stereo, is180: false };
}

const _ICON_360 = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><ellipse cx="12" cy="12" rx="10" ry="5"/><path d="M2 12a10 5 0 0 0 20 0"/><path d="M12 2a5 10 0 0 0 0 20"/></svg>';
const _ICON_EXPAND = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>';
const _ICON_COMPRESS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/><path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/></svg>';
