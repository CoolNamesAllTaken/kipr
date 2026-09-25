// BOM, netlist and ERC/DRC delta tables with text + status filters and sortable columns.
import { el, clear, badge, arr, obj, cellText } from './util.js';
import { formatHash } from './route.js';

/** True if every whitespace-separated term of q occurs in hay (case-insensitive). */
export function matchesQuery(hay, q) {
  if (!q) return true;
  const h = String(hay).toLowerCase();
  return String(q).toLowerCase().split(/\s+/).filter(Boolean).every((t) => h.includes(t));
}

/** Counts of row.status values. */
export function statusCounts(rows, key = 'status') {
  const c = {};
  for (const r of rows) { const s = String(r[key] ?? 'unknown'); c[s] = (c[s] || 0) + 1; }
  return c;
}

/**
 * Generic filtered table. columns: [{title, get(row) -> Node|string, sort(row) -> string|number, cls}]
 * rows carry .status; hay(row) is the text searched by the filter box.
 */
function filteredTable(container, { rows, columns, hay, statuses, ctx, emptyText, rowClass, changesFilter = false }) {
  // changesFilter: a first "Changes" option (every status but unchanged and minor), the default
  const state = { q: ctx.route.params.q || '', st: ctx.route.params.st || (changesFilter ? 'changes' : ''), sort: null, dir: 1 };
  const isChange = (r) => r.status !== 'unchanged' && r.status !== 'minor';
  const input = el('input', { type: 'search', class: 'table-filter', placeholder: 'Filter…', 'aria-label': 'Filter rows', value: state.q });
  const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Status filter' });
  const counts = statusCounts(rows);
  const opts = [...(changesFilter ? [['changes', `Changes (${rows.filter(isChange).length})`]] : []),
    [changesFilter ? 'all' : '', `All (${rows.length})`], ...statuses.filter((s) => counts[s]).map((s) => [s, `${s} (${counts[s]})`])];
  for (const [s, label] of opts) {
    seg.append(el('button', { class: 'seg-btn', 'aria-selected': String(state.st === s), dataset: { st: s }, onclick: () => { state.st = s; sync(); } }, label));
  }
  const shown = el('span', { class: 'muted small' });
  const table = el('table', { class: 'grid' });
  container.append(el('div', { class: 'table-tools' }, input, seg, el('span', { class: 'spacer' }), shown), el('div', { class: 'scroll-x' }, table));
  input.addEventListener('input', () => { state.q = input.value; sync(); });

  function sync() {
    for (const b of seg.children) b.setAttribute('aria-selected', String(b.dataset.st === state.st));
    ctx.setRoute({ params: { q: state.q || null, st: state.st === (changesFilter ? 'changes' : '') ? null : state.st || null } }, true);
    render();
  }

  function render() {
    clear(table);
    const head = el('tr', {}, columns.map((c, i) => el('th', { scope: 'col', class: c.cls || null },
      el('button', { class: 'th-sort', onclick: () => { state.dir = state.sort === i ? -state.dir : 1; state.sort = i; render(); } },
        c.title, state.sort === i ? (state.dir > 0 ? ' ▲' : ' ▼') : ''))));
    const byStatus = (r) => !state.st || state.st === 'all' || (state.st === 'changes' ? isChange(r) : r.status === state.st);
    let list = rows.filter((r) => byStatus(r) && matchesQuery(hay(r), state.q));
    if (state.sort !== null && columns[state.sort].sort) {
      const f = columns[state.sort].sort;
      list = [...list].sort((a, b) => {
        const x = f(a); const y = f(b);
        return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * state.dir;
      });
    }
    const body = el('tbody', {}, list.map((r) => el('tr', { class: rowClass ? rowClass(r) : null }, columns.map((c) => el('td', { class: c.cls || null }, c.get(r))))));
    if (!list.length) body.append(el('tr', {}, el('td', { colspan: columns.length, class: 'muted' }, rows.length ? 'No rows match.' : emptyText)));
    table.append(el('thead', {}, head), body);
    shown.textContent = `${list.length} of ${rows.length}`;
  }
  render();
  return { focus() { input.focus(); } };
}

const ROW_CLASS = { added: 'row-added', removed: 'row-removed', changed: 'changed', modified: 'changed', moved: 'changed' };
const rowClass = (r) => ROW_CLASS[r.status] || null;

