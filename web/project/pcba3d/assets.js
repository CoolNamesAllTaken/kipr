// Where the 3D module's inputs come from: OUT-relative paths from the contract (GLBs, gerbers,
// drill files), read either with fetch() or, when the report was opened from disk (file://,
// where browsers block fetch), from data packs the report build embedded as classic scripts.
//
// A pack is `OUT/offline/pcba3d-<slug>.js` (plus `pcba3d-vendor.js` for the renderer's WASM),
// written by pcba3d/build_offline.mjs, and does
//     (window.KIPR_OFFLINE ||= {files: {}}).files['p/<slug>/3d/head.glb'] = {b64: '…'} | {text: '…'}
// It is loaded on demand with a <script> tag, which file:// allows. The same idea as kicad-libs'
// component-review viewer (tools/component-review/viewer/js/boot.js and its offline packs).

const packs = new Map();

function offlineFiles() {
  return (globalThis.KIPR_OFFLINE && globalThis.KIPR_OFFLINE.files) || null;
}

/** Load a classic script once (resolves on load, rejects on error). */
function loadScript(url) {
  if (!packs.has(url)) {
    packs.set(url, new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = url;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error(`could not load ${url}`));
      document.head.appendChild(s);
    }));
  }
  return packs.get(url);
}

function decodeBase64(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/**
 * A loader for one OUT dir. `base` is its URL; `slug` names the offline pack to try.
 * bytes(path, onProgress, signal) -> ArrayBuffer; text(path) -> string; url(path) -> string.
 */
export function assetLoader(base, slug = null) {
  const baseUrl = base instanceof URL ? base : new URL(base || '.', document.baseURI);
  const offline = baseUrl.protocol === 'file:';

  // The project viewer's own pack (offline/<slug>.js, written by kipr/project/site.py) holds the
  // gerbers and drill files as text, keyed by their URL-encoded path; site.py doesn't repeat them
  // in the 3D pack.
  async function shellPack(path) {
    if (!offline || !slug) return null;
    const key = path.split('/').map(encodeURIComponent).join('/');
    const has = () => globalThis.KIPR_PACKS?.[slug]?.files?.[key];
    if (!has()) {
      try { await loadScript(new URL(`offline/${encodeURIComponent(slug)}.js`, baseUrl).href); } catch { /* reported below */ }
    }
    return has() || null;
  }

  async function embedded(path) {
    let files = offlineFiles();
    if ((!files || !(path in files)) && offline) {
      // The renderer's WASM is in a pack of its own (every project needs the same one).
      const pack = path.startsWith('vendor/') ? 'offline/pcba3d-vendor.js' : slug ? `offline/pcba3d-${slug}.js` : null;
      if (pack) {
        try { await loadScript(new URL(pack, baseUrl).href); } catch { /* reported below */ }
        files = offlineFiles();
      }
    }
    if (files && path in files) return files[path];
    return shellPack(path);
  }

  return {
    offline,
    url: (path) => new URL(path, baseUrl).href,
    async bytes(path, onProgress = () => {}, signal) {
      const e = await embedded(path);
      if (e) {
        const data = e.b64 !== undefined ? decodeBase64(e.b64) : new TextEncoder().encode(e.text);
        onProgress(data.byteLength, data.byteLength);
        return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
      }
      if (offline) throw new Error(`${path} is not in the offline pack (open the report over http: python3 serve.py)`);
      return fetchBytes(new URL(path, baseUrl).href, onProgress, signal);
    },
    async text(path, signal) {
      const e = await embedded(path);
      if (e) return e.text !== undefined ? e.text : new TextDecoder().decode(decodeBase64(e.b64));
      if (offline) throw new Error(`${path} is not in the offline pack (open the report over http: python3 serve.py)`);
      const r = await fetch(new URL(path, baseUrl).href, { signal });
      if (!r.ok) throw new Error(`HTTP ${r.status} for ${path}`);
      return r.text();
    },
  };
}

/** Fetch with byte progress: onProgress(loaded, total|0). */
export async function fetchBytes(url, onProgress = () => {}, signal) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  const total = Number(response.headers.get('content-length')) || 0;
  if (!response.body || !response.body.getReader) {
    const buf = await response.arrayBuffer();
    onProgress(buf.byteLength, buf.byteLength);
    return buf;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onProgress(loaded, total);
  }
  const out = new Uint8Array(loaded);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.byteLength; }
  return out.buffer;
}
