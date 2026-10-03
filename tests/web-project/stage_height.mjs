// The diff stages (schematic, layout, 3D PCBA) fill the viewport below the header and toolbars.
//
//   node stage_height.mjs --site OUT --shots DIR
//
// At 1920x1080 and 2560x1440 the stage must be at least 80% of (viewport height - its top), the page
// (#main) must not scroll and the side / change columns must end inside the viewport (they scroll on
// their own). Shrinking the window shrinks the stage and re-fits it (the px/mm readout changes). At
// 390x844 (phone) the stage is a usable block (>= 300 px, at most the viewport) and the page scrolls.
// One screenshot per view and size goes to --shots.
import fs from 'node:fs';
import path from 'node:path';
import { serve, launch, settle, args } from './harness.mjs';

const a = args(process.argv.slice(2), { shots: 'shots-stage' });
if (!a.site) { console.error('usage: node stage_height.mjs --site OUT --shots DIR'); process.exit(2); }
fs.mkdirSync(a.shots, { recursive: true });
const hasPcba3d = fs.existsSync(path.join(a.site, 'pcba3d', 'index.js'));

const P = '#/p/demo_board';
const VIEWS = [
  ['sch-side', `${P}/schematic/root?mode=side`, '.stage-wrap'],
  ['sch-diff', `${P}/schematic/root?mode=diff`, '.stage-wrap'],
  ['pcb-top', `${P}/layout?view=top&mode=side`, '.stage-wrap'],
  ['pcb-layers', `${P}/layout?view=layers&mode=onion`, '.stage-wrap'],
  ...(hasPcba3d ? [['pcba3d', `${P}/pcba3d`, '.pcba3d-host']] : []),
];
const SIZES = [['1920x1080', 1920, 1080, true], ['2560x1440', 2560, 1440, true], ['390x844', 390, 844, false]];

const measure = (sel) => {
  const m = document.querySelector('#main');
  m.scrollTop = 0;
  const s = document.querySelector(sel);
  if (!s) return null;
  const r = s.getBoundingClientRect();
  const bottoms = [...document.querySelectorAll('.side-col, .diff-changes')].map((e) => e.getBoundingClientRect().bottom);
  return {
    top: r.top, h: r.height, w: r.width, vh: innerHeight, vw: innerWidth,
    mainScrolls: m.scrollHeight > m.clientHeight + 1,
    colBottom: bottoms.length ? Math.max(...bottoms) : 0,
    zoom: document.querySelector('.readout.zoom')?.textContent || '',
    docW: document.documentElement.scrollWidth,
  };
};

const { server, base } = await serve(a.site);
const browser = await launch();
const problems = [];
let n = 0;
for (const [size, width, height, desktop] of SIZES) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => problems.push(`${size} pageerror: ${e.message}`));
  for (const [name, hash, sel] of VIEWS) {
    const tag = `${size} ${name}`;
    await page.setViewportSize({ width, height });
    await page.goto('about:blank');
    await page.goto(base + hash);
    await settle(page);
    if (sel === '.pcba3d-host') await page.waitForFunction(() => document.querySelector('.pcba3d-host')?.dataset.ready !== undefined, null, { timeout: 180000 }).catch(() => {});
    const m = await page.evaluate(measure, sel);
    await page.screenshot({ path: path.join(a.shots, `${name}.${size}.png`), fullPage: !desktop });
    n++;
    if (!m) { problems.push(`${tag}: no ${sel}`); continue; }
    const info = `top ${m.top.toFixed(0)} h ${m.h.toFixed(0)} vh ${m.vh}`;
    console.log(`${tag}: ${info}${m.mainScrolls ? ' (main scrolls)' : ''}`);
    if (m.docW > m.vw + 1) problems.push(`${tag}: page scrolls sideways (${m.docW} > ${m.vw})`);
    if (desktop) {
      if (m.h < 0.8 * (m.vh - m.top)) problems.push(`${tag}: stage ${info}: below 80% of the space under it`);
      if (m.mainScrolls) problems.push(`${tag}: the page scrolls`);
      if (m.colBottom > m.vh + 1) problems.push(`${tag}: side/change column ends below the viewport (${m.colBottom.toFixed(0)})`);
      if (name === 'sch-side') {
        // a smaller window: the stage follows and re-fits (the zoom readout changes)
        await page.setViewportSize({ width, height: Math.round(height * 0.6) });
        await page.waitForTimeout(400);
        const s = await page.evaluate(measure, sel);
        if (!(s.h < m.h - 50)) problems.push(`${tag}: stage did not shrink with the window (${m.h} -> ${s.h})`);
        if (s.h < 0.8 * (s.vh - s.top) && s.h > 360) problems.push(`${tag}: shrunk stage below 80% (${s.h} of ${s.vh - s.top})`);
        if (!s.zoom || s.zoom === m.zoom) problems.push(`${tag}: no re-fit on resize (zoom ${m.zoom} -> ${s.zoom})`);
        await page.screenshot({ path: path.join(a.shots, `${name}-resized.${size}.png`) });
      }
    } else {
      if (m.h < 300 || m.h > m.vh) problems.push(`${tag}: stage ${info}: not a usable phone height`);
      if (m.w > m.vw) problems.push(`${tag}: stage wider than the screen (${m.w})`);
    }
  }
  await ctx.close();
}
await browser.close();
server.close();
console.log(`stage_height: ${n} screenshots in ${a.shots}`);
if (problems.length) {
  console.error(`stage_height: ${problems.length} problem(s):\n  ${problems.join('\n  ')}`);
  process.exit(1);
}
console.log('stage_height: OK');
