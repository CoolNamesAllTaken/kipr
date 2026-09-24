// The board built from the fab outputs rather than taken from the GLB: Edge.Cuts outline
// extruded to the stackup's thickness, drilled and plated from the drill files, and both faces
// painted with the gerbers as the board will come back from the fab (mask colour, finish on
// exposed copper, silkscreen, see-through holes). The same frame carries a copper-diff picture
// of each face for the overlay modes: removed copper red, added green, unchanged dim, with the
// outline and holes in it, so rerouting and outline changes can be seen in 3D.
//
// All gerber work is done by our wasm-gerber-renderer fork (CoolNamesAllTaken/wasm-gerber-viewer,
// the shared vendored copy in web/project/vendor/): layers.groupBoardLayers, outline.boardOutline,
// drills.parseExcellon/holesToGerber, board.renderFaceRaster (+ faceRasterSize), diff.renderLayerDiff
// in the face raster's own view so both pictures share UVs, raster.copyScaled. The solid is
// boardgeom.js (ported from gentoo's viewer3d.js buildBoard).

import * as THREE from './vendor/three/three.module.js';
import { createGerberRenderer } from '../vendor/wasm-gerber-renderer/index.js';
import * as wasmGlue from '../vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor.js';
import { renderFaceRaster, faceRasterSize } from '../vendor/wasm-gerber-renderer/board.js';
import { renderLayerDiff } from '../vendor/wasm-gerber-renderer/diff.js';
import { parseExcellon, holesToGerber } from '../vendor/wasm-gerber-renderer/drills.js';
import { groupBoardLayers } from '../vendor/wasm-gerber-renderer/layers.js';
import { boardOutline } from '../vendor/wasm-gerber-renderer/outline.js';
import { copyScaled } from '../vendor/wasm-gerber-renderer/raster.js';
import {
  boardGeometry, rectOutline, loopBounds, outlineLoops, outlinesDiffer, COPPER, FR4,
} from './boardgeom.js';

const WASM_URL = new URL('../vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm', import.meta.url);
export const OFFLINE_WASM_KEY = 'vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm';
const PX_PER_MM = 24;              // ~0.04 mm per texel: 0.1 mm tracks and silk stay legible
const MAX_PX = 4096;               // what every WebGL2 implementation must support
const DIFF_BACKGROUND = '#2d333b'; // the board behind the diff: neutral, dark enough for dim copper
// Unchanged copper has to read as copper at board scale (the fork's default is tuned for an
// overlay on top of a render); changes stay the fork's red and green.
const DIFF_STYLE = { unchanged: { color: [0.72, 0.64, 0.5], alpha: 0.7 } };
export const FAB_KINDS = new Set(['copper', 'mask', 'silk', 'outline', 'drill']);   // the layers a board is built from

function basename(path) {
  return String(path).split('/').pop();
}

/** The fork's renderer on its own canvas, with the WASM from the vendored copy (or the pack). */
async function makeRenderer(assets) {
  let module_or_path = WASM_URL;
  if (assets.offline) module_or_path = new Uint8Array(await assets.bytes(OFFLINE_WASM_KEY));
  const canvas = document.createElement('canvas');
  const renderer = await createGerberRenderer(canvas, {
    wasmModule: wasmGlue,
    wasmInitInput: { module_or_path },
    contextAttributes: { preserveDrawingBuffer: true },
  });
  return { renderer, canvas };
}

