# boarddd 0.6.0, vendored at 93ebaf4

MIT (see LICENSE). https://github.com/CoolNamesAllTaken/boarddd, commit `93ebaf4d78d564dfab264873e1ef69072fc7cc96`.

**This directory is generated. Do not edit it.** Change boarddd upstream, tag it, and re-run
(from the root of this repository, `<boarddd>` being a boarddd checkout):

    node <boarddd>/scripts/vendor.mjs --out web/vendor/boarddd --ref 93ebaf4d78d564dfab264873e1ef69072fc7cc96 --subpaths geom,board,footprint,models,scene,gerber,view2d --three-dir web/vendor/three --three 0.185.1 --occt-dir web/vendor/occt-import-js --occt 0.0.23

Taken: `src/geom`, `src/board`, `src/footprint`, `src/models`, `src/scene`, `src/gerber`, `src/view2d` (sources, `.d.ts` typings and assets), `third_party/wasm-gerber-renderer`
(what those import, with its own LICENSE), and boarddd's LICENSE.
Import `src/<subpath>/index.js`. Its `three` and `three/addons/...` imports are rewritten to relative paths into
`../three` (`three.module.js`, `addons/<dir>/<file>.js`), so pages need
no importmap. three.js 0.185.1 (MIT) is vendored there by the same script.
The `.d.ts` files keep `from 'three'` for the type checker (@types/three).

occt-import-js 0.0.23 (LGPL-2.1) is in `../occt-import-js`, unmodified; pass its
`dist/occt-import-js.js` and `.wasm` URLs to boarddd's STEP loader.
