// Unit tests for web/project/pcba3d/match.js:  node --test tests/web-3d/
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  refFromName, naturalCompare, houghTranslation, matchByPosition, mapNodesToRefs, toBoardFrame,
} from '../../web/project/pcba3d/match.js';

const REFS = new Set(['R1', 'R11', 'U3', 'C10', 'J1']);

test('refFromName: exact, copy suffixes, leading token', () => {
  assert.equal(refFromName('R1', REFS), 'R1');
  assert.equal(refFromName('R1_1', REFS), 'R1');
  assert.equal(refFromName('U3 (2)', REFS), 'U3');
  assert.equal(refFromName('U3:1', REFS), 'U3');
  assert.equal(refFromName('J1 [conn]', REFS), 'J1');
  assert.equal(refFromName(' C10 ', REFS), 'C10');
});

test('refFromName: never strips digits without a separator, ignores opaque names', () => {
  assert.equal(refFromName('R11', REFS), 'R11');
  assert.equal(refFromName('R111', REFS), null);
  assert.equal(refFromName('=>[0:1:1:3]', REFS), null);
  assert.equal(refFromName('pic_programmer_PCB', REFS), null);
  assert.equal(refFromName('', REFS), null);
  assert.equal(refFromName(undefined, REFS), null);
});

test('naturalCompare sorts designators like a person would', () => {
  const refs = ['R10', 'R2', 'C1', 'R1', 'U1A', 'U1', 'R100'];
  assert.deepEqual(refs.sort(naturalCompare), ['C1', 'R1', 'R2', 'R10', 'R100', 'U1', 'U1A']);
});

// A board of components and the nodes a GLB would have for them, shifted by an unknown origin.
function board(n = 40, seed = 1) {
  let s = seed;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  return Array.from({ length: n }, (_, i) => ({ ref: `R${i + 1}`, x: 100 + rand() * 80, y: 50 + rand() * 60 }));
}
function nodesFor(components, shift, { names = false, jitter = 0 } = {}) {
  return components.map((c, i) => {
    const a = toBoardFrame(c);
    const x = a.x + shift.x + (i % 2 ? jitter : -jitter), y = a.y + shift.y;
    return { name: names ? c.ref : `=>[0:1:1:${i}]`, x, y, cx: x + 0.3, cy: y };
  });
}

test('houghTranslation recovers an unknown export origin', () => {
  const comps = board();
  const shift = { x: -140, y: 80 };    // e.g. kicad-cli --user-origin / board centre
  const t = houghTranslation(nodesFor(comps, shift), comps.map(toBoardFrame));
  assert.ok(Math.abs(t.x - shift.x) < 0.05 && Math.abs(t.y - shift.y) < 0.05, JSON.stringify(t));
});

test('mapNodesToRefs by name: offset from the named nodes, all matched', () => {
  const comps = board(30);
  const r = mapNodesToRefs(nodesFor(comps, { x: 5, y: -7 }, { names: true }), comps);
  assert.equal(r.method, 'name');
  assert.equal(r.byRef.size, 30);
  assert.deepEqual(r.offset, { x: 5, y: -7 });
  assert.deepEqual(r.leftover, []);
});

test('mapNodesToRefs by position: shuffled opaque nodes, shifted origin', () => {
  const comps = board(60, 7);
  const nodes = nodesFor(comps, { x: 12.5, y: -3 }, { jitter: 0.05 });
  const order = nodes.map((n, i) => i).reverse();
  const shuffled = order.map((i) => nodes[i]);
  const r = mapNodesToRefs(shuffled, comps);
  assert.equal(r.method, 'position');
  assert.equal(r.byRef.size, 60);
  for (const [ref, [k]] of r.byRef) assert.equal(`R${order[k] + 1}`, ref);
});

test('mapNodesToRefs mixed: names for some, positions for the rest', () => {
  const comps = board(20, 3);
  const nodes = nodesFor(comps, { x: 0, y: 0 });
  nodes[0].name = 'R1'; nodes[1].name = 'R2'; nodes[2].name = 'R3';
  const r = mapNodesToRefs(nodes, comps);
  assert.equal(r.method, 'mixed');
  assert.equal(r.byName, 3);
  assert.equal(r.byRef.size, 20);
});

test('mapNodesToRefs: a part split over two nodes keeps both; far nodes stay leftover', () => {
  const comps = [{ ref: 'U1', x: 10, y: 10 }, { ref: 'R1', x: 30, y: 10 }, { ref: 'R2', x: 50, y: 20 }];
  const nodes = [
    { name: 'U1', x: 10, y: -10, cx: 10, cy: -10 },
    { name: 'pins', x: 10.2, y: -10, cx: 10.2, cy: -10 },       // second piece of U1
    { name: 'R1', x: 30, y: -10, cx: 30, cy: -10 },
    { name: 'R2', x: 50, y: -20, cx: 50, cy: -20 },
    { name: 'stray', x: 90, y: -90, cx: 90, cy: -90 },
  ];
  const r = mapNodesToRefs(nodes, comps);
  assert.deepEqual(r.byRef.get('U1'), [0, 1]);
  assert.deepEqual(r.leftover, [4]);
});

test('matchByPosition refuses a coin toss, then settles mutual nearest pairs', () => {
  // Two targets 0.4 mm apart with nodes right on them: each is the other's runner-up.
  const nodes = [{ x: 0, y: 0, cx: 0, cy: 0 }, { x: 0.4, y: 0, cx: 0.4, cy: 0 }];
  const r = matchByPosition(nodes, [{ ref: 'A', aim: { x: 0.01, y: 0 } }, { ref: 'B', aim: { x: 0.39, y: 0 } }]);
  assert.equal(r.matched.get('A'), 0);
  assert.equal(r.matched.get('B'), 1);
  // A target with nothing near stays unmatched.
  const r2 = matchByPosition(nodes, [{ ref: 'C', aim: { x: 20, y: 0 } }]);
  assert.deepEqual(r2.unmatched, ['C']);
});

test('mapNodesToRefs: nothing to go by falls back to the given offset', () => {
  const r = mapNodesToRefs([], [{ ref: 'R1', x: 1, y: 1 }], { fallbackOffset: { x: 3, y: 4 } });
  assert.deepEqual(r.offset, { x: 3, y: 4 });
  assert.deepEqual(r.unmatched, ['R1']);
  assert.equal(r.method, 'none');
});
