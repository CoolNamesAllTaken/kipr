// Layout diff: gerber-rendered board (realistic top/bottom faces and a per-layer view), layer toggles,
// per-layer pixel diff, side-by-side / onion / swipe, change list that zooms, measure tool.
// Without WebGL2 (or from file://) it falls back to the per-layer SVG exports.
import { el, clear, badge, arr, obj, bbox, fetchText, parseViewBox, debounce } from './util.js';
import { createStage, PX_PER_MM } from './panzoom.js';
import { createChangeList, describeChange } from './changes.js';
import { loadImage, rasterize, rasterScale, diffRasters, bitmapOf } from './raster.js';
import { createModeBar, legend } from './widgets.js';
import { comparePanes } from './compare.js';
import {
  layerList, sortLayers, defaultOn, faceLayers, gerberOf, svgOf, boardRect, gerberOrigin,
  layerColor, cssColor, grow, union,
} from './board.js';
import { renderGerbers, renderFace, renderLayerDiff, gerberUnavailableReason } from './gerber.js';

const MODES = [['side', 'Side by side'], ['diff', 'Diff'], ['onion', 'Onion skin'], ['swipe', 'Swipe']];
const VIEWS = [['top', 'Top'], ['bottom', 'Bottom'], ['layers', 'Layers']];
// same red / green / grey as the SVG and schematic diffs (inkdiff.js DIFF_COLORS)
const GPU_DIFF_COLORS = { removed: [0.88, 0.16, 0.16], added: [0.12, 0.69, 0.27], unchanged: [0.43, 0.43, 0.43] };
const LAYER_ALPHA = { copper: 0.85, mask: 0.45, paste: 0.6, silk: 0.95, outline: 1, drill: 1, fab: 0.8, courtyard: 0.8, user: 0.7 };
const layerVisible = new Map(); // persists across projects, like the library viewer's layer state
let preferred = { mode: 'side', view: 'top' };

/** Which face a layer belongs to, for switching the realistic view when a change is selected. */
export function faceOf(layerId) {
  if (typeof layerId !== 'string') return null;
  if (layerId.startsWith('F.')) return 'top';
  if (layerId.startsWith('B.')) return 'bottom';
  return null;
}

/** The layer the diff shows first: modified copper on the preferred face, else any modified layer, else the first. */
export function pickDiffLayer(layers, face) {
  const changed = layers.filter((l) => l.status && l.status !== 'unchanged');
  const onFace = (l) => (face === 'bottom' ? l.side === 'bottom' : face === 'top' ? l.side === 'top' : true);
  return changed.find((l) => l.kind === 'copper' && onFace(l)) || changed.find(onFace) || changed[0] || layers[0] || null;
}

/** "at=x,y" from a DRC link -> a 4 mm box around the point. */
export function parseAt(v) {
  const m = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(String(v || ''));
  return m ? { x: +m[1] - 2, y: +m[2] - 2, w: 4, h: 4 } : null;
}

