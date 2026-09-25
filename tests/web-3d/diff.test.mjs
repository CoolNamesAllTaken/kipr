// Unit tests for web/project/pcba3d/diff.js:  node --test tests/web-3d/
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  tagsOf, angleDelta, whatChanged, deriveStatus, normalizeComponents, countByStatus, describe, summary, isChange,
} from '../../web/project/pcba3d/diff.js';

const side = (o = {}) => ({ x: 10, y: 20, rot: 0, side: 'top', footprint: 'Lib:R_0603', value: '10k', model: 'r.step', dnp: false, ...o });

test('angleDelta wraps around', () => {
  assert.equal(angleDelta(0, 360), 0);
  assert.equal(angleDelta(350, 10), 20);
  assert.equal(angleDelta(-90, 270), 0);
  assert.equal(angleDelta(0, 180), 180);
});

test('whatChanged lists every differing field', () => {
  assert.deepEqual(whatChanged(side(), side()), []);
  assert.deepEqual(whatChanged(side(), side({ x: 12 })), ['position']);
  assert.deepEqual(whatChanged(side(), side({ rot: 90 })), ['rotation']);
  assert.deepEqual(whatChanged(side(), side({ rot: 360 })), []);
  assert.deepEqual(whatChanged(side(), side({ value: '4.7k', side: 'bottom' })), ['value', 'side']);
  assert.deepEqual(whatChanged(side(), side({ x: 10.0005 })), []);   // below 1 µm is noise
});

test('deriveStatus: presence, then changed > moved > rotated', () => {
  assert.equal(deriveStatus(null, side()), 'added');
  assert.equal(deriveStatus(side(), null), 'removed');
  assert.equal(deriveStatus(side(), side()), 'unchanged');
  assert.equal(deriveStatus(side(), side({ rot: 90 })), 'rotated');
  assert.equal(deriveStatus(side(), side({ rot: 90, x: 0 })), 'moved');
  assert.equal(deriveStatus(side(), side({ x: 0, value: '1k' })), 'changed');
  assert.equal(deriveStatus(side(), side({ dnp: true })), 'changed');
});

test('normalizeComponents keeps backend statuses, fills gaps, sorts changes first', () => {
  const list = normalizeComponents([
    { ref: 'R10', base: side(), head: side() },
    { ref: 'R2', base: side(), head: side({ x: 0 }) },
    { ref: 'C1', status: 'changed', base: side(), head: side(), what: ['value'] },   // trusted as given
    { ref: 'U1', base: null, head: side() },
    { ref: 'R1', base: side(), head: null },
    { nope: true },
  ]);
  assert.deepEqual(list.map((c) => [c.ref, c.status]), [
    ['U1', 'added'], ['R1', 'removed'], ['R2', 'moved'], ['C1', 'changed'], ['R10', 'unchanged']]);
  assert.deepEqual(list.find((c) => c.ref === 'R2').what, ['position']);
  assert.deepEqual(countByStatus(list), { added: 1, removed: 1, moved: 1, rotated: 0, changed: 1, minor: 0, unchanged: 1 });
  assert.deepEqual(normalizeComponents(undefined), []);
});

test('describe: base -> head rows with changed flags', () => {
  const [c] = normalizeComponents([{ ref: 'R5', base: side(), head: side({ value: '4.7k', x: 11 }) }]);
  const rows = Object.fromEntries(describe(c).map((r) => [r.field, r]));
  assert.equal(rows.value.base, '10k');
  assert.equal(rows.value.head, '4.7k');
  assert.equal(rows.value.changed, true);
  assert.equal(rows.footprint.changed, false);
  assert.equal(rows.position.changed, true);
  assert.equal(rows.dnp.head, 'no');
  const [added] = normalizeComponents([{ ref: 'R6', base: null, head: side() }]);
  assert.ok(describe(added).every((r) => r.base === null && !r.changed));
});

