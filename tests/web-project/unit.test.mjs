// Unit tests for the pure parts of the project viewer.   node --test tests/web-project/unit.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHash, formatHash, TABS } from '../../web/project/js/route.js';
import {
  sortLayers, copperIndex, isDocLayer, docExtent, frameBox, faceLayers, kicadBoxToGerber, gerberPointToKicad, boardRect, gerberOrigin, boardStyle, union, grow, layerColor,
} from '../../web/project/js/board.js';
import { kicadFrame, measureText } from '../../web/project/js/stage2d.js';
// the URL's z= / sw= / op= strings are boarddd/view2d's (vendored): pinned here so old links keep working
import {
  formatRegion as formatZoom, parseRegion as parseZoom, sameRegion as sameZoom, formatSlider as sliderParam, parseSlider,
} from '../../web/vendor/boarddd/src/view2d/viewstate.js';
import {
  mergeParams, sameSize, stepItem, rememberRoute, routeFor, layerNote, sheetNote,
} from '../../web/project/js/viewstate.js';
import { safeUrl, assetUrl, commitUrl, blobUrl, bbox, parseViewBox, cellText, parseAtParam } from '../../web/project/js/util.js';
import { matchesQuery, statusCounts, sheetSpotHash, gridGroups, impedanceSideTitle } from '../../web/project/js/tables.js';
import { faceOf, pickDiffLayer, parseAt } from '../../web/project/js/layout.js';
import { pickSheet } from '../../web/project/js/schematic.js';
import { describeChange } from '../../web/project/js/changes.js';
import { summaryChips, tabCount, projectsOf, tabsOf, kindTitle } from '../../web/project/js/app.js';

// --- route ---------------------------------------------------------------------------------------

test('route: overview and unknown', () => {
  assert.deepEqual(parseHash(''), { slug: null, tab: null, item: null, params: {} });
  assert.deepEqual(parseHash('#/'), { slug: null, tab: null, item: null, params: {} });
  assert.equal(parseHash('#/whatever').slug, null);
});

test('route: project, tab, item with slashes, params', () => {
  const r = parseHash('#/p/demo/schematic/root%2Fpower?mode=diff&c=2');
  assert.deepEqual(r, { slug: 'demo', tab: 'schematic', item: 'root/power', params: { mode: 'diff', c: '2' } });
  assert.equal(parseHash('#/p/demo').tab, null);
  assert.equal(parseHash('#/p/demo/bogus/x').tab, null);
  assert.equal(parseHash('#/p/demo/bogus/x').item, null);
});

test('route: round trip', () => {
  for (const r of [
    { slug: 'a_b-1', tab: 'layout', item: 'F.Cu', params: { view: 'bottom', mode: 'swipe', c: '0' } },
    { slug: 'x', tab: 'schematic', item: 'root/sub sheet/ü', params: {} },
    { slug: 'x', tab: 'bom', item: null, params: { q: 'a&b=c #d' } },
  ]) assert.deepEqual(parseHash(formatHash(r)), r);
  assert.equal(formatHash({ slug: 'x', tab: 'bom', params: { q: '', st: null } }), '#/p/x/bom');
  assert.equal(formatHash({}), '#/');
  assert.equal(TABS.length, 6);
});

test('route: hostile input never throws and drops odd param names', () => {
  const r = parseHash('#/p/%E0%A4%A/schematic/%?<script>=1&ok=%&x y=2');
  assert.equal(r.slug, '%E0%A4%A');
  assert.equal(r.params.ok, '%');
  assert.ok(!('<script>' in r.params));
  assert.ok(!('x y' in r.params));
});

// --- board ---------------------------------------------------------------------------------------

const L = (id, kind, side, status = 'unchanged') => ({ id, kind, side, status });
const LAYERS = [L('F.SilkS', 'silk', 'top'), L('Edge.Cuts', 'outline', 'none'), L('B.Cu', 'copper', 'bottom', 'modified'),
  L('PTH', 'drill', 'none'), L('F.Cu', 'copper', 'top', 'modified'), L('In1.Cu', 'copper', 'inner'), L('F.Mask', 'mask', 'top'), L('B.SilkS', 'silk', 'bottom')];

