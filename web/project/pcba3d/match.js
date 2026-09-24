// Mesh -> reference designator matching for the 3D PCBA viewer. Pure functions, no three.js, so
// they run under `node --test` (tests/web-3d/match.test.mjs).
//
// A GLB from `kicad-cli pcb export glb` is a scene of nodes. Two ways to know which node is which
// component, tried in this order:
//
//   1. By name. KiCad's STEP/glTF exporter labels each component instance with its reference
//      designator, and OCCT's glTF writer uses instance names for nodes, so a node is usually
//      called "R5" -- sometimes with a suffix when a footprint carries several models
//      ("R5_1", "R5 (2)"). `refFromName` handles those.
//   2. By position, for whatever the names did not settle. Ported from gentoo's viewer3d.js
//      (PantsForBirds/internal, branch john/gentoo, fab/static/fab/viewer3d.js:
//      matchToDesignators / settleTies): a match has to be close AND clearly closer than the
//      runner-up, so two parts within a hair of each other stay unnamed rather than being
//      named by a rounding error; then pairs that are each other's nearest are settled.
//      Unlike gentoo we do not know the export origin (kicad-cli may put the board centre, the
//      drill origin or the page origin at 0,0), so the translation between the component list
//      and the GLB is fitted first: from the name matches when there are any, otherwise by a
//      Hough vote over every (component, node) pair.
//
// Frames: components arrive in KiCad board mm with y DOWN (the contract's frame). Nodes are
// given here in the viewer's board frame: mm, x right, y UP, z out of the top copper. So a
// component at (x, y) is expected at (x, -y) + T, where T is the fitted translation.

export const MATCH_MM = 2.0;          // how far a node may be from its placement and still match
export const HOUGH_BIN_MM = 0.5;      // translation vote resolution

/** Natural sort for designators: R2 < R10, and U1 < U1A. */
export function naturalCompare(a, b) {
  const ax = String(a).match(/(\d+|\D+)/g) || [];
  const bx = String(b).match(/(\d+|\D+)/g) || [];
  for (let i = 0; i < Math.min(ax.length, bx.length); i++) {
    const x = ax[i], y = bx[i];
    if (x === y) continue;
    const nx = /^\d/.test(x), ny = /^\d/.test(y);
    if (nx && ny) return Number(x) - Number(y) || x.length - y.length;
    return x < y ? -1 : 1;
  }
  return ax.length - bx.length;
}

/**
 * The reference designator a node name stands for, or null. `refs` is a Set of the designators
 * that exist on this side. Tries the exact name, then the name with a copy suffix removed
 * ("R5_1", "R5-2", "R5:1", "R5.1", "R5 (2)"), then the leading token ("R5 [0603]"). Never strips
 * digits without a separator: "R11" must not become "R1".
 */
export function refFromName(name, refs) {
  if (!name) return null;
  const text = String(name).trim();
  if (refs.has(text)) return text;
  const stripped = text.replace(/(?:[_:.\- ]\d+|\s*\(\d+\))+$/, '');
  if (stripped !== text && refs.has(stripped)) return stripped;
  const token = text.split(/[\s\[\](){}<>,;|/\\]+/)[0];
  if (token && token !== text && refs.has(token)) return token;
  return null;
}

/** KiCad board mm (y down) -> viewer board frame (y up). */
export function toBoardFrame(component) {
  return { x: Number(component.x) || 0, y: -(Number(component.y) || 0) };
}

