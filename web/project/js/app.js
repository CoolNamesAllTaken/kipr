// kipr project review viewer: app shell. Loads project-review.json (docs/CONTRACT-project.md) from the
// same directory; project list -> per-project tabs, deep-linkable through the URL hash (route.js).
import { el, clear, append, fetchJson, commitUrl, blobUrl, shortSha, badge, arr, obj, SLUG_RE } from './util.js';
import { parseHash, formatHash, TABS } from './route.js';
import { initTheme, toggleTheme, themeButton } from './theme.js';
import { createSchematicView } from './schematic.js';
import { createLayoutView } from './layout.js';
import { createPcba3dView } from './pcba3d.js';
import { createBomView, createNetlistView, createChecksView } from './tables.js';

const $ = (sel) => document.querySelector(sel);
const STATUS_ORDER = { modified: 0, added: 1, removed: 2 };
const VIEWS = { schematic: createSchematicView, layout: createLayoutView, pcba3d: createPcba3dView, bom: createBomView, netlist: createNetlistView, checks: createChecksView };

const state = { review: null, projects: [], filter: '', route: parseHash(''), project: null, view: null, viewKey: null };

/** Projects from the review: valid slug, unique, sorted by status then name. */
export function projectsOf(review) {
  const seen = new Set();
  return arr(review?.projects).filter((p) => {
    if (!obj(p) || typeof p.slug !== 'string' || !SLUG_RE.test(p.slug) || seen.has(p.slug)) return false;
    seen.add(p.slug);
    return true;
  }).sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || String(a.name || a.slug).localeCompare(String(b.name || b.slug)));
}

async function boot() {
  initTheme();
  try {
    state.review = await fetchJson('project-review.json');
  } catch (e) {
    const msg = location.protocol === 'file:'
      ? ['Browsers block loading data from file:// pages and this copy has no ', el('code', {}, 'data.js'), '. Run ', el('code', {}, 'python3 serve.py'), ' in this folder and open the address it prints.']
      : [`Could not load project-review.json (${e.message}).`];
    clear($('#main')).append(el('div', { class: 'fatal' }, el('h2', {}, 'No project review data'), el('p', {}, ...msg)));
    renderHeader();
    return;
  }
  state.projects = projectsOf(state.review);
  renderHeader();
  renderSidebar();
  window.addEventListener('hashchange', route);
  document.addEventListener('keydown', onKey);
  route();
}

// --- header -------------------------------------------------------------------------------------------

function renderHeader() {
  const r = obj(state.review) || {};
  const h = clear($('#header'));
  const side = (s) => {
    const o = obj(s);
    if (!o) return el('span', { class: 'muted' }, '—');
    const url = commitUrl(r.repo, o.sha);
    const label = el('code', {}, typeof o.short === 'string' ? o.short : shortSha(o.sha));
    return el('span', { title: typeof o.sha === 'string' ? o.sha : null }, typeof o.ref === 'string' ? `${o.ref} ` : '', url ? el('a', { href: url }, label) : label);
  };
  const tool = obj(r.tool);
  append(h, [
    el('a', { class: 'title', href: '#/' }, 'Project review'),
    typeof obj(r.repo)?.url === 'string' ? el('span', { class: 'hdr-item' }, el('a', { href: r.repo.url }, r.repo.url.replace(/^https?:\/\/(www\.)?/, ''))) : null,
    state.review ? el('span', { class: 'hdr-item' }, side(r.base), ' → ', side(r.head)) : null,
    tool ? el('span', { class: 'hdr-item muted' }, [tool.name && tool.version ? `${tool.name} ${tool.version}` : '', tool.kicad ? ` · KiCad ${tool.kicad}` : ''].join('')) : null,
    el('span', { class: 'spacer' }),
    el('button', { class: 'btn small', title: 'Keyboard shortcuts (?)', onclick: () => toggleHelp() }, '?'),
    themeButton(),
  ]);
}

// --- sidebar ------------------------------------------------------------------------------------------