/** "old → new" cell: plain value when equal, del/ins when different. */
function delta(b, h) {
  const bs = cellText(b); const hs = cellText(h);
  if (bs === hs) return bs;
  return [bs ? el('del', {}, bs) : null, bs && hs ? ' → ' : null, hs ? el('ins', {}, hs) : null];
}

export function createBomView(project, container, ctx) {
  const bom = obj(project.bom);
  // minor rows (only fields that don't name the part changed; see kipr.project.classify) get their own status
  const rows = arr(bom?.rows).filter(obj).map((r) => ({ ...r, status: r.minor === true ? 'minor' : typeof r.status === 'string' ? r.status : 'unknown' }));
  if (!bom) { container.append(el('div', { class: 'empty' }, 'No BOM in this project.')); return { destroy() {} }; }
  const field = (r, side, k) => obj(r[side])?.[k];
  const fieldsDelta = (r) => {
    const b = obj(field(r, 'base', 'fields')) || {}; const h = obj(field(r, 'head', 'fields')) || {};
    const fc = obj(r.fields_changed);
    // the backend's classification when present (noise such as empty Sim.* fields is never listed);
    // older data: every key that differs
    // (added/removed rows and older data: every non-empty field that differs, minus Sim.* / ki_* noise)
    const sig = fc ? arr(fc.significant).map(String) : [...new Set([...Object.keys(b), ...Object.keys(h)])]
      .filter((k) => !/^(sim\.|ki_)/i.test(k) && cellText(b[k]).trim() !== cellText(h[k]).trim());
    const minor = fc ? arr(fc.minor).map(String) : [];
    return [...sig.map((k) => el('div', { class: 'small' }, el('span', { class: 'muted' }, `${k}: `), delta(b[k], h[k]))),
      ...minor.map((k) => el('div', { class: 'small muted', title: 'minor: not counted as a change' }, `${k}: `, delta(b[k], h[k])))];
  };
  const t = filteredTable(container, {
    rows, ctx, emptyText: 'The BOM is empty.', rowClass, changesFilter: true,
    statuses: ['added', 'removed', 'changed', 'minor', 'unchanged'],
    hay: (r) => `${arr(r.refs).join(' ')} ${r.key} ${r.status} ${JSON.stringify(r.base || {})} ${JSON.stringify(r.head || {})}`,
    columns: [
      { title: 'Status', get: (r) => badge('status', r.status), sort: (r) => r.status },
      { title: 'Refs', get: (r) => arr(r.refs).map(String).join(', ') || String(r.key ?? ''), sort: (r) => arr(r.refs)[0] || r.key || '' },
      { title: 'Qty', get: (r) => delta(obj(r.base) ? arr(r.refs).length || 1 : '', obj(r.head) ? arr(r.refs).length || 1 : ''), cls: 'num', sort: (r) => arr(r.refs).length },
      { title: 'Value', get: (r) => delta(field(r, 'base', 'value'), field(r, 'head', 'value')), sort: (r) => String(field(r, 'head', 'value') ?? field(r, 'base', 'value') ?? '') },
      { title: 'Footprint', get: (r) => delta(field(r, 'base', 'footprint'), field(r, 'head', 'footprint')), sort: (r) => String(field(r, 'head', 'footprint') ?? '') },
      { title: 'Fields changed', get: fieldsDelta },
      { title: 'What', get: (r) => arr(r.what).map(String).join(', ') },
    ],
  });
  return { destroy() {}, onKey: (e) => (e.key === '/' ? (e.preventDefault(), t.focus(), true) : false) };
}

