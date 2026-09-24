// Small DOM + safety helpers shared by all viewer modules (generic: no project-review specifics).
//
// SECURITY: project-review.json and every file next to it are derived from a pull request and
// must be treated as untrusted. Never assign data to innerHTML; build nodes with el() and
// textContent, and pass every data-derived URL through safeUrl()/assetUrl().

/** Create an element. attrs: {class, title, href, ...}; children: nodes, strings, null/false (skipped), arrays. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'href' || k === 'src') {
      const safe = k === 'href' ? safeUrl(v) : assetUrl(v);
      if (safe) node.setAttribute(k, safe);
    } else node.setAttribute(k, v === true ? '' : String(v));
  }
  append(node, children);
  if (node.tagName === 'A' && /^https?:/i.test(node.getAttribute('href') || '')) {
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
  }
  return node;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

/** SVG element with plain (non-URL) attributes. Only ever used with numbers and fixed class names. */
export function svgEl(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.setAttribute('class', v);
    else if (/^(href|xlink:href|src)$|^on/i.test(k)) throw new Error(`svgEl: refusing attribute ${k}`);
    else node.setAttribute(k, String(v));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Only http(s) absolute URLs, in-page "#..." links, or relative asset paths inside the site. Everything else -> null. */
export function safeUrl(u) {
  if (typeof u !== 'string' || !u.trim()) return null;
  const s = u.trim();
  if (/^https?:\/\//i.test(s)) {
    try { return new URL(s).href; } catch { return null; }
  }
  if (s.startsWith('#')) return s;
  return assetUrl(s);
}

/** Relative path inside OUT (e.g. "p/<slug>/sch/head/root.svg"). Rejects absolute, protocol, "..", backslashes. */
export function assetUrl(p) {
  if (typeof p !== 'string' || !p) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(p) || p.startsWith('/') || p.includes('\\') || p.includes('\0')) return null;
  if (p.split('/').some((seg) => seg === '..' || seg === '.')) return null;
  return p.split('/').map(encodeURIComponent).join('/');
}

const SHA_RE = /^[0-9a-f]{7,64}$/i;

/** Link to a commit / file on the forge from the contract's repo {url, blob}. Only https URLs survive. */
export function commitUrl(repo, sha) {
  if (!SHA_RE.test(sha || '')) return null;
  const base = safeUrl(repo?.url);
  if (!base || !/^https:\/\//i.test(base)) return null;
  return `${base.replace(/\/+$/, '')}/commit/${sha}`;
}

export function blobUrl(repo, sha, path) {
  if (!SHA_RE.test(sha || '') || typeof path !== 'string' || !path) return null;
  if (path.split('/').some((s) => s === '..' || s === '')) return null;
  const tpl = typeof repo?.blob === 'string' ? repo.blob : null;
  if (!tpl || !tpl.includes('{sha}') || !tpl.includes('{path}')) return null;
  const url = tpl.replace('{sha}', sha).replace('{path}', path.split('/').map(encodeURIComponent).join('/'));
  const safe = safeUrl(url);
  return safe && /^https:\/\//i.test(safe) ? safe : null;
}

export const shortSha = (s) => (typeof s === 'string' && s ? s.slice(0, 8) : '?');

// --- offline (file://) mode ----------------------------------------------------------------------------
// Opened from a downloaded artifact, browsers refuse fetch() of file:// URLs. site.py then provides
// data.js (window.KIPR_DATA = {review, pcba3d}) and one offline/<slug>.js per project with the SVG texts the
// pixel diffs need (window.KIPR_PACKS[slug] = {files: {<asset url>: {text}}}), loaded on demand with <script>.

export const OFFLINE = typeof location !== 'undefined' && location.protocol === 'file:';
export const SLUG_RE = /^[a-z0-9_-]{1,100}$/;
const packs = new Map();

/** The per-project pack that holds `url` ("p/<slug>/..."), or null. Loaded once with a <script> tag. */
function packFor(url) {
  const m = /^p\/([^/]+)\//.exec(url || '');
  const slug = m && decodeURIComponent(m[1]);
  if (!slug || !SLUG_RE.test(slug)) return Promise.resolve(null);
  if (!packs.has(slug)) {
    packs.set(slug, new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = `offline/${encodeURIComponent(slug)}.js`;
      s.onload = () => resolve(window.KIPR_PACKS?.[slug] || null);
      s.onerror = () => resolve(null);
      document.head.append(s);
    }));
  }
  return packs.get(slug);
}