export function createLayoutView(project, container, ctx) {
  const pcb = obj(project.pcb);
  const layers = sortLayers(layerList(pcb));
  if (!pcb || !layers.length) {
    container.append(el('div', { class: 'empty' }, pcb ? 'No layers in this PCB export.' : 'No PCB in this project (or it was not exported).'));
    return { destroy() {} };
  }
  const params = ctx.route.params;
  const noGl = gerberUnavailableReason();
  const hasGerbers = layers.some((l) => gerberOf(l, 'head') || gerberOf(l, 'base'));
  const useGl = !noGl && hasGerbers;
  const origin = gerberOrigin(pcb);
  const b0 = obj(pcb.board) || {};
  // the fork's palette resolves colour names / #hex itself; unknown values fall back to its defaults
  const palette = { mask: typeof b0.mask_color === 'string' ? b0.mask_color : undefined, silk: typeof b0.silk_color === 'string' ? b0.silk_color : undefined,
    finish: typeof b0.finish === 'string' && b0.finish.toLowerCase() !== 'none' ? b0.finish : undefined, maskAlpha: 0.85 };
  const sides = { base: project.status !== 'added', head: project.status !== 'removed' };
  const bothSides = sides.base && sides.head;
  const modes = bothSides ? MODES : [['single', sides.head ? 'Head (added)' : 'Base (removed)'], ['diff', 'Diff']];
  let mode = pick(modes, params.mode, preferred.mode);
  let view = pick(VIEWS, params.view, preferred.view);
  for (const l of layers) if (!layerVisible.has(l.id)) layerVisible.set(l.id, defaultOn(l));
  let focus = layers.find((l) => l.id === ctx.route.item) || pickDiffLayer(layers, view);
  let destroyed = false;
  let stage = null;
  let svgBoxes = new Map(); // svg path -> viewBox
  let renderR = 0;
  const cache = new Map();
  const modeCleanups = [];

  // --- shell
  const readout = el('span', { class: 'readout' });
  const zoomLbl = el('span', { class: 'readout zoom' });
  const measureOut = el('span', { class: 'readout measure' });
  const modeBar = createModeBar(modes, mode, (m) => { preferred.mode = m; setMode(m); pushRoute(); });
  const viewBar = createModeBar(VIEWS, view, (v) => { setView(v); pushRoute(); }, 'Board view');
  const measureBtn = el('button', { class: 'btn', title: 'Measure distance (r): click two points', 'aria-pressed': 'false', onclick: () => toggleMeasure() }, 'Measure');
  const extra = el('div', { class: 'toolbar-extra' });
  const toolbar = el('div', { class: 'toolbar' }, viewBar.el, modeBar.el, extra, el('span', { class: 'spacer' }), measureOut, readout, zoomLbl, measureBtn,
    el('button', { class: 'btn', title: 'Fit (f, or double-click)', onclick: () => stage?.fit() }, 'Fit'));
  const note = el('div', { class: 'notice small' });
  note.hidden = true;
  const stageWrap = el('div', { class: 'stage-wrap board' });
  const legendBox = el('div', { class: 'legend' });
  const layerPanel = el('div', { class: 'layer-panel' });
  const changeBox = el('div', { class: 'diff-changes card' });
  container.append(el('div', { class: 'diff-grid' },
    el('div', { class: 'side-col card' }, el('h3', {}, 'Layers'), layerPanel),
    el('div', { class: 'diff-main' }, toolbar, note, stageWrap, legendBox),
    changeBox));
  if (noGl && hasGerbers) { note.hidden = false; note.textContent = noGl; }
  if (!hasGerbers) { note.hidden = false; note.textContent = 'No gerbers in this export; showing the per-layer SVGs.'; }

  // --- changes
  const toItem = (c) => ({ ...describeChange(c), kind: c.kind, status: null, box: bbox(c.bbox_mm), sides: bbox(c.base_bbox_mm) || bbox(c.head_bbox_mm) ? { base: bbox(c.base_bbox_mm), head: bbox(c.head_bbox_mm) } : null, layer: typeof c.layer === 'string' ? c.layer : null, layers: arr(c.layers).concat(arr(c.holes)).filter((x) => typeof x === 'string') });
  const allChanges = arr(pcb.changes).filter(obj);
  // The backend orders changes by significance and marks the bulky, low-signal ones with a `group`
  // (routing, properties, minor): those are folded into collapsed buckets after the list.
  const GROUPED = new Set(['routing', 'properties', 'minor']);
  const grouped = (c) => GROUPED.has(c.group) || !!c.minor;
  const changeItems = allChanges.filter((c) => !grouped(c)).map(toItem);
  const buckets = new Map();
  for (const c of allChanges.filter(grouped)) {
    const group = c.minor ? 'minor' : c.group;
    let key;
    if (group === 'minor') key = `minor|${typeof c.detail === 'string' && c.detail ? c.detail : String(c.what || 'minor')}`;
    else if (group === 'routing') key = `routing|${c.kind} ${c.what}`;
    else key = 'properties|';
    if (!buckets.has(key)) buckets.set(key, { key, group, items: [] });
    const who = typeof c.ref === 'string' ? c.ref : typeof c.net === 'string' ? c.net : '';
    buckets.get(key).items.push({ ...toItem(c), title: group === 'routing' ? [who, c.layer].filter(Boolean).join(' · ') || c.kind : who || '?' });
  }
  const natural = new Intl.Collator(undefined, { numeric: true });
  const minorGroups = [...buckets.values()];
  for (const g of minorGroups) {
    g.items.sort((x, y) => natural.compare(x.title, y.title));
    const n = g.items.length;
    const [, rest] = g.key.split('|');
    if (g.group === 'minor') g.label = `${n} part${n === 1 ? '' : 's'}: ${rest}`;
    else if (g.group === 'routing') {
      const [kind, what] = rest.split(' ');
      g.label = `${n} ${kind}${n === 1 ? '' : 's'} ${what}`; // "214 vias added", "55 tracks rerouted"
    }
    else g.label = `${n} part${n === 1 ? '' : 's'}: fields / attributes only (see BOM)`;
    g.badge = g.group;
  }
  const ORDER = { routing: 0, properties: 1, minor: 2 };
  minorGroups.sort((x, y) => (ORDER[x.group] - ORDER[y.group]) || (y.items.length - x.items.length));
  const changes = createChangeList(changeBox, {
    title: 'Changes',
    empty: 'No itemised layout changes.',
    onSelect: (i, it) => {
      if (it.layer) {
        const l = layers.find((x) => x.id === it.layer);
        const face = faceOf(it.layer);
        if (l && mode === 'diff') { focus = l; renderLayerPanel(); }
        if (face && view !== 'layers' && face !== view) setView(face);
        else if (l && mode === 'diff') setMode('diff');
      }
      pushRoute(i);
      if (it.box && stage) { stage.zoomTo(it.box); stage.highlight(it.box, it.sides); }
    },
  });
  changes.set(changeItems);
  changes.setMinor(minorGroups);

  function pushRoute(c = changes.current) {
    ctx.setRoute({ item: focus?.id || null, params: { view, mode, c: c >= 0 ? c : null } }, true);
  }

  // --- layer panel
  function renderLayerPanel() {
    clear(layerPanel);
    const preset = (label, pred) => el('button', { class: 'btn small', onclick: () => { for (const l of layers) layerVisible.set(l.id, pred(l)); if (view !== 'layers') setView('layers'); else refresh(); renderLayerPanel(); } }, label);
    layerPanel.append(el('div', { class: 'presets' },
      preset('All', () => true), preset('Front', (l) => l.side !== 'bottom' && l.side !== 'inner'), preset('Back', (l) => l.side !== 'top' && l.side !== 'inner'),
      preset('Copper', (l) => ['copper', 'outline', 'drill'].includes(l.kind)), preset('Changed', (l) => (l.status && l.status !== 'unchanged') || l.kind === 'outline')));
    const ul = el('ul', { class: 'layer-list' });
    for (const l of [...layers].reverse()) {
      const id = `ly-${l.id.replace(/[^A-Za-z0-9]/g, '_')}`;
      const cb = el('input', { type: 'checkbox', id, checked: layerVisible.get(l.id) || null, 'aria-label': `show ${l.id}` });
      cb.addEventListener('change', () => { layerVisible.set(l.id, cb.checked); if (view === 'layers') refresh(); });
      const sw = el('span', { class: 'swatch' });
      sw.style.background = cssColor(layerColor(l));
      ul.append(el('li', { class: `layer-row${focus === l ? ' focus' : ''}` },
        cb, sw,
        el('button', { class: 'layer-name', title: 'Diff this layer', onclick: () => { focus = l; renderLayerPanel(); if (mode !== 'diff') { setMode('diff'); } else setMode('diff'); pushRoute(); } }, l.id),
        badge('status', l.status)));
    }
    layerPanel.append(ul, el('p', { class: 'hint' }, 'Checkboxes: layers of the Layers view. Click a name to diff that layer.'));
  }

  // --- world box
  function worldBox() {
    // base and head outlines can differ (a board that grew): frame both
    const b = union([boardRect(pcb), boardRect({ board: obj(obj(pcb.board)?.base) }), boardRect({ board: obj(obj(pcb.board)?.head) })]);
    if (b) return grow(b, Math.max(2, Math.max(b.w, b.h) * 0.03));
    const u = union([...svgBoxes.values()]) || union(changeItems.map((c) => c.box));
    return u ? grow(u, 2) : { x: 0, y: 0, w: 100, h: 80 };
  }

  // --- content: gerber canvases or SVG stacks
  function layerSetFor(side) {
    if (view === 'layers') {
      return layers.filter((l) => layerVisible.get(l.id)).map((l) => ({ l, path: gerberOf(l, side), svg: svgOf(l, side) }));
    }
    const f = faceLayers(layers, view);
    return [f.outline, f.copper, f.mask, f.silk, ...f.drills].filter(Boolean).map((l) => ({ l, path: gerberOf(l, side), svg: svgOf(l, side) }));
  }

  function gerberJob(side) {
    return layerSetFor(side).filter((x) => x.path)
      .map(({ l, path }) => ({ path, color: layerColor(l), alpha: LAYER_ALPHA[l.kind] ?? 0.8, kind: l.kind }));
  }

  function wantR() {
    const box = worldBox();
    const dpr = window.devicePixelRatio || 1;
    const cssPerMm = stage ? stage.view.s * PX_PER_MM : 6;
    let r = Math.max(4, cssPerMm * dpr);
    r = 2 ** (Math.ceil(Math.log2(r) * 2) / 2); // steps of sqrt(2), so small zooms don't re-render
    return Math.min(r, rasterScale(box.w, box.h, 64));
  }

  function makeSide(side) {
    if (!sides[side]) return null;
    const box = worldBox();
    const holder = el('div', { class: 'layer-holder', dataset: { side } });
    stage.place(holder, box);
    if (useGl) {
      let key;
      let make;
      if (view === 'layers') {
        const job = gerberJob(side);
        if (!job.length) { holder.append(el('div', { class: 'missing-msg' }, 'no gerbers for these layers')); return holder; }
        key = `${side}|layers|${JSON.stringify(job.map((j) => [j.path, j.color]))}|${renderR}`;
        make = () => renderGerbers(job, box, renderR, { origin });
      } else {
        const f = faceLayers(layers, view);
        const face = { outline: gerberOf(f.outline, side), copper: gerberOf(f.copper, side), mask: gerberOf(f.mask, side), silk: gerberOf(f.silk, side),
          drills: f.drills.map((l) => gerberOf(l, side)).filter(Boolean) };
        if (!face.outline && !face.copper) { holder.append(el('div', { class: 'missing-msg' }, 'no gerbers for this face')); return holder; }
        key = `${side}|${view}|${JSON.stringify(face)}|${renderR}`;
        make = () => renderFace(face, view, box, renderR, { origin, palette });
      }
      if (!cache.has(key)) {
        if (cache.size > 16) cache.delete(cache.keys().next().value);
        cache.set(key, make());
      }
      holder.append(el('div', { class: 'loading small' }, 'Rendering…'));
      cache.get(key).then(({ canvas, failures }) => {
        if (destroyed) return;
        const c = canvas.cloneNode(false);
        c.getContext('2d').drawImage(canvas, 0, 0);
        c.className = 'layer-canvas';
        clear(holder).append(c);
        if (failures.length) showNote(`Could not render: ${failures.map((f) => f.path).join(', ')}`);
      }).catch((e) => { clear(holder).append(el('div', { class: 'missing-msg' }, `Render failed: ${e.message}`)); });
      return holder;
    }
    // SVG fallback: one bitmap per layer, rasterised at its own viewBox (KiCad mm) and placed there
    const layerSet = view === 'layers' ? layerSetFor(side) : layerSetFor(side).filter(({ l }) => l.kind !== 'mask');
    for (const { l, svg } of layerSet) {
      if (!svg) continue;
      const vb = svgBoxes.get(svg) || box;
      const slot = el('div', { class: 'layer-slot', dataset: { layer: l.id } });
      const placed = stage.place(el('div'), vb);
      for (const k of ['left', 'top']) slot.style[k] = `${parseFloat(placed.style[k]) - parseFloat(holder.style[k])}px`;
      slot.style.width = placed.style.width; slot.style.height = placed.style.height;
      holder.append(slot);
      bitmapOf(svg, vb, Math.min(renderR, rasterScale(vb.w, vb.h, 64))).then((c) => {
        if (destroyed) return;
        const copy = c.cloneNode(false);
        copy.getContext('2d').drawImage(c, 0, 0);
        copy.className = 'layer-canvas';
        slot.append(copy);
      }).catch(() => {});
    }
    return holder;
  }

  function showNote(t) { note.hidden = false; note.textContent = t; }

  // --- per-layer diff
  const diffCache = new Map();
  function computeDiff(layer) {
    const key = `${layer.id}|${useGl ? renderR : 'svg'}`;
    if (!diffCache.has(key)) {
      const box = worldBox();
      let p;
      if (useGl) {
        const r = renderR;
        const one = (side) => (gerberOf(layer, side) && sides[side]
          ? renderGerbers([{ path: gerberOf(layer, side), color: [1, 1, 1], alpha: 1, kind: layer.kind }], box, r, { origin }).then(({ canvas }) => canvasRgba(canvas))
          : Promise.resolve(null));
        p = renderLayerDiff(sides.base ? gerberOf(layer, 'base') : null, sides.head ? gerberOf(layer, 'head') : null, box, r,
          { origin, kind: layer.kind === 'drill' ? 'drill' : 'gerber', colors: GPU_DIFF_COLORS })
          .catch(() => Promise.all([one('base'), one('head')]).then(([b, h]) => ensure(b, h, box, r)).then(([b, h]) => diffRasters(b, h, box, r, { mode: 'alpha', tol: 1 })));
      } else {
        const r = rasterScale(box.w, box.h, 12);
        const one = (side) => (svgOf(layer, side) && sides[side]
          ? loadImage(svgOf(layer, side)).then((img) => rasterize(img, svgBoxes.get(svgOf(layer, side)) || box, box, r))
          : Promise.resolve(null));
        p = Promise.all([one('base'), one('head')]).then(([b, h]) => ensure(b, h, box, r)).then(([b, h]) => diffRasters(b, h, box, r, { mode: 'ink', tol: 1 }));
      }
      diffCache.set(key, p);
    }
    return diffCache.get(key);
  }
  function ensure(b, h, box, r) {
    if (b || h) return [b, h];
    const w = Math.max(1, Math.round(box.w * r)); const hh = Math.max(1, Math.round(box.h * r));
    return [{ data: new Uint8ClampedArray(w * hh * 4), w, h: hh }, null];
  }

  // --- modes / views
  let modeToken = 0;
  function setMode(m) {
    const token = ++modeToken;
    mode = m;
    modeBar.select(m);
    for (const c of modeCleanups.splice(0)) c();
    clear(extra); clear(legendBox);
    if (!stage) return;
    clear(stageWrap);
    stageWrap.className = `stage-wrap board${m === 'side' ? ' split' : ''}${view === 'layers' ? ' dark' : ''}`;
    let panes;
    if (m === 'diff') {
      const layer = focus || pickDiffLayer(layers, view);
      const under = makeSide(sides.head ? 'head' : 'base');
      if (under) under.classList.add('faint');
      const holder = el('div', { class: 'diff-holder' }, el('div', { class: 'loading' }, 'Computing diff…'));
      stage.place(holder, worldBox());
      panes = [stage.pane(`diff: ${layer ? layer.id : '—'}`, under, holder)];
      legendBox.append(...legend());
      // only the listed changes that can alter this layer get a box: the diff itself has to pop
      stage.setMarks(changeItems.filter((c) => c.box && layer && (c.layer === layer.id || c.layers.includes(layer.id))).map((c) => ({ box: c.box, cls: 'outline' })));
      if (layer) {
        computeDiff(layer).then((d) => {
          if (destroyed || token !== modeToken) return;
          const c = d.canvas.cloneNode(false);
          c.getContext('2d').drawImage(d.canvas, 0, 0);
          c.className = 'diff-canvas';
          clear(holder).append(c);
          legendBox.append(el('span', { class: 'muted' }, `${layer.id}: ${d.regions.length} changed area${d.regions.length === 1 ? '' : 's'}`));
          if (!changeItems.length) {
            changes.set(d.regions.map((q, i) => ({ title: `${layer.id} change ${i + 1}`, detail: `${q.w.toFixed(2)} × ${q.h.toFixed(2)} mm`, kind: 'visual', box: q })));
            stage.setMarks(d.regions.map((q) => ({ box: q })));
          }
        }).catch((e) => { clear(holder).append(el('div', { class: 'missing-msg' }, `Diff failed: ${e.message}`)); });
      }
    } else {
      stage.setMarks(changeItems.filter((c) => c.box).map((c) => ({ box: c.box })));
      const c = comparePanes(stage, m, makeSide, extra, { single: sides.head ? 'head' : 'base' });
      modeCleanups.push(c.cleanup);
      panes = c.panes;
    }
    stageWrap.append(...panes);
    stage.setPanes(panes);
  }

  function buildStage() {
    const old = stage;
    const keep = old ? { ...old.view } : null;
    stage?.destroy();
    stage = createStage({ box: worldBox(), readout, zoomLabel: zoomLbl, flip: view === 'bottom' });
    stage.observe(stageWrap);
    stage.setMarks(changeItems.filter((c) => c.box).map((c) => ({ box: c.box })));
    stage.onMeasure((t) => { measureOut.textContent = t; });
    stage.onTransform(resharpen);
    if (!renderR) renderR = wantR();
    setMode(mode);
    if (keep && old && old.flip === stage.flip) { Object.assign(stage.view, keep); stage.apply(); }
  }

  function setView(v) {
    view = v;
    preferred.view = v;
    viewBar.select(v);
    buildStage();
  }

  function refresh() { setMode(mode); }

  // re-render sharper when zoomed in (gerber mode only)
  const resharpen = debounce(() => {
    if (destroyed || !stage) return;
    const r = wantR();
    if (r > renderR * 1.3 || r < renderR / 2.5) {
      renderR = r;
      setMode(mode);
    }
  }, 250);

  function toggleMeasure() {
    const on = !stage?.measuring;
    stage?.setMeasuring(on);
    measureBtn.setAttribute('aria-pressed', String(on));
    if (!on) measureOut.textContent = '';
  }

  // --- start: read SVG viewBoxes (fallback placement), then build
  const svgPaths = useGl ? [] : [...new Set(layers.flatMap((l) => [svgOf(l, 'base'), svgOf(l, 'head')]).filter(Boolean))];
  Promise.all(svgPaths.map((p) => fetchText(p).then((t) => [p, parseViewBox(t)]).catch(() => [p, null]))).then((pairs) => {
    if (destroyed) return;
    svgBoxes = new Map(pairs.filter(([, v]) => v));
    renderLayerPanel();
    buildStage();
    const ci = Number.parseInt(params.c, 10);
    const at = parseAt(params.at);
    if (Number.isInteger(ci) && ci >= 0 && ci < changes.items.length) changes.select(ci);
    else if (at) { stage.zoomTo(at); stage.highlight(at); }
  });

  return {
    destroy() { destroyed = true; resharpen.cancel(); for (const c of modeCleanups) c(); stage?.destroy(); },
    onParams(p) {
      if (p.view && p.view !== view && VIEWS.some(([v]) => v === p.view)) setView(p.view);
      if (p.mode && p.mode !== mode && modes.some(([m]) => m === p.mode)) setMode(p.mode);
      const ci = Number.parseInt(p.c, 10);
      if (Number.isInteger(ci) && ci !== changes.current && ci < changes.items.length) changes.select(ci);
      const at = parseAt(p.at);
      if (at && stage) { stage.zoomTo(at); stage.highlight(at); }
    },
    onKey(e) {
      if (e.key === 'n') { changes.next(); return true; }
      if (e.key === 'p') { changes.prev(); return true; }
      if (e.key === 'f') { stage?.fit(); return true; }
      if (e.key === 'r') { toggleMeasure(); return true; }
      if (e.key === 'v') { const i = VIEWS.findIndex(([v]) => v === view); setView(VIEWS[(i + 1) % VIEWS.length][0]); pushRoute(); return true; }
      if (e.key === 'm') {
        const i = modes.findIndex(([m]) => m === mode);
        preferred.mode = modes[(i + 1) % modes.length][0];
        setMode(preferred.mode); pushRoute(); return true;
      }
      if (e.key === '[' || e.key === ']') {
        const i = layers.indexOf(focus);
        focus = layers[(i + (e.key === ']' ? 1 : -1) + layers.length) % layers.length];
        renderLayerPanel();
        if (mode === 'diff') setMode('diff');
        pushRoute();
        return true;
      }
      if (e.key === 'Escape') { stage?.highlight(null); if (stage?.measuring) toggleMeasure(); return true; }
      return false;
    },
  };
}

function pick(list, want, pref) {
  if (list.some(([m]) => m === want)) return want;
  if (list.some(([m]) => m === pref)) return pref;
  return list[0][0];
}

function canvasRgba(canvas) {
  const g = canvas.getContext('2d', { willReadFrequently: true });
  return { data: g.getImageData(0, 0, canvas.width, canvas.height).data, w: canvas.width, h: canvas.height };
}
