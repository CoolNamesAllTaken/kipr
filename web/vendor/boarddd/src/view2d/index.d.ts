// boarddd/view2d: see index.js. World coordinates are board mm, y up (the Gerber frame).
import type { FrameBounds, GerberRenderer, GerberSource, RGBColor } from '../../third_party/wasm-gerber-renderer/core/index.js';
import type { BoardDescription, BoardLayerOptions } from '../gerber/board.js';
import type { DiffSide, DiffStyle } from '../gerber/diff.js';
import type { BoardPalette, BoardPaletteOptions } from '../gerber/palette.js';
import type { LayerRoleName, LayerSide } from '../gerber/layers.js';

export type Bounds = FrameBounds;
/** A view: the world point at the pane centre and CSS px per mm. */
export type View = { cx: number; cy: number; s: number };
/** What a view shows, independent of pane size: centre and visible width (mm). */
export type Region = { cx: number; cy: number; w: number };

// --- math.js
export declare const MIN_SCALE: number;
export declare const MAX_SCALE: number;
export declare function boundsSize(b: Bounds): { w: number; h: number };
export declare function fitBounds(bounds: Bounds, pw: number, ph: number, pad?: number, padPx?: number): View;
export declare function clampScale(s: number, min?: number, max?: number): number;
export declare function toScreen(v: View, pw: number, ph: number, x: number, y: number, flip?: boolean): [number, number];
export declare function toWorld(v: View, pw: number, ph: number, px: number, py: number, flip?: boolean): [number, number];
export declare function zoomAt(v: View, pw: number, ph: number, px: number, py: number, factor: number, flip?: boolean, min?: number, max?: number): View;
export declare function panBy(v: View, dx: number, dy: number, flip?: boolean): View;
export declare function visibleBounds(v: View, pw: number, ph: number): Bounds;
export declare function regionOf(v: View, pw: number): Region;
export declare function viewForRegion(r: Region, pw: number): View;
export declare function viewForBox(b: Bounds, pw: number, ph: number, options?: { minMm?: number; pad?: number }): View;
export declare function rasterMatrix(v: View, pw: number, ph: number, rect: Bounds, r: number, flip?: boolean): [number, number, number, number, number, number];
export declare function worldMatrix(v: View, pw: number, ph: number, flip?: boolean): [number, number, number, number, number, number];
export declare function stepScale(cssPerMm: number, dpr?: number, min?: number): number;
export declare function budgetScale(wMm: number, hMm: number, options?: { maxEdge?: number; maxPixels?: number }): number;
export declare function rasterRect(b: Bounds, r: number): { rect: Bounds; width: number; height: number };
export declare function intersect(a: Bounds, b: Bounds): Bounds | null;
export declare function grow(b: Bounds, frac: number): Bounds;
export declare function contains(outer: Bounds, inner: Bounds, eps?: number): boolean;