async function offlineFile(url) {
  const pack = await packFor(url);
  const f = pack?.files && Object.prototype.hasOwnProperty.call(pack.files, url) ? pack.files[url] : null;
  if (!f) throw new Error(`${url} is not available offline`);
  return f;
}

/**
 * URL to load an image asset into a canvas. file:// images taint canvases, so offline the pack's
 * SVG text is turned into a same-origin blob: URL (caller may revoke it).
 */
export async function canvasImageSrc(path) {
  const url = assetUrl(path);
  if (!url || !OFFLINE) return url;
  const f = await offlineFile(url);
  if (typeof f.text !== 'string') throw new Error(`${url}: not an image offline`);
  return URL.createObjectURL(new Blob([f.text], { type: 'image/svg+xml' }));
}

export async function fetchJson(path) {
  const data = typeof window !== 'undefined' ? window.KIPR_DATA : null;
  if (data && path === 'project-review.json') {
    if (!data.review) throw new Error(`${path}: not in data.js`);
    return data.review;
  }
  const r = await fetch(path, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

const textCache = new Map();
/** Text of an asset (gerber, SVG). Cached; offline it comes from the project's pack. */
export function fetchText(path) {
  const url = assetUrl(path);
  if (!url) return Promise.reject(new Error(`refusing to load ${path}`));
  if (!textCache.has(url)) {
    const p = OFFLINE
      ? offlineFile(url).then((f) => {
        if (typeof f.text !== 'string') throw new Error(`${url}: not text`);
        return f.text;
      })
      : fetch(url).then((r) => {
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
        return r.text();
      });
    p.catch(() => textCache.delete(url));
    textCache.set(url, p);
  }
  return textCache.get(url);
}

/** Parse the first viewBox of an SVG text: {x, y, w, h} or null. */
export function parseViewBox(txt) {
  const m = /viewBox\s*=\s*["']\s*(-?[\d.eE+-]+)[\s,]+(-?[\d.eE+-]+)[\s,]+([\d.eE+-]+)[\s,]+([\d.eE+-]+)/.exec(txt || '');
  if (!m) return null;
  const v = { x: +m[1], y: +m[2], w: +m[3], h: +m[4] };
  return [v.x, v.y, v.w, v.h].every(Number.isFinite) && v.w > 0 && v.h > 0 ? v : null;
}

/** A badge whose class is derived from a sanitised value: `status-added`, `sev-error`, ... */
export function badge(kind, value, title) {
  if (value === null || value === undefined || value === '') return null;
  return el('span', { class: `badge ${kind}-${String(value).replace(/[^a-z-]/gi, '')}`, title: title || null }, String(value));
}

export function fmtNum(v, digits = 3) {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(+v.toFixed(digits));
  return v;
}

/** Stable, readable string for table cells (numbers rounded, arrays joined). */
export function cellText(v) {
  if (v === null || v === undefined) return '';
  if (Array.isArray(v)) return v.map((x) => cellText(x)).join(', ');
  if (typeof v === 'object') return JSON.stringify(v);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  return String(fmtNum(v));
}

export const arr = (v) => (Array.isArray(v) ? v : []);
export const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : null);
export const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** A [x, y, w, h] mm box from the JSON, or null if it is not four finite numbers with w, h >= 0. */
export function bbox(v) {
  if (!Array.isArray(v) || v.length !== 4 || !v.every((n) => typeof n === 'number' && Number.isFinite(n))) return null;
  if (v[2] < 0 || v[3] < 0) return null;
  return { x: v[0], y: v[1], w: v[2], h: v[3] };
}

/** Debounce: run fn once, `ms` after the last call. */
export function debounce(fn, ms) {
  let t = null;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}
