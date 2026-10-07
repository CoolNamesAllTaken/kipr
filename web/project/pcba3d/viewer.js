// The 3D PCBA diff view: one canvas, one camera, two prepared sides (scene.js).
//
// The renderer, camera, trackball, lighting, view cube and render-on-demand loop are boarddd's
// createViewer (web/vendor/boarddd/src/scene). Side-by-side is its panes: the base drawn into the
// left half and the head into the right half with the SAME camera, so the two views cannot drift
// apart and a drag anywhere turns both. Overlay and highlight draw both sides into the full canvas
// with materials swapped per component status: that, the change markers, hover and explode are
// kipr's and stay here. Frames only render when something changed (camera, mode, hover).
//
// The see-through two-colour comparison follows gentoo's compare3d.js (PantsForBirds/internal,
// branch john/gentoo, fab/static/fab/).

import * as THREE from '../vendor/three/three.module.js';
import { createViewer, VIEWS as BOARDDD_VIEWS } from '../vendor/boarddd/src/scene/index.js';
import { disposeObject } from '../vendor/boarddd/src/models/index.js';
import { tagsOf } from './diff.js';

export const STATUS_COLORS = {
  added: 0x2ea043, removed: 0xe5534b, moved: 0xd29922, rotated: 0xa371f7, changed: 0x388bfd,
};
export const MODES = ['side', 'overlay', 'highlight'];
const EXPLODE_MM = { component: 10, silk: 3, mask: 2, copper: 1, substrate: 0, loose: 10 };
const CLICK_SLOP_PX = 5;

