import type * as THREE from 'three';
import type { Outline, Hole, HoleReport, Bounds } from '../geom/index.js';

export const COLORS: { fr4: number; copper: number; mask: number };
export function planarUVs(geometry: THREE.BufferGeometry, bounds: Bounds): void;
export function splitCaps(geometry: THREE.BufferGeometry): void;
export function boardGeometry(outline: Outline, holes?: Hole[], thickness?: number, uvBounds?: Bounds | null, options?: { budget?: number }):
  { body: THREE.ExtrudeGeometry; barrels: THREE.ExtrudeGeometry | null; holes: HoleReport };
/** A texture, a canvas/image (made an sRGB texture), or a colour. */
export type Face = THREE.Texture | HTMLCanvasElement | OffscreenCanvas | HTMLImageElement | THREE.ColorRepresentation;
export function faceMaterial(face?: Face, fallbackColor?: THREE.ColorRepresentation): THREE.MeshStandardMaterial;
export function canvasTexture(canvas: HTMLCanvasElement | OffscreenCanvas | HTMLImageElement | THREE.Texture, options?: { anisotropy?: number }): THREE.Texture;

export interface BoardSolid {
  group: THREE.Group;
  /** groups: 0 top, 1 bottom, 2 walls; userData.group = 'board' */
  body: THREE.Mesh;
  /** userData.group = 'barrels' */
  barrels: THREE.Mesh | null;
  materials: { top: THREE.MeshStandardMaterial; bottom: THREE.MeshStandardMaterial; walls: THREE.MeshStandardMaterial; barrels: THREE.MeshStandardMaterial };
  holes: HoleReport;
  outline: Outline;
  thickness: number;
  setFaces(faces: { top?: Face; bottom?: Face }): void;
  dispose(): void;
}
export function buildBoard(options: {
  outline: Outline; holes?: Hole[]; thickness?: number; uvBounds?: Bounds | null;
  faces?: { top?: Face; bottom?: Face }; budget?: number; name?: string;
}): BoardSolid;
export function outlineGhost(outline: Outline, zs?: number[], options?: { color?: THREE.ColorRepresentation; opacity?: number }): THREE.Group;
export function outlinesDiffer(a: Outline | null, b: Outline | null, tol?: number): boolean;

// Gerber faces: `gerber` = boarddd/gerber's functions (null/undefined = boarddd/gerber itself, or inject another
// implementation), `renderer` = a GerberRenderer (null/undefined = defaultRenderer()).
export type GerberApi = Record<string, (...args: any[]) => any> | null | undefined;
export type GerberRenderer = import('../gerber/index.js').GerberRenderer | null | undefined;
/** One shared boarddd/gerber renderer on its own canvas (preserveDrawingBuffer), made on first call. */
export function defaultRenderer(): Promise<import('../gerber/index.js').GerberRenderer>;
export interface FabFile { name: string; text: string; plated?: boolean }
export interface Fab {
  grouped: any;
  outline: Outline | null;
  holes: Hole[];
  drills: { name: string; text: string; plated: boolean; holes: Hole[] }[];
  edge: string | null;
}
export interface PaintedFaces {
  top: HTMLCanvasElement; bottom: HTMLCanvasElement; bounds: Bounds;
  size: { width: number; height: number; pxPerMm?: number }; view: any;
}
export const FACE_PX_PER_MM: number;
export const MAX_FACE_PX: number;
/** boarddd/gerber's withoutEmptyTools, kept here for 0.1 callers. */
export function withoutEmptyTools(text: string): string;
export function readFabFiles(gerber: GerberApi, files: FabFile[], board?: { size_mm?: [number, number]; origin_mm?: [number, number] }): Fab;
export function faceBounds(outlines: (Outline | null)[], pad?: number): Bounds;
export function paintFaces(gerber: GerberApi, renderer: GerberRenderer, fab: Fab, options?: {
  bounds?: Bounds | null; pxPerMm?: number; maxTextureSize?: number; palette?: { mask?: string; silk?: string; finish?: string };
}): Promise<PaintedFaces>;
export function paintCopperDiff(gerber: GerberApi, renderer: GerberRenderer, pair: { base: Fab | null; head: Fab | null }, painted: PaintedFaces,
  options?: { maxTextureSize?: number }): Promise<{ top: HTMLCanvasElement; bottom: HTMLCanvasElement }>;
export function buildGerberBoard(gerber: GerberApi, renderer: GerberRenderer, files: FabFile[], options?: {
  thickness?: number; board?: { size_mm?: [number, number]; origin_mm?: [number, number] }; palette?: { mask?: string; silk?: string; finish?: string };
  bounds?: Bounds | null; pxPerMm?: number; maxTextureSize?: number; budget?: number; name?: string;
  /** Fill and cap plated round holes up to this drill diameter, mm (fillFab). */
  fillUpTo?: number | null;
}): Promise<BoardSolid & { fab: Fab; painted: PaintedFaces; textures: { top: THREE.Texture; bottom: THREE.Texture } }>;
/** `fab` with plated round holes up to `upTo` mm drill filled and capped: out of the solid and the painted drills. */
export function fillFab(gerber: GerberApi, fab: Fab, upTo: number | null | undefined): Fab;
export interface PasteSolids {
  group: THREE.Group;
  /** userData.group = 'paste' */
  meshes: { top: THREE.Mesh | null; bottom: THREE.Mesh | null };
  material: THREE.MeshStandardMaterial;
  dispose(): void;
}
/** Each face's paste Gerber traced from a raster in `painted`'s frame and extruded off that face. */
export function buildPaste(gerber: GerberApi, renderer: GerberRenderer, fab: Fab, painted: PaintedFaces, options?: {
  thickness?: number; height?: number; color?: THREE.ColorRepresentation; name?: string;
}): Promise<PasteSolids>;
