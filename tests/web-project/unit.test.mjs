// Unit tests for the pure parts of the project viewer.   node --test tests/web-project/unit.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHash, formatHash, TABS } from '../../web/project/js/route.js';
import { inkMask, alphaMask, dilate, diffMasks, paintDiff, regions } from '../../web/project/js/inkdiff.js';
import {
  sortLayers, faceLayers, kicadBoxToGerber, gerberPointToKicad, boardRect, gerberOrigin, boardStyle, union, grow, layerColor,
} from '../../web/project/js/board.js';
import { fitTransform, zoomAbout } from '../../web/project/js/panzoom.js';
import { safeUrl, assetUrl, commitUrl, blobUrl, bbox, parseViewBox, cellText } from '../../web/project/js/util.js';
import { matchesQuery, statusCounts } from '../../web/project/js/tables.js';
import { faceOf, pickDiffLayer, parseAt } from '../../web/project/js/layout.js';
import { pickSheet } from '../../web/project/js/schematic.js';
import { describeChange } from '../../web/project/js/changes.js';
import { summaryChips, tabCount, projectsOf } from '../../web/project/js/app.js';

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

// --- inkdiff -------------------------------------------------------------------------------------

function rgba(w, h, inkAt) {
  const d = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    const [r, g, b, a] = inkAt(i % w, Math.floor(i / w)) ? [0, 0, 0, 255] : [255, 255, 255, 255];
    d.set([r, g, b, a], i * 4);
  }
  return d;
}

test('inkdiff: white paper is not ink, dark opaque is', () => {
  const m = inkMask(rgba(4, 1, (x) => x === 2), 4, 1);
  assert.deepEqual([...m], [0, 0, 1, 0]);
  const t = new Uint8ClampedArray([0, 0, 0, 10, 0, 0, 0, 255]);
  assert.deepEqual([...inkMask(t, 2, 1)], [0, 1]);
  assert.deepEqual([...alphaMask(new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 0]), 2, 1)], [1, 0]);
});

test('inkdiff: dilate is a square of radius r, clipped at the edges', () => {
  const m = new Uint8Array(25); m[12] = 1;
  const d = dilate(m, 5, 5, 1);
  assert.equal(d.reduce((a, b) => a + b, 0), 9);
  const e = new Uint8Array(25); e[0] = 1;
  assert.equal(dilate(e, 5, 5, 2).reduce((a, b) => a + b, 0), 9);
  assert.deepEqual([...dilate(m, 5, 5, 0)], [...m]);
});

test('inkdiff: classify removed / added / common with tolerance', () => {
  const w = 20; const h = 1;
  const base = inkMask(rgba(w, h, (x) => x === 2 || x === 10), w, h);
  const head = inkMask(rgba(w, h, (x) => x === 3 || x === 16), w, h);
  const d = diffMasks(base, head, w, h, 1);
  // 2 vs 3 is within 1 px: common; 10 only in base: removed; 16 only in head: added
  assert.deepEqual(d.counts, { removed: 1, added: 1, common: 2 });
  assert.equal(d.removed[10], 1);
  assert.equal(d.added[16], 1);
  const strict = diffMasks(base, head, w, h, 0);
  assert.deepEqual(strict.counts, { removed: 2, added: 2, common: 0 });
  const out = paintDiff(new Uint8ClampedArray(w * 4), d);
  assert.deepEqual([...out.slice(40, 44)], [225, 40, 40, 255]);
  assert.deepEqual([...out.slice(64, 68)], [30, 175, 70, 255]);
  assert.equal(out[3], 0);
});

test('inkdiff: regions merge nearby pixels and drop noise', () => {
  const w = 40; const h = 20;
  const m = new Uint8Array(w * h);
  const set = (x, y) => { m[y * w + x] = 1; };
  for (let x = 2; x < 6; x++) for (let y = 2; y < 4; y++) set(x, y); // blob A (8 px)
  for (let x = 8; x < 10; x++) for (let y = 2; y < 4; y++) set(x, y); // 2 px gap from A: merged
  for (let x = 30; x < 34; x++) for (let y = 10; y < 14; y++) set(x, y); // blob B
  set(20, 18); // single noise pixel
  const r = regions(m, w, h, { gap: 4, minPixels: 3 });
  assert.equal(r.length, 2);
  assert.deepEqual(r[0], { x: 2, y: 2, w: 8, h: 2, pixels: 12 });
  assert.deepEqual(r[1], { x: 30, y: 10, w: 4, h: 4, pixels: 16 });
});

// --- board ---------------------------------------------------------------------------------------

const L = (id, kind, side, status = 'unchanged') => ({ id, kind, side, status });
const LAYERS = [L('F.SilkS', 'silk', 'top'), L('Edge.Cuts', 'outline', 'none'), L('B.Cu', 'copper', 'bottom', 'modified'),
  L('PTH', 'drill', 'none'), L('F.Cu', 'copper', 'top', 'modified'), L('In1.Cu', 'copper', 'inner'), L('F.Mask', 'mask', 'top'), L('B.SilkS', 'silk', 'bottom')];

test('board: layer stack order back -> inner -> front -> outline -> drills', () => {
  assert.deepEqual(sortLayers(LAYERS).map((l) => l.id), ['B.SilkS', 'B.Cu', 'In1.Cu', 'F.Cu', 'F.Mask', 'F.SilkS', 'Edge.Cuts', 'PTH']);
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

// --- panzoom math --------------------------------------------------------------------------------

test('panzoom: fit centres the box, zoom keeps the anchor point fixed', () => {
  const v = fitTransform({ x: 0, y: 0, w: 200, h: 100 }, 400, 400, 0);
  assert.equal(v.s, 2);
  assert.equal(v.tx, 0);
  assert.equal(v.ty, 100);
  const z = zoomAbout(v, 100, 150, 2);
  assert.equal(z.s, 4);
  // world point under (100, 150) stays under it
  assert.equal((100 - v.tx) / v.s, (100 - z.tx) / z.s);
  assert.equal((150 - v.ty) / v.s, (150 - z.ty) / z.s);
  assert.equal(zoomAbout({ s: 1, tx: 0, ty: 0 }, 0, 0, 1e9).s, 2000);
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
