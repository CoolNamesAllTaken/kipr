// Component change list for the 3D PCBA viewer. Pure functions, no three.js (tested by
// tests/web-3d/diff.test.mjs).
//
// The backend already classifies every component (docs/CONTRACT-project.md, Pcba3d.components),
// so normally this only sorts and describes. When a status or `what` is missing -- an older
// backend, a hand-written fixture -- it is derived here from the two sides, so the viewer never
// has to trust a field that is not there.

import { naturalCompare } from './match.js';

// 'minor': the backend's minor: true (only a 3D model format swap such as .wrl -> .step, or a
// footprint library rename): listed, but not counted, tinted or marked as a change by default.
export const STATUSES = ['added', 'removed', 'moved', 'rotated', 'changed', 'minor', 'unchanged'];
const CHANGE_ORDER = Object.fromEntries(STATUSES.map((s, i) => [s, i]));

export const POSITION_EPS_MM = 0.001;
export const ROTATION_EPS_DEG = 0.01;

/** Fields shown for a component, in order, with how to print them. */
export const FIELDS = [
  ['value', 'Value'], ['footprint', 'Footprint'], ['side', 'Side'], ['position', 'Position'],
  ['rot', 'Rotation'], ['model', '3D model'], ['dnp', 'DNP'],
];

const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

/** Smallest difference between two angles in degrees, 0..180. */
export function angleDelta(a, b) {
  const d = (((num(b) - num(a)) % 360) + 540) % 360 - 180;
  return Math.abs(d);
}

/** What differs between two sides of one component, as contract `what` names. */
export function whatChanged(base, head) {
  if (!base || !head) return [];
  const what = [];
  if (Math.hypot(num(head.x) - num(base.x), num(head.y) - num(base.y)) > POSITION_EPS_MM) what.push('position');
  if (angleDelta(base.rot, head.rot) > ROTATION_EPS_DEG) what.push('rotation');
  for (const f of ['footprint', 'value', 'model', 'side', 'dnp']) {
    const a = base[f] ?? null, b = head[f] ?? null;
    if (JSON.stringify(a) !== JSON.stringify(b)) what.push(f);
  }
  return what;
}

/**
 * The status the contract would give: added/removed by presence; `changed` wins over a move
 * (a different part is the bigger news), then `moved`, then `rotated`.
 */
export function deriveStatus(base, head, what = whatChanged(base, head)) {
  if (!base && head) return 'added';
  if (base && !head) return 'removed';
  if (!base && !head) return 'unchanged';
  if (what.some((w) => ['footprint', 'value', 'model', 'side', 'dnp'].includes(w))) return 'changed';
  if (what.includes('position')) return 'moved';
  if (what.includes('rotation')) return 'rotated';
  return 'unchanged';
}

/** Contract components with status and what filled in, sorted: changes first, then by ref. */
export function normalizeComponents(list) {
  const out = (Array.isArray(list) ? list : []).filter((c) => c && c.ref).map((c) => {
    const base = c.base || null, head = c.head || null;
    const what = Array.isArray(c.what) ? c.what : whatChanged(base, head);
    let status = STATUSES.includes(c.status) ? c.status : deriveStatus(base, head, what);
    if (c.minor && status !== 'added' && status !== 'removed') status = 'minor';
    return { ...c, base, head, what, status };
  });
  out.sort((a, b) => (CHANGE_ORDER[a.status] - CHANGE_ORDER[b.status]) || naturalCompare(a.ref, b.ref));
  return out;
}

export function isChange(c) {
  return c.status !== 'unchanged' && c.status !== 'minor';
}

/**
 * Every kind of change a component shows, primary status first. The contract gives one status
 * per part (changed > moved > rotated), but a part that was moved AND turned is both, and a
 * reviewer filtering for rotations must not miss it: D12 moved 3.59 mm and 180° -> 270° is
 * tagged ['moved', 'rotated'].
 */
