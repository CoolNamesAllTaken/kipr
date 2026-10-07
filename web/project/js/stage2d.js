// kipr's views on boarddd/view2d (the shared 2D stage: pan / zoom, synced panes, compare modes, ink
// diff, measure, overlays). This file only adapts it to kipr: the KiCad frame (mm, y down; the layout's
// gerbers are y up from `origin`), the readouts, the change boxes / highlight (Boxes) and the busy marker
// the tests wait on. view2d comes from the vendored boarddd over http, and from the classic-script
// pcba3d bundle (window.KIPR_GERBER.view2d) from file://, where ES modules can't load.
import { el, OFFLINE, loadOfflineBundle } from './util.js';

let v2Promise = null;
let v2Loaded = null;
/** boarddd/view2d once loaded (loadView2d() resolved), else null. */
export function view2dNow() { return v2Loaded; }
/** boarddd/view2d (the module namespace). */
export function loadView2d() {
  if (!v2Promise) {
    v2Promise = OFFLINE
      ? loadOfflineBundle().then(() => {
        const v = window.KIPR_GERBER?.view2d;
        if (!v) throw new Error('Opened from disk without the offline bundle (pcba3d/pcba3d.bundle.js): run `python3 serve.py` in this folder.');
        return v;
      })
      : import('../vendor/boarddd/src/view2d/index.js');
    v2Promise.then((v) => { v2Loaded = v; }, () => { v2Promise = null; });
  }
  return v2Promise;
}

/**
 * KiCad mm (y down) <-> view2d world (mm, y up): world = (x - ox, oy - y). origin [ox, oy] is the KiCad
 * point of the gerber origin for the layout ([0, 0]: gerbers in absolute KiCad coordinates, y negated).
 */
export function kicadFrame(origin = [0, 0]) {
  const [ox, oy] = origin;
  return {
    point: (x, y) => [x - ox, oy - y],
    kicad: (x, y) => [x + ox, oy - y],
    /** KiCad box {x, y, w, h} -> world bounds. */
    bounds: (b) => ({ minX: b.x - ox, maxX: b.x + b.w - ox, minY: oy - (b.y + b.h), maxY: oy - b.y }),
    /** world bounds -> KiCad box. */
    box: (b) => ({ x: b.minX + ox, y: oy - b.maxY, w: b.maxX - b.minX, h: b.maxY - b.minY }),
    /** a region {cx, cy, w} (the URL's z=) between the frames */
    toWorld: (r) => (r ? { cx: r.cx - ox, cy: oy - r.cy, w: r.w } : null),
    toKicad: (r) => (r ? { cx: r.cx + ox, cy: oy - r.cy, w: r.w } : null),
  };
}

export const FLASH_MS = 1000; // with the boxes hidden, a selected change is outlined this long
let stageIds = 0;

/** "Δx … Δy … d … mm" in KiCad mm, '' or a prompt while measuring. */
export function measureText(points, measuring) {
  if (points.length < 2) return measuring ? 'click two points' : '';
  const [a, b] = points;
  const dx = b.x - a.x; const dy = b.y - a.y;
  return `Δx ${dx.toFixed(3)}  Δy ${dy.toFixed(3)}  d ${Math.hypot(dx, dy).toFixed(3)} mm`;
}

/**
 * A view2d stage in `wrap` (the .stage-wrap) for a KiCad-frame box. Returns the stage plus the kipr
 * conveniences, everything in KiCad mm.
 */
