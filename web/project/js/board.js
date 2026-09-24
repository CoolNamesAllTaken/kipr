// Board model helpers for the layout view. Pure (no DOM): layer ordering, colours, the KiCad <-> gerber
// frame, and which layers make up a realistic face. Colours are renderer triples (0..1).
import { arr, obj, bbox, assetUrl } from './util.js';

// KiCad's default layer colours, for the per-layer view (dark background, like pcbnew).
const LAYER_COLOR = {
  'F.Cu': [0.78, 0.20, 0.20], 'B.Cu': [0.30, 0.50, 0.77], 'In1.Cu': [0.50, 0.78, 0.50], 'In2.Cu': [0.81, 0.49, 0.17],
  'In3.Cu': [0.96, 0.46, 0.66], 'In4.Cu': [0.58, 0.42, 0.95],
  'F.Mask': [0.85, 0.39, 1.0], 'B.Mask': [0.01, 1.0, 0.93], 'F.Paste': [0.71, 0.63, 0.63], 'B.Paste': [0.0, 0.76, 0.76],
  'F.SilkS': [0.95, 0.93, 0.63], 'B.SilkS': [0.91, 0.70, 0.65], 'F.Silkscreen': [0.95, 0.93, 0.63], 'B.Silkscreen': [0.91, 0.70, 0.65],
  'F.Fab': [0.69, 0.69, 0.69], 'B.Fab': [0.35, 0.36, 0.52], 'F.CrtYd': [1.0, 0.15, 0.89], 'B.CrtYd': [0.15, 0.91, 1.0],
  'F.Courtyard': [1.0, 0.15, 0.89], 'B.Courtyard': [0.15, 0.91, 1.0],
  'Edge.Cuts': [0.82, 0.82, 0.0], PTH: [0.86, 0.86, 0.86], NPTH: [0.55, 0.85, 0.95],
  'Dwgs.User': [0.76, 0.76, 0.76], 'Cmts.User': [0.35, 0.55, 0.9],
};
const KIND_COLOR = {
  copper: [0.8, 0.6, 0.2], mask: [0.5, 0.3, 0.8], paste: [0.7, 0.7, 0.72], silk: [0.95, 0.95, 0.9],
  outline: [0.82, 0.82, 0.0], fab: [0.6, 0.6, 0.65], courtyard: [0.9, 0.3, 0.8], user: [0.6, 0.6, 0.6], drill: [0.86, 0.86, 0.86],
};

// What a board looks like (from gentoo's palette.js): substrate, finishes, mask and silk colours.
export const FR4 = [0.79, 0.70, 0.49];
const FINISH = { enig: [0.85, 0.70, 0.30], hasl: [0.78, 0.78, 0.80], osp: [0.80, 0.55, 0.35], default: [0.84, 0.66, 0.30] };
const MASK = {
  green: [0.05, 0.32, 0.16], red: [0.55, 0.06, 0.06], blue: [0.05, 0.16, 0.45], black: [0.06, 0.06, 0.07],
  white: [0.92, 0.92, 0.90], yellow: [0.80, 0.70, 0.10], purple: [0.30, 0.10, 0.40], matte_black: [0.06, 0.06, 0.07],
};
const SILK = { white: [0.96, 0.96, 0.96], black: [0.08, 0.08, 0.08], yellow: [0.95, 0.85, 0.2] };

