// One-tap cache nuke for mobile (no devtools on iOS). Unregisters every service
// worker and deletes every Cache Storage entry, then reloads the app fresh.
// Loaded as an external script because the app CSP blocks un-nonced inline ones.
(function () {
  var log = document.getElementById('log');
  function line(msg, cls) {
    var s = document.createElement('span');
    s.textContent = msg + '\n';
    if (cls) s.className = cls;
    log.appendChild(s);
  }
  (async function () {
    try {
      if ('serviceWorker' in navigator) {
        var regs = await navigator.serviceWorker.getRegistrations();
        if (!regs.length) line('no service workers registered');
        for (var i = 0; i < regs.length; i++) {
          await regs[i].unregister();
          line('unregistered service worker', 'pass');
        }
      } else {
        line('serviceWorker API unavailable');
      }
      if (window.caches && caches.keys) {
        var keys = await caches.keys();
        if (!keys.length) line('no caches to clear');
        for (var j = 0; j < keys.length; j++) {
          await caches.delete(keys[j]);
          line('deleted cache: ' + keys[j], 'pass');
        }
      } else {
        line('Cache Storage API unavailable');
      }
      line('\nDone. Loading a fresh copy in 1.5s…', 'pass');
      setTimeout(function () { location.replace('/?fresh=' + Date.now()); }, 1500);
    } catch (e) {
      line('error: ' + e, 'fail');
      line('You can also clear it manually: Brave > site settings > clear data, or use a Private tab.');
    }
  })();
})();
