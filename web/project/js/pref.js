// An on/off viewer preference: remembered per browser (localStorage, best effort) and carried in the URL
// when it differs from the default, so a deep link keeps it. A link's value applies to this visit
// without changing the remembered choice. Used for the change boxes (boxes.js) and the schematic's
// smart diff (smart.js).

/** A preference stored under `key`, in the URL as `param` ('0' / '1'), default `def`. */
export function createPref(key, param, def) {
  let on = null;
  const listeners = new Set();
  const stored = () => {
    try { const v = localStorage.getItem(key); return v === '0' ? false : v === '1' ? true : null; } catch { return null; }
  };
  const get = () => { if (on === null) on = stored() ?? def; return on; };
  const set = (v) => {
    if (v === get()) return;
    on = v;
    for (const fn of listeners) fn(v);
  };
  const api = {
    get,
    /** The user toggled: apply and remember. */
    set(v) {
      try { localStorage.setItem(key, v ? '1' : '0'); } catch { /* private mode: not remembered */ }
      set(!!v);
    },
    toggle() { api.set(!get()); },
    /** `param` from a route's params (a deep link) wins for this visit; absent: keep the current choice. */
    fromParams(params) {
      if (params?.[param] === '0') set(false);
      else if (params?.[param] === '1') set(true);
      return get();
    },
    /** The URL value for the current choice: nothing for the default. */
    param() { return get() === def ? null : (get() ? '1' : '0'); },
    /** Call fn(on) on every change; returns the unsubscribe function. */
    on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    /** Test hook: forget the in-memory choice (the next read goes back to localStorage). */
    reset() { on = null; listeners.clear(); },
  };
  return api;
}
