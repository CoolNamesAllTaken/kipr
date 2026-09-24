// Deep links. Pure (no DOM) so it can be unit-tested.
//
//   #/                                  overview
//   #/p/<slug>                          project, default tab
//   #/p/<slug>/<tab>                    tab: schematic | layout | pcba3d | bom | netlist | checks
//   #/p/<slug>/<tab>/<item>?k=v&...     item: sheet id (schematic) or layer id (layout), URI-encoded;
//                                       params: mode, c (change index), view (top|bottom|layers), q (filter), ...

export const TABS = [
  ['schematic', 'Schematic'],
  ['layout', 'Layout'],
  ['pcba3d', '3D PCBA'],
  ['bom', 'BOM'],
  ['netlist', 'Netlist'],
  ['checks', 'ERC/DRC'],
];
const TAB_IDS = new Set(TABS.map(([t]) => t));

/** {slug, tab, item, params} from a location.hash; slug null for the overview. Never throws. */
export function parseHash(hash) {
  let h = String(hash || '').replace(/^#/, '');
  let query = '';
  const qi = h.indexOf('?');
  if (qi >= 0) { query = h.slice(qi + 1); h = h.slice(0, qi); }
  const parts = h.replace(/^\/+/, '').split('/').map(safeDecode);
  const params = {};
  for (const kv of query.split('&')) {
    if (!kv) continue;
    const eq = kv.indexOf('=');
    const k = safeDecode(eq >= 0 ? kv.slice(0, eq) : kv);
    const v = eq >= 0 ? safeDecode(kv.slice(eq + 1)) : '';
    if (/^[a-z0-9_]{1,20}$/i.test(k)) params[k] = v;
  }
  if (parts[0] !== 'p' || !parts[1]) return { slug: null, tab: null, item: null, params };
  const tab = TAB_IDS.has(parts[2]) ? parts[2] : null;
  const item = tab && parts.length > 3 ? parts.slice(3).join('/') : null;
  return { slug: parts[1], tab, item, params };
}

function safeDecode(s) {
  try { return decodeURIComponent(s); } catch { return s; }
}

/** Inverse of parseHash. Empty/null params are dropped; the item is one encoded segment. */
export function formatHash({ slug = null, tab = null, item = null, params = {} } = {}) {
  if (!slug) return '#/';
  let h = `#/p/${encodeURIComponent(slug)}`;
  if (tab) {
    h += `/${tab}`;
    if (item !== null && item !== undefined && item !== '') h += `/${encodeURIComponent(item)}`;
  }
  const q = Object.entries(params || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return q.length ? `${h}?${q.join('&')}` : h;
}
