// Hostile-input check: project-review.json is derived from a pull request, so every string in it must
// be rendered as text and every URL / path filtered.
//
//   node xss_check.mjs --site OUT [--mode http|file|both]
//
// Copies OUT, rewrites project-review.json with injection payloads in every string field (names,
// slugs, sheet ids, refs, nets, descriptions, paths, repo URLs), rebuilds the site (kipr.project.site)
// and the no-JS report (kipr.project.report) from the poisoned copy, opens every view of every project
// and fails if any script runs, an injected element or attribute appears, a javascript:/data: link is
// rendered, data.js / the offline packs contain a raw </script> or files from outside p/<slug>/, or the
// report contains markup from the payloads.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { serve, launch, settle, args } from './harness.mjs';

const a = args(process.argv.slice(2), { mode: 'both' });
if (!a.site) { console.error('usage: node xss_check.mjs --site OUT [--mode http|file|both]'); process.exit(2); }
const ROOT = path.resolve(new URL('../..', import.meta.url).pathname);

const P = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
const JS = 'javascript:window.__pwned=1';
const SECRET = 'TOP-SECRET-OUTSIDE-P';

function poisonStrings(v, key = '') {
  // every string becomes hostile, except the fields that must stay valid for the view to render at all
  if (typeof v === 'string') {
    if (['status', 'kind', 'side', 'severity', 'slug'].includes(key)) return v;
    if (/^(base|head|gerber|svg|glb)$/.test(key) && /\.(svg|gbr|drl|glb)$/.test(v)) return v;
    return v + P;
  }
  if (Array.isArray(v)) return v.map((x) => poisonStrings(x, key));
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, poisonStrings(x, k)]));
  return v;
}

function poison(dir) {
  const f = path.join(dir, 'project-review.json');
  const r = poisonStrings(JSON.parse(fs.readFileSync(f, 'utf8')));
  r.repo = { url: JS, blob: `${JS}/{sha}/{path}` };
  r.base = { sha: `abc"><img src=x onerror=window.__pwned=1>`, ref: P, short: P };
  fs.writeFileSync(path.join(dir, 'secret.txt'), SECRET);
  const first = r.projects[0];
  // paths that point outside the project / site / to other schemes must be refused everywhere
  const sheets = first.schematic?.sheets || [];
  if (sheets[0]) { sheets[0].base = '../secret.txt'; sheets[0].id = `root"><img src=x onerror=window.__pwned=1>`; }
  if (sheets[1]) { sheets[1].head = JS; sheets[1].base = `p/${first.slug}/../../secret.txt`; }
  if (sheets[2]) sheets[2].head = '/etc/passwd';
  const layers = first.pcb?.layers || [];
  if (layers[0]) layers[0].base = { gerber: '../../secret.txt', svg: 'data:image/svg+xml,<svg onload=alert(1)>' };
  if (layers[1]) layers[1].id = `F.Cu"><img src=x onerror=window.__pwned=1>`;
  // a hostile SVG export next to the data: must only ever be loaded as an image
  const evil = path.join(dir, 'p', first.slug, 'evil.svg');
  fs.mkdirSync(path.dirname(evil), { recursive: true });
  fs.writeFileSync(evil, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 297 210" onload="parent.__pwned=1"><script>parent.__pwned=1</script><text x="10" y="10">&lt;/script&gt;${SECRET.length}</text></svg>`);
  if (sheets[3]) sheets[3].head = `p/${first.slug}/evil.svg`;
  // extra projects: a hostile slug (must be dropped) and one with nothing but hostile strings
  r.projects.push({ slug: '../../x"><img src=x onerror=window.__pwned=1>', name: P, status: 'added' });
  r.projects.push({ slug: 'hostile', name: P, path: P, status: P, summary: { sheets_changed: P, components: P }, schematic: { sheets: P }, pcb: { layers: [P], changes: [P, { bbox_mm: [P, 1, 2, 3], kind: P }] }, bom: { rows: [P, { refs: P, status: P }] }, netlist: { changes: [{ net: P, added: [P] }] }, checks: { erc: { new: [{ description: P, pos_mm: [JS, 1] }] }, drc: P }, errors: [P] });
  fs.writeFileSync(f, JSON.stringify(r));
  return r;
}

function views(review) {
  const out = ['#/', '#/p/hostile', `#/p/${encodeURIComponent('<img src=x onerror=window.__pwned=1>')}`];
  for (const p of review.projects) {
    const h = `#/p/${encodeURIComponent(p.slug)}`;
    for (const t of ['schematic', 'layout', 'bom', 'netlist', 'checks']) out.push(`${h}/${t}`);
    for (const s of p.schematic?.sheets || []) {
      if (s && typeof s === 'object') for (const m of ['side', 'diff']) out.push(`${h}/schematic/${encodeURIComponent(s.id)}?mode=${m}&c=0`);
    }
    out.push(`${h}/layout?view=layers&mode=side&c=0`, `${h}/layout?view=top&mode=diff`, `${h}/bom?q=${encodeURIComponent(P)}`);
  }
  return out;
}

async function checkPages(base, list, tag) {
  const problems = [];
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('dialog', (d) => { problems.push(`${tag}: dialog opened: ${d.message()}`); d.dismiss(); });
  for (const hash of list) {
    await page.goto('about:blank');
    await page.goto(base + hash);
    await settle(page, 8000);
    const r = await page.evaluate(() => ({
      pwned: !!window.__pwned,
      injected: [...document.querySelectorAll('[onerror], [onload], img[src="x"], iframe, object, embed')].map((e) => e.outerHTML.slice(0, 80)),
      scripts: [...document.scripts].map((s) => s.getAttribute('src')).filter((s) => !/^(js\/(boot|app|bundle)\.js|data\.js|offline\/[a-z0-9_-]+\.js|pcba3d\/pcba3d\.bundle\.js)$/.test(s || '')),
      links: [...document.querySelectorAll('a[href], img[src], [href]:not(link[rel=icon]), [src]')].map((e) => e.getAttribute('href') || e.getAttribute('src'))
        .filter((u) => /^\s*(javascript|data|vbscript):/i.test(u || '') || /\.\.\/|secret\.txt|^\/etc/.test(u || '')),
      title: document.title,
    }));
    if (r.pwned) problems.push(`${tag} ${hash}: script ran`);
    if (r.injected.length) problems.push(`${tag} ${hash}: injected elements ${r.injected.join(' | ')}`);
    if (r.scripts.length) problems.push(`${tag} ${hash}: unexpected scripts ${r.scripts.join(', ')}`);
    if (r.links.length) problems.push(`${tag} ${hash}: unsafe URLs ${r.links.join(', ')}`);
  }
  await browser.close();
  return problems;
}

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'kipr-xss-'));
fs.cpSync(path.resolve(a.site), work, { recursive: true, dereference: true });
const review = poison(work);
const py = (mod, ...rest) => execFileSync('python3', ['-m', mod, '--out', work, ...rest], { cwd: ROOT, stdio: 'pipe' });
py('kipr.project.site');
py('kipr.project.report');

