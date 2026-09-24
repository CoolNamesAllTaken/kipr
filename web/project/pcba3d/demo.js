// Standalone harness for the 3D PCBA module: loads OUT/project-review.json and mounts one
// project, the way the project viewer shell does. Serve the repository root over HTTP (ES modules
// and fetch() do not work from file://):
//
//   python3 -m http.server -d <repo> 8000
//   http://localhost:8000/web/project/pcba3d/demo.html?out=../../../tests/web-3d/out/mock/&project=demo
//
// Query: out (OUT dir URL, relative to this page), project (slug), mode (side|overlay|highlight),
// focus (a ref), theme (light|dark), explode (0..1), fab=0 (keep the GLB's own board).

import { mountPcba3d } from './index.js';

const params = new URLSearchParams(location.search);
const outInput = document.getElementById('out');
const select = document.getElementById('project');
const err = document.getElementById('err');
const mountEl = document.getElementById('mount');
if (params.get('theme')) document.documentElement.dataset.theme = params.get('theme');
document.getElementById('theme').onclick = () => {
  const html = document.documentElement;
  html.dataset.theme = html.dataset.theme === 'dark' ? 'light' : 'dark';
};

let review = null;
let handle = null;

async function loadOut(out) {
  const base = new URL(out.endsWith('/') ? out : `${out}/`, location.href);
  const r = await fetch(new URL('project-review.json', base));
  if (!r.ok) throw new Error(`HTTP ${r.status} for project-review.json`);
  review = await r.json();
  review.baseUrl = base.href;
  select.replaceChildren(...review.projects.map((p) => new Option(`${p.name} (${p.status})`, p.slug)));
}

async function show(slug) {
  document.body.dataset.ready = '';
  handle?.dispose();
  const project = review.projects.find((p) => p.slug === slug) || review.projects[0];
  select.value = project.slug;
  handle = await mountPcba3d(mountEl, project, review.baseUrl, {
    baseLabel: `Base · ${review.base?.short || 'base'}`,
    headLabel: `Head · ${review.head?.short || 'head'}`,
    mode: params.get('mode') || 'side',
    fabBoard: params.get('fab') !== '0',
  });
  window.kipr3d = handle;           // for tests and the console
  await handle.ready;
  if (params.get('explode')) {
    const input = mountEl.querySelector('input[type=range]');
    input.value = params.get('explode');
    input.dispatchEvent(new Event('input'));
  }
  if (params.get('focus')) handle.focus(params.get('focus'));
  // The overlays paint the copper diff after the rest; wait for it so screenshots include it.
  if (handle.view?.gerber && handle.view.mode !== 'side') {
    await handle.view.gerber.diffTextures().catch(() => {});
    await new Promise((r) => setTimeout(r, 50));
  }
  document.body.dataset.ready = '1';
}

async function main() {
  const out = params.get('out') || '../../../tests/web-3d/out/mock/';
  outInput.value = out;
  try {
    await loadOut(out);
    await show(params.get('project'));
  } catch (e) {
    err.textContent = String(e.message || e);
    document.body.dataset.ready = 'error';
  }
}

outInput.addEventListener('change', async () => {
  try { await loadOut(outInput.value); await show(null); err.textContent = ''; } catch (e) { err.textContent = String(e.message || e); }
});
select.addEventListener('change', () => show(select.value));
main();
