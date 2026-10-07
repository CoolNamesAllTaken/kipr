// Schematic diff: per-sheet list, side-by-side / ink diff / onion skin / swipe, change list that zooms.
// The stage, the sheet rasters and the ink diff are boarddd/view2d (stage2d.js); this is the app around it.
// Smart diff (smart.js, default): changes that only move things with the same connections (move_only)
// get a faint outline, are washed out of the ink diff and are listed in one collapsed group; raw: they
// are changes like any other.
import { el, clear, badge, arr, obj, bbox, fetchText, parseViewBox, assetUrl, debounce, parseAtParam, fillViewport, OFFLINE } from './util.js';
import { loadView2d, view2dNow, createKiprStage } from './stage2d.js';
import { createChangeList, describeChange } from './changes.js';
import { createModeBar, legend, boxesToggle, smartToggle } from './widgets.js';
import { boxesShown, toggleBoxes } from './boxes.js';
import { smartOn, toggleSmart, onSmart, sheetCounts, onlyMoved } from './smart.js';
import { showCompare, compareSliders, setCompareSliders, preferredMode, setPreferredMode } from './compare.js';
import { sameSize, stepItem, sheetNote } from './viewstate.js';

// Sheets and their ink diff are rendered at least this sharp (px/mm): the diff's changed areas are found
// at this resolution, and a zoom-in starts from a readable picture.
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

  const sheetNav = el('nav', { class: 'side-list', 'aria-label': 'Sheets' });
  const mainBox = el('div', { class: 'diff-main' });
  const changeBox = el('div', { class: 'diff-changes card' });
  container.append(el('div', { class: 'diff-grid' }, el('div', { class: 'side-col card' }, el('h3', {}, 'Sheets'), sheetNav), mainBox, changeBox));

  function renderNav() {
    clear(sheetNav);
    const smart = smartOn();
    for (const s of sheets) {
      const k = sheetCounts(s, smart);
      const n = k.changed + k.minor;
      const quiet = smart && s.status === 'modified' && !k.changed && k.moved > 0;
      sheetNav.append(el('button', {
        class: `side-item${s === sheet ? ' active' : ''}${s.status === 'unchanged' || quiet ? ' dim' : ''}`,
        'aria-current': s === sheet ? 'true' : null,
        onclick: () => switchSheet(s),
      },
      el('span', { class: 'side-name', title: s.id }, s.title || s.id),
      el('span', { class: 'side-meta' }, s.page ? el('span', { class: 'muted' }, `p.${s.page}`) : null,
        quiet ? el('span', { class: 'badge quiet-moved', title: `${k.moved} item${k.moved === 1 ? '' : 's'} moved, same connections` }, 'moved') : badge('status', s.status),
        n ? el('span', { class: 'count', title: k.moved ? `${n} change${n === 1 ? '' : 's'}, ${k.moved} moved` : null }, String(n)) : null)));
    }
  }
  const stopSmart = onSmart(() => renderNav());

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
    destroy() { stopSmart(); sheetView?.destroy(); },
    // back / forward, or a link to this view: another sheet opens with the URL's state
    onParams(params, item) {
      const slid = setCompareSliders({ swipe: view2dNow()?.parseSlider(params.sw), opacity: view2dNow()?.parseSlider(params.op) });
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
  let stage = null; // createKiprStage(): view2d stage in KiCad terms
  let cmp = null;
  let v2 = null;
  let vbs = { base: null, head: null };
  let srcs = { base: null, head: null }; // image sources: URLs, or SVG text from disk
  let content = null; // {base, head, diff} view2d content, made once per sheet

  const readout = el('span', { class: 'readout' });
  const zoomLbl = el('span', { class: 'readout zoom' });
  const modeBar = createModeBar(modes, mode, (m) => { setMode(m); writeRoute(); });
  const extra = el('div', { class: 'toolbar-extra' });
  const title = el('div', { class: 'view-title' },
    el('strong', {}, sheet.title || sheet.id), el('span', { class: 'muted' }, sheet.file || ''), badge('status', sheet.status));
  const boxes = boxesToggle((on) => stage?.setBoxes(on));
  const smartBtn = smartToggle(() => applyChanges());
  const toolbar = el('div', { class: 'toolbar' }, modeBar.el, extra, el('span', { class: 'spacer' }), readout, zoomLbl, smartBtn.el, boxes.el,
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
    const v = view2dNow();
    if (!v) return { mode }; // not loaded yet: the URL keeps the rest
    const z = v.formatRegion(stage?.region());
    const out = { mode, z, sw: v.formatSlider(sl.swipe, mode === 'swipe'), op: v.formatSlider(sl.opacity, mode === 'onion') };
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
  // are folded into one collapsed bucket and get no box on the sheet; so are moved items in smart mode
  // (a faint outline instead)
  const allSheetChanges = arr(sheet.changes).filter(obj);
  let contractChanges = [];
  let movedItems = [];
  let regions = null; // the diff's changed areas from its first render (they don't follow the zoom)
  let selected = null; // the moved item picked in the list
  const changes = createChangeList(changeBox, {
    title: 'Changes',
    empty: sheet.status === 'unchanged' ? 'Sheet unchanged.' : 'No itemised changes for this sheet.',
    onSelect: (i, it) => {
      writeRoute({ c: i >= 0 ? i : null });
      selected = movedItems.includes(it) ? it : null; // a moved item picked from its group: shown unwashed
      if (stage) stage.setQuiet(mode === 'diff' ? quietArea() : null);
      if (it.box && stage) { stage.zoomTo(it.box); stage.highlight(it.box, it.sides); }
    },
  });
  const short = (c) => ({ ...toItem(c), title: [typeof c.ref === 'string' ? c.ref : c.kind, c.kind === 'symbol' ? '' : c.what].filter(Boolean).join(' ') });
  const parts = (c) => {
    const p = arr(c.parts_mm).map(bbox).filter(Boolean);
    if (p.length) return p;
    const sides = [bbox(c.base_bbox_mm), bbox(c.head_bbox_mm)].filter(Boolean);
    return sides.length ? sides : [bbox(c.bbox_mm)].filter(Boolean);
  };

  /** The change list, marks and wash for the current smart / raw choice. */
  function applyChanges() {
    const smart = smartOn();
    const quiet = (c) => smart && c.move_only;
    contractChanges = allSheetChanges.filter((c) => !quiet(c) && !c.minor).map(toItem);
    const minorItems = allSheetChanges.filter((c) => c.minor).map(short);
    movedItems = allSheetChanges.filter(quiet).map((c) => ({ ...short(c), parts: parts(c) }));
    selected = null;
    const groups = [];
    if (movedItems.length) groups.push({ label: `${movedItems.length} moved, same connections`, badge: 'moved', items: movedItems });
    if (minorItems.length) groups.push({ label: `${minorItems.length} minor change${minorItems.length === 1 ? '' : 's'} (fields that don't name the part, hidden fields, …)`, badge: 'minor', items: minorItems });
    changes.set(contractChanges);
    changes.setMinor(groups);
    if (!stage) return;
    stage.highlight(null);
    if (!allSheetChanges.length && regions?.length) showRegionItems();
    else stage.setMarks([...movedItems.filter((c) => c.box).map((c) => ({ box: c.box, cls: 'moved' })), ...contractChanges.filter((c) => c.box).map((c) => ({ box: c.box }))]);
    stage.setQuiet(mode === 'diff' ? quietArea() : null);
    showDiffRegions();
  }
  /** Moved items' boxes (each rerouted wire, a symbol's old and new place), minus the changes over them: the ink diff's wash. */
  function quietArea() {
    const holes = contractChanges.map((c) => c.box).filter(Boolean).map((b) => grow(b, 1));
    return { boxes: movedItems.flatMap((c) => c.parts), holes: selected ? holes.concat(selected.parts) : holes };
  }
  applyChanges();

  function worldBox() {
    const vb = [vbs.base, vbs.head].filter(Boolean);
    const size = Array.isArray(sheet.size_mm) && sheet.size_mm.length === 2 && sheet.size_mm.every((n) => typeof n === 'number' && n > 0) ? sheet.size_mm : [297, 210];
    if (!vb.length) return { x: 0, y: 0, w: size[0], h: size[1] };
    const x0 = Math.min(...vb.map((v) => v.x)); const y0 = Math.min(...vb.map((v) => v.y));
    const x1 = Math.max(...vb.map((v) => v.x + v.w)); const y1 = Math.max(...vb.map((v) => v.y + v.h));
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  // The sheets are view2d images over their viewBoxes (re-rasterised sharper as the zoom settles); the
  // diff is their ink diff over the whole sheet frame. Made once per sheet, so the tiles are kept.
  function makeContent() {
    const f = stage.frame;
    const img = (side) => (srcs[side] ? v2.image(srcs[side], f.bounds(vbs[side] || worldBox())) : null);
    const base = img('base');
    const head = img('head');
    const diff = v2.inkdiff(base && { src: base.src, rect: base.rect }, head && { src: head.src, rect: head.rect }, f.bounds(worldBox()), { mode: 'ink', regionGapMm: 3 });
    return { base, head, diff };
  }

  function setMode(m) {
    mode = m;
    if (m !== 'single') setPreferredMode(m); // the mode on show is the one the next view opens in
    modeBar.select(m);
    updateNote();
    if (!stage) return;
    cmp?.destroy();
    clear(extra); clear(legendBox);
    stageWrap.className = `stage-wrap paper${m === 'side' ? ' split' : ''}`;
    if (m === 'diff') { legendBox.append(...legend()); showDiffRegions(); }
    stage.setQuiet(m === 'diff' ? quietArea() : null);
    cmp = showCompare(stage, m, m === 'diff' ? { diff: content.diff } : { base: content.base, head: content.head }, extra,
      { single: hasHead ? 'head' : 'base', onSlide: () => writeView() });
  }

  function onRender(e) {
    if (destroyed || e.content !== content?.diff || !e.info?.regions || regions) return;
    regions = e.info.regions.map((q) => stage.frame.box(q));
    if (!allSheetChanges.length && regions.length) showRegionItems();
    showDiffRegions();
  }
  // no itemised changes at all: the ink diff's regions are the list
  function showRegionItems() {
    changes.set(regions.map((q, i) => ({ title: `ink change ${i + 1}`, detail: `${q.w.toFixed(1)} × ${q.h.toFixed(1)} mm`, kind: 'visual', box: q })));
    stage.setMarks(regions.map((q) => ({ box: q })));
  }
  function showDiffRegions() {
    legendBox.querySelector('.diff-count')?.remove();
    if (!regions || mode !== 'diff') return;
    // smart: areas inside moved items (and over no change) are not counted
    const q = smartOn() ? quietArea() : { boxes: [], holes: [] };
    const n = regions.filter((r) => !onlyMoved(r, q)).length;
    const hidden = regions.length - n;
    legendBox.append(el('span', { class: 'muted diff-count', title: hidden ? `${hidden} more where items only moved (smart diff)` : null }, `${n} changed area${n === 1 ? '' : 's'}${hidden ? ` (+${hidden} moved)` : ''}`));
  }

  // view2d and the sheets' viewBoxes first (they define the world), then build the stage
  const sideSrc = (side) => (assetUrl(sheet[side]) ? (OFFLINE ? fetchText(sheet[side]) : Promise.resolve(assetUrl(sheet[side]))) : Promise.resolve(null));
  Promise.all([loadView2d(), ...['base', 'head'].map((side) => (assetUrl(sheet[side]) ? fetchText(sheet[side]).then(parseViewBox).catch(() => null) : null)),
    sideSrc('base').catch(() => null), sideSrc('head').catch(() => null)])
    .then(([mod, b, h, sb, sh]) => {
      if (destroyed) return;
      v2 = mod;
      setCompareSliders({ swipe: v2.parseSlider(params.sw), opacity: v2.parseSlider(params.op) });
      vbs = { base: b, head: h };
      srcs = { base: sb, head: sh };
      clear(stageWrap);
      stage = createKiprStage(v2, stageWrap, { box: worldBox(), readout, zoomLabel: zoomLbl, boxes: boxesShown(), minRender: SHEET_R });
      stage.onTransform(() => writeView());
      stage.stage.on('render', onRender);
      content = makeContent();
      applyChanges();
      setMode(mode); // lays out the panes and fits
      // zoom: the URL's region, else (another sheet picked) the previous sheet's region when the paper is
      // the same size; sheets of other sizes are fitted
      const z = v2.parseRegion(params.z) || (params.keep?.region && sameSize(params.keep.box, worldBox()) ? params.keep.region : null);
      if (z) stage.showRegion(z);
      const ci = Number.parseInt(params.c, 10);
      const at = parseAtParam(params.at);
      if (Number.isInteger(ci) && ci >= 0 && ci < changes.items.length) changes.select(ci);
      else if (at) showAt(at);
      if (z && (Number.isInteger(ci) || at)) stage.showRegion(z); // the region was saved after that zoom
      writeRoute();
    }).catch((e) => { if (!destroyed) clear(stageWrap).append(el('div', { class: 'empty' }, e.message)); });

  // "at=x,y[,w,h]" (links from the ERC/DRC tab, e.g. a grid finding): zoom there and outline it
  function showAt(box) { stage.zoomTo(box); stage.highlight(box); }

  return {
    get mode() { return mode; },
    get box() { return stage ? worldBox() : null; },
    region() { return stage?.region() || null; },
    destroy() { destroyed = true; writeView.cancel(); stopFill(); boxes.stop(); smartBtn.stop(); cmp?.destroy(); stage?.destroy(); },
    onParams(p, slid = false) {
      writeView.cancel();
      if (p.mode && p.mode !== mode && modes.some(([m]) => m === p.mode)) setMode(p.mode);
      else if (slid && stage && mode !== 'diff') setMode(mode); // new slider values from the URL
      const ci = Number.parseInt(p.c, 10);
      const reselect = Number.isInteger(ci) && ci !== changes.current && ci < changes.items.length;
      if (reselect) changes.select(ci);
      const at = parseAtParam(p.at);
      const z = view2dNow().parseRegion(p.z);
      if (!stage) return;
      if (z) { if (!view2dNow().sameRegion(z, stage.region())) stage.showRegion(z); } else if (at) showAt(at); else if (!reselect && stage.region()) stage.fit();
    },
    onKey(e) {
      if (e.key === 'n') { changes.next(); return true; }
      if (e.key === 'p') { changes.prev(); return true; }
      if (e.key === 'f') { stage?.fit(); return true; }
      if (e.key === 'b') { toggleBoxes(); return true; }
      if (e.key === 's') { toggleSmart(); return true; }
      if (e.key === 'm') {
        const i = modes.findIndex(([m]) => m === mode);
        setMode(modes[(i + 1) % modes.length][0]);
        writeRoute();
        return true;
      }
      if (e.key === 'Escape') {
        stage?.highlight(null);
        if (selected) { selected = null; stage?.setQuiet(mode === 'diff' ? quietArea() : null); }
        return true;
      }
      return false;
    },
  };
}


const grow = (b, d) => ({ x: b.x - d, y: b.y - d, w: b.w + 2 * d, h: b.h + 2 * d });