const problems = [];
// file:// support data must be escaped and confined
const blobs = [fs.readFileSync(path.join(work, 'data.js'), 'utf8'), ...(fs.existsSync(path.join(work, 'offline'))
  ? fs.readdirSync(path.join(work, 'offline')).map((f) => fs.readFileSync(path.join(work, 'offline', f), 'utf8')) : [])];
for (const b of blobs) {
  if (/<\/script/i.test(b) || /<img/i.test(b)) problems.push('data.js / offline pack contains raw markup');
  if (b.includes(SECRET)) problems.push('data.js / offline pack contains a file from outside p/<slug>/');
}
// the report: no markup from the payloads, no scripts, no unsafe links
const rep = fs.readFileSync(path.join(work, 'project-review.html'), 'utf8');
if (/<script/i.test(rep)) problems.push('report contains <script');
if (/href="\s*(javascript|data):/i.test(rep)) problems.push('report contains a javascript:/data: link');
if (rep.includes(SECRET)) problems.push('report embeds a file from outside the project');

const list = views(review);
let server = null;
if (a.mode === 'http' || a.mode === 'both') {
  const s = await serve(work);
  server = s.server;
  problems.push(...await checkPages(s.base, list, 'http'));
}
if (a.mode === 'file' || a.mode === 'both') problems.push(...await checkPages(`file://${work}/index.html`, list, 'file'));
// the report, as a browser parses it: no injected elements or handlers, no script ran
{
  const browser = await launch();
  const page = await browser.newPage();
  await page.goto(`file://${work}/project-review.html`);
  const r = await page.evaluate(() => ({
    pwned: !!window.__pwned,
    injected: [...document.querySelectorAll('*')].filter((e) => [...e.attributes].some((x) => /^on/i.test(x.name)) || ['SCRIPT', 'IFRAME', 'OBJECT', 'EMBED'].includes(e.tagName)).map((e) => e.outerHTML.slice(0, 80)),
    imgs: [...document.images].map((i) => i.getAttribute('src')).filter((s) => !/^data:image\/(png|svg\+xml);base64,/.test(s || '')),
  }));
  await browser.close();
  if (r.pwned) problems.push('report: script ran');
  if (r.injected.length) problems.push(`report: injected elements ${r.injected.join(' | ')}`);
  if (r.imgs.length) problems.push(`report: non-data images ${r.imgs.join(', ')}`);
}
server?.close();
fs.rmSync(work, { recursive: true, force: true });
console.log(`xss_check: ${list.length} views x ${a.mode}`);
if (problems.length) {
  console.error(`xss_check: FAIL\n  ${[...new Set(problems)].join('\n  ')}`);
  process.exit(1);
}
console.log('xss_check: OK');
