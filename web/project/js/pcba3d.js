// Mount point for the 3D PCBA diff module, which lives in ../pcba3d/ (built separately).
// Its contract: pcba3d/index.js exports async mountPcba3d(el, project, baseUrl) -> {destroy()}.
// If the module is missing, or we run from file:// (no ES modules, no fetch of GLBs), show a placeholder.
import { el, clear, OFFLINE } from './util.js';

export function createPcba3dView(project, container) {
  const host = el('div', { class: 'pcba3d-host' });
  container.append(host);
  let handle = null;
  let destroyed = false;
  const placeholder = (...msg) => clear(host).append(el('div', { class: 'empty' }, ...msg));

  if (OFFLINE) {
    placeholder('The 3D view needs a web server: run ', el('code', {}, 'python3 serve.py'), ' in this folder and open the address it prints.');
  } else {
    host.append(el('div', { class: 'loading' }, 'Loading 3D view…'));
    const baseUrl = new URL('./', document.baseURI).href;
    import('../pcba3d/index.js')
      .then(async (mod) => {
        if (destroyed) return;
        if (typeof mod.mountPcba3d !== 'function') { placeholder('3D module has no mountPcba3d().'); return; }
        clear(host);
        const h = await mod.mountPcba3d(host, project, baseUrl);
        if (destroyed) { h?.destroy?.(); return; }
        handle = h;
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
