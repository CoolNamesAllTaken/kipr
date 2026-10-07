// View state that survives navigation: route param merging, stepping through layers / sheets, the last
// route of each tab, and the short notes shown when the kept compare mode does not fit the newly selected
// layer or sheet. Pure (no DOM) so it can be unit-tested.
//
// URL params (besides route.js's mode, c, view, q, ...), written and read with boarddd/view2d's
// formatRegion / parseRegion / formatSlider / parseSlider:
//   z=cx,cy,w    zoomed region: centre (KiCad mm) and visible width (mm) of the first pane; absent: fitted
//   sw=0.3       swipe divider position (0..1), in swipe mode; absent: as it is (the sliders are shared)
//   op=0.7       onion skin head opacity (0..1), in onion mode; absent: as it is

/** params with `patch` merged over them; null / undefined / '' values remove a key. */
export function mergeParams(params, patch) {
  const out = { ...(params || {}), ...(patch || {}) };
  for (const [k, v] of Object.entries(out)) if (v === null || v === undefined || v === '') delete out[k];
  return out;
}

/** Two world boxes of the same size (sheets: keep the zoom when stepping between equal paper sizes). */
export function sameSize(a, b, tolMm = 0.5) {
  return !!a && !!b && Math.abs(a.w - b.w) <= tolMm && Math.abs(a.h - b.h) <= tolMm;
}

/** The item `d` steps from `cur` in `list`; wraps around with `wrap`, else stops at the ends. */
export function stepItem(list, cur, d, wrap = false) {
  if (!list.length) return null;
  const i = list.indexOf(cur);
  if (i < 0) return list[d > 0 ? 0 : list.length - 1];
  const j = i + d;
  if (wrap) return list[((j % list.length) + list.length) % list.length];
  return list[Math.min(Math.max(j, 0), list.length - 1)];
}

// Params that are not view state: boxes is global (boxes.js), at= is a one-off "zoom here" from a link.
const NOT_REMEMBERED = new Set(['boxes', 'at']);

/** Remember a project tab's route (item + params) in `mem` (a Map), so going back to that tab restores it. */
export function rememberRoute(mem, r) {
  if (!r?.slug || !r.tab) return mem;
  const params = Object.fromEntries(Object.entries(r.params || {}).filter(([k]) => !NOT_REMEMBERED.has(k)));
  mem.set(`${r.slug}|${r.tab}`, { item: r.item ?? null, params });
  return mem;
}

/** The route to open for a project tab: the remembered one, else the tab's default. */
export function routeFor(mem, slug, tab) {
  const m = mem.get(`${slug}|${tab}`);
  return { slug, tab, item: m?.item ?? null, params: { ...(m?.params || {}) } };
}

/**
 * Note for the selected PCB layer under the kept compare mode, or null when the combination is fine.
 * solo: a layer is selected (every mode shows it alone); otherwise only Diff shows a single layer.
 */
export function layerNote(layer, mode, { bothSides = true, solo = true } = {}) {
  if (!layer || typeof layer.id !== 'string' || (mode !== 'diff' && !solo)) return null;
  const id = layer.id;
  const diff = mode === 'diff';
  if (layer.status === 'unchanged') return `${id} is identical in base and head${diff ? ': the diff shows no changes' : ''}.`;
  if (bothSides && layer.status === 'added') return `${id} is only in head: ${diff ? 'all of it shows as added' : 'the base side is empty'}.`;
  if (bothSides && layer.status === 'removed') return `${id} is only in base: ${diff ? 'all of it shows as removed' : 'the head side is empty'}.`;
  return null;
}

/** Note for a schematic sheet under the kept compare mode, or null. */
export function sheetNote(sheet, mode, { hasBase = true, hasHead = true, bothSides = true } = {}) {
  if (!sheet) return null;
  if (bothSides && hasBase !== hasHead && (hasBase || hasHead)) {
    const only = hasHead ? 'head (added)' : 'base (removed)';
    if (mode === 'diff') return `This sheet is only in ${only}: all of it shows as ${hasHead ? 'added' : 'removed'}.`;
    return `This sheet is only in ${only}: the ${hasHead ? 'base' : 'head'} side is empty.`;
  }
  if (mode === 'diff' && sheet.status === 'unchanged') return 'This sheet is identical in base and head: the diff shows no changes.';
  return null;
}
