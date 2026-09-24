#!/usr/bin/env node
// Synthetic OUT dir for the 3D PCBA viewer: project-review.json + base/head GLBs per project.
//
//   node tests/web-3d/mock/make_mock.mjs [OUT]      (default: tests/web-3d/out/mock)
//
// The GLBs mimic `kicad-cli pcb export glb` (KiCad 10.0.6, checked against a real export of the
// pic_programmer demo): metres, y up, x = KiCad x, z = KiCad y, one root node holding a node per
// component named by its reference (the model mesh is a child node), and the board bodies as
// nodes named "=>[0:1:1:N]" whose mesh names carry the kind ("<board>_PCB", "_pad",
// "_soldermask", "_silkscreen"). Everything is synthetic; no real design is involved.
//
// Projects:
//   demo       ~70 parts, every change kind, page origin (like kicad-cli's default)
//   big        ~480 parts on a 160x100 board, for smoothness
//   unnamed    component nodes carry model names instead of refs and the export origin is the
//              board centre: exercises position matching and the origin fit
//   newboard   status "added": no base side

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { GlbBuilder } from './glb_writer.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(process.argv[2] || join(HERE, '..', 'out', 'mock'));

function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

// Part kinds: body size (mm, x along the footprint, y across, z height), colours, model name.
const KINDS = {
  R: { fp: 'Resistor_SMD:R_0603_1608Metric', model: 'R_0603_1608Metric', size: [1.6, 0.8, 0.45], body: [0.1, 0.1, 0.1, 1], values: ['10k', '4.7k', '100R', '1k', '22R'] },
  R8: { fp: 'Resistor_SMD:R_0805_2012Metric', model: 'R_0805_2012Metric', size: [2.0, 1.25, 0.5], body: [0.1, 0.1, 0.1, 1], values: ['10k'] },
  C: { fp: 'Capacitor_SMD:C_0603_1608Metric', model: 'C_0603_1608Metric', size: [1.6, 0.8, 0.8], body: [0.72, 0.58, 0.36, 1], values: ['100n', '1u', '10u', '22p'] },
  U: { fp: 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm', model: 'SOIC-8_3.9x4.9mm_P1.27mm', size: [4.9, 3.9, 1.5], body: [0.15, 0.15, 0.17, 1], values: ['LM358', 'NE555', 'TL072'], pins: 4 },
  Q: { fp: 'Package_TO_SOT_SMD:SOT-23', model: 'SOT-23', size: [2.9, 1.3, 1.0], body: [0.15, 0.15, 0.17, 1], values: ['BSS138', 'MMBT3904'], pins: 2 },
  D: { fp: 'LED_SMD:LED_0805_2012Metric', model: 'LED_0805_2012Metric', size: [2.0, 1.25, 0.8], body: [0.95, 0.95, 0.9, 1], values: ['RED', 'GREEN'] },
  J: { fp: 'Connector_JST:JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical', model: 'JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical', size: [9.9, 4.5, 6.0], body: [0.93, 0.93, 0.88, 1], values: ['Conn_01x04'], offset: [3.0, 1.2] },
};
const PIN = [0.78, 0.78, 0.8, 1];

/** Components on a grid: [{ref, kind, x, y, rot, side, value}], KiCad mm (y down). */
function layout({ seed, cols, rows, origin, pitch, bottomEvery = 7 }) {
  const rand = rng(seed);
  const counters = {};
  const out = [];
  const kinds = ['R', 'R', 'C', 'C', 'C', 'R', 'U', 'Q', 'D', 'C', 'R', 'J'];
  let n = 0;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++, n++) {
      const kind = kinds[Math.floor(rand() * kinds.length)];
      const prefix = kind === 'R8' ? 'R' : kind;
      counters[prefix] = (counters[prefix] || 0) + 1;
      const k = KINDS[kind];
      out.push({
        ref: `${prefix}${counters[prefix]}`, kind,
        x: +(origin[0] + pitch[0] * (c + 0.5) + (rand() - 0.5) * 0.6).toFixed(3),
        y: +(origin[1] + pitch[1] * (r + 0.5) + (rand() - 0.5) * 0.6).toFixed(3),
        rot: [0, 90, 180, 270][Math.floor(rand() * 4)],
        side: kind !== 'J' && n % bottomEvery === 3 ? 'bottom' : 'top',
        value: k.values[Math.floor(rand() * k.values.length)],
      });
    }
  }
  return out;
}