test('board: layer stack order back -> inner -> front -> outline -> drills', () => {
  assert.deepEqual(sortLayers(LAYERS).map((l) => l.id), ['B.SilkS', 'B.Cu', 'In1.Cu', 'F.Cu', 'F.Mask', 'F.SilkS', 'Edge.Cuts', 'PTH']);
});

// a board's layers as the backend lists them (header order), for an n-layer stack
function stack(n) {
  const inner = Array.from({ length: n - 2 }, (_, i) => L(`In${i + 1}.Cu`, 'copper', 'inner'));
  return [L('F.Cu', 'copper', 'top'), ...inner, L('B.Cu', 'copper', 'bottom'), L('F.Mask', 'mask', 'top'), L('B.Mask', 'mask', 'bottom'),
    L('F.SilkS', 'silk', 'top'), L('B.SilkS', 'silk', 'bottom'), L('Edge.Cuts', 'outline', 'none')];
}
const copperOf = (ls) => ls.filter((l) => l.kind === 'copper').map((l) => l.id);
const shuffled = (ls) => [...ls].reverse().sort((a, b) => (a.id.length % 3) - (b.id.length % 3));

test('board: inner copper in physical order (4, 6, 10 and 32 layers)', () => {
  for (const n of [4, 6, 10, 32]) {
    const top = ['F.Cu', ...Array.from({ length: n - 2 }, (_, i) => `In${i + 1}.Cu`), 'B.Cu'];
    const sorted = sortLayers(shuffled(stack(n)));
    // paint order: B.Cu first, then In<n-2> ... In1, F.Cu last
    assert.deepEqual(copperOf(sorted), [...top].reverse(), `paint order, ${n} layers`);
    // the layer list shows the stack top -> bottom
    const list = [...sorted].reverse().map((l) => l.id);
    assert.deepEqual(list.filter((id) => id.endsWith('.Cu')), top, `list order, ${n} layers`);
    assert.deepEqual(list.slice(0, 3), ['Edge.Cuts', 'F.SilkS', 'F.Mask'], `list starts with the front, ${n} layers`);
    assert.deepEqual(list.slice(-2), ['B.Mask', 'B.SilkS'], `list ends with the back, ${n} layers`);
  }
  // numeric, not lexical: In10 below In9, In2 above In10
  const ten = [...sortLayers(shuffled(stack(12)))].reverse().map((l) => l.id);
  assert.ok(ten.indexOf('In9.Cu') < ten.indexOf('In10.Cu') && ten.indexOf('In2.Cu') < ten.indexOf('In10.Cu'));
  assert.deepEqual(['F.Cu', 'In1.Cu', 'In10.Cu', 'B.Cu', 'F.SilkS', 'In1.User'].map(copperIndex), [0, 1, 10, 1000, null, null]);
  const users = ['User.10', 'User.2', 'User.1', 'Dwgs.User', 'Cmts.User'].map((id) => L(id, 'user', 'none'));
  assert.deepEqual([...sortLayers(users)].reverse().map((l) => l.id), ['Cmts.User', 'Dwgs.User', 'User.1', 'User.2', 'User.10']);
});

test('board: documentation layers are framed by their own extents', () => {
  const dwgs = { id: 'Dwgs.User', kind: 'user', side: 'none', extent_mm: [210, 80, 30, 14] };
  const fab = { id: 'F.Fab', kind: 'fab', side: 'top', extent_mm: [90, 60, 5, 5] };
  const silk = { id: 'F.SilkS', kind: 'silk', side: 'top', extent_mm: [0, 0, 500, 500] };
  assert.deepEqual([dwgs, fab, silk].map(isDocLayer), [true, true, false]);
  assert.equal(docExtent(silk), null); // board layers never grow the frame
  assert.equal(docExtent({ id: 'Cmts.User', kind: 'user' }), null);
  const board = { x: 98, y: 68, w: 64, h: 44 };
  assert.deepEqual(frameBox(board, []), board);
  assert.deepEqual(frameBox(board, [null]), board);
  assert.deepEqual(frameBox(board, [docExtent(dwgs)]), { x: 98, y: 68, w: 144, h: 44 });
  assert.deepEqual(frameBox(board, [docExtent(dwgs), docExtent(fab)]), { x: 88, y: 58, w: 154, h: 54 });
});

