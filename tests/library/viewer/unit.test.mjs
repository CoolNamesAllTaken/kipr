// Unit tests for the pure parts of the viewer.   node --test tests/library/viewer/unit.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { modelMatrix, applyMatrix, padToPcb, padDrill, padCopperSides, padOutline, padOffset, padHoleCenter, slotPoints, padDrillSlot, padDrillRing } from '../../../web/library/js/kicad3d.js';
import { safeUrl, assetUrl, githubBlobUrl } from '../../../web/library/js/util.js';
import { diffRows } from '../../../web/library/js/details.js';

const near = (a, b, eps = 1e-9) => a.every((v, i) => Math.abs(v - b[i]) < eps);

test('model transform: identity', () => {
  assert.ok(near(applyMatrix(modelMatrix({}), [1, 2, 3]), [1, 2, 3]));
});

test('model transform: offset in KiCad 3D frame (y up)', () => {
  assert.ok(near(applyMatrix(modelMatrix({ offset: [5.55, -3.05, 1.2] }), [0, 0, 0]), [5.55, -3.05, 1.2]));
});

test('model transform: rotate z is negated like KiCad (+90 turns +x to -y)', () => {
  assert.ok(near(applyMatrix(modelMatrix({ rotate: [0, 0, 90] }), [1, 0, 0]), [0, -1, 0]));
});

test('model transform: order is scale, X, Y, Z, then translate', () => {
  // rotate x 90 (negated -> -90 about X): +y -> -z ; then z 90 (negated): unaffected for a z vector
  const m = modelMatrix({ rotate: [90, 0, 90], scale: [2, 2, 2], offset: [1, 0, 0] });
  assert.ok(near(applyMatrix(m, [0, 1, 0]), [1, 0, -2]));
  // buzzer case from the demo PR: rotate (90, 180, 0), offset (0, -2, 5)
  const b = modelMatrix({ rotate: [90, 180, 0], offset: [0, -2, 5] });
  assert.ok(near(applyMatrix(b, [0, 0, 0]), [0, -2, 5]));
});

test('pad helpers', () => {
  const pad = { at: [1, 2, 90], size: [2, 1], shape: 'rect', layers: ['*.Cu', '*.Mask'], drill: { shape: 'circle', size: [0.8, 0.8], offset: [0, 0] } };
  assert.ok(near(padToPcb(pad, [1, 0]), [1, 1], 1e-12)); // CCW on screen: +x goes to -y (up)
  assert.deepEqual(padCopperSides(pad), { top: true, bottom: true });
  assert.equal(padDrill(pad).w, 0.8);
  assert.equal(padDrill({ drill: null }), null);
  assert.equal(padOutline({ size: [1, 1], shape: 'roundrect', roundrect_rratio: 0.25 }).outer.length, 36);
});

test('pad shape offset moves the copper, not the hole (KiCad (drill (offset)))', () => {
  const box = (ring) => [Math.min(...ring.map((q) => q[0])), Math.max(...ring.map((q) => q[0]))];
  // RP2040-Zero castellated pad 1: SMD, offset only, copper centred 0.65 mm outward of `at`
  const smd = { at: [-7.62, -10.16, 0], size: [3, 1.7], shape: 'roundrect', roundrect_rratio: 0.25, offset: [-0.65, 0], drill: null };
  assert.ok(near(box(padOutline(smd).outer), [-2.15, 0.85], 1e-9));
  assert.equal(padDrill(smd), null);
  // rotated THT pad: offset turns with the pad; hole stays on `at`
  const tht = { at: [2, 3, 90], size: [1, 2], shape: 'oval', offset: [0, -0.5], drill: { shape: 'circle', size: [0.6, 0.6] } };
  const c = padOutline(tht, 32).outer.map((q) => padToPcb(tht, q));
  const mid = (i) => (Math.min(...c.map((q) => q[i])) + Math.max(...c.map((q) => q[i]))) / 2;
  assert.ok(near([mid(0), mid(1)], padToPcb(tht, [0, -0.5]), 1e-9));
  assert.ok(near(padToPcb(tht, [0, -0.5]), [1.5, 3], 1e-12));
  assert.deepEqual(padHoleCenter(tht), [2, 3]);
  // geom.json from before the fix: the offset only came as drill.offset
  assert.deepEqual(padOffset({ drill: { size: [1, 1], offset: [0.2, 0] } }), [0.2, 0]);
  assert.deepEqual(padOffset({ drill: null }), [0, 0]);
});

