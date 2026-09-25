// 3D PCBA diff viewer: the module the project viewer mounts.
//
//   import { mountPcba3d } from './pcba3d/index.js';
//   const h = await mountPcba3d(el, project, baseUrl, { baseLabel, headLabel });
//   h.setMode('side' | 'overlay' | 'highlight'); h.focus('U3'); await h.ready; h.dispose();
//
// `project` is one Project of OUT/project-review.json (docs/CONTRACT-project.md); this reads
// project.pcba3d, project.pcb.board (for units and a fallback origin) and project.errors.
// `baseUrl` is the OUT directory's URL; the contract's relative paths resolve against it.
// `el` is emptied and owned by the module until dispose(); give it a height.
//
// The returned promise resolves once the UI is up; the GLBs keep loading behind a progress bar
// and `ready` resolves when they are in (it never rejects: problems are shown in the viewer
// and listed in `h.errors`).

import { normalizeComponents, countByStatus, describe, summary, tagsOf, isChange, STATUSES } from './diff.js';
import { parseGlb, prepareSide } from './scene.js';
import { assetLoader } from './assets.js';
import { buildGerberBoards } from './gerberboard.js';
import { Pcba3dView, MODES } from './viewer.js';

const MODE_LABELS = { side: 'Side by side', overlay: 'Overlay', highlight: 'Changes' };
const STATUS_LABELS = { added: 'Added', removed: 'Removed', moved: 'Moved', rotated: 'Rotated', changed: 'Changed', minor: 'Minor', unchanged: 'Unchanged' };

let cssLoaded = null;
/** Add pcba3d.css once; resolves when it applies (the scene background is read from it). */
function ensureCss() {
  if (cssLoaded) return cssLoaded;
  const href = new URL('./pcba3d.css', import.meta.url).href;
  const existing = [...document.querySelectorAll('link[rel="stylesheet"]')].find((l) => l.href === href);
  if (existing) { cssLoaded = Promise.resolve(); return cssLoaded; }
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = href;
  cssLoaded = new Promise((resolve) => {
    link.onload = resolve;
    link.onerror = resolve;          // unstyled beats not at all
    setTimeout(resolve, 3000);
  });
  document.head.appendChild(link);
  return cssLoaded;
}

