"""checks.impedance: controlled-impedance net classes evaluated on base and head (see docs/CONTRACT-project.md).

For every net class with an impedance target (KiCad 10 tuning profile or the class-name convention SE_50_CP,
DP_90_MS…, read by boarddd.io.kicad), on every copper layer its tracks use: the class's tracks grouped by width
(and, for a pair, by the edge-to-edge gap between its parallel segments), each group evaluated on that side's
stackup and weighted by its length. Launch stubs (a short run of another width that ends on a pad), runs too short
to matter and tracks hidden under a wider one of the same net are left out and listed. The row reports the
controlled length out of tolerance, not one width's verdict.

The numbers come from boarddd.impedance: its tier-2 field solver when the ``field`` extra (numpy, scipy) is
installed, with its own error estimate, else the tier-1 closed-form models (about ±2 % of a field solver inside
their validity ranges). A review aid, never a pass/fail: fab tolerances are ±10 % and the stackup data usually
matters more.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict

from kipr.common.sexpr import parse

from .pcb import _fp_transform, _pad_box, item_layers, net_of, pt

DEFAULT_TOLERANCE_PCT = 10.0
SHIFT_PCT = 0.5  # a stackup change that moves Z by less than this is not reported
WIDTH_EPS = 0.0005  # mm
MIN_RUN_MM = 0.5  # a run (connected same-width tracks) shorter than this is electrically irrelevant: left out
LAUNCH_MAX_MM = 3.0  # a run of another width than the class's main one, ending on a pad, up to this long: a launch
BREAKOUT_GAP = 2.0  # a pair section at more than this times the class's main gap, up to LAUNCH_MAX_MM long: a breakout
METHODS = {"field": "field solver (boarddd.impedance tier 2, 2D quasi-static)",
           "closedform": "closed-form estimate (boarddd.impedance tier 1, quasi-static)"}

try:  # boarddd is a dependency, but a broken install must not take the review down
    import boarddd
    from boarddd import impedance as zlib
    from boarddd.io.kicad import read_kicad_pcb
except Exception as _e:  # noqa: BLE001
    zlib = None
    _IMPORT_ERROR = f"boarddd is not available: {_e!r}"
else:
    _IMPORT_ERROR = None


def field_available() -> str | None:
    """None when boarddd's field solver can run (the [field] extra: numpy, scipy), else why not."""
    if zlib is None or not hasattr(zlib, "solve_cross_section"):
        return "this boarddd has no field solver (needs boarddd >= 0.4.0)"
    try:
        import numpy  # noqa: F401
        import scipy.sparse.linalg  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return f'the field solver needs numpy and scipy (pip install "boarddd[field]"): {e}'
    return None


def pick_solver(want: str | None = None) -> tuple[str, str | None]:
    """(solver, note): "field" when available, else "closedform" with the reason. `want` (or the
    KIPR_IMPEDANCE_SOLVER environment variable): auto | field | closedform."""
    want = (want or os.environ.get("KIPR_IMPEDANCE_SOLVER") or "auto").strip().lower()
    if want in ("closedform", "closed-form", "tier1"):
        return "closedform", "closed-form models requested (KIPR_IMPEDANCE_SOLVER)"
    why = field_available()
    if why is None:
        return "field", None
    return "closedform", f"field solver unavailable, fell back to the closed-form models: {why}"


# -- copper geometry from the board text ---------------------------------------------------------------------------
def _root(pcb):
    """The parsed board from its text (or an already parsed root)."""
    return parse(pcb) if isinstance(pcb, str) else pcb


def _nets(root) -> dict:
    nets = {}
    for n in root.children("net"):
        a = n.atoms()
        if len(a) >= 2:
            nets[str(a[0])] = str(a[1])
    return nets