// --- content.js
export type ImageInput = string | Blob | HTMLImageElement | HTMLCanvasElement | ImageBitmap | OffscreenCanvas;
export type StackLayer = {
  source: GerberSource;
  color?: RGBColor;
  alpha?: number;
  /** 'drill' for Excellon (also `role: 'drill'`). */
  kind?: 'gerber' | 'drill';
  role?: LayerRoleName;
  name?: string;
  /** false: skipped. */
  visible?: boolean;
  /** Drawn inverted (a solder mask: the file marks the openings), filled to the content's outline when it has one. */
  inverted?: boolean;
};
/** World mm. `[board, ...cutouts]` or `{ board, cutouts }`. */
export type Ring = Array<[number, number]>;
export type Outline = Ring[] | { board: Ring; cutouts?: Ring[] };
/** A drill in world mm; a slot has (x2, y2); a filled hole is not cut. */
export type Hole = { x: number; y: number; d?: number; diameter?: number; x2?: number | null; y2?: number | null; filled?: boolean };
export type LayersOptions = {
  /** Inverted layers fill to it; the layers are clipped to it (even-odd) unless `clip: false`. */
  outline?: Outline | null;
  clip?: boolean;
  /** CSS colour inside the outline, under the layers (laminate). */
  substrate?: string | null;
  /** Cut out of the drawing: what is under the stage shows through. */
  holes?: Hole[] | null;
  /** World area to rasterise over (default: the stage bounds), e.g. a drawing sheet past the board. */
  rect?: Bounds | null;
};
/** Where a repeat places its content: turned `rotation` degrees CCW about the world origin, then moved by (x, y). */
export type Placement = { x?: number; y?: number; rotation?: number };
export type FaceContent = { type: 'face'; board: BoardDescription; side: 'top' | 'bottom'; palette: BoardPalette | BoardPaletteOptions; options: BoardLayerOptions };
export type LayersContent = { type: 'layers'; layers: StackLayer[]; options: Omit<LayersOptions, 'outline' | 'rect'> & { outline: Ring[] | null }; rect?: Bounds | null };
export type RepeatContent = { type: 'repeat'; content: Content; placements: Placement[]; rect: Bounds | null };
export type DiffContent = {
  type: 'diff'; base: DiffSide; head: DiffSide; regions: boolean;
  options: { style?: DiffStyle; colors?: { removed?: RGBColor; added?: RGBColor; unchanged?: RGBColor }; showUnchanged?: boolean; underlay?: Array<{ source: GerberSource; name?: string; color?: RGBColor; alpha?: number }> };
};
export type ImageContent = { type: 'image'; src: ImageInput; rect: Bounds };
/** An image, or an image over its own world rect. */
export type InkSide = ImageInput | { src: ImageInput; rect: Bounds };
export type InkDiffContent = {
  type: 'inkdiff'; base: InkSide | null; head: InkSide | null; rect: Bounds;
  options: { mode: 'ink' | 'alpha'; tol: number; colors: InkColors; regionGapMm: number };
};
export type RasterJob = { rect: Bounds; width: number; height: number; r: number };
export type DrawContent = { type: 'draw'; draw: (ctx: CanvasRenderingContext2D, job: RasterJob) => void | Promise<void>; rect: Bounds | null };
export type Content = FaceContent | LayersContent | RepeatContent | DiffContent | ImageContent | InkDiffContent | DrawContent;

/** A changed area in world mm. */
export type ChangeRegion = Bounds & { pixels: number; kind?: 'added' | 'removed' | 'mixed' };
export type ContentInfo = {
  failures?: Array<{ name: string | null; error: string }>;
  counts?: { removed: number; added: number; unchanged?: number | null; common?: number };
  regions?: ChangeRegion[];
  ids?: unknown;
};

export declare function face(board: BoardDescription, options?: BoardLayerOptions & { palette?: BoardPalette | BoardPaletteOptions }): FaceContent;
export declare function layers(list: StackLayer[], options?: LayersOptions): LayersContent;
/** `content` drawn once over `rect` (default: its own) and placed at each placement (a panel's copies). */
export declare function repeat(content: Content, placements: Placement[], rect?: Bounds | null): RepeatContent;
/** Rings from either outline form; null without a usable board ring. */
export declare function outlineRings(outline: Outline | null | undefined): Ring[] | null;
/** Round holes and stadium slots (world mm) as one Path2D; filled holes left out. */
export declare function holesPath(holes: Hole[] | null | undefined): Path2D;
export declare function diff(
  base: DiffSide,
  head: DiffSide,
  options?: DiffContent['options'] & { /** Also report changed regions (an extra analysis pass). */ regions?: boolean },
): DiffContent;
export declare function image(src: ImageInput, rect: Bounds): ImageContent;
export declare function inkdiff(base: InkSide | null, head: InkSide | null, rect: Bounds, options?: Partial<InkDiffContent['options']>): InkDiffContent;
export declare function draw(fn: DrawContent['draw'], rect?: Bounds | null): DrawContent;
export declare function contentRect(c: Content): Bounds | null;
export declare function isEmptySource(source: GerberSource, drill?: boolean): boolean;
export declare function decodeImage(src: ImageInput | null): Promise<CanvasImageSource | null>;
/** Run `work` when no other frame of `renderer` is in flight (shared with every stage on the page). */
export declare function inTurn<T>(renderer: GerberRenderer, work: () => T | Promise<T>): Promise<T>;
export declare function renderContent(
  c: Content,
  job: RasterJob,
  getRenderer: () => Promise<GerberRenderer>,
): Promise<{ canvas: HTMLCanvasElement | OffscreenCanvas; info: ContentInfo }>;

