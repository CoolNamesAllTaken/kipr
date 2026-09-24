// Starts demo.html. Over http(s) it loads demo.js as an ES module, as before. From file:// the
// browser blocks module scripts and fetch(), so it loads OUT/offline/review.js (the review
// JSON as a script) and demo.bundle.js (demo.js and everything it imports, as one classic
// script), both written by build_offline.mjs. The same scheme as kicad-libs' component-review
// viewer (tools/component-review/viewer/js/boot.js).
(function () {
  'use strict';
  function add(src, module) {
    var s = document.createElement('script');
    if (module) s.type = 'module';
    s.src = src;
    s.async = false;
    s.onerror = function () {
      var err = document.getElementById('err');
      if (err) err.textContent = 'Could not load ' + src + (module ? '' : ' (run: node web/project/pcba3d/build_offline.mjs --out <OUT>)');
      document.body.dataset.ready = 'error';
    };
    document.head.appendChild(s);
  }
  if (location.protocol === 'file:') {
    var out = new URLSearchParams(location.search).get('out') || '../../../tests/web-3d/out/mock/';
    if (out.charAt(out.length - 1) !== '/') out += '/';
    add(out + 'offline/review.js');
    add('demo.bundle.js');
    return;
  }
  add('demo.js', true);
}());
