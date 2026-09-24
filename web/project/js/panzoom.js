// Synced pan/zoom over one or more panes that show the same "world": a box in mm (sheet or board
// coordinates, KiCad frame, y down). Every pane holds a `world` div of box.w x box.h mm at PX_PER_MM,
// moved by one shared CSS transform, plus an SVG overlay in mm for change boxes, the highlight and
// the measure tool. Generic: the schematic and layout views both use it.
import { el, svgEl, clear } from './util.js';

export const PX_PER_MM = 4; // world pixels per mm at scale 1

/** View {tx, ty, s} that fits box (mm, relative to world origin) into a pw x ph pane with `pad` fraction margin. */
export function fitTransform(boxPx, pw, ph, pad = 0.04) {
  const s = Math.min(pw / boxPx.w, ph / boxPx.h) * (1 - 2 * pad);
  return { s, tx: (pw - boxPx.w * s) / 2 - boxPx.x * s, ty: (ph - boxPx.h * s) / 2 - boxPx.y * s };
}

/** Zoom by `factor` about pane point (px, py), clamped to [minS, maxS]. */
export function zoomAbout(v, px, py, factor, minS = 0.02, maxS = 2000) {
  const s = Math.min(Math.max(v.s * factor, minS), maxS);
  const f = s / v.s;
  return { s, tx: px - (px - v.tx) * f, ty: py - (py - v.ty) * f };
}