export function createNetlistView(project, container, ctx) {
  const nl = obj(project.netlist);
  if (!nl) { container.append(el('div', { class: 'empty' }, 'No netlist in this project.')); return { destroy() {} }; }
  const rows = arr(nl.changes).filter(obj).map((r) => ({ ...r, status: typeof r.status === 'string' ? r.status : 'unknown' }));
  const pins = (list, cls) => arr(list).map((p) => el('code', { class: `pin ${cls}` }, String(p)));
  const t = filteredTable(container, {
    rows, ctx, emptyText: 'No net changes.', rowClass,
    statuses: ['added', 'removed', 'modified', 'renamed'],
    hay: (r) => `${r.net} ${r.status} ${arr(r.added).join(' ')} ${arr(r.removed).join(' ')} ${r.renamed_from || ''}`,
    columns: [
      { title: 'Status', get: (r) => badge('status', r.status), sort: (r) => r.status },
      { title: 'Net', get: (r) => [el('code', {}, String(r.net ?? '')), r.renamed_from ? el('div', { class: 'small muted' }, 'was ', el('code', {}, String(r.renamed_from))) : null], sort: (r) => String(r.net ?? '') },
      { title: 'Pins added', get: (r) => pins(r.added, 'add'), sort: (r) => arr(r.added).length },
      { title: 'Pins removed', get: (r) => pins(r.removed, 'del'), sort: (r) => arr(r.removed).length },
    ],
  });
  return { destroy() {}, onKey: (e) => (e.key === '/' ? (e.preventDefault(), t.focus(), true) : false) };
}

const SEV_ORDER = { error: 0, warning: 1, info: 2, exclusion: 3 };

export function createChecksView(project, container, ctx) {
  const checks = obj(project.checks) || {};
  let any = false;
  for (const [kind, label] of [['drc', 'DRC (layout)'], ['erc', 'ERC (schematic)']]) {
    const d = obj(checks[kind]);
    const sec = el('section', { class: 'card' });
    container.append(sec);
    if (!d) { sec.append(el('h3', {}, label), el('p', { class: 'muted' }, 'Not run.')); continue; }
    any = true;
    const newRows = arr(d.new).filter(obj).map((r) => ({ ...r, status: 'new' }));
    const fixedRows = arr(d.fixed).filter(obj).map((r) => ({ ...r, status: 'fixed' }));
    const n = (v) => (typeof v === 'number' ? String(v) : '?');
    sec.append(el('h3', {}, label, ' ', el('span', { class: 'muted' }, `${n(d.base_count)} → ${n(d.head_count)}`), ' ',
      newRows.length ? badge('delta', `+${newRows.length} new`) : null, ' ', fixedRows.length ? badge('delta', `${fixedRows.length} fixed`) : null));
    const where = (r) => {
      const p = r.pos_mm;
      if (!Array.isArray(p) || p.length !== 2 || !p.every(Number.isFinite)) return typeof r.sheet === 'string' ? r.sheet : '';
      const at = `${p[0].toFixed(2)},${p[1].toFixed(2)}`;
      const href = kind === 'drc'
        ? formatHash({ slug: project.slug, tab: 'layout', params: { at } })
        : formatHash({ slug: project.slug, tab: 'schematic', item: typeof r.sheet === 'string' ? r.sheet : null, params: {} });
      return el('a', { href, title: 'Show on the board / sheet' }, `(${p[0].toFixed(2)}, ${p[1].toFixed(2)})`, typeof r.sheet === 'string' ? ` ${r.sheet}` : '');
    };
    const sub = el('div');
    sec.append(sub);
    filteredTable(sub, {
      rows: [...newRows, ...fixedRows], ctx: { ...ctx, route: { ...ctx.route, params: {} }, setRoute: () => {} }, emptyText: 'No new or fixed violations.',
      rowClass: (r) => (r.status === 'new' ? 'row-removed' : 'row-added'),
      statuses: ['new', 'fixed'],
      hay: (r) => `${r.severity} ${r.type} ${r.description} ${arr(r.items).join(' ')} ${r.sheet || ''}`,
      columns: [
        { title: '', get: (r) => badge('status', r.status), sort: (r) => r.status },
        { title: 'Severity', get: (r) => badge('sev', r.severity), sort: (r) => SEV_ORDER[r.severity] ?? 9 },
        { title: 'Type', get: (r) => el('code', {}, String(r.type ?? '')), sort: (r) => String(r.type ?? '') },
        { title: 'Description', get: (r) => [String(r.description ?? ''), arr(r.items).length ? el('ul', { class: 'items' }, arr(r.items).map((i) => el('li', {}, String(i)))) : null] },
        { title: 'Where', get: where },
      ],
    });
  }
  if (!any && !obj(checks.drc) && !obj(checks.erc)) container.prepend(el('p', { class: 'muted' }, 'ERC/DRC were not run for this project.'));
  return { destroy() {} };
}
