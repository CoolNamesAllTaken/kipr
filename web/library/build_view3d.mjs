#!/usr/bin/env node
// The library viewer's 3D view as one classic script, for reports opened from disk (file://).
//
//   node web/library/build_view3d.mjs            rebuild js/view3d.bundle.js (commit it)
//   node web/library/build_view3d.mjs --check    CI: fail if the committed bundle is stale
//
// Browsers refuse ES modules on file://, and js/view3d.js imports boarddd and three.js from vendor/.
// kipr library site turns the viewer's own modules into js/bundle.js (Python, kipr/library/site.py)
// but cannot bundle vendor/, so js/view3d.bundle.js is prebuilt here with esbuild (pinned) and
// committed (shipped in the wheel): building a site needs no node or network. It sets
// window.KIPR_VIEW3D = {create3DViewer, ...}; js/panel3d.js loads it with a <script> on file://
// and imports js/view3d.js over http. import.meta.url becomes the bundle's own script URL, so
// vendor/ paths resolve as they do from the modules. Same scheme as web/project/pcba3d/build_offline.mjs.

import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ESBUILD = process.env.ESBUILD || 'esbuild@0.28.2';
const BUNDLE = join(HERE, 'js', 'view3d.bundle.js');

function bundle(outfile) {
  execFileSync('npx', ['--yes', ESBUILD, 'js/view3d.js', '--bundle', '--format=iife', '--target=es2020',
    '--global-name=KIPR_VIEW3D',
    '--define:import.meta.url=KIPR_VIEW3D_SCRIPT_URL',
    '--banner:js=var KIPR_VIEW3D_SCRIPT_URL = (document.currentScript && document.currentScript.src) || location.href;',
    '--legal-comments=eof', '--log-level=warning', `--outfile=${outfile}`], { stdio: 'inherit', cwd: HERE });
}

if (process.argv.includes('--check')) {
  const tmp = mkdtempSync(join(tmpdir(), 'kipr-view3d-'));
  try {
    bundle(join(tmp, 'view3d.bundle.js'));
    if (!existsSync(BUNDLE) || !readFileSync(BUNDLE).equals(readFileSync(join(tmp, 'view3d.bundle.js')))) {
      console.error(`${BUNDLE} is stale: rebuild it with node ${join(HERE, 'build_view3d.mjs')}`);
      process.exit(1);
    }
    console.log('view3d.bundle.js is up to date');
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
} else {
  bundle(BUNDLE);
  console.log(`bundle: ${BUNDLE}`);
}