function colorFrom(v, table, fallback) {
  if (typeof v !== 'string') return fallback;
  const s = v.trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (table[s]) return table[s];
  if (/^#[0-9a-f]{6}$/.test(s)) return [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16) / 255);
  return fallback;
}

export function boardStyle(board) {
  const b = obj(board) || {};
  return {
    mask: colorFrom(b.mask_color, MASK, MASK.green),
    silk: colorFrom(b.silk_color, SILK, SILK.white),
    copper: FINISH[String(b.finish || '').toLowerCase()] || FINISH.default,
    fr4: FR4,
  };
}

export function layerColor(layer) {
  return LAYER_COLOR[layer.id] || KIND_COLOR[layer.kind] || [0.6, 0.6, 0.6];
}

export function cssColor(c, a = 1) {
  return `rgba(${c.map((v) => Math.round(v * 255)).join(', ')}, ${a})`;
}

/** Valid layers from the contract. */
export function layerList(pcb) {
  return arr(obj(pcb)?.layers).filter((l) => obj(l) && typeof l.id === 'string' && l.id.length < 80);
}

/** Stack order bottom -> top: back side, inner, front, then outline and drills on top. */
export function layerRank(l) {
  const kindRank = { copper: 0, mask: 1, paste: 2, silk: 3, fab: 4, courtyard: 5, user: 6 };
  if (l.kind === 'outline') return 400;
  if (l.kind === 'drill') return 500;
  const k = kindRank[l.kind] ?? 6;
  if (l.side === 'bottom') return 100 + (6 - k);
  if (l.side === 'inner') {
    const n = /^In(\d+)\./.exec(l.id);
    return 200 + (n ? +n[1] : 50) / 100;
  }
  if (l.side === 'top') return 300 + k;
  return 350 + k;
}

export function sortLayers(layers) {
  return [...layers].sort((a, b) => layerRank(a) - layerRank(b) || a.id.localeCompare(b.id));
}

/** Layers shown by default in the per-layer view: copper, silk, outline, drills. */
export function defaultOn(l) {
  return ['copper', 'silk', 'outline', 'drill'].includes(l.kind);
}

/**
 * The layers of one realistic face, in paint order: substrate (outline), copper, mask, silk, drills.
 * Returns {outline, copper, mask, silk, drills[]} (entries may be null).
 */
export function faceLayers(layers, side) {
  const on = (kind) => layers.find((l) => l.kind === kind && l.side === side) || null;
  return {
    outline: layers.find((l) => l.kind === 'outline') || null,
    copper: on('copper'),
    mask: on('mask'),
    silk: on('silk'),
    drills: layers.filter((l) => l.kind === 'drill'),
  };
}

/** Gerber path of a layer on a side, or null. */
export function gerberOf(layer, side) {
  const p = obj(layer?.[side])?.gerber;
  return assetUrl(p) ? p : null;
}
export function svgOf(layer, side) {
  const p = obj(layer?.[side])?.svg;
  return assetUrl(p) ? p : null;
}

/** Board rect in KiCad mm {x, y, w, h}, from board.origin_mm + size_mm; null if not stated. */
export function boardRect(pcb) {
  const b = obj(obj(pcb)?.board);
  if (!b) return null;
  const o = b.origin_mm; const s = b.size_mm;
  return bbox(Array.isArray(o) && Array.isArray(s) ? [o[0], o[1], s[0], s[1]] : null);
}

/** KiCad coords of the gerber origin (default: gerbers in absolute KiCad coords, y negated). */
export function gerberOrigin(pcb) {
  const g = obj(obj(pcb)?.board)?.gerber_origin_mm;
  return Array.isArray(g) && g.length === 2 && g.every(Number.isFinite) ? g : [0, 0];
}

/** Gerber-frame bounds {minX, maxX, minY, maxY} of a KiCad-frame box. Gerber y = oy - kicad y. */
export function kicadBoxToGerber(box, origin = [0, 0]) {
  const [ox, oy] = origin;
  return { minX: box.x - ox, maxX: box.x + box.w - ox, minY: oy - (box.y + box.h), maxY: oy - box.y };
}

export function gerberPointToKicad(x, y, origin = [0, 0]) {
  return [x + origin[0], origin[1] - y];
}

/** Grow a box by `m` mm on every side. */
export function grow(b, m) {
  return { x: b.x - m, y: b.y - m, w: b.w + 2 * m, h: b.h + 2 * m };
}

/** Union of boxes (null entries ignored); null if none. */
export function union(boxes) {
  const bs = boxes.filter(Boolean);
  if (!bs.length) return null;
  const x0 = Math.min(...bs.map((b) => b.x)); const y0 = Math.min(...bs.map((b) => b.y));
  const x1 = Math.max(...bs.map((b) => b.x + b.w)); const y1 = Math.max(...bs.map((b) => b.y + b.h));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
