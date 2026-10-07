// The board built from the fab outputs rather than taken from the GLB: Edge.Cuts outline
// extruded to the stackup's thickness, drilled and plated from the drill files, and both faces
// painted with the gerbers as the board will come back from the fab (mask colour, finish on
// exposed copper, silkscreen, see-through holes). The same frame carries a copper-diff picture
// of each face for the overlay modes: removed copper red, added green, unchanged dim, with the
// outline and holes in it, so rerouting and outline changes can be seen in 3D.
//
// The board itself is boarddd/board (web/vendor/boarddd: readFabFiles, buildGerberBoard,
// paintCopperDiff, outlineGhost, buildPaste), drawn by boarddd/gerber from the same vendored copy.
// What stays here is kipr's: which of the contract's layers make the board, the stackup's colours
// and thickness, and the file:// WASM pack. Options: filled and capped holes up to a drill size
// (boarddd fillFab) and the solder paste as solids, built on demand.

import * as gerber from '../vendor/boarddd/src/gerber/index.js';
import * as wasmGlue from '../vendor/boarddd/third_party/wasm-gerber-renderer/core/wasm/wasm_gerber_processor.js';
import {
  readFabFiles, faceBounds, buildGerberBoard, paintCopperDiff, buildPaste, canvasTexture, outlineGhost, outlinesDiffer, MAX_FACE_PX,
} from '../vendor/boarddd/src/board/index.js';
import { drillSizes } from '../vendor/boarddd/src/geom/index.js';

// The vendored wasm, relative to web/project/; also its key in the file:// pack offline/pcba3d-vendor.js
// (kipr/project/site.py PCBA3D_WASM).
export const OFFLINE_WASM_KEY = 'vendor/boarddd/third_party/wasm-gerber-renderer/core/wasm/wasm_gerber_processor_bg.wasm';
const WASM_URL = new URL(`../${OFFLINE_WASM_KEY}`, import.meta.url);
export const FAB_KINDS = new Set(['copper', 'mask', 'silk', 'paste', 'outline', 'drill']);   // the layers a board is built from
// boarddd/board takes the gerber implementation explicitly: the same module, so the bundle has one copy.
const GERBER = gerber;

function basename(path) {
  return String(path).split('/').pop();
}

/** boarddd/gerber's renderer on its own canvas, with the WASM from the vendored copy (or the pack). */
async function makeRenderer(assets) {
  let module_or_path = WASM_URL;
  if (assets.offline) module_or_path = new Uint8Array(await assets.bytes(OFFLINE_WASM_KEY));
  return gerber.createGerberRenderer(document.createElement('canvas'), {
    wasmModule: wasmGlue,
    wasmInitInput: { module_or_path },
    contextAttributes: { preserveDrawingBuffer: true },
  });
}

/** One side's fab files, [{name, text, plated?}]: the contract's outer copper, mask, silk, outline and drills. */
async function loadSideFiles(project, side, assets) {
  const layers = (project.pcb?.layers || []).filter((l) => l[side]?.gerber && FAB_KINDS.has(l.kind)
    && !(l.kind === 'copper' && l.side === 'inner'));
  return Promise.all(layers.map(async (l) => {
    const name = basename(l[side].gerber);
    const file = { name, text: await assets.text(l[side].gerber) };
    if (l.kind === 'drill') file.plated = !/NPTH/i.test(l.id) && !/NPTH/i.test(name);
    return file;
  }));
}

function boardInfo(project, side) {
  const b = project.pcb?.board || {};
  return { ...b, ...(b[side] || {}) };
}

function palette(info) {
  const p = {};
  if (info.mask_color) p.mask = info.mask_color;
  if (info.silk_color) p.silk = info.silk_color;
  if (info.finish && !/^none$/i.test(info.finish)) p.finish = info.finish;
  return p;
}

/**
 * Build both sides' boards.
 *   project  Project (reads pcb.layers, pcb.board and its base/head)
 *   assets   assetLoader (assets.js)
 *   onStatus (text) progress messages
 *   fillUpTo mm: fill and cap plated round holes up to this drill diameter (null: all open)
 * Resolves to null when the project has no fab outputs, else
 *   {bounds, sides: {base, head}, outlineChanged, ghost: THREE.Group | null, fillUpTo,
 *    drillSizes: [{diameter, count, vias}] (both sides' plated round holes, per side the larger count),
 *    diffTextures(): Promise<{top, bottom}>, paste(): Promise<boolean> (adds each side's paste solids
 *    to its group as side.paste; false when no side has paste), dispose()}
 * where a side is boarddd's buildGerberBoard() result ({group, body, barrels, outline, materials,
 * textures, holes, thickness, fab, painted, ...}) plus `approximate`.
 */