test('board: realistic face picks that side', () => {
  const f = faceLayers(LAYERS, 'bottom');
  assert.equal(f.copper.id, 'B.Cu');
  assert.equal(f.silk.id, 'B.SilkS');
  assert.equal(f.mask, null);
  assert.equal(f.outline.id, 'Edge.Cuts');
  assert.deepEqual(f.drills.map((l) => l.id), ['PTH']);
});

test('board: KiCad <-> gerber frame (y negated, origin offset)', () => {
  assert.deepEqual(kicadBoxToGerber({ x: 100, y: 70, w: 60, h: 40 }), { minX: 100, maxX: 160, minY: -110, maxY: -70 });
  assert.deepEqual(kicadBoxToGerber({ x: 100, y: 70, w: 60, h: 40 }, [100, 110]), { minX: 0, maxX: 60, minY: 0, maxY: 40 });
  assert.deepEqual(gerberPointToKicad(118, -85), [118, 85]);
  assert.deepEqual(gerberPointToKicad(18, 25, [100, 110]), [118, 85]);
});

test('board: rect, origin, style from the contract', () => {
  const pcb = { board: { origin_mm: [87.9, 51.8], size_mm: [100.7, 80], mask_color: 'red', finish: 'HASL', gerber_origin_mm: [1, 2] } };
  assert.deepEqual(boardRect(pcb), { x: 87.9, y: 51.8, w: 100.7, h: 80 });
  assert.equal(boardRect({ board: { origin_mm: [1, 2] } }), null);
  assert.deepEqual(gerberOrigin(pcb), [1, 2]);
  assert.deepEqual(gerberOrigin({ board: {} }), [0, 0]);
  const s = boardStyle(pcb.board);
  assert.deepEqual(s.mask, [0.55, 0.06, 0.06]);
  assert.deepEqual(boardStyle({ mask_color: '#ff0000' }).mask, [1, 0, 0]);
  assert.deepEqual(boardStyle({ mask_color: 'javascript:1' }).mask, boardStyle({}).mask);
  assert.deepEqual(union([null, { x: 0, y: 0, w: 1, h: 1 }, { x: 2, y: -1, w: 1, h: 1 }]), { x: 0, y: -1, w: 3, h: 2 });
  assert.equal(union([]), null);
  assert.deepEqual(grow({ x: 1, y: 1, w: 2, h: 2 }, 1), { x: 0, y: 0, w: 4, h: 4 });
  assert.equal(layerColor({ id: 'weird', kind: 'nope' }).length, 3);
});

// --- util ----------------------------------------------------------------------------------------

test('util: URL filters', () => {
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl(' JaVaScRiPt:alert(1)'), null);
  assert.equal(safeUrl('data:text/html,x'), null);
  assert.equal(safeUrl('https://x.y/a b'), 'https://x.y/a%20b');
  assert.equal(safeUrl('#/p/x'), '#/p/x');
  assert.equal(assetUrl('p/x/../../etc/passwd'), null);
  assert.equal(assetUrl('/etc/passwd'), null);
  assert.equal(assetUrl('p\\x'), null);
  assert.equal(assetUrl('p/x/./a'), null);
  assert.equal(assetUrl('p/x/sch/head/root/power sheet#1.svg'), 'p/x/sch/head/root/power%20sheet%231.svg');
});

test('util: forge links only for https + hex SHAs', () => {
  const repo = { url: 'https://github.com/o/r', blob: 'https://github.com/o/r/blob/{sha}/{path}' };
  assert.equal(commitUrl(repo, 'abc1234'), 'https://github.com/o/r/commit/abc1234');
  assert.equal(commitUrl(repo, 'abc"><x'), null);
  assert.equal(commitUrl({ url: 'javascript:alert(1)' }, 'abc1234'), null);
  assert.equal(commitUrl({ url: 'http://insecure/x' }, 'abc1234'), null);
  assert.equal(blobUrl(repo, 'abc1234', 'boards/a b'), 'https://github.com/o/r/blob/abc1234/boards/a%20b');
  assert.equal(blobUrl(repo, 'abc1234', '../x'), null);
  assert.equal(blobUrl({ blob: 'javascript:{sha}{path}' }, 'abc1234', 'x'), null);
});

