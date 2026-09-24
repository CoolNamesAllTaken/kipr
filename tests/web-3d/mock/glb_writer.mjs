// Minimal binary glTF (GLB) writer for synthetic test boards. No dependencies.
//
// Builds a scene the way `kicad-cli pcb export glb` (OCCT's RWGltf_CafWriter) lays one out:
// metres, y up, a root node holding the board bodies and one node per component instance,
// meshes shared between instances of the same model.

const FLOAT = 5126, UINT32 = 5125, ARRAY_BUFFER = 34962, ELEMENT_ARRAY_BUFFER = 34963;

export class GlbBuilder {
  constructor() {
    this.json = {
      asset: { version: '2.0', generator: 'kipr tests/web-3d mock' },
      scene: 0, scenes: [{ nodes: [] }], nodes: [], meshes: [], materials: [],
      accessors: [], bufferViews: [], buffers: [{ byteLength: 0 }],
    };
    this.chunks = [];
    this.length = 0;
    this.materialIds = new Map();
  }

  _view(bytes, target) {
    const pad = (4 - (this.length % 4)) % 4;
    if (pad) { this.chunks.push(new Uint8Array(pad)); this.length += pad; }
    const view = { buffer: 0, byteOffset: this.length, byteLength: bytes.byteLength, target };
    this.chunks.push(new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength));
    this.length += bytes.byteLength;
    this.json.bufferViews.push(view);
    return this.json.bufferViews.length - 1;
  }

  _accessor(array, type, componentType, target, minmax = false) {
    const count = array.length / { SCALAR: 1, VEC3: 3 }[type];
    const acc = { bufferView: this._view(array, target), componentType, count, type };
    if (minmax) {
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < array.length; i += 3) for (let k = 0; k < 3; k++) {
        min[k] = Math.min(min[k], array[i + k]); max[k] = Math.max(max[k], array[i + k]);
      }
      acc.min = min; acc.max = max;
    }
    this.json.accessors.push(acc);
    return this.json.accessors.length - 1;
  }

  material(rgba, { metallic = 0, roughness = 0.6, name } = {}) {
    const key = rgba.join(',') + metallic + roughness;
    if (this.materialIds.has(key)) return this.materialIds.get(key);
    this.json.materials.push({
      name, pbrMetallicRoughness: { baseColorFactor: rgba, metallicFactor: metallic, roughnessFactor: roughness },
      ...(rgba[3] < 1 ? { alphaMode: 'BLEND' } : {}),
    });
    this.materialIds.set(key, this.json.materials.length - 1);
    return this.json.materials.length - 1;
  }

  /**
   * A mesh of axis-aligned boxes: boxes = [{min:[x,y,z], max:[x,y,z], material}]. Boxes sharing
   * a material are merged into one primitive (like OCCT does per colour).
   */
  boxMesh(name, boxes) {
    const byMaterial = new Map();
    for (const b of boxes) {
      if (!byMaterial.has(b.material)) byMaterial.set(b.material, []);
      byMaterial.get(b.material).push(b);
    }
    const primitives = [...byMaterial].map(([material, list]) => {
      const parts = list.map((b) => boxGeometry(b.min, b.max));
      const positions = new Float32Array(parts.reduce((n, p) => n + p.positions.length, 0));
      const normals = new Float32Array(positions.length);
      const indices = new Uint32Array(parts.reduce((n, p) => n + p.indices.length, 0));
      let pv = 0, pi = 0;
      for (const p of parts) {
        positions.set(p.positions, pv); normals.set(p.normals, pv);
        for (let i = 0; i < p.indices.length; i++) indices[pi + i] = p.indices[i] + pv / 3;
        pv += p.positions.length; pi += p.indices.length;
      }
      return {
        attributes: {
          POSITION: this._accessor(positions, 'VEC3', FLOAT, ARRAY_BUFFER, true),
          NORMAL: this._accessor(normals, 'VEC3', FLOAT, ARRAY_BUFFER),
        },
        indices: this._accessor(indices, 'SCALAR', UINT32, ELEMENT_ARRAY_BUFFER),
        material,
      };
    });
    this.json.meshes.push({ name, primitives });
    return this.json.meshes.length - 1;
  }

  node(spec, parent = null) {
    this.json.nodes.push(spec);
    const i = this.json.nodes.length - 1;
    if (parent === null) this.json.scenes[0].nodes.push(i);
    else (this.json.nodes[parent].children ||= []).push(i);
    return i;
  }

  toBuffer() {
    const pad = (4 - (this.length % 4)) % 4;
    if (pad) { this.chunks.push(new Uint8Array(pad)); this.length += pad; }
    this.json.buffers[0].byteLength = this.length;
    let jsonBytes = Buffer.from(JSON.stringify(this.json), 'utf8');
    const jpad = (4 - (jsonBytes.length % 4)) % 4;
    jsonBytes = Buffer.concat([jsonBytes, Buffer.alloc(jpad, 0x20)]);
    const bin = Buffer.concat(this.chunks.map((c) => Buffer.from(c.buffer, c.byteOffset, c.byteLength)));
    const header = Buffer.alloc(12);
    header.writeUInt32LE(0x46546c67, 0);
    header.writeUInt32LE(2, 4);
    header.writeUInt32LE(12 + 8 + jsonBytes.length + 8 + bin.length, 8);
    const jh = Buffer.alloc(8); jh.writeUInt32LE(jsonBytes.length, 0); jh.writeUInt32LE(0x4e4f534a, 4);
    const bh = Buffer.alloc(8); bh.writeUInt32LE(bin.length, 0); bh.writeUInt32LE(0x004e4942, 4);
    return Buffer.concat([header, jh, jsonBytes, bh, bin]);
  }
}

function boxGeometry(min, max) {
  const [x0, y0, z0] = min, [x1, y1, z1] = max;
  const faces = [
    [[1, 0, 0], [[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]]],
    [[-1, 0, 0], [[x0, y0, z1], [x0, y1, z1], [x0, y1, z0], [x0, y0, z0]]],
    [[0, 1, 0], [[x0, y1, z0], [x0, y1, z1], [x1, y1, z1], [x1, y1, z0]]],
    [[0, -1, 0], [[x0, y0, z1], [x0, y0, z0], [x1, y0, z0], [x1, y0, z1]]],
    [[0, 0, 1], [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]],
    [[0, 0, -1], [[x1, y0, z0], [x0, y0, z0], [x0, y1, z0], [x1, y1, z0]]],
  ];
  const positions = [], normals = [], indices = [];
  faces.forEach(([n, quad], f) => {
    for (const p of quad) { positions.push(...p); normals.push(...n); }
    const b = f * 4;
    indices.push(b, b + 1, b + 2, b, b + 2, b + 3);
  });
  return { positions: new Float32Array(positions), normals: new Float32Array(normals), indices: new Uint32Array(indices) };
}