const place = (c) => ({ x: c.x, y: c.y, rot: c.rot, side: c.side, footprint: KINDS[c.kind].fp, value: c.value, model: `\${KICAD10_3DMODEL_DIR}/${KINDS[c.kind].model}.step` });

/** Apply a list of edits to a copy of `base` and build the contract's component list. */
function diffComponents(base, head) {
  const byRefB = new Map(base.map((c) => [c.ref, c]));
  const byRefH = new Map(head.map((c) => [c.ref, c]));
  const refs = [...new Set([...byRefB.keys(), ...byRefH.keys()])];
  return refs.map((ref) => {
    const b = byRefB.get(ref), h = byRefH.get(ref);
    const what = [];
    if (b && h) {
      if (b.x !== h.x || b.y !== h.y) what.push('position');
      if (b.rot !== h.rot) what.push('rotation');
      if (b.kind !== h.kind) what.push('footprint', 'model');
      if (b.value !== h.value) what.push('value');
      if (b.side !== h.side) what.push('side');
    }
    let status = !b ? 'added' : !h ? 'removed' : 'unchanged';
    if (b && h) {
      if (what.some((w) => ['footprint', 'value', 'model', 'side'].includes(w))) status = 'changed';
      else if (what.includes('position')) status = 'moved';
      else if (what.includes('rotation')) status = 'rotated';
    }
    return { ref, status, base: b ? place(b) : null, head: h ? place(h) : null, what };
  });
}

function edit(base, seed) {
  const rand = rng(seed);
  const head = base.map((c) => ({ ...c }));
  const pick = (pred) => {
    for (let tries = 0; tries < 500; tries++) {
      const c = head[Math.floor(rand() * head.length)];
      if (!c.touched && pred(c)) { c.touched = true; return c; }
    }
    return null;
  };
  pick((c) => c.kind === 'C').removed = true;
  pick((c) => c.kind === 'R').removed = true;
  for (let i = 0; i < 3; i++) { const c = pick((c) => c.kind !== 'J'); c.x = +(c.x + 1.5 + i).toFixed(3); c.y = +(c.y - 1.0).toFixed(3); }
  for (let i = 0; i < 2; i++) { const c = pick((c) => c.kind === 'U' || c.kind === 'Q' || c.kind === 'D'); c.rot = (c.rot + 90) % 360; }
  for (let i = 0; i < 2; i++) { const c = pick((c) => c.kind === 'C' || c.kind === 'R'); const vs = KINDS[c.kind].values; c.value = vs[(vs.indexOf(c.value) + 1) % vs.length]; }
  const fp = pick((c) => c.kind === 'R'); fp.kind = 'R8';
  const flip = pick((c) => c.kind === 'C' && c.side === 'top'); flip.side = 'bottom';
  const out = head.filter((c) => !c.removed).map(({ touched, removed, ...c }) => c);
  // Two new parts in the gaps between grid cells.
  const a = base[Math.floor(base.length / 3)], b = base[Math.floor((2 * base.length) / 3)];
  out.push({ ref: 'C900', kind: 'C', x: +(a.x + 3.2).toFixed(3), y: +(a.y + 3.2).toFixed(3), rot: 0, side: 'top', value: '4.7u' });
  out.push({ ref: 'R900', kind: 'R', x: +(b.x + 3.2).toFixed(3), y: +(b.y + 3.2).toFixed(3), rot: 90, side: 'top', value: '0R' });
  return out;
}

// glTF frame: (x, y, z) = (kicad x, height, kicad y) in metres. Rotation about +Y by the KiCad
// angle is counter-clockwise seen from above; the bottom side is a half-turn about x first.
function quat(rotDeg, bottom) {
  const t = (rotDeg * Math.PI) / 180;
  const s = Math.sin(t / 2), c = Math.cos(t / 2);
  return bottom ? [c, 0, -s, 0] : [0, s, 0, c];
}

