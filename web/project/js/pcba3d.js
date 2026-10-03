// Mount point for the 3D PCBA diff module, which lives in ../pcba3d/ (built separately).
// Its contract: pcba3d/index.js exports async mountPcba3d(el, project, baseUrl) -> {destroy()}.
// From file:// ES modules can't load, so there it is pcba3d/pcba3d.bundle.js (a classic script built by
// pcba3d/build_offline.mjs, run by site.py) setting window.KIPR_PCBA3D = {mountPcba3d}; it loads its own
// data packs. If the module (or, offline, its bundle) is missing, show a placeholder.
import { el, clear, OFFLINE, loadOfflineBundle, fillViewport } from './util.js';
import { boxesShown, setBoxesShown, toggleBoxes, onBoxes } from './boxes.js';

export function createPcba3dView(project, container) {
  const host = el('div', { class: 'pcba3d-host' });
  container.append(host);
  const stopFill = fillViewport(host);
  let stopMarkers = () => {};
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
      stopMarkers = syncMarkers(host);
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
        stopMarkers = syncMarkers(host);
        markReady(host, h);
      })
      .catch((e) => {
        if (destroyed) return;
        console.warn('pcba3d module unavailable:', e); // eslint-disable-line no-console
        placeholder('The 3D PCBA view is not included in this build.');
      });
  }
  return {
    destroy() { destroyed = true; stopFill(); stopMarkers(); try { handle?.destroy?.(); } catch { /* module teardown errors are not ours */ } },
    focusRef(ref) { handle?.focus?.(ref); },
    onKey(e) {
      if (e.key === 'b') { toggleBoxes(); return true; }
      return false;
    },
  };
}

/** The module's Markers checkbox follows the schematic / layout Boxes choice (boxes.js), and sets it. */
function syncMarkers(host) {
  const cb = host.querySelector('input[data-toggle="markers"]');
  if (!cb) return () => {};
  const apply = (on) => {
    if (cb.checked === on) return;
    cb.checked = on;
    cb.dispatchEvent(new Event('change'));
  };
  cb.addEventListener('change', () => { if (cb.checked !== boxesShown()) setBoxesShown(cb.checked); });
  apply(boxesShown());
  return onBoxes(apply);
}

/** host[data-ready] = number of viewer errors once the module has loaded its data (for tests and scripts). */
function markReady(host, h) {
  Promise.resolve(h?.ready).then(() => { host.dataset.ready = String((h?.errors || []).length); }, () => { host.dataset.ready = 'failed'; });
}