/** One side's fab files: {files, grouped, edge, drills: [{name, text, plated}]}. */
async function loadSideFiles(project, side, assets) {
  const layers = (project.pcb?.layers || []).filter((l) => l[side]?.gerber && FAB_KINDS.has(l.kind)
    && !(l.kind === 'copper' && l.side === 'inner'));
  const files = await Promise.all(layers.map(async (l) => {
    const text = await assets.text(l[side].gerber);
    return { name: basename(l[side].gerber), source: text, content: text, layer: l };
  }));
  if (!files.length) return null;
  // KiCad writes a layer with nothing on it (B_SilkS on a board with no bottom silkscreen) as a
  // header-only gerber, which fork main still refuses (fixed in CoolNamesAllTaken/
  // wasm-gerber-viewer#2; drop this filter once that is vendored). Nothing on it is nothing to
  // draw -- except a mask: an empty mask is no openings, mask over the whole board.
  const grouped = groupBoardLayers(files.filter((f) => f.layer.kind === 'drill' || f.layer.kind === 'mask' || hasGeometry(f.content)));
  // A drill file with no holes (KiCad writes a header-only NPTH.drl) is simply no holes.
  const drills = files.filter((f) => f.layer.kind === 'drill').map((f) => ({
    name: f.name, text: f.content, plated: !/^NPTH/i.test(f.layer.id) && !/NPTH/i.test(f.name),
    holes: parseExcellon(f.content, { plated: !/NPTH/i.test(f.name) }),
  })).filter((d) => d.holes.length);
  grouped.drills = grouped.drills.filter((d) => drills.some((x) => x.name === d.name));
  const edge = files.find((f) => f.layer.kind === 'outline') || null;
  return { files, grouped, edge, drills };
}

/**
 * Whether a gerber draws anything: an interpolate (D01), a flash (D03) or a region (G36).
 * Aperture selections are D10 and up, so D1 and D3 (with or without the zero) are always operations.
 */
export function hasGeometry(text) {
  return /D0?[13]\*|G36\*/.test(text);
}

function boardInfo(project, side) {
  const b = project.pcb?.board || {};
  return { ...b, ...(b[side] || {}) };
}

function outlineFor(sideFiles, info) {
  let o = null;
  if (sideFiles?.edge) {
    const size = info.size_mm;
    o = boardOutline(sideFiles.edge.content, size ? { width: size[0], height: size[1] } : {});
  }
  if (o && o.outer && o.outer.length >= 3) return { board: o.outer, cutouts: o.holes || [], approximate: false };
  return info.origin_mm && info.size_mm ? rectOutline(info) : null;
}

