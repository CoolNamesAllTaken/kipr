"""Parse a .kicad_pcb (KiCad 6 .. 10) into plain objects for diffing.

Coordinates are board mm in KiCad's frame (y down). Footprint-local geometry is transformed to
board coordinates using the footprint's position and rotation (KiCad stores flipped footprints
already mirrored, so no extra mirror is needed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom
from ._compat import Atom, Node, dumps, parse

R = 4  # rounding for geometric keys (0.1 µm)

SHAPE_NAMES = ("line", "rect", "circle", "arc", "poly", "curve", "bbox")
TEXT_NAMES = ("text", "text_box")


def rnd(v: float) -> float:
    v = round(float(v), R)
    return 0.0 if v == 0 else v


def pt(node: Node | None, default=(0.0, 0.0)):
    if node is None:
        return default
    a = node.nums()
    return (a[0], a[1]) if len(a) >= 2 else default


def net_of(node: Node, nets: dict[str, str]) -> str:
    """Net name of an item: `(net 3)`, `(net 3 "GND")` (pads) or `(net "GND")` (KiCad 10)."""
    nn = node.value("net_name")
    if nn is not None:
        return str(nn)
    c = node.child("net")
    if c is None:
        return ""
    args = c.atoms()
    if len(args) >= 2:
        return str(args[1])
    if len(args) == 1:
        a = args[0]
        return nets.get(str(a), "") if isinstance(a, Atom) else str(a)
    return ""


def item_layers(node: Node) -> list[str]:
    ls = node.child("layers")
    if ls is not None:
        return [str(a) for a in ls.atoms()]
    lay = node.value("layer")
    return [str(lay)] if lay is not None else []


def shape_points(node: Node) -> list[tuple[float, float]]:
    """Outline points of a gr_*/fp_* shape in its own frame."""
    kind = node.name.split("_", 1)[-1]
    if kind in ("line", "rect", "bbox"):
        s, e = pt(node.child("start")), pt(node.child("end"))
        if kind == "line":
            return [s, e]
        return [s, (e[0], s[1]), e, (s[0], e[1])]
    if kind == "circle":
        c, e = pt(node.child("center")), pt(node.child("end"))
        r = math.hypot(e[0] - c[0], e[1] - c[1])
        return [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
    if kind == "arc":
        s, m, e = pt(node.child("start")), node.child("mid"), pt(node.child("end"))
        if m is None:  # KiCad 5 style (start=centre, end=point, angle)
            return [s, e]
        return geom.arc_points(s, pt(m), e)
    pts = node.child("pts")
    out = []
    if pts is not None:
        for c in pts.children():
            if c.name == "xy":
                out.append(pt(c))
            elif c.name == "arc":
                out.extend(geom.arc_points(pt(c.child("start")), pt(c.child("mid")), pt(c.child("end"))))
    return out


def text_box(node: Node, text: str) -> list[float] | None:
    x, y = pt(node.child("at"))
    font = node.child("effects")
    size = 1.0
    if font is not None and font.child("font") is not None and font.child("font").child("size") is not None:
        size = (font.child("font").nums("size") or [1.0])[0]
    lines = str(text).split("\n")
    w = max((len(ln) for ln in lines), default=1) * size * 0.8 / 2
    h = len(lines) * size * 1.4 / 2
    if node.name.endswith("text_box") and node.child("start") is not None:
        return geom.box_of(shape_points(node) if node.child("pts") else
                           [pt(node.child("start")), pt(node.child("end"))])
    rot = (node.child("at").nums() + [0, 0, 0])[2] if node.child("at") is not None else 0
    corners = [geom.rotate(dx, dy, rot) for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]
    return geom.box_of([(x + dx, y + dy) for dx, dy in corners])


@dataclass
class Item:
    """A board-level item that is compared as a whole (track, via, graphic, text, outline)."""
    kind: str
    layers: list[str]
    key: str
    box: list[float] | None
    net: str = ""
    uuid: str = ""
    text: str = ""
    length: float = 0.0


@dataclass
class Zone:
    uuid: str
    name: str
    net: str
    layers: list[str]
    keepout: bool
    outline: str
    settings: str
    fill: str
    box: list[float] | None


@dataclass
class Footprint:
    uuid: str
    ref: str
    value: str
    lib_id: str
    x: float
    y: float
    rot: float
    side: str
    fields: dict[str, str]
    attrs: list[str]
    dnp: bool
    locked: bool
    models: list[dict]
    pads_key: str
    graphics_key: str
    box: list[float] | None
    layers: set[str] = field(default_factory=set)
    path: str = ""
    pad_nets: dict[str, str] = field(default_factory=dict)
    edge_pts: list = field(default_factory=list)
    holes: set[str] = field(default_factory=set)  # {"PTH", "NPTH"} if it has drilled pads


@dataclass
class Board:
    layers: list[dict]
    copper: list[str]
    nets: dict[str, str]
    footprints: list[Footprint]
    items: list[Item]
    zones: list[Zone]
    setup_key: dict[str, str]
    thickness: float | None
    stackup: dict
    edge_box: list[float] | None
    title: dict


def layer_kind(name: str) -> str:
    if name.endswith(".Cu"):
        return "copper"
    suffix = name.split(".", 1)[-1]
    return {"Mask": "mask", "Paste": "paste", "SilkS": "silk", "Silkscreen": "silk", "Fab": "fab",
            "CrtYd": "courtyard", "Courtyard": "courtyard", "Adhes": "adhesive", "Adhesive": "adhesive",
            "Cuts": "outline"}.get(suffix, "user")


def layer_side(name: str) -> str:
    if name.startswith("F."):
        return "top"
    if name.startswith("B."):
        return "bottom"
    if name.startswith("In") and name.endswith(".Cu"):
        return "inner"
    return "none"


def expand_layers(names, copper: list[str], all_layers: list[str]) -> set[str]:
    out = set()
    for n in names:
        if n.startswith("*."):
            suf = n[1:]
            out.update(x for x in (copper if suf == ".Cu" else all_layers) if x.endswith(suf))
        elif n.startswith("F&B."):
            suf = n[3:]
            out.update(("F" + suf, "B" + suf))
        else:
            out.add(n)
    return out


def _fp_transform(x0, y0, rot):
    def tf(p):
        dx, dy = geom.rotate(p[0], p[1], rot)
        return (x0 + dx, y0 + dy)
    return tf


def _pad_box(pad: Node, tf, fprot: float):
    px, py = pt(pad.child("at"))
    a = (pad.child("at").nums() + [0, 0, 0])[2] if pad.child("at") is not None else 0.0
    sw, sh = ((pad.nums("size") or []) + [0.0, 0.0])[:2]
    cx, cy = tf((px, py))
    corners = [geom.rotate(dx, dy, a) for dx, dy in ((-sw / 2, -sh / 2), (sw / 2, -sh / 2),
                                                        (sw / 2, sh / 2), (-sw / 2, sh / 2))]
    return geom.box_of([(cx + dx, cy + dy) for dx, dy in corners])


def _pad_key(pad: Node, fprot: float) -> str:
    """Pad canonical text, footprint-relative (pad angles in files include the footprint's)."""
    at = pad.child("at")
    parts = [dumps(c, drop=("uuid", "tstamp", "net", "pinfunction", "pintype"))
             for c in pad[1:] if not (isinstance(c, Node) and c.name == "at")]
    if at is not None:
        n = at.nums() + [0, 0, 0]
        parts.insert(0, f"(at {rnd(n[0])} {rnd(n[1])} {rnd((n[2] - fprot) % 360)})")
    return "(pad " + " ".join(parts) + ")"


def _parse_footprint(fp: Node, nets, copper, all_layers) -> Footprint:
    at = fp.child("at")
    xyz = (at.nums() + [0, 0, 0]) if at is not None else [0, 0, 0]
    x, y, rot = xyz[0], xyz[1], xyz[2]
    tf = _fp_transform(x, y, rot)
    layer = str(fp.value("layer", "F.Cu"))
    props: dict[str, str] = {}
    for p in fp.children("property"):
        if p.arg(0) is not None:
            props[str(p.arg(0))] = str(p.arg(1, ""))
    for t in fp.children("fp_text"):  # KiCad <= 7
        kind = str(t.arg(0, ""))
        if kind == "reference":
            props.setdefault("Reference", str(t.arg(1, "")))
        elif kind == "value":
            props.setdefault("Value", str(t.arg(1, "")))
    ref, value = props.pop("Reference", ""), props.pop("Value", "")
    props.pop("Footprint", None)
    attr = fp.child("attr")
    attrs = sorted(str(a) for a in attr.atoms()) if attr is not None else []
    dnp = "dnp" in attrs or fp.flag("dnp")
    models = []
    for m in fp.children("model"):
        md = {"path": str(m.arg(0, ""))}
        for k in ("offset", "scale", "rotate"):
            c = m.child(k)
            if c is not None:
                md[k] = [rnd(v) for v in (c.nums("xyz") or [])]
        if m.flag("hide"):
            md["hide"] = True
        models.append(md)
    pads = list(fp.children("pad"))
    pad_keys = sorted(_pad_key(p, rot) for p in pads)
    pad_nets = {}
    for p in pads:
        num = str(p.arg(0, ""))
        if num:
            n = net_of(p, nets)
            if n or num not in pad_nets:
                pad_nets[num] = n
    gfx_keys, crt_pts, edge_pts, all_boxes, layers = [], [], [], [], set()
    for c in fp.children():
        nm = c.name
        if nm.startswith("fp_") and nm[3:] in SHAPE_NAMES:
            gfx_keys.append(dumps(c, drop=("uuid", "tstamp")))
            pts = [tf(p) for p in shape_points(c)]
            lay = str(c.value("layer", ""))
            layers.add(lay)
            if lay.endswith("CrtYd"):
                crt_pts.extend(pts)
            elif lay == "Edge.Cuts":
                edge_pts.extend(pts)
            b = geom.box_of(pts)
            if b:
                all_boxes.append(b)
        elif nm in ("fp_text", "fp_text_box"):
            gfx_keys.append(f"(text {str(c.arg(0, ''))!r} {str(c.arg(1, ''))!r} {c.value('layer', '')})")
            layers.add(str(c.value("layer", "")))
        elif nm == "property":
            lay = c.value("layer")
            if lay is not None and not c.flag("hide"):
                layers.add(str(lay))
    for p in pads:
        layers |= expand_layers([str(a) for a in (p.child("layers").atoms() if p.child("layers") else [])],
                                copper, all_layers)
        b = _pad_box(p, tf, rot)
        if b:
            all_boxes.append(b)
    box = geom.box_of(crt_pts) or geom.union(*all_boxes)
    if box is None:
        box = [x - 0.5, y - 0.5, x + 0.5, y + 0.5]
    layers.discard("")
    return Footprint(
        uuid=str(fp.value("uuid", fp.value("tstamp", "")) or ""),
        ref=ref, value=value, lib_id=str(fp.arg(0, "")), x=rnd(x), y=rnd(y), rot=rnd(rot % 360),
        side="bottom" if layer.startswith("B.") else "top", fields=props, attrs=attrs, dnp=dnp,
        locked=fp.flag("locked"), models=models, pads_key="\n".join(pad_keys),
        graphics_key="\n".join(sorted(gfx_keys)), box=box, layers=layers,
        path=str(fp.value("path", "") or ""), pad_nets=pad_nets, edge_pts=edge_pts,
        holes={{"thru_hole": "PTH", "np_thru_hole": "NPTH"}[str(p.arg(1))] for p in pads
               if str(p.arg(1, "")) in ("thru_hole", "np_thru_hole")})


def _track_item(n: Node, nets) -> Item:
    layers = item_layers(n)
    net = net_of(n, nets)
    w = rnd(n.num("width"))
    if n.name == "via":
        x, y = pt(n.child("at"))
        size = n.num("size")
        extra = [a for a in n.atoms()]  # blind / micro
        key = f"via {rnd(x)} {rnd(y)} {rnd(size)} {rnd(n.num('drill'))} {'/'.join(layers)} {net} {' '.join(map(str, extra))}"
        return Item("via", layers, key, geom.circle_box(x, y, size / 2), net, str(n.value("uuid", "")))
    s, e = pt(n.child("start")), pt(n.child("end"))
    if n.name == "arc":
        m = pt(n.child("mid"))
        pts = geom.arc_points(s, m, e)
        ends = sorted([(rnd(s[0]), rnd(s[1])), (rnd(e[0]), rnd(e[1]))])
        key = f"arc {ends} {rnd(m[0])} {rnd(m[1])} {w} {'/'.join(layers)} {net}"
        length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    else:
        pts = [s, e]
        ends = sorted([(rnd(s[0]), rnd(s[1])), (rnd(e[0]), rnd(e[1]))])
        key = f"seg {ends} {w} {'/'.join(layers)} {net}"
        length = math.dist(s, e)
    return Item("track", layers, key, geom.grow(geom.box_of(pts), w / 2), net, str(n.value("uuid", "")),
                length=length)


def _zone(z: Node, nets) -> Zone:
    layers = item_layers(z)
    poly_pts = []
    for p in z.children("polygon"):
        poly_pts.extend(shape_points(p))
    outline = " ".join(f"{rnd(a)},{rnd(b)}" for a, b in poly_pts)
    settings = " ".join(dumps(c) for c in z.children()
                        if c.name not in ("polygon", "filled_polygon", "uuid", "tstamp", "fill_segments",
                                          "net", "net_name", "layer", "layers", "name"))
    fill = str(hash(" ".join(dumps(c) for c in z.children("filled_polygon"))))
    return Zone(uuid=str(z.value("uuid", z.value("tstamp", "")) or ""), name=str(z.value("name", "") or ""),
                net=net_of(z, nets), layers=layers, keepout=z.child("keepout") is not None,
                outline=outline, settings=settings, fill=fill, box=geom.box_of(poly_pts))


def load(text: str) -> Board:
    root = parse(text)
    layers, all_names = [], []
    lnode = root.child("layers")
    if lnode is not None:
        for c in lnode.children():
            a = c.atoms()
            # (0 "F.Cu" signal): the head is the ordinal
            if len(a) >= 2:
                layers.append({"ordinal": str(c[0]), "name": str(a[0]), "type": str(a[1]),
                               "user_name": str(a[2]) if len(a) > 2 else None})
                all_names.append(str(a[0]))
    copper = [ly["name"] for ly in layers if ly["name"].endswith(".Cu")]
    nets = {}
    for n in root.children("net"):
        a = n.atoms()
        if len(a) >= 2:
            nets[str(a[0])] = str(a[1])
    footprints = [_parse_footprint(f, nets, copper, all_names) for f in root.children("footprint")]
    items: list[Item] = []
    zones: list[Zone] = []
    edge_pts = []
    for c in root.children():
        nm = c.name
        if nm in ("segment", "arc", "via"):
            items.append(_track_item(c, nets))
        elif nm == "zone":
            zones.append(_zone(c, nets))
        elif nm.startswith("gr_") and nm[3:] in SHAPE_NAMES:
            pts = shape_points(c)
            lay = item_layers(c)
            kind = "outline" if "Edge.Cuts" in lay else "graphic"
            if kind == "outline":
                edge_pts.extend(pts)
            items.append(Item(kind, lay, dumps(c, drop=("uuid", "tstamp")), geom.box_of(pts),
                              net_of(c, nets), str(c.value("uuid", "") or "")))
        elif nm in ("gr_text", "gr_text_box"):
            txt = str(c.arg(0, ""))
            items.append(Item("text", item_layers(c), dumps(c, drop=("uuid", "tstamp")), text_box(c, txt),
                              "", str(c.value("uuid", "") or ""), text=txt))
        elif nm == "dimension":
            pts = [pt(x) for x in (c.child("pts").children("xy") if c.child("pts") else [])]
            items.append(Item("graphic", item_layers(c), dumps(c, drop=("uuid", "tstamp")), geom.box_of(pts),
                              "", str(c.value("uuid", "") or "")))
    for fp in footprints:  # footprint-embedded Edge.Cuts (slots, cut-outs) count for the size
        edge_pts.extend(fp.edge_pts)
    setup = root.child("setup")
    setup_key = {}
    stackup = {}
    if setup is not None:
        for c in setup.children():
            if c.name == "stackup":
                for ly in c.children("layer"):
                    stackup[str(ly.arg(0, ""))] = {k: str(ly.value(k)) for k in ("type", "color", "material",
                                                                                 "thickness")
                                                   if ly.value(k) is not None}
                for k in ("copper_finish", "dielectric_constraints", "edge_connector", "castellated_pads",
                          "edge_plating"):
                    if c.value(k) is not None:
                        stackup[k] = str(c.value(k))
                setup_key["stackup"] = dumps(c)
            elif c.name != "pcbplotparams":
                setup_key[c.name] = dumps(c)
    gen = root.child("general")
    thickness = gen.num("thickness", None) if gen is not None else None
    tb = root.child("title_block")
    title = {}
    if tb is not None:
        for c in tb.children():
            title[c.name if c.name != "comment" else f"comment{c.arg(0)}"] = str(c.arg(1 if c.name == "comment" else 0, ""))
    return Board(layers=layers, copper=copper, nets=nets, footprints=footprints, items=items, zones=zones,
                 setup_key=setup_key, thickness=thickness, stackup=stackup,
                 edge_box=geom.box_of(edge_pts), title=title)