def tracks(pcb_text) -> list[dict]:
    """Track segments and arcs: {net, layer, width, a, b, length} (arcs as their chord pairs via mid)."""
    root = _root(pcb_text)
    nets = _nets(root)
    out = []
    for c in root.children():
        if c.name not in ("segment", "arc"):
            continue
        lay = item_layers(c)
        w = c.num("width", 0.0)
        if not lay or w <= 0:
            continue
        net = net_of(c, nets)
        s, e = pt(c.child("start")), pt(c.child("end"))
        pts = [s, pt(c.child("mid")), e] if c.name == "arc" and c.child("mid") is not None else [s, e]
        for a, b in zip(pts, pts[1:]):
            if math.dist(a, b) < 1e-6:
                continue
            out.append({"net": net, "layer": lay[0], "width": round(w, 4), "a": a, "b": b, "length": math.dist(a, b)})
    return out


def pads(pcb_text) -> list[dict]:
    """Footprint pads with a net: {net, layers (as written, e.g. "*.Cu"), box} in board coordinates."""
    root = _root(pcb_text)
    nets = _nets(root)
    out = []
    for fp in root.children("footprint"):
        at = fp.child("at")
        x, y, rot = ((at.nums() + [0, 0, 0])[:3]) if at is not None else (0, 0, 0)
        tf = _fp_transform(x, y, rot)
        for p in fp.children("pad"):
            net = net_of(p, nets)
            box = _pad_box(p, tf, rot) if net else None
            if box:
                lays = p.child("layers")
                out.append({"net": net, "layers": [str(a) for a in lays.atoms()] if lays is not None else [], "box": box})
    return out


def zones(pcb_text) -> list[dict]:
    """Copper zones: {net, layers, clearance, box} (keepouts dropped)."""
    root = _root(pcb_text)
    nets = _nets(root)
    out = []
    for z in root.children("zone"):
        if z.child("keepout") is not None:
            continue
        cp = z.child("connect_pads")
        clr = cp.num("clearance", 0.0) if cp is not None else 0.0
        pts = []
        for p in z.children("polygon"):
            pts += [tuple(x.nums()[:2]) for x in (p.child("pts").children("xy") if p.child("pts") is not None else [])]
        if not pts:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out.append({"net": net_of(z, nets), "layers": item_layers(z), "clearance": clr,
                    "box": (min(xs), min(ys), max(xs), max(ys))})
    return out


