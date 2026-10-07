// View state survives layer / sheet changes (and back / forward, reloads, tab switches).
//
//   node persist.mjs --site OUT [--shots DIR] [--mode http|file]      (OUT: make_mock.py --site)
//
// Layout: in swipe (slider at 30%), side by side and diff, zoomed in with the boxes hidden, clicking
// three layers in turn and stepping with [ / ] change only the selected layer: the compare mode, the
// slider, the zoom region (px/mm and transform), the Boxes toggle and the ticked layers stay, the URL
// names the new layer with the same mode / sw / z, and the panes show that layer (their pixels change
// per layer; base and head both drawn). Top / Bottom / Layers clear the selection (the board view, no
// layer in the URL) and a layer click selects one again. In diff a doc layer (Dwgs.User) reframes the
// world but keeps the region on show. Back / forward return to the previous layer with that state, a
// reload restores it, and a tab switch comes back to it. Schematic: the same for sheets (all A4 in the
// mock, so the zoom stays too), including a sheet that is only in head (mode kept, note shown).
// Arrows: ↑ / ↓ step the layer list front to back (no selection: from the top row), stop at the ends,
// work from a layer checkbox, are ignored in the filter input and with Shift, and do not scroll the page.
import fs from 'node:fs';
import path from 'node:path';
import { serve, launch, settle, args } from './harness.mjs';

const a = args(process.argv.slice(2), { shots: '', mode: 'http' });
if (!a.site) { console.error('usage: node persist.mjs --site OUT [--shots DIR]'); process.exit(2); }
if (a.shots) fs.mkdirSync(a.shots, { recursive: true });
const P = '#/p/demo_board';
const problems = [];
const check = (ok, msg) => { if (!ok) problems.push(msg); };

let server = null;
let base;
if (a.mode === 'file') base = `file://${path.resolve(a.site)}/index.html`; // the bundle + per-layer SVGs
else ({ server, base } = await serve(a.site));
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error' && !/pcba3d|404/.test(m.text())) problems.push(`console: ${m.text()}`); });
const shot = (name) => (a.shots ? page.screenshot({ path: path.join(a.shots, `${name}.png`) }) : null);

/** Everything that must not change when only the layer / sheet does. */
const snapshot = () => page.evaluate(() => {
  const h = location.hash;
  const q = new URLSearchParams(h.split('?')[1] || '');
  const item = decodeURIComponent((h.split('?')[0].split('/')[4]) || '');
  const slider = document.querySelector('.toolbar-extra input[type=range]');
  return {
    mode: document.querySelector('.seg[aria-label="Compare mode"] .seg-btn[aria-selected="true"]')?.dataset.mode,
    view: document.querySelector('.seg[aria-label="Board view"] .seg-btn[aria-selected="true"]')?.dataset.mode || null,
    slider: slider ? Number(slider.value) : null,
    zoom: document.querySelector('.readout.zoom')?.textContent,
    transform: document.querySelector('.stage-wrap')?.dataset.view,
    boxes: document.querySelector('.boxes-toggle')?.getAttribute('aria-pressed'),
    ticked: [...document.querySelectorAll('.layer-list input[type=checkbox]')].map((c) => c.checked).join(''),
    marks: document.querySelectorAll('svg.bd2-overlay .mark').length,
    item, hashMode: q.get('mode'), sw: q.get('sw'), z: q.get('z'), boxesParam: q.get('boxes'),
    selected: document.querySelector('.layer-row.focus .layer-name, .side-item.active .side-name')?.textContent || null,
    note: [...document.querySelectorAll('.layer-note, .sheet-note')].filter((n) => !n.hidden).map((n) => n.textContent).join(' '),
  };
});

/** What the panes draw: a checksum of every layer canvas (whole bitmap, not just the visible part). */
const pixels = () => page.evaluate(() => {
  let h = 0;
  let n = 0;
  for (const c of document.querySelectorAll('.bd2-pane .bd2-slot canvas')) {
    if (!c.width || !c.height) continue;
    n++;
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    const step = Math.max(4, Math.floor(d.length / 200000) * 4);
    for (let i = 0; i < d.length; i += step) h = (h * 31 + d[i] + 3 * d[i + 1] + 7 * d[i + 2] + 11 * d[i + 3]) % 2147483647;
    h = (h * 31 + c.width * 7 + c.height) % 2147483647;
  }
  return { h, n, sides: [...document.querySelectorAll('.bd2-pane .bd2-slot')].map((x) => x.closest('.bd2-pane').dataset.side).sort().join(',') };
});

