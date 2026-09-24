// Shared bits of the browser tests: a static server for a site dir and a Chromium launcher.
// Chromium runs headless with SwiftShader, so WebGL2 (the gerber renderer) works without a GPU.
// If Chromium's shared libraries are not installed system-wide, point LD_LIBRARY_PATH at them.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.wasm': 'application/wasm', '.png': 'image/png',
  '.gbr': 'text/plain', '.drl': 'text/plain', '.glb': 'model/gltf-binary', '.txt': 'text/plain',
};

export function serve(root) {
  const abs = path.resolve(root);
  const server = http.createServer((req, res) => {
    const u = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    let p = path.join(abs, u);
    if (!p.startsWith(abs)) { res.writeHead(403).end(); return; }
    if (u.endsWith('/')) p = path.join(p, 'index.html');
    fs.readFile(p, (err, data) => {
      if (err) { res.writeHead(404).end('not found'); return; }
      res.writeHead(200, { 'content-type': TYPES[path.extname(p)] || 'application/octet-stream', 'cache-control': 'no-cache' });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ server, base: `http://127.0.0.1:${server.address().port}/` })));
}

export function launch() {
  return chromium.launch({ args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--ignore-gpu-blocklist'] });
}

/** Wait until the viewer has settled: no loading placeholders, no pending renders. */
export async function settle(page, timeout = 20000) {
  await page.waitForLoadState('networkidle', { timeout }).catch(() => {});
  await page.waitForFunction(() => ![...document.querySelectorAll('.loading')].some((e) => e.offsetParent !== null), null, { timeout }).catch(() => {});
  await page.waitForTimeout(250);
}

export function args(argv, defaults) {
  const out = { ...defaults };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const k = a.slice(2);
    const v = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
    out[k] = v;
  }
  return out;
}
