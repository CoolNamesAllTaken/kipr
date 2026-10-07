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

/** "font-dependent" badge for a violation whose items are text in a font kicad-cli didn't have. */
function fontBadge(r) {
  const faces = arr(r.font_dependent).filter((x) => typeof x === 'string');
  if (!faces.length) return null;
  return [' ', badge('sev', 'font-dependent', `text in ${faces.join(', ')}, which was not available: KiCad substituted another font`)];
}

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
      hay: (r) => `${r.severity} ${r.type} ${r.description} ${arr(r.items).join(' ')} ${r.sheet || ''}${arr(r.font_dependent).length ? ' font-dependent' : ''}`,
      columns: [
        { title: '', get: (r) => badge('status', r.status), sort: (r) => r.status },
        { title: 'Severity', get: (r) => badge('sev', r.severity), sort: (r) => SEV_ORDER[r.severity] ?? 9 },
        { title: 'Type', get: (r) => [el('code', {}, String(r.type ?? '')), fontBadge(r)], sort: (r) => String(r.type ?? '') },
        { title: 'Description', get: (r) => [String(r.description ?? ''), arr(r.items).length ? el('ul', { class: 'items' }, arr(r.items).map((i) => el('li', {}, String(i)))) : null] },
        { title: 'Where', get: where },
      ],
    });
  }
  const grid = obj(checks.grid);
  if (grid) container.append(gridCard(project, grid));
  const imp = obj(checks.impedance);
  if (imp) container.append(impedanceCard(imp));
  if (!any && !obj(checks.drc) && !obj(checks.erc)) container.prepend(el('p', { class: 'muted' }, 'ERC/DRC were not run for this project.'));
  return { destroy() {} };
}

const num = (v) => typeof v === 'number' && Number.isFinite(v);
const box4 = (b) => Array.isArray(b) && b.length === 4 && b.every(num) && b[2] > 0 && b[3] > 0;

/** Link to a spot on a schematic sheet: #/p/<slug>/schematic/<sheet>?at=x,y,w,h (or x,y). */
export function sheetSpotHash(slug, sheet, bboxMm, posMm) {
  let at = null;
  if (box4(bboxMm)) at = bboxMm.map((v) => +v.toFixed(3)).join(',');
  else if (Array.isArray(posMm) && posMm.length === 2 && posMm.every(num)) at = posMm.map((v) => +v.toFixed(3)).join(',');
  return formatHash({ slug, tab: 'schematic', item: typeof sheet === 'string' ? sheet : null, params: { at } });
}

/** Group grid findings by sheet, in the backend's (hierarchy) order. */
export function gridGroups(items) {
  const groups = new Map();
  for (const f of arr(items).filter(obj)) {
    const k = typeof f.sheet === 'string' ? f.sheet : '';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(f);
  }
  return [...groups].map(([sheet, list]) => ({ sheet, items: list }));
}

// checks.grid: schematic items off the connection grid, one block per sheet; a row links to the spot
function gridCard(project, g) {
  const items = arr(g.items).filter(obj);
  const mil = num(g.grid_mil) ? g.grid_mil : '?';
  const scope = g.mode === 'all' ? 'all items' : 'items the PR added or moved';
  const sec = el('section', { class: 'card grid-check' });
  sec.append(el('h3', {}, `Schematic grid (${mil} mil)`, ' ',
    el('span', { class: 'muted' }, `${scope}${num(g.checked) ? `, ${g.checked} points checked` : ''}`), ' ',
    items.length ? badge('sev', 'warning') : null, ' ', items.length ? badge('delta', `${items.length} off grid`) : null));
  if (!items.length) { sec.append(el('p', { class: 'muted' }, 'Everything checked is on the grid.')); return sec; }
  const sheets = new Map(arr(obj(project.schematic)?.sheets).filter(obj).map((s) => [s.id, s]));
  for (const { sheet, items: list } of gridGroups(items)) {
    const sh = sheets.get(sheet);
    const rows = list.map((f) => {
      const who = typeof f.ref === 'string' && f.ref ? f.ref : typeof f.text === 'string' ? f.text : '';
      const pos = Array.isArray(f.pos_mm) && f.pos_mm.length === 2 && f.pos_mm.every(num) ? f.pos_mm : null;
      const file = typeof f.file === 'string' ? `${f.file}${Number.isInteger(f.line) ? `:${f.line}` : ''}` : '';
      const rel = arr(f.related).filter(obj);
      return el('tr', {},
        el('td', {}, badge('status', typeof f.change === 'string' ? f.change : null)),
        el('td', {}, badge('kind', String(f.kind ?? '').replace(/_/g, ' '))),
        el('td', {}, el('code', {}, who)),
        el('td', {}, String(f.detail ?? ''), rel.length ? el('ul', { class: 'items' }, rel.map((r) => el('li', {},
          `${String(r.kind ?? '').replace(/_/g, ' ')}${typeof r.text === 'string' ? ` ${r.text}` : ''}${Number.isInteger(r.line) ? ` (line ${r.line})` : ''}`))) : null),
        el('td', {}, el('a', { href: sheetSpotHash(project.slug, sheet, f.bbox_mm, f.pos_mm), title: 'Show on the sheet' },
          pos ? `(${pos[0].toFixed(2)}, ${pos[1].toFixed(2)})` : 'sheet'), file ? el('div', { class: 'small muted path' }, file) : null));
    });
    sec.append(el('h4', {}, el('a', { href: formatHash({ slug: project.slug, tab: 'schematic', item: sheet || null }) }, sh?.title || sheet || '?'),
      ' ', el('span', { class: 'muted' }, `${sheet} · ${list.length} item${list.length === 1 ? '' : 's'}`)),
    el('div', { class: 'scroll-x' }, el('table', { class: 'grid' },
      el('thead', {}, el('tr', {}, ['', 'Kind', 'Item', 'Detail', 'Where'].map((t) => el('th', { scope: 'col' }, t)))),
      el('tbody', {}, rows))));
  }
  return sec;
}

