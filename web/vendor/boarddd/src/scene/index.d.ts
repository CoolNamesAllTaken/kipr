import type { WebGLRenderer, Scene, PerspectiveCamera, Group, Object3D, Box3, Texture, DirectionalLight } from 'three';

export type Vec3 = [number, number, number];
export interface View { dir: Vec3; up: Vec3 }
export type ViewName = 'top' | 'bottom' | 'front' | 'side' | 'back' | 'left' | 'right' | 'iso' | 'isoBottom';
export const VIEWS: Record<ViewName, View>;
export function fitCamera(box: { min: Vec3; max: Vec3 }, view: View, fovDeg: number, aspect: number, pad?: number):
  { position: Vec3; target: Vec3; up: Vec3; distance: number; radius: number };
export function clipPlanes(cameraPos: Vec3, sphere: { center: Vec3; radius: number }): { near: number; far: number };

export interface PickHit { object: Object3D; ref: string | null; point: Vec3; /** pane index (0 without panes) */ pane: number }
export interface ViewerOptions {
  controls?: 'trackball' | 'orbit';
  theme?: 'light' | 'dark';
  /** [top, bottom] gradient, one CSS colour, null (transparent); default: the theme's gradient. */
  background?: [string, string] | string | null;
  viewCube?: boolean;
  fov?: number;
  pixelRatio?: number;
  environment?: boolean;
  antialias?: boolean;
  /** Keep the drawn frame readable after compositing (toDataURL / drawImage outside a frame). Default false. */
  preserveDrawingBuffer?: boolean;
  onPick?(hit: PickHit | null): void;
  onRender?(): void;
}
export interface CaptureOptions { width?: number; height?: number; transparent?: boolean; viewCube?: boolean; type?: string }
export interface Viewer {
  readonly renderer: WebGLRenderer;
  readonly scene: Scene;
  readonly camera: PerspectiveCamera;
  readonly content: Group;
  readonly canvas: HTMLCanvasElement;
  readonly controls: { target: import('three').Vector3; update(): unknown; dispose(): void };
  readonly stats: { frames: number; requests: number };
  readonly theme: 'light' | 'dark';
  add<T extends Object3D>(...objects: T[]): T;
  remove(object: Object3D, opts?: { dispose?: boolean }): void;
  clear(opts?: { dispose?: boolean }): void;
  requestRender(): void;
  render(): void;
  setView(view: ViewName | View, opts?: { fit?: boolean; box?: Box3 | null; pad?: number }): Viewer;
  fit(view?: ViewName | View | null, box?: Box3 | null, pad?: number): Viewer;
  setTheme(theme: 'light' | 'dark'): void;
  setBackground(spec: [string, string] | string | null | 'theme'): void;
  setControls(kind: 'trackball' | 'orbit'): void;
  pick(clientX: number, clientY: number, opts?: { filter?: (object: Object3D) => boolean }): PickHit | null;
  /** Side-by-side panes with one camera: each inner array is what only that pane shows; null = one view. */
  setPanes(panes: Object3D[][] | null): Viewer;
  readonly panes: Object3D[][] | null;
  paneRect(index?: number): { x: number; y: number; width: number; height: number };
  /** The cube face under a client point ('top', ...), '' for its corner off the cube, null elsewhere. */
  cubeAt(clientX: number, clientY: number): string | null;
  cubeFacePoint(face: string): { x: number; y: number } | null;
  capture(opts?: CaptureOptions): string;
  captureBlob(opts?: CaptureOptions): Promise<Blob>;
  resize(): void;
  on(event: 'render' | 'view', fn: () => void): () => void;
  on(event: 'cube', fn: (face: string) => void): () => void;
  dispose(opts?: { content?: boolean }): void;
}
export function createViewer(el: HTMLElement, opts?: ViewerOptions): Viewer;

export class ViewCube {
  constructor(theme?: 'light' | 'dark');
  setTheme(theme: 'light' | 'dark'): void;
  draw(renderer: WebGLRenderer, camera: PerspectiveCamera, target: import('three').Vector3, width: number, height: number): void;
  faceAt(x: number, y: number, width: number, height: number): string | null;
  facePoint(face: string, width: number, height: number): { x: number; y: number } | null;
  setHover(face: string | null): boolean;
  dispose(): void;
}
export const BACKGROUNDS: Record<'light' | 'dark', [string, string]>;
export function gradientTexture(stops: [string, string]): Texture;
export function roomEnvironment(renderer: WebGLRenderer): import("three").WebGLRenderTarget;
export function kicadLights(): { group: Group; headlight: DirectionalLight; update(camera: PerspectiveCamera, target: import('three').Vector3): void; dispose(): void };