export function createKiprStage(v2, wrap, { box, origin = [0, 0], flip = false, renderer = null, readout = null, zoomLabel = null, boxes = true, minRender = 0 }) {
  const f = kicadFrame(origin);
  const stage = v2.createStage(wrap, { renderer, bounds: f.bounds(box), flip, injectCss: false, measureLabel: false, padding: 0.02, settleMs: 250, minRender }); // re-render after the URL write (200 ms)
  let marks = [];
  let hl = null;
  let hlSides = null; // {base, head}: per-pane highlight for things that moved
  let boxesOn = boxes;
  let quiet = null; // {boxes, holes}: areas washed out (the schematic's moved items in its smart diff)
  const sid = ++stageIds;
  let flashTimer = null;
  const measureListeners = new Set();
  const transformListeners = new Set();
  let lastView = '';
  const busy = el('div', { class: 'loading small stage-busy' }, 'Rendering…');
  busy.hidden = true;
  wrap.append(busy);

  const rect = (g, ctx, b, cls, attrs = {}) => {
    const w = f.bounds(b);
    ctx.svg('rect', { x: w.minX, y: w.minY, width: Math.max(w.maxX - w.minX, 0.01), height: Math.max(w.maxY - w.minY, 0.01), class: cls, ...attrs }, g);
  };
  const overlay = stage.addOverlay({ space: 'world', className: 'marks', draw: (g, ctx) => {
    if (quiet?.boxes.length) {
      // paper over the quiet boxes, minus the holes (changes that overlap them)
      const id = `kipr-quiet-${sid}-${ctx.pane}`;
      const mask = ctx.svg('mask', { id, maskUnits: 'userSpaceOnUse', x: -1e5, y: -1e5, width: 2e5, height: 2e5 }, g);
      for (const b of quiet.boxes) rect(mask, ctx, b, null, { fill: '#fff' });
      for (const b of quiet.holes) rect(mask, ctx, b, null, { fill: '#000' });
      const x0 = Math.min(...quiet.boxes.map((b) => b.x)); const y0 = Math.min(...quiet.boxes.map((b) => b.y));
      const x1 = Math.max(...quiet.boxes.map((b) => b.x + b.w)); const y1 = Math.max(...quiet.boxes.map((b) => b.y + b.h));
      rect(g, ctx, { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, 'quiet-wash', { mask: `url(#${id})` });
    }
    for (const m of boxesOn ? marks : []) rect(g, ctx, m.box, `mark ${m.cls || ''}`);
    const hb = (ctx.side && hlSides?.[ctx.side]) || hl;
    if (hb) {
      const pad = Math.max(0.6, Math.min(hb.w, hb.h) * 0.1);
      rect(g, ctx, { x: hb.x - pad, y: hb.y - pad, w: hb.w + 2 * pad, h: hb.h + 2 * pad }, boxesOn ? 'hl' : 'hl flash');
    }
  } });

  stage.on('view', ({ view }) => {
    if (zoomLabel) zoomLabel.textContent = `${view.s.toFixed(1)} px/mm`;
    const key = `${view.cx.toFixed(4)},${view.cy.toFixed(4)},${view.s.toFixed(5)}`;
    wrap.dataset.view = key; // for tests
    // view2d also emits 'view' when new tiles land: only a real pan / zoom counts as a transform
    if (key === lastView) return;
    lastView = key;
    for (const fn of transformListeners) fn(view);
  });
  stage.on('move', (e) => {
    if (!readout) return;
    const [x, y] = f.kicad(e.x, e.y);
    readout.textContent = `x ${x.toFixed(3)}  y ${y.toFixed(3)} mm`;
  });
  stage.on('leave', () => { if (readout) readout.textContent = ''; });
  stage.on('busy', (e) => { busy.hidden = !e.busy; });
  stage.on('measure', () => { const t = api.measureText(); for (const fn of measureListeners) fn(t); });

  const kpts = () => stage.getState().measure.map((p) => { const [x, y] = f.kicad(p.x, p.y); return { x, y }; });

  const api = {
    v2, stage, frame: f,
    get box() { return box; },
    get boxes() { return boxesOn; },
    get measuring() { return stage.tool === 'measure'; },
    get measurePoints() { return kpts(); },
    /** A new world box (a doc layer came or went): the region on show stays. */
    setBox(b) { box = b; stage.setBounds(f.bounds(b)); },
    setFlip(on) { stage.setFlip(on); },
    fit() { stage.fit(); },
    zoomTo(b, o) { stage.zoomTo(f.bounds(b), o); },
    /** The region on show (KiCad {cx, cy, w}), or null while fitted. */
    region() { return f.toKicad(stage.getRegion()); },
    showRegion(r) { stage.setRegion(f.toWorld(r)); },
    setMarks(list) { marks = list || []; overlay.invalidate(); },
    /** Wash out `boxes` except `holes` (KiCad boxes), e.g. moved items under an ink diff; null: none. */
    setQuiet(q) { quiet = q?.boxes?.length ? { boxes: q.boxes, holes: q.holes || [] } : null; overlay.invalidate(); },
    /** Highlight box b; `sides` {base, head} overrides it on the base / head pane of a side-by-side view. */
    highlight(b, sides = null) {
      hl = b || null; hlSides = sides;
      clearTimeout(flashTimer);
      if (hl && !boxesOn) flashTimer = setTimeout(() => { hl = null; hlSides = null; overlay.invalidate(); }, FLASH_MS);
      overlay.invalidate();
    },
    setBoxes(on) {
      boxesOn = !!on;
      if (!boxesOn) { clearTimeout(flashTimer); hl = null; hlSides = null; }
      overlay.invalidate();
    },
    setMeasuring(on, points = []) {
      stage.setTool(on ? 'measure' : 'pan');
      if (on) stage.setMeasure(points.map((p) => { const [x, y] = f.point(p.x, p.y); return { x, y }; }));
      const t = api.measureText();
      for (const fn of measureListeners) fn(t);
    },
    measureText() { return measureText(kpts(), stage.tool === 'measure'); },
    onMeasure(fn) { measureListeners.add(fn); },
    /** fn(view) after a pan / zoom / fit (not when tiles are redrawn). */
    onTransform(fn) { transformListeners.add(fn); return () => transformListeners.delete(fn); },
    destroy() { clearTimeout(flashTimer); stage.destroy(); busy.remove(); },
  };
  return api;
}
