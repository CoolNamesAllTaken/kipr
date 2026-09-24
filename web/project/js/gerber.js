// Gerber rendering through our wasm-gerber-renderer fork (vendored in ../vendor/wasm-gerber-renderer).
//
// One hidden WebGL2 canvas and renderer, loaded on first use. Every render frames an explicit mm box
// (never `fit`), so a render of any layer set lands on exactly the same pixels, and the result is copied
// into a plain 2D canvas that the view places in its world. Renders are queued: the renderer cannot run
// two frames at once.
import { fetchText, OFFLINE } from './util.js';
import { kicadBoxToGerber } from './board.js';

let rendererPromise = null;
let glCanvas = null;
let queue = Promise.resolve();

export function webgl2Available() {
  try {
    const c = document.createElement('canvas');
    return !!c.getContext('webgl2');
  } catch { return false; }
}

/** Why the gerber renderer can't be used here, or null if it can. */
export function gerberUnavailableReason() {
  if (OFFLINE) return 'Opened from disk (file://): browsers block the WebAssembly gerber renderer there. Showing the per-layer SVG exports; run `python3 serve.py` in this folder for the gerber view.';
  if (!webgl2Available()) return 'WebGL2 is not available in this browser. Showing the per-layer SVG exports instead of the gerber render.';
  return null;
}

async function getRenderer() {
  if (!rendererPromise) {
    rendererPromise = (async () => {
      const mod = await import('../vendor/wasm-gerber-renderer/index.js');
      glCanvas = document.createElement('canvas');
      glCanvas.width = 16; glCanvas.height = 16;
      const wasmUrl = new URL('../vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm', import.meta.url);
      const renderer = await mod.createGerberRenderer(glCanvas, { wasmInitInput: wasmUrl });
      return { mod, renderer };
    })();
    rendererPromise.catch(() => { rendererPromise = null; });
  }
  return rendererPromise;
}

/**
 * Render `layers` over KiCad mm `box` at `r` px/mm into a new 2D canvas.
 * layers: [{path, color:[r,g,b], alpha, kind:'gerber'|'drill', invert:bool, outline:bool, hidden:bool}]
 *   outline: use this layer as the board outline for later `invert` layers (drawn only if !hidden).
 * Returns {canvas, failures:[{path, error}]}.
 */
export function renderGerbers(layers, box, r, { origin = [0, 0] } = {}) {
  const job = queue.then(() => doRender(layers, box, r, origin));
  queue = job.catch(() => {});
  return job;
}

async function doRender(layers, box, r, origin) {
  const { mod, renderer } = await getRenderer();
  const W = Math.max(1, Math.round(box.w * r));
  const H = Math.max(1, Math.round(box.h * r));
  const texts = await Promise.all(layers.map((l) => fetchText(l.path).catch((e) => e)));
  const failures = [];
  const view = mod.calculateFitView(kicadBoxToGerber(box, origin), W, H, 0);
  await renderer.withFrame({
    width: W, height: H, background: null, compositeMode: 'stack', view, renderDrills: true,
    globalAlpha: 1,
  }, async () => {
    let outlineId = null;
    for (let i = 0; i < layers.length; i++) {
      const l = layers[i];
      const text = texts[i];
      if (text instanceof Error || typeof text !== 'string') { failures.push({ path: l.path, error: String(text?.message || text) }); continue; }
      if (isEmpty(text, l.kind)) continue; // e.g. an NPTH file of a board without unplated holes
      try {
        if (l.invert) {
          const opts = { color: l.color, alpha: l.alpha ?? 1 };
          if (outlineId !== null) opts.outlineLayerId = outlineId;
          await renderer.renderInvertedLayer(text, opts);
        } else {
          const id = await renderer.renderLayer(text, { color: l.color, alpha: l.alpha ?? 1, kind: l.kind === 'drill' ? 'drill' : 'gerber', visible: !l.hidden });
          if (l.outline && Number.isInteger(id)) outlineId = id;
        }
      } catch (e) {
        failures.push({ path: l.path, error: String(e?.message || e) });
      }
    }
  });
  const out = document.createElement('canvas');
  out.width = W; out.height = H;
  out.getContext('2d').drawImage(glCanvas, 0, 0);
  return { canvas: out, failures };
}

/** True for a gerber/drill file that draws nothing (the renderer rejects those). */
export function isEmpty(text, kind) {
  if (kind === 'drill') return !/^\s*(?:G0?[0-3]\s*)?[XY][-+]?\d/m.test(text);
  return !/D0?[123]\*/.test(text);
}
