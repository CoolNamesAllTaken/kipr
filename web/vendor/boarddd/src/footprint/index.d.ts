import type * as THREE from 'three';
import type { Pad, Outline, Bounds, Mat4, Vec2, Vec3, Loop } from '../geom/index.js';
import type { Face } from '../board/index.js';

export interface Graphic {
  layer: string; kind: 'line' | 'rect' | 'circle' | 'arc' | 'poly' | 'curve';
  /** KiCad frame (y down), arcs/circles/curves flattened */
  pts: Vec2[]; width: number; closed: boolean; filled: boolean;
}
export interface FootprintModel { path: string; offset: Vec3; rotate: Vec3; scale: Vec3; hide: boolean; opacity: number }
export interface Footprint { name: string; layer: string; attr: string[]; pads: Pad[]; graphics: Graphic[]; models: FootprintModel[] }

export function parseSexpr(text: string): any[];
export function parseKicadFootprint(text: string): Footprint;
export function arcThrough(start: Vec2, mid: Vec2, end: Vec2, segments?: number): Vec2[];

export const FOOTPRINT_COLORS: { mask: number; fr4: number; copper: number; silk: number; fab: number; courtyard: number };
export function chainLoops(polylines: Vec2[][], tol?: number): Loop[];
export function footprintOutline(fp: Footprint, margin?: number): Outline;
export function footprintModelMatrix(model: FootprintModel, thickness?: number): Mat4;
export function wallGeometry(loop: Loop, z0: number, z1: number): THREE.BufferGeometry;
export interface BuiltFootprint {
  group: THREE.Group;
  outline: Outline;
  thickness: number;
  uvBounds: Bounds;
  meshes: { board: THREE.Mesh; copper: THREE.Mesh[]; barrels: THREE.Mesh[]; silk: THREE.Mesh[]; fab: THREE.Mesh[]; courtyard: THREE.Mesh[] };
  /** Board-frame placement for one of fp.models (KiCad's model matrix on the top face). */
  modelMatrix(model: FootprintModel): Mat4;
  dispose(): void;
}
export function buildFootprint(fp: Footprint, options?: {
  thickness?: number; margin?: number; outline?: Outline; faces?: { top?: Face; bottom?: Face }; uvBounds?: Bounds;
  /** Layer pictures over uvBounds drawn as transparent sheets in the silk / fab / courtyard groups. */
  decals?: Partial<Record<'silk' | 'fab' | 'courtyard', { top?: Face; bottom?: Face }>>;
  colors?: Partial<{ mask: number; fr4: number; copper: number; silk: number; fab: number; courtyard: number }>;
}): BuiltFootprint;
