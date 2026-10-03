// Show / hide the boxes drawn around changes: one choice for the schematic and layout views and the 3D
// view's Markers. Default shown. A toggle is remembered per browser (localStorage, best effort); hidden
// is also carried in the URL (boxes=0) so a deep link keeps it. A link's boxes= applies to this visit
// without changing the remembered choice.
const KEY = 'kipr.boxes';
let shown = null;
const listeners = new Set();

function stored() {
  try { const v = localStorage.getItem(KEY); return v === '0' ? false : v === '1' ? true : null; } catch { return null; }
}

export function boxesShown() {
  if (shown === null) shown = stored() ?? true;
  return shown;
}

function set(on) {
  if (on === boxesShown()) return;
  shown = on;
  for (const fn of listeners) fn(on);
}

/** The user toggled: apply and remember. */
export function setBoxesShown(on) {
  try { localStorage.setItem(KEY, on ? '1' : '0'); } catch { /* private mode: not remembered */ }
  set(!!on);
}

export function toggleBoxes() { setBoxesShown(!boxesShown()); }

/** boxes=0|1 from a route's params (a deep link) wins for this visit; absent: keep the current choice. */
export function boxesFromParams(params) {
  if (params?.boxes === '0') set(false);
  else if (params?.boxes === '1') set(true);
  return boxesShown();
}

/** The URL param for the current choice: '0' when hidden, nothing (the default) when shown. */
export function boxesParam() { return boxesShown() ? null : '0'; }

/** Call fn(shown) on every change; returns the unsubscribe function. */
export function onBoxes(fn) { listeners.add(fn); return () => listeners.delete(fn); }

/** Test hook: forget the in-memory choice (the next read goes back to localStorage). */
export function resetBoxes() { shown = null; listeners.clear(); }
