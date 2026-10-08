// Show / hide the boxes drawn around changes: one choice for the schematic and layout views and the 3D
// view's Markers. Default shown; remembered per browser, hidden is also carried in the URL (boxes=0).
import { createPref } from './pref.js';

const pref = createPref('kipr.boxes', 'boxes', true);

export const boxesShown = pref.get;
/** The user toggled: apply and remember. */
export const setBoxesShown = (on) => pref.set(on);
export const toggleBoxes = () => pref.toggle();
/** boxes=0|1 from a route's params (a deep link) wins for this visit; absent: keep the current choice. */
export const boxesFromParams = (params) => pref.fromParams(params);
/** The URL param for the current choice: '0' when hidden, nothing (the default) when shown. */
export const boxesParam = () => pref.param();
/** Call fn(shown) on every change; returns the unsubscribe function. */
export const onBoxes = (fn) => pref.on(fn);
/** Test hook: forget the in-memory choice (the next read goes back to localStorage). */
export const resetBoxes = () => pref.reset();
