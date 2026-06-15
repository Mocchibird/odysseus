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

  // Which build is actually deployed/served (so we know what we're testing).
  (function () {
    var row = add('Deployed build (sw CACHE_NAME)', 'checking…', 'warn');
    fetch('/static/sw.js?cb=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { return r.text(); })
      .then(function (t) {
        var m = t.match(/CACHE_NAME\s*=\s*['"]([^'"]+)/);
        setLast(row, m ? m[1] + ' (360-on-three.js = v438+)' : 'could not parse', m ? '' : 'warn');
      })
      .catch(function (e) { setLast(row, 'fetch failed: ' + e, 'fail'); });
  })();

  // F. The REAL 360 path on this device: does three.js import, and can a real
  // gallery video play + texture through it? Distinguishes "three.js won't load
  // on iOS" from "the video won't play" from "all good (app integration bug)".
  (function () {
    var row = add('F. three.js + real video (360 path)', 'importing three.js…', 'warn');
    import('/static/lib/three.module.min.js').then(function (THREE) {
      var ver = 'r' + (THREE.REVISION || '?');
      return fetch('/api/gallery/library?media_type=video&limit=1', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          var items = (data && data.items) || [];
          var url = items.length ? items[0].url : null;
          if (!url) { setLast(row, 'three.js ' + ver + ' loaded OK — but no videos in gallery to test', 'warn'); return; }
          var v = document.createElement('video');
          v.muted = true; v.setAttribute('muted', ''); v.playsInline = true;
          v.setAttribute('playsinline', ''); v.setAttribute('webkit-playsinline', '');
          v.loop = true; v.src = url;
          // ON-SCREEN (small, visible) so iOS doesn't pause it for being offscreen
          v.style.cssText = 'position:fixed;right:4px;bottom:4px;width:64px;height:36px;z-index:99;opacity:0.6';
          document.body.appendChild(v);
          var finish = function () {
            var rendered = false, err = '';
            try {
              var rr = new THREE.WebGLRenderer(); rr.setSize(64, 36);
              var sc = new THREE.Scene(); var cam = new THREE.PerspectiveCamera(75, 2, 0.1, 100);
              var tex = new THREE.VideoTexture(v);
              var geo = new THREE.SphereGeometry(10, 24, 16); geo.scale(-1, 1, 1);
              sc.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex })));
              rr.render(sc, cam); rendered = true; rr.dispose();
            } catch (e) { err = String(e); }
            var okk = rendered && !v.paused && v.videoWidth > 0;
            setLast(row, 'three.js ' + ver + ' OK | video paused=' + v.paused + ' readyState=' + v.readyState +
              ' vw=' + v.videoWidth + ' | three render ' + (rendered ? 'OK' : 'FAIL ' + err), okk ? 'pass' : 'fail');
            setTimeout(function () { v.remove(); }, 1500);
          };
          var p = v.play();
          if (p && p.then) p.then(function () { setTimeout(finish, 700); }).catch(function (e) { setLast(row, 'three.js ' + ver + ' OK; but video.play() REJECTED: ' + e, 'fail'); setTimeout(finish, 700); });
          else setTimeout(finish, 700);
        });
    }).catch(function (e) {
      setLast(row, 'three.js FAILED to import: ' + e + ' — THIS is why 360 is black on iOS', 'fail');
    });
  })();

  var IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='4' height='6'%3E%3Crect width='4' height='6' fill='%234a90d9'/%3E%3C/svg%3E";

  // Build a 4-cell grid variant, measure squareness + overlap AFTER `delay`ms.
  // `decorate(card)` adds the differentiating feature being tested.
  function testVariant(label, opts) {
    var host = document.createElement('div'); host.className = 'row';
    host.innerHTML = '<span class="k">' + label + ':</span> <span class="warn">measuring…</span>';
    out.appendChild(host);
    var statusSpan = host.querySelector('span:last-child');  // grab BEFORE appending the grid below
    var holder = document.createElement('div');
    // optional scroll container
    var scroller = holder;
    if (opts.scroller) {
      scroller = document.createElement('div');
      scroller.style.cssText = 'max-height:120px;overflow-y:auto';
      holder.appendChild(scroller);
    }
    var grid = document.createElement('div');
    grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:6px';
    if (opts.gridClass) grid.className = opts.gridClass;
    for (var i = 0; i < 4; i++) {
      var card = document.createElement('div');
      card.style.cssText = 'position:relative;aspect-ratio:1;border-radius:6px;overflow:hidden;border:1px solid #3a3d45;background:#2a2d35';
      if (opts.cardClass) card.className = opts.cardClass;
      var img = document.createElement('img');
      img.src = IMG;
      img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block';
      card.appendChild(img);
      if (opts.absChildren) {
        var btn = document.createElement('button');
        btn.textContent = '♥';
        btn.style.cssText = 'position:absolute;top:4px;right:4px';
        card.appendChild(btn);
        var bar = document.createElement('div');
        bar.textContent = 'label';
        bar.style.cssText = 'position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.5);font-size:9px;padding:2px';
        card.appendChild(bar);
      }
      grid.appendChild(card);
    }
    scroller.appendChild(grid);
    holder.style.marginTop = '6px';
    host.appendChild(holder);
    if (opts.afterAppend) opts.afterAppend(grid);

    setTimeout(function () {
      var cards = grid.children;
      var rects = [].map.call(cards, function (c) { var r = c.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), t: Math.round(r.top), l: Math.round(r.left) }; });
      var square = rects.length && rects.every(function (r) { return r.h > 0 && Math.abs(r.w - r.h) <= 3; });
      var overlap = false;
      for (var i = 0; i < rects.length; i++) for (var j = i + 1; j < rects.length; j++) {
        var a = rects[i], b = rects[j];
        if (a.l < b.l + b.w && a.l + a.w > b.l && a.t < b.t + b.h && a.t + a.h > b.t) overlap = true;
      }
      var okk = square && !overlap;
      statusSpan.textContent = (okk ? 'OK (square, no overlap)' : 'BROKEN') + ' — cell0 ' + JSON.stringify(rects[0]) + (overlap ? ' OVERLAP' : '');
      statusSpan.className = okk ? 'pass' : 'fail';
    }, opts.delay || 400);
  }

  // Each variant isolates ONE difference between the working replica and the
  // real gallery card. Whichever comes back BROKEN is the trigger.
  testVariant('A. plain card (control)', {});
  testVariant('B. + absolutely-positioned children (♥ + label bar)', { absChildren: true });
  testVariant('C. + open animation (transform, like .gallery-just-opened)', {
    absChildren: true,
    afterAppend: function (grid) {
      // Mimic section-domino-in: animate transform with backwards fill, then
      // measure AFTER it finishes to see if aspect-ratio stays broken.
      [].forEach.call(grid.children, function (c, i) {
        c.style.animation = 'diag-domino 0.36s cubic-bezier(0.22,1.61,0.36,1) ' + (0.02 * (i + 1)) + 's backwards';
      });
    },
    delay: 1400,
  });
  testVariant('D. + inside max-height scroller', { absChildren: true, scroller: true });

  // E. SQUISH test — reproduce the height-dependent card squish with the REAL
  // gallery-grid CSS, a REAL image, in a HEIGHT-CONSTRAINED scroller (mimics the
  // keyboard-shrunk modal). Runs the same grid at a TALL vs SHORT height to show
  // the height-dependence, then tries fix candidates at the SHORT height.
  // 'square' = card w≈h (good); if SHORT is squished but TALL is square, the
  // grid is distributing its height to rows instead of scrolling.
  (function () {
    var head = add('E. SQUISH test (real image, height-constrained grid)', 'fetching a real gallery image…', 'warn');
    // build a grid of 12 real-image cards inside a fixed-height flex column.
    // `mode` selects baseline vs a candidate fix.
    function run(label, imgUrl, hPx, mode) {
      var host = document.createElement('div'); host.className = 'row';
      host.innerHTML = '<span class="k">' + label + ':</span> <span class="warn">measuring…</span>';
      out.appendChild(host);
      var status = host.querySelector('span:last-child');
      var flex = document.createElement('div');
      flex.style.cssText = 'display:flex;flex-direction:column;height:' + hPx + 'px;width:330px;margin-top:6px;border:1px dashed #444';
      var grid = document.createElement('div');
      // mirror .gallery-grid; max-height removed for the "no-maxheight" fix
      var gridCss = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:6px;overflow-y:auto;';
      if (mode === 'nomax') gridCss += 'flex:1;min-height:0;';          // fix C: grid fills+scrolls, no vh cap
      else gridCss += 'max-height:60vh;';                                // baseline/others: vh-capped like the app
      if (mode === 'aligncontent') gridCss += 'align-content:start;';   // fix A
      grid.style.cssText = gridCss;
      for (var i = 0; i < 12; i++) {
        var card = document.createElement('div');
        var cardCss = 'position:relative;border-radius:6px;overflow:hidden;border:1px solid #3a3d45;background:#2a2d35;min-height:0;';
        if (mode === 'padhack') cardCss += '';                           // height via ::before-like spacer below
        else cardCss += 'aspect-ratio:1;';
        card.style.cssText = cardCss;
        if (mode === 'padhack') {
          var spacer = document.createElement('div'); spacer.style.cssText = 'padding-top:100%;'; card.appendChild(spacer);
          var im = document.createElement('img'); im.src = imgUrl;
          im.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;';
          card.appendChild(im);
        } else {
          var img = document.createElement('img'); img.src = imgUrl;
          img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
          card.appendChild(img);
        }
        grid.appendChild(card);
      }
      flex.appendChild(grid); host.appendChild(flex);
      setTimeout(function () {
        var c0 = grid.children[0].getBoundingClientRect();
        var square = c0.h > 0 && Math.abs(c0.w - c0.h) <= 4;
        status.textContent = (square ? 'square OK' : (c0.h < c0.w ? 'SQUISHED' : 'TALL')) +
          ' — card ' + Math.round(c0.w) + 'x' + Math.round(c0.h) + ', scrollH ' + grid.scrollHeight + ' clientH ' + grid.clientHeight;
        status.className = square ? 'pass' : 'fail';
      }, 900);
    }
    fetch('/api/gallery/library?limit=4', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        var items = (data && data.items) || [];
        var url = items.length ? items[0].url : null;
        if (!url) { setLast(head, 'no gallery images (open the gallery once, or it is empty) — items=' + items.length, 'warn'); return; }
        setLast(head, 'using ' + url.slice(0, 50), '');
        run('E-tall  baseline (height 440)', url, 440, 'base');
        run('E-short baseline (height 200)', url, 200, 'base');
        run('E-short fix A: align-content:start', url, 200, 'aligncontent');
        run('E-short fix B: padding-hack + abs img', url, 200, 'padhack');
        run('E-short fix C: no vh cap, flex+scroll', url, 200, 'nomax');
      })
      .catch(function (e) { setLast(head, 'fetch failed: ' + e, 'fail'); });
  })();

  // WebGL video-texture test — the exact 360 mechanism (kept from v1).
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
         .catch(function (e) { setLast(status, 'video.play() REJECTED: ' + e, 'fail'); setTimeout(run, 500); });
      } else { setTimeout(run, 500); }
    } catch (e) { setLast(status, 'error: ' + e, 'fail'); }
  })();
})();
