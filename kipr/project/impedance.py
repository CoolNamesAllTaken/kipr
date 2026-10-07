"""checks.impedance: controlled-impedance net classes evaluated on base and head (see docs/CONTRACT-project.md).

For every net class with an impedance target (KiCad 10 tuning profile or the class-name convention SE_50_CP,
DP_90_MS…, read by boarddd.io.kicad), on every copper layer its tracks use: the track width actually used there
(the longest total length wins), the pair's edge-to-edge gap measured between its parallel segments, the coplanar
gap (zone clearance), and the impedance from boarddd.impedance's closed-form models on that side's stackup.
A review aid, never a pass/fail: the numbers are quasi-static closed-form estimates (±2 % of a field solver at
best, and fab tolerances are ±10 %).
"""

from __future__ import annotations

import math
from collections import defaultdict

from kipr.common.sexpr import parse

from .pcb import item_layers, net_of, pt

DEFAULT_TOLERANCE_PCT = 10.0
SHIFT_PCT = 0.5  # a stackup change that moves Z by less than this is not reported
WIDTH_EPS = 0.0005  # mm
METHOD = "closed-form estimate (boarddd.impedance tier 1, quasi-static)"

try:  # boarddd is a dependency, but a broken install must not take the review down
    import boarddd
    from boarddd import impedance as zlib
    from boarddd.io.kicad import read_kicad_pcb
except Exception as _e:  # noqa: BLE001
    zlib = None
    _IMPORT_ERROR = f"boarddd is not available: {_e!r}"
else:
    _IMPORT_ERROR = None


# -- copper geometry from the board text ---------------------------------------------------------------------------
def tracks(pcb_text: str) -> list[dict]:
    """Track segments and arcs: {net, layer, width, a, b, length} (arcs as their chord pairs via mid)."""
    root = parse(pcb_text)
    nets = {}
    for n in root.children("net"):
        a = n.atoms()
        if len(a) >= 2:
            nets[str(a[0])] = str(a[1])
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
            out.append({"net": net, "layer": lay[0], "width": round(w, 4), "a": a, "b": b, "length": math.dist(a, b)})
    return out


def zones(pcb_text: str) -> list[dict]:
    """Copper zones: {net, layers, clearance, box} (keepouts dropped)."""
    root = parse(pcb_text)
    nets = {}
    for n in root.children("net"):
        a = n.atoms()
        if len(a) >= 2:
            nets[str(a[0])] = str(a[1])
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


def pair_gap(segs_p: list[dict], segs_n: list[dict]) -> dict:
    """Edge-to-edge gap of a differential pair on one layer: for each segment of one net, the nearest parallel
    segment of the other that overlaps it along its direction; {gap: length coupled} rounded to 1 µm."""
    gaps: dict[float, float] = defaultdict(float)
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
            gaps[round(best[0], 3)] += best[1]
    return dict(gaps)


# -- one side --------------------------------------------------------------------------------------------------
def _structure(target_structure, outer: bool, kind: str, has_coplanar_gnd: bool) -> tuple[str, list[str]]:
    """The structure to evaluate on this layer, and notes when it differs from what the class says."""
    notes = []
    want = target_structure
    if not outer:
        if want not in (None, "stripline"):
            notes.append(f"class says {want}, routed on an inner layer: evaluated as stripline")
        return "stripline", notes
    if want == "stripline":
        notes.append("class says stripline, routed on an outer layer: evaluated as microstrip")
        return "microstrip", notes
    if want in ("coplanar", "coplanar_grounded"):
        if kind == "differential":
            notes.append("differential coplanar has no closed-form model: evaluated as edge-coupled microstrip "
                         "(the coplanar ground lowers Z)")
            return "microstrip", notes
        if not has_coplanar_gnd:
            notes.append(f"class says {want}, but no copper zone beside the track: evaluated as microstrip")
            return "microstrip", notes
        if want == "coplanar":
            notes.append("coplanar with a plane below: evaluated as grounded CPW")
        return "coplanar_grounded", notes
    return "microstrip", notes