test('util: bbox, viewBox, cellText', () => {
  assert.deepEqual(bbox([1, 2, 3, 4]), { x: 1, y: 2, w: 3, h: 4 });
  assert.equal(bbox([1, 2, -3, 4]), null);
  assert.equal(bbox([1, 2, 3]), null);
  assert.equal(bbox([1, 2, 3, '4']), null);
  assert.equal(bbox([1, 2, 3, NaN]), null);
  assert.deepEqual(parseViewBox('<svg width="297mm" viewBox="0 0 297.0022 210.0072">'), { x: 0, y: 0, w: 297.0022, h: 210.0072 });
  assert.equal(parseViewBox('<svg viewBox="0 0 0 5">'), null);
  assert.equal(cellText([1, 'a']), '1, a');
  assert.equal(cellText(null), '');
});

// --- tables / views -------------------------------------------------------------------------------

test('tables: query terms all have to match; status counts', () => {
  assert.ok(matchesQuery('R5 10k Resistor_SMD', 'r5 smd'));
  assert.ok(!matchesQuery('R5 10k', 'r5 4.7k'));
  assert.ok(matchesQuery('x', ''));
  assert.deepEqual(statusCounts([{ status: 'added' }, { status: 'added' }, {}]), { added: 2, unknown: 1 });
});

test('layout: face of a layer, diff layer choice, at= parsing', () => {
  assert.equal(faceOf('B.Cu'), 'bottom');
  assert.equal(faceOf('F.SilkS'), 'top');
  assert.equal(faceOf('In1.Cu'), null);
  assert.equal(faceOf(null), null);
  assert.equal(pickDiffLayer(LAYERS, 'bottom').id, 'B.Cu');
  assert.equal(pickDiffLayer(LAYERS, 'top').id, 'F.Cu');
  assert.equal(pickDiffLayer([L('F.Cu', 'copper', 'top')], 'top').id, 'F.Cu');
  assert.deepEqual(parseAt('135.3,91.6'), { x: 133.3, y: 89.6, w: 4, h: 4 });
  assert.equal(parseAt('1,2,3'), null);
  assert.equal(parseAt('javascript:1'), null);
});

test('schematic: sheet choice', () => {
  const s = [{ id: 'a', status: 'unchanged' }, { id: 'b', status: 'modified' }];
  assert.equal(pickSheet(s, 'a').id, 'a');
  assert.equal(pickSheet(s, 'zz').id, 'b');
  assert.equal(pickSheet([], 'a'), null);
});

test('changes: description', () => {
  assert.deepEqual(describeChange({ kind: 'symbol', ref: 'R5', what: 'value', base: '10k', head: '4.7k' }), { title: 'R5 value', detail: '10k → 4.7k' });
  assert.deepEqual(describeChange({ kind: 'zone', layer: 'B.Cu', detail: 'GND' }), { title: 'zone', detail: 'B.Cu · GND' });
  assert.deepEqual(describeChange({ what: 'added', base: null, head: [1, 2] }), { title: 'added', detail: '∅ → [1,2]' });
});

test('app: summary chips, tab counts, project list hygiene', () => {
  const p = { summary: { sheets_changed: 2, layers_changed: 0, components: { added: 1, moved: 3 }, nets_changed: 'x', erc: { new: 0 }, drc: { new: 2 } },
    bom: { rows: [{ status: 'added' }, { status: 'unchanged' }, null] } };
  assert.deepEqual(summaryChips(p.summary).map(([k, v]) => `${k}${v}`), ['sch2', '+1', 'mv3', 'DRC2']);
  assert.equal(tabCount(p, 'schematic'), 2);
  assert.equal(tabCount(p, 'pcba3d'), 4);
  assert.equal(tabCount(p, 'bom'), 1);
  assert.equal(tabCount(p, 'checks'), 2);
  const list = projectsOf({ projects: [{ slug: 'b', status: 'added' }, { slug: 'a', status: 'modified' }, { slug: 'a' }, { slug: '../x' }, { slug: '<img>' }, null, 'x'] });
  assert.deepEqual(list.map((x) => x.slug), ['a', 'b']);
  assert.deepEqual(projectsOf(null), []);
});

