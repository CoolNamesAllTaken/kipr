import type { Object3D, Group, Box3, Mesh, MeshStandardMaterial } from 'three';

export type Vec2 = [number, number];

/** A component in KiCad mm (y down). `assembly` + `box` [x0, y0, x1, y1]: a module that claims the solids inside its box. */
export interface Component { ref: string; /** the designator node names carry when it isn't ref (panel copies) */ name?: string; x: number; y: number; side?: 'top' | 'bottom'; assembly?: boolean; box?: [number, number, number, number] | [number, number, number, number, number, number] }
/** A candidate node in the board frame (mm, y up): origin and bounding-box middle. */
export interface MatchNode { name?: string; x: number; y: number; cx: number; cy: number; cz?: number }
export interface MatchResult {
  byRef: Map<string, number[]>; offset: { x: number; y: number };
  method: 'name' | 'position' | 'mixed' | 'none'; byName: number; byPosition: number;
  ambiguous: string[]; unmatched: string[]; leftover: number[];
}
export type BoardKind = 'substrate' | 'mask' | 'copper' | 'silk';
export interface BoxArrays { min: [number, number, number]; max: [number, number, number] }

export const MATCH_MM: number;
export const HOUGH_BIN_MM: number;
export function naturalCompare(a: string, b: string): number;
export function refFromName(name: string | null | undefined, refs: Set<string>): string | null;
export function toBoardFrame(c: { x: number; y: number }): { x: number; y: number };
export function houghTranslation(nodes: MatchNode[], aims: { x: number; y: number }[], binMm?: number): { x: number; y: number; support: number } | null;
export function refineTranslation(nodes: MatchNode[], aims: { x: number; y: number }[], guess: { x: number; y: number }, radius: number): { x: number; y: number; support: number };
export function matchByPosition(nodes: MatchNode[], targets: { ref: string; aim: { x: number; y: number } }[], tol?: number): { matched: Map<string, number>; ambiguous: string[]; unmatched: string[]; taken: Set<number> };
export function mapNodesToRefs(nodes: MatchNode[], components: Component[], opts?: {
  tol?: number; fallbackOffset?: { x: number; y: number } | null;
  /** 'board': components and module boxes are already in the nodes' frame (no y flip). */
  frame?: 'kicad' | 'board';
  /** A known export offset, used as is. */
  offset?: { x: number; y: number } | null;
  byName?: boolean; joinExtras?: boolean;
}): MatchResult;
export function boardKindFromName(name: string | null | undefined): BoardKind | null;
export function boardKindFromLook(hsl: { h: number; s: number; l: number } | null, thicknessMm: number): BoardKind;
export function flatness(size: [number, number, number]): number;
export function splitBoardBodies(nodes: { box: BoxArrays }[], boardArea: number, measured?: { box: number[]; [k: string]: unknown }[] | null): { board: number[]; components: number[]; measuredBy: Map<number, unknown> };
export function measureBoard(bodies: { kind?: BoardKind; box: BoxArrays }[]): { bottom: number; top: number; thickness: number } | null;

export const UNIT_SCALE: Record<'m' | 'mm' | 'cm' | 'in' | 'mil', number>;
export function detectUp(size: [number, number, number], stated?: string | null): 'x' | 'y' | 'z';
export function detectScale(span: number, opts?: { units?: string | null; expectedMm?: number }): number;
export function stepColorToLinear(rgb: [number, number, number] | null): [number, number, number] | null;

export interface LoadedGLB { scene: Group; gltf: unknown; meshName(object: Object3D): string }
export function loadGLB(source: string | URL | ArrayBuffer | Uint8Array, opts?: { signal?: AbortSignal; loader?: unknown }): Promise<LoadedGLB>;

export interface OcctUrls { js: string; wasm: string }
export interface StepOptions {
  /** URLs of occt-import-js (LGPL-2.1, not bundled): used in the Worker or loaded by a script tag. */
  occt?: OcctUrls;
  /** Returns an occt-import-js instance (main thread; node, or a classic-script bundle). */
  occtFactory?: () => Promise<unknown>;
  /** Worker script URL (default: step_worker.js beside the module); false = main thread. */
  workerUrl?: string | false;
  onProgress?(stage: string): void;
  signal?: AbortSignal;
  /** Polygon offset on the STEP materials (default true). */
  polygonOffset?: boolean;
  /** Run occt on the main thread when the Worker crashes (default true). */
  fallback?: boolean;
  /** stepToObject: put each node's group at its box middle (default true); false keeps the vertices absolute. */
  center?: boolean;
}
export interface StepMesh { name: string; color: [number, number, number] | null; position: Float32Array; normal: Float32Array | null; index: Uint32Array; faces: Int32Array; faceColors: ([number, number, number] | null)[] }
export interface StepData { root: { name: string; meshes: number[]; children: StepData['root'][] }; meshes: StepMesh[]; triangles: number }
export function readStep(source: string | URL | ArrayBuffer | Uint8Array, opts?: StepOptions): Promise<StepData>;
export function stepToObject(data: StepData, opts?: { polygonOffset?: boolean; center?: boolean }): Group;
export function loadSTEP(source: string | URL | ArrayBuffer | Uint8Array, opts?: StepOptions): Promise<Group>;
export function stepMaterial(rgb: [number, number, number] | null, opts?: { polygonOffset?: boolean }): MeshStandardMaterial;
export function terminateStepWorkers(): void;

export interface PreparedComponent { ref: string; objects: Object3D[]; meshes: Mesh[]; component: Component; box: Box3; bottom: boolean }
export interface PrepareOptions {
  boardSize?: Vec2 | null; boardOrigin?: Vec2 | null; units?: string | null; up?: string | null;
  merge?: boolean; seat?: boolean;
}
export interface PreparedModel {
  root: Group;
  parts: Record<BoardKind, Object3D[]>;
  comps: Map<string, PreparedComponent>;
  loose: Object3D[];
  meshes: Mesh[];
  board: { bottom: number; top: number; thickness: number } | null;
  boardBox: Box3;
  bounds: Box3;
  report: {
    method: MatchResult['method']; byName: number; byPosition: number; matched: number; expected: number;
    ambiguous: string[]; unmatched: string[]; loose: number; offset: { x: number; y: number }; mirrored: boolean;
    up: 'x' | 'y' | 'z'; scale: number; zShift: number; boardParts: Record<BoardKind, number>;
  };
}
export const PART_GROUP: Record<BoardKind, string>;
export function prepareModel(model: Object3D | LoadedGLB | { scene: Object3D }, components?: Component[], opts?: PrepareOptions): PreparedModel;
export function orientModel(object: Object3D, opts?: { up?: string | null; units?: string | null; boardSizeMm?: Vec2 | null }): { up: 'x' | 'y' | 'z'; scale: number };
export function mergeObject(object: Object3D): Group;
export function disposeObject(object: Object3D): void;
