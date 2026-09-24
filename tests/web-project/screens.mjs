// Smoke test + screenshots of every view of the project viewer.
//
//   node screens.mjs --site OUT --shots DIR [--mode http|file] [--quick]
//
// Opens each view (overview, every schematic mode, every layout view/mode, change selection, tables,
// 3D mount, help) in light + dark and desktop + narrow, fails (exit 1) on page errors, console errors
// or failed requests, and writes one PNG per view/theme/size into --shots.
import fs from 'node:fs';
import path from 'node:path';
import { serve, launch, settle, args } from './harness.mjs';

const a = args(process.argv.slice(2), { mode: 'http', shots: 'shots' });
if (!a.site) { console.error('usage: node screens.mjs --site OUT --shots DIR [--mode http|file] [--quick]'); process.exit(2); }
fs.mkdirSync(a.shots, { recursive: true });
const hasPcba3d = fs.existsSync(path.join(a.site, 'pcba3d', 'index.js'));

const P = '#/p/demo_board';
const MOCK_VIEWS = [
  ['sch-side', `${P}/schematic/root?mode=side`],
  ['sch-diff', `${P}/schematic/root?mode=diff`],
  ['sch-onion', `${P}/schematic/root?mode=onion`],
  ['sch-swipe', `${P}/schematic/root?mode=swipe`],
  ['sch-change', `${P}/schematic/root?mode=side&c=0`],
  ['sch-power-diff', `${P}/schematic/root%2Fpower?mode=diff&c=0`],
  ['sch-added', `${P}/schematic/root%2Fsensors`],
  ['sch-removed', `${P}/schematic/root%2Flegacy?mode=diff`],
  ['pcb-top-side', `${P}/layout?view=top&mode=side`],
  ['pcb-bottom-side', `${P}/layout?view=bottom&mode=side`],
  ['pcb-layers-onion', `${P}/layout?view=layers&mode=onion`],
  ['pcb-top-swipe', `${P}/layout?view=top&mode=swipe`],
  ['pcb-bottom-swipe', `${P}/layout?view=bottom&mode=swipe`],
  ['pcb-diff-fcu', `${P}/layout/F.Cu?view=top&mode=diff`],
  ['pcb-diff-bcu', `${P}/layout/B.Cu?view=bottom&mode=diff`],
  ['pcb-change', `${P}/layout?view=top&mode=side&c=0`],
  ['pcb-drc-at', `${P}/layout?view=top&mode=side&at=135.3,91.6`],
  ['bom', `${P}/bom`],
  ['bom-filter', `${P}/bom?q=4.7k&st=changed`],
  ['netlist', `${P}/netlist`],
  ['checks', `${P}/checks`],
  ['pcba3d', `${P}/pcba3d`],
  ['added-layout', '#/p/sensor_breakout/layout?view=top&mode=single'],
  ['removed-sch', '#/p/old_adapter/schematic'],
  ['unknown', '#/p/nope'],
];

/** Views for any OUT: per project, the first changed sheet (side + diff), layout (top, diff, first change), tables. */
function autoViews(review) {
  const v = [];
  for (const p of review.projects || []) {
    const h = `#/p/${encodeURIComponent(p.slug)}`;
    const sheets = p.schematic?.sheets || [];
    const sh = sheets.find((s) => s.status !== 'unchanged') || sheets[0];
    if (sh) {
      const item = encodeURIComponent(sh.id);
      v.push([`${p.slug}-sch-side`, `${h}/schematic/${item}?mode=side`], [`${p.slug}-sch-diff`, `${h}/schematic/${item}?mode=diff`]);
      if ((sh.changes || []).length) v.push([`${p.slug}-sch-change`, `${h}/schematic/${item}?mode=diff&c=0`]);
    }
    if (p.pcb) {
      v.push([`${p.slug}-pcb-top`, `${h}/layout?view=top&mode=side`], [`${p.slug}-pcb-bottom`, `${h}/layout?view=bottom&mode=side`],
        [`${p.slug}-pcb-layers`, `${h}/layout?view=layers&mode=onion`], [`${p.slug}-pcb-diff`, `${h}/layout/F.Cu?view=top&mode=diff`]);
      if ((p.pcb.changes || []).length) v.push([`${p.slug}-pcb-change`, `${h}/layout?view=top&mode=side&c=0`]);
    }
    if (hasPcba3d && (p.pcba3d?.base?.glb || p.pcba3d?.head?.glb)) v.push([`${p.slug}-pcba3d`, `${h}/pcba3d`]);
    v.push([`${p.slug}-bom`, `${h}/bom`], [`${p.slug}-netlist`, `${h}/netlist`], [`${p.slug}-checks`, `${h}/checks`]);
  }
  return v;
}
const review = JSON.parse(fs.readFileSync(path.join(a.site, 'project-review.json'), 'utf8'));
const isMock = (review.projects || []).some((p) => p.slug === 'demo_board');
const VIEWS = [['overview', '#/'], ...(isMock ? MOCK_VIEWS : autoViews(review))];
const THEMES = a.quick ? ['light'] : ['light', 'dark'];
const SIZES = a.quick ? [['desktop', 1440, 900]] : [['desktop', 1440, 900], ['narrow', 390, 844]];