test('app: panels and boards without a schematic', () => {
  const panel = { kind: 'panel', panel: { signals: ['kikit', 'copies'], copies: 4, sources: [{ path: 'boards/a/a.kicad_pcb', copies: 4 }] },
    summary: { layers_changed: 3, panel: { fiducial: 2, mousebites: 1, bogus: 'x' } } };
  assert.deepEqual(tabsOf(panel).map(([t]) => t), ['layout', 'pcba3d', 'checks']);
  assert.equal(tabsOf(panel).at(-1)[1], 'DRC');
  assert.deepEqual(tabsOf({ kind: 'board' }).map(([t]) => t), ['layout', 'pcba3d', 'checks']);
  assert.equal(tabsOf({ kind: 'project' }).length, TABS.length);
  assert.equal(tabsOf({}).length, TABS.length); // older reviews: no kind
  assert.equal(kindTitle(panel), 'Panel: 4 × a · KiKit markers, repeated references');
  assert.equal(kindTitle({ kind: 'panel', panel: null }), 'Panel');
  assert.equal(kindTitle({ kind: 'board' }), 'Board without a schematic');
  const chips = summaryChips(panel.summary);
  assert.deepEqual(chips.map(([k, v]) => `${k}${v}`), ['pcb3', 'panel3']);
  assert.equal(chips[1][2], 'panel changes: 2 fiducials, 1 mousebite group');
});

test('grid findings: per-sheet groups and links to the spot on the sheet', () => {
  const items = [{ sheet: 'root', ref: 'U1' }, { sheet: 'root/power', text: '+3V3' }, { sheet: 'root', ref: 'R1' }, 'junk'];
  assert.deepEqual(gridGroups(items).map((g) => [g.sheet, g.items.length]), [['root', 2], ['root/power', 1]]);
  assert.equal(sheetSpotHash('demo', 'root/power', [198.73, 128.73, 2.54, 2.54], [200, 130]),
    '#/p/demo/schematic/root%2Fpower?at=198.73,128.73,2.54,2.54');
  assert.equal(sheetSpotHash('demo', 'root', 'junk', [200, 130]), '#/p/demo/schematic/root?at=200,130');
  assert.equal(sheetSpotHash('demo', 'root', null, ['x', 1]), '#/p/demo/schematic/root');
  assert.deepEqual(parseAtParam('198.73,128.73,2.54,2.54'), { x: 198.73, y: 128.73, w: 2.54, h: 2.54 });
  assert.deepEqual(parseAtParam('200,130'), { x: 198, y: 128, w: 4, h: 4 });
  assert.equal(parseAtParam('1,2,0,3'), null);
  assert.equal(parseAtParam('1,2,3'), null);
  assert.equal(parseAtParam('1e3,2'), null);
});

// --- boxes toggle --------------------------------------------------------------------------------------

test('boxes: default shown, toggle remembered, deep link wins without being remembered', async () => {
  const { boxesShown, setBoxesShown, toggleBoxes, boxesFromParams, boxesParam, onBoxes, resetBoxes } = await import('../../web/project/js/boxes.js');
  const store = new Map();
  globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) };
  try {
    resetBoxes();
    assert.equal(boxesShown(), true);
    assert.equal(boxesParam(), null);
    const seen = [];
    onBoxes((on) => seen.push(on));
    toggleBoxes();
    assert.equal(boxesShown(), false);
    assert.equal(boxesParam(), '0');
    assert.equal(store.get('kipr.boxes'), '0');
    resetBoxes(); // a new page load
    assert.equal(boxesShown(), false);
    assert.equal(boxesFromParams({ boxes: '1' }), true); // a link with boxes=1
    assert.equal(store.get('kipr.boxes'), '0', 'a link does not change the remembered choice');
    assert.equal(boxesFromParams({}), true, 'no param: keep the current choice');
    assert.deepEqual(seen, [false]);
    // storage that throws (private mode, blocked site data): still works, just not remembered
    globalThis.localStorage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
    resetBoxes();
    assert.equal(boxesShown(), true);
    setBoxesShown(false);
    assert.equal(boxesShown(), false);
  } finally {
    delete globalThis.localStorage;
    resetBoxes();
  }
});