// checks.impedance: Z per net class x layer (field solver or closed form), base -> head, judged by the controlled
// length out of tolerance. Compact: symbols in the table, the inputs (width groups, left-out stubs, stackup, model
// parameters, notes) in tooltips.
const IMP_FLAG = {
  new_violation: ['▲', 'newly out of tolerance', 'bad'],
  violation: ['⚠', 'out of tolerance (also on base)', 'warn'],
  fixed: ['✓', 'back in tolerance', 'ok'],
  stackup_shift: ['≋', 'Z moved by a stackup change', 'warn'],
  width_change: ['↔', 'track width or pair gap changed', 'warn'],
  target_change: ['◎', 'the class target changed', 'warn'],
};
const fx = (v, d = 1) => (num(v) ? v.toFixed(d) : '–');
const mmTxt = (v) => (num(v) ? String(+v.toFixed(4)) : '–');

/** Tooltip text for one side of an impedance row. */
export function impedanceSideTitle(label, sd) {
  if (!obj(sd)) return `${label}: —`;
  const routed = num(sd.routed_mm) ? sd.routed_mm : sd.length_mm;
  const lines = [`${label}: ${sd.structure ?? '?'} (${sd.model ?? 'no closed-form model'}), ${arr(sd.nets).length} net(s), ${fx(sd.length_mm)} of ${fx(routed)} mm controlled`];
  if (sd.solver) lines.push(`solver: ${sd.solver === 'field' ? `field${num(sd.error_pct) ? ` (error estimate ±${sd.error_pct.toFixed(2)} %)` : ''}` : 'closed form'}${num(sd.Z_closedform) && sd.solver === 'field' ? `, closed form ${fx(sd.Z_closedform)} Ω` : ''}`);
  const segs = arr(sd.segments).filter(obj);
  for (const g of segs) {
    lines.push(`${g.within === false ? '✗' : g.within ? '✓' : '·'} ${mmTxt(g.width)}${num(g.gap) ? `/${mmTxt(g.gap)}` : ''} mm × ${fx(g.length_mm)} mm: ${num(g.Z) ? `${fx(g.Z)} Ω (${g.deviation_pct > 0 ? '+' : ''}${fx(g.deviation_pct)} %)` : `no Z${g.error ? `: ${g.error}` : ''}`}`);
  }
  if (!segs.length) {
    const ws = arr(sd.widths).filter(obj);
    if (ws.length) lines.push(`widths: ${ws.map((w) => `${mmTxt(w.width)} mm × ${fx(w.length_mm)} mm`).join(', ')}`);
    const gs = arr(sd.gaps).filter(obj);
    if (gs.length) lines.push(`gaps: ${gs.map((g) => `${mmTxt(g.gap)} mm × ${fx(g.length_mm)} mm`).join(', ')}`);
  }
  const ex = arr(sd.excluded).filter(obj);
  if (ex.length) lines.push(`left out: ${ex.map((x) => `${mmTxt(x.width)} mm × ${fx(x.length_mm)} mm ${String(x.reason ?? '')}`).join(', ')}`);
  if (num(sd.coplanar_gap)) lines.push(`coplanar gap: ${mmTxt(sd.coplanar_gap)} mm`);
  const p = obj(sd.params);
  if (p) lines.push(`inputs: ${Object.entries(p).map(([k, v]) => `${k} ${mmTxt(v)}`).join(', ')}`);
  if (num(sd.Zcommon)) lines.push(`Zcommon ${fx(sd.Zcommon)} Ω`);
  for (const v of arr(sd.validity)) lines.push(`⚠ ${v}`);
  for (const v of arr(sd.notes)) lines.push(`· ${v}`);
  if (typeof sd.error === 'string' && sd.error) lines.push(`no Z: ${sd.error}`);
  return lines.join('\n');
}

