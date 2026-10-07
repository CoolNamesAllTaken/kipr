import type { GerberRenderer } from '../../third_party/wasm-gerber-renderer/core/index.js';

/** One ODB++ layer as a renderer source: `source` is handed to the renderer like a Gerber/Excellon file's text. */
export interface OdbLayerSource {
  /** Gerber-style file name: `f.cu.gtl`, `b.mask.gbs`, `drill_plated_f.cu-b.cu.drl`, `profile`. */
  name: string;
  kind: 'gerber' | 'drill';
  source: string;
}

export type OdbFileItem = File | { file: File; relativePath: string };

export interface LoadOdbJobOptions {
  /** Job label for messages (default: the file name). */
  name?: string;
  /** Step to load (default: the board step). */
  stepName?: string | null;
  /** A renderer whose wasm decompresses `.Z` members. */
  renderer?: GerberRenderer;
  /** Or the decoder itself: UNIX compress (`.Z`) bytes -> bytes. */
  decompressUnixZ?: (bytes: Uint8Array, maxOutputBytes: number) => Uint8Array | Promise<Uint8Array>;
  onWarning?: (label: string, message: string) => void;
  onInfo?: (label: string, message: string) => void;
  onStage?: (label: string, stage: string) => void;
}

/** Read an ODB++ job (.zip, .tgz/.tar.gz, .tar, or a dropped folder) into renderer layer sources. */
export function loadOdbJob(
  input: Blob | ArrayBuffer | ArrayBufferView | OdbFileItem[],
  options?: LoadOdbJobOptions,
): Promise<OdbLayerSource[]>;
