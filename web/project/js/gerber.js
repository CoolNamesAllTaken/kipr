// The gerber renderer (boarddd/gerber, vendored in ../vendor/boarddd: src/gerber plus the wasm renderer
// core in third_party/wasm-gerber-renderer/core): one hidden WebGL2 canvas, loaded on first use and
// handed to the view2d stages, which frame and queue every render.
import { OFFLINE, loadOfflineBundle, offlineWasm } from './util.js';

let rendererPromise = null;
let glCanvas = null;

export function webgl2Available() {
  try {
    const c = document.createElement('canvas');
    return !!c.getContext('webgl2');
  } catch { return false; }
}

/** Why the gerber renderer can't be used here, or null if it can. */
export function gerberUnavailableReason() {
  // From disk the renderer comes from the prebuilt 3D bundle (pcba3d/pcba3d.bundle.js) and its WASM from
  // offline/pcba3d-vendor.js; getRenderer() fails over to the SVGs if either is missing.
  if (OFFLINE && !(typeof window !== 'undefined' && window.KIPR_DATA?.pcba3d)) return 'Opened from disk (file://) without the offline renderer. Showing the per-layer SVG exports; run `python3 serve.py` in this folder for the gerber view.';
  if (!webgl2Available()) return 'WebGL2 is not available in this browser. Showing the per-layer SVG exports instead of the gerber render.';
  return null;
}

/** The shared renderer (a GerberRenderer); rejects when it can't load. */
export function getRenderer() {
  if (!rendererPromise) {
    rendererPromise = (async () => {
      if (OFFLINE) return offlineRenderer();
      const mod = await import('../vendor/boarddd/src/gerber/index.js');
      glCanvas = document.createElement('canvas');
      glCanvas.width = 16; glCanvas.height = 16;
      // wasm-bindgen's init takes {module_or_path}; a bare URL still works but logs a deprecation warning
      const renderer = await mod.createGerberRenderer(glCanvas, { wasmInitInput: { module_or_path: new URL(`../${WASM_KEY}`, import.meta.url) } });
      // board compositing and the layer diff are part of boarddd/gerber too: same renderer, same frame rules
      return renderer;
    })();
    rendererPromise.catch(() => { rendererPromise = null; });
  }
  return rendererPromise;
}

// the vendored wasm, relative to web/project/ (also its key in the file:// pack offline/pcba3d-vendor.js)
const WASM_KEY = 'vendor/boarddd/third_party/wasm-gerber-renderer/core/wasm/wasm_gerber_processor_bg.wasm';

/** file://: ES modules and fetch() are blocked, so take the renderer from the classic 3D bundle
 * (window.KIPR_GERBER = {gerber, wasmGlue}: boarddd/gerber and the wasm-bindgen glue) and the WASM from
 * its data pack. */
async function offlineRenderer() {
  await loadOfflineBundle();
  const g = window.KIPR_GERBER;
  const bytes = g ? await offlineWasm(WASM_KEY) : null;
  if (!g || !bytes) throw new Error('offline gerber renderer not available (no pcba3d bundle or WASM pack)');
  glCanvas = document.createElement('canvas');
  glCanvas.width = 16; glCanvas.height = 16;
  const renderer = await g.gerber.createGerberRenderer(glCanvas, { wasmModule: g.wasmGlue, wasmInitInput: { module_or_path: bytes } });
  return renderer;
}