function impedanceCard(z) {
  const rows = arr(z.rows).filter(obj);
  const c = obj(z.count) || {};
  const sec = el('section', { class: 'card impedance-check' });
  const field = z.solver === 'field';
  const method = `${z.method || 'closed-form estimate'}${z.boarddd ? `, boarddd ${z.boarddd}` : ''}. ${field
    ? 'Each Z carries the solver\'s own error estimate (hover a row).' : 'About ±2 % of a field solver inside the models\' validity ranges.'} Fab tolerance is ±10 %. Every track width (and pair gap) is evaluated and weighted by its length; launch stubs, breakouts and short pieces are left out. A review aid, not a sign-off.${z.solver_note ? `\n${z.solver_note}` : ''}`;
  sec.append(el('h3', {}, 'Impedance', ' ', el('span', { class: 'muted imp-method', title: method }, field ? 'field solver' : 'closed-form estimate'), ' ',
    num(c.new_violations) && c.new_violations ? badge('sev', 'error', 'newly out of tolerance') : num(c.violations) && c.violations ? badge('sev', 'warning') : null, ' ',
    rows.length ? badge('delta', `${num(c.violations) ? c.violations : 0} / ${num(c.rows) ? c.rows : 0} out of tol.`) : null));
  if (!rows.length) { sec.append(el('p', { class: 'muted' }, 'No net class has an impedance target.')); return sec; }
  const sc = arr(z.stackup_changes).filter(obj);
  if (sc.length) sec.append(el('p', { class: 'small muted' }, `Stackup: ${sc.map((x) => `${x.layer} ${x.field} ${cellText(x.base)} → ${cellText(x.head)}`).join('; ')}`));
  const body = rows.map((r) => {
    const t = obj(r.target) || {};
    const b = obj(r.base); const h = obj(r.head); const sd = h || b || {};
    const geo = (x) => (x ? `${mmTxt(x.width)}${num(x.gap) ? `/${mmTxt(x.gap)}` : ''}` : '–');
    const zTxt = b && h && num(b.Z) && num(h.Z) && Math.abs(h.Z - b.Z) >= 0.05 ? `${fx(b.Z)} → ${fx(h.Z)}` : fx((h || b || {}).Z);
    const dev = h && num(h.deviation_pct) ? `${h.deviation_pct > 0 ? '+' : ''}${h.deviation_pct.toFixed(1)} %` : '–';
    const out = h && num(h.length_out_mm) ? (h.length_out_mm > 0 ? `${fx(h.length_out_mm)} / ${fx(h.length_mm)}` : '0') : '–';
    const flags = arr(r.flags).filter((f) => IMP_FLAG[f]);
    const shift = num(r.shift_pct) ? ` (stackup alone ${r.shift_pct > 0 ? '+' : ''}${r.shift_pct.toFixed(1)} %)` : '';
    const warn = arr(sd.validity).length || (typeof sd.error === 'string' && sd.error);
    const title = [impedanceSideTitle('base', b), impedanceSideTitle('head', h), ...flags.map((f) => IMP_FLAG[f][1] + (f === 'stackup_shift' ? shift : ''))].join('\n\n');
    return el('tr', { class: r.severity === 'bad' ? 'row-removed' : null, title },
      el('td', { class: 'imp-sev' }, el('span', { class: `imp imp-${r.severity === 'bad' ? 'bad' : r.severity === 'warn' ? 'warn' : 'ok'}` }, r.severity === 'bad' ? '▲' : r.severity === 'warn' ? '⚠' : '✓')),
      el('td', {}, el('code', {}, String(r.class ?? '')), t.kind === 'differential' ? el('span', { class: 'muted small' }, ' diff') : null),
      el('td', {}, String(r.layer ?? '')),
      el('td', { class: 'small' }, String(sd.structure ?? '–').replace('coplanar_grounded', 'CPWG').replace('microstrip', 'MS').replace('stripline', 'SL')),
      el('td', { class: 'num' }, b && h && geo(b) !== geo(h) ? `${geo(b)} → ${geo(h)}` : geo(h || b)),
      el('td', { class: 'num' }, zTxt, warn ? el('span', { class: 'imp-warn-dot', title: 'outside the model\'s validity range or no Z: see the tooltip' }, ' *') : null),
      el('td', { class: 'num' }, `${fx(t.target, 0)} ±${fx(t.tolerance_pct, 0)}%`),
      el('td', { class: 'num' }, dev),
      el('td', { class: `num${h && h.within === false ? ' warn-text' : ''}`, title: 'controlled length out of tolerance / controlled length, mm' }, out),
      el('td', { class: 'imp-flags' }, flags.map((f) => el('span', { class: `imp imp-${IMP_FLAG[f][2]}`, title: IMP_FLAG[f][1] + (f === 'stackup_shift' ? shift : '') }, IMP_FLAG[f][0]))));
  });
  sec.append(el('div', { class: 'scroll-x' }, el('table', { class: 'grid impedance' },
    el('thead', {}, el('tr', {}, ['', 'Class', 'Layer', 'Str.', 'w/gap mm', 'Z Ω', 'Target', 'Dev.', 'Out mm', ''].map((x) => el('th', { scope: 'col' }, x)))),
    el('tbody', {}, body))),
  el('p', { class: 'small muted' }, 'w/gap, Z and Dev. are the longest-routed width; Out is the length (any width) out of tolerance. ▲ new violation · ⚠ out of tolerance / check · ≋ stackup change · ↔ width/gap change · * outside the model\'s range. Hover a row for every width and the inputs.'));
  return sec;
}