/** Clicking / stepping to another layer redrew the panes with it (a different picture, both sides). */
async function redrawn(tag, before, id) {
  const now = await pixels();
  check(now.h !== before.h, `${tag}: the panes look the same as before (not redrawn for ${id})`);
  const both = await page.evaluate(() => [...document.querySelectorAll('.bd2-pane .bd2-slot')].length);
  check(both === 2, `${tag}: ${both} layer holders on show, expected base + head`);
  return now;
}
const parseZ = (z) => (z ? z.split(',').map(Number) : null);
const closeZ = (p, q) => !!p && !!q && Math.abs(p[0] - q[0]) <= p[2] * 0.01 && Math.abs(p[1] - q[1]) <= p[2] * 0.01 && Math.abs(p[2] - q[2]) <= p[2] * 0.01;

// pan/zoom transforms equal: exactly after a click / key; within URL rounding when restored from a link
const nums = (t) => (t || '').match(/-?\d+(\.\d+)?/g)?.map(Number) || [];
const closeT = (p, q) => { const [x0, y0, s0] = nums(p); const [x1, y1, s1] = nums(q); return Math.abs(s1 / s0 - 1) < 0.002 && Math.abs(x1 - x0) < 3 && Math.abs(y1 - y0) < 3; };
const closeZoom = (p, q) => Math.abs(parseFloat(p) / parseFloat(q) - 1) < 0.005;

/** s0 (before) vs s1 (after a layer / sheet change to `item`): only the item may differ. */
function same(tag, s0, s1, item, { transform = true, exact = true } = {}) {
  check(s1.mode === s0.mode, `${tag}: compare mode ${s0.mode} -> ${s1.mode}`);
  check(s1.view === s0.view, `${tag}: board view ${s0.view} -> ${s1.view}`);
  check(s1.slider === s0.slider, `${tag}: slider ${s0.slider} -> ${s1.slider}`);
  check(exact ? s1.zoom === s0.zoom : closeZoom(s1.zoom, s0.zoom), `${tag}: zoom ${s0.zoom} -> ${s1.zoom}`);
  if (transform) check(exact ? s1.transform === s0.transform : closeT(s0.transform, s1.transform), `${tag}: pan/zoom transform ${s0.transform} -> ${s1.transform}`);
  check(s1.boxes === s0.boxes && s1.boxesParam === s0.boxesParam, `${tag}: boxes ${s0.boxes}/${s0.boxesParam} -> ${s1.boxes}/${s1.boxesParam}`);
  check(s1.marks === 0 || s0.boxes === 'true', `${tag}: ${s1.marks} change boxes drawn while hidden`);
  check(s1.ticked === s0.ticked, `${tag}: ticked layers changed`);
  check(s1.item === item, `${tag}: URL item ${s1.item}, expected ${item}`);
  check(s1.selected === item || s1.selected?.length, `${tag}: selected ${s1.selected}`);
  check(s1.hashMode === s0.mode, `${tag}: URL mode=${s1.hashMode}, expected ${s0.mode}`);
  check(s1.sw === s0.sw, `${tag}: URL sw=${s1.sw}, expected ${s0.sw}`);
  check(closeZ(parseZ(s1.z), parseZ(s0.z)), `${tag}: URL z=${s1.z}, expected ${s0.z}`);
}

async function zoomIn(steps = 3) {
  const bb = await page.locator('.bd2-pane').first().boundingBox();
  await page.mouse.move(bb.x + bb.width * 0.45, bb.y + bb.height * 0.5);
  for (let i = 0; i < steps; i++) { await page.mouse.wheel(0, -300); await page.waitForTimeout(60); }
  await page.mouse.move(bb.x + 5, bb.y + 5);
  await page.waitForTimeout(400); // the URL's z= is written once the zoom settles
}
async function setSlider(v) {
  await page.locator('.toolbar-extra input[type=range]').evaluate((s, val) => { s.value = String(val); s.dispatchEvent(new Event('input', { bubbles: true })); }, v);
  await page.waitForTimeout(300);
}
async function pickMode(m) {
  await page.locator(`.seg[aria-label="Compare mode"] .seg-btn[data-mode="${m}"]`).click();
  await settle(page);
}
const clickLayer = (id) => page.locator('.layer-name', { hasText: new RegExp(`^${id.replace('.', '\\.')}$`) }).click();
async function stable() { await settle(page); await page.waitForTimeout(300); }

