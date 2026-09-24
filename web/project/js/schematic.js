// Schematic diff: per-sheet list, side-by-side / ink diff / onion skin / swipe, change list that zooms.
import { el, clear, badge, arr, obj, bbox, fetchText, parseViewBox, assetUrl, debounce } from './util.js';
import { createStage, PX_PER_MM } from './panzoom.js';
import { createChangeList, describeChange } from './changes.js';
import { rasterize, rasterScale, diffRasters, bitmapOf, displayScale } from './raster.js';
import { createModeBar, legend } from './widgets.js';
import { comparePanes } from './compare.js';

// Base raster resolution (px/mm): shared by the first display bitmaps and the ink diff, so a sheet is
// drawn once on open. Real KiCad sheets are megabytes of SVG and drawing one is the expensive part.
const SHEET_R = 6;
const MODES = [['side', 'Side by side'], ['diff', 'Diff'], ['onion', 'Onion skin'], ['swipe', 'Swipe']];
let preferredMode = 'side';

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
        onclick: () => openSheet(s, {}),
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
    ctx.setRoute({ item: s.id, params: { mode: params.mode || null, c: params.c ?? null } }, true);
    sheetView = createSheetView(project, s, mainBox, changeBox, ctx, params);
  }

  renderNav();
  sheetView = createSheetView(project, sheet, mainBox, changeBox, ctx, ctx.route.params);

  const step = (d) => {
    const i = sheets.indexOf(sheet);
    const n = sheets[Math.min(Math.max(i + d, 0), sheets.length - 1)];
    if (n !== sheet) openSheet(n, { mode: sheetView?.mode });
  };

  return {
    destroy() { sheetView?.destroy(); },
    onParams(params) { sheetView?.onParams(params); },
    onKey(e) {
      if (e.key === ']') { step(1); return true; }
      if (e.key === '[') { step(-1); return true; }
      return sheetView?.onKey(e) || false;
    },
  };
}

function createSheetView(project, sheet, mainBox, changeBox, ctx, params) {
  const hasBase = !!assetUrl(sheet.base);
  const hasHead = !!assetUrl(sheet.head);
  const modes = hasBase && hasHead ? MODES : [['single', hasHead ? 'Head (added)' : 'Base (removed)'], ['diff', 'Diff']];
  if (!hasBase && !hasHead) {
    mainBox.append(el('div', { class: 'empty' }, 'No SVG export for this sheet.'));
  }
  let mode = modes.some(([m]) => m === params.mode) ? params.mode : modes.some(([m]) => m === preferredMode) ? preferredMode : modes[0][0];
  let destroyed = false;
  let stage = null;
  let vbs = { base: null, head: null };
  let diff = null; // {canvas, counts, regions} once computed
  let diffPromise = null;
  const cleanups = [];

  const readout = el('span', { class: 'readout' });
  const zoomLbl = el('span', { class: 'readout zoom' });
  const modeBar = createModeBar(modes, mode, (m) => { preferredMode = m; setMode(m); });
  const extra = el('div', { class: 'toolbar-extra' });
  const title = el('div', { class: 'view-title' },
    el('strong', {}, sheet.title || sheet.id), el('span', { class: 'muted' }, sheet.file || ''), badge('status', sheet.status));
  const toolbar = el('div', { class: 'toolbar' }, modeBar.el, extra, el('span', { class: 'spacer' }), readout, zoomLbl,
    el('button', { class: 'btn', title: 'Fit (f, or double-click)', onclick: () => stage?.fit() }, 'Fit'));
  const stageWrap = el('div', { class: 'stage-wrap paper' }, el('div', { class: 'loading' }, 'Loading sheet…'));
  const legendBox = el('div', { class: 'legend' });
  mainBox.append(title, toolbar, stageWrap, legendBox);

  // --- change list: contract changes; if there are none, the pixel diff's regions
  const contractChanges = arr(sheet.changes).filter(obj).map((c) => ({ ...describeChange(c), kind: c.kind, status: null, box: bbox(c.bbox_mm), sides: bbox(c.base_bbox_mm) || bbox(c.head_bbox_mm) ? { base: bbox(c.base_bbox_mm), head: bbox(c.head_bbox_mm) } : null }));
  const changes = createChangeList(changeBox, {
    title: 'Changes',
    empty: sheet.status === 'unchanged' ? 'Sheet unchanged.' : 'No itemised changes for this sheet.',
    onSelect: (i, it) => {
      ctx.setRoute({ params: { mode, c: i } }, true);
      if (it.box && stage) { stage.zoomTo(it.box); stage.highlight(it.box, it.sides); }
    },
  });
  changes.set(contractChanges);

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
    modeBar.select(m);
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
      const c = comparePanes(stage, m, img, extra, { single: hasHead ? 'head' : 'base' });
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
      stage = createStage({ box: worldBox(), readout, zoomLabel: zoomLbl });
      stage.observe(stageWrap);
      stage.onTransform(resharpen);
      stage.setMarks(contractChanges.filter((c) => c.box).map((c) => ({ box: c.box })));
      setMode(mode); // lays out the panes and fits
      dispR = wantR();
      setMode(mode);
      const ci = Number.parseInt(params.c, 10);
      if (Number.isInteger(ci) && ci >= 0 && ci < changes.items.length) changes.select(ci);
    });

  return {
    get mode() { return mode; },
    destroy() { destroyed = true; resharpen.cancel(); for (const c of cleanups) c(); stage?.destroy(); },
    onParams(p) {
      if (p.mode && p.mode !== mode && modes.some(([m]) => m === p.mode)) setMode(p.mode);
      const ci = Number.parseInt(p.c, 10);
      if (Number.isInteger(ci) && ci !== changes.current && ci < changes.items.length) changes.select(ci);
    },
    onKey(e) {
      if (e.key === 'n') { changes.next(); return true; }
      if (e.key === 'p') { changes.prev(); return true; }
      if (e.key === 'f') { stage?.fit(); return true; }
      if (e.key === 'm') {
        const i = modes.findIndex(([m]) => m === mode);
        preferredMode = modes[(i + 1) % modes.length][0];
        setMode(preferredMode);
        ctx.setRoute({ params: { mode: preferredMode, c: changes.current >= 0 ? changes.current : null } }, true);
        return true;
      }
      if (e.key === 'Escape') { stage?.highlight(null); return true; }
      return false;
    },
  };
}