function buildGlb(board, parts, { named = true, origin = [0, 0], name = 'mock' } = {}) {
  const g = new GlbBuilder();
  const M = 0.001, T = 1.6;
  const root = g.node({});
  const meshFor = new Map();
  const matBody = new Map();
  const mat = (rgba, opts) => g.material(rgba, opts);
  const pinMat = mat(PIN, { metallic: 0.8, roughness: 0.3 });

  function modelMesh(kindName) {
    if (meshFor.has(kindName)) return meshFor.get(kindName);
    const k = KINDS[kindName];
    const [L, W, H] = k.size.map((v) => v * M);
    const [ox, oz] = (k.offset || [0, 0]).map((v) => v * M);
    if (!matBody.has(kindName)) matBody.set(kindName, mat(k.body));
    const boxes = [{ min: [-L / 2 + ox, 0, -W / 2 + oz], max: [L / 2 + ox, H, W / 2 + oz], material: matBody.get(kindName) }];
    const pins = k.pins || 1;
    for (let i = 0; i < pins; i++) {
      const px = pins === 1 ? 0 : (-L / 2 + (L * (i + 0.5)) / pins);
      if (pins === 1) {
        boxes.push({ min: [-L / 2 - 0.1 * M, 0, -W / 2 + 0.05 * M], max: [-L / 2 + 0.3 * M, H * 0.6, W / 2 - 0.05 * M], material: pinMat });
        boxes.push({ min: [L / 2 - 0.3 * M, 0, -W / 2 + 0.05 * M], max: [L / 2 + 0.1 * M, H * 0.6, W / 2 - 0.05 * M], material: pinMat });
      } else {
        for (const sgn of [-1, 1]) boxes.push({ min: [px - 0.2 * M, 0, sgn > 0 ? W / 2 : -W / 2 - 0.9 * M], max: [px + 0.2 * M, 0.25 * M, sgn > 0 ? W / 2 + 0.9 * M : -W / 2], material: pinMat });
      }
    }
    const i = g.boxMesh(k.model, boxes);
    meshFor.set(kindName, i);
    return i;
  }

  for (const c of parts) {
    const bottom = c.side === 'bottom';
    const node = g.node({
      name: named ? c.ref : KINDS[c.kind].model,
      translation: [(c.x - origin[0]) * M, (bottom ? 0 : T) * M, (c.y - origin[1]) * M],
      rotation: quat(c.rot, bottom),
    }, root);
    g.node({ name: `=>[0:1:1:${node}]`, mesh: modelMesh(c.kind) }, node);
  }

  // Board bodies, in board coordinates like KiCad writes them (no node transform).
  const [bx, by] = board.origin_mm.map((v, i) => (v - origin[i]) * M);
  const [bw, bh] = board.size_mm.map((v) => v * M);
  const body = (label, boxes) => g.node({ name: `=>[0:1:1:${900 + g.json.nodes.length}]`, mesh: g.boxMesh(`${name}_${label}`, boxes) }, root);
  body('PCB', [{ min: [bx, 0, by], max: [bx + bw, 1.51 * M, by + bh], material: mat([0.42, 0.45, 0.29, 0.98]) }]);
  const mask = mat([0.063, 0.16, 0.113, 0.83]);
  body('soldermask', [{ min: [bx, 1.55 * M, by], max: [bx + bw, 1.56 * M, by + bh], material: mask }]);
  body('soldermask', [{ min: [bx, -0.05 * M, by], max: [bx + bw, -0.04 * M, by + bh], material: mask }]);
  const pad = mat([0.5, 0.5, 0.5, 1], { metallic: 0.9, roughness: 0.3 });
  const silk = mat([0.96, 0.96, 0.96, 1]);
  const pads = [], silkTop = [], silkBot = [];
  for (const c of parts) {
    const k = KINDS[c.kind];
    const bottom = c.side === 'bottom';
    const x = (c.x - origin[0]) * M, z = (c.y - origin[1]) * M;
    const along = c.rot % 180 === 0;
    const [L, W] = along ? [k.size[0], k.size[1]] : [k.size[1], k.size[0]];
    const y0 = bottom ? -0.04 * M : 1.51 * M, y1 = bottom ? 0 : 1.55 * M;
    const e = 0.5 * M;
    pads.push({ min: [x - (L / 2) * M - e, y0, z - (W / 2) * M], max: [x - (L / 2) * M + e, y1, z + (W / 2) * M], material: pad });
    pads.push({ min: [x + (L / 2) * M - e, y0, z - (W / 2) * M], max: [x + (L / 2) * M + e, y1, z + (W / 2) * M], material: pad });
    const sy = bottom ? -0.08 * M : 1.58 * M;
    (bottom ? silkBot : silkTop).push({ min: [x - (L / 2) * M, sy, z - (W / 2) * M - 0.45 * M], max: [x + (L / 2) * M, sy + 0.001 * M, z - (W / 2) * M - 0.3 * M], material: silk });
  }
  // Board name silkscreen: a row of blocks near the edge.
  for (let i = 0; i < 12; i++) silkTop.push({ min: [bx + (2 + i * 1.6) * M, 1.58 * M, by + 2 * M], max: [bx + (3.2 + i * 1.6) * M, 1.581 * M, by + 4 * M], material: silk });
  body('pad', pads);
  body('silkscreen', silkTop);
  if (silkBot.length) body('silkscreen', silkBot);
  return g.toBuffer();
}

