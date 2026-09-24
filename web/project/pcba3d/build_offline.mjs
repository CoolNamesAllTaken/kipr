#!/usr/bin/env node
// Makes the 3D PCBA viewer work from a report opened from disk (file://).
//
//   node web/project/pcba3d/build_offline.mjs --out OUT [--bundle-dir DIR] [--no-bundle] [--no-packs]
//
// Browsers opened on file:// refuse ES modules and fetch(), which is how the viewer normally
// loads itself and its data. This writes the two things that get around that, the scheme
// kicad-libs' component-review viewer uses (tools/component-review/viewer/js/boot.js):
//
//   1. Classic-script bundles (esbuild, pinned), next to index.js by default:
//        pcba3d.bundle.js  the module; sets window.KIPR_PCBA3D = {mountPcba3d} (for the shell)
//        demo.bundle.js    demo.js and everything it imports (for demo.html; see demo-boot.js)
//      import.meta.url is replaced by the bundle's own script URL, so relative assets
//      (pcba3d.css, ../vendor/...) resolve exactly as they do from the modules.
//   2. Data packs in OUT/offline/, loaded with <script> on demand (assets.js):
//        review.js              window.KIPR_OFFLINE.review = project-review.json
//        pcba3d-<slug>.js       that project's GLBs (base64) and fab files (text)
//        pcba3d-vendor.js       the gerber renderer's WASM (base64), shared by every project
//
// Cost: the packs repeat the GLBs at +33 % (base64); pic_programmer is ~28 MB. Over http none of
// this is used. Pack contents are JSON with <, >, &, U+2028/2029 escaped, so no file can end
// the script it is embedded in.

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ESBUILD = process.env.ESBUILD || 'esbuild@0.28.2';
// The names the module looks the WASM and the fab files up by, from the module itself. Importing
// gerberboard.js needs the shared renderer vendored (it is, wherever there is a WASM to pack).
const { OFFLINE_WASM_KEY: WASM_KEY, FAB_KINDS } = await import('./gerberboard.js').catch(() => ({
  OFFLINE_WASM_KEY: 'vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm',
  FAB_KINDS: new Set(['copper', 'mask', 'silk', 'outline', 'drill']),
}));
const WASM_FILE = resolve(HERE, '..', WASM_KEY);

function args() {
  const a = process.argv.slice(2);
  const opt = { out: null, bundleDir: HERE, bundle: true, packs: true };
  for (let i = 0; i < a.length; i++) {
    if (a[i] === '--out') opt.out = resolve(a[++i]);
    else if (a[i] === '--bundle-dir') opt.bundleDir = resolve(a[++i]);
    else if (a[i] === '--no-bundle') opt.bundle = false;
    else if (a[i] === '--no-packs') opt.packs = false;
    else throw new Error(`unknown argument ${a[i]}`);
  }
  if (opt.packs && !opt.out) throw new Error('--out OUT is required (or --no-packs)');
  return opt;
}

/** JSON that is safe inside a <script>: nothing in it can close the tag or break the line. */
export function scriptJson(value) {
  return JSON.stringify(value).replace(/[<>&\u2028\u2029]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

function bundle(entry, outfile) {
  mkdirSync(dirname(outfile), { recursive: true });
  execFileSync('npx', ['--yes', ESBUILD, join(HERE, entry), '--bundle', '--format=iife', '--target=es2020',
    '--define:import.meta.url=KIPR_PCBA3D_SCRIPT_URL',
    '--banner:js=var KIPR_PCBA3D_SCRIPT_URL = (document.currentScript && document.currentScript.src) || location.href;',
    '--legal-comments=eof', '--log-level=warning', `--outfile=${outfile}`], { stdio: 'inherit' });
}

function pack(file, entries, extra = '') {
  const lines = ['(function (o) {', '  o.files = o.files || {};'];
  for (const [path, value] of entries) lines.push(`  o.files[${scriptJson(path)}] = ${scriptJson(value)};`);
  if (extra) lines.push(extra);
  lines.push('})(window.KIPR_OFFLINE = window.KIPR_OFFLINE || {});');
  writeFileSync(file, lines.join('\n') + '\n');
}

export function packs(out) {
  const review = JSON.parse(readFileSync(join(out, 'project-review.json'), 'utf8'));
  const dir = join(out, 'offline');
  mkdirSync(dir, { recursive: true });
  pack(join(dir, 'review.js'), [], `  o.review = ${scriptJson(review)};`);
  const written = ['review.js'];
  const inOut = (p) => typeof p === 'string' && !p.includes('..') && existsSync(join(out, p));
  for (const project of review.projects || []) {
    const entries = [];
    for (const side of ['base', 'head']) {
      const glb = project.pcba3d?.[side]?.glb;
      if (inOut(glb)) entries.push([glb, { b64: readFileSync(join(out, glb)).toString('base64') }]);
    }
    for (const layer of project.pcb?.layers || []) {
      if (!FAB_KINDS.has(layer.kind)) continue;
      for (const side of ['base', 'head']) {
        const p = layer[side]?.gerber;
        if (inOut(p)) entries.push([p, { text: readFileSync(join(out, p), 'utf8') }]);
      }
    }
    if (!entries.length) continue;
    pack(join(dir, `pcba3d-${project.slug}.js`), entries);
    written.push(`pcba3d-${project.slug}.js`);
  }
  if (existsSync(WASM_FILE)) {
    pack(join(dir, 'pcba3d-vendor.js'), [[WASM_KEY, { b64: readFileSync(WASM_FILE).toString('base64') }]]);
    written.push('pcba3d-vendor.js');
  } else {
    console.warn(`no ${WASM_FILE}: the board from gerbers will fall back to the GLB's from file://`);
  }
  return written.map((f) => join(dir, f));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const opt = args();
  if (opt.bundle) {
    bundle('offline_entry.js', join(opt.bundleDir, 'pcba3d.bundle.js'));
    bundle('demo.js', join(opt.bundleDir, 'demo.bundle.js'));
    console.log(`bundles: ${join(opt.bundleDir, 'pcba3d.bundle.js')}, demo.bundle.js`);
  }
  if (opt.packs) for (const f of packs(opt.out)) console.log(`pack: ${f}`);
}
