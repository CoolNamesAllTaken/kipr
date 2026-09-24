# wasm-gerber-renderer 0.7.0 (fork version), vendored from our fork

MIT (see LICENSE). Upstream: https://github.com/dsafdsaf132/wasm-gerber-viewer. Ours:
https://github.com/CoolNamesAllTaken/wasm-gerber-viewer, which adds the exported view math
(`calculateFitView`, `projectToCanvas`, ...) and `renderInvertedLayer` the layout view uses.

**Generated; do not edit.** Refresh with `bash web/project/scripts/sync_vendored_renderer.bash`.

- JavaScript: fork commit `9b7ade3` (`9b7ade3`), `packages/wasm-gerber-renderer/*.js` and `*.d.ts` minus the Node entry point.
  Shared by the layout view (`js/gerber.js`) and the 3D module (`pcba3d/`).
- wasm: the published npm release wasm-gerber-renderer@0.6.0 (wasm-pack not installed here).

The layout view passes the .wasm URL explicitly (`wasmInitInput`) and never uses `fit`: every
render frames an explicit KiCad-mm box so all layers and both commits land on the same pixels.