let server = null;
let base;
if (a.mode === 'file') base = `file://${path.resolve(a.site)}/index.html`;
else ({ server, base } = await serve(a.site));

const browser = await launch();
const problems = [];
const shots = [];
for (const theme of THEMES) {
  for (const [sizeName, width, height] of SIZES) {
    const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: theme, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const tag = `${theme}/${sizeName}`;
    page.on('pageerror', (e) => problems.push(`${tag} pageerror: ${e.message}`));
    page.on('console', (m) => {
      if (m.type() !== 'error') return;
      const t = m.text();
      if (!hasPcba3d && /pcba3d|404/.test(t)) return; // 3D module not part of this build
      problems.push(`${tag} console: ${t}`);
    });
    page.on('requestfailed', (r) => { if (!(!hasPcba3d && r.url().includes('/pcba3d/'))) problems.push(`${tag} requestfailed: ${r.url()} ${r.failure()?.errorText}`); });
    page.on('response', (r) => {
      if (r.status() >= 400 && !(!hasPcba3d && r.url().includes('/pcba3d/')) && !r.url().endsWith('.glb')) problems.push(`${tag} HTTP ${r.status()} ${r.url()}`);
    });
    for (const [name, hash] of VIEWS) {
      await page.goto('about:blank');
      await page.goto(base + hash);
      await settle(page);
      if (!isMock && name.endsWith('-pcba3d')) {
        // the real 3D viewer: wait until it has loaded both boards, and fail on a placeholder or viewer errors
        const state = await page.waitForFunction(() => {
          const host = document.querySelector('.pcba3d-host');
          if (!host) return null;
          if (host.dataset.ready !== undefined) return host.dataset.ready;
          return host.querySelector(':scope > .empty') ? `placeholder: ${host.textContent.trim()}` : null;
        }, null, { timeout: 180000 }).then((h) => h.jsonValue()).catch(() => 'timeout');
        if (state !== '0') problems.push(`${tag} ${name}: 3D view not ready (${state})`);
        await page.waitForTimeout(500);
      }
      const file = path.join(a.shots, `${name}.${theme}.${sizeName}.png`);
      await page.screenshot({ path: file, fullPage: sizeName === 'narrow' });
      shots.push(file);
    }
    if (!isMock) { await ctx.close(); continue; }
    // interactions: next change, help overlay, measure tool
    await page.goto(base + `${P}/schematic/root?mode=side`);
    await settle(page);
    await page.keyboard.press('n');
    await page.keyboard.press('n');
    await settle(page);
    if (!page.url().includes('c=1')) problems.push(`${tag}: 'n' twice did not select change 1 (${page.url()})`);
    await page.keyboard.press('?');
    await page.screenshot({ path: path.join(a.shots, `help.${theme}.${sizeName}.png`) });
    await page.keyboard.press('Escape');
    await page.goto(base + `${P}/layout?view=top&mode=side`);
    await settle(page);
    await page.keyboard.press('r');
    const pane = page.locator('.pane').first();
    await pane.scrollIntoViewIfNeeded();
    const bb = await pane.boundingBox();
    if (bb) {
      await page.mouse.click(bb.x + bb.width * 0.3, bb.y + bb.height * 0.4);
      await page.mouse.click(bb.x + bb.width * 0.6, bb.y + bb.height * 0.6);
      const txt = await page.locator('.readout.measure').textContent();
      if (!/d \d+\.\d+ mm/.test(txt || '')) problems.push(`${tag}: measure tool shows "${txt}"`);
      await page.screenshot({ path: path.join(a.shots, `pcb-measure.${theme}.${sizeName}.png`) });
    }
    await ctx.close();
  }
}
await browser.close();
server?.close();
console.log(`screens: ${shots.length} screenshots in ${a.shots}`);
if (problems.length) {
  console.error(`screens: ${problems.length} problem(s):\n  ${[...new Set(problems)].join('\n  ')}`);
  process.exit(1);
}
console.log('screens: OK');