async function layout() {
  // --- layout -----------------------------------------------------------------------------------------
  await page.goto(`${base}${P}/layout/F.Cu?view=top&mode=swipe`);
  await stable();
  await setSlider(0.3);
  await zoomIn();
  await page.keyboard.press('b'); // boxes off
  await stable();
  let s0 = await snapshot();
  check(s0.mode === 'swipe' && s0.slider === 0.3 && s0.sw === '0.3' && s0.z && s0.boxes === 'false', `layout setup: ${JSON.stringify(s0)}`);
  check(s0.view === null, `layout setup: a layer is selected but the board view ${s0.view} is still marked`);
  let px = await pixels();
  for (const id of ['B.Cu', 'F.SilkS', 'Edge.Cuts']) {
    await clickLayer(id);
    await stable();
    const s1 = await snapshot();
    same(`layout swipe click ${id}`, s0, s1, id);
    px = await redrawn(`layout swipe click ${id}`, px, id);
    check(s1.selected === id, `layout swipe click ${id}: selected row is ${s1.selected}`);
    if (id === 'Edge.Cuts') check(/identical/.test(s1.note), `layout swipe on unchanged Edge.Cuts: no note (${s1.note})`);
    else check(!s1.note, `layout swipe click ${id}: unexpected note "${s1.note}"`);
  }
  await shot('layout-swipe-after-clicks');
  // [ / ] in list order (top of the stack first): from Edge.Cuts
  const order = await page.locator('.layer-name').allTextContents();
  let at = order.indexOf('Edge.Cuts');
  for (const key of [']', ']', '[']) {
    await page.keyboard.press(key);
    await stable();
    at = (at + (key === ']' ? 1 : -1) + order.length) % order.length;
    same(`layout swipe key ${key}`, s0, await snapshot(), order[at]);
    if (a.mode !== 'file') px = await redrawn(`layout swipe key ${key}`, px, order[at]); // drills have no SVG
  }

  // Top clears the selection: the realistic face, no layer in the URL, mode / slider / zoom kept; a
  // layer click selects one again
  await page.locator('.seg[aria-label="Board view"] .seg-btn[data-mode="top"]').click();
  await stable();
  let sb = await snapshot();
  check(sb.view === 'top' && sb.item === '' && sb.selected === null, `layout Top: view ${sb.view}, URL item "${sb.item}", selected ${sb.selected}`);
  check(sb.mode === s0.mode && sb.slider === s0.slider && sb.zoom === s0.zoom && sb.sw === s0.sw, `layout Top: mode / slider / zoom changed (${JSON.stringify(sb)})`);
  check((await pixels()).h !== px.h, 'layout Top: still the single layer on show');
  await clickLayer('B.Cu');
  await stable();
  sb = await snapshot();
  check(sb.view === null && sb.item === 'B.Cu' && sb.mode === 'swipe' && sb.zoom === s0.zoom, `layout re-select: ${JSON.stringify(sb)}`);
  px = await pixels();

  // side by side, zoomed elsewhere
  await pickMode('side');
  await zoomIn(2);
  s0 = await snapshot();
  check(s0.mode === 'side' && s0.hashMode === 'side', `layout side setup: ${s0.mode}`);
  px = await pixels();
  for (const id of ['F.Cu', 'B.Mask', 'F.Paste']) {
    await clickLayer(id);
    await stable();
    same(`layout side click ${id}`, s0, await snapshot(), id);
    px = await redrawn(`layout side click ${id}`, px, id);
    check(await page.locator('.bd2-pane').count() === 2, `layout side click ${id}: not two panes`);
  }
  for (const key of [']', '[', '[']) {
    const before = (await snapshot()).item;
    await page.keyboard.press(key);
    await stable();
    const s1 = await snapshot();
    check(s1.item !== before, `layout side key ${key}: layer did not change`);
    same(`layout side key ${key}`, s0, s1, s1.item);
    px = await redrawn(`layout side key ${key}`, px, s1.item);
  }

  // diff: re-rendered for each layer at the same region; an unchanged layer keeps diff with a note
  await clickLayer('F.Cu');
  await pickMode('diff');
  await stable();
  s0 = await snapshot();
  for (const id of ['B.Cu', 'B.SilkS', 'F.SilkS']) {
    await clickLayer(id);
    await stable();
    const s1 = await snapshot();
    same(`layout diff click ${id}`, s0, s1, id);
    check(await page.locator('.bd2-label', { hasText: `diff: ${id}` }).count() === 1, `layout diff click ${id}: the diff pane does not show ${id}`);
    if (id === 'B.SilkS') check(/identical/.test(s1.note), `layout diff on unchanged B.SilkS: no note (${s1.note})`);
  }
  await shot('layout-diff-after-clicks');
  // a doc layer reframes the world (data-world) but the same region stays on show
  const world0 = await page.locator('.stage-wrap').getAttribute('data-world');
  await clickLayer('Dwgs.User');
  await stable();
  const sDoc = await snapshot();
  same('layout diff doc layer', s0, sDoc, 'Dwgs.User', { transform: false });
  check(await page.locator('.stage-wrap').getAttribute('data-world') !== world0, 'layout diff doc layer: the frame did not grow for Dwgs.User');
  await clickLayer('F.SilkS');
  await stable();
  same('layout diff back from doc layer', s0, await snapshot(), 'F.SilkS');

  // back / forward: the previous layers, same mode and region; forward again
  await page.goBack();
  await stable();
  let s1 = await snapshot();
  check(s1.item === 'Dwgs.User' && s1.mode === 'diff' && closeZ(parseZ(s1.z), parseZ(s0.z)), `layout back: ${s1.item} ${s1.mode} z=${s1.z}`);
  await page.goBack();
  await stable();
  s1 = await snapshot();
  same('layout back x2', s0, s1, 'F.SilkS', { exact: false }); // the F.SilkS before Dwgs.User
  await page.goForward();
  await page.goForward();
  await stable();
  same('layout forward', s0, await snapshot(), 'F.SilkS', { exact: false });
  // reload (a deep link): the same view comes back
  await page.reload();
  await stable();
  s1 = await snapshot();
  same('layout reload', s0, s1, 'F.SilkS', { exact: false });
  s0 = s1;
  // tab switch and back (keys 1, 2): the layout comes back as it was left
  await page.keyboard.press('1');
  await stable();
  await page.keyboard.press('2');
  await stable();
  same('layout tab switch', s0, await snapshot(), 'F.SilkS', { exact: false });
}

