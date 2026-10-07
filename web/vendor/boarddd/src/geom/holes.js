// Which drilled holes and slots can be punched through a board solid. Ported from gentoo's viewer3d.js
// (usableDrills, withinBudget) by way of kipr's web/project/pcba3d/boardgeom.js (usableHoles).
//
// A hole is kept only where it is clear of the outline and of every cutout by its own radius: earcut's
// answer to overlapping loops is a silently wrong mesh, up to no board at all; a missing hole is visible,
// a vanished board is a mystery. Filled (plugged and capped) vias are not punched. And punching costs
// O(holes^2) in earcut (400 holes ~ 1 s, 2600 was a minute), so past HOLE_BUDGET the largest openings
// are punched (a slot ranks by its length: a USB shell goes through it) and the rest left out.

import { clearance, loopAt } from './loops.js';

export const HOLE_BUDGET = 400;
/** Class-2 minimum plating thickness: what a fab hits unasked. */
export const PLATING_MM = 0.025;

/**
 * Holes as wasm-gerber-renderer's parseExcellon gives them, `{x, y, diameter, plated, x2?, y2?, filled?}`
 * in board mm (`d` accepted for `diameter`), against an outline `{board, cutouts}`.
 * Returns `{kept: [{plated, radius, ends, extent}], leftOut: {count, total, largest_mm} | null, rejected}`.
 */
export function usableHoles(holes, outline, budget = HOLE_BUDGET) {
  const board = outline.board;
  const cutouts = (outline.cutouts || []).filter((l) => l.length >= 3);
  const kept = [];
  let rejected = 0;
  for (const hole of holes || []) {
    if (hole.filled) continue;
    const diameter = Number(hole.diameter ?? hole.d);
    if (!(diameter > 0)) { rejected++; continue; }
    const radius = diameter / 2;
    const ends = [[Number(hole.x), Number(hole.y)]];
    if (hole.x2 != null && hole.y2 != null) ends.push([Number(hole.x2), Number(hole.y2)]);
    if (!ends.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y))) { rejected++; continue; }
    const fits = ends.every(([x, y]) => {
      const c = clearance(board, x, y);
      if (!c.inside || c.distance <= radius) return false;
      return cutouts.every((loop) => {
        const g = clearance(loop, x, y);
        return !g.inside && g.distance > radius;
      });
    });
    if (!fits) { rejected++; continue; }
    kept.push({
      plated: !!hole.plated, radius, ends,
      extent: diameter + (ends.length === 1 ? 0 : Math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1])),
    });
  }
  if (kept.length <= budget) return { kept, leftOut: null, rejected };
  const ordered = kept.slice().sort((a, b) => b.extent - a.extent);   // stable: file order on ties
  const left = ordered.slice(budget);
  return { kept: ordered.slice(0, budget), leftOut: { count: left.length, total: kept.length, largest_mm: left[0].extent }, rejected };
}

/** A kept hole's loop at its radius grown by `grow` (plating: negative; barrel bite: positive). */
export const holeLoop = (hole, grow = 0) => loopAt(hole.ends, hole.radius + grow);

/** Paste deposit height, mm: a 0.12 mm (about 5 mil) stencil. */
export const PASTE_THICKNESS = 0.12;

const fillable = (hole) => hole.plated !== false && (hole.x2 == null || hole.y2 == null
  || (hole.x2 === hole.x && hole.y2 === hole.y));

/**
 * Holes with every plated round hole of drill diameter <= `upTo` mm marked `filled: true` (filled and
 * capped, VIPPO-style: no opening, plating over both ends). Vias and plated pad holes alike; unplated
 * holes and slots stay open. `upTo` null or <= 0 leaves the holes as given. Returns a new array.
 */
export function fillHoles(holes, upTo) {
  const limit = Number(upTo);
  if (!(limit > 0)) return (holes || []).slice();
  return (holes || []).map((hole) => (fillable(hole) && Number(hole.diameter ?? hole.d) <= limit + 1e-6 ? { ...hole, filled: true } : hole));
}

/**
 * The plated round drill sizes of a board, smallest first: `[{diameter, count, vias}]` (`vias`: holes
 * the drill file marks as vias). The sizes `fillHoles` can fill; diameters rounded to the micron.
 */
export function drillSizes(holes) {
  const sizes = new Map();
  for (const hole of holes || []) {
    const diameter = Math.round(Number(hole.diameter ?? hole.d) * 1000) / 1000;
    if (!(diameter > 0) || !fillable(hole)) continue;
    const s = sizes.get(diameter) || { diameter, count: 0, vias: 0 };
    s.count += 1;
    if (hole.via) s.vias += 1;
    sizes.set(diameter, s);
  }
  return [...sizes.values()].sort((a, b) => a.diameter - b.diameter);
}
