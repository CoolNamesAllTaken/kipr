// The 3D PCBA diff view: one canvas, one camera, two prepared sides (scene.js).
//
// Side-by-side is one renderer drawing the base scene into the left half and the head scene into
// the right half with the SAME camera, so the two views cannot drift apart and a drag anywhere
// turns both. Overlay and highlight draw both sides into the full canvas with materials swapped
// per component status. Frames only render when something changed (camera, mode, hover), so a
// board of hundreds of parts costs nothing while it is just being looked at.
//
// Camera handling (trackball, no damping), lighting and the view cube follow gentoo's
// viewer3d.js (PantsForBirds/internal, branch john/gentoo, fab/static/fab/viewer3d.js);
// see viewcube.js. The see-through two-colour comparison follows gentoo's compare3d.js.

import * as THREE from './vendor/three/three.module.js';
import { TrackballControls } from './vendor/three/addons/TrackballControls.js';
import { ViewCube, FACES } from './viewcube.js';
import { disposeObject } from './scene.js';

export const STATUS_COLORS = {
  added: 0x2ea043, removed: 0xe5534b, moved: 0xd29922, rotated: 0xa371f7, changed: 0x388bfd,
};
export const MODES = ['side', 'overlay', 'highlight'];
const EXPLODE_MM = { component: 10, silk: 3, mask: 2, copper: 1, substrate: 0, loose: 10 };
const CLICK_SLOP_PX = 5;

export const VIEWS = {
  top: FACES.Top,
  bottom: FACES.Bottom,
  iso: { dir: [0.55, -1, 0.95], up: [0, 0, 1] },
  isoBottom: { dir: [0.55, -1, -0.95], up: [0, 0, -1] },
};

function ghost(color, opacity) {
  return new THREE.MeshStandardMaterial({
    color, transparent: true, opacity, depthWrite: false, roughness: 0.6, metalness: 0,
    side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1,
  });
}