function median(values) {
  if (!values.length) return 0;
  const s = values.slice().sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * Distance from where a component should be to a node: the nearer of the node's origin and
 * the middle of its bounding box. The origin is the placement for almost every part; the box
 * middle is the fallback for a GLB whose meshes were baked into world coordinates (every
 * origin at 0,0), and for models drawn far from their own origin.
 */
function distanceTo(node, aim) {
  const d1 = Math.hypot(node.x - aim.x, node.y - aim.y);
  const d2 = Math.hypot(node.cx - aim.x, node.cy - aim.y);
  return Math.min(d1, d2);
}

/**
 * Translation T (board frame) such that node ~= component + T, by voting: every pair of a
 * component and a node votes for the translation that would put one on the other; the bin with
 * the most votes wins and is refined by the median of the pairs that voted for it (and its
 * neighbours). O(components x nodes), which is ~10^5 for a board of a few hundred parts.
 */
export function houghTranslation(nodes, aims, binMm = HOUGH_BIN_MM) {
  if (!nodes.length || !aims.length) return null;
  const votes = new Map();
  // Numeric keys: a string per pair is the slow part of this loop on a board of hundreds.
  const key = (i, j) => (i + 1048576) * 2097152 + (j + 1048576);
  for (const a of aims) {
    for (const n of nodes) {
      for (const [px, py] of [[n.x, n.y], [n.cx, n.cy]]) {
        const i = Math.round((px - a.x) / binMm), j = Math.round((py - a.y) / binMm);
        const k = key(i, j);
        votes.set(k, (votes.get(k) || 0) + 1);
      }
    }
  }
  // Score a bin with its 3x3 neighbourhood so a translation that falls on a bin edge still wins.
  let best = null, bestScore = -1;
  for (const k of votes.keys()) {
    const i = Math.floor(k / 2097152) - 1048576, j = (k % 2097152) - 1048576;
    let score = 0;
    for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) score += votes.get(key(i + di, j + dj)) || 0;
    if (score > bestScore) { bestScore = score; best = [i, j]; }
  }
  const tx = best[0] * binMm, ty = best[1] * binMm;
  return refineTranslation(nodes, aims, { x: tx, y: ty }, binMm * 2);
}

/** Median offset of each aim's nearest node within `radius` of the guess. */
export function refineTranslation(nodes, aims, guess, radius) {
  const dx = [], dy = [];
  for (const a of aims) {
    const aim = { x: a.x + guess.x, y: a.y + guess.y };
    let best = null, bestD = radius;
    for (const n of nodes) {
      for (const [px, py] of [[n.x, n.y], [n.cx, n.cy]]) {
        const d = Math.hypot(px - aim.x, py - aim.y);
        if (d <= bestD) { bestD = d; best = [px, py]; }
      }
    }
    if (best) { dx.push(best[0] - a.x); dy.push(best[1] - a.y); }
  }
  if (!dx.length) return { x: guess.x, y: guess.y, support: 0 };
  return { x: median(dx), y: median(dy), support: dx.length };
}

/**
 * One-to-one nearest matching with an ambiguity guard, then tie settling (gentoo's
 * matchToDesignators + settleTies). `nodes` are candidates not yet taken; `targets` are
 * {ref, aim:{x,y}}. Returns {matched: Map ref -> node index, ambiguous: [ref], unmatched: [ref]}.
 */
export function matchByPosition(nodes, targets, tol = MATCH_MM) {
  const taken = new Set();
  const matched = new Map();
  let ambiguous = [];
  const unmatched = [];

  for (const t of targets) {
    let best = -1, bestD = Infinity, runnerUp = Infinity;
    nodes.forEach((n, i) => {
      if (taken.has(i)) return;
      const d = distanceTo(n, t.aim);
      if (d < bestD) { runnerUp = bestD; bestD = d; best = i; } else if (d < runnerUp) runnerUp = d;
    });
    // "Clearly closer" means under half the runner-up's distance -- but two exact hits (a model
    // split in two nodes on the same origin) are not a reason to refuse: allow a small floor.
    if (best < 0 || bestD > tol) { unmatched.push(t.ref); continue; }
    if (bestD > Math.max(runnerUp / 2, 0.05)) { ambiguous.push(t); continue; }
    taken.add(best);
    matched.set(t.ref, best);
  }

  // Among what is left, a node whose nearest waiting claimant is this component is its node.
  let progress = true;
  while (progress && ambiguous.length) {
    progress = false;
    for (const t of ambiguous.slice()) {
      let best = -1, bestD = Infinity;
      nodes.forEach((n, i) => {
        if (taken.has(i)) return;
        const d = distanceTo(n, t.aim);
        if (d < bestD) { bestD = d; best = i; }
      });
      if (best < 0 || bestD > tol) continue;
      const contender = ambiguous.find((o) => o !== t && distanceTo(nodes[best], o.aim) < bestD);
      if (contender) continue;
      taken.add(best);
      matched.set(t.ref, best);
      ambiguous.splice(ambiguous.indexOf(t), 1);
      progress = true;
    }
  }
  ambiguous = ambiguous.map((t) => t.ref);
  return { matched, ambiguous, unmatched, taken };
}