// --- view state kept across layer / sheet / tab changes (viewstate.js) ----------------------------

test('viewstate: mergeParams keeps what a view does not name, null removes', () => {
  const cur = { view: 'top', mode: 'swipe', c: '2', z: '120,80,30', sw: '0.3', boxes: '0' };
  // a layer change names only the layer-related keys: mode, slider, zoom, boxes stay
  assert.deepEqual(mergeParams(cur, { view: 'top', mode: 'swipe', c: null }), { view: 'top', mode: 'swipe', z: '120,80,30', sw: '0.3', boxes: '0' });
  assert.deepEqual(mergeParams(cur, { z: null, at: undefined, q: '' }), { view: 'top', mode: 'swipe', c: '2', sw: '0.3', boxes: '0' });
  assert.deepEqual(mergeParams(null, { a: 1 }), { a: 1 });
  assert.deepEqual(mergeParams({ a: '1' }, null), { a: '1' });
  assert.deepEqual(cur.c, '2', 'input not mutated');
});

test('viewstate: zoom region in the URL round trips', () => {
  for (const r of [{ cx: 124.76, cy: 88.68, w: 7.027 }, { cx: -10.5, cy: 0, w: 300 }, { cx: 1.2345, cy: 2.3456, w: 0.5 }]) {
    const back = parseZoom(formatZoom(r));
    assert.ok(sameZoom(back, r), `${JSON.stringify(r)} -> ${formatZoom(r)}`);
  }
  assert.equal(formatZoom(null), null);
  assert.equal(formatZoom({ cx: 1, cy: 2, w: 0 }), null);
  assert.equal(formatZoom({ cx: NaN, cy: 2, w: 3 }), null);
  for (const bad of ['', '1,2', '1,2,3,4', 'a,b,c', '1,2,-3', '1,2,0', '1,2,1e9', undefined, 5]) assert.equal(parseZoom(bad), null, String(bad));
  assert.ok(sameZoom(null, null));
  assert.ok(!sameZoom(null, { cx: 0, cy: 0, w: 1 }));
  assert.ok(!sameZoom({ cx: 0, cy: 0, w: 10 }, { cx: 0.5, cy: 0, w: 10 }));
});

test('viewstate: sliders in the URL (only the one on show; absent keeps the current value)', () => {
  assert.equal(sliderParam(0.5), '0.5');
  assert.equal(sliderParam(0.3, false), null);
  assert.equal(sliderParam(0.3), '0.3');
  assert.equal(sliderParam(0.12345), '0.123');
  assert.equal(sliderParam(1.5), '1');
  assert.equal(parseSlider('0.3'), 0.3);
  assert.equal(parseSlider('0'), 0);
  for (const bad of [undefined, '', 'x', '-0.1', '1.1']) assert.equal(parseSlider(bad), null, String(bad));
  assert.equal(parseSlider(sliderParam(0.7)), 0.7);
});

test('viewstate: stepping through layers / sheets', () => {
  const l = ['a', 'b', 'c'];
  assert.equal(stepItem(l, 'a', 1), 'b');
  assert.equal(stepItem(l, 'c', 1), 'c'); // stops at the end
  assert.equal(stepItem(l, 'a', -1), 'a');
  assert.equal(stepItem(l, 'c', 1, true), 'a'); // wraps
  assert.equal(stepItem(l, 'a', -1, true), 'c');
  assert.equal(stepItem(l, 'zz', 1), 'a');
  assert.equal(stepItem(l, 'zz', -1), 'c');
  assert.equal(stepItem([], 'a', 1), null);
});

