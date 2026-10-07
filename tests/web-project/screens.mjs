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
  ['sch-grid-at', `${P}/schematic/root?mode=side&at=133.5,76.5,14,11`],
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
      if (hasPcba3d && (name === 'pcba3d' || name.endsWith('-pcba3d'))) {
        // the 3D viewer: wait until it has loaded both boards, and fail on a placeholder or viewer errors
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
    // a missing font: one notice on the overview and the project, a badge on the font-dependent DRC row
    for (const hash of ['#/', `${P}/checks`]) {
      await page.goto(base + hash);
      await settle(page);
      const n = await page.locator('.font-warning').count();
      if (n !== 1) problems.push(`${tag} ${hash}: ${n} font warnings, expected 1`);
    }
    if (await page.locator('.badge.sev-font-dependent').count() !== 1) problems.push(`${tag}: no font-dependent badge on the DRC row`);
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
    const pane = page.locator('.bd2-pane').first();
    await pane.scrollIntoViewIfNeeded();
    const bb = await pane.boundingBox();
    if (bb) {
      await page.mouse.click(bb.x + bb.width * 0.3, bb.y + bb.height * 0.4);
      await page.mouse.click(bb.x + bb.width * 0.6, bb.y + bb.height * 0.6);
      const txt = await page.locator('.readout.measure').textContent();
      if (!/d \d+\.\d+ mm/.test(txt || '')) problems.push(`${tag}: measure tool shows "${txt}"`);
      await page.screenshot({ path: path.join(a.shots, `pcb-measure.${theme}.${sizeName}.png`) });
    }
    await checkBoxes(page, tag, `${theme}.${sizeName}`);
    await checkSmart(page, tag, `${theme}.${sizeName}`);
    if (a.mode !== 'file') await checkDocLayer(page, tag, `${theme}.${sizeName}`);
    await ctx.close();
  }
}
/**
 * A fab note on Dwgs.User 50 mm outside the board (make_mock NOTE_*): off by default and outside the
 * board frame; once the layer is ticked the frame covers it and its strokes are drawn (pixels in the
 * head canvas); the per-layer diff of Dwgs.User frames it too and shows the added line.
 */
async function checkDocLayer(page, tag, suffix) {
  const NOTE = { x: 210, y: 80, w: 30, h: 14 };
  const world = () => page.locator('.stage-wrap').getAttribute('data-world').then((v) => (v || '0,0,0,0').split(',').map(Number));
  const covers = ([x, y, w, h]) => x <= NOTE.x && y <= NOTE.y && x + w >= NOTE.x + NOTE.w && y + h >= NOTE.y + NOTE.h;
  // max alpha of `sel`'s canvas in a 1.5 mm square around KiCad point (x, y); the canvas (view2d's
  // whole-frame tile) spans the frame
  const inkAt = (sel, x, y) => world().then((wb) => page.evaluate(([sel, wb, x, y]) => {
    const c = document.querySelector(sel);
    if (!c) return -1;
    const [wx, wy, ww, wh] = wb;
    const sx = c.width / ww; const sy = c.height / wh;
    const px = Math.round((x - wx) * sx); const py = Math.round((y - wy) * sy);
    const r = Math.max(2, Math.round(0.75 * sx));
    const d = c.getContext('2d').getImageData(Math.max(0, px - r), Math.max(0, py - r), 2 * r, 2 * r).data;
    let m = 0;
    for (let i = 3; i < d.length; i += 4) m = Math.max(m, d[i]);
    return m;
  }, [sel, wb, x, y]));
  await page.goto('about:blank');
  await page.goto(base + `${P}/layout?view=layers&mode=side`);
  await settle(page);
  if (covers(await world())) problems.push(`${tag} doc layer: the frame covers the note with Dwgs.User off (${await world()})`);
  await page.locator('#ly-Dwgs_User').check();
  await settle(page);
  await page.waitForTimeout(500);
  if (!covers(await world())) problems.push(`${tag} doc layer: frame ${await world()} does not cover the note after ticking Dwgs.User`);
  const ink = await inkAt('.bd2-pane[data-side="head"] .bd2-slot canvas', NOTE.x + 15, NOTE.y);
  if (!(ink > 64)) problems.push(`${tag} doc layer: no note pixels in the head pane (alpha ${ink})`);
  await page.screenshot({ path: path.join(a.shots, `doc-layer-on.${suffix}.png`) });
  await page.locator('#ly-Dwgs_User').uncheck(); // back to the default for the next run
  await settle(page);
  if (covers(await world())) problems.push(`${tag} doc layer: frame still covers the note after unticking`);
  await page.goto('about:blank');
  await page.goto(base + `${P}/layout/Dwgs.User?view=layers&mode=diff`);
  await settle(page);
  await page.waitForTimeout(500);
  if (!covers(await world())) problems.push(`${tag} doc layer diff: frame ${await world()} does not cover the note`);
  const added = await inkAt('.bd2-slot:not(.bd2-underlay) canvas', NOTE.x + 15, NOTE.y + 12);
  if (!(added > 64)) problems.push(`${tag} doc layer diff: the added note line is not drawn (alpha ${added})`);
  if (!/Dwgs\.User: [1-9]\d* changed area/.test(await page.locator('.legend').textContent())) problems.push(`${tag} doc layer diff: no changed area found`);
  await page.screenshot({ path: path.join(a.shots, `doc-layer-diff.${suffix}.png`) });
}