// --- layers.js
export type StackEntry = {
  id: string; name: string; source: GerberSource; role: LayerRoleName; side: LayerSide;
  index: number | null; plated: boolean | null; color: RGBColor; alpha: number; visible: boolean;
};
export type StackFile = { name: string; source: GerberSource } & Partial<Omit<StackEntry, 'name' | 'source'>>;
export declare const LAYER_ALPHA: Readonly<Record<LayerRoleName, number>>;
export declare function layerColor(l: { role: LayerRoleName; side?: LayerSide; index?: number | null; plated?: boolean | null }): RGBColor;
export declare function layerRank(l: { role: LayerRoleName; side?: LayerSide; index?: number | null }): number;
export declare function sortLayers<T extends { role: LayerRoleName; side?: LayerSide; index?: number | null; name: string }>(layers: T[]): T[];
export declare function defaultVisible(l: { role: LayerRoleName }): boolean;
export declare function layerStack(files: StackFile[]): StackEntry[];
export declare function faceBoard(files: Array<{ name: string; source: GerberSource }>, side?: 'top' | 'bottom'): BoardDescription;

// --- hit.js
export type Shape<D = unknown> = {
  kind: string;
  bounds: Bounds;
  area?: number;
  /** Distance (mm) from a point to the shape; 0 inside. */
  distance(x: number, y: number): number;
  data: D;
};
export declare function segmentShape<D = unknown>(x1: number, y1: number, x2: number, y2: number, width?: number, data?: D): Shape<D> & { x1: number; y1: number; x2: number; y2: number; width: number };
export declare function rectShape<D = unknown>(x: number, y: number, w: number, h: number, data?: D): Shape<D>;
export declare function circleShape<D = unknown>(x: number, y: number, d: number, data?: D): Shape<D>;
export declare function polygonShape<D = unknown>(points: Array<[number, number]>, data?: D): Shape<D>;
export type HitIndex<D = unknown> = {
  shapes: Shape<D>[];
  /** The shape picked at a point: the smallest one under it, else the nearest within `slack` mm. */
  at(x: number, y: number, slack?: number): Shape<D> | null;
  all(x: number, y: number, slack?: number): Shape<D>[];
};
export declare function createHitIndex<D = unknown>(shapes: Shape<D>[], options?: { cell?: number }): HitIndex<D>;

// --- viewstate.js
export type ViewState = { region?: Region | null; mode?: string; opacity?: number; swipe?: number };
export declare function formatRegion(r: Region | null | undefined): string | null;
export declare function parseRegion(v: unknown): Region | null;
export declare function sameRegion(a: Region | null | undefined, b: Region | null | undefined): boolean;
export declare function formatSlider(v: number, on?: boolean): string | null;
export declare function parseSlider(v: unknown): number | null;
export declare function formatViewState(state?: ViewState): { z?: string; mode?: string; sw?: string; op?: string };
export declare function parseViewState(params: URLSearchParams | Record<string, string | undefined> | null | undefined): ViewState;

// --- inkdiff.js
export type InkColors = { removed: number[]; added: number[]; common: number[] };
export declare const DIFF_COLORS: InkColors;
export declare function inkMask(rgba: ArrayLike<number>, w: number, h: number, options?: { alphaMin?: number; lumMax?: number }): Uint8Array;
export declare function alphaMask(rgba: ArrayLike<number>, w: number, h: number, alphaMin?: number): Uint8Array;
export declare function dilate(mask: Uint8Array, w: number, h: number, r: number): Uint8Array;
export declare function diffMasks(base: Uint8Array, head: Uint8Array, w: number, h: number, tol?: number): {
  removed: Uint8Array; added: Uint8Array; common: Uint8Array; counts: { removed: number; added: number; common: number };
};
export declare function paintDiff<T extends { [i: number]: number; length: number }>(out: T, d: { removed: Uint8Array; added: Uint8Array; common: Uint8Array }, colors?: InkColors): T;
export type PixelRegion = { x: number; y: number; w: number; h: number; pixels: number };
export declare function regions(mask: Uint8Array, w: number, h: number, options?: { gap?: number; minPixels?: number; max?: number }): PixelRegion[];
export declare function orMask(a: Uint8Array, b: Uint8Array): Uint8Array;
export declare function inkDiff(
  base: ArrayLike<number> | null,
  head: ArrayLike<number> | null,
  w: number,
  h: number,
  options?: { mode?: 'ink' | 'alpha'; tol?: number; colors?: InkColors; gap?: number; minPixels?: number; max?: number },
): { rgba: Uint8ClampedArray; counts: { removed: number; added: number; common: number }; regions: PixelRegion[] };

