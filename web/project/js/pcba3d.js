// Mount point for the 3D PCBA diff module, which lives in ../pcba3d/ (built separately).
// Its contract: pcba3d/index.js exports async mountPcba3d(el, project, baseUrl) -> {destroy()}.
// From file:// ES modules can't load, so there it is pcba3d/pcba3d.bundle.js (a classic script built by
// pcba3d/build_offline.mjs, run by site.py) setting window.KIPR_PCBA3D = {mountPcba3d}; it loads its own
// data packs. If the module (or, offline, its bundle) is missing, show a placeholder.
import { el, clear, OFFLINE } from './util.js';

export function createPcba3dView(project, container) {
  const host = el('div', { class: 'pcba3d-host' });
  container.append(host);
  let handle = null;
  let destroyed = false;
  const placeholder = (...msg) => clear(host).append(el('div', { class: 'empty' }, ...msg));

  const baseUrl = new URL('./', document.baseURI).href;
  const serveHint = () => placeholder('The 3D view needs a web server here: run ', el('code', {}, 'python3 serve.py'), ' in this folder and open the address it prints.');
  host.append(el('div', { class: 'loading' }, 'Loading 3D view…'));
  if (OFFLINE) {
    loadOfflineBundle().then(async (mod) => {
      if (destroyed) return;
      if (!mod) { serveHint(); return; }
      clear(host);
      const h = await mod.mountPcba3d(host, project, baseUrl);
      if (destroyed) { h?.destroy?.(); return; }
      handle = h;
      markReady(host, h);
    }).catch(() => { if (!destroyed) serveHint(); });
  } else {
    import('../pcba3d/index.js')
      .then(async (mod) => {
        if (destroyed) return;
        if (typeof mod.mountPcba3d !== 'function') { placeholder('3D module has no mountPcba3d().'); return; }
        clear(host);
        const h = await mod.mountPcba3d(host, project, baseUrl);
        if (destroyed) { h?.destroy?.(); return; }
        handle = h;
        markReady(host, h);
      })
      .catch((e) => {
        if (destroyed) return;
        console.warn('pcba3d module unavailable:', e); // eslint-disable-line no-console
        placeholder('The 3D PCBA view is not included in this build.');
      });
  }
  return {
    destroy() { destroyed = true; try { handle?.destroy?.(); } catch { /* module teardown errors are not ours */ } },
    focusRef(ref) { handle?.focus?.(ref); },
  };
}

/** host[data-ready] = number of viewer errors once the module has loaded its data (for tests and scripts). */
function markReady(host, h) {
  Promise.resolve(h?.ready).then(() => { host.dataset.ready = String((h?.errors || []).length); }, () => { host.dataset.ready = 'failed'; });
}

let offlineBundle = null;
/** window.KIPR_PCBA3D from pcba3d/pcba3d.bundle.js, loaded once with a <script> tag; null if absent. */
function loadOfflineBundle() {
  if (!offlineBundle) {
    offlineBundle = new Promise((resolve) => {
      if (window.KIPR_PCBA3D?.mountPcba3d) { resolve(window.KIPR_PCBA3D); return; }
      if (!window.KIPR_DATA?.pcba3d) { resolve(null); return; } // site.py built no 3D bundle: don't request it
      const s = document.createElement('script');
      s.src = 'pcba3d/pcba3d.bundle.js';
      s.onload = () => resolve(typeof window.KIPR_PCBA3D?.mountPcba3d === 'function' ? window.KIPR_PCBA3D : null);
      s.onerror = () => resolve(null);
      document.head.append(s);
    });
  }
  return offlineBundle;
}
