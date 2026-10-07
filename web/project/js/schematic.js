// Schematic diff: per-sheet list, side-by-side / ink diff / onion skin / swipe, change list that zooms.
import { el, clear, badge, arr, obj, bbox, fetchText, parseViewBox, assetUrl, debounce, parseAtParam, fillViewport } from './util.js';
import { createStage, PX_PER_MM } from './panzoom.js';
import { createChangeList, describeChange } from './changes.js';
import { rasterize, rasterScale, diffRasters, bitmapOf, displayScale } from './raster.js';
import { createModeBar, legend, boxesToggle } from './widgets.js';
import { boxesShown, toggleBoxes } from './boxes.js';
import { comparePanes, compareSliders, setCompareSliders, preferredMode, setPreferredMode } from './compare.js';
import { formatZoom, parseZoom, sameZoom, sameSize, sliderParam, parseSlider, stepItem, sheetNote } from './viewstate.js';

// Base raster resolution (px/mm): shared by the first display bitmaps and the ink diff, so a sheet is
// drawn once on open. Real KiCad sheets are megabytes of SVG and drawing one is the expensive part.
const SHEET_R = 6;
const MODES = [['side', 'Side by side'], ['diff', 'Diff'], ['onion', 'Onion skin'], ['swipe', 'Swipe']];

export function sheetList(project) {
  return arr(obj(project.schematic)?.sheets).filter((s) => obj(s) && typeof s.id === 'string');
}

/** Initial sheet: the one in the URL, else the first changed one, else the first. */
export function pickSheet(sheets, id) {
  return sheets.find((s) => s.id === id) || sheets.find((s) => s.status && s.status !== 'unchanged') || sheets[0] || null;
}

export function createSchematicView(project, container, ctx) {
  const sheets = sheetList(project);
  if (!sheets.length) {
    container.append(el('div', { class: 'empty' }, project.schematic ? 'No sheets in this schematic.' : 'No schematic in this project (or it was not exported).'));
    return { destroy() {} };
  }
  let sheet = pickSheet(sheets, ctx.route.item);
  let sheetView = null;
  // the compare modes are the project's: a sheet only in head or base of a modified project keeps the
  // chosen mode (its missing side is empty), so stepping through sheets never switches mode
  const bothSides = project.status !== 'added' && project.status !== 'removed';
  setCompareSliders({ swipe: parseSlider(ctx.route.params.sw), opacity: parseSlider(ctx.route.params.op) });

  const sheetNav = el('nav', { class: 'side-list', 'aria-label': 'Sheets' });
  const mainBox = el('div', { class: 'diff-main' });
  const changeBox = el('div', { class: 'diff-changes card' });
  container.append(el('div', { class: 'diff-grid' }, el('div', { class: 'side-col card' }, el('h3', {}, 'Sheets'), sheetNav), mainBox, changeBox));

  function renderNav() {
    clear(sheetNav);
    for (const s of sheets) {
      const n = arr(s.changes).length;
      sheetNav.append(el('button', {
        class: `side-item${s === sheet ? ' active' : ''}${s.status === 'unchanged' ? ' dim' : ''}`,
        'aria-current': s === sheet ? 'true' : null,
        onclick: () => switchSheet(s),
      },
      el('span', { class: 'side-name', title: s.id }, s.title || s.id),
      el('span', { class: 'side-meta' }, s.page ? el('span', { class: 'muted' }, `p.${s.page}`) : null, badge('status', s.status), n ? el('span', { class: 'count' }, String(n)) : null)));
    }
  }

  function openSheet(s, params) {
    sheet = s;
    renderNav();
    sheetView?.destroy();
    clear(mainBox); clear(changeBox);
    sheetView = createSheetView(project, s, mainBox, changeBox, ctx, params, bothSides);
  }

  /**
   * The user picked sheet s (click, [ / ]): only the sheet changes. The compare mode and sliders stay;
   * the zoom region stays when the new sheet has the same size (else it is fitted). New history entry.
   */
  function switchSheet(s) {
    if (!s || s === sheet) return;
    const keep = sheetView ? { region: sheetView.region(), box: sheetView.box } : null;
    const params = { ...ctx.route.params, mode: sheetView?.mode || ctx.route.params.mode, c: null, at: null, z: null };
    ctx.setRoute({ item: s.id, params }, false);
    openSheet(s, { ...params, keep });
  }

  renderNav();
  sheetView = createSheetView(project, sheet, mainBox, changeBox, ctx, ctx.route.params, bothSides);

  return {
    destroy() { sheetView?.destroy(); },
    // back / forward, or a link to this view: another sheet opens with the URL's state
    onParams(params, item) {
      const slid = setCompareSliders({ swipe: parseSlider(params.sw), opacity: parseSlider(params.op) });
      const s = item ? sheets.find((x) => x.id === item) : null;
      if (s && s !== sheet) openSheet(s, { ...params, mode: params.mode || sheetView?.mode });
      else sheetView?.onParams(params, slid);
    },
    onKey(e) {
      if (e.key === ']' || e.key === '[') { switchSheet(stepItem(sheets, sheet, e.key === ']' ? 1 : -1)); return true; }
      return sheetView?.onKey(e) || false;
    },
  };
}

