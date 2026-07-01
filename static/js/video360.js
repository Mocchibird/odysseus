/**
 * video360.js — 360°/VR video viewer for the gallery, rendered with three.js.
 *
 * Renders the gallery <video> as an equirectangular panorama on an inward-facing
 * sphere via three.js VideoTexture. three.js handles the iOS/WebKit video-texture
 * quirks (playsinline, RGBA upload, per-frame refresh) that a hand-rolled WebGL
 * path kept tripping over. three.js is vendored at /static/lib/three.module.min.js
 * and lazy-imported only when a 360 video is actually viewed.
 *
 * Layouts (manual toggle — no reliable projection metadata):
 *   - mono : full-frame equirectangular
 *   - sbs  : side-by-side stereo  -> shows the LEFT eye (texture left half)
 *   - tb   : top-bottom  stereo   -> shows the TOP  eye (texture top half)
 * Plus a 180° toggle (front hemisphere only).
 *
 * The raw <video> keeps playing underneath (audio + frame source); the three.js
 * canvas sits on top. Drag to look, wheel to zoom, tap to play/pause. Only one
 * viewer is ever live, so attach() detaches the previous one.
 */

const THREE_URL = '/static/lib/three.module.min.js';
let _threePromise = null;
function _loadThree() {
  if (!_threePromise) _threePromise = import(THREE_URL);
  return _threePromise;
}

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
    this.raf = 0;
    this.canvas = null;
    this.THREE = null;
    this.renderer = null;
    this.scene = null;
    this.camera = null;
    this.mesh = null;
    this.tex = null;
    this._destroyed = false;
    this._pseudoFs = false;
    this._onFsKey = null;
    this._onResize = () => this._resize();
    this._onFsChange = () => this._onFullscreenChange();
  }

  // Decide whether this is actually a 360 video; only then reveal the toggle.
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
    //    stereo packing). Best-effort: a single small head range request.
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
    sel.addEventListener('change', () => { this.layout = sel.value; if (this.enabled) this._applyStereo(); });
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
      if (this.enabled) this._buildSphere();
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
    // Sync the button + drop the fullscreen class when the user leaves real
    // fullscreen via Esc / the system gesture (not our button).
    document.addEventListener('fullscreenchange', this._onFsChange);
    document.addEventListener('webkitfullscreenchange', this._onFsChange);
  }

  _onFullscreenChange() {
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fsEl !== this.frame && !this._pseudoFs) {
      // Left real fullscreen externally — clean up the landscape lock + class.
      this.frame.classList.remove('video360-fullscreen');
      try { if (screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); }
      catch (e) { /* noop */ }
    }
    this._syncFullscreenBtn();
  }

  // Fullscreen, best-effort across platforms:
  //  - Real Fullscreen API + lock to landscape where supported (desktop,
  //    Android, iPadOS, and iOS when installed as a standalone PWA) — this is
  //    the "normal" fullscreen the user expects, rotated to landscape.
  //  - iPhone Safari TABS expose no Fullscreen API for a <div>/<canvas> (only
  //    <video>), and orientation lock only works inside real fullscreen, so we
  //    fall back to CSS pseudo-fullscreen + a "rotate to landscape" hint.
  _isFs() {
    return this._pseudoFs ||
      (document.fullscreenElement || document.webkitFullscreenElement) === this.frame;
  }

  async _toggleFullscreen() {
    if (this._isFs()) { this._exitFullscreen(); return; }
    const req = this.frame.requestFullscreen || this.frame.webkitRequestFullscreen;
    if (req) {
      try {
        await Promise.resolve(req.call(this.frame));
        this.frame.classList.add('video360-fullscreen');  // background + sizing
        this._lockLandscape();
        this._syncFullscreenBtn();
        this._resize();
        return;
      } catch (e) { /* API present but refused (iPhone tab) → pseudo-fs */ }
    }
    this._enterPseudoFs();
  }

  _exitFullscreen() {
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fsEl === this.frame) {
      try { (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
      catch (e) { /* noop */ }
    }
    try { if (screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); }
    catch (e) { /* noop */ }
    this.frame.classList.remove('video360-fullscreen');
    if (this._pseudoFs) this._exitPseudoFs();
    else this._syncFullscreenBtn();
  }

  _lockLandscape() {
    try {
      const o = screen.orientation;
      if (o && o.lock) Promise.resolve(o.lock('landscape')).catch(() => {});
    } catch (e) { /* not supported (iPhone Safari tab) — user rotates manually */ }
  }

  _enterPseudoFs() {
    if (this._pseudoFs) return;
    this._pseudoFs = true;
    this.frame.classList.add('video360-fullscreen');
    this._onFsKey = (e) => { if (e.key === 'Escape') this._exitPseudoFs(); };
    document.addEventListener('keydown', this._onFsKey);
    this._syncFullscreenBtn();
    this._resize();
  }

  _exitPseudoFs() {
    if (!this._pseudoFs) return;
    this._pseudoFs = false;
    this.frame.classList.remove('video360-fullscreen');
    if (this._onFsKey) { document.removeEventListener('keydown', this._onFsKey); this._onFsKey = null; }
    this._syncFullscreenBtn();
    this._resize();
  }

  _syncFullscreenBtn() {
    const on = this._isFs();
    if (this.fsBtn) { this.fsBtn.innerHTML = on ? _ICON_COMPRESS : _ICON_EXPAND; this.fsBtn.classList.toggle('active', on); }
    this._resize();
  }

  _setEnabled(on) {
    if (on === this.enabled) return;
    this.enabled = on;
    this.toggleBtn.classList.toggle('active', on);
    this.optsWrap.style.display = on ? 'flex' : 'none';
    if (on) this._enable(); else this._disable();
  }

  async _enable() {
    // Play SYNCHRONOUSLY inside the toggle's tap (a user gesture), BEFORE any
    // await — iOS revokes the gesture across an await and would reject play(),
    // leaving the video paused (black + no audio). three.js then textures the
    // now-playing video.
    try {
      this.video.setAttribute('playsinline', '');
      this.video.setAttribute('webkit-playsinline', '');
      const p = this.video.play();
      if (p && p.catch) p.catch(() => {});
    } catch (e) { /* user can tap the view to play */ }

    let THREE;
    try { THREE = await _loadThree(); }
    catch (e) { console.warn('three.js failed to load — 360 disabled', e); this._setEnabled(false); return; }
    if (!this.enabled || this._destroyed) return;  // toggled off / closed during load
    this.THREE = THREE;

    const renderer = new THREE.WebGLRenderer({ alpha: false, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const c = renderer.domElement;
    c.className = 'video360-canvas';
    c.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;z-index:1;cursor:grab;touch-action:none;';
    // A look-around drag must NOT be read as a swipe-to-dismiss by the modal
    // sheet (ui.js) — that was closing the whole gallery on a fast swipe.
    c.dataset.noSwipeDismiss = '';
    this.frame.appendChild(c);
    this.canvas = c;
    this.renderer = renderer;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(this.fov * 180 / Math.PI, 1, 0.1, 1100);
    this.camera.rotation.order = 'YXZ';

    const tex = new THREE.VideoTexture(this.video);
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;
    if ('colorSpace' in tex) tex.colorSpace = THREE.SRGBColorSpace;
    this.tex = tex;

    this._buildSphere();
    this.frame.classList.add('video360-on');
    this._bindPointer();
    this._resize();

    this._ro = ('ResizeObserver' in window) ? new ResizeObserver(this._onResize) : null;
    if (this._ro) this._ro.observe(this.frame);
    else window.addEventListener('resize', this._onResize);

    const loop = () => { this.raf = requestAnimationFrame(loop); this._render(); };
    this.raf = requestAnimationFrame(loop);
  }

  // (Re)build the sphere mesh for the current 360-vs-180 mode. Equirect maps
  // onto a sphere viewed from the inside (scale x -1).
  _buildSphere() {
    const THREE = this.THREE;
    if (!THREE || !this.scene) return;
    if (this.mesh) {
      this.scene.remove(this.mesh);
      this.mesh.geometry.dispose();
      this.mesh.material.dispose();
      this.mesh = null;
    }
    const R = 500;
    const geo = this.is180
      ? new THREE.SphereGeometry(R, 60, 40, -Math.PI / 2, Math.PI)  // front hemisphere
      : new THREE.SphereGeometry(R, 60, 40);
    geo.scale(-1, 1, 1);  // view from inside
    const mat = new THREE.MeshBasicMaterial({ map: this.tex });
    this.mesh = new THREE.Mesh(geo, mat);
    this.scene.add(this.mesh);
    this._applyStereo();
  }

  // Crop the texture to one eye for stereo packings (flat screen can't show 3D).
  _applyStereo() {
    const t = this.tex;
    if (!t) return;
    if (this.layout === 'sbs') { t.repeat.set(0.5, 1); t.offset.set(0, 0); }       // left eye
    else if (this.layout === 'tb') { t.repeat.set(1, 0.5); t.offset.set(0, 0.5); } // top eye
    else { t.repeat.set(1, 1); t.offset.set(0, 0); }
    t.needsUpdate = true;
  }

  _resize() {
    if (!this.renderer || !this.camera) return;
    const w = this.frame.clientWidth, h = this.frame.clientHeight;
    if (!w || !h) return;
    // Skip when unchanged: _render() calls this every frame to catch the iOS
    // 0->N first-frame transition the ResizeObserver can miss, but re-issuing
    // setSize + updateProjectionMatrix ~60x/s at a steady size is wasted work.
    if (w === this._lastW && h === this._lastH) return;
    this._lastW = w; this._lastH = h;
    this.renderer.setSize(w, h, false);  // false: keep our 100%/inset CSS sizing
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _render() {
    if (!this.renderer) return;
    this._resize();  // cheap now (no-ops at steady size); covers the iOS 0->N first frame
    this.camera.fov = this.fov * 180 / Math.PI;
    this.camera.rotation.y = this.yaw;
    this.camera.rotation.x = this.pitch;
    this.camera.updateProjectionMatrix();
    // VideoTexture refreshes from the playing <video> automatically each render.
    this.renderer.render(this.scene, this.camera);
  }

  _disable() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this._ro) { this._ro.disconnect(); this._ro = null; }
    else window.removeEventListener('resize', this._onResize);
    try {
      if (this.mesh) { this.mesh.geometry.dispose(); this.mesh.material.dispose(); }
      if (this.tex) this.tex.dispose();
      if (this.renderer) this.renderer.dispose();
    } catch (e) { /* noop */ }
    this.mesh = null; this.tex = null; this.scene = null; this.camera = null;
    this.renderer = null;
    if (this.canvas) { this.canvas.remove(); this.canvas = null; }
    this.frame.classList.remove('video360-on');
  }

  destroy() {
    this._destroyed = true;
    this._disable();
    this._exitFullscreen();
    document.removeEventListener('fullscreenchange', this._onFsChange);
    document.removeEventListener('webkitfullscreenchange', this._onFsChange);
    if (this._onFsKey) { document.removeEventListener('keydown', this._onFsKey); this._onFsKey = null; }
    if (this.bar) this.bar.remove();
    this.bar = null;
  }

  _bindPointer() {
    const c = this.canvas;
    let dragging = false, lx = 0, ly = 0, moved = 0;
    const down = (e) => { e.stopPropagation(); dragging = true; moved = 0; lx = e.clientX; ly = e.clientY; c.style.cursor = 'grabbing'; c.setPointerCapture && c.setPointerCapture(e.pointerId); };
    const move = (e) => {
      if (!dragging) return;
      e.stopPropagation();
      const dx = e.clientX - lx, dy = e.clientY - ly;
      lx = e.clientX; ly = e.clientY; moved += Math.abs(dx) + Math.abs(dy);
      const k = this.fov / (this.canvas.clientHeight || 1); // pixels -> radians (zoom-aware)
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

// Read the head of an MP4 and look for Google spatial-media markers (the
// spherical-video v1 XML "Spherical" token, or the v2 'sv3d'/'st3d' boxes).
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
