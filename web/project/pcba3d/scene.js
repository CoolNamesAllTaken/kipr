// Loading one side's GLB and turning it into something the viewer can reason about: a board
// (substrate / mask / copper / silk), and components keyed by reference designator.
//
// kicad-cli's GLB comes out of OCCT's glTF writer: y-up and in metres, as glTF says, where KiCad
// is z-up and in millimetres. Rather than assume, this measures: the board is by far the flattest
// thing in the file, so its thinnest axis is "up", and a board a few metres across is a board a
// few millimetres across in the wrong unit. The result is placed in the viewer's board frame
// (mm, x right, y up, z out of the top) with the contract's component coordinates landing on the
// components -- the translation between the two is fitted in match.js, so it does not matter
// which origin the export used, and base and head line up even if they used different ones.

import * as THREE from './vendor/three/three.module.js';
import { GLTFLoader } from './vendor/three/addons/GLTFLoader.js';
import { mergeGeometries } from './vendor/three/utils/BufferGeometryUtils.js';
import { mapNodesToRefs, refFromName } from './match.js';

/** Fetch with byte progress: onProgress(loaded, total|0). */
export async function fetchBytes(url, onProgress = () => {}, signal) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  const total = Number(response.headers.get('content-length')) || 0;
  if (!response.body || !response.body.getReader) {
    const buf = await response.arrayBuffer();
    onProgress(buf.byteLength, buf.byteLength);
    return buf;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onProgress(loaded, total);
  }
  const out = new Uint8Array(loaded);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.byteLength; }
  return out.buffer;
}

export function parseGlb(buffer) {
  return new Promise((resolve, reject) => {
    new GLTFLoader().parse(buffer, '', resolve, (e) => reject(e instanceof Error ? e : new Error(String(e?.message || e))));
  });
}

// Board parts by the names KiCad's exporter gives them ("<board>_PCB", "_soldermask",
// "_silkscreen", "_copper", pads/tracks/vias/zones). Only applied to leaf nodes: a root node
// called "my-board" holds everything and is not the substrate.
const BOARD_KINDS = [
  ['silk', /silk/i],
  ['mask', /mask/i],
  ['copper', /copper|\bcu\b|[_\s-]cu$|pad|track|via|zone|plating/i],
  ['substrate', /(^|[_\s-])(pcb|board|substrate|core|fr-?4)($|[_\s-])/i],
];

export function boardKindFromName(name) {
  if (!name) return null;
  for (const [kind, re] of BOARD_KINDS) if (re.test(name)) return kind;
  return null;
}

/**
 * The names a node goes by: its own and its first mesh's. KiCad 10 leaves board bodies' nodes
 * unnamed ("=>[0:1:1:84]") and puts the kind in the mesh name ("pic_programmer_silkscreen").
 */
function namesOf(node, meshName) {
  const names = [node.name];
  const mesh = node.isMesh ? node : node.children.find((c) => c.isMesh);
  if (mesh && mesh !== node) names.push(mesh.name);
  // A single-primitive mesh becomes the node's own object and takes the node's name, so the
  // glTF mesh name is only to be had from the loader's associations.
  if (mesh) names.push(meshName(mesh), meshName(node));
  if (node.userData?.name) names.push(node.userData.name);
  return names.filter(Boolean).join(' ');
}

/**
 * Collapse everything under `obj` into one mesh per material, in `obj`'s own frame.
 *
 * kicad-cli writes one glTF primitive per face set: a small board's pads alone came to 3914
 * primitives, the whole of pic_programmer to ~7000 -- each a draw call, twice over for base and
 * head. Merged, a component is a handful of meshes and a board layer is one. Returns the object
 * that replaces `obj` in its parent (a Group carrying obj's name and transform).
 */
