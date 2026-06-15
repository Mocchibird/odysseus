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

  // E. REAL gallery image inside a flex-column scroller (mimics the modal),
  // testing fix candidates. The A-D variants used tiny instant images + no app
  // CSS, so they couldn't reproduce the real bug (card grows to a real, large,
  // async-loading image). Fetches a real image so we see the actual failure
  // and which fix makes the card square again.
  (function () {
    var head = add('E. REAL image in flex-col scroller (3 fix candidates)', 'fetching a real gallery image…', 'warn');
    function buildVariant(label, imgUrl, cardExtra, imgExtra) {
      var host = document.createElement('div'); host.className = 'row';
      host.innerHTML = '<span class="k">' + label + ':</span> <span class="warn">measuring…</span>';
      out.appendChild(host);
      var status = host.querySelector('span:last-child');
      // flex-column constrained parent, like the gallery modal-body
      var flex = document.createElement('div');
      flex.style.cssText = 'display:flex;flex-direction:column;max-height:240px;margin-top:6px';
      var grid = document.createElement('div');
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:6px;max-height:220px;overflow-y:auto';
      for (var i = 0; i < 4; i++) {
        var card = document.createElement('div');
        card.style.cssText = 'position:relative;aspect-ratio:1;border-radius:6px;overflow:hidden;border:1px solid #3a3d45;background:#2a2d35;' + (cardExtra || '');
        var img = document.createElement('img');
        img.src = imgUrl;
        img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;' + (imgExtra || '');
        card.appendChild(img);
        grid.appendChild(card);
      }
      flex.appendChild(grid); host.appendChild(flex);
      setTimeout(function () {
        var c0 = grid.children[0].getBoundingClientRect();
        var img0 = grid.children[0].querySelector('img');
        var square = grid.children[0] && c0.h > 0 && Math.abs(c0.w - c0.h) <= 3;
        status.textContent = (square ? 'OK square' : 'BROKEN') + ' — card ' + Math.round(c0.w) + 'x' + Math.round(c0.h) +
          ', imgNatural ' + img0.naturalWidth + 'x' + img0.naturalHeight + ', imgClientH ' + img0.clientHeight;
        status.className = square ? 'pass' : 'fail';
      }, 900);
    }
    fetch('/api/gallery/library?limit=4', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        var items = (data && data.items) || [];
        var url = items.length ? items[0].url : null;
        if (!url) { setLast(head, 'no gallery images returned (cannot run E) — items=' + items.length, 'warn'); return; }
        setLast(head, 'using ' + url.slice(0, 60), '');
        buildVariant('E0 baseline (height:100% img)', url, '', '');
        buildVariant('E1 card min-height:0', url, 'min-height:0;', '');
        buildVariant('E2 img position:absolute inset:0', url, '', 'position:absolute;inset:0;');
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
