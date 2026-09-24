// file:// support (web/project/pcba3d/build_offline.mjs): the data packs load as plain scripts
// and give back every byte. The browser side is covered by `screenshots.py --file`.
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, cpSync, mkdtempSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { scriptJson, packs } from '../../web/project/pcba3d/build_offline.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOCK = join(HERE, 'out', 'mock');
if (!existsSync(join(MOCK, 'project-review.json'))) execFileSync('node', [join(HERE, 'mock', 'make_mock.mjs'), MOCK]);

test('scriptJson cannot close a script tag or break a line', () => {
  const nasty = { s: '</script><!-- & \u2028\u2029 "q"' };
  const text = scriptJson(nasty);
  assert.ok(!/[<>&\u2028\u2029]/.test(text), text);
  assert.deepEqual(JSON.parse(text), nasty);
});

test('packs: review + one pack per project, GLBs byte for byte', () => {
  const out = mkdtempSync(join(tmpdir(), 'kp3d-offline-'));
  try {
    cpSync(MOCK, out, { recursive: true });
    const written = packs(out).map((f) => f.split('/').pop());
    assert.ok(written.includes('review.js'));
    assert.ok(written.includes('pcba3d-demo.js') && written.includes('pcba3d-newboard.js'));
    const window = {};
    const ctx = vm.createContext({ window });
    for (const f of written) vm.runInContext(readFileSync(join(out, 'offline', f), 'utf8'), ctx, { filename: f });
    const review = JSON.parse(readFileSync(join(out, 'project-review.json'), 'utf8'));
    assert.deepEqual(JSON.parse(JSON.stringify(window.KIPR_OFFLINE.review)), review);   // other realm
    const glb = 'p/demo/3d/head.glb';
    assert.deepEqual(Buffer.from(window.KIPR_OFFLINE.files[glb].b64, 'base64'), readFileSync(join(out, glb)));
    assert.equal(window.KIPR_OFFLINE.files['p/newboard/3d/base.glb'], undefined);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});
