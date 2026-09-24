// The bare board as a solid: the Edge.Cuts outline extruded to the board's thickness, drilled
// holes punched through it, plated barrels standing in the plated ones, and UVs that put a
// gerber face texture exactly where the gerber is. No DOM and no gerber rendering here (that is
// gerberboard.js), so it runs under node (tests/web-3d/boardgeom.test.mjs).
//
// Ported from gentoo's viewer3d.js (PantsForBirds/internal, branch john/gentoo,
// fab/static/fab/viewer3d.js: buildBoard, usableDrills, withinBudget, clearance, loopAt,
// ringPoints, slotPoints, counterClockwise, buildBarrels, planarUVs, splitCaps), where the
// reasons for each rule are written up at length; the short versions are kept below.
//
// Frame: board mm, x right, y UP (gerber coordinates, which are KiCad's with y negated), z out
// of the top copper. The board's bottom face is at z = 0 and its top at z = thickness, which is
// where kicad-cli's GLB mounts the top-side components.

import * as THREE from './vendor/three/three.module.js';

export const PLATING_MM = 0.025;           // class-2 minimum; what a fab hits unasked
export const COPPER = 0xb87333;
export const FR4 = 0xc9b27c;               // the laminate, seen on the cut edge and hole walls

// Round enough that the flats never show at any size, without spending sides on 0.3 mm vias.
const HOLE_SAGITTA_MM = 0.01;
const HOLE_SEGMENTS_MIN = 10;
const HOLE_SEGMENTS_MAX = 48;
// A barrel's outer wall is buried this far in the laminate: coplanar walls z-fight, and the
// plating loses.
const BARREL_BITE_MM = 0.005;
// earcut bridges every hole into the outer ring, so punching costs O(holes^2): 400 is about a
// second, 2600 was a minute. The largest openings are punched first; the rest stay painted.
export const HOLE_BUDGET = 400;

export function segmentsFor(radius) {
  if (!(radius > 0)) return HOLE_SEGMENTS_MIN;
  const ratio = Math.min(1, HOLE_SAGITTA_MM / radius);
  const needed = Math.ceil(Math.PI / Math.acos(Math.max(-1, 1 - ratio)));
  return Math.max(HOLE_SEGMENTS_MIN, Math.min(HOLE_SEGMENTS_MAX, needed));
}

/** Counter-clockwise by three's reckoning, so ExtrudeGeometry normalises every hole's winding. */
export function counterClockwise(points) {
  return THREE.ShapeUtils.isClockWise(points) ? points.slice().reverse() : points;
}

export function pointToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const l2 = dx * dx + dy * dy;
  const t = l2 > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Inside a closed loop ([[x, y]]), and the distance to its nearest edge. */
export function clearance(loop, x, y) {
  let inside = false, distance = Infinity;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i, i += 1) {
    const [xi, yi] = loop[i], [xj, yj] = loop[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    distance = Math.min(distance, pointToSegment(x, y, xi, yi, xj, yj));
  }
  return { inside, distance };
}

export function ringPoints(cx, cy, radius, segments = segmentsFor(radius)) {
  const points = [];
  for (let i = 0; i < segments; i += 1) {
    const a = (-2 * Math.PI * i) / segments;
    points.push(new THREE.Vector2(cx + radius * Math.cos(a), cy + radius * Math.sin(a)));
  }
  return points;
}

/** A routed slot: a semicircle at each end joined by its flanks, as one loop. */
export function slotPoints(x1, y1, x2, y2, radius, segments = segmentsFor(radius)) {
  const along = Math.atan2(y2 - y1, x2 - x1);
  const half = Math.max(2, Math.round(segments / 2));
  const points = [];
  const cap = (cx, cy, from) => {
    for (let i = 0; i <= half; i += 1) {
      const a = from + (Math.PI * i) / half;
      points.push(new THREE.Vector2(cx + radius * Math.cos(a), cy + radius * Math.sin(a)));
    }
  };
  cap(x2, y2, along - Math.PI / 2);
  cap(x1, y1, along + Math.PI / 2);
  return points;
}

export function loopAt(ends, radius) {
  return ends.length === 1
    ? ringPoints(ends[0][0], ends[0][1], radius)
    : slotPoints(ends[0][0], ends[0][1], ends[1][0], ends[1][1], radius);
}

/**
 * The holes that can be punched: clear of the outline and of every cutout by their own radius
 * (earcut's answer to overlapping loops is a silently wrong mesh, up to no board at all), and
 * not filled. `holes` as parsed by the renderer's drills.js: {x, y, diameter, plated, x2, y2,
 * filled} in gerber mm. Returns {kept, leftOut: {count, total, largest_mm} | null, rejected}.
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

/** Bounds of an outline's board loop, {minX, maxX, minY, maxY}. */
export function loopBounds(loop) {
  const b = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const [x, y] of loop) {
    b.minX = Math.min(b.minX, x); b.maxX = Math.max(b.maxX, x);
    b.minY = Math.min(b.minY, y); b.maxY = Math.max(b.maxY, y);
  }
  return b;
}

