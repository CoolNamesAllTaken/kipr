// Layout diff: gerber-rendered board (realistic top/bottom faces and a per-layer view), layer toggles,
// per-layer pixel diff, side-by-side / onion / swipe, change list that zooms, measure tool.
// Without WebGL2 (or from file:// without the offline renderer) it falls back to the per-layer SVG exports.
// The stage, the renders and the compare modes are boarddd/view2d (stage2d.js); this is the app around it.
import { el, clear, badge, arr, obj, bbox, fetchText, parseViewBox, debounce, parseAtParam, fillViewport, assetUrl, OFFLINE } from './util.js';
import { loadView2d, view2dNow, createKiprStage } from './stage2d.js';
import { createChangeList, describeChange } from './changes.js';
import { createModeBar, legend, boxesToggle } from './widgets.js';
import { boxesShown, toggleBoxes } from './boxes.js';
import { showCompare, compareSliders, setCompareSliders, preferredMode, setPreferredMode } from './compare.js';
import { stepItem, layerNote } from './viewstate.js';
import {
  layerList, sortLayers, defaultOn, docExtent, frameBox, faceLayers, gerberOf, svgOf, boardRect, gerberOrigin,
  layerColor, cssColor, grow, union,
} from './board.js';
import { getRenderer, gerberUnavailableReason } from './gerber.js';

const MODES = [['side', 'Side by side'], ['diff', 'Diff'], ['onion', 'Onion skin'], ['swipe', 'Swipe']];
const VIEWS = [['top', 'Top'], ['bottom', 'Bottom'], ['layers', 'Layers']];
// same red / green / grey as the SVG and schematic ink diffs (boarddd/view2d DIFF_COLORS)
const GPU_DIFF_COLORS = { removed: [0.88, 0.16, 0.16], added: [0.12, 0.69, 0.27], unchanged: [0.43, 0.43, 0.43] };
const LAYER_ALPHA = { copper: 0.85, mask: 0.45, paste: 0.6, silk: 0.95, outline: 1, drill: 1, fab: 0.8, courtyard: 0.8, user: 0.7 };
const layerVisible = new Map(); // persists across projects, like the library viewer's layer state
let preferred = { view: 'top' }; // compare mode: preferredMode() (compare.js), shared with the schematic

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

