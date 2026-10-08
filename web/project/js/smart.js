// The schematic's smart diff (default on): changes that only move things with the same connections
// (`move_only`, kipr.project.classify) are kept quiet. Off: the raw diff shows them as changes.
// Remembered per browser; off is also carried in the URL (smart=0).
import { createPref } from './pref.js';

const pref = createPref('kipr.schSmart', 'smart', true);

export const smartOn = pref.get;
export const setSmart = (on) => pref.set(on);
export const toggleSmart = () => pref.toggle();
export const smartFromParams = (params) => pref.fromParams(params);
export const smartParam = () => pref.param();
export const onSmart = (fn) => pref.on(fn);
export const resetSmart = () => pref.reset();

/** A sheet's change counts in the current mode: {changed, minor, moved} (moved is 0 in raw mode). */
export function sheetCounts(sheet, smart = smartOn()) {
  const list = Array.isArray(sheet?.changes) ? sheet.changes.filter((c) => c && typeof c === 'object') : [];
  const moved = smart ? list.filter((c) => c.move_only).length : 0;
  const minor = list.filter((c) => c.minor && !(smart && c.move_only)).length;
  return { changed: list.length - moved - minor, minor, moved };
}

/** Changed sheets of a project summary in the current mode (smart leaves out sheets that only moved things). */
export function sheetsChanged(summary, smart = smartOn()) {
  const n = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  return n(summary?.sheets_changed) + (smart ? 0 : n(summary?.sheets_moved));
}

const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const inside = (x, y, b) => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h;

/**
 * An ink diff region {x, y, w, h} that only shows moved items: its corners and centre lie in the quiet
 * `boxes` and it touches none of the `holes` (the real changes).
 */
export function onlyMoved(r, { boxes = [], holes = [] } = {}) {
  const pts = [[r.x, r.y], [r.x + r.w, r.y], [r.x, r.y + r.h], [r.x + r.w, r.y + r.h], [r.x + r.w / 2, r.y + r.h / 2]];
  return boxes.length > 0 && pts.every(([x, y]) => boxes.some((b) => inside(x, y, b))) && !holes.some((h) => overlaps(r, h));
}