async function schematic() {
  // --- schematic --------------------------------------------------------------------------------------
  // the compare mode and slider carry over from the layout tab to a first visit of the schematic tab
  await page.goto('about:blank');
  await page.goto(`${base}${P}/layout/F.Cu?view=top&mode=swipe&sw=0.3`);
  await stable();
  await page.keyboard.press('1');
  await stable();
  const sx = await snapshot();
  check(sx.mode === 'swipe' && sx.slider === 0.3 && sx.sw === '0.3', `layout -> schematic tab: mode ${sx.mode}, slider ${sx.slider}, sw=${sx.sw}`);
  await page.goto('about:blank');
  await page.goto(`${base}${P}/schematic/root?mode=swipe`);
  await stable();
  await setSlider(0.3);
  await zoomIn();
  await stable();
  const s0 = await snapshot();
  check(s0.mode === 'swipe' && s0.sw === '0.3' && s0.z, `schematic setup: ${JSON.stringify(s0)}`);
  const sheetIds = ['root/power', 'root/connectors', 'root/sensors'];
  for (const id of sheetIds) {
    await page.locator('.side-item', { has: page.locator(`.side-name[title="${id}"]`) }).click();
    await stable();
    const sh = await snapshot();
    same(`schematic click ${id}`, s0, sh, id);
    if (id === 'root/sensors') check(/only in head/.test(sh.note), `schematic added sheet: no note (${sh.note})`);
  }
  await shot('schematic-swipe-after-clicks');
  for (const [key, id] of [['[', 'root/connectors'], ['[', 'root/power'], [']', 'root/connectors']]) {
    await page.keyboard.press(key);
    await stable();
    same(`schematic key ${key}`, s0, await snapshot(), id);
  }
  await page.goBack();
  await stable();
  let s1 = await snapshot();
  same('schematic back', s0, s1, 'root/power', { exact: false });
  await pickMode('diff');
  const sd = await snapshot();
  await page.keyboard.press(']');
  await stable();
  s1 = await snapshot();
  same('schematic diff key ]', sd, s1, 'root/connectors');
  check(/identical/.test(s1.note), `schematic diff on unchanged sheet: no note (${s1.note})`);
}

