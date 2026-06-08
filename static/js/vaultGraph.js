// vaultGraph.js — lightweight, dependency-free canvas force-directed graph for
// the vault backlink view. Canvas (not SVG) for performance + headroom; a simple
// O(n²) force sim is fine for the few-hundred-node personal vaults this targets.
// Pointer events so pan/drag/tap work on touch too.

export function createVaultGraph(container, opts = {}) {
  const onOpen = opts.onOpen || (() => {});
  const nodes = (opts.nodes || []).map((n, i) => {
    const a = (i / Math.max(1, (opts.nodes || []).length)) * Math.PI * 2;
    return { ...n, x: Math.cos(a) * 120 + (Math.random() - 0.5) * 30, y: Math.sin(a) * 120 + (Math.random() - 0.5) * 30, vx: 0, vy: 0 };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links = (opts.links || [])
    .map((l) => ({ source: byId.get(l.source), target: byId.get(l.target) }))
    .filter((l) => l.source && l.target);
  const maxDeg = Math.max(1, ...nodes.map((n) => n.deg || 0));

  const canvas = document.createElement('canvas');
  canvas.className = 'vault-graph-canvas';
  container.innerHTML = '';
  container.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W = 0, H = 0, tx = 0, ty = 0, scale = 1, alpha = 1, raf = 0, destroyed = false, hover = null, centered = false;

  function resize() {
    const r = container.getBoundingClientRect();
    W = r.width; H = r.height;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    if (!centered && W && H) { tx = W / 2; ty = H / 2; centered = true; }
  }
  const radius = (n) => 3 + 6 * Math.sqrt((n.deg || 0) / maxDeg);

  function tick() {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const d = Math.sqrt(d2), f = 700 / d2;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
      a.vx += -a.x * 0.003; a.vy += -a.y * 0.003;  // gravity toward origin
    }
    for (const l of links) {
      let dx = l.target.x - l.source.x, dy = l.target.y - l.source.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - 64) * 0.02;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      l.source.vx += fx; l.source.vy += fy; l.target.vx -= fx; l.target.vy -= fy;
    }
    for (const n of nodes) {
      if (n.fixed) { n.vx = n.vy = 0; continue; }
      n.vx *= 0.85; n.vy *= 0.85;
      n.x += n.vx * alpha; n.y += n.vy * alpha;
    }
    alpha *= 0.992;
  }

  function css(v, f) { return (getComputedStyle(document.documentElement).getPropertyValue(v) || f).trim() || f; }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(tx, ty); ctx.scale(scale, scale);
    ctx.strokeStyle = 'rgba(150,150,170,0.22)'; ctx.lineWidth = 1 / scale;
    ctx.beginPath();
    for (const l of links) { ctx.moveTo(l.source.x, l.source.y); ctx.lineTo(l.target.x, l.target.y); }
    ctx.stroke();
    const accent = css('--accent-primary', '#b996ff') || css('--red', '#b996ff');
    for (const n of nodes) {
      ctx.beginPath(); ctx.arc(n.x, n.y, radius(n), 0, Math.PI * 2);
      ctx.fillStyle = n === hover ? '#ffffff' : accent;
      ctx.fill();
    }
    ctx.fillStyle = css('--fg', '#ddd'); ctx.font = `${12 / scale}px sans-serif`; ctx.textAlign = 'center';
    const labelThresh = Math.max(2, maxDeg * 0.45);
    for (const n of nodes) {
      if (n === hover || (n.deg || 0) >= labelThresh) ctx.fillText(n.label, n.x, n.y - radius(n) - 3 / scale);
    }
    ctx.restore();
  }

  function frame() {
    if (destroyed) return;
    if (alpha > 0.02) tick();
    draw();
    raf = requestAnimationFrame(frame);
  }

  const toWorld = (px, py) => ({ x: (px - tx) / scale, y: (py - ty) / scale });
  function nodeAt(px, py) {
    const w = toWorld(px, py);
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i], r = radius(n) + 5;
      if ((n.x - w.x) ** 2 + (n.y - w.y) ** 2 <= r * r) return n;
    }
    return null;
  }

  let dragNode = null, panning = false, lastX = 0, lastY = 0, moved = false;
  function _pos(e) { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  function _down(e) {
    const [px, py] = _pos(e);
    dragNode = nodeAt(px, py); panning = !dragNode; lastX = px; lastY = py; moved = false;
    if (dragNode) { dragNode.fixed = true; alpha = Math.max(alpha, 0.3); }
    canvas.setPointerCapture?.(e.pointerId);
    canvas.style.cursor = 'grabbing';
  }
  function _move(e) {
    const [px, py] = _pos(e);
    if (!dragNode && !panning) { hover = nodeAt(px, py); canvas.style.cursor = hover ? 'pointer' : 'grab'; return; }
    const ddx = px - lastX, ddy = py - lastY;
    if (Math.abs(ddx) + Math.abs(ddy) > 3) moved = true;
    lastX = px; lastY = py;
    if (dragNode) { const w = toWorld(px, py); dragNode.x = w.x; dragNode.y = w.y; alpha = Math.max(alpha, 0.3); }
    else if (panning) { tx += ddx; ty += ddy; }
  }
  function _up() {
    if (dragNode) { dragNode.fixed = false; if (!moved) onOpen(dragNode.path); }
    dragNode = null; panning = false; canvas.style.cursor = 'grab';
  }
  function _wheel(e) {
    e.preventDefault();
    const [px, py] = _pos(e);
    const w = toWorld(px, py);
    const ns = Math.max(0.2, Math.min(4, scale * Math.exp(-e.deltaY * 0.0012)));
    tx = px - w.x * ns; ty = py - w.y * ns; scale = ns;
  }
  canvas.addEventListener('pointerdown', _down);
  canvas.addEventListener('pointermove', _move);
  canvas.addEventListener('pointerup', _up);
  canvas.addEventListener('pointercancel', _up);
  canvas.addEventListener('wheel', _wheel, { passive: false });
  canvas.style.cursor = 'grab';
  canvas.style.touchAction = 'none';

  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();
  frame();

  return {
    destroy() {
      destroyed = true;
      cancelAnimationFrame(raf);
      try { ro.disconnect(); } catch (_) {}
      container.innerHTML = '';
    },
  };
}