/** A rectangle outline from the contract's board box (KiCad mm, y down): the fallback. */
export function rectOutline(board) {
  const [ox, oy] = board.origin_mm, [w, h] = board.size_mm;
  return { board: [[ox, -oy - h], [ox + w, -oy - h], [ox + w, -oy], [ox, -oy]], cutouts: [], approximate: true };
}

/** UVs from x and y over `bounds`, the same linear map the face raster was painted with. */
export function planarUVs(geometry, bounds) {
  const w = Math.max(bounds.maxX - bounds.minX, 1e-6), h = Math.max(bounds.maxY - bounds.minY, 1e-6);
  const pos = geometry.attributes.position, uv = geometry.attributes.uv;
  for (let i = 0; i < pos.count; i += 1) uv.setXY(i, (pos.getX(i) - bounds.minX) / w, (pos.getY(i) - bounds.minY) / h);
  uv.needsUpdate = true;
}

/** Groups: 0 = top cap, 1 = bottom cap, 2 = walls (the extruder puts both caps in one group). */
export function splitCaps(geometry) {
  const pos = geometry.attributes.position;
  const caps = geometry.groups.find((g) => g.materialIndex === 0);
  const walls = geometry.groups.find((g) => g.materialIndex === 1);
  if (!caps) return;
  let low = Infinity, high = -Infinity;
  for (let i = caps.start; i < caps.start + caps.count; i += 1) {
    const z = pos.getZ(i);
    if (z < low) low = z;
    if (z > high) high = z;
  }
  if (high - low < 1e-9) return;
  const middle = (low + high) / 2;
  const firstIsTop = pos.getZ(caps.start) > middle;
  let boundary = caps.start + caps.count;
  for (let i = caps.start; i < caps.start + caps.count; i += 3) {
    if ((pos.getZ(i) > middle) !== firstIsTop) { boundary = i; break; }
  }
  const runs = [[caps.start, boundary - caps.start], [boundary, caps.start + caps.count - boundary]];
  const [top, bottom] = firstIsTop ? runs : [runs[1], runs[0]];
  geometry.clearGroups();
  geometry.addGroup(top[0], top[1], 0);
  geometry.addGroup(bottom[0], bottom[1], 1);
  if (walls) geometry.addGroup(walls.start, walls.count, 2);
}

/**
 * The board solid and its barrels.
 *   outline   {board: [[x, y]], cutouts: [[[x, y]]]} in board mm (y up)
 *   holes     drill list (see usableHoles)
 *   thickness mm
 *   uvBounds  the face rasters' bounds, so the textures land on the copper they show
 * Returns {body: BufferGeometry (groups top/bottom/walls), barrels: BufferGeometry | null,
 *          holes: usableHoles() report}.
 */
export function boardGeometry(outline, holes, thickness, uvBounds) {
  const ring = outline.board.map(([x, y]) => new THREE.Vector2(x, y));
  const shape = new THREE.Shape(counterClockwise(ring));
  for (const hole of outline.cutouts || []) {
    if (hole.length >= 3) shape.holes.push(new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x, y))));
  }
  const report = usableHoles(holes, outline);
  for (const hole of report.kept) shape.holes.push(new THREE.Path(loopAt(hole.ends, hole.radius)));
  const body = new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false, curveSegments: 1 });
  planarUVs(body, uvBounds || loopBounds(outline.board));
  splitCaps(body);

  const shapes = [];
  for (const hole of report.kept) {
    if (!hole.plated) continue;
    const s = new THREE.Shape(counterClockwise(loopAt(hole.ends, hole.radius + BARREL_BITE_MM)));
    const inner = hole.radius - PLATING_MM;
    if (inner > 0) s.holes.push(new THREE.Path(loopAt(hole.ends, inner)));
    shapes.push(s);
  }
  const barrels = shapes.length ? new THREE.ExtrudeGeometry(shapes, { depth: thickness, bevelEnabled: false }) : null;
  return { body, barrels, holes: report };
}

/** Polyline points of an outline's loops at height z, for a ghost edge (THREE.LineLoop each). */
export function outlineLoops(outline, z) {
  return [outline.board, ...(outline.cutouts || [])].filter((l) => l.length >= 2)
    .map((l) => l.map(([x, y]) => new THREE.Vector3(x, y, z)));
}

/** Whether two outlines differ by more than `tol` mm anywhere (vertex-wise, both directions). */
export function outlinesDiffer(a, b, tol = 0.01) {
  if (!a || !b) return !!(a || b);
  const loops = (o) => [o.board, ...(o.cutouts || [])];
  const la = loops(a), lb = loops(b);
  if (la.length !== lb.length) return true;
  const far = (from, to) => from.some((ring) => ring.some(([x, y]) =>
    !to.some((other) => clearance(other, x, y).distance <= tol)));
  return far(la, lb) || far(lb, la);
}