def side_rows(board, pcb_text: str) -> dict:
    """{(class, layer): row side dict} for one revision."""
    trk = tracks(pcb_text)
    zn = zones(pcb_text)
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
        for layer in layers:
            segs = [t for n in members for t in by_net_layer.get((n, layer), [])]
            lengths: dict[float, float] = defaultdict(float)
            for t in segs:
                lengths[t["width"]] += t["length"]
            width = _dominant(lengths)
            row = {"class": nc.name, "layer": layer, "nets": [n for n in members if by_net_layer.get((n, layer))],
                   "width": width, "widths": [{"width": w, "length_mm": round(v, 3)} for w, v in
                                               sorted(lengths.items(), key=lambda kv: -kv[1])],
                   "length_mm": round(sum(lengths.values()), 3), "gap": None, "coplanar_gap": None,
                   "structure": None, "model": None, "Z": None, "Zcommon": None, "deviation_pct": None,
                   "within": None, "params": None, "validity": [], "notes": [], "error": None}
            out[(nc.name, layer)] = row
            outer = copper.index(layer) in (0, len(copper) - 1) if layer in copper else True
            kind = tgt.kind
            gap = None
            if kind == "differential":
                gaps: dict[float, float] = defaultdict(float)
                for n in row["nets"]:
                    p = pair_of.get(n)
                    if p and n < p and p in members:
                        for g, v in pair_gap(by_net_layer.get((n, layer), []), by_net_layer.get((p, layer), [])).items():
                            gaps[g] += v
                if gaps:
                    gap = _dominant(gaps)
                    row["gaps"] = [{"gap": g, "length_mm": round(v, 3)} for g, v in sorted(gaps.items(), key=lambda kv: -kv[1])]
                elif nc.diff_pair_gap:
                    gap = nc.diff_pair_gap
                    row["notes"].append("no coupled segments found: gap from the net class")
                row["gap"] = gap
            box = None
            if segs:
                xs = [c for t in segs for c in (t["a"][0], t["b"][0])]
                ys = [c for t in segs for c in (t["a"][1], t["b"][1])]
                box = (min(xs), min(ys), max(xs), max(ys))
            beside = [z for z in zn if layer in z["layers"] and z["net"] and z["net"] not in members and box
                      and z["box"][0] <= box[2] and z["box"][2] >= box[0] and z["box"][1] <= box[3] and z["box"][3] >= box[1]]
            structure, notes = _structure(tgt.structure, outer, kind, bool(beside))
            row["notes"] += notes
            row["structure"] = structure
            opts = {"width": width, "kind": kind, "structure": structure}
            for il in tgt.layers or []:
                if il.layer == layer:
                    opts.update(ref_top=il.ref_top, ref_bottom=il.ref_bottom)
            if structure == "coplanar_grounded":
                cg = max([nc.clearance or 0.0] + [min(z["clearance"] for z in beside)])
                row["coplanar_gap"] = round(cg, 4)
                opts["coplanar_gap"] = cg
            if kind == "differential":
                if gap is None:
                    row["error"] = "differential class without a gap (no coupled segments, no class gap)"
                    continue
                opts["gap"] = gap
            try:
                line = zlib.line_from_stackup(stackup, layer, **opts)
                res = zlib.calculate(line.model, line.params)
            except Exception as e:  # noqa: BLE001  one bad layer must not hide the others
                row["error"] = str(e)
                continue
            row["model"], row["params"] = line.model, {k: round(v, 6) for k, v in line.params.items()}
            row["notes"] += line.warnings
            row["validity"] = [f.message for f in res.flags]
            z = res.Zdiff if kind == "differential" else res.Z0
            row["Z"] = round(z, 3)
            if kind == "differential":
                row["Zcommon"] = round(res.Zcommon, 3)
            row["deviation_pct"] = round(100 * (z - tgt.target) / tgt.target, 2)
            tol = tgt.tolerance_pct if tgt.tolerance_pct is not None else DEFAULT_TOLERANCE_PCT
            row["within"] = abs(row["deviation_pct"]) <= tol
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


def check(sides: dict, err=print) -> dict | None:
    """checks.impedance from {"base": (pcb_path, pro_path, pcb_text) | None, "head": …}."""
    if all(v is None for v in sides.values()):
        return None
    if zlib is None:
        err(_IMPORT_ERROR)
        return None
    boards, rows, targets = {}, {}, {}
    for name, v in sides.items():
        if v is None:
            continue
        path, pro, text = v
        try:
            b = read_kicad_pcb(path, pro) if pro else read_kicad_pcb(path)
            boards[name] = b
            rows[name] = side_rows(b, text)
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
            sp = _stackup_shift(boards, b, h)
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
    head_rows = [r for r in out_rows if r["head"]]
    return {
        "method": METHOD,
        "boarddd": getattr(boarddd, "__version__", None),
        "tolerance_default_pct": DEFAULT_TOLERANCE_PCT,
        "classes": sorted(targets),
        "rows": out_rows,
        "stackup_changes": changes,
        "count": {
            "rows": len(head_rows),
            "violations": sum(1 for r in head_rows if r["head"].get("within") is False),
            "new_violations": sum(1 for r in out_rows if "new_violation" in r["flags"]),
            "stackup_shifts": sum(1 for r in out_rows if "stackup_shift" in r["flags"]),
            "width_changes": sum(1 for r in out_rows if "width_change" in r["flags"]),
        },
    }


def _stackup_shift(boards, b: dict, h: dict) -> float | None:
    """Z on the head stackup at the base geometry vs base Z: what the stackup change alone did (%)."""
    if b.get("Z") is None or not b.get("params") or not h.get("params") or "head" not in boards:
        return None
    geom = ("w", "s", "gap")
    bs = {k: v for k, v in b["params"].items() if k not in geom}
    hs = {k: v for k, v in h["params"].items() if k not in geom}
    if bs == hs or b["model"] != h["model"]:
        return None
    try:
        p = {**h["params"], **{k: b["params"][k] for k in geom if k in b["params"]}}
        res = zlib.calculate(h["model"], p)
    except Exception:  # noqa: BLE001
        return None
    z = getattr(res, "Zdiff", None)
    z = res.Z0 if z is None else z
    pct = round(100 * (z - b["Z"]) / b["Z"], 2)
    return pct if abs(pct) >= SHIFT_PCT else None