export function createStage({ box, readout = null, zoomLabel = null, flip = false }) {
  const view = { tx: 0, ty: 0, s: 1 };
  const W = box.w * PX_PER_MM;
  const H = box.h * PX_PER_MM;
  let panes = [];
  let fitted = false;
  let marks = [];
  let hl = null;
  let hlSides = null; // {base, head}: per-pane highlight for things that moved
  let measuring = false;
  let measure = [];
  const listeners = new Set();
  const cleanup = [];

  const mmToWorld = (x, y) => [(flip ? box.x + box.w - x : x - box.x) * PX_PER_MM, (y - box.y) * PX_PER_MM];

  /** A pane: `label` badge, children placed in world px (use place()). */
  function pane(label, ...children) {
    const world = el('div', { class: 'world' }, ...children);
    world.style.width = `${W}px`;
    world.style.height = `${H}px`;
    const overlay = svgEl('svg', { class: 'overlay', viewBox: `${box.x} ${box.y} ${box.w} ${box.h}`, preserveAspectRatio: 'none' });
    overlay.style.width = `${W}px`;
    overlay.style.height = `${H}px`;
    if (flip) overlay.style.transform = 'scaleX(-1)';
    world.append(overlay);
    const p = el('div', { class: 'pane' }, world, label ? el('div', { class: 'pane-label' }, label) : null);
    p._world = world;
    p._side = label === 'base' || label === 'head' ? label : null;
    p._overlay = overlay;
    attach(p);
    return p;
  }

  /** Position `node` over the mm rect {x, y, w, h} inside the world. */
  function place(node, r) {
    const [x0] = mmToWorld(flip ? r.x + r.w : r.x, r.y);
    node.style.position = 'absolute';
    node.style.left = `${x0}px`;
    node.style.top = `${(r.y - box.y) * PX_PER_MM}px`;
    node.style.width = `${r.w * PX_PER_MM}px`;
    node.style.height = `${r.h * PX_PER_MM}px`;
    if (flip) node.style.transform = 'scaleX(-1)';
    return node;
  }

  function setPanes(list) {
    panes = list;
    drawOverlay();
    if (!fitted) fit(); else apply();
  }

  function apply() {
    // overlay strokes are in mm (the SVG's viewBox); --px is one screen pixel in mm, so CSS can size them
    const px = `${1 / (view.s * PX_PER_MM)}`;
    for (const p of panes) {
      p._world.style.transform = `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`;
      p._overlay.style.setProperty('--px', px);
    }
    if (zoomLabel) zoomLabel.textContent = `${(view.s * PX_PER_MM).toFixed(1)} px/mm`;
    for (const fn of listeners) fn(view);
  }

  function paneSize() {
    const p = panes[0];
    return { pw: p?.clientWidth || 600, ph: p?.clientHeight || 400 };
  }

  function fit() {
    const { pw, ph } = paneSize();
    Object.assign(view, fitTransform({ x: 0, y: 0, w: W, h: H }, pw, ph, 0.02));
    fitted = true;
    apply();
  }

  /** Zoom to a mm box (KiCad frame); small boxes get at least `minMm` of context around them. */
  function zoomTo(b, { minMm = 8, pad = 0.18 } = {}) {
    const cx = b.x + b.w / 2;
    const cy = b.y + b.h / 2;
    const w = Math.max(b.w, minMm);
    const h = Math.max(b.h, minMm);
    const [x0, y0] = mmToWorld(flip ? cx + w / 2 : cx - w / 2, cy - h / 2);
    const { pw, ph } = paneSize();
    Object.assign(view, fitTransform({ x: x0, y: y0, w: w * PX_PER_MM, h: h * PX_PER_MM }, pw, ph, pad));
    fitted = true;
    apply();
  }

  function toMm(p, clientX, clientY) {
    const r = p.getBoundingClientRect();
    const wx = (clientX - r.left - view.tx) / view.s / PX_PER_MM;
    const wy = (clientY - r.top - view.ty) / view.s / PX_PER_MM;
    return { x: flip ? box.x + box.w - wx : box.x + wx, y: box.y + wy };
  }

  // --- overlay: change boxes, highlight, measure
  function drawOverlay() {
    for (const p of panes) {
      const o = clear(p._overlay);
      for (const m of marks) {
        o.append(svgEl('rect', { x: m.box.x, y: m.box.y, width: Math.max(m.box.w, 0.01), height: Math.max(m.box.h, 0.01), class: `mark ${m.cls || ''}` }));
      }
      const hb = (p._side && hlSides?.[p._side]) || hl;
      if (hb) {
        const hl = hb; // eslint-disable-line no-shadow
        const padMm = Math.max(0.6, Math.min(hl.w, hl.h) * 0.1);
        o.append(svgEl('rect', { x: hl.x - padMm, y: hl.y - padMm, width: hl.w + 2 * padMm, height: hl.h + 2 * padMm, class: 'hl' }));
      }
      if (measure.length) {
        const [a, b] = measure;
        o.append(svgEl('circle', { cx: a.x, cy: a.y, class: 'measure-pt' }));
        if (b) {
          o.append(svgEl('line', { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: 'measure-line' }));
          o.append(svgEl('circle', { cx: b.x, cy: b.y, class: 'measure-pt' }));
        }
      }
    }
  }
  function setMarks(list) { marks = list || []; drawOverlay(); }
  /** Highlight box b; `sides` {base, head} overrides it on the base / head pane of a side-by-side view. */
  function highlight(b, sides = null) { hl = b || null; hlSides = sides; drawOverlay(); }

  function measureText() {
    if (measure.length < 2) return measuring ? 'click two points' : '';
    const [a, b] = measure;
    const dx = b.x - a.x; const dy = b.y - a.y;
    return `Δx ${dx.toFixed(3)}  Δy ${dy.toFixed(3)}  d ${Math.hypot(dx, dy).toFixed(3)} mm`;
  }
  const measureListeners = new Set();
  function setMeasuring(on) {
    measuring = !!on;
    measure = [];
    for (const p of panes) p.classList.toggle('measuring', measuring);
    drawOverlay();
    for (const fn of measureListeners) fn(measureText());
  }

  // --- interaction (wheel zoom, drag pan, pinch, double-click fit, measure clicks)
  function attach(p) {
    const pointers = new Map();
    let pinchDist = 0;
    let moved = 0;
    p.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = p.getBoundingClientRect();
      Object.assign(view, zoomAbout(view, e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0015))));
      apply();
    }, { passive: false });
    p.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 && e.pointerType === 'mouse') return;
      p.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      moved = 0;
      p.classList.add('grabbing');
    });
    p.addEventListener('pointermove', (e) => {
      const mm = toMm(p, e.clientX, e.clientY);
      if (readout) readout.textContent = `x ${mm.x.toFixed(3)}  y ${mm.y.toFixed(3)} mm`;
      const prev = pointers.get(e.pointerId);
      if (!prev) return;
      if (pointers.size === 1) {
        moved += Math.abs(e.clientX - prev.x) + Math.abs(e.clientY - prev.y);
        view.tx += e.clientX - prev.x;
        view.ty += e.clientY - prev.y;
        apply();
      } else if (pointers.size === 2) {
        moved += 10;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinchDist) {
          const r = p.getBoundingClientRect();
          Object.assign(view, zoomAbout(view, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top, d / pinchDist));
          apply();
        }
        pinchDist = d;
        return;
      }
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    });
    const up = (e) => {
      const wasClick = pointers.has(e.pointerId) && pointers.size === 1 && moved < 4 && e.type === 'pointerup';
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinchDist = 0;
      if (!pointers.size) p.classList.remove('grabbing');
      if (wasClick && measuring) {
        const pt = toMm(p, e.clientX, e.clientY);
        measure = measure.length >= 2 ? [pt] : [...measure, pt];
        drawOverlay();
        for (const fn of measureListeners) fn(measureText());
      }
    };
    p.addEventListener('pointerup', up);
    p.addEventListener('pointercancel', up);
    p.addEventListener('pointerleave', () => { if (readout) readout.textContent = ''; });
    p.addEventListener('dblclick', () => fit());
  }

  const ro = new ResizeObserver(() => { if (fitted) apply(); });
  cleanup.push(() => ro.disconnect());

  return {
    box, W, H, view, flip,
    pane, place, setPanes, fit, zoomTo, apply, toMm, setMarks, highlight,
    observe(container) { ro.observe(container); },
    onTransform(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    onMeasure(fn) { measureListeners.add(fn); },
    setMeasuring, get measuring() { return measuring; },
    /** Pane-space x of a world mm x (for the swipe divider). */
    paneX(p, mmX) { return view.tx + mmToWorld(mmX, 0)[0] * view.s; },
    destroy() { for (const fn of cleanup) fn(); listeners.clear(); },
  };
}
