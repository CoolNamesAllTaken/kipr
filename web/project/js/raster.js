// Rasterise image assets (SVG renders) over a mm box and diff them with inkdiff.js.
import { canvasImageSrc, OFFLINE } from './util.js';
import { inkMask, alphaMask, diffMasks, paintDiff, regions, orMask, DIFF_COLORS } from './inkdiff.js';

const MAX_PIXELS = 12e6;
const MAX_EDGE = 6000;

/** px per mm for rasterising a w x h mm box: `want`, reduced to stay within the pixel budget. */
export function rasterScale(wMm, hMm, want = 10) {
  let r = want;
  r = Math.min(r, MAX_EDGE / Math.max(wMm, 1e-6), MAX_EDGE / Math.max(hMm, 1e-6));
  r = Math.min(r, Math.sqrt(MAX_PIXELS / Math.max(wMm * hMm, 1e-6)));
  return Math.max(r, 0.5);
}

export function loadImage(path) {
  return canvasImageSrc(path).then((src) => new Promise((resolve, reject) => {
    if (!src) { reject(new Error(`refusing to load ${path}`)); return; }
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => { if (OFFLINE && src.startsWith('blob:')) URL.revokeObjectURL(src); resolve(img); };
    img.onerror = () => reject(new Error(`could not load ${path}`));
    img.src = src;
  }));
}

const bitmapCache = new Map();
/**
 * A canvas with the image at `path` drawn at r px/mm over its own mm rect `at` (for display: panning a
 * bitmap is cheap, while a big vector SVG <img> is re-rasterised by the browser on every zoom step).
 * Cached per (path, r); the most recent few only.
 */
export function bitmapOf(path, at, r) {
  const key = `${path}|${r}`;
  if (!bitmapCache.has(key)) {
    if (bitmapCache.size > 8) bitmapCache.delete(bitmapCache.keys().next().value);
    const p = loadImage(path).then((img) => {
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(at.w * r));
      c.height = Math.max(1, Math.round(at.h * r));
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      return c;
    });
    p.catch(() => bitmapCache.delete(key));
    bitmapCache.set(key, p);
  }
  return bitmapCache.get(key);
}

/** Display resolution for a stage scale: css px per mm x devicePixelRatio, in steps of sqrt(2), within budget. */
export function displayScale(cssPerMm, wMm, hMm, dpr = 1, min = 2) {
  const want = Math.max(cssPerMm * dpr, 1e-3);
  const r = Math.max(min, 2 ** (Math.ceil(Math.log2(want) * 2) / 2)); // steps of sqrt(2), at least `min`
  return Math.min(r, rasterScale(wMm, hMm, 64));
}

/** RGBA of `img`, drawn so that its mm rect `at` lands correctly in the mm `box`, at r px/mm. */
export function rasterize(img, at, box, r) {
  const w = Math.max(1, Math.round(box.w * r));
  const h = Math.max(1, Math.round(box.h * r));
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d', { willReadFrequently: true });
  if (img) g.drawImage(img, (at.x - box.x) * r, (at.y - box.y) * r, at.w * r, at.h * r);
  return { data: g.getImageData(0, 0, w, h).data, w, h };
}

/**
 * Diff two rasters (either may be null = nothing drawn). Returns a canvas with the coloured diff,
 * pixel counts and changed regions converted to mm boxes (KiCad frame).
 * `mode`: 'ink' (paper-coloured background ignored) or 'alpha' (any opaque pixel is ink).
 */
export function diffRasters(base, head, box, r, { mode = 'ink', tol = 1, colors = DIFF_COLORS, regionGapMm = 1.5 } = {}) {
  const ref = base || head;
  const { w, h } = ref;
  const mk = (x) => (x ? (mode === 'alpha' ? alphaMask(x.data, w, h) : inkMask(x.data, w, h)) : new Uint8Array(w * h));
  const d = diffMasks(mk(base), mk(head), w, h, tol);
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d');
  const out = g.createImageData(w, h);
  paintDiff(out.data, d, colors);
  g.putImageData(out, 0, 0);
  const regs = regions(orMask(d.removed, d.added), w, h, { gap: Math.max(2, regionGapMm * r), minPixels: Math.max(3, Math.round(r * r * 0.05)) })
    .map((q) => ({ x: box.x + q.x / r, y: box.y + q.y / r, w: q.w / r, h: q.h / r, pixels: q.pixels }));
  return { canvas: c, counts: d.counts, regions: regs };
}