def _dominant(lengths: dict) -> float:
    return max(lengths.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def _pt_seg(p, a, b) -> float:
    """Distance of point p from segment ab."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def covered(segs: list[dict]) -> set[int]:
    """Indices of segments lying entirely inside a wider segment of the same net (a redundant thin track drawn
    over a fat one: the copper is the fat one)."""
    out = set()
    for i, s in enumerate(segs):
        for j, t in enumerate(segs):
            if i == j or t["width"] <= s["width"] + WIDTH_EPS or t["net"] != s["net"]:
                continue
            r = (t["width"] - s["width"]) / 2 + 1e-4
            if _pt_seg(s["a"], t["a"], t["b"]) <= r and _pt_seg(s["b"], t["a"], t["b"]) <= r:
                out.add(i)
                break
    return out


def runs(segs: list[dict]) -> list[list[int]]:
    """Connected chains of same-net, same-width segments (ends within 1 µm), as lists of indices."""
    parent = list(range(len(segs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    at: dict[tuple, int] = {}
    for i, s in enumerate(segs):
        for p in (s["a"], s["b"]):
            k = (s["net"], s["width"], round(p[0], 3), round(p[1], 3))
            if k in at:
                parent[find(i)] = find(at[k])
            else:
                at[k] = i
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(segs)):
        groups[find(i)].append(i)
    return list(groups.values())


def _on_pad(p, net: str, layer: str, pad_list: list[dict]) -> bool:
    x, y = p
    for q in pad_list:
        if q["net"] != net or not any(ly in (layer, "*.Cu") or (ly == "F&B.Cu" and layer in ("F.Cu", "B.Cu"))
                                      for ly in q["layers"]):
            continue
        b = q["box"]
        if b[0] - 1e-3 <= x <= b[2] + 1e-3 and b[1] - 1e-3 <= y <= b[3] + 1e-3:
            return True
    return False


def controlled(segs: list[dict], main_width: float | None, pad_list: list[dict]) -> tuple[list[dict], list[dict]]:
    """(the segments that count, the left-out ones with a reason: covered | launch | short).

    covered: inside a wider track of the same net; launch: a run of another width than `main_width` that ends
    on a pad of its net and is at most LAUNCH_MAX_MM long (a pad/connector launch or neck-down); short: a run
    shorter than MIN_RUN_MM."""
    cov = covered(segs)
    keep_i = [i for i in range(len(segs)) if i not in cov]
    left = [dict(segs[i], reason="covered") for i in sorted(cov)]
    sub = [segs[i] for i in keep_i]
    keep = []
    for run in runs(sub):
        rs = [sub[i] for i in run]
        length = sum(s["length"] for s in rs)
        w = rs[0]["width"]
        reason = None
        if (main_width is not None and abs(w - main_width) > WIDTH_EPS and length <= LAUNCH_MAX_MM
                and any(_on_pad(p, s["net"], s["layer"], pad_list) for s in rs for p in (s["a"], s["b"]))):
            reason = "launch"
        elif length < MIN_RUN_MM:
            reason = "short"
        if reason:
            left += [dict(s, reason=reason) for s in rs]
        else:
            keep += rs
    return keep, left


def pair_matches(segs_p: list[dict], segs_n: list[dict]) -> list[tuple[dict, float | None, float]]:
    """For each segment of one net of a differential pair on one layer: (segment, gap, coupled length) where gap is
    the edge-to-edge distance to the nearest parallel segment of the other net that overlaps it along its direction
    (rounded to 1 µm; None and 0 when uncoupled)."""
    out = []
    for s in segs_p:
        (ax, ay), (bx, by) = s["a"], s["b"]
        L = s["length"]
        if L < 1e-6:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        best = None
        for t in segs_n:
            (cx, cy), (dx, dy) = t["a"], t["b"]
            M = t["length"]
            if M < 1e-6 or abs(ux * (dy - cy) / M - uy * (dx - cx) / M) > 0.02:  # not parallel (> ~1°)
                continue
            # overlap along u
            p0, p1 = sorted(((cx - ax) * ux + (cy - ay) * uy, (dx - ax) * ux + (dy - ay) * uy))
            ov = min(L, p1) - max(0.0, p0)
            if ov <= 1e-3:
                continue
            dist = abs((cx - ax) * -uy + (cy - ay) * ux)  # perpendicular distance of the centre lines
            g = dist - (s["width"] + t["width"]) / 2
            if g > 0 and (best is None or g < best[0]):
                best = (g, ov)
        if best is not None and best[0] < 10 * s["width"] + 1.0:  # coupled, not just the other side of the board
            out.append((s, round(best[0], 3), best[1]))
        else:
            out.append((s, None, 0.0))
    return out


def pair_gap(segs_p: list[dict], segs_n: list[dict]) -> dict:
    """{gap: length coupled} of a differential pair on one layer (see pair_matches)."""
    gaps: dict[float, float] = defaultdict(float)
    for _s, g, ov in pair_matches(segs_p, segs_n):
        if g is not None:
            gaps[g] += ov
    return dict(gaps)


# -- evaluation ----------------------------------------------------------------------------------------------------
class Evaluator:
    """Z of one cross-section with the chosen solver; field solves are cached (same geometry, same answer)."""

    def __init__(self, solver: str):
        self.solver = solver
        self.cache: dict[str, object] = {}
        self.solves = 0

    def __call__(self, stackup, layer: str, opts: dict) -> dict:
        """{model, params, Z, Zcommon, error_pct, Z_closedform, validity, warnings} (raises on bad input)."""
        kind = opts.get("kind", "single")
        key = "Zdiff" if kind == "differential" else "Z0"
        out = {"model": None, "params": None, "Z": None, "Zcommon": None, "error_pct": None, "Z_closedform": None,
               "validity": [], "warnings": []}
        cf_err = None
        try:
            line = zlib.line_from_stackup(stackup, layer, **opts)
            res = zlib.calculate(line.model, line.params)
            out.update(model=line.model, params={k: round(v, 6) for k, v in line.params.items()},
                       Z_closedform=getattr(res, key), validity=[f.message for f in res.flags], warnings=list(line.warnings))
            if kind == "differential":
                out["Zcommon"] = res.Zcommon
        except Exception as e:  # noqa: BLE001  the field solver may still have a model for it
            if self.solver != "field":
                raise
            cf_err = e
        if self.solver != "field":
            out["Z"] = out["Z_closedform"]
            return out
        line = zlib.line_from_stackup(stackup, layer, solver="field", **opts)
        ck = json.dumps(line.section, sort_keys=True)
        res = self.cache.get(ck)
        if res is None:
            res = zlib.solve_cross_section(line.section)
            self.cache[ck] = res
            self.solves += 1
        out["Z"] = getattr(res, key)
        out["Zcommon"] = res.Zcommon if kind == "differential" else None
        out["error_pct"] = res.error_pct
        out["validity"] = []  # the field solver has no validity range; tier 1's flags no longer apply
        if out["params"] is None:  # no tier-1 model: the inputs as line_from_stackup gives them
            out["params"] = {k: round(v, 6) for k, v in line.params.items()}
            out["warnings"] = list(line.warnings) + (["no closed-form model for this structure (field solver only)"]
                                                     if cf_err is not None else [])
        return out


# -- one side --------------------------------------------------------------------------------------------------
def _structure(target_structure, outer: bool, kind: str, has_coplanar_gnd: bool, solver: str = "closedform") -> tuple[str, list[str]]:
    """The structure to evaluate on this layer, and notes when it differs from what the class says."""
    notes = []
    want = target_structure
    field = solver == "field"
    if not outer:
        if want in ("coplanar", "coplanar_grounded") and field and has_coplanar_gnd:
            return "coplanar_grounded", notes  # embedded coplanar: the field solver has it (planes both sides)
        if want not in (None, "stripline"):
            notes.append(f"class says {want}, routed on an inner layer: evaluated as stripline")
        return "stripline", notes
    if want == "stripline":
        notes.append("class says stripline, routed on an outer layer: evaluated as microstrip")
        return "microstrip", notes
    if want in ("coplanar", "coplanar_grounded"):
        if not has_coplanar_gnd:
            notes.append(f"class says {want}, but no copper zone beside the track: evaluated as microstrip")
            return "microstrip", notes
        if kind == "differential" and not field:
            notes.append("differential coplanar has no closed-form model: evaluated as edge-coupled microstrip "
                         "(the coplanar ground lowers Z)")
            return "microstrip", notes
        if want == "coplanar":
            notes.append("coplanar with a plane below: evaluated as grounded CPW")
        return "coplanar_grounded", notes
    return "microstrip", notes


def _side_template(nc_name: str, layer: str) -> dict:
    return {"class": nc_name, "layer": layer, "nets": [], "width": None, "widths": [], "length_mm": 0.0,
            "routed_mm": 0.0, "gap": None, "coplanar_gap": None, "structure": None, "model": None, "solver": None,
            "Z": None, "Zcommon": None, "Z_closedform": None, "error_pct": None, "deviation_pct": None,
            "worst_deviation_pct": None, "within": None, "length_out_mm": None, "segments": [], "excluded": [],
            "params": None, "validity": [], "notes": [], "error": None}


def side_rows(board, pcb_text: str, evaluate: Evaluator | None = None) -> dict:
    """{(class, layer): row side dict} for one revision."""
    evaluate = evaluate or Evaluator("closedform")
    root = _root(pcb_text)
    trk = tracks(root)
    zn = zones(root)
    pad_list = pads(root)
    stackup = board.stackup
    copper = [ly.layer or ly.name for ly in stackup.layers if ly.kind == "copper"]
    by_net_layer: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in trk:
        by_net_layer[(t["net"], t["layer"])].append(t)
    net_class = {n.name: n.net_class for n in board.nets}
    pair_of = {n.name: n.pair for n in board.nets if n.pair}
    out = {}
    for nc in board.net_classes:
        tgt = nc.impedance
        if tgt is None:
            continue
        members = sorted(n for n, c in net_class.items() if c == nc.name)
        layers = sorted({ly for (n, ly) in by_net_layer if n in members},
                        key=lambda ly: copper.index(ly) if ly in copper else 99)
        # the class's main width: the longest-routed one over all its layers, tracks under wider ones left out
        allw: dict[float, float] = defaultdict(float)
        for layer in layers:
            segs = [t for n in members for t in by_net_layer.get((n, layer), [])]
            cov = covered(segs)
            for i, t in enumerate(segs):
                if i not in cov:
                    allw[t["width"]] += t["length"]
        main_width = _dominant(allw) if allw else None
        kind = tgt.kind
        main_gap = None
        if kind == "differential":  # the class's main gap: the longest-coupled one over all its layers
            allg: dict[float, float] = defaultdict(float)
            for layer in layers:
                for n in members:
                    p = pair_of.get(n)
                    if p and n < p and p in members:
                        for g, v in pair_gap(by_net_layer.get((n, layer), []), by_net_layer.get((p, layer), [])).items():
                            allg[g] += v
            main_gap = _dominant(allg) if allg else nc.diff_pair_gap
        tol = tgt.tolerance_pct if tgt.tolerance_pct is not None else DEFAULT_TOLERANCE_PCT
        for layer in layers:
            segs = [t for n in members for t in by_net_layer.get((n, layer), [])]
            row = _side_template(nc.name, layer)
            out[(nc.name, layer)] = row
            row["nets"] = [n for n in members if by_net_layer.get((n, layer))]
            lengths: dict[float, float] = defaultdict(float)
            for t in segs:
                lengths[t["width"]] += t["length"]
            row["widths"] = [{"width": w, "length_mm": round(v, 3)} for w, v in sorted(lengths.items(), key=lambda kv: -kv[1])]
            row["routed_mm"] = round(sum(lengths.values()), 3)
            keep, left = controlled(segs, main_width, pad_list)
            # groups: (width, gap) -> controlled length
            groups: dict[tuple[float, float | None], float] = defaultdict(float)
            if kind == "differential":
                gaps: dict[float, float] = defaultdict(float)
                seen = False
                for n in row["nets"]:
                    p = pair_of.get(n)
                    if not (p and n < p and p in members):
                        continue
                    seen = True
                    sp = [s for s in keep if s["net"] == n and s["layer"] == layer]
                    sn = [s for s in keep if s["net"] == p and s["layer"] == layer]
                    coupled = pair_matches(sp, sn)
                    # a breakout: a short section far wider than the main gap (the pair fanning out to its pads);
                    # a long one is a real gap error and stays
                    breakout = {id(s) for s, g, ov in coupled if g is not None and main_gap
                                and g > BREAKOUT_GAP * main_gap and ov <= LAUNCH_MAX_MM}
                    for s, g, ov in coupled:
                        if g is None:
                            left.append(dict(s, reason="uncoupled"))
                        elif id(s) in breakout:
                            left.append(dict(s, reason="breakout"))
                        else:
                            groups[(s["width"], g)] += ov
                            gaps[g] += ov
                            if s["length"] - ov > 1e-3:
                                left.append(dict(s, length=s["length"] - ov, reason="uncoupled"))
                if gaps:
                    row["gaps"] = [{"gap": g, "length_mm": round(v, 3)} for g, v in sorted(gaps.items(), key=lambda kv: -kv[1])]
                elif nc.diff_pair_gap and keep:
                    row["notes"].append("no coupled segments found: gap from the net class")
                    left = [x for x in left if x["reason"] != "uncoupled"]
                    for s in keep:
                        if s["net"] < (pair_of.get(s["net"]) or "") or not seen:
                            groups[(s["width"], nc.diff_pair_gap)] += s["length"]
            else:
                for s in keep:
                    groups[(s["width"], None)] += s["length"]
            exc: dict[tuple[float, str], float] = defaultdict(float)
            for x in left:
                exc[(x["width"], x["reason"])] += x["length"]
            row["excluded"] = [{"width": w, "reason": r, "length_mm": round(v, 3)}
                               for (w, r), v in sorted(exc.items(), key=lambda kv: -kv[1]) if v >= 1e-3]
            if not groups:
                if kind == "differential" and keep:
                    row["error"] = "differential class without a gap (no coupled segments, no class gap)"
                else:
                    row["notes"].append("no controlled length: every track here is a launch, stub or covered (see excluded)")
                row["width"] = _dominant(lengths) if lengths else None
                continue
            box = None
            if segs:
                xs = [c for t in segs for c in (t["a"][0], t["b"][0])]
                ys = [c for t in segs for c in (t["a"][1], t["b"][1])]
                box = (min(xs), min(ys), max(xs), max(ys))
            beside = [z for z in zn if layer in z["layers"] and z["net"] and z["net"] not in members and box
                      and z["box"][0] <= box[2] and z["box"][2] >= box[0] and z["box"][1] <= box[3] and z["box"][3] >= box[1]]
            outer = copper.index(layer) in (0, len(copper) - 1) if layer in copper else True
            structure, notes = _structure(tgt.structure, outer, kind, bool(beside), evaluate.solver)
            row["notes"] += notes
            row["structure"] = structure
            base_opts = {"kind": kind, "structure": structure}
            for il in tgt.layers or []:
                if il.layer == layer:
                    base_opts.update(ref_top=il.ref_top, ref_bottom=il.ref_bottom)
            if structure == "coplanar_grounded":
                cg = max([nc.clearance or 0.0] + [min(z["clearance"] for z in beside)])
                row["coplanar_gap"] = round(cg, 4)
                base_opts["coplanar_gap"] = cg
            row["solver"] = evaluate.solver
            seg_rows = []
            for (w, g), L in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0][0])):
                opts = dict(base_opts, width=w)
                if g is not None:
                    opts["gap"] = g
                sr = {"width": w, "gap": g, "length_mm": round(L, 3), "Z": None, "Zcommon": None,
                      "deviation_pct": None, "within": None, "error_pct": None, "error": None}
                try:
                    r = evaluate(stackup, layer, opts)
                except Exception as e:  # noqa: BLE001  one bad group must not hide the others
                    sr["error"] = str(e)
                    seg_rows.append((sr, None, opts))
                    continue
                z = r["Z"]
                sr.update(Z=round(z, 3), Zcommon=None if r["Zcommon"] is None else round(r["Zcommon"], 3),
                          deviation_pct=round(100 * (z - tgt.target) / tgt.target, 2),
                          error_pct=None if r["error_pct"] is None else round(r["error_pct"], 3))
                sr["within"] = abs(sr["deviation_pct"]) <= tol
                seg_rows.append((sr, r, opts))
            row["segments"] = [x[0] for x in seg_rows]
            main = next(((sr, r, o) for sr, r, o in seg_rows if r is not None), None)
            row["length_mm"] = round(sum(sr["length_mm"] for sr, r, _o in seg_rows if r is not None), 3)
            if main is None:
                row["error"] = seg_rows[0][0]["error"]
                row["width"], row["gap"] = seg_rows[0][0]["width"], seg_rows[0][0]["gap"]
                continue
            sr, r, o = main
            row["_opts"] = o
            row["width"], row["gap"] = sr["width"], sr["gap"]
            row["model"], row["params"] = r["model"], r["params"]
            row["notes"] += [w for w in r["warnings"] if w not in row["notes"]]
            row["validity"] = r["validity"]
            row["Z"], row["Zcommon"], row["deviation_pct"] = sr["Z"], sr["Zcommon"], sr["deviation_pct"]
            row["Z_closedform"] = None if r["Z_closedform"] is None else round(r["Z_closedform"], 3)
            ok = [x for x, rr, _o in seg_rows if rr is not None]
            errs = [x["error_pct"] for x in ok if x["error_pct"] is not None]
            row["error_pct"] = max(errs) if errs else None
            row["worst_deviation_pct"] = max((x["deviation_pct"] for x in ok), key=abs)
            row["length_out_mm"] = round(sum(x["length_mm"] for x in ok if not x["within"]), 3)
            row["within"] = row["length_out_mm"] == 0
            if any(x["error"] for x in row["segments"]):
                row["notes"].append("some widths could not be evaluated: " + "; ".join(
                    f'{x["width"]} mm: {x["error"]}' for x in row["segments"] if x["error"]))
    return out


def _target(nc) -> dict:
    t = nc.impedance
    return {"kind": t.kind, "target": t.target,
            "tolerance_pct": t.tolerance_pct if t.tolerance_pct is not None else DEFAULT_TOLERANCE_PCT,
            "tolerance_default": t.tolerance_pct is None, "common_mode": t.common_mode,
            "structure": t.structure, "source": t.source}


def stackup_changes(base, head) -> list[dict]:
    """Copper/dielectric/mask layers whose thickness or εr differs (by name)."""
    if base is None or head is None:
        return []
    keep = ("copper", "dielectric", "mask")
    b = {ly.name: ly for ly in base.stackup.layers if ly.kind in keep}
    h = {ly.name: ly for ly in head.stackup.layers if ly.kind in keep}
    out = []
    for name in list(b) + [n for n in h if n not in b]:
        x, y = b.get(name), h.get(name)
        if x is None or y is None:
            out.append({"layer": name, "field": "layer", "base": x is not None, "head": y is not None})
            continue
        for f in ("thickness", "epsilon_r"):
            if getattr(x, f) != getattr(y, f):
                out.append({"layer": name, "field": f, "base": getattr(x, f), "head": getattr(y, f)})
    return out


def check(sides: dict, err=print, solver: str | None = None) -> dict | None:
    """checks.impedance from {"base": (pcb_path, pro_path, pcb_text) | None, "head": …}.

    solver: auto (the field solver when boarddd's [field] extra is installed) | field | closedform; default the
    KIPR_IMPEDANCE_SOLVER environment variable, else auto."""
    if all(v is None for v in sides.values()):
        return None
    if zlib is None:
        err(_IMPORT_ERROR)
        return None
    solver, solver_note = pick_solver(solver)
    evaluate = Evaluator(solver)
    boards, rows, targets = {}, {}, {}
    for name, v in sides.items():
        if v is None:
            continue
        path, pro, text = v
        try:
            b = read_kicad_pcb(path, pro) if pro else read_kicad_pcb(path)
            boards[name] = b
            rows[name] = side_rows(b, text, evaluate)
            for nc in b.net_classes:
                if nc.impedance is not None:
                    targets.setdefault(nc.name, {})[name] = _target(nc)
        except Exception as e:  # noqa: BLE001
            err(f"impedance check ({name}): {e}")
    if not boards:
        return None
    changes = stackup_changes(boards.get("base"), boards.get("head"))
    out_rows = []
    keys = sorted(set(rows.get("base", {})) | set(rows.get("head", {})))
    for key in keys:
        b, h = rows.get("base", {}).get(key), rows.get("head", {}).get(key)
        tgt = targets.get(key[0], {})
        t = tgt.get("head") or tgt.get("base")
        r = {"class": key[0], "layer": key[1], "status": "added" if b is None else "removed" if h is None else "same",
             "target": t, "base": b, "head": h, "flags": [], "severity": "ok", "delta_pct": None, "shift_pct": None}
        if b and h:
            if b.get("Z") is not None and h.get("Z") is not None:
                r["delta_pct"] = round(100 * (h["Z"] - b["Z"]) / b["Z"], 2)
                if abs(r["delta_pct"]) >= 0.05:
                    r["status"] = "changed"
            if abs((h["width"] or 0) - (b["width"] or 0)) > WIDTH_EPS or (
                    b.get("gap") is not None and h.get("gap") is not None and abs(h["gap"] - b["gap"]) > WIDTH_EPS):
                r["flags"].append("width_change")
            if tgt.get("base") and tgt.get("head") and tgt["base"]["target"] != tgt["head"]["target"]:
                r["flags"].append("target_change")
            sp = _stackup_shift(boards, b, h, evaluate)
            if sp is not None:
                r["shift_pct"] = sp
                r["flags"].append("stackup_shift")
        if h and h.get("within") is False and not (b and b.get("within") is False):
            r["flags"].append("new_violation")
        elif h and h.get("within") is False:
            r["flags"].append("violation")
        if b and b.get("within") is False and h and h.get("within") is True:
            r["flags"].append("fixed")
        r["severity"] = ("bad" if "new_violation" in r["flags"] else
                         "warn" if set(r["flags"]) & {"violation", "stackup_shift", "width_change", "target_change"}
                         or (h and (h.get("validity") or h.get("error"))) else "ok")
        out_rows.append(r)
    for r in out_rows:
        for sd in (r["base"], r["head"]):
            if sd:
                sd.pop("_opts", None)
    head_rows = [r for r in out_rows if r["head"]]
    return {
        "method": METHODS[solver],
        "solver": solver,
        "solver_note": solver_note,
        "boarddd": getattr(boarddd, "__version__", None),
        "tolerance_default_pct": DEFAULT_TOLERANCE_PCT,
        "rules": {"min_run_mm": MIN_RUN_MM, "launch_max_mm": LAUNCH_MAX_MM},
        "field_solves": evaluate.solves,
        "classes": sorted(targets),
        "rows": out_rows,
        "stackup_changes": changes,
        "count": {
            "rows": len(head_rows),
            "violations": sum(1 for r in head_rows if r["head"].get("within") is False),
            "length_out_mm": round(sum(r["head"].get("length_out_mm") or 0 for r in head_rows), 3),
            "new_violations": sum(1 for r in out_rows if "new_violation" in r["flags"]),
            "stackup_shifts": sum(1 for r in out_rows if "stackup_shift" in r["flags"]),
            "width_changes": sum(1 for r in out_rows if "width_change" in r["flags"]),
        },
    }


def _stackup_shift(boards, b: dict, h: dict, evaluate: Evaluator) -> float | None:
    """Z on the head stackup at the base geometry vs base Z: what the stackup change alone did (%)."""
    if b.get("Z") is None or not b.get("params") or not h.get("params") or "head" not in boards or not b.get("_opts"):
        return None
    geom = ("w", "s", "gap")
    bs = {k: v for k, v in b["params"].items() if k not in geom}
    hs = {k: v for k, v in h["params"].items() if k not in geom}
    if bs == hs or b["model"] != h["model"] or b["structure"] != h["structure"]:
        return None
    try:
        z = evaluate(boards["head"].stackup, b["layer"], b["_opts"])["Z"]
    except Exception:  # noqa: BLE001
        return None
    pct = round(100 * (z - b["Z"]) / b["Z"], 2)
    return pct if abs(pct) >= SHIFT_PCT else None
