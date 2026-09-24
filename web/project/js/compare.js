// Base/head compare panes on a panzoom stage: side by side, onion skin, swipe, single. Generic over what
// the content is (sheet <img>s, gerber canvases, layer SVG stacks): the caller passes node factories.
import { el } from './util.js';
import { sliderLabel } from './widgets.js';

const shared = { opacity: 0.5, swipe: 0.5 };

/**
 * mode: 'side' | 'onion' | 'swipe' | 'single'
 * make(side) -> Node placed in the world (or null when that side does not exist)
 * Returns {panes, cleanup()}; appends sliders to `extra`.
 */
export function comparePanes(stage, mode, make, extra, { single = 'head', labels = { base: 'base', head: 'head' } } = {}) {
  const cleanups = [];
  const missing = (text) => el('div', { class: 'missing-msg' }, text);
  let panes;
  if (mode === 'side') {
    panes = [stage.pane(labels.base, make('base') || missing(`not in ${labels.base}`)), stage.pane(labels.head, make('head') || missing(`not in ${labels.head}`))];
  } else if (mode === 'onion') {
    const head = make('head');
    if (head) head.style.opacity = String(shared.opacity);
    panes = [stage.pane(`${labels.base} + ${labels.head}`, make('base'), head)];
    extra.append(sliderLabel(labels.base, labels.head, shared.opacity, (v) => { shared.opacity = v; if (head) head.style.opacity = String(v); }, 'Head opacity'));
  } else if (mode === 'swipe') {
    const head = make('head');
    const handle = el('div', { class: 'swipe-handle' });
    const p = stage.pane(null, make('base'), head);
    p.append(el('div', { class: 'pane-label left' }, labels.base), el('div', { class: 'pane-label' }, labels.head), handle);
    panes = [p];
    const apply = () => {
      if (!head) return;
      const cut = shared.swipe * p.clientWidth;
      const wx = (cut - stage.view.tx) / stage.view.s; // world px of the divider
      const left = parseFloat(head.style.left) || 0;
      const width = parseFloat(head.style.width) || stage.W;
      // clip-path is in the node's own box; in a flipped (bottom) world the node is mirrored, so its
      // local x runs right-to-left and the part left of the divider is its right-hand end
      const px = Math.max(0, Math.min(width, wx - left));
      head.style.clipPath = stage.flip ? `inset(0 ${px}px 0 0)` : `inset(0 0 0 ${px}px)`;
      handle.style.left = `${cut}px`;
    };
    cleanups.push(stage.onTransform(apply));
    extra.append(sliderLabel(labels.base, labels.head, shared.swipe, (v) => { shared.swipe = v; apply(); }, 'Swipe position', 0.001));
    requestAnimationFrame(apply);
  } else {
    panes = [stage.pane(labels[single], make(single) || missing(`not in ${labels[single]}`))];
  }
  return { panes, cleanup() { for (const c of cleanups) c(); } };
}
