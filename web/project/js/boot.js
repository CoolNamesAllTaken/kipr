// Entry point, a classic script so it also runs from file:// (a downloaded, unzipped CI artifact).
//
//   http(s)  (Pages, serve.py): tighten the CSP to what the ES-module app needs and load js/app.js
//            as a module.
//   file://  browsers block module scripts and fetch() there, so load data.js (project-review.json,
//            written by kipr/project/site.py) and js/bundle.js, the same modules as one classic script.
//            The gerber renderer (WebAssembly) and the 3D module can't load from disk; those views say so.
(function () {
  'use strict';
  function add(src, module) {
    var s = document.createElement('script');
    if (module) s.type = 'module';
    s.src = src;
    s.async = false; // keep insertion order
    document.head.appendChild(s);
  }
  if (location.protocol === 'file:') {
    add('data.js');
    add('js/bundle.js');
    return;
  }
  add('js/app.js', true);
}());
