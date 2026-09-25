"""Semantic diff of two parsed boards (see `pcb.load`)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from . import geom
from .pcb import Board, Footprint, Zone

MOVE_EPS = 0.001  # mm
ROT_EPS = 0.01  # degrees

# Order in which a footprint's differences are named when one "what" has to be picked.
FP_WHAT_ORDER = ("footprint", "flipped", "moved", "rotated", "pads", "graphics", "value", "reference",
                 "model", "dnp", "attributes", "fields", "locked", "footprint_library", "model_format")
FP_GEOMETRIC = {"footprint", "flipped", "moved", "rotated", "pads", "graphics"}
# Changes that don't change the assembled board: a 3D model path that only swaps the file format
# (same dir and stem, e.g. .wrl -> .step) and a footprint whose library nickname changed while the
# footprint itself is identical. They are listed with minor: true but not counted as "changed".
MINOR_WHATS = {"model_format", "footprint_library"}
MODEL_EXTS = (".wrl", ".wrz", ".step", ".stp", ".stpz", ".igs", ".iges")


def split_model_ext(path: str) -> tuple[str, str] | None:
    """("dir/stem", ".ext") for a 3D model path with a known model extension, else None."""
    low = path.lower()
    for ext in MODEL_EXTS:
        if low.endswith(ext):
            return path[: -len(ext)], ext
    return None


def model_format_only(bm: list[dict], hm: list[dict]) -> list[tuple[str, str]] | None:
    """[(base ext, head ext)] if the two model lists differ only in file extensions, else None."""
    if len(bm) != len(hm) or bm == hm:
        return None
    swaps = []
    for a, b in zip(bm, hm):
        if {k: v for k, v in a.items() if k != "path"} != {k: v for k, v in b.items() if k != "path"}:
            return None
        if a["path"] == b["path"]:
            continue
        sa, sb = split_model_ext(a["path"]), split_model_ext(b["path"])
        if not sa or not sb or sa[0] != sb[0]:
            return None
        swaps.append((sa[1].lower(), sb[1].lower()))
    return swaps or None


def lib_name(lib_id: str) -> tuple[str, str]:
    nick, _, name = lib_id.rpartition(":")
    return nick, name


def match(base_items, head_items, keys):
    """Pair items across revisions by successive key functions. Returns (pairs, only_base, only_head)."""
    rb, rh = list(base_items), list(head_items)
    pairs = []
    for key in keys:
        idx = defaultdict(list)
        for h in rh:
            k = key(h)
            if k:
                idx[k].append(h)
        left = []
        for b in rb:
            k = key(b)
            cands = idx.get(k) if k else None
            if cands:
                h = cands.pop(0)
                pairs.append((b, h))
            else:
                left.append(b)
        used = {id(h) for _, h in pairs}
        rb, rh = left, [h for h in rh if id(h) not in used]
    return pairs, rb, rh


def copper_span(layers: list[str], copper: list[str]) -> list[str]:
    """Via layers "F.Cu"/"B.Cu" -> every copper layer in between (stack order)."""
    idx = [copper.index(ly) for ly in layers if ly in copper]
    if len(idx) < 2:
        return list(layers)
    lo, hi = min(idx), max(idx)
    return copper[lo:hi + 1]


def cu(f: Footprint) -> str:
    return "B.Cu" if f.side == "bottom" else "F.Cu"


def fp_whats(b: Footprint, h: Footprint) -> tuple[list[str], list[str]]:
    whats, details = [], []
    same_body = b.pads_key == h.pads_key and b.graphics_key == h.graphics_key
    if b.lib_id != h.lib_id:
        if lib_name(b.lib_id)[1] == lib_name(h.lib_id)[1] and b.side == h.side and same_body:
            whats.append("footprint_library")
            details.append(f"footprint library {lib_name(b.lib_id)[0] or '-'} -> {lib_name(h.lib_id)[0] or '-'} (same footprint)")
        else:
            whats.append("footprint")
            details.append(f"footprint {b.lib_id} -> {h.lib_id}")
    if b.side != h.side:
        whats.append("flipped")
        details.append(f"side {b.side} -> {h.side}")
    d = math.hypot(h.x - b.x, h.y - b.y)
    if d > MOVE_EPS:
        whats.append("moved")
        details.append(f"moved {d:.3f} mm ({b.x:g}, {b.y:g}) -> ({h.x:g}, {h.y:g})")
    dr = (h.rot - b.rot + 180) % 360 - 180
    if abs(dr) > ROT_EPS:
        whats.append("rotated")
        details.append(f"rotated {b.rot:g}° -> {h.rot:g}°")
    if lib_name(b.lib_id)[1] == lib_name(h.lib_id)[1] and b.side == h.side:
        if b.pads_key != h.pads_key:
            whats.append("pads")
            details.append("pads changed")
        if b.graphics_key != h.graphics_key:
            whats.append("graphics")
            details.append("footprint graphics changed")
    if b.value != h.value:
        whats.append("value")
        details.append(f"value {b.value} -> {h.value}")
    if b.ref != h.ref:
        whats.append("reference")
        details.append(f"reference {b.ref} -> {h.ref}")
    swaps = model_format_only(b.models, h.models)
    if swaps:
        whats.append("model_format")
        details.append("3D model format " + ", ".join(sorted({f"{x} -> {y}" for x, y in swaps})))
    elif b.models != h.models:
        whats.append("model")
        bm = ", ".join(m["path"] for m in b.models) or "none"
        hm = ", ".join(m["path"] for m in h.models) or "none"
        details.append(f"3D model {bm} -> {hm}" if bm != hm else "3D model offset/scale/rotation changed")
    if b.dnp != h.dnp:
        whats.append("dnp")
        details.append("now DNP" if h.dnp else "no longer DNP")
    ba, ha = [a for a in b.attrs if a != "dnp"], [a for a in h.attrs if a != "dnp"]
    if ba != ha:
        whats.append("attributes")
        details.append(f"attributes {' '.join(ba) or '-'} -> {' '.join(ha) or '-'}")
    fd = field_diff(b.fields, h.fields)
    if fd:
        whats.append("fields")
        details.append("; ".join(fd))
    if b.locked != h.locked:
        whats.append("locked")
        details.append("locked" if h.locked else "unlocked")
    whats.sort(key=FP_WHAT_ORDER.index)
    return whats, details


def field_diff(a: dict, b: dict) -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if k.startswith("ki_"):
            continue
        va, vb = a.get(k), b.get(k)
        # a field that appears or disappears empty is not a change: KiCad upgrades add empty
        # fields such as Sim.Library / Sim.Name to every symbol
        if (va or None) is None and (vb or None) is None:
            continue
        if va != vb:
            if va is None:
                out.append(f"{k} added: {vb!r}")
            elif vb is None:
                out.append(f"{k} removed (was {va!r})")
            else:
                out.append(f"{k} {va!r} -> {vb!r}")
    return out


def _change(kind, what, layers, box, layer=None, **kw):
    c = {"kind": kind, "what": what, "layer": layer or (layers[0] if layers else None),
         "layers": sorted(set(layers)), "bbox_mm": geom.to_xywh(box)}
    c.update({k: v for k, v in kw.items() if v is not None})
    return c


def diff_footprints(base: Board, head: Board):
    pairs, removed, added = match(base.footprints, head.footprints, [
        lambda f: f.uuid, lambda f: f.ref if f.ref and not f.ref.startswith(("REF", "#")) else None,
        lambda f: (f.lib_id, f.x, f.y)])
    changes, components = [], []
    for f in removed:
        changes.append(_change("footprint", "removed", sorted(f.layers), f.box, layer=cu(f), ref=f.ref,
                               detail=f"{f.lib_id} {f.value}", holes=sorted(f.holes) or None,
                               base_bbox_mm=geom.to_xywh(f.box)))
        components.append(component("removed", f, None, []))
    for f in added:
        changes.append(_change("footprint", "added", sorted(f.layers), f.box, layer=cu(f), ref=f.ref,
                               detail=f"{f.lib_id} {f.value}", holes=sorted(f.holes) or None,
                               head_bbox_mm=geom.to_xywh(f.box)))
        components.append(component("added", None, f, []))
    for b, h in pairs:
        whats, details = fp_whats(b, h)
        comp_what = [w for w in ("position", "rotation", "footprint", "value", "model", "side", "dnp",
                                 "footprint_library", "model_format")
                     if {"position": "moved", "rotation": "rotated", "side": "flipped"}.get(w, w) in whats]
        minor = all(w in MINOR_WHATS for w in whats)
        if not whats:
            components.append(component("unchanged", b, h, []))
            continue
        geo = any(w in FP_GEOMETRIC for w in whats)
        layers = sorted(b.layers | h.layers) if geo else []
        if not geo and ("value" in whats or "reference" in whats):
            layers = sorted(ly for ly in (b.layers | h.layers) if ly.endswith(("Fab", "SilkS")))
        holes = sorted(b.holes | h.holes) if geo and (b.holes or h.holes) else None
        changes.append(_change("footprint", whats[0], layers, geom.union(b.box, h.box), layer=cu(h),
                               ref=h.ref or b.ref,
                               whats=whats, detail="; ".join(details), holes=holes, minor=minor or None,
                               base_bbox_mm=geom.to_xywh(b.box), head_bbox_mm=geom.to_xywh(h.box)))
        if "moved" in whats:
            st = "moved"
        elif "rotated" in whats:
            st = "rotated"
        elif comp_what or whats:
            st = "changed"
        c = component(st, b, h, comp_what or whats)
        if minor:
            c["minor"] = True
        components.append(c)
    components.sort(key=lambda c: natural_key(c["ref"]))
    return changes, components


def minor_groups(changes: list[dict]) -> list[dict]:
    """Minor footprint changes grouped by what they are: [{"what", "detail", "count", "refs"}],
    e.g. {"what": "model_format", "detail": "3D model format .wrl -> .step", "count": 112, ...}."""
    groups: dict = {}
    for c in changes:
        if c.get("kind") == "footprint" and c.get("minor"):
            key = (tuple(c.get("whats") or [c["what"]]), c.get("detail") or "")
            groups.setdefault(key, []).append(c.get("ref") or "?")
    out = [{"what": "+".join(k[0]), "detail": k[1], "count": len(refs), "refs": sorted(refs, key=natural_key)}
           for k, refs in groups.items()]
    return sorted(out, key=lambda g: -g["count"])


def natural_key(s: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s or "")]


def fp_side(f: Footprint | None):
    if f is None:
        return None
    return {"x": f.x, "y": f.y, "rot": f.rot, "side": f.side, "footprint": f.lib_id, "value": f.value,
            "model": f.models[0]["path"] if f.models else None, "models": f.models, "dnp": f.dnp,
            "bbox_mm": geom.to_xywh(f.box), "uuid": f.uuid}


def component(status, b: Footprint | None, h: Footprint | None, what):
    return {"ref": (h or b).ref, "status": status, "base": fp_side(b), "head": fp_side(h), "what": list(what)}


def _counter_diff(base_items, head_items):
    cb, ch = Counter(i.key for i in base_items), Counter(i.key for i in head_items)
    removed, added = [], []
    rem, add = cb - ch, ch - cb
    for i in base_items:
        if rem.get(i.key, 0) > 0:
            rem[i.key] -= 1
            removed.append(i)
    for i in head_items:
        if add.get(i.key, 0) > 0:
            add[i.key] -= 1
            added.append(i)
    return removed, added


def diff_items(base: Board, head: Board, gap: float = 1.0):
    """Tracks/vias clustered per (layer, net); graphics/outline/text clustered per layer."""
    changes = []
    copper = head.copper or base.copper
    for kinds in (("track", "via"), ("graphic",), ("outline",), ("text",)):
        bi = [i for i in base.items if i.kind in kinds]
        hi = [i for i in head.items if i.kind in kinds]
        removed, added = _counter_diff(bi, hi)
        groups = defaultdict(list)
        for side, lst in (("removed", removed), ("added", added)):
            for it in lst:
                if kinds[0] == "track":
                    lays = [it.layers[0]] if it.kind == "track" else ["via:" + "/".join(it.layers)]
                    for ly in lays:
                        groups[(ly, it.net)].append((side, it))
                else:
                    groups[(it.layers[0] if it.layers else "", "")].append((side, it))
        for (layer, net), members in sorted(groups.items()):
            boxed = [(it.box or [0, 0, 0, 0], (side, it)) for side, it in members]
            for box, grp in geom.cluster(boxed, gap):
                changes.append(_item_change(kinds[0], layer, net, box, grp, copper))
    return changes


def _item_change(kind, layer, net, box, grp, copper):
    n_add = sum(1 for s, _ in grp if s == "added")
    n_rem = len(grp) - n_add
    what = "added" if not n_rem else "removed" if not n_add else "modified"
    first = grp[0][1]
    if layer.startswith("via:"):
        kind = "via"
        via_layers = layer[4:].split("/")
        layers = copper_span(via_layers, copper)
        layer = via_layers[0]
    elif kind == "track" and all(it.kind == "via" for _, it in grp):
        kind, layers = "via", first.layers
    else:
        layers = [layer]
    if kind == "track":
        dl = sum(it.length if s == "added" else -it.length for s, it in grp)
        detail = f"+{n_add}/-{n_rem} segments, length {dl:+.2f} mm"
        if what == "modified":
            what = "rerouted"
    elif kind == "via":
        detail = f"+{n_add}/-{n_rem} vias"
    elif kind == "text":
        texts = sorted({it.text for _, it in grp})
        if n_add and n_rem and len(grp) == 2:
            old = next(it.text for s, it in grp if s == "removed")
            new = next(it.text for s, it in grp if s == "added")
            detail = f"{old!r} -> {new!r}" if old != new else "moved/restyled"
        else:
            detail = "; ".join(repr(t) for t in texts[:5])
    else:
        detail = f"+{n_add}/-{n_rem} shapes"
    c = _change(kind, what, layers, box, net=net or None, detail=detail, count={"added": n_add, "removed": n_rem})
    if kind == "via":
        c["holes"] = ["PTH"]
    return c


def diff_zones(base: Board, head: Board):
    changes = []
    pairs, removed, added = match(base.zones, head.zones, [
        lambda z: z.uuid, lambda z: (z.net, tuple(z.layers), z.name, z.keepout, z.outline),
        lambda z: (z.net, tuple(z.layers), z.name, z.keepout)])

    def label(z: Zone):
        return ("rule area" if z.keepout else "zone") + (f" '{z.name}'" if z.name else "") + (
            f" {z.net}" if z.net else "")

    for z in removed:
        changes.append(_change("zone", "removed", z.layers, z.box, net=z.net or None, detail=label(z)))
    for z in added:
        changes.append(_change("zone", "added", z.layers, z.box, net=z.net or None, detail=label(z)))
    for b, h in pairs:
        whats = []
        if b.outline != h.outline:
            whats.append("outline")
        if b.layers != h.layers:
            whats.append("layers")
        if b.net != h.net:
            whats.append("net")
        if b.settings != h.settings or b.name != h.name:
            whats.append("settings")
        if b.fill != h.fill:
            whats.append("fill")
        if not whats:
            continue
        detail = label(h) + ": " + ", ".join(whats)
        if "net" in whats:
            detail += f" (net {b.net} -> {h.net})"
        changes.append(_change("zone", whats[0], sorted(set(b.layers) | set(h.layers)), geom.union(b.box, h.box),
                               net=h.net or None, whats=whats, detail=detail))
    return changes


def diff_setup(base: Board, head: Board):
    changes = []
    keys = sorted(set(base.setup_key) | set(head.setup_key))
    diffs = [k for k in keys if base.setup_key.get(k) != head.setup_key.get(k)]
    if "stackup" in diffs:
        det = []
        for k in sorted(set(base.stackup) | set(head.stackup)):
            if base.stackup.get(k) != head.stackup.get(k):
                det.append(f"{k}: {base.stackup.get(k)} -> {head.stackup.get(k)}")
        changes.append({"kind": "board", "what": "stackup", "layer": None, "layers": [], "bbox_mm": None,
                        "detail": "; ".join(det)[:1000]})
        diffs.remove("stackup")
    if diffs:
        changes.append({"kind": "board", "what": "setup", "layer": None, "layers": [], "bbox_mm": None,
                        "detail": "board setup changed: " + ", ".join(diffs)})
    if base.thickness != head.thickness:
        changes.append({"kind": "board", "what": "thickness", "layer": None, "layers": [], "bbox_mm": None,
                        "detail": f"thickness {base.thickness} -> {head.thickness} mm"})
    bl = [(ly["name"], ly["type"]) for ly in base.layers]
    hl = [(ly["name"], ly["type"]) for ly in head.layers]
    if bl != hl:
        changes.append({"kind": "board", "what": "layers", "layer": None, "layers": [], "bbox_mm": None,
                        "detail": f"layer stack changed ({len(base.copper)} -> {len(head.copper)} copper layers)"})
    return changes


def diff_boards(base: Board, head: Board):
    """-> (changes, components). Changes are sorted footprint-first, then by kind/layer."""
    fp_changes, components = diff_footprints(base, head)
    changes = fp_changes + diff_items(base, head) + diff_zones(base, head) + diff_setup(base, head)
    return changes, components


def touched_layers(changes) -> set[str]:
    out = set()
    for c in changes:
        out.update(c.get("layers") or [])
        out.update(c.get("holes") or [])
    return out