export function mergeObject(obj) {
  obj.updateMatrixWorld(true);
  const inverse = obj.matrixWorld.clone().invert();
  const groups = new Map();
  obj.traverse((n) => {
    if (!n.isMesh || !n.geometry?.attributes?.position) return;
    const material = Array.isArray(n.material) ? n.material[0] : n.material;
    let g = n.geometry.clone();
    for (const name of Object.keys(g.attributes)) if (name !== 'position' && name !== 'normal') g.deleteAttribute(name);
    g.morphAttributes = {};
    if (!g.attributes.normal) g.computeVertexNormals();
    if (!g.index) {
      const count = g.attributes.position.count;
      const index = new (count > 65535 ? Uint32Array : Uint16Array)(count);
      for (let i = 0; i < count; i++) index[i] = i;
      g.setIndex(new THREE.BufferAttribute(index, 1));
    }
    g.clearGroups();
    g.applyMatrix4(new THREE.Matrix4().multiplyMatrices(inverse, n.matrixWorld));
    const key = material ? material.uuid : 'none';
    if (!groups.has(key)) groups.set(key, { material, list: [] });
    groups.get(key).list.push(g);
  });
  const out = new THREE.Group();
  out.name = obj.name;
  out.userData = { ...obj.userData };
  out.position.copy(obj.position);
  out.quaternion.copy(obj.quaternion);
  out.scale.copy(obj.scale);
  for (const { material, list } of groups.values()) {
    const merged = list.length === 1 ? list[0] : mergeGeometries(list, false);
    if (!merged) continue;
    for (const g of list) if (g !== merged) g.dispose();
    merged.computeBoundingSphere();
    merged.computeBoundingBox();
    const mesh = new THREE.Mesh(merged, material || new THREE.MeshStandardMaterial({ color: 0x9aa4ae }));
    mesh.name = obj.name;
    out.add(mesh);
  }
  // The originals' geometry is no longer referenced by the scene (shared meshes may still be
  // used by other instances, which are merged from their own clones; the GC takes the rest).
  const parent = obj.parent;
  if (parent) {
    const i = parent.children.indexOf(obj);
    parent.children[i] = out;
    out.parent = parent;
    obj.parent = null;
  }
  out.updateMatrixWorld(true);
  return out;
}

function hasMesh(node) {
  let found = false;
  node.traverse((n) => { if (n.isMesh) found = true; });
  return found;
}

/** A node that is one body: a mesh, or a group of nothing but meshes (a multi-primitive mesh). */
function isLeafish(node) {
  return node.isMesh || (node.children.length > 0 && node.children.every((c) => c.isMesh && c.children.length === 0));
}

function firstColor(node) {
  let color = null;
  node.traverse((n) => {
    if (color || !n.isMesh) return;
    const m = Array.isArray(n.material) ? n.material[0] : n.material;
    if (m && m.color) color = m.color;
  });
  return color;
}

/** A board-sized body with no name to go by: tell the layers apart by colour and thickness. */
function boardKindFromLook(node, box) {
  const c = firstColor(node);
  const thick = box.max.z - box.min.z;
  if (thick > 0.3) return 'substrate';
  if (!c) return 'substrate';
  const hsl = {};
  c.getHSL(hsl);
  if (hsl.l > 0.75 && hsl.s < 0.3) return 'silk';
  if (hsl.h > 0.06 && hsl.h < 0.17 && hsl.s > 0.35) return 'copper';
  return 'mask';
}

function worldBox(obj) {
  return new THREE.Box3().setFromObject(obj);
}

function orient(inner, boardSizeMm, frame) {
  inner.updateMatrixWorld(true);
  const box = worldBox(inner);
  const size = box.getSize(new THREE.Vector3());
  // Thinnest axis is up, unless the backend says (pcba3d.frame.up). glTF's y-up is the usual
  // answer; a z-up file (hand-made, or a future exporter) is left alone.
  const up = frame?.up ? String(frame.up).replace('+', '') : null;
  if (up === 'y' || (!up && size.y <= size.x && size.y <= size.z)) inner.rotation.x = Math.PI / 2;  // (x,y,z) -> (x,-z,y)
  else if (up === 'x' || (!up && size.x < size.y && size.x < size.z)) inner.rotation.y = -Math.PI / 2;
  inner.updateMatrixWorld(true);
  const s2 = worldBox(inner).getSize(new THREE.Vector3());
  const span = Math.max(s2.x, s2.y);
  let scale = 1;
  const stated = { m: 1000, mm: 1, cm: 10, in: 25.4 }[frame?.units];
  const expected = boardSizeMm ? Math.max(boardSizeMm[0] || 0, boardSizeMm[1] || 0) : 0;
  if (stated) {
    scale = stated;
  } else if (expected > 0 && span > 0) {
    // Whichever power of ten brings the file's span closest to the board the contract describes.
    let best = Infinity;
    for (const s of [1, 10, 25.4, 1000]) {
      const err = Math.abs(Math.log((span * s) / expected));
      if (err < best) { best = err; scale = s; }
    }
  } else if (span > 0 && span < 2) {
    scale = 1000;                                            // metres; no board is 2 mm across
  }
  inner.scale.setScalar(scale);
  inner.updateMatrixWorld(true);
  return { scale, up: inner.rotation.x ? 'y' : inner.rotation.y ? 'x' : 'z' };
}