test('viewstate: sheet zoom kept only for the same paper size', () => {
  assert.ok(sameSize({ w: 297, h: 210 }, { w: 297.2, h: 210 }));
  assert.ok(!sameSize({ w: 297, h: 210 }, { w: 420, h: 297 }));
  assert.ok(!sameSize(null, { w: 1, h: 1 }));
});

test('viewstate: each tab remembers its route (not boxes / at)', () => {
  const mem = new Map();
  rememberRoute(mem, { slug: 'a', tab: 'layout', item: 'B.Cu', params: { mode: 'diff', z: '1,2,3', boxes: '0', at: '4,5' } });
  rememberRoute(mem, { slug: 'a', tab: 'schematic', item: 'root/power', params: { mode: 'swipe', sw: '0.3' } });
  rememberRoute(mem, { slug: null, tab: null, item: null, params: {} }); // overview: nothing
  assert.deepEqual(routeFor(mem, 'a', 'layout'), { slug: 'a', tab: 'layout', item: 'B.Cu', params: { mode: 'diff', z: '1,2,3' } });
  assert.deepEqual(routeFor(mem, 'a', 'schematic').params, { mode: 'swipe', sw: '0.3' });
  assert.deepEqual(routeFor(mem, 'b', 'layout'), { slug: 'b', tab: 'layout', item: null, params: {} });
  assert.equal(formatHash(routeFor(mem, 'a', 'layout')), '#/p/a/layout/B.Cu?mode=diff&z=1,2,3');
  // the copy is the caller's: changing it does not change the memory
  routeFor(mem, 'a', 'layout').params.mode = 'side';
  assert.equal(routeFor(mem, 'a', 'layout').params.mode, 'diff');
  assert.equal(mem.size, 2);
});

test('viewstate: notes when the kept mode does not fit the layer / sheet', () => {
  const L = (status) => ({ id: 'In1.Cu', status });
  assert.equal(layerNote(L('modified'), 'diff'), null);
  assert.match(layerNote(L('unchanged'), 'diff'), /identical/);
  assert.match(layerNote(L('added'), 'diff'), /only in head/);
  assert.match(layerNote(L('removed'), 'diff'), /only in base/);
  assert.equal(layerNote(L('added'), 'diff', { bothSides: false }), null); // an added project: every layer is
  assert.match(layerNote(L('unchanged'), 'diff', { solo: false }), /identical/); // Diff always shows one layer
  // a selected layer in the other modes: base vs head of that layer
  assert.equal(layerNote(L('modified'), 'swipe'), null);
  assert.match(layerNote(L('unchanged'), 'side'), /^In1\.Cu is identical in base and head\.$/);
  assert.match(layerNote(L('added'), 'swipe'), /only in head: the base side is empty/);
  assert.match(layerNote(L('removed'), 'onion'), /only in base: the head side is empty/);
  // no layer selected: the board view, nothing to say
  assert.equal(layerNote(L('unchanged'), 'side', { solo: false }), null);
  assert.equal(layerNote(null, 'diff'), null);
  const S = (status) => ({ id: 'root', status });
  assert.equal(sheetNote(S('modified'), 'diff'), null);
  assert.match(sheetNote(S('unchanged'), 'diff'), /identical/);
  assert.equal(sheetNote(S('unchanged'), 'side'), null);
  assert.match(sheetNote(S('added'), 'side', { hasBase: false }), /only in head.*base side is empty/);
  assert.match(sheetNote(S('removed'), 'diff', { hasHead: false }), /only in base.*removed/);
  assert.equal(sheetNote(S('added'), 'single', { hasBase: false, bothSides: false }), null);
});

// --- KiCad frame <-> view2d world (stage2d.js) ------------------------------------------------------

