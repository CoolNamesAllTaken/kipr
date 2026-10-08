// boarddd/geom: pure geometry, no three.js. Board frame: mm, x right, y up, z up; KiCad frame: mm, y down.
export type Vec2 = [number, number];
export type Vec3 = [number, number, number];
export type Loop = Vec2[];
/** Column-major 4x4 (three's Matrix4.fromArray order). */
export type Mat4 = number[];
export interface Bounds { minX: number; maxX: number; minY: number; maxY: number }
export interface Outline { board: Loop; cutouts?: Loop[]; approximate?: boolean }

// frames
export const BOARD_THICKNESS: number;
export const COPPER_THICKNESS: number;
export function kicadToBoard(x: number, y: number): Vec2;
export function boardToKicad(x: number, y: number): Vec2;
export interface KicadModelPlacement { offset?: Vec3; rotate?: Vec3; scale?: Vec3 }
/** KiCad 3D-viewer placement: T(offset) Rz(-rz) Ry(-ry) Rx(-rx) S(scale). */
export function kicadModelMatrix(model?: KicadModelPlacement): Mat4;
export function applyMatrix(m: Mat4, p: Vec3): Vec3;

// loops
export function segmentsFor(radius: number): number;
export function signedArea2(loop: Loop): number;
export function isClockwise(loop: Loop): boolean;
export function counterClockwise(loop: Loop): Loop;
export function clockwise(loop: Loop): Loop;
export function pointToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number): number;
export function clearance(loop: Loop, x: number, y: number): { inside: boolean; distance: number };
export function loopBounds(...loops: Loop[]): Bounds;
export function padBounds(b: Bounds, margin: number): Bounds;
/** A circle, wound clockwise. */
export function ringPoints(cx: number, cy: number, radius: number, segments?: number): Loop;
/** A stadium (routed slot / oval drill): semicircles around both ends joined by straight flanks; CCW. */
export function slotPoints(x1: number, y1: number, x2: number, y2: number, radius: number, segments?: number): Loop;
export function loopAt(ends: Vec2[], radius: number, segments?: number): Loop;
export function rectLoop(minX: number, minY: number, maxX: number, maxY: number): Loop;
/** From a KiCad board box (KiCad mm, y down). */
export function rectOutline(box: { origin_mm: Vec2; size_mm: Vec2 }): Outline;
export function outlinesDiffer(a: Outline | null, b: Outline | null, tol?: number): boolean;
/** A stroked polyline as one stadium per segment (round caps and joins). */
export function strokeLoops(pts: Vec2[], width: number, closed?: boolean): Loop[];

// holes
export const HOLE_BUDGET: number;
export const PLATING_MM: number;
/** A drill as wasm-gerber-renderer's parseExcellon gives it (board mm); `d` is accepted for `diameter`. */
export interface Hole { x: number; y: number; diameter?: number; d?: number; plated?: boolean; x2?: number | null; y2?: number | null; filled?: boolean; via?: boolean }
export interface KeptHole { plated: boolean; radius: number; ends: Vec2[]; extent: number }
export interface HoleReport { kept: KeptHole[]; leftOut: { count: number; total: number; largest_mm: number } | null; rejected: number }
export function usableHoles(holes: Hole[], outline: Outline, budget?: number): HoleReport;
export function holeLoop(hole: KeptHole, grow?: number): Loop;
/** Paste deposit height, mm (0.12). */
export const PASTE_THICKNESS: number;
/** Plated round holes with drill diameter <= upTo mm marked filled (and capped); a new array. */
export function fillHoles<H extends Hole>(holes: H[], upTo: number | null | undefined): (H & { filled?: boolean })[];
/** Plated round drill sizes, smallest first, with counts (vias: marked ViaDrill). */
export function drillSizes(holes: Hole[]): { diameter: number; count: number; vias: number }[];

// pads (KiCad frame, y down; see src/geom/pads.js)
export interface Pad {
  number: string;
  type: 'smd' | 'thru_hole' | 'np_thru_hole' | 'connect' | string;
  shape: 'circle' | 'rect' | 'oval' | 'roundrect' | 'chamfered_rect' | 'trapezoid' | 'custom' | string;
  at: [number, number, number?];
  size: Vec2;
  offset?: Vec2;
  drill?: { shape?: 'circle' | 'oval'; size: Vec2 | number; offset?: Vec2 } | number | null;
  layers: string[];
  roundrect_rratio?: number | null;
  chamfer_ratio?: number | null;
  chamfer?: string[] | null;
  rect_delta?: Vec2 | null;
  anchor?: 'circle' | 'rect' | null;
  primitives?: { pts: Vec2[] }[];
}
export function padOffset(pad: Pad): Vec2;
export function padToKicad(pad: Pad, p: Vec2): Vec2;
export function padOutline(pad: Pad, segments?: number): { outer: Loop; extra: Loop[] };
/** The copper loops in the footprint's board frame (y up): [outer, ...custom primitives]. */
export function padCopperLoops(pad: Pad, segments?: number): Loop[];
export function padDrill(pad: Pad): { w: number; h: number; oval: boolean } | null;
export function padHoleCenter(pad: Pad): Vec2;
export function padDrillSlot(pad: Pad, grow?: number): { ends: [Vec2, Vec2]; radius: number } | null;
export function padDrillLoop(pad: Pad, options?: { grow?: number; segments?: number }): Loop | null;
export function padCopperSides(pad: Pad): { top: boolean; bottom: boolean };
export function padHasCopper(pad: Pad): boolean;