// Distance from (px, py) to the segment a-b.
const toSegment = ([px, py], [ax, ay], [bx, by]) => {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
};
// A ring's straight flanks: edges longer than any cap chord, as [length, direction in degrees mod 180].
const flanks = (ring) => ring.map((p, i) => [p, ring[(i + 1) % ring.length]])
  .map(([[x1, y1], [x2, y2]]) => [Math.hypot(x2 - x1, y2 - y1), ((Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI + 360) % 180])
  .filter(([len]) => len > 0.5);

test('slotPoints: a stadium, every point on the radius around the centre segment, straight flanks', () => {
  const ring = slotPoints(-0.5, 0, 0.5, 0, 0.5, 24);
  for (const q of ring) assert.ok(Math.abs(toSegment(q, [-0.5, 0], [0.5, 0]) - 0.5) < 1e-12, `${q}`);
  const f = flanks(ring);
  assert.equal(f.length, 2);
  for (const [len, dir] of f) { assert.ok(Math.abs(len - 1) < 1e-12); assert.ok(Math.abs(dir % 180) < 1e-9 || Math.abs(dir - 180) < 1e-9); }
  // an ellipse with the same 2 x 1 extent would bulge past the stadium's flanks nowhere and fall short of its
  // corners: the stadium reaches (+-0.5, +-0.5)
  assert.ok(ring.some(([x, y]) => Math.abs(x - 0.5) < 1e-12 && Math.abs(y - 0.5) < 1e-12));
  assert.ok(ring.some(([x, y]) => Math.abs(x + 0.5) < 1e-12 && Math.abs(y + 0.5) < 1e-12));
  // coincident ends: a circle
  const c = slotPoints(1, 1, 1, 1, 0.4, 24);
  assert.equal(c.length, 24);
  for (const [x, y] of c) assert.ok(Math.abs(Math.hypot(x - 1, y - 1) - 0.4) < 1e-12);
});

test('oval drill 1.0 x 2.0 mm: flank 1.0, end radius 0.5, long axis along the larger size, turned with the pad', () => {
  const check = (pad, axisDeg) => {
    const { ends, radius } = padDrillSlot(pad);
    assert.ok(Math.abs(radius - 0.5) < 1e-12);
    const [[x1, y1], [x2, y2]] = ends;
    assert.ok(Math.abs(Math.hypot(x2 - x1, y2 - y1) - 1) < 1e-12);
    const ring = padDrillRing(pad, { segments: 32 });   // board frame: y up
    const [b1, b2] = ends.map(([x, y]) => [x, -y]);
    for (const q of ring) assert.ok(Math.abs(toSegment(q, b1, b2) - 0.5) < 1e-9);
    const f = flanks(ring);
    assert.equal(f.length, 2, JSON.stringify(f));
    for (const [len, dir] of f) {
      assert.ok(Math.abs(len - 1) < 1e-9);
      const off = Math.abs(((dir - axisDeg) % 180 + 180) % 180);
      assert.ok(off < 1e-6 || Math.abs(off - 180) < 1e-6, `flank at ${dir} deg, want ${axisDeg}`);
    }
    // extent: 2 mm along the axis, 1 mm across
    const a = (axisDeg * Math.PI) / 180;
    const along = ring.map(([x, y]) => x * Math.cos(a) + y * Math.sin(a));
    const across = ring.map(([x, y]) => -x * Math.sin(a) + y * Math.cos(a));
    const span = (v) => Math.max(...v) - Math.min(...v);
    assert.ok(Math.abs(span(along) - 2) < 1e-9);
    assert.ok(Math.abs(span(across) - 1) < 1e-9);
  };
  const oval = (w, h, rot) => ({ at: [3, -2, rot], size: [w + 0.6, h + 0.6], shape: 'oval', layers: ['*.Cu'], drill: { shape: 'oval', size: [w, h] } });
  check(oval(2, 1, 0), 0);      // w > h: along x
  check(oval(1, 2, 0), 90);     // h > w: along y
  check(oval(2, 1, 90), 90);    // turned a quarter: along y
  check(oval(1, 2, 90), 0);
  check(oval(2, 1, 45), 45);    // KiCad turns CCW on screen; the board frame is y up, so +45 stays +45
  check(oval(1, 2, 45), 135);
  // round drill: a circle around `at`
  const round = padDrillRing({ at: [1, 2, 30], drill: { shape: 'circle', size: [0.8, 0.8] } });
  for (const [x, y] of round) assert.ok(Math.abs(Math.hypot(x - 1, y + 2) - 0.4) < 1e-12);
  // grow (plating / annular ring) widens the radius, not the flanks
  assert.ok(Math.abs(padDrillSlot(oval(2, 1, 0), 0.1).radius - 0.6) < 1e-12);
  assert.equal(padDrillRing({ drill: null }), null);
});

test('URL filters', () => {
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl('data:text/html,x'), null);
  assert.equal(safeUrl('https://example.com/a b'), 'https://example.com/a%20b');
  assert.equal(assetUrl('../x'), null);
  assert.equal(assetUrl('items/a/../../x'), null);
  assert.equal(assetUrl('/etc/passwd'), null);
  assert.equal(assetUrl('items\\a'), null);
  assert.equal(assetUrl('items/foo bar/head_F.Cu.svg'), 'items/foo%20bar/head_F.Cu.svg');
  assert.equal(githubBlobUrl('a/b', 'abc1234', 'lib/x.kicad_mod', [3, 9]), 'https://github.com/a/b/blob/abc1234/lib/x.kicad_mod#L3-L9');
  assert.equal(githubBlobUrl('a/b"><x', 'abc1234', 'x'), null);
  assert.equal(githubBlobUrl('a/b', 'nothex!', 'x'), null);
  assert.equal(githubBlobUrl('a/b', 'abc1234', '../x'), null);
});

test('pad diff matches duplicate numbers in order', () => {
  const cols = [{ name: 'x', get: (p) => p.x }];
  const rows = diffRows([{ number: 'SH', x: 1 }, { number: 'SH', x: 2 }, { number: '1', x: 0 }],
    [{ number: 'SH', x: 1 }, { number: 'SH', x: 3 }, { number: '2', x: 0 }], (p) => p.number, cols);
  const st = rows.map((r) => r.status).sort();
  assert.deepEqual(st, ['added', 'changed', 'removed', 'same']);
});
