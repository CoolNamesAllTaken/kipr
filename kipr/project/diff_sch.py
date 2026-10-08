"""Semantic schematic diff: per sheet instance, symbols / wiring / labels / text / graphics / sub-sheets.

Changes that only move things (classify.is_move_only) get `move_only: true` when the connections are
unchanged. Connections come from each sheet's local connectivity (sch.Connectivity) with groups named
by the netlist (kicad-cli's, when there is one): a symbol keeps every pin on a net of the same name; a
wire, junction, no-connect flag or label belongs to a net whose anchors (pins, labels, power symbols,
sheet pins) keep that net's name, and every anchor it touches directly exists on both sides; a sheet
box keeps its size, pins and fields and each sheet pin's net.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from . import classify, geom, sch
from .diff_pcb import match
from .sch import Connectivity, SchematicSet, Sheet, Symbol, is_pin_anchor

SYM_WHAT_ORDER = ("symbol", "reference", "value", "footprint", "fields", "dnp", "in_bom", "on_board",
                  "exclude_from_sim", "moved", "rotated", "mirrored", "unit", "library", "fields_minor")


def sym_whats(b: Symbol, h: Symbol):
    whats, det = [], []

    def add(w, d):
        whats.append(w)
        det.append(d)

    if b.lib_id != h.lib_id:
        add("symbol", f"symbol {b.lib_id} -> {h.lib_id}")
    if b.ref != h.ref:
        add("reference", f"reference {b.ref} -> {h.ref}")
    if b.value != h.value:
        add("value", f"value {b.value} -> {h.value}")
    if b.footprint != h.footprint:
        add("footprint", f"footprint {b.footprint or '-'} -> {h.footprint or '-'}")
    fd = classify.field_diff({**b.fields, "Datasheet": b.datasheet, "Description": b.description},
                             {**h.fields, "Datasheet": h.datasheet, "Description": h.description})
    if fd.significant:
        add("fields", "; ".join(fd.significant))
    if fd.minor:
        add("fields_minor", "minor: " + "; ".join(fd.minor))
    for flag in ("dnp", "in_bom", "on_board", "exclude_from_sim"):
        if getattr(b, flag) != getattr(h, flag):
            add(flag, f"{flag} {'yes' if getattr(b, flag) else 'no'} -> {'yes' if getattr(h, flag) else 'no'}")
    d = math.hypot(h.x - b.x, h.y - b.y)
    if d > 0.001:
        add("moved", f"moved ({b.x:g}, {b.y:g}) -> ({h.x:g}, {h.y:g})")
    if b.rot != h.rot:
        add("rotated", f"rotated {b.rot:g}° -> {h.rot:g}°")
    if b.mirror != h.mirror:
        add("mirrored", f"mirror {b.mirror or 'none'} -> {h.mirror or 'none'}")
    if b.unit != h.unit:
        add("unit", f"unit {b.unit} -> {h.unit}")
    if b.lib_id == h.lib_id and b.lib_key != h.lib_key:
        add("library", "library symbol (graphics/pins) changed")
    order = sorted(range(len(whats)), key=lambda i: SYM_WHAT_ORDER.index(whats[i]))
    return [whats[i] for i in order], [det[i] for i in order]


MAX_PARTS = 400  # parts_mm of a rerouted cluster
_AT = re.compile(r"\(at [^()]*\)")  # a text's position (a move changes nothing else)


class _Nets:
    """One side of a sheet: its local connectivity, groups named by the netlist ({"REF.PIN": net})."""

    def __init__(self, sheet: Sheet, pin_net: dict | None):
        self.c = Connectivity(sheet)
        self.pin_net = pin_net or {}
        self._id: dict = {}

    def ident(self, g):
        """(net name, connected to anything else) of a group."""
        if g not in self._id:
            anchors = self.c.anchors.get(g, set())
            pins = sorted(a for a in anchors if is_pin_anchor(a))
            names = sorted({self.pin_net[a] for a in pins if a in self.pin_net})
            if names:
                name = names[0]
            else:
                named = sorted((a for a in anchors if not is_pin_anchor(a)), key=_name_rank)
                name = named[0] if named else "~" + ",".join(pins)
            self._id[g] = (name, len(anchors) > 1)
        return self._id[g]


def _name_rank(a):
    return next(i for i, p in enumerate(sch.NAMED_ANCHORS) if a.startswith(p)), a


def _same_net(side: _Nets, other: _Nets, g, direct=()) -> bool:
    """Group g of `side` is the same net in `other`: every anchor of g that `other` has is in a group of
    the same name there, and the anchors in `direct` (touched by the changed element) all exist there."""
    anchors = side.c.anchors.get(g)
    if not anchors:
        return False  # wiring that connects nothing
    me = side.ident(g)
    for a in anchors:
        go = other.c.anchor_group.get(a)
        if go is None:
            if a in direct:
                return False
            continue
        if other.ident(go) != me:
            return False
    return True


def _el_same(side: _Nets, other: _Nets, e) -> bool:
    g = side.c.group_of(e)
    return g is not None and _same_net(side, other, g, side.c.direct(e))


def _pins_same(nb: _Nets, nh: _Nets, bs: Symbol, hs: Symbol) -> bool:
    pb, ph = nb.c.pin_groups(bs), nh.c.pin_groups(hs)
    return set(pb) == set(ph) and all(nb.ident(pb[n]) == nh.ident(ph[n]) for n in pb)


def _chg(kind, what, box, **kw):
    c = {"kind": kind, "what": what, "bbox_mm": geom.to_xywh(box, 0.5)}
    c.update({k: v for k, v in kw.items() if v not in (None, [], "")})
    return c


def _multiset(base_els, head_els):
    cb, ch = Counter(e.key for e in base_els), Counter(e.key for e in head_els)
    rem, add = cb - ch, ch - cb
    removed, added = [], []
    for e in base_els:
        if rem.get(e.key, 0) > 0:
            rem[e.key] -= 1
            removed.append(e)
    for e in head_els:
        if add.get(e.key, 0) > 0:
            add[e.key] -= 1
            added.append(e)
    return removed, added


def diff_sheet(b: Sheet | None, h: Sheet | None, pin_nets: tuple[dict | None, dict | None] = (None, None)) -> list[dict]:
    """Changes between two instances of the same sheet id (either may be None). `pin_nets`: base and
    head {"REF.PIN": net name} from the netlists, to name the local nets."""
    if b is None or h is None:
        s = h or b
        what = "added" if b is None else "removed"
        return [_chg("symbol", what, sym.box, ref=sym.ref, detail=f"{sym.lib_id} {sym.value}")
                for sym in s.symbols if not sym.power]
    changes = []
    nb, nh = _Nets(b, pin_nets[0]), _Nets(h, pin_nets[1])
    # symbols
    pairs, removed, added = match(b.symbols, h.symbols, [
        lambda s: s.uuid,
        lambda s: s.ref if s.ref and not s.ref.startswith("#") and not s.ref.endswith("?") else None,
        lambda s: (s.lib_id, s.x, s.y)])
    for s in removed:
        changes.append(_chg("symbol", "removed", s.box, ref=s.ref, detail=f"{s.lib_id} {s.value}",
                            power=s.power or None))
    for s in added:
        changes.append(_chg("symbol", "added", s.box, ref=s.ref, detail=f"{s.lib_id} {s.value}",
                            power=s.power or None))
    for bs, hs in pairs:
        whats, det = sym_whats(bs, hs)
        if whats:
            moved = classify.is_move_only(whats, _pins_same(nb, nh, bs, hs))
            c = _chg("symbol", whats[0], geom.union(bs.box, hs.box), ref=hs.ref, whats=whats,
                     detail="; ".join(det), power=hs.power or None, minor=classify.is_minor(whats) or None,
                     move_only=moved or None)
            for w, a, z in (("value", bs.value, hs.value), ("footprint", bs.footprint, hs.footprint),
                            ("reference", bs.ref, hs.ref)):
                if whats[0] == w:
                    c["base"], c["head"] = a, z
            if "moved" in whats:
                c["base_bbox_mm"], c["head_bbox_mm"] = geom.to_xywh(bs.box, 0.5), geom.to_xywh(hs.box, 0.5)
            if moved:  # what to fade: body and field texts, old and new place
                c["parts_mm"] = [geom.to_xywh(geom.union(x.box, x.text_box), 0.5) for x in (bs, hs)]
            changes.append(c)
    # wiring: clustered
    # (rerouted wiring that keeps every connection is clustered apart and marked move_only)
    for kind, gap in (("wire", 1.3), ("graphic", 1.0)):
        rem, add = _multiset([e for e in b.elements if e.kind == kind], [e for e in h.elements if e.kind == kind])
        same = set()  # ids of the removed / added items whose connections are the same on the other side
        if kind == "wire":
            flags = {side: Counter(frozenset(side.c.direct(e)) for e in sh.elements if e.sub == "no_connect")
                     for side, sh in ((nb, b), (nh, h))}
            for e, me, other in [(e, nb, nh) for e in rem] + [(e, nh, nb) for e in add]:
                ok = e.sub in ("wire", "junction", "no_connect") and _el_same(me, other, e)
                if ok and e.sub == "no_connect":  # the flag stays on the same pins
                    pins = frozenset(me.c.direct(e))
                    ok = bool(pins) and flags[other][pins] > 0
                if ok:
                    same.add(id(e))
        for quiet in (False, True):
            items = [(e.box or [0, 0, 0, 0], ("removed", e)) for e in rem if (id(e) in same) == quiet] + \
                    [(e.box or [0, 0, 0, 0], ("added", e)) for e in add if (id(e) in same) == quiet]
            for box, grp in geom.cluster(items, gap):
                na = sum(1 for s, _ in grp if s == "added")
                nr = len(grp) - na
                what = "added" if not nr else "removed" if not na else "modified"
                subs = Counter(f"{s}:{e.sub}" for s, e in grp)
                detail = ", ".join(f"{'+' if k.startswith('added') else '-'}{v} {k.split(':')[1]}"
                                   for k, v in sorted(subs.items()))
                parts = None
                if quiet:
                    what, detail = "rerouted", f"{detail} (same connections)"
                    if len(grp) <= MAX_PARTS:  # the items themselves (the cluster's box spans much more)
                        parts = [geom.to_xywh(e.box, 0.5) for _, e in grp if e.box]
                changes.append(_chg(kind, what, box, detail=detail, count={"added": na, "removed": nr},
                                    move_only=classify.is_move_only(["rerouted"], True) if quiet else None,
                                    parts_mm=parts))
    # labels and texts: pair removed/added by position (moved text) or by text (moved label)
    for kind in ("label", "text"):
        rem, add = _multiset([e for e in b.elements if e.kind == kind], [e for e in h.elements if e.kind == kind])
        pairs, rem, add = match(rem, add, [
            lambda e: (e.sub, e.pos) if e.pos is not None else None,
            lambda e: (e.sub, e.text) if e.text else None,
            lambda e: (e.sub, tuple(round(v, 1) for v in e.box)) if e.box else None])
        for e in rem:
            changes.append(_chg(kind, "removed", e.box, detail=f"{e.sub} {e.text!r}"[:300], text=e.text[:300]))
        for e in add:
            changes.append(_chg(kind, "added", e.box, detail=f"{e.sub} {e.text!r}"[:300], text=e.text[:300]))
        for eb, eh in pairs:
            if eb.text != eh.text:
                what = "renamed" if kind == "label" else "edited"
                det = f"{eh.sub} {eb.text!r} -> {eh.text!r}"
            elif eb.box != eh.box and (eb.pos != eh.pos if kind == "label" else _AT.sub("", eb.key) == _AT.sub("", eh.key)):
                what, det = "moved", f"{eh.sub} {eh.text!r} moved"
            else:
                what, det = "modified", f"{eh.sub} {eh.text!r} style/properties changed"
            # a text has no connections; a label must stay on the same net
            conn = kind == "text" or (_el_same(nb, nh, eb) and _el_same(nh, nb, eh))
            moved = what == "moved" and classify.is_move_only([what], conn)
            c = _chg(kind, what, geom.union(eb.box, eh.box), detail=det[:300], base=eb.text[:300],
                     head=eh.text[:300], move_only=moved or None)
            if what == "moved":
                c["base_bbox_mm"], c["head_bbox_mm"] = geom.to_xywh(eb.box, 0.5), geom.to_xywh(eh.box, 0.5)
            changes.append(c)
    # sub-sheet boxes
    pairs, removed, added = match(b.subsheets, h.subsheets, [lambda s: s.uuid, lambda s: s.name])
    for s in removed:
        changes.append(_chg("sheet", "removed", s.box, detail=f"sheet {s.name} ({s.file})"))
    for s in added:
        changes.append(_chg("sheet", "added", s.box, detail=f"sheet {s.name} ({s.file})"))
    for bs, hs in pairs:
        if bs.key != hs.key or bs.name != hs.name:
            det = []
            if bs.name != hs.name:
                det.append(f"renamed {bs.name} -> {hs.name}")
            if bs.file != hs.file:
                det.append(f"file {bs.file} -> {hs.file}")
            if bs.box != hs.box:
                det.append("moved/resized")
            if not det:
                det.append("pins/fields changed")
            pb, ph = nb.c.sheet_pin_groups(bs), nh.c.sheet_pin_groups(hs)
            moved = bs.name == hs.name and bs.file == hs.file and bs.shape_key == hs.shape_key and \
                set(pb) == set(ph) and all(nb.ident(pb[n]) == nh.ident(ph[n]) for n in pb)
            c = _chg("sheet", "moved" if moved else "modified", geom.union(bs.box, hs.box),
                     detail=f"sheet {hs.name}: " + ("moved" if moved else "; ".join(det)),
                     move_only=classify.is_move_only(["moved"], moved) or None)
            if moved:
                c["base_bbox_mm"], c["head_bbox_mm"] = geom.to_xywh(bs.box, 0.5), geom.to_xywh(hs.box, 0.5)
            changes.append(c)
    if not changes and b.digest != h.digest:
        # the file differs but nothing semantic does (hidden fields, field positions, upgrade artefacts)
        changes.append({"kind": "other", "what": "modified", "bbox_mm": None, "minor": True,
                        "detail": "other changes (properties, title block, positions of fields, ...)"})
    return changes


def pin_net_map(nets: dict | None) -> dict | None:
    """{net: {"REF.PIN", ...}} -> {"REF.PIN": net}."""
    return {p: n for n, pins in nets.items() for p in pins} if nets else None


def diff_schematics(base: SchematicSet | None, head: SchematicSet | None, nets: dict | None = None):
    """-> list of sheet dicts (without SVG paths), in head order then removed sheets. `nets`: the
    netlists {"base": {net: pins}, "head": ...} (kicad-cli's), to name local nets; without them nets
    are named by their labels and power symbols."""
    pin_nets = (pin_net_map((nets or {}).get("base")), pin_net_map((nets or {}).get("head")))
    bmap = base.by_id() if base else {}
    hmap = head.by_id() if head else {}
    order = [s.id for s in (head.sheets if head else [])] + [s.id for s in (base.sheets if base else [])
                                                             if s.id not in hmap]
    out = []
    for sid in order:
        b, h = bmap.get(sid), hmap.get(sid)
        s = h or b
        changes = diff_sheet(b, h, pin_nets)
        status = "added" if b is None else "removed" if h is None else ("modified" if changes else "unchanged")
        out.append({"id": sid, "title": s.name, "file": s.file, "page": s.page, "status": status,
                    "size_mm": list(s.size_mm), "base_file": b.file if b else None, "head_file": h.file if h else None,
                    "changes": changes, **sheet_counts(changes)})
    return out


def sheet_counts(changes: list[dict]) -> dict:
    """counts {changed, minor, moved} of a sheet's changes; moved_only: true when every change that is not
    minor only moves things (smart mode shows the sheet as moved)."""
    moved = sum(1 for c in changes if c.get("move_only"))
    minor = sum(1 for c in changes if c.get("minor") and not c.get("move_only"))
    out = {"counts": {"changed": len(changes) - moved - minor, "minor": minor, "moved": moved}}
    if moved and moved + minor == len(changes):
        out["moved_only"] = True
    return out


# --- BOM -------------------------------------------------------------------------------

from .sch import MPN_FIELDS  # noqa: E402


def mpn_of(fields: dict) -> str:
    for k in MPN_FIELDS:
        for fk, v in fields.items():
            if fk.lower() == k.lower() and v and v != "~":
                return v
    return ""


def _bom_side(c):
    if c is None:
        return None
    return {"value": c["value"], "footprint": c["footprint"], "fields": c["fields"], "dnp": c["dnp"],
            "in_bom": c["in_bom"], "lib_id": c["lib_id"], "sheet": c["sheet"], "mpn": mpn_of(c["fields"])}


def diff_bom(base: dict | None, head: dict | None):
    """Per-reference rows plus rows grouped by (value, footprint, MPN, DNP)."""
    from .diff_pcb import natural_key
    base, head = base or {}, head or {}
    rows = []
    for ref in sorted(set(base) | set(head), key=natural_key):
        b, h = base.get(ref), head.get(ref)
        b = b if b is not None and b["in_bom"] else None  # excluded from BOM = not in the BOM
        h = h if h is not None and h["in_bom"] else None
        if b is None and h is None:
            continue
        what = []
        if b is None:
            status = "added"
        elif h is None:
            status = "removed"
        else:
            for k in ("value", "footprint", "dnp", "lib_id"):
                if classify.norm_value(b[k]) != classify.norm_value(h[k]):
                    what.append(k)
            fdiff = classify.field_diff(b["fields"], h["fields"])
            what += fdiff.whats()
            status = "changed" if what else "unchanged"
        row = {"key": ref, "refs": [ref], "status": status, "base": _bom_side(b), "head": _bom_side(h), "what": what}
        if b is not None and h is not None and (fdiff.significant_keys or fdiff.minor_keys):
            row["fields_changed"] = {"significant": fdiff.significant_keys, "minor": fdiff.minor_keys}
        if status == "changed" and classify.is_minor(what):
            row["minor"] = True  # only fields that don't name the part, or the symbol's library
        rows.append(row)

    def groups(side):
        g = defaultdict(list)
        for ref, c in side.items():
            if not c["in_bom"]:
                continue
            g[(c["value"], c["footprint"], mpn_of(c["fields"]), c["dnp"])].append(ref)
        return g

    gb, gh = groups(base), groups(head)
    grouped = []
    for key in sorted(set(gb) | set(gh), key=lambda k: (k[1], k[0], k[2], k[3])):
        rb, rh = sorted(gb.get(key, []), key=natural_key), sorted(gh.get(key, []), key=natural_key)
        status = "added" if not rb else "removed" if not rh else ("changed" if rb != rh else "unchanged")
        grouped.append({"value": key[0], "footprint": key[1], "mpn": key[2], "dnp": key[3],
                        "status": status, "base": {"qty": len(rb), "refs": rb} if rb else None,
                        "head": {"qty": len(rh), "refs": rh} if rh else None,
                        "refs_added": [r for r in rh if r not in rb], "refs_removed": [r for r in rb if r not in rh]})
    return {"rows": rows, "groups": grouped}