async function arrows() {
  // --- ↑ / ↓: up / down the layer list, stopping at the ends; the selection, render and URL follow -------
  await page.goto(`${base}${P}/layout?view=top&mode=side`);
  await stable();
  // window listeners run after the viewer's (document) one: did it prevent the default (scrolling)?
  const probe = () => page.evaluate(() => {
    window.__prevented = null;
    if (!window.__probe) window.addEventListener('keydown', (e) => { if (e.key.startsWith('Arrow')) window.__prevented = e.defaultPrevented; });
    window.__probe = true;
  });
  await probe();
  const order = await page.locator('.layer-name').allTextContents();
  const scrolled = () => page.evaluate(() => (document.querySelector('#main')?.scrollTop || 0) + scrollY);
  const s0 = await snapshot();
  check(s0.selected === null && s0.item === '', `arrows setup: ${JSON.stringify(s0)}`);
  let px = await pixels();
  const press = async (key, want, tag = key) => {
    await page.keyboard.press(key);
    await stable();
    const s = await snapshot();
    check(s.item === want && s.selected === want, `arrows ${tag}: URL item ${s.item}, selected ${s.selected}, expected ${want}`);
    check(s.mode === 'side' && s.view === null, `arrows ${tag}: mode ${s.mode}, view ${s.view}`);
    return s;
  };
  await press('ArrowDown', order[0], 'first ArrowDown (nothing selected: the top row)');
  check(await page.evaluate(() => window.__prevented) === true, 'arrows: ArrowDown not prevented (the page scrolls)');
  check(await scrolled() === 0, 'arrows: the page scrolled');
  px = await redrawn('arrows ArrowDown', px, order[0]);
  await press('ArrowDown', order[1]);
  await press('ArrowDown', order[2]);
  px = await redrawn('arrows ArrowDown x2', px, order[2]);
  await press('ArrowUp', order[1]);
  await press('ArrowUp', order[0]);
  await press('ArrowUp', order[0], 'ArrowUp at the top (no wrap)');
  await page.goBack();
  await stable();
  check((await snapshot()).item === order[1], 'arrows: back does not return to the previous layer');
  await page.goForward();
  await stable();
  await clickLayer(order.at(-1));
  await stable();
  await press('ArrowDown', order.at(-1), 'ArrowDown at the bottom (no wrap)');
  await press('ArrowUp', order.at(-2));
  await press('Shift+ArrowUp', order.at(-2), 'Shift+ArrowUp (ignored)');
  // from a layer checkbox (focus stays on it after a tick) the arrows still step
  await page.locator('.layer-row input[type=checkbox]').nth(order.length - 1).click();
  await press('ArrowUp', order.at(-3), 'ArrowUp from a checkbox');
  // typing in the filter: ignored, not prevented
  await page.locator('#sidebar input').focus();
  await page.keyboard.press('ArrowDown');
  await stable();
  check((await snapshot()).item === order.at(-3), 'arrows: ArrowDown in the filter input changed the layer');
  check(await page.evaluate(() => window.__prevented) === false, 'arrows: ArrowDown in the filter input was prevented');
  await page.evaluate(() => document.activeElement.blur());
  await page.keyboard.press('?');
  check(/↑ \/ ↓/.test(await page.locator('#help').textContent()), 'arrows: not in the ? help');
  await shot('layout-arrows-help');
  await page.keyboard.press('Escape');
  // the schematic has no layer list: arrows are left to the page
  await page.goto(`${base}${P}/schematic`);
  await stable();
  await probe();
  await page.keyboard.press('ArrowDown');
  check(await page.evaluate(() => window.__prevented) === false, 'arrows: ArrowDown prevented in the schematic');
}

// a broken step stops its section; what failed before it is still reported
for (const run of [layout, schematic, arrows]) {
  try { await run(); } catch (e) { problems.push(`${run.name}: stopped: ${e.message.split('\n')[0]}`); }
}
await browser.close();
server?.close();
if (problems.length) {
  console.error(`persist: ${problems.length} problem(s):\n  ${[...new Set(problems)].join('\n  ')}`);
  process.exit(1);
}
console.log('persist: OK');
