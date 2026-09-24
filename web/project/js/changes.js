// A list of changes that each may carry a bbox; click (or n/p) selects one, and the owner zooms to it.
// Generic: schematic sheet changes, PCB changes and pixel-diff regions all come through here.
import { el, clear, badge } from './util.js';

/**
 * items: [{title, detail, kind, status, box: {x,y,w,h} | null}]
 * onSelect(index, item) is called on click and on next()/prev().
 */
export function createChangeList(container, { title = 'Changes', empty = 'No changes listed.', onSelect }) {
  let items = [];
  let current = -1;
  const count = el('span', { class: 'muted' });
  const prevBtn = el('button', { class: 'btn small', title: 'Previous change (p)', onclick: () => prev() }, '‹ prev');
  const nextBtn = el('button', { class: 'btn small', title: 'Next change (n)', onclick: () => next() }, 'next ›');
  const list = el('ol', { class: 'change-list' });
  container.append(el('div', { class: 'change-head' }, el('h3', {}, title, ' ', count), el('span', { class: 'spacer' }), prevBtn, nextBtn), list);

  function render() {
    clear(list);
    count.textContent = `(${items.length})`;
    prevBtn.disabled = nextBtn.disabled = !items.length;
    if (!items.length) list.append(el('li', { class: 'muted empty-li' }, empty));
    items.forEach((it, i) => {
      list.append(el('li', {}, el('button', {
        class: `change${i === current ? ' active' : ''}${it.box ? '' : ' nobox'}`,
        'aria-current': i === current ? 'true' : null,
        title: it.box ? 'Zoom to this change' : 'No location for this change',
        onclick: () => select(i),
      },
      el('span', { class: 'change-top' }, badge('kind', it.kind), badge('status', it.status), el('span', { class: 'change-title' }, it.title || '')),
      it.detail ? el('span', { class: 'change-detail' }, it.detail) : null)));
    });
  }

  function select(i, notify = true) {
    if (!items.length) return;
    current = ((i % items.length) + items.length) % items.length;
    render();
    list.querySelector('.change.active')?.scrollIntoView({ block: 'nearest' });
    if (notify) onSelect?.(current, items[current]);
  }
  const next = () => select(current + 1);
  const prev = () => select(current < 0 ? items.length - 1 : current - 1);

  return {
    set(list2, sel = -1) { items = list2 || []; current = sel < items.length ? sel : -1; render(); },
    select, next, prev,
    get current() { return current; },
    get items() { return items; },
  };
}

/** Human text for a contract change entry: {kind, ref, what, base, head, layer, detail}. */
export function describeChange(c) {
  const ref = typeof c.ref === 'string' ? c.ref : '';
  const what = typeof c.what === 'string' ? c.what : '';
  let title = [ref, what].filter(Boolean).join(' ');
  if (!title) title = typeof c.kind === 'string' ? c.kind : 'change';
  const parts = [];
  if (c.base !== undefined || c.head !== undefined) parts.push(`${fmt(c.base)} → ${fmt(c.head)}`);
  if (typeof c.layer === 'string') parts.push(c.layer);
  if (typeof c.detail === 'string' && c.detail) parts.push(c.detail);
  return { title, detail: parts.join(' · ') };
}

function fmt(v) {
  if (v === null || v === undefined || v === '') return '∅';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