function createSheetView(project, sheet, mainBox, changeBox, ctx, params, bothSides = true) {
  const hasBase = !!assetUrl(sheet.base);
  const hasHead = !!assetUrl(sheet.head);
  const modes = bothSides || (hasBase && hasHead) ? MODES : [['single', hasHead ? 'Head (added)' : 'Base (removed)'], ['diff', 'Diff']];
  if (!hasBase && !hasHead) {
    mainBox.append(el('div', { class: 'empty' }, 'No SVG export for this sheet.'));
  }
  let mode = modes.some(([m]) => m === params.mode) ? params.mode : modes.some(([m]) => m === preferredMode()) ? preferredMode() : modes[0][0];
  let destroyed = false;
  let stage = null;
  let vbs = { base: null, head: null };
  let diff = null; // {canvas, counts, regions} once computed
  let diffPromise = null;
  const cleanups = [];

  const readout = el('span', { class: 'readout' });
  const zoomLbl = el('span', { class: 'readout zoom' });
  const modeBar = createModeBar(modes, mode, (m) => { setMode(m); writeRoute(); });
  const extra = el('div', { class: 'toolbar-extra' });
  const title = el('div', { class: 'view-title' },
    el('strong', {}, sheet.title || sheet.id), el('span', { class: 'muted' }, sheet.file || ''), badge('status', sheet.status));
  const boxes = boxesToggle((on) => stage?.setBoxes(on));
  const toolbar = el('div', { class: 'toolbar' }, modeBar.el, extra, el('span', { class: 'spacer' }), readout, zoomLbl, boxes.el,
    el('button', { class: 'btn', title: 'Fit (f, or double-click)', onclick: () => stage?.fit() }, 'Fit'));
  const stageWrap = el('div', { class: 'stage-wrap paper' }, el('div', { class: 'loading' }, 'Loading sheet…'));
  const legendBox = el('div', { class: 'legend' });
  const noteEl = el('div', { class: 'notice small sheet-note', role: 'status' });
  noteEl.hidden = true;
  const noteElBox = el('div', { class: 'view-note' }, noteEl);
  mainBox.append(title, toolbar, noteElBox, stageWrap, legendBox);
  const stopFill = fillViewport(stageWrap, { until: mainBox, watch: [title, toolbar, legendBox] });

  // the URL holds the sheet's view: compare mode, change, zoom region, sliders (zoom / sliders once settled)
  function viewParams() {
    const sl = compareSliders();
    const z = formatZoom(stage?.region());
    const out = { mode, z, sw: sliderParam(sl.swipe, mode === 'swipe'), op: sliderParam(sl.opacity, mode === 'onion') };
    if (z) out.at = null;
    return out;
  }
  function writeRoute(extra = {}) { writeView.cancel(); ctx.setRoute({ item: sheet.id, params: { ...viewParams(), ...extra } }, true); }
  const writeView = debounce(() => { if (!destroyed && stage) ctx.setRoute({ params: viewParams() }, true); }, 200);
  function updateNote() {
    const t = sheetNote(sheet, mode, { hasBase, hasHead, bothSides });
    noteEl.hidden = !t;
    noteEl.textContent = t || '';
  }

  // --- change list: contract changes; if there are none, the pixel diff's regions
  const toItem = (c) => ({ ...describeChange(c), kind: c.kind, status: null, box: bbox(c.bbox_mm), sides: bbox(c.base_bbox_mm) || bbox(c.head_bbox_mm) ? { base: bbox(c.base_bbox_mm), head: bbox(c.head_bbox_mm) } : null });
  // minor changes (fields that don't name the part, sim flags, hidden-field edits; kipr.project.classify)
  // are folded into one collapsed bucket and get no box on the sheet
  const allSheetChanges = arr(sheet.changes).filter(obj);
  const contractChanges = allSheetChanges.filter((c) => !c.minor).map(toItem);
  const minorItems = allSheetChanges.filter((c) => c.minor).map((c) => ({ ...toItem(c), title: typeof c.ref === 'string' ? c.ref : c.kind }));
  const changes = createChangeList(changeBox, {
    title: 'Changes',
    empty: sheet.status === 'unchanged' ? 'Sheet unchanged.' : 'No itemised changes for this sheet.',
    onSelect: (i, it) => {
      writeRoute({ c: i >= 0 ? i : null });
      if (it.box && stage) { stage.zoomTo(it.box); stage.highlight(it.box, it.sides); }
    },
  });
  changes.set(contractChanges);
  if (minorItems.length) {
    changes.setMinor([{ label: `${minorItems.length} minor change${minorItems.length === 1 ? '' : 's'} (fields that don't name the part, hidden fields, …)`, badge: 'minor', items: minorItems }]);
  }

  function worldBox() {
    const vb = [vbs.base, vbs.head].filter(Boolean);
    const size = Array.isArray(sheet.size_mm) && sheet.size_mm.length === 2 && sheet.size_mm.every((n) => typeof n === 'number' && n > 0) ? sheet.size_mm : [297, 210];
    if (!vb.length) return { x: 0, y: 0, w: size[0], h: size[1] };
    const x0 = Math.min(...vb.map((v) => v.x)); const y0 = Math.min(...vb.map((v) => v.y));
    const x1 = Math.max(...vb.map((v) => v.x + v.w)); const y1 = Math.max(...vb.map((v) => v.y + v.h));
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  // Sheets are shown as bitmaps rasterised at the current zoom (re-sharpened when it settles). Past the
  // pixel budget the vector <img> takes over; by then only a small part of the sheet is on screen.
  let dispR = 0;
  function img(side) {
    const ok = side === 'base' ? hasBase : hasHead;
    if (!ok) return null;
    const src = side === 'base' ? sheet.base : sheet.head;
    const at = vbs[side] || worldBox();
    const holder = stage.place(el('div', { class: 'layer-holder', dataset: { side } }), at);
    if (dispR >= rasterScale(at.w, at.h, 64)) {
      holder.append(el('img', { src, alt: `${side} ${sheet.title || sheet.id}`, draggable: 'false', class: 'sheet-img' }));
      return holder;
    }
    holder.append(el('div', { class: 'loading small' }, 'Rendering…'));
    bitmapOf(src, at, dispR).then((c) => {
      if (destroyed) return;
      const copy = c.cloneNode(false);
      copy.getContext('2d').drawImage(c, 0, 0);
      copy.className = 'layer-canvas';
      clear(holder).append(copy);
    }).catch((e) => { clear(holder).append(el('div', { class: 'missing-msg' }, `Could not load: ${e.message}`)); });
    return holder;
  }
  const wantR = () => displayScale(stage ? stage.view.s * PX_PER_MM : 2, worldBox().w, worldBox().h, window.devicePixelRatio || 1, SHEET_R);
  const resharpen = debounce(() => {
    if (destroyed || !stage || mode === 'diff') return;
    const r = wantR();
    if (r > dispR * 1.3 || r < dispR / 2.5) { dispR = r; setMode(mode); }
  }, 250);

  let modeToken = 0;
  function setMode(m) {
    const token = ++modeToken;
    mode = m;
    if (m !== 'single') setPreferredMode(m); // the mode on show is the one the next view opens in
    modeBar.select(m);
    updateNote();
    for (const c of cleanups.splice(0)) c();
    clear(extra); clear(legendBox);
    if (!stage) return;
    clear(stageWrap);
    stageWrap.className = `stage-wrap paper${m === 'side' ? ' split' : ''}`;
    let panes;
    if (m === 'diff') {
      const holder = el('div', { class: 'diff-holder' }, el('div', { class: 'loading' }, 'Computing diff…'));
      stage.place(holder, worldBox());
      panes = [stage.pane('diff', holder)];
      legendBox.append(...legend());
      computeDiff().then((d) => {
        if (destroyed || token !== modeToken) return;
        clear(holder).append(d.canvas);
        d.canvas.className = 'diff-canvas';
        legendBox.append(el('span', { class: 'muted' }, `${d.regions.length} changed area${d.regions.length === 1 ? '' : 's'}`));
      }).catch((e) => { clear(holder).append(el('div', { class: 'missing-msg' }, `Diff failed: ${e.message}`)); });
    } else {
      const c = comparePanes(stage, m, img, extra, { single: hasHead ? 'head' : 'base', onSlide: () => writeView() });
      cleanups.push(c.cleanup);
      panes = c.panes;
    }
    stageWrap.append(...panes);
    stage.setPanes(panes);
  }

  function computeDiff() {
    if (!diffPromise) {
      const box = worldBox();
      const r = Math.min(SHEET_R, rasterScale(box.w, box.h, SHEET_R));
      const bm = (side) => (side === 'base' ? hasBase : hasHead) ? bitmapOf(sheet[side], vbs[side] || box, r) : null;
      diffPromise = Promise.all([bm('base'), bm('head')]).then(([bi, hi]) => {
        const base = bi ? rasterize(bi, vbs.base || box, box, r) : null;
        const head = hi ? rasterize(hi, vbs.head || box, box, r) : null;
        diff = diffRasters(base, head, box, r, { mode: 'ink', regionGapMm: 3 });
        if (!contractChanges.length && diff.regions.length) {
          changes.set(diff.regions.map((q, i) => ({ title: `ink change ${i + 1}`, detail: `${q.w.toFixed(1)} × ${q.h.toFixed(1)} mm`, kind: 'visual', box: q })));
          stage.setMarks(diff.regions.map((q) => ({ box: q })));
        }
        return diff;
      });
    }
    return diffPromise;
  }

  // viewBoxes first (they define the world), then build the stage
  Promise.all(['base', 'head'].map((side) => (assetUrl(sheet[side]) ? fetchText(sheet[side]).then(parseViewBox).catch(() => null) : null)))
    .then(([b, h]) => {
      if (destroyed) return;
      vbs = { base: b, head: h };
      stage = createStage({ box: worldBox(), readout, zoomLabel: zoomLbl, boxes: boxesShown() });
      stage.observe(stageWrap);
      stage.onTransform(resharpen);
      stage.onTransform(() => writeView());
      stage.setMarks(contractChanges.filter((c) => c.box).map((c) => ({ box: c.box })));
      setMode(mode); // lays out the panes and fits
      // zoom: the URL's region, else (another sheet picked) the previous sheet's region when the paper is
      // the same size; sheets of other sizes are fitted
      const z = parseZoom(params.z) || (params.keep?.region && sameSize(params.keep.box, worldBox()) ? params.keep.region : null);
      if (z) stage.showRegion(z);
      dispR = wantR();
      setMode(mode);
      const ci = Number.parseInt(params.c, 10);
      const at = parseAtParam(params.at);
      if (Number.isInteger(ci) && ci >= 0 && ci < changes.items.length) changes.select(ci);
      else if (at) showAt(at);
      if (z && (Number.isInteger(ci) || at)) stage.showRegion(z); // the region was saved after that zoom
      writeRoute();
    });

  // "at=x,y[,w,h]" (links from the ERC/DRC tab, e.g. a grid finding): zoom there and outline it
  function showAt(box) { stage.zoomTo(box); stage.highlight(box); }

  return {
    get mode() { return mode; },
    get box() { return stage ? worldBox() : null; },
    region() { return stage?.region() || null; },
    destroy() { destroyed = true; resharpen.cancel(); writeView.cancel(); stopFill(); boxes.stop(); for (const c of cleanups) c(); stage?.destroy(); },
    onParams(p, slid = false) {
      writeView.cancel();
      if (p.mode && p.mode !== mode && modes.some(([m]) => m === p.mode)) setMode(p.mode);
      else if (slid && stage && mode !== 'diff') setMode(mode);
      const ci = Number.parseInt(p.c, 10);
      const reselect = Number.isInteger(ci) && ci !== changes.current && ci < changes.items.length;
      if (reselect) changes.select(ci);
      const at = parseAtParam(p.at);
      const z = parseZoom(p.z);
      if (!stage) return;
      if (z) { if (!sameZoom(z, stage.region())) stage.showRegion(z); } else if (at) showAt(at); else if (!reselect && stage.region()) stage.fit();
    },
    onKey(e) {
      if (e.key === 'n') { changes.next(); return true; }
      if (e.key === 'p') { changes.prev(); return true; }
      if (e.key === 'f') { stage?.fit(); return true; }
      if (e.key === 'b') { toggleBoxes(); return true; }
      if (e.key === 'm') {
        const i = modes.findIndex(([m]) => m === mode);
        setMode(modes[(i + 1) % modes.length][0]);
        writeRoute();
        return true;
      }
      if (e.key === 'Escape') { stage?.highlight(null); return true; }
      return false;
    },
  };
}
