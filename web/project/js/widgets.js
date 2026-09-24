// Tiny shared UI bits: segmented mode bar, labelled slider, diff legend.
import { el } from './util.js';

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

export function legend() {
  return [el('span', { class: 'key removed' }, 'removed (base only)'), el('span', { class: 'key added' }, 'added (head only)'), el('span', { class: 'key common' }, 'unchanged')];
}