// --- stage.js
export type SceneLayer = {
  content: Content;
  opacity?: number;
  /** Visible horizontal band of the pane, fractions 0..1 (swipe). */
  clip?: { x0?: number; x1?: number } | null;
  className?: string;
};
export type PaneSpec = {
  /** Extra classes on the pane element (it always has `bd2-pane`, and `data-side` when `side` is set). */
  className?: string;
  label?: string;
  labelRight?: string;
  /** Free tag handed back in events and overlay contexts ('base', 'head', ...). */
  side?: string | null;
  /** Text shown when the pane has nothing to show. */
  missing?: string | null;
  layers?: SceneLayer[];
};
export type PointerHit = { x: number; y: number; px: number; py: number; pane: number; side: string | null; event: PointerEvent };
export type MeasureResult = { a: { x: number; y: number }; b: { x: number; y: number }; dx: number; dy: number; distance: number };
export type OverlayContext = {
  pane: number;
  side: string | null;
  label: string | null;
  width: number;
  height: number;
  view: View;
  flip: boolean;
  toScreen(x: number, y: number): [number, number];
  toWorld(px: number, py: number): [number, number];
  mmPerPx(): number;
  svg: typeof svgEl;
};
export type StageEvents = {
  view: { view: View; region: Region | null; flip: boolean };
  click: PointerHit;
  move: PointerHit;
  leave: { pane: number };
  measure: { points: Array<{ x: number; y: number }>; result: MeasureResult | null };
  render: { pane: number; layer: number; content: Content; info: ContentInfo; r: number };
  error: { error: unknown; pane?: number; layer?: number; content?: Content };
  /** A re-render is scheduled or running (true), or the panes are up to date (false). */
  busy: { busy: boolean };
};
export type StageState = { region: Region | null; flip: boolean; tool: 'pan' | 'measure'; measure: Array<{ x: number; y: number }> };
export type StageOptions = {
  /** World bounds to fit and to rasterise gerber content over. */
  bounds?: Bounds | null;
  /** Needed for gerber content only; a function is called once, on first use. */
  renderer?: GerberRenderer | Promise<GerberRenderer> | (() => GerberRenderer | Promise<GerberRenderer>);
  flip?: boolean;
  /** Fit margin, fraction of the pane (default 0.02). */
  padding?: number;
  /** Fit margin in CSS px on every side, besides `padding` (default 0). */
  paddingPx?: number;
  /** false: a picture, no pan / zoom / pointer events (they reach what is under the stage). Default true. */
  interactive?: boolean;
  /**
   * Render at the screen's own resolution (CSS px per mm x dpr, not sqrt(2) steps), each tile on the
   * device pixel grid: at rest a tile pixel is a screen pixel, as crisp as drawing straight to the
   * screen. Default false.
   */
  pixelSnap?: boolean;
  region?: Region | null;
  /** Re-render this long after the view stops moving (default 180 ms). */
  settleMs?: number;
  /** Raster limits per tile (defaults 4096 px edge, 16e6 pixels). */
  maxEdge?: number;
  maxPixels?: number;
  minScale?: number;
  maxScale?: number;
  /** Backing pixels per CSS pixel (default devicePixelRatio, at most 2). */
  dpr?: number;
  /** CSS background of every pane. */
  background?: string | null;
  /** Render at least this many px/mm (within maxEdge / maxPixels), so zooming in starts sharper and pixel
   * results (ink diff regions) don't get coarser when zoomed out. Default 0. */
  minRender?: number;
  /** Distance label on the measure line (default true). */
  measureLabel?: boolean;
  /** Add a <style> with STAGE_CSS (default true). false under a CSP without 'unsafe-inline' styles: ship STAGE_CSS in a stylesheet. */
  injectCss?: boolean;
};
export type TileStats = { r: number; width: number; height: number; rect: Bounds } | null;
export type Stage = {
  readonly root: HTMLDivElement;
  readonly panes: Array<{ el: HTMLDivElement; index: number; side: string | null; label: string | null }>;
  readonly flip: boolean;
  readonly bounds: Bounds | null;
  readonly tool: 'pan' | 'measure';
  setScene(panes: PaneSpec[]): void;
  setLayer(pane: number, layer: number, change: { opacity?: number; clip?: SceneLayer['clip'] }): void;
  setBounds(b: Bounds | null): void;
  setFlip(flip: boolean): void;
  fit(): void;
  zoomTo(b: Bounds, options?: { minMm?: number; pad?: number }): void;
  getView(): View;
  setView(v: View): void;
  getRegion(): Region | null;
  setRegion(r: Region | null): void;
  getState(): StageState;
  setState(s: Partial<StageState>): void;
  toScreen(x: number, y: number, pane?: number): [number, number];
  toWorld(px: number, py: number, pane?: number): [number, number];
  mmPerPx(): number;
  setTool(tool: 'pan' | 'measure'): void;
  getMeasure(): MeasureResult | null;
  setMeasure(points: Array<{ x: number; y: number }>): void;
  addOverlay(o: { space?: 'world' | 'screen'; draw: (g: SVGGElement, ctx: OverlayContext) => void; className?: string }): { invalidate(): void; remove(): void };
  on<K extends keyof StageEvents>(name: K, fn: (e: StageEvents[K]) => void): () => void;
  info(pane?: number, layer?: number): ContentInfo | null;
  invalidate(): void;
  ready(): Promise<void>;
  stats(): { frames: number; renders: number; pixels: number; tiles: Array<Array<{ base: TileStats; detail: TileStats }>> };
  capture(pane?: number, options?: { scale?: number; background?: string | null }): HTMLCanvasElement;
  destroy(): void;
};
export declare function createStage(container: HTMLElement, options?: StageOptions): Stage;
/** The stage's base CSS (pane layout, overlay, labels, measure), as createStage injects it. */
export declare const STAGE_CSS: string;
export declare function svgEl<K extends keyof SVGElementTagNameMap>(tag: K, attrs?: Record<string, string | number | null | undefined>, parent?: Element | null): SVGElementTagNameMap[K];
export declare function measureText(m: MeasureResult | null): string;