export async function buildGerberBoards(project, assets, { onStatus = () => {}, maxTextureSize = MAX_FACE_PX, fillUpTo = null } = {}) {
  if (!project.pcb?.layers?.length) return null;
  const files = {};
  const fab = {};
  for (const side of ['base', 'head']) {
    const wanted = project.pcba3d?.[side] || project.pcb.board?.[side] || project.status !== (side === 'base' ? 'added' : 'removed');
    files[side] = wanted ? await loadSideFiles(project, side, assets).catch(() => null) : null;
    if (!files[side]?.length) { files[side] = null; continue; }
    fab[side] = readFabFiles(GERBER, files[side], boardInfo(project, side));
  }
  const present = ['base', 'head'].filter((s) => fab[s]?.outline);
  if (!present.length) return null;

  // One frame for every picture of either side, so any texture fits any face.
  const bounds = faceBounds(present.map((s) => fab[s].outline), 0.5);
  const renderer = await makeRenderer(assets);
  const sides = {};
  try {
    for (const side of present) {
      const info = boardInfo(project, side);
      onStatus(`Painting the ${side} board…`);
      const s = await buildGerberBoard(GERBER, renderer, files[side], {
        thickness: Number(info.thickness_mm) > 0 ? Number(info.thickness_mm) : 1.6,
        board: info, palette: palette(info), bounds, maxTextureSize, name: `gerber-${side}`, fillUpTo,
      });
      s.body.userData.boardKind = 'substrate';
      s.approximate = !!s.outline.approximate;
      sides[side] = s;
    }
  } catch (e) {
    for (const s of Object.values(sides)) s.dispose();
    renderer.dispose?.();
    throw e;
  }

  // The base outline as a ghost edge on the head board, when the two differ.
  const outlineChanged = !!(sides.base && sides.head && outlinesDiffer(sides.base.outline, sides.head.outline));
  const ghost = outlineChanged ? outlineGhost(sides.base.outline, [sides.base.thickness + 0.03, -0.03]) : null;
  if (ghost) ghost.name = 'base-outline-ghost';

  // The renderer draws one frame at a time: the on-demand jobs below take turns.
  let queue = Promise.resolve();
  const exclusive = (job) => {
    const run = queue.then(job, job);
    queue = run.catch(() => {});
    return run;
  };

  // Copper diff pictures, on demand (only the overlay modes use them).
  const owned = [];
  let diffPromise = null;
  const diffTextures = () => {
    diffPromise ??= exclusive(async () => {
      onStatus('Diffing the copper…');
      const painted = (sides.head || sides.base).painted;
      const c = await paintCopperDiff(GERBER, renderer, { base: fab.base || null, head: fab.head || null }, painted, { maxTextureSize });
      const out = { top: canvasTexture(c.top), bottom: canvasTexture(c.bottom) };
      owned.push(out.top, out.bottom);
      return out;
    });
    return diffPromise;
  };

  // Paste solids, on demand (traced from a raster of each face's paste layer).
  let pastePromise = null;
  const paste = () => {
    pastePromise ??= exclusive(async () => {
      let any = false;
      for (const s of Object.values(sides)) {
        if (!s.fab.grouped.top?.paste && !s.fab.grouped.bottom?.paste) continue;
        onStatus('Tracing the solder paste…');
        s.paste = await buildPaste(GERBER, renderer, s.fab, s.painted, { thickness: s.thickness, name: `${s.group.name}-paste` });
        s.group.add(s.paste.group);
        any = any || !!(s.paste.meshes.top || s.paste.meshes.bottom);
      }
      return any;
    });
    return pastePromise;
  };

  // The sizes there are to fill: per size, the larger of the two sides' counts.
  const sizes = new Map();
  for (const s of Object.values(sides)) {
    for (const d of drillSizes(s.fab.holes)) {
      const had = sizes.get(d.diameter);
      if (!had || had.count < d.count) sizes.set(d.diameter, d);
    }
  }

  return {
    bounds, sides, outlineChanged, ghost, diffTextures, paste, fillUpTo: Number(fillUpTo) > 0 ? Number(fillUpTo) : null,
    drillSizes: [...sizes.values()].sort((a, b) => a.diameter - b.diameter),
    dispose() {
      for (const s of Object.values(sides)) { s.paste?.dispose(); s.dispose(); }
      ghost?.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
      for (const t of owned) t.dispose();
      renderer.dispose?.();
    },
  };
}
