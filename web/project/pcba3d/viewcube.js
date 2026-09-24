// The view cube: a labelled cube in the corner that says which way up you are, and takes you
// there when a face is clicked.
//
// Ported from gentoo's viewer3d.js (PantsForBirds/internal, branch john/gentoo,
// fab/static/fab/viewer3d.js, "The view cube"): drawn into the same canvas as a second pass with
// its own viewport and a cleared depth buffer rather than a second canvas -- one WebGL context,
// one resize. It carries the main camera's rotation and none of its position. The faces are
// labelled because on a PCB the question is nearly always "which side am I looking at".

import * as THREE from './vendor/three/three.module.js';

export const CUBE_PX = 84;
const MARGIN = 8;

// Board frame: z up, y up the screen in the top view.
export const FACES = {
  Top: { dir: [0, 0, 1], up: [0, 1, 0] },
  Bottom: { dir: [0, 0, -1], up: [0, 1, 0] },
  Front: { dir: [0, -1, 0], up: [0, 0, 1] },
  Back: { dir: [0, 1, 0], up: [0, 0, 1] },
  Right: { dir: [1, 0, 0], up: [0, 0, 1] },
  Left: { dir: [-1, 0, 0], up: [0, 0, 1] },
};
// BoxGeometry's material order: +x, -x, +y, -y, +z, -z.
const ORDER = ['Right', 'Left', 'Back', 'Front', 'Top', 'Bottom'];

function faceTexture(label) {
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = size; canvas.height = size;
  const g = canvas.getContext('2d');
  g.fillStyle = '#2b3540'; g.fillRect(0, 0, size, size);
  g.strokeStyle = '#7c8b9a'; g.lineWidth = 6; g.strokeRect(3, 3, size - 6, size - 6);
  g.fillStyle = '#e6edf3'; g.font = '600 26px system-ui, sans-serif';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(label, size / 2, size / 2);
  const t = new THREE.CanvasTexture(canvas);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class ViewCube {
  constructor() {
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1.6, 1.6, 1.6, -1.6, 0.1, 100);
    this.materials = ORDER.map((label) => new THREE.MeshBasicMaterial({ map: faceTexture(label), color: 0xffffff }));
    this.mesh = new THREE.Mesh(new THREE.BoxGeometry(1.7, 1.7, 1.7), this.materials);
    this.scene.add(this.mesh);
    this.hover = -1;
    this.raycaster = new THREE.Raycaster();
  }

  size(width, height) {
    const s = Math.min(CUBE_PX, Math.floor(Math.min(width, height) * 0.28));
    return s < 40 ? 0 : s;
  }

  /** Draw in the top-right corner of a width x height canvas (CSS px). */
  draw(renderer, camera, target, width, height) {
    const s = this.size(width, height);
    if (!s) return;
    const dir = camera.position.clone().sub(target).normalize();
    this.camera.position.copy(dir.multiplyScalar(5));
    this.camera.up.copy(camera.up);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateProjectionMatrix();
    const x = width - s - MARGIN, y = height - s - MARGIN;   // GL counts from the bottom
    renderer.setScissorTest(true);
    renderer.setViewport(x, y, s, s);
    renderer.setScissor(x, y, s, s);
    renderer.clearDepth();
    // No colour clear: the board shows around the cube instead of a black square.
    const autoClear = renderer.autoClear;
    renderer.autoClear = false;
    renderer.render(this.scene, this.camera);
    renderer.autoClear = autoClear;
    renderer.setScissorTest(false);
  }

  /** Face name under a canvas-relative point, '' for the corner but not the cube, null elsewhere. */
  faceAt(px, py, width, height) {
    const s = this.size(width, height);
    if (!s) return null;
    const left = width - s - MARGIN;
    if (px < left || py > s + MARGIN || py < MARGIN) return null;
    const ndc = new THREE.Vector2(((px - left) / s) * 2 - 1, -((py - MARGIN) / s) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObject(this.mesh, false)[0];
    return hit ? ORDER[hit.face.materialIndex] : '';
  }

  /** Light the face under the pointer; returns whether anything changed. */
  setHover(face) {
    const i = face ? ORDER.indexOf(face) : -1;
    if (i === this.hover) return false;
    if (this.hover >= 0) this.materials[this.hover].color.setHex(0xffffff);
    this.hover = i;
    if (i >= 0) this.materials[i].color.setHex(0x8ab4f8);
    return true;
  }

  dispose() {
    for (const m of this.materials) { m.map.dispose(); m.dispose(); }
    this.mesh.geometry.dispose();
  }
}