/**
 * The Boxes toggle (key b): change boxes on / off in every compare mode of the schematic and layout,
 * kept in the URL (boxes=0) and across reloads (localStorage), a selected change still flashes its
 * outline for about a second, and the 3D Markers checkbox follows.
 */
async function checkBoxes(page, tag, suffix) {
  const marks = () => page.locator('svg.bd2-overlay .mark').count();
  const shot = (name) => page.screenshot({ path: path.join(a.shots, `${name}.${suffix}.png`) });
  await page.goto(base + `${P}/schematic/root?mode=side`);
  await settle(page);
  if (!(await marks())) problems.push(`${tag} boxes: no change boxes by default`);
  if (await page.locator('.boxes-toggle[aria-pressed="true"]').count() !== 1) problems.push(`${tag} boxes: toggle not pressed by default`);
  await shot('boxes-on-sch');
  await page.keyboard.press('b');
  await page.waitForTimeout(100);
  if (await marks()) problems.push(`${tag} boxes: 'b' left ${await marks()} boxes`);
  if (!page.url().includes('boxes=0')) problems.push(`${tag} boxes: hidden but not in the URL (${page.url()})`);
  await shot('boxes-off-sch');
  // a change in the list still zooms there and flashes its outline, which goes away
  const before = await page.locator('.readout.zoom').textContent();
  await page.locator('.change').first().click();
  await page.waitForTimeout(150);
  if (await page.locator('svg.bd2-overlay .hl.flash').count() < 1) problems.push(`${tag} boxes: no flash on a selected change`);
  if ((await page.locator('.readout.zoom').textContent()) === before) problems.push(`${tag} boxes: selecting a change did not zoom`);
  await shot('boxes-off-flash');
  await page.waitForTimeout(1300);
  if (await page.locator('svg.bd2-overlay .hl').count()) problems.push(`${tag} boxes: the flash outline stayed`);
  // every compare mode, schematic and layout, plus a head-only project: still hidden (localStorage, no URL param)
  for (const hash of [`${P}/schematic/root?mode=diff`, `${P}/schematic/root?mode=onion`, `${P}/schematic/root?mode=swipe`,
    `${P}/layout?view=top&mode=side`, `${P}/layout/F.Cu?view=top&mode=diff`, `${P}/layout?view=layers&mode=onion`, `${P}/layout?view=top&mode=swipe`,
    '#/p/sensor_breakout/layout?view=top&mode=single', '#/p/sensor_breakout/schematic']) {
    await page.goto('about:blank');
    await page.goto(base + hash);
    await settle(page);
    if (await marks()) problems.push(`${tag} boxes: ${await marks()} boxes while hidden at ${hash}`);
    if (!page.url().includes('boxes=0')) problems.push(`${tag} boxes: no boxes=0 in the URL at ${hash}`);
  }
  await shot('boxes-off-pcb');
  if (hasPcba3d) {
    await page.goto(base + `${P}/pcba3d`);
    await page.waitForFunction(() => document.querySelector('.pcba3d-host')?.dataset.ready !== undefined, null, { timeout: 180000 }).catch(() => {});
    if (await page.locator('input[data-toggle="markers"]').isChecked()) problems.push(`${tag} boxes: 3D Markers still on while boxes are hidden`);
    await page.locator('input[data-toggle="markers"]').click(); // turning Markers on shows the boxes again
  } else {
    await page.goto(base + `${P}/layout?view=top&mode=side`);
    await settle(page);
    await page.keyboard.press('b');
  }
  await page.goto('about:blank');
  await page.goto(base + `${P}/layout?view=top&mode=side`);
  await settle(page);
  if (!(await marks())) problems.push(`${tag} boxes: not back on after turning them on again`);
  if (page.url().includes('boxes=')) problems.push(`${tag} boxes: shown but the URL has ${page.url()}`);
  await shot('boxes-on-pcb');
  // a deep link with boxes=0 hides them in a fresh browser (no stored choice)
  const ctx2 = await browser.newContext({ viewport: page.viewportSize() });
  const p2 = await ctx2.newPage();
  await p2.goto(base + `${P}/layout?view=top&mode=side&boxes=0`);
  await settle(p2);
  if (await p2.locator('svg.bd2-overlay .mark').count()) problems.push(`${tag} boxes: boxes=0 deep link shows boxes`);
  await ctx2.close();
}

