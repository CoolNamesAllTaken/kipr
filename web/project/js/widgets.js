// Tiny shared UI bits: segmented mode bar, labelled slider, diff legend, Boxes and smart diff toggles.
import { el, svgEl } from './util.js';
import { boxesShown, toggleBoxes, onBoxes } from './boxes.js';
import { smartOn, toggleSmart, onSmart } from './smart.js';

export function createModeBar(modes, current, onPick, label = 'Compare mode') {
  const bar = el('div', { class: 'seg', role: 'tablist', 'aria-label': label });
  for (const [m, text] of modes) {
    bar.append(el('button', { class: 'seg-btn', role: 'tab', dataset: { mode: m }, 'aria-selected': String(m === current), onclick: () => onPick(m) }, text));
  }
  return {
    el: bar,
    select(m) { for (const b of bar.children) b.setAttribute('aria-selected', String(b.dataset.mode === m)); },
  };
}

export function sliderLabel(left, right, value, onInput, aria, step = 0.01) {
  const s = el('input', { type: 'range', min: 0, max: 1, step, value, 'aria-label': aria });
  s.addEventListener('input', () => onInput(+s.value));
  return el('label', { class: 'slider' }, left, s, right);
}

/** The 'Boxes' toggle (show / hide the change boxes, key b); onChange(shown) on every change. Call stop() on destroy. */
export function boxesToggle(onChange) {
  const btn = el('button', { class: 'btn boxes-toggle', title: 'Show / hide the boxes around changes (b)', 'aria-pressed': String(boxesShown()), onclick: () => toggleBoxes() }, 'Boxes');
  const stop = onBoxes((on) => { btn.setAttribute('aria-pressed', String(on)); onChange(on); });
  return { el: btn, stop };
}

const SMART_TIP = 'Smart diff (s): items that only moved, with the same connections, stay quiet. Off: raw diff, moves show as changes.';

/** The schematic's smart diff toggle (an icon, key s); onChange(on) on every change. Call stop() on destroy. */
export function smartToggle(onChange) {
  // four-way move arrows
  const icon = svgEl('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' },
    svgEl('path', { d: 'M8 1.5v13M1.5 8h13M6 3.5l2-2 2 2M6 12.5l2 2 2-2M3.5 6l-2 2 2 2M12.5 6l2 2-2 2' }));
  const btn = el('button', { class: 'btn smart-toggle', title: SMART_TIP, 'aria-label': 'Smart diff', 'aria-pressed': String(smartOn()), onclick: () => toggleSmart() }, icon);
  const stop = onSmart((on) => { btn.setAttribute('aria-pressed', String(on)); onChange(on); });
  return { el: btn, stop };
}

export function legend() {
  return [el('span', { class: 'key removed' }, 'removed (base only)'), el('span', { class: 'key added' }, 'added (head only)'), el('span', { class: 'key common' }, 'unchanged')];
}
