// The board built from the fab outputs rather than taken from the GLB: Edge.Cuts outline
// extruded to the stackup's thickness, drilled and plated from the drill files, and both faces
// painted with the gerbers as the board will come back from the fab (mask colour, finish on
// exposed copper, silkscreen, see-through holes). The same frame carries a copper-diff picture
// of each face for the overlay modes: removed copper red, added green, unchanged dim, with the
// outline and holes in it, so rerouting and outline changes can be seen in 3D.
//
// The board itself is boarddd/board (web/vendor/boarddd: readFabFiles, buildGerberBoard,
// paintCopperDiff, outlineGhost), with our wasm-gerber-renderer fork injected: the project
// viewer's shared vendored copy in web/project/vendor/. What stays here is kipr's: which of the
// contract's layers make the board, the stackup's colours and thickness, and the file:// WASM pack.

import { createGerberRenderer } from '../vendor/wasm-gerber-renderer/index.js';
import * as wasmGlue from '../vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor.js';
import * as gerberBoard from '../vendor/wasm-gerber-renderer/board.js';
import * as gerberDiff from '../vendor/wasm-gerber-renderer/diff.js';
import * as gerberDrills from '../vendor/wasm-gerber-renderer/drills.js';
import * as gerberLayers from '../vendor/wasm-gerber-renderer/layers.js';
import * as gerberOutline from '../vendor/wasm-gerber-renderer/outline.js';
import * as gerberRaster from '../vendor/wasm-gerber-renderer/raster.js';
import {
  readFabFiles, faceBounds, buildGerberBoard, paintCopperDiff, canvasTexture, outlineGhost, outlinesDiffer, MAX_FACE_PX,
} from '../vendor/boarddd/src/board/index.js';

const WASM_URL = new URL('../vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm', import.meta.url);
export const OFFLINE_WASM_KEY = 'vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm';
export const FAB_KINDS = new Set(['copper', 'mask', 'silk', 'outline', 'drill']);   // the layers a board is built from
// The fork's functions boarddd uses, as one object (boarddd never imports the renderer itself).
const GERBER = { ...gerberBoard, ...gerberDiff, ...gerberDrills, ...gerberLayers, ...gerberOutline, ...gerberRaster };

function basename(path) {
  return String(path).split('/').pop();
}

/** The fork's renderer on its own canvas, with the WASM from the vendored copy (or the pack). */
async function makeRenderer(assets) {
  let module_or_path = WASM_URL;
  if (assets.offline) module_or_path = new Uint8Array(await assets.bytes(OFFLINE_WASM_KEY));
  return createGerberRenderer(document.createElement('canvas'), {
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
 * Resolves to null when the project has no fab outputs, else
 *   {bounds, sides: {base, head}, outlineChanged, ghost: THREE.Group | null,
 *    diffTextures(): Promise<{top, bottom}>, dispose()}
 * where a side is boarddd's buildGerberBoard() result ({group, body, barrels, outline, materials,
 * textures, holes, thickness, fab, painted, ...}) plus `approximate`.
 */
export async function buildGerberBoards(project, assets, { onStatus = () => {}, maxTextureSize = MAX_FACE_PX } = {}) {
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
        board: info, palette: palette(info), bounds, maxTextureSize, name: `gerber-${side}`,
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

  // Copper diff pictures, on demand (only the overlay modes use them).
  const owned = [];
  let diffPromise = null;
  const diffTextures = () => {
    diffPromise ??= (async () => {
      onStatus('Diffing the copper…');
      const painted = (sides.head || sides.base).painted;
      const c = await paintCopperDiff(GERBER, renderer, { base: fab.base || null, head: fab.head || null }, painted, { maxTextureSize });
      const out = { top: canvasTexture(c.top), bottom: canvasTexture(c.bottom) };
      owned.push(out.top, out.bottom);
      return out;
    })();
    return diffPromise;
  };

  return {
    bounds, sides, outlineChanged, ghost, diffTextures,
    dispose() {
      for (const s of Object.values(sides)) s.dispose();
      ghost?.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
      for (const t of owned) t.dispose();
      renderer.dispose?.();
    },
  };
}
