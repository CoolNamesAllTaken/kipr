// Loading one side's GLB and turning it into something the viewer can reason about: a board
// (substrate / mask / copper / silk), and components keyed by reference designator.
//
// The work is boarddd/models (web/vendor/boarddd: loadGLB, prepareModel): it measures the up axis
// and the units (the board is by far the flattest thing in the file), merges each part to a few
// meshes, tells board bodies apart by name, size and colour, and fits the translation between the
// contract's KiCad coordinates and the GLB from name and position matches, so the export origin
// does not matter and base and head line up. What stays here is the contract's side of it: each
// component's placement on this side, the board's size/origin, and pcba3d.frame.

import { loadGLB, prepareModel, boardKindFromName, disposeObject } from '../vendor/boarddd/src/models/index.js';

export { fetchBytes } from './assets.js';
export { boardKindFromName, disposeObject };

/** A GLB's bytes -> boarddd's loadGLB result ({scene, gltf, meshName}). */
export function parseGlb(buffer) {
  return loadGLB(buffer);
}

/**
 * Build a side.
 *   model       a parseGlb() / loadGLB() result (or a GLTFLoader result, or just a scene)
 *   components  contract components (normalized), each with this side's placement in c[sideName]
 *   board       the contract's pcb.board, or null (used for the unit check and as a fallback origin)
 *   frame       pcba3d.frame ({units, up}) if the backend states it; measured otherwise
 * Returns boarddd's prepareModel() result ({root, parts, comps, loose, meshes, report, boardBox,
 * bounds, ...}) with each comps entry's `component` the contract component, and boardMidZ.
 */
export function prepareSide(model, components, sideName, board = null, frame = null) {
  const mine = components.filter((c) => c[sideName]);
  // a panel's copies ("R7·2") export under their designator ("R7"): boarddd matches those names to the nearest copy
  const placements = mine.map((c) => ({ ref: c.ref, name: typeof c.designator === 'string' ? c.designator : undefined, x: c[sideName].x, y: c[sideName].y, side: c[sideName].side }));
  const side = prepareModel(model, placements, {
    boardSize: board?.size_mm || null,
    boardOrigin: board?.origin_mm || null,
    units: frame?.units || null,
    up: frame?.up ? String(frame.up).replace('+', '') : null,
  });
  side.root.name = `pcba3d-${sideName}`;
  const byRef = new Map(mine.map((c) => [c.ref, c]));
  for (const entry of side.comps.values()) entry.component = byRef.get(entry.ref);
  side.boardMidZ = side.boardBox.isEmpty() ? 0 : (side.boardBox.min.z + side.boardBox.max.z) / 2;
  return side;
}