export function tagsOf(c) {
  const tags = [c.status];
  const what = c.what || [];
  if (['unchanged', 'minor', 'added', 'removed'].includes(c.status)) return tags;
  if (what.includes('position') && !tags.includes('moved')) tags.push('moved');
  if (what.includes('rotation') && !tags.includes('rotated')) tags.push('rotated');
  // a format-only model swap next to a move doesn't make the part 'changed' too
  if (what.some((w) => !['position', 'rotation', 'model_format', 'footprint_library'].includes(w)) && !tags.includes('changed')) tags.push('changed');
  return tags;
}

/** Counts per tag (see tagsOf), every status present (0 when none): a part counts once per tag. */
export function countByStatus(list) {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const c of list) for (const t of tagsOf(c)) counts[t] = (counts[t] || 0) + 1;
  return counts;
}

function fmt(side, field) {
  if (!side) return null;
  if (field === 'position') return `${num(side.x).toFixed(2)}, ${num(side.y).toFixed(2)} mm`;
  if (field === 'rot') return `${+num(side.rot).toFixed(2)}°`;
  const v = side[field];
  if (v === undefined || v === null || v === '') return null;
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  return String(v);
}

/**
 * Rows for the hover card: [{field, label, base, head, changed}]. Rows with nothing on either
 * side are dropped; `changed` marks the ones that differ.
 */
export function describe(c) {
  const rows = [];
  for (const [field, label] of FIELDS) {
    const base = fmt(c.base, field), head = fmt(c.head, field);
    if (base === null && head === null) continue;
    rows.push({ field, label, base, head, changed: !!(c.base && c.head) && base !== head });
  }
  return rows;
}

/** One-line summary of what changed, for the list: "10k → 4.7k", "moved 1.20 mm", … */
export function summary(c) {
  if (c.status === 'added') return [c.head?.value, c.head?.footprint].filter(Boolean).join(' · ');
  if (c.status === 'removed') return [c.base?.value, c.base?.footprint].filter(Boolean).join(' · ');
  const parts = [];
  const b = c.base || {}, h = c.head || {};
  if (c.what.includes('value')) parts.push(`${b.value ?? '∅'} → ${h.value ?? '∅'}`);
  if (c.what.includes('footprint')) parts.push(`${shortFp(b.footprint)} → ${shortFp(h.footprint)}`);
  if (c.what.includes('side')) parts.push(`${b.side} → ${h.side}`);
  if (c.what.includes('position')) parts.push(`moved ${Math.hypot(num(h.x) - num(b.x), num(h.y) - num(b.y)).toFixed(2)} mm`);
  if (c.what.includes('rotation')) parts.push(`${+num(b.rot).toFixed(1)}° → ${+num(h.rot).toFixed(1)}°`);
  if (c.what.includes('model')) parts.push('3D model');
  if (c.what.includes('model_format')) {
    const ext = (m) => (String(m || '').match(/\.[A-Za-z0-9]+$/) || ['?'])[0].toLowerCase();
    parts.push(`3D model ${ext(b.model)} → ${ext(h.model)}`);
  }
  if (c.what.includes('pads')) parts.push('pads');
  if (c.what.includes('graphics')) parts.push('footprint graphics');
  if (c.what.includes('fields')) parts.push('part fields');
  if (c.what.includes('footprint_library')) {
    const nick = (f) => (String(f || '').includes(':') ? String(f).split(':')[0] : '∅');
    parts.push(`library ${nick(b.footprint)} → ${nick(h.footprint)}`);
  }
  if (c.what.includes('dnp')) parts.push(h.dnp ? 'now DNP' : 'no longer DNP');
  // The backend may name other footprint changes (pads, fields, …); list them as they come.
  const known = new Set(['value', 'footprint', 'side', 'position', 'rotation', 'model', 'dnp', 'model_format', 'footprint_library', 'pads', 'graphics', 'fields', 'fields_minor']);
  const other = c.what.filter((w) => !known.has(w));
  if (other.length) parts.push(other.join(', '));
  return parts.join(' · ');
}

function shortFp(fp) {
  if (!fp) return '∅';
  const s = String(fp);
  return s.includes(':') ? s.slice(s.indexOf(':') + 1) : s;
}
