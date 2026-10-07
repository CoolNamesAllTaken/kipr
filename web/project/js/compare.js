// Base/head compare on boarddd/view2d's createCompare: side by side, diff, onion skin, swipe, single.
// What stays here is kipr's: one onion opacity and swipe position for every view, the mode the next
// view opens in, and the toolbar sliders.
import { sliderLabel } from './widgets.js';

// one onion opacity and swipe position for every view: kept across layers, sheets, tabs and projects
const shared = { opacity: 0.5, swipe: 0.5 };

// the compare mode last on show in the schematic or layout tab (not head / base only): new views open in it
let preferred = 'side';
export function preferredMode() { return preferred; }
export function setPreferredMode(m) { preferred = m; }

/** The current slider values {opacity, swipe} (0..1). */
export function compareSliders() { return { ...shared }; }

/** Set slider values (from a URL; null / undefined: keep); returns true when one changed. */
export function setCompareSliders({ opacity, swipe } = {}) {
  opacity ??= shared.opacity;
  swipe ??= shared.swipe;
  const changed = opacity !== shared.opacity || swipe !== shared.swipe;
  shared.opacity = opacity;
  shared.swipe = swipe;
  return changed;
}

/**
 * Show `mode` ('side' | 'diff' | 'onion' | 'swipe' | 'single') on a kipr stage (stage2d.js).
 * content: {base, head, diff, underlay} (view2d content, arrays or null); a missing side says so.
 * Appends the onion / swipe slider to `extra`; onSlide() after the user moves one (slider or divider).
 * Returns the view2d compare (destroy() it before the next one).
 */
export function showCompare(kstage, mode, content, extra, { single = 'head', labels = { base: 'base', head: 'head', diff: 'diff' }, onSlide = null } = {}) {
  const v2mode = mode === 'single' ? single : mode;
  let slider = null;
  const cmp = kstage.v2.createCompare(kstage.stage, {
    base: content.base ?? null, head: content.head ?? null, diff: content.diff ?? null, underlay: content.underlay ?? null,
    mode: v2mode, opacity: shared.opacity, swipe: shared.swipe, labels,
    onChange: (s) => { shared.swipe = s.swipe; if (slider) slider.value = String(s.swipe); onSlide?.(); },
  });
  if (mode === 'onion') {
    const l = sliderLabel(labels.base, labels.head, shared.opacity, (v) => { shared.opacity = v; cmp.setOpacity(v); onSlide?.(); }, 'Head opacity');
    slider = l.querySelector('input');
    extra.append(l);
  } else if (mode === 'swipe') {
    const l = sliderLabel(labels.base, labels.head, shared.swipe, (v) => { shared.swipe = v; cmp.setSwipe(v); onSlide?.(); }, 'Swipe position', 0.001);
    slider = l.querySelector('input');
    extra.append(l);
  }
  return cmp;
}
