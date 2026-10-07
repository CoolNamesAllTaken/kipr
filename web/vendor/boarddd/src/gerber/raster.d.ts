// boarddd/gerber: moved from CoolNamesAllTaken/wasm-gerber-viewer packages/wasm-gerber-renderer/raster.d.ts at 92976b5
// (earlier history lives there).
import type { GerberRenderer } from "../../third_party/wasm-gerber-renderer/core/index.js";
import type { PixelRect } from "./view.js";

export declare function readRendererPixels(
  renderer: GerberRenderer,
  options?: { rect?: PixelRect | null; bottomUp?: boolean; into?: Uint8Array | null },
): { pixels: Uint8Array; width: number; height: number };
export declare function hasInk(canvas: HTMLCanvasElement | OffscreenCanvas, minShare?: number): boolean;
export declare function copyScaled(
  source: CanvasImageSource & { width: number; height: number },
  maxPx?: number,
): HTMLCanvasElement | OffscreenCanvas | null;
export declare function flattenOnto(
  source: CanvasImageSource & { width: number; height: number },
  color: string,
): HTMLCanvasElement | OffscreenCanvas;