/** "at=x,y" from a DRC link -> a 4 mm box around the point ("x,y,w,h": that box). */
export function parseAt(v) {
  return parseAtParam(v);
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
  let mode = pick(modes, params.mode, preferredMode());
  let view = pick(VIEWS, params.view, preferred.view);
  for (const l of layers) if (!layerVisible.has(l.id)) layerVisible.set(l.id, defaultOn(l));
  // The selected layer. While one is selected (`solo`: a click, [ / ], a layer in the URL) every compare
  // mode shows that one layer, base vs head; Top / Bottom / Layers clear the selection and show the
  // board view again. Diff always diffs `focus` (the default diff layer when nothing is selected).
  const urlLayer = layers.find((l) => l.id === ctx.route.item);
  let focus = urlLayer || pickDiffLayer(layers, view);
  let solo = !!urlLayer;
  let destroyed = false;
  let stage = null; // createKiprStage(): view2d stage in KiCad terms
  let cmp = null; // view2d compare on show
  let svgBoxes = new Map(); // svg path -> viewBox
  const cache = new Map(); // content key -> Promise<view2d content>: the same object keeps its tiles

  // --- shell
  const readout = el('span', { class: 'readout' });
  const zoomLbl = el('span', { class: 'readout zoom' });
  const measureOut = el('span', { class: 'readout measure' });
  // the panes change (side by side <-> one), so the region's width does: write the URL again once shown
  const modeBar = createModeBar(modes, mode, (m) => { setMode(m).then((shown) => { if (shown) pushRoute(); }); pushRoute(); });
  const viewBar = createModeBar(VIEWS, solo ? null : view, (v) => { showBoard(v); pushRoute(); }, 'Board view');
  const measureBtn = el('button', { class: 'btn', title: 'Measure distance (r): click two points', 'aria-pressed': 'false', onclick: () => toggleMeasure() }, 'Measure');
  const extra = el('div', { class: 'toolbar-extra' });
  const boxes = boxesToggle((on) => stage?.setBoxes(on));
  const toolbar = el('div', { class: 'toolbar' }, viewBar.el, modeBar.el, extra, el('span', { class: 'spacer' }), measureOut, readout, zoomLbl, boxes.el, measureBtn,
    el('button', { class: 'btn', title: 'Fit (f, or double-click)', onclick: () => stage?.fit() }, 'Fit'));
  const note = el('div', { class: 'notice small' });
  note.hidden = true;
  const layerNoteEl = el('div', { class: 'notice small layer-note', role: 'status' });
  layerNoteEl.hidden = true;
  const layerNoteElBox = el('div', { class: 'view-note' }, layerNoteEl);
  const stageWrap = el('div', { class: 'stage-wrap board' });
  const legendBox = el('div', { class: 'legend' });
  const layerPanel = el('div', { class: 'layer-panel' });
  const changeBox = el('div', { class: 'diff-changes card' });
  const mainCol = el('div', { class: 'diff-main' }, toolbar, note, layerNoteElBox, stageWrap, legendBox);
  container.append(el('div', { class: 'diff-grid' },
    el('div', { class: 'side-col card' }, el('h3', {}, 'Layers'), layerPanel),
    mainCol,
    changeBox));
  const stopFill = fillViewport(stageWrap, { until: mainCol, watch: [toolbar, note, legendBox] });
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
        // the change's layer becomes the selection where a layer is on show (Diff, or a selected layer)
        const take = l && (mode === 'diff' || solo) && l !== focus;
        if (take) { focus = l; solo = true; viewBar.select(null); markFocus(); }
        if (face && view !== 'layers' && face !== view) setView(face);
        else if (take) setMode(mode);
        updateLayerNote();
      }
      pushRoute(i);
      if (it.box && stage) { stage.zoomTo(it.box); stage.highlight(it.box, it.sides); }
    },
  });
  changes.set(changeItems);
  changes.setMinor(minorGroups);

  // The URL holds the whole view: layer (item), board view, compare mode, change, zoom region, sliders.
  function pushRoute(c = changes.current, replace = true) {
    writeView.cancel();
    ctx.setRoute({ item: solo ? focus?.id : null, params: { view, mode, c: c >= 0 ? c : null, ...viewParams() } }, replace);
  }
  function viewParams() {
    const sl = compareSliders();
    const v = view2dNow();
    if (!v) return {}; // not loaded yet: the URL keeps what it has
    const z = v.formatRegion(stage?.region());
    const out = { z, sw: v.formatSlider(sl.swipe, mode === 'swipe'), op: v.formatSlider(sl.opacity, mode === 'onion') };
    if (z) out.at = null; // zoomed somewhere else since: the region replaces a link's at=
    return out;
  }
  // zoom / pan and slider moves: replace the URL once they settle
  const writeView = debounce(() => { if (!destroyed && stage) ctx.setRoute({ params: viewParams() }, true); }, 200);

  /**
   * Select layer l: every compare mode now shows that layer, base vs head (Diff: its diff). Only the
   * selection changes: the compare mode, sliders, Boxes, ticked layers and zoom region stay (a doc layer
   * reframes the world but keeps the region on show). A new history entry unless `push` is false.
   */
  function selectLayer(l, push = true) {
    if (!l) return;
    if (l !== focus || !solo) {
      focus = l;
      solo = true;
      viewBar.select(null);
      setMode(mode); // also marks the row
    }
    if (push) pushRoute(changes.current, false);
  }

  /** Top / Bottom / Layers picked: back to that board view, no layer selected (Diff keeps its layer). */
  function showBoard(v) {
    const was = solo;
    solo = false;
    markFocus();
    if (v !== view) setView(v);
    else { viewBar.select(v); if (was) setMode(mode); }
  }

  function updateLayerNote() {
    const t = layerNote(focus, mode, { bothSides, solo });
    layerNoteEl.hidden = !t;
    layerNoteEl.textContent = t || '';
  }

  // --- layer panel
  // the highlighted row: the selected layer, or in Diff the diffed one
  function markFocus() {
    for (const row of layerPanel.querySelectorAll('.layer-row')) {
      const on = row.dataset.layer === focus?.id && (solo || mode === 'diff');
      row.classList.toggle('focus', on);
      const name = row.querySelector('.layer-name');
      if (on && solo) name?.setAttribute('aria-current', 'true'); else name?.removeAttribute('aria-current');
    }
  }
  function renderLayerPanel() {
    clear(layerPanel);
    const preset = (label, pred) => el('button', { class: 'btn small', onclick: () => { for (const l of layers) layerVisible.set(l.id, pred(l)); showBoard('layers'); refresh(); renderLayerPanel(); pushRoute(); } }, label);
    layerPanel.append(el('div', { class: 'presets' },
      preset('All', () => true), preset('Front', (l) => l.side !== 'bottom' && l.side !== 'inner'), preset('Back', (l) => l.side !== 'top' && l.side !== 'inner'),
      preset('Copper', (l) => ['copper', 'outline', 'drill'].includes(l.kind)), preset('Changed', (l) => (l.status && l.status !== 'unchanged') || l.kind === 'outline')));
    const ul = el('ul', { class: 'layer-list' });
    for (const l of [...layers].reverse()) {
      const id = `ly-${l.id.replace(/[^A-Za-z0-9]/g, '_')}`;
      const cb = el('input', { type: 'checkbox', id, checked: layerVisible.get(l.id) || null, 'aria-label': `show ${l.id}` });
      cb.addEventListener('change', () => { layerVisible.set(l.id, cb.checked); if (view === 'layers' && !solo) refresh(); });
      const sw = el('span', { class: 'swatch' });
      sw.style.background = cssColor(layerColor(l));
      ul.append(el('li', { class: 'layer-row', dataset: { layer: l.id } },
        cb, sw,
        el('button', { class: 'layer-name', title: 'Show this layer, base vs head, in the current compare mode ([ / ] step); Top / Bottom / Layers go back to the board', onclick: () => selectLayer(l) }, l.id),
        badge('status', l.status)));
    }
    markFocus();
    layerPanel.append(ul, el('p', { class: 'hint' }, 'Checkboxes: layers of the Layers view. Click a name (or [ / ]) to compare that one layer in any mode; Top / Bottom / Layers go back to the board.'));
  }

  // --- world box
  function boardBox() {
    // base and head outlines can differ (a board that grew): frame both
    const b = union([boardRect(pcb), boardRect({ board: obj(obj(pcb.board)?.base) }), boardRect({ board: obj(obj(pcb.board)?.head) })]);
    if (b) return grow(b, Math.max(2, Math.max(b.w, b.h) * 0.03));
    const u = union([...svgBoxes.values()]) || union(changeItems.map((c) => c.box));
    return u ? grow(u, 2) : { x: 0, y: 0, w: 100, h: 80 };
  }
  // The board, plus the documentation layers on show (fab notes and drawings often sit outside the
  // outline): the visible ones in the Layers view, the selected / diffed one. Board layers alone: the board.
  function worldBox() {
    const ext = [];
    if (mode === 'diff' || solo) ext.push(docExtent(focus || pickDiffLayer(layers, view)));
    else if (view === 'layers') for (const l of layers) if (layerVisible.get(l.id)) ext.push(docExtent(l));
    return frameBox(boardBox(), ext);
  }
  const boxKey = (b) => [b.x, b.y, b.w, b.h].map((v) => v.toFixed(3)).join(',');

  // --- content: view2d content for a side (gerber faces / layer stacks, or the SVG exports)
  // single: the selected layer alone (in the Layers view's colours), else the board view
  function layerSetFor(side, single = solo) {
    if (single) return [{ l: focus, path: gerberOf(focus, side), svg: svgOf(focus, side) }];
    if (view === 'layers') {
      return layers.filter((l) => layerVisible.get(l.id)).map((l) => ({ l, path: gerberOf(l, side), svg: svgOf(l, side) }));
    }
    const f = faceLayers(layers, view);
    return [f.outline, f.copper, f.mask, f.silk, ...f.drills].filter(Boolean).map((l) => ({ l, path: gerberOf(l, side), svg: svgOf(l, side) }));
  }

  function memo(key, make) {
    if (!cache.has(key)) {
      if (cache.size > 32) cache.delete(cache.keys().next().value);
      const p = make();
      p.catch(() => cache.delete(key));
      cache.set(key, p);
    }
    return cache.get(key);
  }

  // gerber / drill text as a view2d source, null when missing or empty (e.g. an NPTH file without holes)
  const source = (path, name, drill = false) => (path
    ? fetchText(path).then((text) => (v2.isEmptySource(text, drill) ? null : { source: text, name })).catch((e) => { showNote(`Could not render: ${path} (${e.message})`); return null; })
    : Promise.resolve(null));
  // an SVG export over its viewBox: over http the URL, from disk its text (a file:// image taints canvases)
  const svgImage = (path) => (OFFLINE ? fetchText(path) : Promise.resolve(assetUrl(path)))
    .then((src) => v2.image(src, stage.frame.bounds(svgBoxes.get(path) || worldBox())));

  // single (default: a layer is selected): that layer alone; null when this side has none of it, so the
  // compare pane says "not in base / head". Diff's faint underlay is always the board view.
  function makeSide(side, single = solo) {
    if (!sides[side]) return Promise.resolve(null);
    if (single && !gerberOf(focus, side) && !svgOf(focus, side)) return Promise.resolve(null);
    if (useGl) {
      if (single || view === 'layers') {
        const set = layerSetFor(side, single).filter((x) => x.path);
        if (!set.length) return Promise.resolve(null);
        const key = `${side}|layers|${JSON.stringify(set.map(({ l, path }) => [path, layerColor(l)]))}`;
        return memo(key, () => Promise.all(set.map(({ l, path }) => source(path, l.id, l.kind === 'drill')))
          .then((srcs) => v2.layers(set.map(({ l }, i) => srcs[i] && { ...srcs[i], color: layerColor(l), alpha: LAYER_ALPHA[l.kind] ?? 0.8, kind: l.kind === 'drill' ? 'drill' : 'gerber' }).filter(Boolean))));
      }
      const f = faceLayers(layers, view);
      const paths = { outline: gerberOf(f.outline, side), copper: gerberOf(f.copper, side), mask: gerberOf(f.mask, side), silk: gerberOf(f.silk, side),
        drills: f.drills.map((l) => gerberOf(l, side)).filter(Boolean) };
      if (!paths.outline && !paths.copper) return Promise.resolve(null);
      return memo(`${side}|${view}|${JSON.stringify(paths)}`, async () => {
        const [outline, copper, mask, silk, ...drills] = await Promise.all([source(paths.outline, 'outline'), source(paths.copper, 'copper'),
          source(paths.mask, 'mask'), source(paths.silk, 'silk'), ...paths.drills.map((p) => source(p, 'drill', true))]);
        // not mirrored: the stage mirrors the bottom view itself
        return v2.face({ outline, [view]: { copper, mask, silk }, drills: drills.filter(Boolean) }, { side: view, palette, holes: true, clipSilk: true });
      });
    }
    // SVG fallback: one image per layer, placed over its own viewBox (KiCad mm)
    const set = single || view === 'layers' ? layerSetFor(side, single) : layerSetFor(side).filter(({ l }) => l.kind !== 'mask');
    const svgs = set.filter((x) => x.svg);
    if (!svgs.length) return Promise.resolve(null);
    return memo(`${side}|svg|${svgs.map((x) => x.svg).join('|')}|${boxKey(worldBox())}`, () => Promise.all(svgs.map((x) => svgImage(x.svg))));
  }

  function showNote(t) { note.hidden = false; note.textContent = t; }

  // --- per-layer diff: the GPU layer diff of the gerbers, or the ink diff of the SVG exports
  function diffContent(layer) {
    const box = worldBox();
    if (useGl) {
      const drill = layer.kind === 'drill';
      return memo(`diff|${layer.id}`, () => Promise.all(['base', 'head'].map((s) => (sides[s] ? source(gerberOf(layer, s), layer.id, drill) : null)))
        .then(([b, h]) => v2.diff(b, h, { colors: GPU_DIFF_COLORS, style: { unchanged: { alpha: 0.45 } }, regions: true })));
    }
    const one = (s) => (sides[s] && svgOf(layer, s) ? svgImage(svgOf(layer, s)) : Promise.resolve(null));
    return memo(`diff|svg|${layer.id}|${boxKey(box)}`, () => Promise.all([one('base'), one('head')])
      .then(([b, h]) => v2.inkdiff(b && { src: b.src, rect: b.rect }, h && { src: h.src, rect: h.rect }, stage.frame.bounds(box), { mode: 'ink', tol: 1 })));
  }

  // --- modes / views
  let modeToken = 0;
  let diffShown = null; // the diff content on show (its render reports the changed areas)
  async function setMode(m) {
    const token = ++modeToken;
    mode = m;
    if (m !== 'single') setPreferredMode(m); // the mode on show is the one the next view opens in
    modeBar.select(m);
    updateLayerNote();
    markFocus();
    if (!stage) return;
    const box = worldBox();
    if (boxKey(box) !== boxKey(stage.box)) { stage.setBox(box); stageWrap.dataset.world = boxKey(box); } // a doc layer came or went: new frame, same region
    stageWrap.className = `stage-wrap board${m === 'side' ? ' split' : ''}${view === 'layers' || (solo && m !== 'diff') ? ' dark' : ''}`;
    let content;
    const layer = focus || pickDiffLayer(layers, view);
    if (m === 'diff') {
      const [under, diff] = await Promise.all([makeSide(sides.head ? 'head' : 'base', false), layer ? diffContent(layer) : null]);
      content = { underlay: under, diff };
    } else {
      const [base, head] = await Promise.all([makeSide('base'), makeSide('head')]);
      content = { base, head };
    }
    if (destroyed || token !== modeToken) return false;
    cmp?.destroy();
    clear(extra); clear(legendBox);
    diffShown = content.diff ?? null;
    if (m === 'diff') {
      legendBox.append(...legend());
      // only the listed changes that can alter this layer get a box: the diff itself has to pop
      stage.setMarks(changeItems.filter((c) => c.box && layer && (c.layer === layer.id || c.layers.includes(layer.id))).map((c) => ({ box: c.box, cls: 'outline' })));
    } else {
      stage.setMarks(changeItems.filter((c) => c.box).map((c) => ({ box: c.box })));
    }
    cmp = showCompare(stage, m, content, extra, {
      single: sides.head ? 'head' : 'base',
      labels: { base: 'base', head: 'head', diff: `diff: ${layer ? layer.id : '—'}` },
      onSlide: () => writeView(),
    });
    return true;
  }

  function onRender(e) {
    if (destroyed || !diffShown || e.content !== diffShown || !e.info?.regions) return;
    const regions = e.info.regions.map((q) => stage.frame.box(q));
    const layer = focus || pickDiffLayer(layers, view);
    legendBox.querySelector('.diff-count')?.remove();
    legendBox.append(el('span', { class: 'muted diff-count' }, `${layer.id}: ${regions.length} changed area${regions.length === 1 ? '' : 's'}`));
    if (!changeItems.length) {
      changes.set(regions.map((q, i) => ({ title: `${layer.id} change ${i + 1}`, detail: `${q.w.toFixed(2)} × ${q.h.toFixed(2)} mm`, kind: 'visual', box: q })));
      stage.setMarks(regions.map((q) => ({ box: q })));
    }
  }

  // The stage is made once; another board view mirrors it (bottom) and keeps the region on show
  // (view2d's world is the board frame, so the same region also across top <-> bottom).
  function buildStage() {
    const box = worldBox();
    stage = createKiprStage(v2, stageWrap, { box, origin, flip: view === 'bottom', renderer: useGl ? getRenderer : null, readout, zoomLabel: zoomLbl, boxes: boxesShown() });
    stageWrap.dataset.world = boxKey(box); // KiCad mm x,y,w,h of the frame (for tests and scripts)
    stage.onMeasure((t) => { measureOut.textContent = t; });
    stage.onTransform(() => writeView());
    stage.stage.on('render', onRender);
    stage.stage.on('error', (e) => showNote(`Render failed: ${e.error?.message || e.error}`));
    return setMode(mode);
  }

  function setView(v) {
    view = v;
    preferred.view = v;
    viewBar.select(solo ? null : v);
    stage?.setFlip(v === 'bottom');
    setMode(mode);
  }

  function refresh() { setMode(mode); }

  function toggleMeasure() {
    const on = !stage?.measuring;
    stage?.setMeasuring(on);
    measureBtn.setAttribute('aria-pressed', String(on));
    if (!on) measureOut.textContent = '';
  }

  // --- start: view2d and the SVG viewBoxes (fallback placement), then build
  let v2 = null;
  const svgPaths = useGl ? [] : [...new Set(layers.flatMap((l) => [svgOf(l, 'base'), svgOf(l, 'head')]).filter(Boolean))];
  Promise.all([loadView2d(), ...svgPaths.map((p) => fetchText(p).then((t) => [p, parseViewBox(t)]).catch(() => [p, null]))]).then(([mod, ...pairs]) => {
    if (destroyed) return;
    v2 = mod;
    setCompareSliders({ swipe: v2.parseSlider(params.sw), opacity: v2.parseSlider(params.op) });
    svgBoxes = new Map(pairs.filter(([, v]) => v));
    renderLayerPanel();
    return buildStage();
  }).then(() => {
    if (destroyed || !stage) return;
    // the deep link's change / spot / region, once the panes are there (zooms are sized to them)
    const ci = Number.parseInt(params.c, 10);
    const at = parseAt(params.at);
    if (Number.isInteger(ci) && ci >= 0 && ci < changes.items.length) changes.select(ci);
    else if (at) { stage.zoomTo(at); stage.highlight(at); }
    const z = v2.parseRegion(params.z);
    if (z) stage.showRegion(z); // a link / reload with a zoom region: exactly that region
    updateLayerNote();
    pushRoute(); // the URL names the whole view from the start (layer, view, mode)
  }).catch((e) => {
    if (destroyed) return;
    showNote(e.message);
  });

  return {
    destroy() { destroyed = true; writeView.cancel(); stopFill(); boxes.stop(); cmp?.destroy(); stage?.destroy(); },
    // back / forward, or a link to this view: apply what the URL says, keep what it does not mention
    onParams(p, item) {
      writeView.cancel();
      if (setCompareSliders({ swipe: view2dNow()?.parseSlider(p.sw), opacity: view2dNow()?.parseSlider(p.op) }) && stage && mode !== 'diff') setMode(mode);
      if (p.view && p.view !== view && VIEWS.some(([v]) => v === p.view)) setView(p.view);
      const l = item ? layers.find((x) => x.id === item) : null;
      if (l && (l !== focus || !solo)) selectLayer(l, false);
      else if (!l && solo) showBoard(view); // an entry without a layer: the board view
      if (p.mode && p.mode !== mode && modes.some(([m]) => m === p.mode)) setMode(p.mode);
      const ci = Number.parseInt(p.c, 10);
      const reselect = Number.isInteger(ci) && ci !== changes.current && ci < changes.items.length;
      if (reselect) changes.select(ci);
      const at = parseAt(p.at);
      const z = view2dNow().parseRegion(p.z);
      if (!stage) return;
      if (z) { if (!view2dNow().sameRegion(z, stage.region())) stage.showRegion(z); } else if (at) { stage.zoomTo(at); stage.highlight(at); } else if (!reselect && stage.region()) stage.fit();
    },
    onKey(e) {
      if (e.key === 'n') { changes.next(); return true; }
      if (e.key === 'p') { changes.prev(); return true; }
      if (e.key === 'f') { stage?.fit(); return true; }
      if (e.key === 'b') { toggleBoxes(); return true; }
      if (e.key === 'r') { toggleMeasure(); return true; }
      if (e.key === 'v') { const i = VIEWS.findIndex(([v]) => v === view); showBoard(solo ? view : VIEWS[(i + 1) % VIEWS.length][0]); pushRoute(); return true; }
      if (e.key === 'm') {
        const i = modes.findIndex(([m]) => m === mode);
        setMode(modes[(i + 1) % modes.length][0]).then((shown) => { if (shown) pushRoute(); }); pushRoute(); return true;
      }
      if (e.key === '[' || e.key === ']') {
        // in the order of the layer list (top of the stack first), wrapping around
        selectLayer(stepItem([...layers].reverse(), focus, e.key === ']' ? 1 : -1, true));
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