function renderSidebar() {
  const side = clear($('#sidebar'));
  const input = el('input', { type: 'search', placeholder: 'Filter projects…', 'aria-label': 'Filter projects', value: state.filter });
  input.addEventListener('input', () => { state.filter = input.value; renderList(); });
  side.append(el('div', { class: 'filter' }, input), el('nav', { id: 'project-list', 'aria-label': 'Projects' }));
  renderList();
}

function matches(p, q) {
  if (!q) return true;
  const hay = `${p.name} ${p.slug} ${p.path} ${p.status}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).every((t) => hay.includes(t));
}

/** Short summary chips for a project: [label, value, title] with value > 0 only. */
export function summaryChips(summary) {
  const s = obj(summary) || {};
  const c = obj(s.components) || {};
  const n = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  const chips = [
    ['sch', n(s.sheets_changed), 'schematic sheets changed'],
    ['pcb', n(s.layers_changed), 'PCB layers changed'],
    ['+', n(c.added), 'components added'],
    ['−', n(c.removed), 'components removed'],
    ['mv', n(c.moved), 'components moved'],
    ['chg', n(c.changed), 'components changed'],
    ['nets', n(s.nets_changed), 'nets changed'],
    ['ERC', n(obj(s.erc)?.new), 'new ERC violations'],
    ['DRC', n(obj(s.drc)?.new), 'new DRC violations'],
  ];
  return chips.filter(([, v]) => v > 0);
}

function chipEls(summary) {
  return summaryChips(summary).map(([k, v, t]) => el('span', { class: `chip${k === 'ERC' || k === 'DRC' ? ' bad' : ''}`, title: `${v} ${t}` }, `${k} ${v}`));
}

function renderList() {
  const list = clear($('#project-list'));
  const shown = state.projects.filter((p) => matches(p, state.filter));
  if (!shown.length) list.append(el('p', { class: 'muted pad' }, state.projects.length ? 'No projects match.' : 'No changed projects in this review.'));
  const ul = el('ul');
  for (const p of shown) {
    const active = state.project?.slug === p.slug;
    ul.append(el('li', {}, el('a', {
      href: formatHash({ slug: p.slug, tab: state.route.tab }), class: `item-link${active ? ' active' : ''}`, 'aria-current': active ? 'page' : null,
    },
    el('span', { class: 'item-name', title: String(p.path || p.name || p.slug) }, String(p.name || p.slug)),
    el('span', { class: 'item-meta' }, badge('status', p.status), chipEls(p.summary)))));
  }
  list.append(ul);
}

// --- routing ------------------------------------------------------------------------------------------

/** Update the hash for the current project/tab without re-rendering (views call this). */
function setRoute(partial, replace = true) {
  const cur = state.route;
  const next = {
    slug: cur.slug, tab: cur.tab,
    item: 'item' in partial ? partial.item : cur.item,
    params: partial.params ? Object.fromEntries(Object.entries(partial.params).filter(([, v]) => v !== null && v !== undefined && v !== '')) : cur.params,
  };
  state.route = next;
  const h = formatHash(next);
  if (h === location.hash) return;
  if (replace) history.replaceState(null, '', h); else history.pushState(null, '', h);
}

function defaultTab(p) {
  if (p.schematic) return 'schematic';
  if (p.pcb) return 'layout';
  return 'bom';
}

function route() {
  const r = parseHash(location.hash);
  const p = r.slug ? state.projects.find((x) => x.slug === r.slug) : null;
  if (p && !r.tab) r.tab = defaultTab(p);
  const key = p ? `${p.slug}|${r.tab}|${r.item ?? ''}` : null;
  state.route = r;
  if (p && key === state.viewKey && state.view) {
    state.view.onParams?.(r.params);
    return;
  }
  // a different item within the same tab re-creates the view, but keep the item list scroll etc. simple
  state.view?.destroy?.();
  state.view = null;
  state.viewKey = key;
  state.project = p;
  renderList();
  document.querySelector('.item-link.active')?.scrollIntoView({ block: 'nearest' });
  if (p) renderProject(p, r);
  else renderOverview(r.slug);
}

// --- overview -----------------------------------------------------------------------------------------

function renderOverview(unknownSlug) {
  const main = clear($('#main'));
  document.title = 'Project review · kipr';
  if (unknownSlug) main.append(el('div', { class: 'notice' }, `No project "${unknownSlug}" in this review; showing the overview.`));
  main.append(el('h1', {}, 'Projects'));
  const runErrors = arr(state.review?.errors).filter((x) => typeof x === 'string');
  if (runErrors.length) {
    main.append(el('details', { class: 'notice' }, el('summary', {}, `${runErrors.length} problem${runErrors.length > 1 ? 's' : ''} while generating this review`),
      el('ul', {}, runErrors.map((e) => el('li', {}, e)))));
  }
  if (!state.projects.length) { main.append(el('p', { class: 'muted' }, 'No KiCad projects changed between these commits.')); return; }
  const cols = ['Project', 'Status', 'Sheets', 'Layers', 'Comp. +', 'Comp. −', 'Moved', 'Changed', 'Nets', 'ERC new', 'DRC new', 'Problems'];
  const n = (v) => (typeof v === 'number' ? String(v) : '');
  const rows = state.projects.map((p) => {
    const s = obj(p.summary) || {};
    const c = obj(s.components) || {};
    const errs = arr(p.errors).length;
    return el('tr', {},
      el('td', {}, el('a', { href: formatHash({ slug: p.slug }) }, String(p.name || p.slug)), el('div', { class: 'small muted path' }, String(p.path || ''))),
      el('td', {}, badge('status', p.status)),
      [s.sheets_changed, s.layers_changed, c.added, c.removed, c.moved, c.changed, s.nets_changed, obj(s.erc)?.new, obj(s.drc)?.new].map((v) => el('td', { class: 'num' }, n(v))),
      el('td', { class: 'num' }, errs ? el('span', { class: 'warn-text', title: 'non-fatal export problems' }, String(errs)) : ''));
  });
  main.append(el('section', { class: 'card' }, el('div', { class: 'scroll-x' }, el('table', { class: 'grid overview' },
    el('thead', {}, el('tr', {}, cols.map((t) => el('th', { scope: 'col' }, t)))), el('tbody', {}, rows)))));
  main.append(el('p', { class: 'hint' }, 'Press ? for keyboard shortcuts.'));
}

// --- project page -------------------------------------------------------------------------------------

function renderProject(p, r) {
  const main = clear($('#main'));
  main.scrollTop = 0;
  const tabLabel = TABS.find(([t]) => t === r.tab)?.[1] || '';
  document.title = `${p.name || p.slug} · ${tabLabel} · Project review`;
  const r0 = obj(state.review) || {};
  const src = blobUrl(r0.repo, obj(p.status === 'removed' ? r0.base : r0.head)?.sha, typeof p.path === 'string' ? p.path : '');
  main.append(el('div', { class: 'item-title' },
    el('h1', {}, String(p.name || p.slug)), badge('status', p.status),
    el('span', { class: 'muted path' }, src ? el('a', { href: src }, String(p.path)) : String(p.path || '')),
    el('span', { class: 'chips' }, chipEls(p.summary)),
    el('button', { class: 'btn small', title: 'Copy a link to this view', onclick: (e) => copyLink(e.currentTarget) }, 'Copy link')));
  const errors = arr(p.errors).filter((x) => typeof x === 'string');
  if (errors.length) {
    main.append(el('details', { class: 'notice' }, el('summary', {}, `${errors.length} export problem${errors.length > 1 ? 's' : ''}`), el('ul', {}, errors.map((e) => el('li', {}, e)))));
  }
  const tabBar = el('div', { class: 'tabs', role: 'tablist' });
  TABS.forEach(([t, label], i) => {
    const count = tabCount(p, t);
    tabBar.append(el('a', {
      class: 'tab', role: 'tab', href: formatHash({ slug: p.slug, tab: t }), 'aria-selected': String(t === r.tab), title: `${label} (${i + 1})`,
    }, label, count ? el('span', { class: 'tab-count' }, String(count)) : null));
  });
  const box = el('div', { class: `view-box tab-${r.tab}` });
  main.append(tabBar, box);
  const make = VIEWS[r.tab];
  state.view = make(p, box, { route: r, setRoute });
}

/** Badge number on a tab: changed sheets / layers / BOM rows / nets / new violations. */
export function tabCount(p, t) {
  const s = obj(p.summary) || {};
  const c = obj(s.components) || {};
  const n = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  if (t === 'schematic') return n(s.sheets_changed);
  if (t === 'layout') return n(s.layers_changed);
  if (t === 'pcba3d') return n(c.added) + n(c.removed) + n(c.moved) + n(c.changed);
  if (t === 'bom') return arr(obj(p.bom)?.rows).filter((x) => obj(x) && x.status !== 'unchanged').length;
  if (t === 'netlist') return n(s.nets_changed);
  if (t === 'checks') return n(obj(s.erc)?.new) + n(obj(s.drc)?.new);
  return 0;
}

function copyLink(btn) {
  const url = location.href;
  const done = () => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy link'; }, 1200); };
  if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, () => prompt('Link', url));
  else prompt('Link', url);
}

// --- keyboard -----------------------------------------------------------------------------------------

const SHORTCUTS = [
  ['1 – 6', 'switch tab (Schematic, Layout, 3D, BOM, Netlist, ERC/DRC)'],
  ['j / k', 'next / previous project'],
  ['[ / ]', 'previous / next sheet (schematic) or diff layer (layout)'],
  ['n / p', 'next / previous change (zooms to it)'],
  ['m', 'cycle compare mode (side by side, diff, onion, swipe)'],
  ['v', 'cycle board view (top, bottom, layers)'],
  ['f', 'fit to view (double-click also works)'],
  ['r', 'measure tool (layout)'],
  ['t', 'toggle dark / light theme'],
  ['/', 'focus the filter'],
  ['Esc', 'clear highlight / close this help'],
  ['?', 'this help'],
];

function toggleHelp(force) {
  let dlg = $('#help');
  if (!dlg) {
    dlg = el('div', { id: 'help', class: 'help', role: 'dialog', 'aria-label': 'Keyboard shortcuts', hidden: true },
      el('div', { class: 'help-card' }, el('h2', {}, 'Keyboard shortcuts'),
        el('table', { class: 'kv' }, el('tbody', {}, SHORTCUTS.map(([k, d]) => el('tr', {}, el('th', { scope: 'row' }, el('kbd', {}, k)), el('td', {}, d))))),
        el('button', { class: 'btn', onclick: () => toggleHelp(false) }, 'Close')));
    dlg.addEventListener('click', (e) => { if (e.target === dlg) toggleHelp(false); });
    document.body.append(dlg);
  }
  dlg.hidden = force === undefined ? !dlg.hidden : !force;
}

function onKey(e) {
  if (e.target.closest?.('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'Escape' && $('#help') && !$('#help').hidden) { toggleHelp(false); return; }
  if (e.key === '?') { toggleHelp(); return; }
  if (state.view?.onKey?.(e)) { e.preventDefault(); return; }
  if (e.key === 't') { toggleTheme(); return; }
  if (/^[1-6]$/.test(e.key) && state.project) {
    location.hash = formatHash({ slug: state.project.slug, tab: TABS[+e.key - 1][0] });
    return;
  }
  if (e.key === 'j' || e.key === 'k') {
    const vis = state.projects.filter((p) => matches(p, state.filter));
    const idx = vis.findIndex((p) => p.slug === state.project?.slug);
    const next = vis[Math.min(Math.max(idx + (e.key === 'j' ? 1 : -1), 0), vis.length - 1)];
    if (next) location.hash = formatHash({ slug: next.slug, tab: state.route.tab });
  } else if (e.key === '/') {
    e.preventDefault();
    $('#sidebar input')?.focus();
  }
}

if (typeof document !== 'undefined' && document.getElementById('main')) boot();