/**
 * Map a side's candidate nodes to its components.
 *
 * nodes:      [{name, x, y, cx, cy}] in the viewer board frame (mm, y up). x/y is the node's
 *             origin, cx/cy the middle of its bounding box.
 * components: [{ref, x, y}] in KiCad board mm (y down) -- one side of the contract's list.
 *
 * Returns {
 *   byRef: Map ref -> [node index],       every node that belongs to a component
 *   offset: {x, y},                        T: where the component frame's origin sits in the GLB
 *   method: 'name' | 'position' | 'mixed' | 'none',
 *   byName, byPosition: counts,
 *   ambiguous: [ref], unmatched: [ref],    components with no geometry found
 *   leftover: [node index],                nodes nobody claimed
 * }
 */
export function mapNodesToRefs(nodes, components, { tol = MATCH_MM, fallbackOffset = null } = {}) {
  const refs = new Set(components.map((c) => c.ref));
  const byRef = new Map();
  const claim = (ref, i) => { if (!byRef.has(ref)) byRef.set(ref, []); byRef.get(ref).push(i); };
  const claimed = new Set();

  // 1. Names.
  nodes.forEach((n, i) => {
    const ref = refFromName(n.name, refs);
    if (ref) { claim(ref, i); claimed.add(i); }
  });
  const byName = byRef.size;

  // 2. The translation between the two frames.
  const aimsOf = (list) => list.map((c) => ({ ref: c.ref, ...toBoardFrame(c) }));
  let offset = null;
  if (byName) {
    const dx = [], dy = [];
    for (const c of components) {
      const idx = byRef.get(c.ref);
      if (!idx) continue;
      const a = toBoardFrame(c);
      // The node nearest its placement among the ref's nodes (a ref may have several models).
      const n = idx.map((i) => nodes[i]).reduce((p, q) => (Math.hypot(q.x - a.x, q.y - a.y) < Math.hypot(p.x - a.x, p.y - a.y) ? q : p));
      dx.push(n.x - a.x); dy.push(n.y - a.y);
    }
    offset = { x: median(dx), y: median(dy) };
  }
  const restComponents = components.filter((c) => !byRef.has(c.ref));
  const restNodes = nodes.map((n, i) => ({ n, i })).filter(({ i }) => !claimed.has(i));
  if (!offset && restNodes.length && restComponents.length) {
    const t = houghTranslation(restNodes.map(({ n }) => n), aimsOf(restComponents));
    // A vote carried by only one or two pairs on a big board is noise; trust it only with support.
    const needed = Math.min(3, restComponents.length, restNodes.length);
    if (t && t.support >= needed) offset = { x: t.x, y: t.y };
  }
  if (!offset) offset = fallbackOffset || { x: 0, y: 0 };

  // 3. Positions, for the components the names did not find.
  let byPosition = 0, ambiguous = [], unmatched = [];
  if (restComponents.length) {
    const targets = restComponents.map((c) => {
      const a = toBoardFrame(c);
      return { ref: c.ref, aim: { x: a.x + offset.x, y: a.y + offset.y } };
    });
    const candidates = restNodes.map(({ n }) => n);
    const r = matchByPosition(candidates, targets, tol);
    for (const [ref, k] of r.matched) { claim(ref, restNodes[k].i); claimed.add(restNodes[k].i); byPosition++; }
    ambiguous = r.ambiguous;
    unmatched = r.unmatched;
  }

  // 4. Extra pieces: a component split over several sibling nodes (body + pins as separate
  //    nodes, or several models on one footprint). An unclaimed node sitting on a matched
  //    component's placement, and nearer it than any other, joins it.
  const placed = components.filter((c) => byRef.has(c.ref)).map((c) => {
    const a = toBoardFrame(c);
    return { ref: c.ref, x: a.x + offset.x, y: a.y + offset.y };
  });
  const leftover = [];
  nodes.forEach((n, i) => {
    if (claimed.has(i)) return;
    let best = null, bestD = tol;
    for (const p of placed) {
      const d = distanceTo(n, p);
      if (d < bestD) { bestD = d; best = p.ref; }
    }
    if (best) { claim(best, i); claimed.add(i); } else leftover.push(i);
  });

  const method = byName && byPosition ? 'mixed' : byName ? 'name' : byPosition ? 'position' : 'none';
  return { byRef, offset, method, byName, byPosition, ambiguous, unmatched, leftover };
}
