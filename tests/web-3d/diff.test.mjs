// Unit tests for web/project/pcba3d/diff.js:  node --test tests/web-3d/
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  angleDelta, whatChanged, deriveStatus, normalizeComponents, countByStatus, describe, summary,
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
  assert.deepEqual(countByStatus(list), { added: 1, removed: 1, moved: 1, rotated: 0, changed: 1, unchanged: 1 });
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
  const [a] = normalizeComponents([{ ref: 'R5', base: null, head: side() }]);
  assert.equal(summary(a), '10k · Lib:R_0603');
});
