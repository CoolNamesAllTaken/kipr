// Unit tests for web/project/pcba3d/boardgeom.js (the board solid):  node --test 'tests/web-3d/*.test.mjs'
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  usableHoles, boardGeometry, outlinesDiffer, rectOutline, clearance, segmentsFor, HOLE_BUDGET,
} from '../../web/project/pcba3d/boardgeom.js';

const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const OUTLINE = { board: rect(0, -50, 100, 0), cutouts: [rect(40, -30, 60, -20)] };

test('clearance: inside/outside and distance to the nearest edge', () => {
  assert.deepEqual(clearance(OUTLINE.board, 10, -10), { inside: true, distance: 10 });
  assert.equal(clearance(OUTLINE.board, -1, -10).inside, false);
});

test('usableHoles keeps holes clear of the edge and of cutouts, skips filled ones', () => {
  const r = usableHoles([
    { x: 10, y: -10, diameter: 1, plated: true },           // fine
    { x: 0.2, y: -10, diameter: 1, plated: true },          // crosses the board edge
    { x: 50, y: -25, diameter: 1, plated: false },          // inside the cutout
    { x: 39.8, y: -25, diameter: 1 },                       // overlaps the cutout's edge
    { x: 20, y: -20, diameter: 0.3, filled: true },         // filled and capped via
    { x: 70, y: -40, diameter: 1, x2: 80, y2: -40, plated: false },   // routed slot
    { x: 30, y: -30, d: 3.2 },                              // `d` alias
    { x: NaN, y: 0, diameter: 1 },
  ], OUTLINE);
  assert.deepEqual(r.kept.map((h) => [h.ends[0][0], h.plated]), [[10, true], [70, false], [30, false]]);
  assert.equal(r.kept[1].ends.length, 2);
  assert.equal(r.kept[1].extent, 11);
  assert.equal(r.leftOut, null);
  assert.equal(r.rejected, 4);
});

test('usableHoles punches the largest openings first when over budget', () => {
  const holes = Array.from({ length: HOLE_BUDGET + 10 }, (_, i) => ({ x: 5 + (i % 40) * 2, y: -5 - Math.floor(i / 40) * 4, diameter: i === 7 ? 3 : 0.3 }));
  const r = usableHoles(holes, { board: rect(0, -100, 100, 0), cutouts: [] });
  assert.equal(r.kept.length, HOLE_BUDGET);
  assert.equal(r.kept[0].extent, 3);
  assert.deepEqual(r.leftOut, { count: 10, total: HOLE_BUDGET + 10, largest_mm: 0.3 });
});

test('boardGeometry: top/bottom/wall groups, planar UVs on the caps, barrels for plated holes', () => {
  const holes = [{ x: 10, y: -10, diameter: 1, plated: true }, { x: 20, y: -10, diameter: 3.2, plated: false }];
  const bounds = { minX: -1, maxX: 101, minY: -51, maxY: 1 };
  const g = boardGeometry(OUTLINE, holes, 1.6, bounds);
  assert.deepEqual(g.body.groups.map((x) => x.materialIndex), [0, 1, 2]);
  const pos = g.body.attributes.position, uv = g.body.attributes.uv;
  const [top, bottom] = g.body.groups;
  for (let i = top.start; i < top.start + top.count; i++) assert.ok(Math.abs(pos.getZ(i) - 1.6) < 1e-6);
  for (let i = bottom.start; i < bottom.start + bottom.count; i++) assert.ok(Math.abs(pos.getZ(i)) < 1e-6);
  for (let i = 0; i < pos.count; i++) {
    assert.ok(Math.abs(uv.getX(i) - (pos.getX(i) + 1) / 102) < 1e-6);
    assert.ok(Math.abs(uv.getY(i) - (pos.getY(i) + 51) / 52) < 1e-6);
  }
  assert.equal(g.holes.kept.length, 2);
  assert.ok(g.barrels, 'a plated hole has a barrel');
  const bb = (g.barrels.computeBoundingBox(), g.barrels.boundingBox);
  assert.ok(Math.abs(bb.min.x - (10 - 0.505)) < 0.01 && Math.abs(bb.max.x - (10 + 0.505)) < 0.01, 'barrel only around the plated hole');
  // No plated holes, no barrels.
  assert.equal(boardGeometry(OUTLINE, [holes[1]], 1.6, bounds).barrels, null);
});

test('outlinesDiffer and rectOutline', () => {
  assert.equal(outlinesDiffer(OUTLINE, JSON.parse(JSON.stringify(OUTLINE))), false);
  assert.equal(outlinesDiffer(OUTLINE, { ...OUTLINE, board: rect(0, -50, 110.16, 0) }), true);
  assert.equal(outlinesDiffer(OUTLINE, { board: OUTLINE.board, cutouts: [] }), true);
  const r = rectOutline({ origin_mm: [10, 20], size_mm: [30, 40] });
  assert.deepEqual(r.board, [[10, -60], [40, -60], [40, -20], [10, -20]]);
  assert.equal(r.approximate, true);
  assert.ok(segmentsFor(0.15) >= 10 && segmentsFor(1.6) <= 48);
});