export const VIEWS = BOARDDD_VIEWS;

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
  constructor(host, { onHover = () => {}, onPick = () => {}, onError = () => {} } = {}) {
    this.host = host;
    this.onHover = onHover;
    this.onPick = onPick;
    this.onError = onError;
    this.sides = { base: null, head: null };
    this.statusOf = new Map();
    this.mode = 'side';
    this.show = { components: true, board: true, silk: true, markers: true };
    // the change kinds the reviewer asked for (the list's active chips): only these get a marker and,
    // in the Changes view, a tint. null = every change (the module's API default).
    this.emphasis = null;
    this.explode = 0;
    this.selected = null;
    this.hovered = null;
    this.disposed = false;
    this.pointer = null;

    this.viewer = createViewer(host, { controls: 'trackball', preserveDrawingBuffer: true });
    this.renderer = this.viewer.renderer;
    this.scene = this.viewer.scene;
    this.camera = this.viewer.camera;
    this.canvas = this.viewer.canvas;
    this.canvas.classList.add('kp3d-canvas');
    // Markers per side, so side-by-side shows each side's own in its own half.
    this.helpers = { base: new THREE.Group(), head: new THREE.Group() };
    // One holder per side: side-by-side shows one holder per viewport. Each holds the side's GLB
    // root and, when there is one, the board built from its fab outputs (gerberboard.js).
    this.holders = { base: new THREE.Group(), head: new THREE.Group() };
    this.fab = { base: new THREE.Group(), head: new THREE.Group() };
    for (const k of ['base', 'head']) {
      this.holders[k].name = `pcba3d-holder-${k}`;
      this.holders[k].userData.side = k;
      this.holders[k].add(this.fab[k], this.helpers[k]);
      this.viewer.add(this.holders[k]);
    }
    this.gerber = null;              // buildGerberBoards() result
    this.boardSource = 'gerber';     // 'gerber' (fab outputs) | 'glb' (the GLB's own board bodies)
    this.diffMaterials = null;       // {top, bottom} once the copper diff is rendered

    this.materials = {
      baseGhost: ghost(STATUS_COLORS.removed, 0.45),
      baseFaint: ghost(STATUS_COLORS.removed, 0.22),
      headGhost: ghost(STATUS_COLORS.added, 0.45),
      neutral: new THREE.MeshStandardMaterial({ color: 0xb8bcc4, roughness: 0.7, metalness: 0 }),
      board: ghost(0x7d8590, 0.35),
      silk: ghost(0xf0f0f0, 0.55),
    };

    this.pressed = null;
    // Hover: at most one pick per frame. Clicks: a press and release in place (not on the cube).
    this.canvas.addEventListener('pointerdown', (e) => {
      this.pressed = this.viewer.cubeAt(e.clientX, e.clientY) ? null : { x: e.clientX, y: e.clientY, id: e.pointerId };
    });
    this.canvas.addEventListener('pointerup', (e) => this._up(e));
    this.canvas.addEventListener('pointermove', (e) => {
      if (!this.pointer) requestAnimationFrame(() => { const p = this.pointer; this.pointer = null; if (p && !this.disposed) this._hover(p); });
      this.pointer = e;
    });
    this.canvas.addEventListener('pointerleave', () => { this.pointer = null; this._setHovered(null); });
  }

  /** Draw again (boarddd draws on demand). */
  redraw() { if (!this.disposed) this.viewer.requestRender(); }
  get controls() { return this.viewer.controls; }

  /* ─── Sides and state ──────────────────────────────────────────────────────── */

  /** sides: {base, head}, each a prepareSide() result or null. statusOf: Map ref -> component. */
  setSides(sides, statusOf) {
    for (const k of ['base', 'head']) {
      const old = this.sides[k];
      if (old && old !== sides[k]) { this.holders[k].remove(old.root); disposeObject(old.root); }
      this.sides[k] = sides[k] || null;
      if (this.sides[k]) this.holders[k].add(this.sides[k].root);
    }
    this.statusOf = statusOf || new Map();
    this._computeExplodeDirs();
    this.applyMode();
    this.fit('iso');
  }

  /** Boards built from the fab outputs (gerberboard.js), or null to go back to the GLB's. */
  setGerberBoards(gerber) {
    if (this.gerber && this.gerber !== gerber) {
      for (const k of ['base', 'head']) this.fab[k].clear();
      this.gerber.dispose();
      for (const m of Object.values(this.diffMaterials || {})) m.dispose();
      this.diffMaterials = null;
    }
    this.gerber = gerber || null;
    if (gerber) {
      for (const k of ['base', 'head']) if (gerber.sides[k]) this.fab[k].add(gerber.sides[k].group);
      if (gerber.ghost) this.fab.head.add(gerber.ghost);
    }
    this.applyMode();
  }

  /** 'gerber' (the board from the fab outputs) or 'glb' (the board bodies in the GLB). */
  setBoardSource(source) {
    this.boardSource = source === 'glb' ? 'glb' : 'gerber';
    this.applyMode();
  }

  get usingFabBoard() {
    return !!this.gerber && this.boardSource === 'gerber';
  }

  _loadDiff() {
    if (this.diffMaterials || this._diffLoading || !this.gerber) return;
    this._diffLoading = true;
    this.gerber.diffTextures().then((t) => {
      this.diffMaterials = {
        top: new THREE.MeshStandardMaterial({ map: t.top, roughness: 0.7 }),
        bottom: new THREE.MeshStandardMaterial({ map: t.bottom, roughness: 0.7 }),
      };
      this.applyMode();
    }).catch((e) => this.onError(`copper diff: ${e.message || e}`)).finally(() => { this._diffLoading = false; });
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

  /** The host's panel colour behind the board; the view cube follows its lightness. */
  setBackground(color) {
    const c = new THREE.Color(color);
    this.viewer.setTheme(0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b < 0.2 ? 'dark' : 'light');
    this.viewer.setBackground(color);
  }

  /** Emphasise only components with one of these tags (added, removed, moved, rotated, changed, ...). */
  setEmphasis(tags) {
    this.emphasis = tags ? new Set(tags) : null;
    this.applyMode();
    this._updateHelpers();
    this.redraw();
  }

  emphasised(ref) {
    const c = this.statusOf.get(ref);
    if (!c || c.status === 'unchanged' || c.status === 'minor') return false;
    return !this.emphasis || tagsOf(c).some((t) => this.emphasis.has(t));
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
        const changed = status !== 'unchanged' && status !== 'minor';
        let visible = show.components;
        let material = null;                     // null = the model's own
        if (mode === 'overlay') {
          if (isBase) { visible = visible && changed; material = this.materials.baseGhost; }
          else material = changed ? this.materials.headGhost : this.materials.neutral;
        } else if (mode === 'highlight') {
          if (isBase) {
            visible = visible && changed && this.emphasised(ref) && status !== 'changed' && status !== 'added';
            material = status === 'removed' ? this.materials.baseGhost : this.materials.baseFaint;
          } else if (changed && this.emphasised(ref)) material = 'tint';
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
        const on = (kind === 'silk' ? show.silk : show.board) && !this.usingFabBoard;
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
    this._applyFab();
    this._updateHelpers();
    const panes = this.mode === 'side' ? [[this.holders.base], [this.holders.head]] : null;
    if (!!panes !== !!this.viewer.panes) this.viewer.setPanes(panes);
    this.redraw();
  }

  /** The fab board: normal faces side by side; the copper diff on the head's in the overlays. */
  _applyFab() {
    const g = this.gerber;
    const overlaid = this.mode !== 'side';
    for (const k of ['base', 'head']) {
      const s = g?.sides[k];
      this.fab[k].visible = !!s && this.usingFabBoard && this.show.board && !(overlaid && k === 'base');
      if (!s) continue;
      let faces = [s.materials.top, s.materials.bottom];
      if (overlaid && k === 'head') {
        if (this.diffMaterials) faces = [this.diffMaterials.top, this.diffMaterials.bottom];
        else this._loadDiff();
      }
      s.body.material = [faces[0], faces[1], s.materials.walls];
    }
    if (g?.ghost) g.ghost.visible = overlaid;
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
    this.redraw();
  }

  /* ─── Camera ───────────────────────────────────────────────────────────────── */

  resize() {
    this.viewer.resize();
  }

  sceneBox() {
    const box = new THREE.Box3();
    for (const side of Object.values(this.sides)) {
      if (!side) continue;
      box.union(side.boardBox.isEmpty() ? side.bounds : side.boardBox);
    }
    for (const s of Object.values(this.gerber?.sides || {})) if (s) box.union(new THREE.Box3().setFromObject(s.body));
    if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(), new THREE.Vector3(50, 50, 2));
    return box;
  }

  /** Look at `box` from a named view direction (or keep the current direction). */
  frame(box, view = null, pad = 1.0) {
    this.viewer.fit(view, box, pad);
  }

  /** Fit the whole board from a preset ('top' | 'bottom' | 'iso' | 'isoBottom' | a view-cube face) or as is. */
  fit(name = null) {
    const view = name ? VIEWS[name] || VIEWS[name.toLowerCase()] : null;
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
    this.redraw();
  }

  select(ref) {
    this.selected = ref || null;
    this._updateHelpers();
    this.redraw();
  }

  _updateHelpers() {
    for (const g of Object.values(this.helpers)) {
      for (const h of g.children.slice()) { g.remove(h); h.geometry?.dispose(); h.material?.dispose(); }
    }
    const add = (ref, color, grow, opacity) => {
      for (const [k, side] of Object.entries(this.sides)) {
        const e = side?.comps.get(ref);
        if (!e) continue;
        if (!e.meshes.some((m) => m.visible)) continue;
        const box = new THREE.Box3();
        for (const o of e.objects) box.union(new THREE.Box3().setFromObject(o));
        box.expandByScalar(grow);
        // the box is in world space; the holders are not transformed
        const helper = new THREE.Box3Helper(box, color);
        helper.userData.side = k;
        helper.raycast = () => {};
        helper.material.depthTest = false;
        helper.material.transparent = opacity < 1;
        helper.material.opacity = opacity;
        helper.renderOrder = 10;
        this.helpers[k].add(helper);
      }
    };
    // Change markers: a box in the status colour around every changed part, so a moved 0402
    // on a board of hundreds can be found at a glance. In the overlaid modes the base's copy is
    // drawn only where the base's part is (removed, and the old place of a moved one).
    if (this.show.markers) {
      for (const [ref, c] of this.statusOf) {
        if (!this.emphasised(ref) || ref === this.selected || ref === this.hovered) continue;
        add(ref, STATUS_COLORS[c.status], 0.25, 0.8);
      }
    }
    if (this.selected) add(this.selected, 0x1f6feb, 0.15, 1);
    if (this.hovered && this.hovered !== this.selected) add(this.hovered, 0xff9500, 0.15, 1);
  }

  /* ─── Drawing ──────────────────────────────────────────────────────────────── */

  /** Draw now (tests time this; normally boarddd draws on demand). */
  render() {
    this.viewer.render();
  }

  /* ─── Pointer ──────────────────────────────────────────────────────────────── */

  _up(e) {
    const press = this.pressed;
    this.pressed = null;
    if (!press || press.id !== e.pointerId) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) > CLICK_SLOP_PX) return;
    const hit = this.pickClient(e.clientX, e.clientY);
    this.onPick(hit ? hit.ref : null);
  }

  /** Component under a client point: {ref, side, distance} or null (only what that pane shows). */
  pickClient(clientX, clientY) {
    const meshes = new Set();
    for (const side of Object.values(this.sides)) for (const m of side?.meshes || []) meshes.add(m);
    const hit = this.viewer.pick(clientX, clientY, { filter: (o) => meshes.has(o) });
    if (!hit) return null;
    let side = null;
    for (let o = hit.object; o && !side; o = o.parent) side = o.userData?.side || null;
    const distance = this.camera.position.distanceTo(new THREE.Vector3(...hit.point));
    return { ref: hit.object.userData.ref, side, distance };
  }

  /** Component under a canvas point (CSS px from the canvas' top left): {ref, side} or null. */
  pick(px, py) {
    const r = this.canvas.getBoundingClientRect();
    return this.pickClient(r.left + px, r.top + py);
  }

  _hover(e) {
    if (e.buttons || this.viewer.cubeAt(e.clientX, e.clientY)) { this._setHovered(null); return; }
    this._setHovered(this.pickClient(e.clientX, e.clientY), e);
  }

  _setHovered(hit, e) {
    const ref = hit ? hit.ref : null;
    if (ref !== this.hovered) {
      this.hovered = ref;
      this._updateHelpers();
      this.redraw();
    }
    this.onHover(hit ? { ...hit, clientX: e.clientX, clientY: e.clientY } : null);
  }

  /** PNG data URL of the current view. */
  capture() {
    return this.viewer.capture({ viewCube: true });
  }

  dispose() {
    this.disposed = true;
    this.gerber?.dispose();
    for (const m of Object.values(this.diffMaterials || {})) m.dispose();
    for (const m of Object.values(this.materials)) m.dispose();
    for (const m of this._tintList || []) m.dispose();
    for (const g of Object.values(this.helpers)) for (const h of g.children) { h.geometry?.dispose(); h.material?.dispose(); }
    this.viewer.dispose();   // the sides' GLB roots and the fab boards with it
  }
}