function project(slug, { board, base, head, named = true, origin = [0, 0], status = 'modified' }) {
  const dir = join(OUT, 'p', slug, '3d');
  mkdirSync(dir, { recursive: true });
  const sides = {};
  for (const [k, parts] of [['base', base], ['head', head]]) {
    if (!parts) { sides[k] = null; continue; }
    writeFileSync(join(dir, `${k}.glb`), buildGlb(board, parts, { named, origin, name: slug }));
    sides[k] = { glb: `p/${slug}/3d/${k}.glb` };
  }
  const components = diffComponents(base || [], head || []);
  const counts = { added: 0, removed: 0, moved: 0, changed: 0 };
  for (const c of components) if (c.status in counts) counts[c.status]++;
  return {
    slug, name: slug, path: `boards/${slug}`, status,
    summary: { components: counts },
    schematic: null,
    pcb: { board: { size_mm: board.size_mm, origin_mm: board.origin_mm, thickness_mm: 1.6, copper_layers: 2 }, layers: [], changes: [] },
    pcba3d: { base: sides.base, head: sides.head, components },
    bom: null, netlist: null, checks: { erc: null, drc: null }, errors: [],
  };
}

mkdirSync(OUT, { recursive: true });
const demoBoard = { origin_mm: [100, 60], size_mm: [80, 55] };
const demoBase = layout({ seed: 7, cols: 10, rows: 7, origin: demoBoard.origin_mm, pitch: [8, 7.8] });
const bigBoard = { origin_mm: [50, 40], size_mm: [160, 100] };
const bigBase = layout({ seed: 11, cols: 24, rows: 20, origin: bigBoard.origin_mm, pitch: [160 / 24, 5] });
const unBoard = { origin_mm: [120, 80], size_mm: [60, 40] };
const unBase = layout({ seed: 3, cols: 8, rows: 5, origin: unBoard.origin_mm, pitch: [7.5, 8] });
const newBoard = { origin_mm: [0, 0], size_mm: [40, 30] };

const review = {
  version: 1,
  tool: { name: 'kipr', version: '0.1.0', kicad: 'mock' },
  base: { sha: '0'.repeat(40), ref: 'main', short: 'base000' },
  head: { sha: '1'.repeat(40), ref: 'feature', short: 'head111' },
  repo: { url: 'https://example.invalid/mock', blob: null },
  projects: [
    project('demo', { board: demoBoard, base: demoBase, head: edit(demoBase, 5) }),
    project('big', { board: bigBoard, base: bigBase, head: edit(bigBase, 9) }),
    project('unnamed', { board: unBoard, base: unBase, head: edit(unBase, 2), named: false, origin: [150, 100] }),
    project('newboard', { board: newBoard, base: null, head: layout({ seed: 1, cols: 4, rows: 3, origin: [0, 0], pitch: [10, 10] }), status: 'added' }),
  ],
};
writeFileSync(join(OUT, 'project-review.json'), JSON.stringify(review, null, 1));
console.log(`mock OUT written to ${OUT}`);