// --- compare.js
export type CompareMode = 'side' | 'diff' | 'onion' | 'swipe' | 'base' | 'head';
export declare const COMPARE_MODES: readonly CompareMode[];
export type CompareOptions = {
  /** A side's content (several: stacked); null: that side does not exist. */
  base?: Content | Content[] | null;
  head?: Content | Content[] | null;
  /** Default: defaultDiff(base, head); null: no diff mode. */
  diff?: Content | null;
  /** Drawn faint under the diff (e.g. the board face). */
  underlay?: Content | Content[] | null;
  mode?: CompareMode;
  /** Onion head opacity, swipe divider (0..1); default 0.5. */
  opacity?: number;
  swipe?: number;
  labels?: { base?: string; head?: string; diff?: string };
  missing?: (side: 'base' | 'head') => string;
  /** Draggable swipe divider (default true). */
  handle?: boolean;
  /** After the user drags the divider. */
  onChange?: (state: CompareState) => void;
};
export type CompareState = StageState & { mode: CompareMode; opacity: number; swipe: number };
export type Compare = {
  readonly mode: CompareMode;
  readonly opacity: number;
  readonly swipe: number;
  readonly modes: CompareMode[];
  setMode(mode: CompareMode): void;
  setOpacity(v: number): void;
  setSwipe(v: number): void;
  set(content: { base?: Content | Content[] | null; head?: Content | Content[] | null; diff?: Content | null; underlay?: Content | Content[] | null }): void;
  getState(): CompareState;
  /** getState() or parseViewState(); an unknown mode is ignored. */
  setState(s: Partial<Omit<CompareState, 'mode'>> & ViewState): void;
  destroy(): void;
};
export declare function createCompare(stage: Stage, options?: CompareOptions): Compare;
export declare function defaultDiff(base: Content | Content[] | null | undefined, head: Content | Content[] | null | undefined): DiffContent | InkDiffContent | null;
