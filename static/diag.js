// Standalone on-device diagnostic for the iOS gallery + 360 issues.
// Loaded by /static/diag.html as an external script (the app CSP blocks
// un-nonced inline scripts, but allows same-origin 'self' scripts).
(function () {
  var out = document.getElementById('out');
  function add(k, v, cls) {
    var d = document.createElement('div'); d.className = 'row';
    d.innerHTML = '<span class="k">' + k + ':</span> <span class="' + (cls || '') + '">' + v + '</span>';
    out.appendChild(d); return d;
  }
  function addPre(k, txt) {
    var d = document.createElement('div'); d.className = 'row';
    d.innerHTML = '<span class="k">' + k + '</span><pre>' + txt + '</pre>';
    out.appendChild(d);
  }
  function setLast(rowEl, msg, cls) {
    var s = rowEl.querySelector('span:last-child'); s.textContent = msg; s.className = cls;
  }

  addPre('User agent', navigator.userAgent);
  var arOK = CSS.supports('aspect-ratio', '1 / 1');
  add('CSS aspect-ratio supported', arOK ? 'YES' : 'NO', arOK ? 'pass' : 'fail');

  // Measure the replica grid once layout settles.
  setTimeout(function () {
    var cards = document.querySelectorAll('#grid .card');
    var rects = [].map.call(cards, function (c) {
      var r = c.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), t: Math.round(r.top), l: Math.round(r.left) };
    });
    var square = rects.length && rects.every(function (r) { return r.h > 0 && Math.abs(r.w - r.h) <= 2; });
    add('Grid cells square', square ? 'YES (grid OK)' : 'NO — first cell is ' + JSON.stringify(rects[0]) + ' (THIS is the gallery bug)', square ? 'pass' : 'fail');
    var overlap = false;
    for (var i = 0; i < rects.length; i++) for (var j = i + 1; j < rects.length; j++) {
      var a = rects[i], b = rects[j];
      if (a.l < b.l + b.w && a.l + a.w > b.l && a.t < b.t + b.h && a.t + a.h > b.t) overlap = true;
    }
    add('Cells overlap', overlap ? 'YES (broken)' : 'no', overlap ? 'fail' : 'pass');
  }, 400);

  // WebGL video-texture test — the exact 360 mechanism.
  (function () {
    var status = add('WebGL video-texture (360)', 'testing…', 'warn');
    try {
      var src = document.createElement('canvas'); src.width = 64; src.height = 64;
      var sctx = src.getContext('2d');
      if (!src.captureStream) { setLast(status, 'cannot test (no canvas.captureStream)', 'warn'); return; }
      var stream = src.captureStream(15);
      var v = document.createElement('video');
      v.muted = true; v.setAttribute('muted', ''); v.playsInline = true;
      v.setAttribute('playsinline', ''); v.setAttribute('webkit-playsinline', '');
      v.srcObject = stream;
      var raf; (function draw() { sctx.fillStyle = '#ff0000'; sctx.fillRect(0, 0, 64, 64); raf = requestAnimationFrame(draw); })();
      function run() {
        try {
          var glc = document.createElement('canvas'); glc.width = 1; glc.height = 1;
          var gl = glc.getContext('webgl') || glc.getContext('experimental-webgl');
          if (!gl) { setLast(status, 'NO WebGL context available', 'fail'); cancelAnimationFrame(raf); return; }
          var tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
          var ok = false, err = '';
          try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, v); ok = (gl.getError() === 0); }
          catch (e) { err = String(e); }
          setLast(status,
            'paused=' + v.paused + ' readyState=' + v.readyState + ' videoW=' + v.videoWidth +
            ' | texImage2D ' + (ok ? 'OK' : 'FAILED ' + err),
            (ok && v.videoWidth > 0 && !v.paused) ? 'pass' : 'fail');
        } catch (e) { setLast(status, 'error: ' + e, 'fail'); }
        cancelAnimationFrame(raf);
      }
      var p = v.play();
      if (p && p.then) {
        p.then(function () { setTimeout(run, 500); })
         .catch(function (e) { setLast(status, 'video.play() REJECTED: ' + e + ' — iOS blocked playback (likely why 360 is black)', 'fail'); setTimeout(run, 500); });
      } else { setTimeout(run, 500); }
    } catch (e) { setLast(status, 'error: ' + e, 'fail'); }
  })();
})();