function h(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

function note(el, text) {
  el.replaceChildren(h('div', { class: 'kp3d-note' }, text));
}

export async function mountPcba3d(el, project, baseUrl, options = {}) {
  await ensureCss();
  const pcba = project?.pcba3d;
  const noop = { dispose() {}, destroy() {}, focus() { return false; }, setMode() {}, ready: Promise.resolve(), errors: [] };
  el.replaceChildren();
  el.classList.add('kp3d-host');
  if (!pcba) {
    note(el, 'No 3D PCBA data for this project (the backend did not export a GLB).');
    return noop;
  }
  const base = baseUrl ? new URL(baseUrl, document.baseURI) : new URL('.', document.baseURI);
  const assets = assetLoader(base, project.slug);
  const components = normalizeComponents(pcba.components);
  const byRef = new Map(components.map((c) => [c.ref, c]));
  const counts = countByStatus(components);
  const errors = [];
  const board = project?.pcb?.board || null;

  /* ── DOM ── */
  const root = h('div', { class: 'kp3d', 'data-mode': options.mode && MODES.includes(options.mode) ? options.mode : 'side' });
  const modeButtons = MODES.map((m) => h('button', { type: 'button', 'data-mode': m, onclick: () => api.setMode(m) }, MODE_LABELS[m]));
  const viewButtons = [['top', 'Top'], ['bottom', 'Bottom'], ['iso', 'Iso'], [null, 'Fit']].map(([v, label]) =>
    h('button', { type: 'button', title: v ? `${label} view` : 'Fit the board', onclick: () => view.fit(v || 'iso') }, label));
  const toggle = (key, label) => h('label', {}, h('input', {
    type: 'checkbox', checked: true, 'data-toggle': key,
    onchange: (e) => view.setVisible(key, e.target.checked),
  }), label);
  const explode = h('input', { type: 'range', min: 0, max: 1, step: 0.01, value: 0, 'aria-label': 'Explode',
    oninput: (e) => view.setExplode(e.target.value) });
  // Which board: the one built from the fab outputs (gerbers + drills), or the GLB's own bodies.
  const sourceButtons = [['gerber', 'Fab'], ['glb', 'GLB']].map(([src, label]) => h('button', {
    type: 'button', 'data-board': src, 'aria-pressed': String(src === 'gerber'), disabled: true,
    title: src === 'gerber' ? 'Board built from the gerbers and drill files' : 'Board bodies from the kicad-cli GLB',
    onclick: () => setBoardSource(src),
  }, label));
  const sourceSeg = h('div', { class: 'kp3d-seg kp3d-board-source', role: 'group', 'aria-label': 'Board source', hidden: true }, sourceButtons);
  const boardNote = h('div', { class: 'kp3d-board-note', hidden: true });
  const toolbar = h('div', { class: 'kp3d-toolbar' },
    h('div', { class: 'kp3d-seg', role: 'group', 'aria-label': 'Mode' }, modeButtons),
    h('div', { class: 'kp3d-seg', role: 'group', 'aria-label': 'View' }, viewButtons),
    toggle('components', 'Components'), toggle('board', 'Board'), sourceSeg, toggle('silk', 'Silk'),
    toggle('markers', 'Markers'),
    h('label', { title: 'Lift components off the board' }, 'Explode', explode));

  const bars = {};
  const progress = h('div', { class: 'kp3d-progress' },
    ['base', 'head'].map((k) => {
      bars[k] = h('progress', { max: 1, value: 0 });
      return h('div', { class: 'row' }, h('span', {}, k), bars[k]);
    }),
    h('div', { class: 'msg' }, 'Loading 3D models…'));
  // Classes, not style attributes: the project viewer's CSP is style-src 'self'.
  const swatch = (kind) => h('span', { class: `kp3d-swatch ${kind}` });
  const stage = h('div', { class: 'kp3d-stage' },
    h('div', { class: 'kp3d-divider' }),
    h('div', { class: 'kp3d-label base' }, options.baseLabel || 'Base'),
    h('div', { class: 'kp3d-label head' }, options.headLabel || 'Head'),
    h('div', { class: 'kp3d-legend overlay' },
      h('span', {}, swatch('removed'), 'base'), h('span', {}, swatch('added'), 'head'),
      h('span', {}, swatch('unchanged'), 'unchanged')),
    h('div', { class: 'kp3d-legend highlight' },
      STATUSES.filter((s) => s !== 'unchanged' && s !== 'minor').map((s) => h('span', {}, swatch(s), s))),
    progress, boardNote);
  const tip = h('div', { class: 'kp3d-tip', hidden: true });

  // The active chips filter the list AND decide which parts get a marker / tint, so the default is
  // what a reviewer must look at: parts added, removed or changed (value, footprint, part number,
  // DNP, ...). Moved and rotated parts are one click away; minor ones are never emphasised.
  const DEFAULT_CHIPS = ['added', 'removed', 'changed'];
  const filters = new Set(options.chips || DEFAULT_CHIPS);
  const search = h('input', { type: 'search', placeholder: 'Filter ref, value, footprint…', oninput: () => renderList() });
  const chips = STATUSES.map((s) => h('button', {
    type: 'button', class: 'kp3d-chip', 'data-status': s, 'aria-pressed': String(filters.has(s)),
    onclick: (e) => {
      if (filters.has(s)) filters.delete(s); else filters.add(s);
      e.currentTarget.setAttribute('aria-pressed', String(filters.has(s)));
      view?.setEmphasis(filters);
      renderList();
    },
  }, h('span', { class: `kp3d-dot ${s}` }), `${STATUS_LABELS[s]} ${counts[s]}`));
  const rows = h('ul', { class: 'kp3d-rows', role: 'listbox', 'aria-label': 'Component changes' });
  const list = h('aside', { class: 'kp3d-list' }, h('div', { class: 'kp3d-filters' }, search, chips), rows);
  const status = h('div', { class: 'kp3d-status' }, 'Loading…');
  root.append(toolbar, h('div', { class: 'kp3d-body' }, stage, list), status);
  el.append(root);
  document.body.append(tip);

  /* ── View ── */
  let selected = null;
  const view = new Pcba3dView(stage, {
    onError: (e) => { errors.push(e); statusLine(); },
    onHover: (hit) => showTip(hit),
    onPick: (ref) => { if (ref) api.focus(ref, { frame: false }); else select(null); },
  });
  stage.insertBefore(view.canvas, stage.firstChild);

  function themeBackground() {
    const bg = getComputedStyle(stage).backgroundColor;
    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') view.setBackground(bg);
  }
  themeBackground();
  const themeObserver = new MutationObserver(() => requestAnimationFrame(themeBackground));
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] });
  const media = window.matchMedia?.('(prefers-color-scheme: dark)');
  media?.addEventListener?.('change', themeBackground);

  /* ── List ── */
  let rowByRef = new Map();
  let meshless = new Set();
  function renderList() {
    const q = search.value.trim().toLowerCase();
    const shown = components.filter((c) => tagsOf(c).some((t) => filters.has(t)) && (!q || [c.ref, c.base?.value, c.head?.value, c.base?.footprint, c.head?.footprint]
      .some((v) => v && String(v).toLowerCase().includes(q))));
    rowByRef = new Map();
    rows.replaceChildren(...shown.map((c) => {
      const li = h('li', {
        role: 'option', 'data-ref': c.ref, 'aria-selected': String(c.ref === selected), title: `${c.ref}: ${c.status}`,
        onclick: () => api.focus(c.ref),
        onmouseenter: () => view.highlight(c.ref),
        onmouseleave: () => view.highlight(null),
      }, h('span', { class: `kp3d-dot ${c.status}` }), h('span', { class: 'ref' }, c.ref),
      h('span', { class: 'sum' }, summary(c) || STATUS_LABELS[c.status]),
      tagsOf(c).length > 1 ? h('span', { class: 'kp3d-tags' }, tagsOf(c).map((t) =>
        h('span', { class: `kp3d-badge ${t}`, title: STATUS_LABELS[t] }, t))) : null,
      meshless.has(c.ref) ? h('span', { class: 'nomesh', title: 'No 3D geometry found for this component' }, 'no 3D') : null);
      rowByRef.set(c.ref, li);
      return li;
    }));
    if (!shown.length) rows.append(h('li', { class: 'kp3d-empty' }, components.length ? 'No components match the filter.' : 'No components listed.'));
  }
  renderList();

  function select(ref) {
    selected = ref;
    for (const [r, li] of rowByRef) li.setAttribute('aria-selected', String(r === ref));
    rowByRef.get(ref)?.scrollIntoView({ block: 'nearest' });
    view.select(ref);
  }

  /* ── Hover card ── */
  function showTip(hit) {
    if (!hit) { tip.hidden = true; return; }
    const c = byRef.get(hit.ref);
    const trs = c ? describe(c).map((r) => {
      let cell;
      if (r.changed) cell = h('td', {}, h('span', { class: 'old' }, r.base), h('span', { class: 'arrow' }, ' → '), r.head);
      else if (r.base !== null && r.head !== null) cell = h('td', {}, r.head);
      else cell = h('td', {}, r.head ?? r.base);
      return h('tr', { class: r.changed ? 'changed' : '' }, h('td', {}, r.label), cell);
    }) : [];
    tip.replaceChildren(h('div', {}, h('strong', {}, hit.ref), ' ',
      h('span', { class: `kp3d-badge ${c?.status || ''}` }, c?.status || 'unknown'),
      h('span', { class: 'kp3d-muted' }, `  ${hit.side}`)), h('table', {}, trs));
    tip.hidden = false;
    const pad = 14;
    const w = tip.offsetWidth, ht = tip.offsetHeight;
    const x = hit.clientX + pad + w > window.innerWidth ? hit.clientX - w - pad : hit.clientX + pad;
    const y = hit.clientY + pad + ht > window.innerHeight ? hit.clientY - ht - pad : hit.clientY + pad;
    tip.style.left = `${Math.max(0, x)}px`;
    tip.style.top = `${Math.max(0, y)}px`;
  }

  /* ── Loading ── */
  const controller = new AbortController();
  const sideSpec = { base: pcba.base, head: pcba.head };
  const sideState = { base: null, head: null };
  function showMissing(k, text) {
    stage.querySelector(`.kp3d-missing.${k}`)?.remove();
    if (text) stage.append(h('div', { class: `kp3d-missing ${k}` }, text));
  }

  async function loadSide(k) {
    const spec = sideSpec[k];
    if (!spec || !spec.glb) {
      bars[k].value = 1;
      showMissing(k, project?.status === (k === 'base' ? 'added' : 'removed') ? `Not present in ${k}` : `No ${k} 3D model`);
      return null;
    }
    try {
      const bytes = await assets.bytes(spec.glb, (loaded, total) => {
        bars[k].value = total ? loaded / total : 0.5;
      }, controller.signal);
      bars[k].value = 1;
      const gltf = await parseGlb(bytes);
      return prepareSide(gltf, components, k, board?.[k] ? { ...board, ...board[k] } : board, pcba.frame || null);
    } catch (e) {
      if (controller.signal.aborted) return null;
      const msg = `${k}: could not load 3D model (${e.message || e})`;
      errors.push(msg);
      showMissing(k, `Could not load the ${k} 3D model`);
      return null;
    }
  }

  function fallbackKinds(subs) {
    const ext = (p) => (String(p).match(/\.[A-Za-z0-9]+$/) || [''])[0].toLowerCase();
    return [...new Set(subs.map((s) => `${ext(s.from)} → ${ext(s.to)}`))].join(', ');
  }

  function statusLine() {
    const parts = [];
    const total = components.length;
    const changed = components.filter(isChange).length;
    const minor = components.filter((c) => c.status === 'minor').length;
    parts.push(`${total} components, ${changed} changed` + (minor ? ` (+${minor} minor: model format, library name or non-part fields only)` : ''));
    for (const k of ['base', 'head']) {
      const s = sideState[k];
      if (!s) continue;
      const r = s.report;
      // Footprints with no 3D model (mounting holes, jumpers) have nothing to find in the GLB.
      const noModel = components.filter((c) => c[k] && c[k].model === null && !s.comps.has(c.ref)).length;
      const missing = r.expected - r.matched - noModel;
      let t = `${k}: ${r.matched} parts matched to 3D (${r.method}${r.byPosition && r.byName ? `: ${r.byName} by name, ${r.byPosition} by position` : ''})`;
      if (noModel) t += `, ${noModel} without a 3D model`;
      if (missing > 0) t += `, ${missing} not found`;
      if (r.loose) t += `, ${r.loose} unassigned bodies`;
      const subs = project?.pcba3d?.models?.[k]?.substitutions;
      if (Array.isArray(subs) && subs.length) t += `, ${subs.length} missing model path(s) exported with the other format (${fallbackKinds(subs)})`;
      parts.push(t);
    }
    const fab = boardState.fab;
    if (fab) {
      const h0 = fab.sides.head || fab.sides.base;
      let t = `board from gerbers: ${h0.holes.kept.length} holes drilled`;
      if (h0.holes.leftOut) t += `, ${h0.holes.leftOut.count} smallest painted only`;
      if (Object.values(fab.sides).some((x) => x?.approximate)) t += ', outline approximated by its bounding box';
      if (fab.outlineChanged) t += ', outline changed (base outline shown as a red edge)';
      parts.push(t);
    }
    status.replaceChildren(parts.join(' · '),
      ...[...(project?.errors || []).filter((e) => /3d|glb|pcba/i.test(e)), ...errors].map((e) => h('div', { class: 'err' }, e)));
  }

  let pendingFocus = null;
  const boardState = { fab: null };
  let disposed = false;
  const ready = (async () => {
    const [b, hd] = await Promise.all([loadSide('base'), loadSide('head')]);
    if (disposed) return;
    progress.querySelector('.msg').textContent = 'Building scene…';
    sideState.base = b; sideState.head = hd;
    // A component listed on a side but with no geometry found there.
    meshless = new Set(components.filter((c) =>
      ['base', 'head'].some((k) => c[k] && c[k].model !== null && sideState[k] && !sideState[k].comps.has(c.ref))).map((c) => c.ref));
    view.setSides({ base: b, head: hd }, byRef);
    view.setEmphasis(filters);
    view.setMode(root.dataset.mode);
    view.fit('iso');
    renderList();
    statusLine();
    progress.hidden = true;
    if (!b && !hd) {
      stage.append(h('div', { class: 'kp3d-note kp3d-stage-note' },
        'No 3D model could be loaded for either side.'));
    }
    if (pendingFocus) api.focus(pendingFocus);
    if (options.fabBoard !== false) await loadFabBoard();
  })().catch((e) => {
    errors.push(`3D viewer: ${e.message || e}`);
    progress.hidden = true;
    statusLine();
  });

  function setBoardSource(src) {
    for (const b of sourceButtons) b.setAttribute('aria-pressed', String(b.dataset.board === src));
    view.setBoardSource(src);
  }

  /** The board from the fab outputs, painted with the gerbers; the GLB's board until it is in. */
  async function loadFabBoard() {
    if (!project.pcb?.layers?.length) return;
    boardNote.hidden = false;
    boardNote.textContent = 'Painting the board from the gerbers…';
    try {
      const gb = await buildGerberBoards(project, assets, { onStatus: (t) => { boardNote.textContent = t; } });
      if (disposed) { gb?.dispose(); return; }
      if (!gb) { boardNote.hidden = true; return; }
      view.setGerberBoards(gb);
      sourceSeg.hidden = false;
      for (const b of sourceButtons) b.disabled = false;
      boardState.fab = gb;
      statusLine();
    } catch (e) {
      errors.push(`board from gerbers: ${e.message || e} (showing the GLB's board)`);
      statusLine();
    }
    boardNote.hidden = true;
  }

  /* ── API ── */
  const api = {
    ready,
    errors,
    view,
    setBoardSource,
    setMode(mode) {
      if (!MODES.includes(mode)) return;
      root.dataset.mode = mode;
      for (const b of modeButtons) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
      view.setMode(mode);
    },
    /** Select a component in the list and both views, and frame it. Returns false if unknown. */
    focus(ref, { frame = true } = {}) {
      if (!byRef.has(ref)) return false;
      if (!sideState.base && !sideState.head) { pendingFocus = ref; select(ref); return true; }
      select(ref);
      if (frame) view.focus(ref);
      return true;
    },
    capture() { return view.capture(); },
    dispose() {
      if (disposed) return;
      disposed = true;
      controller.abort();
      themeObserver.disconnect();
      media?.removeEventListener?.('change', themeBackground);
      view.dispose();
      tip.remove();
      el.replaceChildren();
      el.classList.remove('kp3d-host');
    },
  };
  api.destroy = api.dispose;
  api.setMode(root.dataset.mode);
  return api;
}