test('stage2d: KiCad mm (y down) <-> view2d world (y up, from the gerber origin), boxes and regions', () => {
  const f = kicadFrame([100, 50]);
  assert.deepEqual(f.point(110, 60), [10, -10]);
  assert.deepEqual(f.kicad(...f.point(123.4, -7.5)), [123.4, -7.5]);
  const b = { x: 120, y: 40, w: 30, h: 20 };
  assert.deepEqual(f.bounds(b), { minX: 20, maxX: 50, minY: -10, maxY: 10 });
  assert.deepEqual(f.box(f.bounds(b)), b);
  const r = { cx: 124.76, cy: 88.68, w: 7.027 };
  assert.deepEqual(f.toKicad(f.toWorld(r)), r);
  assert.equal(f.toWorld(null), null);
  // the schematic: no origin, y negated
  assert.deepEqual(kicadFrame().bounds({ x: 0, y: 0, w: 297, h: 210 }), { minX: 0, maxX: 297, minY: -210, maxY: 0 });
});

test('stage2d: measure readout in KiCad mm', () => {
  assert.equal(measureText([], true), 'click two points');
  assert.equal(measureText([], false), '');
  assert.equal(measureText([{ x: 0, y: 0 }, { x: 3, y: 4 }], true), 'Δx 3.000  Δy 4.000  d 5.000 mm');
});


test('viewer.css carries view2d STAGE_CSS verbatim (the CSP refuses the <style> view2d would inject)', async () => {
  const { STAGE_CSS } = await import('../../web/vendor/boarddd/src/view2d/stage.js');
  const css = (await import('node:fs')).readFileSync(new URL('../../web/project/viewer.css', import.meta.url), 'utf8');
  assert.ok(css.includes(STAGE_CSS.trim()), 'viewer.css: paste the vendored STAGE_CSS between the STAGE_CSS markers');
});

test('impedance row tooltip: inputs, widths, notes; missing side', () => {
  const t = impedanceSideTitle('head', { structure: 'coplanar_grounded', model: 'cpwg', nets: ['/RF'], length_mm: 41.25,
    widths: [{ width: 0.26, length_mm: 40 }, { width: 0.2, length_mm: 1.25 }], coplanar_gap: 0.15,
    params: { w: 0.26, h: 0.2104, er: 4.4 }, validity: ['w/h = 9 is outside'], notes: ['coplanar with a plane below'], error: null });
  assert.match(t, /^head: coplanar_grounded \(cpwg\), 1 net\(s\), 41\.3 of 41\.3 mm controlled/);
  assert.match(t, /widths: 0\.26 mm × 40\.0 mm, 0\.2 mm × 1\.3 mm/);
  assert.match(t, /coplanar gap: 0\.15 mm\ninputs: w 0\.26, h 0\.2104, er 4\.4\n⚠ w\/h = 9 is outside\n· coplanar with a plane below$/);
  assert.equal(impedanceSideTitle('base', null), 'base: —');
});

test('impedance row tooltip: field solver, every width group, left-out stubs', () => {
  const t = impedanceSideTitle('head', { structure: 'coplanar_grounded', model: 'cpwg', nets: ['/RF'], length_mm: 34.87,
    routed_mm: 41.5, solver: 'field', error_pct: 0.426, Z_closedform: 51.17,
    segments: [{ width: 0.26, gap: null, length_mm: 32, Z: 51.01, deviation_pct: 2.03, within: true },
      { width: 0.18, gap: null, length_mm: 2.87, Z: 58.9, deviation_pct: 17.8, within: false }, { width: 0.3, error: 'x' }],
    excluded: [{ width: 0.8, reason: 'launch', length_mm: 4.76 }], widths: [{ width: 0.26, length_mm: 40 }] });
  assert.match(t, /34\.9 of 41\.5 mm controlled\nsolver: field \(error estimate ±0\.43 %\), closed form 51\.2 Ω/);
  assert.match(t, /✓ 0\.26 mm × 32\.0 mm: 51\.0 Ω \(\+2\.0 %\)\n✗ 0\.18 mm × 2\.9 mm: 58\.9 Ω \(\+17\.8 %\)\n· 0\.3 mm × – mm: no Z: x/);
  assert.match(t, /left out: 0\.8 mm × 4\.8 mm launch/);
  assert.doesNotMatch(t, /widths:/);  // the groups replace the plain width list
});