test('summary: one line per status', () => {
  const [c] = normalizeComponents([{ ref: 'R5', base: side(), head: side({ value: '4.7k' }) }]);
  assert.equal(summary(c), '10k → 4.7k');
  const [m] = normalizeComponents([{ ref: 'R5', base: side(), head: side({ x: 13, y: 24 }) }]);
  assert.equal(summary(m), 'moved 5.00 mm');
  const [r] = normalizeComponents([{ ref: 'R5', base: side(), head: side({ rot: 90 }) }]);
  assert.equal(summary(r), '0° → 90°');
  const [f] = normalizeComponents([{ ref: 'R5', base: side(), head: side({ footprint: 'Lib:R_0805' }) }]);
  assert.equal(summary(f), 'R_0603 → R_0805');
  const [o] = normalizeComponents([{ ref: 'R5', status: 'changed', base: side(), head: side(), what: ['pads', 'fields'] }]);
  assert.equal(summary(o), 'pads · part fields');
  const [a] = normalizeComponents([{ ref: 'R5', base: null, head: side() }]);
  assert.equal(summary(a), '10k · Lib:R_0603');
});

test('tagsOf: a part moved and turned counts under both, primary status first', () => {
  const [d12] = normalizeComponents([{ ref: 'D12', status: 'moved', base: side({ rot: 180 }), head: side({ x: 13, rot: 270 }), what: ['position', 'rotation'] }]);
  assert.deepEqual(tagsOf(d12), ['moved', 'rotated']);
  assert.equal(summary(d12), 'moved 3.00 mm · 180° → 270°');
  const [c] = normalizeComponents([{ ref: 'C9', base: side(), head: side({ footprint: 'Lib:X', x: 11 }) }]);
  assert.deepEqual(tagsOf(c), ['changed', 'moved']);
  const [a] = normalizeComponents([{ ref: 'A1', base: null, head: side() }]);
  assert.deepEqual(tagsOf(a), ['added']);
  const counts = countByStatus([d12, c, a]);
  assert.deepEqual([counts.moved, counts.rotated, counts.changed, counts.added], [2, 1, 1, 1]);
});

test('minor changes (3D model format only) are listed but not changes', () => {
  const side = (model) => ({ x: 1, y: 2, rot: 0, side: 'top', footprint: 'L:R_0603', value: '10k', model });
  const list = normalizeComponents([
    { ref: 'R1', status: 'changed', minor: true, what: ['model_format'], base: side('${KICAD6_3DMODEL_DIR}/R.3dshapes/R_0603.wrl'), head: side('${KICAD6_3DMODEL_DIR}/R.3dshapes/R_0603.step') },
    { ref: 'R2', status: 'changed', what: ['value'], base: side('a.step'), head: { ...side('a.step'), value: '1k' } },
  ]);
  const r1 = list.find((c) => c.ref === 'R1');
  assert.equal(r1.status, 'minor');
  assert.equal(isChange(r1), false);
  assert.deepEqual(tagsOf(r1), ['minor']);
  assert.equal(summary(r1), '3D model .wrl → .step');
  const counts = countByStatus(list);
  assert.equal(counts.minor, 1);
  assert.equal(counts.changed, 1);
  assert.equal(list[0].ref, 'R2'); // real changes first
});

test('a moved part whose model only changed format is tagged moved, not changed', () => {
  assert.deepEqual(tagsOf({ status: 'moved', what: ['position', 'model_format'] }), ['moved']);
  assert.deepEqual(tagsOf({ status: 'moved', what: ['position', 'value'] }), ['moved', 'changed']);
});

test('minor fields-only changes are minor, not changed', () => {
  const s = { x: 1, y: 2, rot: 0, side: 'top', footprint: 'L:R', value: '1k', model: 'r.step' };
  const [c] = normalizeComponents([{ ref: 'R9', status: 'changed', minor: true, what: ['fields_minor'], base: s, head: s }]);
  assert.equal(c.status, 'minor');
  assert.deepEqual(tagsOf(c), ['minor']);
});