/**
 * The schematic's smart diff (default, key s / the move-arrows button): moved items with the same
 * connections are a collapsed "moved" group with faint outlines and washed out of the ink diff; raw
 * lists them as changes. Remembered (localStorage), smart=0 in the URL, and the counts follow.
 */
async function checkSmart(page, tag, suffix) {
  const shot = (name) => page.screenshot({ path: path.join(a.shots, `${name}.${suffix}.png`) });
  const listed = () => page.locator('ol.change-list > li > .change').count();
  const sheetCount = (title) => page.locator('.side-item', { hasText: title }).locator('.count').textContent().catch(() => '');
  await page.goto('about:blank');
  await page.goto(base + `${P}/schematic/root?mode=diff`);
  await settle(page);
  if (await page.locator('.smart-toggle[aria-pressed="true"]').count() !== 1) problems.push(`${tag} smart: toggle not on by default`);
  if (await listed() !== 3) problems.push(`${tag} smart: ${await listed()} changes listed, expected 3 (moved ones grouped)`);
  if (!(await page.locator('.minor-group summary', { hasText: '3 moved' }).count())) problems.push(`${tag} smart: no "3 moved" group`);
  if (await page.locator('svg.bd2-overlay .mark.moved').count() !== 3) problems.push(`${tag} smart: expected 3 faint outlines`);
  if (await page.locator('svg.bd2-overlay .quiet-wash').count() !== 1) problems.push(`${tag} smart: no wash over the moved items in the diff`);
  if (!(await page.locator('.side-item', { hasText: 'Mounting' }).locator('.badge.quiet-moved').count())) problems.push(`${tag} smart: moved-only sheet not marked`);
  if ((await page.locator('.tab[data-tab="schematic"] .tab-count').textContent()) !== '4') problems.push(`${tag} smart: schematic tab count is not 4`);
  await shot('smart-sch-diff');
  await page.keyboard.press('s');
  await page.waitForTimeout(150);
  if (await listed() !== 6) problems.push(`${tag} raw: ${await listed()} changes listed, expected 6`);
  if (await page.locator('svg.bd2-overlay .mark.moved').count()) problems.push(`${tag} raw: faint outlines left`);
  if (await page.locator('svg.bd2-overlay .quiet-wash').count()) problems.push(`${tag} raw: wash left`);
  if (!page.url().includes('smart=0')) problems.push(`${tag} raw: smart=0 not in the URL (${page.url()})`);
  if ((await page.locator('.tab[data-tab="schematic"] .tab-count').textContent()) !== '5') problems.push(`${tag} raw: schematic tab count is not 5`);
  if ((await sheetCount('Mounting')) !== '2') problems.push(`${tag} raw: Mounting sheet count is not 2`);
  await shot('raw-sch-diff');
  // remembered across a reload without the URL param; a smart=1 link wins for that visit
  await page.goto('about:blank');
  await page.goto(base + `${P}/schematic/root?mode=side`);
  await settle(page);
  if (await page.locator('.smart-toggle[aria-pressed="false"]').count() !== 1) problems.push(`${tag} raw: not remembered`);
  await page.goto('about:blank');
  await page.goto(base + `${P}/schematic/root?mode=side&smart=1`);
  await settle(page);
  if (await listed() !== 3) problems.push(`${tag} smart=1 link: ${await listed()} changes listed`);
  await page.locator('.smart-toggle').click(); // the button: raw
  await page.waitForTimeout(100);
  await page.locator('.smart-toggle').click(); // and back to smart (remembered for the next checks)
  await page.waitForTimeout(100);
  if (await listed() !== 3 || page.url().includes('smart=')) problems.push(`${tag} smart: button did not switch back (${page.url()})`);
}

await browser.close();
server?.close();
console.log(`screens: ${shots.length} screenshots in ${a.shots}`);
if (problems.length) {
  console.error(`screens: ${problems.length} problem(s):\n  ${[...new Set(problems)].join('\n  ')}`);
  process.exit(1);
}
console.log('screens: OK');