export class Pcba3dView {
  /**
   * host: element the canvas goes into (sized by the caller).
   * callbacks: onHover({ref, side, x, y} | null), onPick(ref | null)
   */
  constructor(host, { onHover = () => {}, onPick = () => {} } = {}) {
    this.host = host;
    this.onHover = onHover;
    this.onPick = onPick;
    this.sides = { base: null, head: null };
    this.statusOf = new Map();
    this.mode = 'side';
    this.show = { components: true, board: true, silk: true, markers: true };
    this.explode = 0;
    this.selected = null;
    this.hovered = null;
    this.dirty = true;
    this.disposed = false;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.canvas = this.renderer.domElement;
    this.canvas.className = 'kp3d-canvas';
    host.appendChild(this.canvas);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 10000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(0, -100, 100);
    // Sky/ground pair so tops and sides are not one flat colour, plus a headlight so the
    // underside is lit when the board is turned over.
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 2.0));
    const head = new THREE.DirectionalLight(0xffffff, 1.3);
    head.position.set(0.3, 0.6, 1);
    this.camera.add(head);
    this.scene.add(this.camera);
    this.helpers = new THREE.Group();
    this.scene.add(this.helpers);

    this.materials = {
      baseGhost: ghost(STATUS_COLORS.removed, 0.45),
      baseFaint: ghost(STATUS_COLORS.removed, 0.22),
      headGhost: ghost(STATUS_COLORS.added, 0.45),
      neutral: new THREE.MeshStandardMaterial({ color: 0xb8bcc4, roughness: 0.7, metalness: 0 }),
      board: ghost(0x7d8590, 0.35),
      silk: ghost(0xf0f0f0, 0.55),
    };

    this.cube = new ViewCube();
    this.raycaster = new THREE.Raycaster();
    this.pointer = null;
    this.pressed = null;
    this.cubeDown = false;

    // Before the trackball's own listener, so a press on the cube never starts a drag.
    this.canvas.addEventListener('pointerdown', (e) => this._down(e), true);
    this.controls = new TrackballControls(this.camera, this.canvas);
    this.controls.staticMoving = true;   // no coasting: where you let go is where it stops
    this.controls.rotateSpeed = 4.0;
    this.controls.zoomSpeed = 1.6;
    this.controls.panSpeed = 0.8;
    this.controls.addEventListener('change', () => { this.dirty = true; });
    this.canvas.addEventListener('pointermove', (e) => { this.pointer = e; });
    this.canvas.addEventListener('pointerup', (e) => this._up(e));
    this.canvas.addEventListener('pointerleave', () => {
      this.pointer = null;
      if (this.cube.setHover(null)) this.dirty = true;
      this._setHovered(null);
    });

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(host);
    this.resize();
    const loop = () => {
      if (this.disposed) return;
      this.raf = requestAnimationFrame(loop);
      this.controls.update();
      if (this.pointer) { const e = this.pointer; this.pointer = null; this._hover(e); }
      if (this.dirty) { this.dirty = false; this.render(); }
    };
    loop();
  }

  /* ─── Sides and state ──────────────────────────────────────────────────────── */

  /** sides: {base, head}, each a prepareSide() result or null. statusOf: Map ref -> component. */
  setSides(sides, statusOf) {
    for (const k of ['base', 'head']) {
      const old = this.sides[k];
      if (old && old !== sides[k]) { this.scene.remove(old.root); disposeObject(old.root); }
      this.sides[k] = sides[k] || null;
      if (this.sides[k]) this.scene.add(this.sides[k].root);
    }
    this.statusOf = statusOf || new Map();
    this._computeExplodeDirs();
    this.applyMode();
    this.fit('iso');
  }

  setMode(mode) {
    if (!MODES.includes(mode)) throw new Error(`unknown mode ${mode}`);
    this.mode = mode;
    this.applyMode();
    this.resize();
  }

  setVisible(which, on) {
    this.show[which] = !!on;
    this.applyMode();
  }

  setBackground(color) {
    this.scene.background = new THREE.Color(color);
    this.dirty = true;
  }

  statusFor(ref) {
    return this.statusOf.get(ref)?.status || 'unchanged';
  }

  /** Materials and visibility for the current mode and toggles. */
  applyMode() {
    const { mode, show } = this;
    for (const k of ['base', 'head']) {
      const side = this.sides[k];
      if (!side) continue;
      const isBase = k === 'base';
      for (const [ref, entry] of side.comps) {
        const status = this.statusFor(ref);
        const changed = status !== 'unchanged';
        let visible = show.components;
        let material = null;                     // null = the model's own
        if (mode === 'overlay') {
          if (isBase) { visible = visible && changed; material = this.materials.baseGhost; }
          else material = changed ? this.materials.headGhost : this.materials.neutral;
        } else if (mode === 'highlight') {
          if (isBase) {
            visible = visible && changed && status !== 'changed' && status !== 'added';
            material = status === 'removed' ? this.materials.baseGhost : this.materials.baseFaint;
          } else if (changed) material = 'tint';
        }
        for (const m of entry.meshes) {
          m.visible = visible;
          m.material = material === 'tint' ? this._tint(m, status) : material || m.userData.orig;
        }
      }
      for (const o of side.loose) {
        o.traverse((m) => {
          if (!m.isMesh) return;
          m.visible = show.components && !(isBase && mode !== 'side');
          m.material = mode === 'overlay' ? this.materials.neutral : m.userData.orig;
        });
      }
      for (const [kind, list] of Object.entries(side.parts)) {
        const on = kind === 'silk' ? show.silk : show.board;
        for (const p of list) {
          p.traverse((m) => {
            if (!m.isMesh) return;
            // One board per view: in the overlaid modes only the head's is drawn.
            m.visible = on && !(isBase && mode !== 'side');
            if (mode === 'overlay') {
              m.visible = m.visible && kind !== 'copper';
              m.material = kind === 'silk' ? this.materials.silk : this.materials.board;
            } else m.material = m.userData.orig;
          });
        }
      }
    }
    this._updateHelpers();
    this.dirty = true;
  }

  /** The mesh's own material with an emissive glow in the status colour (cached per mesh). */
  _tint(mesh, status) {
    const cache = mesh.userData.tints || (mesh.userData.tints = {});
    if (!cache[status]) {
      const orig = mesh.userData.orig;
      const m = (Array.isArray(orig) ? orig[0] : orig).clone();
      const c = new THREE.Color(STATUS_COLORS[status] || 0xffffff);
      if (m.emissive) { m.emissive = c; m.emissiveIntensity = 0.45; }
      if (m.color) m.color.lerp(c, 0.8);
      cache[status] = m;
      (this._tintList || (this._tintList = [])).push(m);
    }
    return cache[status];
  }

  /* ─── Explode ──────────────────────────────────────────────────────────────── */

  _computeExplodeDirs() {
    const up = new THREE.Vector3();
    const w0 = new THREE.Vector3(), w1 = new THREE.Vector3();
    const dirFor = (o) => {
      // Unit world +z expressed in the object's parent frame (parents carry the GLB's rotation
      // and unit scale), so a world-space lift can be applied to a local position.
      o.parent.updateMatrixWorld(true);
      o.getWorldPosition(w0);
      w1.copy(w0).add(up.set(0, 0, 1));
      return o.parent.worldToLocal(w1.clone()).sub(o.parent.worldToLocal(w0.clone()));
    };
    for (const side of Object.values(this.sides)) {
      if (!side) continue;
      for (const entry of side.comps.values()) {
        for (const o of entry.objects) {
          o.position.copy(o.userData.home);
          o.userData.lift = dirFor(o).multiplyScalar(entry.bottom ? -1 : 1);
          o.userData.liftMm = EXPLODE_MM.component;
        }
      }
      const mid = side.boardMidZ;
      const signOf = (o) => (new THREE.Box3().setFromObject(o).getCenter(w0).z < mid ? -1 : 1);
      for (const o of side.loose) {
        o.position.copy(o.userData.home);
        o.userData.lift = dirFor(o).multiplyScalar(signOf(o));
        o.userData.liftMm = EXPLODE_MM.loose;
      }
      for (const [kind, list] of Object.entries(side.parts)) {
        for (const o of list) {
          o.position.copy(o.userData.home);
          o.userData.lift = dirFor(o).multiplyScalar(signOf(o));
          o.userData.liftMm = EXPLODE_MM[kind] || 0;
        }
      }
    }
  }

  /** 0 = assembled, 1 = components lifted EXPLODE_MM.component off the board. */
  setExplode(f) {
    this.explode = Math.max(0, Math.min(1, Number(f) || 0));
    for (const side of Object.values(this.sides)) {
      if (!side) continue;
      const all = [...[...side.comps.values()].flatMap((e) => e.objects), ...side.loose,
        ...Object.values(side.parts).flat()];
      for (const o of all) {
        if (!o.userData.lift) continue;
        o.position.copy(o.userData.home).addScaledVector(o.userData.lift, this.explode * o.userData.liftMm);
      }
    }
    this._updateHelpers();
    this.dirty = true;
  }

  /* ─── Camera ───────────────────────────────────────────────────────────────── */

  resize() {
    const r = this.host.getBoundingClientRect();
    const w = Math.max(1, Math.floor(r.width)), h = Math.max(1, Math.floor(r.height));
    this.width = w; this.height = h;
    this.renderer.setSize(w, h, false);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.camera.aspect = (this.mode === 'side' ? w / 2 : w) / h;
    this.camera.updateProjectionMatrix();
    this.controls.handleResize();
    this.dirty = true;
  }

  sceneBox() {
    const box = new THREE.Box3();
    for (const side of Object.values(this.sides)) {
      if (!side) continue;
      box.union(side.boardBox.isEmpty() ? side.bounds : side.boardBox);
    }
    if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(), new THREE.Vector3(50, 50, 2));
    return box;
  }

  /** Look at `box` from a named view direction (or keep the current direction). */
  frame(box, view = null, pad = 1.08) {
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.5);
    let dir, up;
    if (view) {
      dir = new THREE.Vector3(...view.dir).normalize();
      up = new THREE.Vector3(...view.up);
    } else {
      dir = this.camera.position.clone().sub(this.controls.target).normalize();
      up = this.camera.up.clone();
    }
    const vfov = THREE.MathUtils.degToRad(this.camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * this.camera.aspect);
    const dist = (radius * pad) / Math.tan(Math.min(vfov, hfov) / 2);
    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(dir, dist);
    this.camera.up.copy(up);
    this.camera.near = Math.max(dist / 1000, 0.01);
    this.camera.far = dist * 100;
    this.camera.lookAt(center);
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.dirty = true;
  }

  /** Fit the whole board from a preset ('top' | 'bottom' | 'iso' | a view-cube face) or as is. */
  fit(name = null) {
    const view = name ? (VIEWS[name] || FACES[name[0].toUpperCase() + name.slice(1)]) : null;
    this.frame(this.sceneBox(), view);
  }

  /** Box of one component: union over both sides (a moved part frames old and new place). */
  boxOf(ref) {
    const box = new THREE.Box3();
    for (const side of Object.values(this.sides)) {
      const e = side?.comps.get(ref);
      if (!e) continue;
      for (const o of e.objects) box.union(new THREE.Box3().setFromObject(o));
    }
    return box;
  }

  /** Select a component and frame it (keeping the current viewing direction). */
  focus(ref) {
    this.select(ref);
    const box = this.boxOf(ref);
    if (box.isEmpty()) return false;
    // Pad small parts so a 0402 is shown with its neighbourhood, not as a wall of pixels.
    const size = box.getSize(new THREE.Vector3());
    const min = 6;
    box.expandByVector(new THREE.Vector3(Math.max(0, (min - size.x) / 2), Math.max(0, (min - size.y) / 2), 0));
    this.frame(box, null, 1.6);
    return true;
  }

  /** Hover highlight from outside the canvas (the change list). */
  highlight(ref) {
    if (ref === this.hovered) return;
    this.hovered = ref || null;
    this._updateHelpers();
    this.dirty = true;
  }

  select(ref) {
    this.selected = ref || null;
    this._updateHelpers();
    this.dirty = true;
  }

  _updateHelpers() {
    for (const h of this.helpers.children.slice()) { this.helpers.remove(h); h.geometry?.dispose(); h.material?.dispose(); }
    const add = (ref, color, grow, opacity) => {
      for (const [k, side] of Object.entries(this.sides)) {
        const e = side?.comps.get(ref);
        if (!e) continue;
        if (!e.meshes.some((m) => m.visible)) continue;
        const box = new THREE.Box3();
        for (const o of e.objects) box.union(new THREE.Box3().setFromObject(o));
        box.expandByScalar(grow);
        const helper = new THREE.Box3Helper(box, color);
        helper.userData.side = k;
        helper.material.depthTest = false;
        helper.material.transparent = opacity < 1;
        helper.material.opacity = opacity;
        helper.renderOrder = 10;
        this.helpers.add(helper);
      }
    };
    // Change markers: a box in the status colour around every changed part, so a moved 0402
    // on a board of hundreds can be found at a glance. In the overlaid modes the base's copy is
    // drawn only where the base's part is (removed, and the old place of a moved one).
    if (this.show.markers) {
      for (const [ref, c] of this.statusOf) {
        if (c.status === 'unchanged' || ref === this.selected || ref === this.hovered) continue;
        add(ref, STATUS_COLORS[c.status], 0.25, 0.8);
      }
    }
    if (this.selected) add(this.selected, 0x1f6feb, 0.15, 1);
    if (this.hovered && this.hovered !== this.selected) add(this.hovered, 0xff9500, 0.15, 1);
  }

  /* ─── Drawing ──────────────────────────────────────────────────────────────── */

  _showOnly(which) {
    for (const k of ['base', 'head']) if (this.sides[k]) this.sides[k].root.visible = !which || k === which;
    for (const h of this.helpers.children) h.visible = !which || h.userData.side === which;
  }

  render() {
    const { renderer, width: w, height: h } = this;
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, w, h);
    renderer.clear();
    if (this.mode === 'side') {
      const half = Math.floor(w / 2);
      renderer.setScissorTest(true);
      for (const [k, x, vw] of [['base', 0, half], ['head', half, w - half]]) {
        renderer.setViewport(x, 0, vw, h);
        renderer.setScissor(x, 0, vw, h);
        this._showOnly(k);
        if (this.sides[k]) renderer.render(this.scene, this.camera);
        else { renderer.setClearColor(this.scene.background || 0x000000); renderer.clear(); }
      }
      renderer.setScissorTest(false);
      this._showOnly(null);
    } else {
      this._showOnly(null);
      renderer.render(this.scene, this.camera);
    }
    this.cube.draw(renderer, this.camera, this.controls.target, w, h);
    renderer.setViewport(0, 0, w, h);
  }

  /* ─── Pointer ──────────────────────────────────────────────────────────────── */

  _local(e) {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  _down(e) {
    const p = this._local(e);
    this.pressed = { x: e.clientX, y: e.clientY, id: e.pointerId };
    const face = this.cube.faceAt(p.x, p.y, this.width, this.height);
    this.cubeDown = face !== null;
    if (this.cubeDown) { e.stopImmediatePropagation(); e.preventDefault(); }
  }

  _up(e) {
    const press = this.pressed;
    this.pressed = null;
    if (!press || press.id !== e.pointerId) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) > CLICK_SLOP_PX) return;
    const p = this._local(e);
    if (this.cubeDown) {
      this.cubeDown = false;
      const face = this.cube.faceAt(p.x, p.y, this.width, this.height);
      if (face) this.frame(this.sceneBox(), FACES[face]);
      return;
    }
    const hit = this.pick(p.x, p.y);
    this.onPick(hit ? hit.ref : null);
  }

  /** Component under a canvas point: {ref, side} or null. */
  pick(px, py) {
    const { width: w, height: h } = this;
    let sides = ['base', 'head'], x0 = 0, vw = w;
    if (this.mode === 'side') {
      const half = Math.floor(w / 2);
      if (px < half) { sides = ['base']; vw = half; } else { sides = ['head']; x0 = half; vw = w - half; }
    }
    const ndc = new THREE.Vector2(((px - x0) / vw) * 2 - 1, -(py / h) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    let best = null;
    for (const k of sides) {
      const side = this.sides[k];
      if (!side) continue;
      const targets = side.meshes.filter((m) => m.visible);
      const hit = this.raycaster.intersectObjects(targets, false)[0];
      if (hit && (!best || hit.distance < best.distance)) best = { ref: hit.object.userData.ref, side: k, distance: hit.distance };
    }
    return best;
  }

  _hover(e) {
    const p = this._local(e);
    const face = this.cube.faceAt(p.x, p.y, this.width, this.height);
    if (this.cube.setHover(face)) this.dirty = true;
    this.canvas.style.cursor = face ? 'pointer' : '';
    if (face !== null || e.buttons) { this._setHovered(null); return; }
    const hit = this.pick(p.x, p.y);
    this._setHovered(hit, e);
  }

  _setHovered(hit, e) {
    const ref = hit ? hit.ref : null;
    if (ref !== this.hovered) {
      this.hovered = ref;
      this._updateHelpers();
      this.dirty = true;
    }
    this.onHover(hit ? { ...hit, clientX: e.clientX, clientY: e.clientY } : null);
  }

  /** PNG data URL of the current view. */
  capture() {
    this.render();
    return this.canvas.toDataURL('image/png');
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.resizeObserver.disconnect();
    this.controls.dispose();
    for (const k of ['base', 'head']) if (this.sides[k]) disposeObject(this.sides[k].root);
    for (const m of Object.values(this.materials)) m.dispose();
    for (const m of this._tintList || []) m.dispose();
    for (const h of this.helpers.children) { h.geometry?.dispose(); h.material?.dispose(); }
    this.cube.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss?.();
    this.canvas.remove();
  }
}