/**
 * Build a side.
 *   gltf        the GLTFLoader result (or just its scene; then glTF mesh names are not available)
 *   components  contract components (normalized), each with this side's placement in c[sideName]
 *   board       the contract's pcb.board, or null (used for the unit check and as a fallback origin)
 *   frame       pcba3d.frame ({units, up}) if the backend states it; measured otherwise
 * Returns {root, parts, comps, meshes, report, boardBox, boardMidZ}.
 */
export function prepareSide(gltf, components, sideName, board = null, frame = null) {
  const root = new THREE.Group();
  root.name = `pcba3d-${sideName}`;
  const inner = gltf.scene || gltf;
  const parser = gltf.parser;
  const meshName = (obj) => {
    const idx = parser?.associations?.get(obj)?.meshes;
    return idx === undefined ? '' : parser.json.meshes?.[idx]?.name || '';
  };
  root.add(inner);
  const orientation = orient(inner, board?.size_mm, frame);
  root.updateMatrixWorld(true);

  const mine = components.filter((c) => c[sideName]);
  const refs = new Set(mine.map((c) => c.ref));
  const all = worldBox(inner);
  const allSize = all.getSize(new THREE.Vector3());
  const boardArea = board?.size_mm?.[0] && board?.size_mm?.[1]
    ? board.size_mm[0] * board.size_mm[1] : Math.max(allSize.x * allSize.y, 1e-6);

  const parts = { substrate: [], mask: [], copper: [], silk: [] };
  const candidates = [];

  (function visit(node) {
    if (!hasMesh(node)) return;
    if (node !== inner && refFromName(node.name, refs)) { candidates.push(mergeObject(node)); return; }
    const leaf = isLeafish(node);
    const byName = leaf ? boardKindFromName(namesOf(node, meshName)) : null;
    if (byName) { parts[byName].push(mergeObject(node)); return; }
    const box = worldBox(node);
    const size = box.getSize(new THREE.Vector3());
    const big = size.x * size.y > 0.4 * boardArea;
    if (!big && node !== inner) { candidates.push(mergeObject(node)); return; }
    if (leaf) {
      if (big) parts[boardKindFromLook(node, box)].push(mergeObject(node));
      else candidates.push(mergeObject(node));
      return;
    }
    // A big group: an assembly root, or a board wrapper. Look inside. A mesh the group carries
    // itself (a node with both a mesh and children) is a leaf of its own.
    for (const child of node.children.slice()) visit(child);
  })(inner);

  // Candidate nodes in plain numbers for the matcher: origin and box middle, board frame.
  const v = new THREE.Vector3();
  const nodes = candidates.map((obj) => {
    obj.getWorldPosition(v);
    const b = worldBox(obj);
    const c = b.getCenter(new THREE.Vector3());
    return { name: obj.name, x: v.x, y: v.y, cx: c.x, cy: c.y };
  });

  // Where the contract's origin would be if nothing matches: the board's box against the
  // contract's (origin_mm is the board's top-left corner in KiCad mm).
  let fallbackOffset = null;
  const substrateBox = new THREE.Box3();
  for (const p of parts.substrate) substrateBox.union(worldBox(p));
  if (board?.origin_mm && board?.size_mm && !substrateBox.isEmpty()) {
    const c = substrateBox.getCenter(new THREE.Vector3());
    fallbackOffset = { x: c.x - (board.origin_mm[0] + board.size_mm[0] / 2), y: c.y + (board.origin_mm[1] + board.size_mm[1] / 2) };
  }

  const placements = mine.map((c) => ({ ref: c.ref, x: c[sideName].x, y: c[sideName].y }));
  let match = mapNodesToRefs(nodes, placements, { fallbackOffset });
  // If this exporter's y turns out to run the other way, the mirror image matches far better.
  let mirrored = false;
  if (match.method !== 'name' && placements.length >= 3) {
    const flipped = mapNodesToRefs(nodes.map((n) => ({ ...n, y: -n.y, cy: -n.cy })), placements,
      { fallbackOffset: fallbackOffset && { x: fallbackOffset.x, y: -fallbackOffset.y } });
    if (flipped.byRef.size > match.byRef.size * 1.5 + 1) { match = flipped; mirrored = true; }
  }
  if (mirrored) {
    // Mirror about the board frame's x axis: wrap so the flip happens after the orientation.
    const flip = new THREE.Group();
    root.remove(inner);
    flip.scale.y = -1;
    flip.add(inner);
    root.add(flip);
  }
  root.position.set(-match.offset.x, -match.offset.y, 0);
  root.updateMatrixWorld(true);

  // Components, keyed by ref.
  const byRef = new Map(mine.map((c) => [c.ref, c]));
  const comps = new Map();
  const meshes = [];
  for (const [ref, idx] of match.byRef) {
    const objects = idx.map((i) => candidates[i]);
    const c = byRef.get(ref);
    const entry = { ref, objects, component: c, box: new THREE.Box3(), meshes: [] };
    for (const o of objects) {
      o.userData.ref = ref;
      o.traverse((n) => {
        if (!n.isMesh) return;
        n.userData.ref = ref;
        n.userData.orig = n.material;
        entry.meshes.push(n);
        meshes.push(n);
      });
      entry.box.union(worldBox(o));
      o.userData.home = o.position.clone();
    }
    comps.set(ref, entry);
  }
  const loose = match.leftover.map((i) => candidates[i]);
  for (const o of loose) {
    o.userData.home = o.position.clone();
    o.traverse((n) => { if (n.isMesh) n.userData.orig = n.material; });
  }
  const boardBox = new THREE.Box3();
  for (const kind of Object.keys(parts)) {
    for (const p of parts[kind]) {
      p.userData.boardKind = kind;
      p.userData.home = p.position.clone();
      p.traverse((n) => { if (n.isMesh) { n.userData.orig = n.material; n.userData.boardKind = kind; } });
      if (kind === 'substrate') boardBox.union(worldBox(p));
    }
  }
  if (boardBox.isEmpty()) for (const kind of Object.keys(parts)) for (const p of parts[kind]) boardBox.union(worldBox(p));
  const boardMidZ = boardBox.isEmpty() ? 0 : (boardBox.min.z + boardBox.max.z) / 2;
  for (const entry of comps.values()) {
    const s = entry.component?.[sideName]?.side;
    entry.bottom = s ? s === 'bottom' : entry.box.getCenter(v).z < boardMidZ;
  }

  const report = {
    method: match.method, byName: match.byName, byPosition: match.byPosition,
    matched: comps.size, expected: mine.length, ambiguous: match.ambiguous, unmatched: match.unmatched,
    loose: loose.length, offset: match.offset, mirrored, ...orientation,
    boardParts: Object.fromEntries(Object.entries(parts).map(([k, l]) => [k, l.length])),
  };
  return { root, parts, comps, loose, meshes, report, boardBox, boardMidZ, bounds: worldBox(root) };
}

/** Free a side's GPU memory. */
export function disposeObject(root) {
  root.traverse((n) => {
    if (n.geometry) n.geometry.dispose();
    const mats = [n.material, n.userData?.orig].flat().filter(Boolean);
    for (const m of mats) {
      for (const key of Object.keys(m)) if (m[key] && m[key].isTexture) m[key].dispose();
      m.dispose?.();
    }
  });
}