function texture(canvas) {
  const t = new THREE.CanvasTexture(canvas);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  t.generateMipmaps = true;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  return t;
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
 * where a side is {group, body, barrels, outline, materials: {top, bottom, walls}, textures,
 * holes, thickness, approximate}.
 */
export async function buildGerberBoards(project, assets, { onStatus = () => {}, maxTextureSize = MAX_PX } = {}) {
  if (!project.pcb?.layers?.length) return null;
  const files = {};
  for (const side of ['base', 'head']) {
    files[side] = project.pcba3d?.[side] || project.pcb.board?.[side] || project.status !== (side === 'base' ? 'added' : 'removed')
      ? await loadSideFiles(project, side, assets).catch(() => null) : null;
  }
  if (!files.base && !files.head) return null;
  const outlines = {};
  for (const side of ['base', 'head']) outlines[side] = files[side] ? outlineFor(files[side], boardInfo(project, side)) : null;
  const present = ['base', 'head'].filter((s) => outlines[s]);
  if (!present.length) return null;

  // One frame for every picture of either side, so any texture fits any face.
  const b = present.map((s) => loopBounds(outlines[s].board));
  const pad = 0.5;
  const bounds = {
    minX: Math.min(...b.map((x) => x.minX)) - pad, maxX: Math.max(...b.map((x) => x.maxX)) + pad,
    minY: Math.min(...b.map((x) => x.minY)) - pad, maxY: Math.max(...b.map((x) => x.maxY)) + pad,
  };
  const size = faceRasterSize(bounds, { pxPerMm: PX_PER_MM, maxPx: maxTextureSize, maxTextureSize });

  const { renderer } = await makeRenderer(assets);
  const disposables = [];
  const sides = {};
  let view = null;
  try {
    for (const side of present) {
      const info = boardInfo(project, side);
      const thickness = Number(info.thickness_mm) > 0 ? Number(info.thickness_mm) : 1.6;
      const textures = {};
      for (const face of ['top', 'bottom']) {
        onStatus(`Painting the ${side} board (${face})…`);
        const options = { bounds, side: face, width: size.width, height: size.height, palette: palette(info) };
        let r;
        try { r = await renderFaceRaster(renderer, files[side].grouped, options); } catch (e) {
          // An unknown colour name in the stackup must not cost the whole board.
          if (!options.palette || !Object.keys(options.palette).length) throw e;
          r = await renderFaceRaster(renderer, files[side].grouped, { ...options, palette: {} });
        }
        view = view || r.view;
        textures[face] = texture(copyScaled(r.canvas, maxTextureSize));
        disposables.push(textures[face]);
      }
      const holes = files[side].drills.flatMap((d) => d.holes.map((h) => ({ ...h, plated: d.plated })));
      const geo = boardGeometry(outlines[side], holes, thickness, bounds);
      const materials = {
        top: new THREE.MeshStandardMaterial({ map: textures.top, roughness: 0.55, metalness: 0.05 }),
        bottom: new THREE.MeshStandardMaterial({ map: textures.bottom, roughness: 0.55, metalness: 0.05 }),
        walls: new THREE.MeshStandardMaterial({ color: FR4, roughness: 0.9 }),
        barrels: new THREE.MeshStandardMaterial({ color: COPPER, roughness: 0.45, metalness: 0.65 }),
      };
      const body = new THREE.Mesh(geo.body, [materials.top, materials.bottom, materials.walls]);
      body.name = `gerber-board-${side}`;
      body.userData.boardKind = 'substrate';
      const group = new THREE.Group();
      group.name = `gerber-${side}`;
      group.add(body);
      let barrels = null;
      if (geo.barrels) {
        barrels = new THREE.Mesh(geo.barrels, materials.barrels);
        barrels.name = `gerber-barrels-${side}`;
        group.add(barrels);
      }
      disposables.push(geo.body, geo.barrels, ...Object.values(materials));
      sides[side] = { group, body, barrels, outline: outlines[side], materials, textures, holes: geo.holes, thickness, approximate: !!outlines[side].approximate };
    }
  } catch (e) {
    renderer.dispose?.();
    throw e;
  }

  // The base outline as a ghost edge on the head board, when the two differ.
  const outlineChanged = !!(outlines.base && outlines.head && outlinesDiffer(outlines.base, outlines.head));
  let ghost = null;
  if (outlineChanged) {
    ghost = new THREE.Group();
    ghost.name = 'base-outline-ghost';
    const material = new THREE.LineBasicMaterial({ color: 0xe5534b, transparent: true, opacity: 0.9, depthTest: false });
    const t = sides.base.thickness;
    for (const z of [t + 0.03, -0.03]) {
      for (const loop of outlineLoops(outlines.base, z)) {
        const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(loop), material);
        line.renderOrder = 5;
        ghost.add(line);
        disposables.push(line.geometry);
      }
    }
    disposables.push(material);
  }

  // Copper diff pictures, on demand (only the overlay modes use them).
  let diffPromise = null;
  const diffTextures = () => {
    if (diffPromise) return diffPromise;
    diffPromise = (async () => {
      const out = {};
      const pick = (side, face) => {
        const f = files[side];
        if (!f) return null;
        const g = f.grouped[face];
        const list = [g?.copper?.source, f.edge?.content].filter(Boolean);
        for (const d of f.drills) list.push(holesToGerber(d.holes));
        return list.length ? list : null;
      };
      for (const face of ['top', 'bottom']) {
        onStatus(`Diffing the ${face} copper…`);
        await renderLayerDiff(renderer, { base: pick('base', face), head: pick('head', face) }, {
          width: size.width, height: size.height, view, background: DIFF_BACKGROUND, showUnchanged: true, style: DIFF_STYLE,
        });
        out[face] = texture(copyScaled(renderer.canvas || renderer.gl?.canvas, maxTextureSize));
        disposables.push(out[face]);
      }
      return out;
    })();
    return diffPromise;
  };

  return {
    bounds, size, sides, outlineChanged, ghost, diffTextures,
    dispose() {
      for (const d of disposables) d?.dispose?.();
      renderer.dispose?.();
    },
  };
}
