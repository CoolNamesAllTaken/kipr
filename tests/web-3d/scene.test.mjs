// GLB -> prepared side (web/project/pcba3d/scene.js) under node, against the mock OUT (generated
// by mock/make_mock.mjs if missing) and, when present, the real kicad-cli OUT (real/make_real.py).
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseGlb, prepareSide, boardKindFromName } from '../../web/project/pcba3d/scene.js';
import { normalizeComponents } from '../../web/project/pcba3d/diff.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOCK = join(HERE, 'out', 'mock');
if (!existsSync(join(MOCK, 'project-review.json'))) execFileSync('node', [join(HERE, 'mock', 'make_mock.mjs'), MOCK]);

function review(out) {
  return JSON.parse(readFileSync(join(out, 'project-review.json'), 'utf8'));
}
async function side(out, project, k, mutate = null) {
  const buf = readFileSync(join(out, project.pcba3d[k].glb));
  const gltf = await parseGlb(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  if (mutate) mutate(gltf.scene);
  return prepareSide(gltf, normalizeComponents(project.pcba3d.components), k, project.pcb?.board, project.pcba3d.frame);
}
const withModel = (project, k) => project.pcba3d.components.filter((c) => c[k] && c[k].model !== null).map((c) => c.ref).sort();

test('board part names as kicad-cli writes them', () => {
  assert.equal(boardKindFromName('=>[0:1:1:89] pic_programmer_PCB'), 'substrate');
  assert.equal(boardKindFromName('x pic_programmer_silkscreen'), 'silk');
  assert.equal(boardKindFromName('x pic_programmer_soldermask'), 'mask');
  assert.equal(boardKindFromName('x pic_programmer_pad'), 'copper');
  assert.equal(boardKindFromName('R12'), null);
});

for (const [slug, method] of [['demo', 'name'], ['big', 'name'], ['unnamed', 'position']]) {
  test(`mock ${slug}: every component found (${method}), board parts classified, frame aligned`, async () => {
    const r = review(MOCK);
    const project = r.projects.find((p) => p.slug === slug);
    for (const k of ['base', 'head']) {
      const s = await side(MOCK, project, k);
      assert.equal(s.report.method, method);
      assert.deepEqual([...s.comps.keys()].sort(), withModel(project, k));
      assert.equal(s.report.scale, 1000);
      assert.deepEqual(s.report.boardParts, { substrate: 1, mask: 2, copper: 1, silk: s.report.boardParts.silk });
      // The board lands where the contract says it is (y flipped: board frame is y up).
      const [ox, oy] = project.pcb.board.origin_mm, [w, h] = project.pcb.board.size_mm;
      assert.ok(Math.abs(s.boardBox.min.x - ox) < 0.01 && Math.abs(s.boardBox.max.x - (ox + w)) < 0.01, `${k} x`);
      assert.ok(Math.abs(s.boardBox.max.y + oy) < 0.01 && Math.abs(s.boardBox.min.y + (oy + h)) < 0.01, `${k} y`);
      // And each component sits on its placement.
      for (const [ref, e] of s.comps) {
        const c = project.pcba3d.components.find((x) => x.ref === ref)[k];
        const p = e.objects[0].getWorldPosition(e.box.min.clone());
        assert.ok(Math.hypot(p.x - c.x, p.y + c.y) < 0.01, `${k} ${ref} at ${p.x},${p.y} not ${c.x},${-c.y}`);
      }
    }
  });
}

test('mock newboard: no base side at all', async () => {
  const project = review(MOCK).projects.find((p) => p.slug === 'newboard');
  assert.equal(project.pcba3d.base, null);
  const s = await side(MOCK, project, 'head');
  assert.equal(s.comps.size, 12);
});

const REAL = join(HERE, 'out', 'real');
const hasReal = existsSync(join(REAL, 'project-review.json'));
test('real kicad-cli GLB: matched by name; with names and origin removed, by position', { skip: !hasReal && 'run tests/web-3d/real/make_real.py first' }, async () => {
  const project = review(REAL).projects[0];
  const expected = withModel(project, 'head');
  const s = await side(REAL, project, 'head');
  assert.equal(s.report.method, 'name');
  assert.deepEqual([...s.comps.keys()].sort(), expected);
  assert.equal(s.report.boardParts.substrate, 1);
  // Same file, names scrubbed and the whole scene shifted as if exported with another origin.
  const blind = await side(REAL, project, 'head', (scene) => {
    scene.traverse((n) => { n.name = ''; });
    scene.children[0].position.x += 0.0123;
    scene.children[0].position.z -= 0.0456;
  });
  assert.equal(blind.report.method, "position");
  const found = [...blind.comps.keys()].sort();
  if (process.env.VERBOSE) console.log('blind match', found.length, '/', expected.length, blind.report);
  // Through-hole parts packed at 2.54 mm are the hard case; demand nearly all, and no wrong ones.
  assert.ok(found.length >= expected.length - 2, `only ${found.length}/${expected.length} by position`);
  for (const [ref, e] of blind.comps) {
    const c = project.pcba3d.components.find((x) => x.ref === ref).head;
    const p = e.objects[0].getWorldPosition(e.box.min.clone());
    assert.ok(Math.hypot(p.x - c.x, p.y + c.y) < 0.05, `${ref} misplaced`);
  }
});
