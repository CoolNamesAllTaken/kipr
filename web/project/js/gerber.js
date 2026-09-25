// Gerber rendering through our wasm-gerber-renderer fork (vendored in ../vendor/wasm-gerber-renderer).
//
// One hidden WebGL2 canvas and renderer, loaded on first use. Every render frames an explicit mm box
// (never `fit`), so a render of any layer set lands on exactly the same pixels, and the result is copied
// into a plain 2D canvas that the view places in its world. Renders are queued: the renderer cannot run
// two frames at once.
import { fetchText, OFFLINE, loadOfflineBundle, offlineWasm } from './util.js';
import { kicadBoxToGerber, gerberPointToKicad } from './board.js';

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
  // From disk the renderer comes from the prebuilt 3D bundle (pcba3d/pcba3d.bundle.js) and its WASM from
  // offline/pcba3d-vendor.js; getRenderer() fails over to the SVGs if either is missing.
  if (OFFLINE && !(typeof window !== 'undefined' && window.KIPR_DATA?.pcba3d)) return 'Opened from disk (file://) without the offline renderer. Showing the per-layer SVG exports; run `python3 serve.py` in this folder for the gerber view.';
  if (!webgl2Available()) return 'WebGL2 is not available in this browser. Showing the per-layer SVG exports instead of the gerber render.';
  return null;
}

async function getRenderer() {
  if (!rendererPromise) {
    rendererPromise = (async () => {
      if (OFFLINE) return offlineRenderer();
      const mod = await import('../vendor/wasm-gerber-renderer/index.js');
      glCanvas = document.createElement('canvas');
      glCanvas.width = 16; glCanvas.height = 16;
      const wasmUrl = new URL('../vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm', import.meta.url);
      // wasm-bindgen's init takes {module_or_path}; a bare URL still works but logs a deprecation warning
      const renderer = await mod.createGerberRenderer(glCanvas, { wasmInitInput: { module_or_path: wasmUrl } });
      // the fork's board compositing and layer diff (claud/board-diff); same renderer, same frame rules
      const [board, diff] = await Promise.all([
        import('../vendor/wasm-gerber-renderer/board.js'),
        import('../vendor/wasm-gerber-renderer/diff.js'),
      ]);
      return { mod, renderer, board, diff };
    })();
    rendererPromise.catch(() => { rendererPromise = null; });
  }
  return rendererPromise;
}

const WASM_KEY = 'vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm';

/** file://: ES modules and fetch() are blocked, so take the renderer from the classic 3D bundle
 * (window.KIPR_GERBER = {index, board, diff, wasmGlue}) and the WASM from its data pack. */
async function offlineRenderer() {
  await loadOfflineBundle();
  const g = window.KIPR_GERBER;
  const bytes = g ? await offlineWasm(WASM_KEY) : null;
  if (!g || !bytes) throw new Error('offline gerber renderer not available (no pcba3d bundle or WASM pack)');
  glCanvas = document.createElement('canvas');
  glCanvas.width = 16; glCanvas.height = 16;
  const renderer = await g.index.createGerberRenderer(glCanvas, { wasmModule: g.wasmGlue, wasmInitInput: { module_or_path: bytes } });
  return { mod: g.index, renderer, board: g.board, diff: g.diff };
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

function frameSize(box, r) {
  return { W: Math.max(1, Math.round(box.w * r)), H: Math.max(1, Math.round(box.h * r)) };
}

function copyCanvas(W, H) {
  const out = document.createElement('canvas');
  out.width = W; out.height = H;
  out.getContext('2d').drawImage(glCanvas, 0, 0);
  return out;
}

async function texts(paths) {
  const t = await Promise.all(paths.map((p) => (p ? fetchText(p).catch(() => null) : null)));
  return t;
}

/**
 * A realistic board face (the fork's addBoardLayers: substrate inside the outline, finish on copper in
 * mask openings, mask, silk clipped to the openings and the outline, transparent holes) over KiCad mm
 * `box` at r px/mm. Not mirrored: the stage mirrors the bottom view itself.
 * face: {outline, copper, mask, silk, drills: []} gerber paths (null when absent).
 */
export function renderFace(face, side, box, r, { origin = [0, 0], palette = {} } = {}) {
  const job = queue.then(async () => {
    const { mod, renderer, board } = await getRenderer();
    const { W, H } = frameSize(box, r);
    const [outline, copper, mask, silk, ...drills] = await texts([face.outline, face.copper, face.mask, face.silk, ...(face.drills || [])]);
    const src = (text, name) => (text && !isEmpty(text, name === 'drill' ? 'drill' : 'gerber') ? { source: text, name } : null);
    const desc = {
      outline: src(outline, 'outline'),
      [side]: { copper: src(copper, 'copper'), mask: src(mask, 'mask'), silk: src(silk, 'silk') },
      drills: drills.map((t) => src(t, 'drill')).filter(Boolean),
    };
    const view = mod.calculateFitView(kicadBoxToGerber(box, origin), W, H, 0);
    await renderer.withFrame({ width: W, height: H, background: null, compositeMode: 'stack', view, renderDrills: true }, async () => {
      await board.addBoardLayers(renderer, desc, { side, palette, holes: true, clipSilk: true });
    });
    return { canvas: copyCanvas(W, H), failures: [] };
  });
  queue = job.catch(() => {});
  return job;
}

/**
 * The fork's GPU layer diff of one layer (base vs head gerber paths, either may be null) over KiCad mm
 * `box` at r px/mm: {canvas, regions: [{x, y, w, h} KiCad mm], counts: {removed, added, common}}.
 * Two passes in one explicit frame: analyzeLayerDiff (counts + regions), then renderLayerDiff (the image).
 */
export function renderLayerDiff(basePath, headPath, box, r, { origin = [0, 0], kind = 'gerber', colors = null } = {}) {
  const job = queue.then(async () => {
    const { mod, renderer, diff } = await getRenderer();
    const { W, H } = frameSize(box, r);
    const [b, h] = await texts([basePath, headPath]);
    const side = (t) => (t && !isEmpty(t, kind) ? { source: t } : null);
    const view = mod.calculateFitView(kicadBoxToGerber(box, origin), W, H, 0);
    const rep = await diff.analyzeLayerDiff(renderer, { base: side(b), head: side(h) }, {
      width: W, height: H, view, skipIdentical: false, showUnchanged: true,
      mergeDistance: Math.max(2, Math.round(1.5 * r)), minRegionPixels: 3, maxRegions: 200,
    });
    // analyzeLayerDiff draws classification colours for counting; the picture is a second pass in the same frame
    await diff.renderLayerDiff(renderer, { base: side(b), head: side(h) }, {
      width: W, height: H, view, background: null, colors: colors || undefined,
      style: { unchanged: { alpha: 0.45 } },
    });
    const regions = (rep.regions || []).filter((q) => q.world).map((q) => {
      const [x0, y1] = gerberPointToKicad(q.world.minX, q.world.minY, origin);
      const [x1, y0] = gerberPointToKicad(q.world.maxX, q.world.maxY, origin);
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0, kind: q.kind, pixels: q.addedPixels + q.removedPixels };
    });
    return { canvas: copyCanvas(W, H), regions, counts: { removed: rep.removedPixels, added: rep.addedPixels, common: rep.unchangedPixels } };
  });
  queue = job.catch(() => {});
  return job;
}

/** True for a gerber/drill file that draws nothing (the renderer rejects those). */
export function isEmpty(text, kind) {
  if (kind === 'drill') return !/^\s*(?:G0?[0-3]\s*)?[XY][-+]?\d/m.test(text);
  return !/D0?[123]\*/.test(text);
}
