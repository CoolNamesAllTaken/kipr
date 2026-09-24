"""Parse a KiCad (6 .. 10) schematic hierarchy into plain objects for diffing.

Sheets get a stable id built from sheet *names* along the hierarchy ("root", "root/power",
"root/power/ldo"), so a sheet keeps its id across revisions even when its uuid or file changes.
Coordinates are sheet mm (y down).
"""

from __future__ import annotations

import math
import posixpath
import re
from dataclasses import dataclass, field

from . import geom
from ._compat import Node, dumps, parse
from .pcb import pt, rnd, shape_points

# Paper sizes in mm (landscape), see KiCad's PAGE_INFO.
PAPER = {"A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594),
         "A0": (1189, 841), "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8),
         "D": (863.6, 558.8), "E": (1117.6, 863.6), "USLetter": (279.4, 215.9),
         "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4), "GERBER": (812.8, 812.8)}

WIRING = ("wire", "bus", "bus_entry", "junction", "no_connect")
LABELS = ("label", "global_label", "hierarchical_label", "netclass_flag", "directive_label")
GRAPHICS = ("polyline", "rectangle", "circle", "arc", "bezier", "image")
TEXTS = ("text", "text_box", "table")
SKIP_HEADER = ("version", "generator", "generator_version", "uuid", "lib_symbols", "sheet_instances",
               "symbol_instances", "embedded_fonts")

MPN_FIELDS = ("MPN", "Manufacturer Part Number", "Mfr. Part Number", "Mfr Part Number", "MFN", "Mfr_PN",
              "PartNumber", "Part Number", "MFR PN", "Manufacturer_Part_Number")


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", str(s).lower()).strip("-")
    return s or "sheet"


def page_size(root: Node) -> tuple[float, float]:
    p = root.child("paper")
    if p is None:
        return PAPER["A4"]
    a = [str(x) for x in p.atoms()]
    if a and a[0] == "User" and len(a) >= 3:
        try:
            w, h = float(a[1]), float(a[2])
        except ValueError:
            w, h = PAPER["A4"]
    else:
        w, h = PAPER.get(a[0] if a else "A4", PAPER["A4"])
    if "portrait" in a:
        w, h = min(w, h), max(w, h)
    return (w, h)


def props_of(node: Node) -> dict[str, str]:
    out = {}
    for p in node.children("property"):
        if p.arg(0) is not None:
            out[str(p.arg(0))] = str(p.arg(1, ""))
    return out


@dataclass
class Symbol:
    uuid: str
    lib_id: str
    ref: str
    value: str
    footprint: str
    fields: dict[str, str]
    unit: int
    x: float
    y: float
    rot: float
    mirror: str
    in_bom: bool
    on_board: bool
    dnp: bool
    exclude_from_sim: bool
    box: list[float] | None
    lib_key: str  # canonical text of the library symbol it uses (graphics/pins)
    power: bool
    datasheet: str = ""
    description: str = ""


@dataclass
class Element:
    """Wiring, label, text or graphic element compared as a whole."""
    kind: str  # wire | label | text | graphic
    sub: str  # node name, e.g. "wire", "global_label"
    key: str
    box: list[float] | None
    text: str = ""
    pos: tuple[float, float] | None = None


@dataclass
class SheetRef:
    """A sub-sheet box drawn on its parent sheet."""
    uuid: str
    name: str
    file: str
    box: list[float] | None
    key: str  # size/pos/pins/fields


@dataclass
class Sheet:
    id: str
    name: str
    file: str
    path: str  # uuid path, "/<root uuid>/<sheet uuid>/..."
    page: str
    size_mm: tuple[float, float]
    symbols: list[Symbol] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    subsheets: list[SheetRef] = field(default_factory=list)
    digest: str = ""
    title: dict = field(default_factory=dict)
    error: str | None = None
    names: list[str] = field(default_factory=list)  # sheet names from the root, e.g. ["Root", "power"]


def _lib_symbols(root: Node) -> dict[str, Node]:
    libs = root.child("lib_symbols")
    out = {}
    if libs is not None:
        for s in libs.children("symbol"):
            out[str(s.arg(0, ""))] = s
    return out


def _lib_points(lib: Node, unit: int, style: int, libs: dict[str, Node], depth=0):
    """Graphic + pin points of a library symbol in its own frame (y up)."""
    pts = []
    ext = lib.value("extends")
    if ext is not None and depth < 4:
        parent = libs.get(str(ext)) or next((v for k, v in libs.items() if k.split(":")[-1] == str(ext)), None)
        if parent is not None:
            pts.extend(_lib_points(parent, unit, style, libs, depth + 1))
    for sub in lib.children("symbol"):
        m = re.match(r"^(.*)_(\d+)_(\d+)$", str(sub.arg(0, "")))
        if m:
            u, st = int(m.group(2)), int(m.group(3))
            if u not in (0, unit) or st not in (0, style):
                continue
        for g in sub.children():
            if g.name == "pin":
                x, y = pt(g.child("at"))
                a = (g.child("at").nums() + [0, 0, 0])[2] if g.child("at") is not None else 0
                ln = g.num("length", 2.54)
                pts += [(x, y), (x + ln * math.cos(math.radians(a)), y + ln * math.sin(math.radians(a)))]
            elif g.name == "circle":
                cx, cy = pt(g.child("center"))
                r = g.num("radius")
                pts += [(cx - r, cy - r), (cx + r, cy + r)]
            elif g.name in ("rectangle", "polyline", "arc", "bezier"):
                if g.name == "rectangle":
                    s, e = pt(g.child("start")), pt(g.child("end"))
                    pts += [s, e]
                elif g.name == "arc" and g.child("mid") is not None:
                    pts += geom.arc_points(pt(g.child("start")), pt(g.child("mid")), pt(g.child("end")))
                else:
                    pts += shape_points(g) or [pt(g.child("start")), pt(g.child("end"))]
            elif g.name == "text":
                pts.append(pt(g.child("at")))
    return pts


def symbol_transform(x0, y0, rot, mirror):
    """Library (y up) -> sheet (y down) transform of a placed symbol."""
    def tf(p):
        x, y = p[0], -p[1]
        if mirror == "y":
            x = -x
        elif mirror == "x":
            y = -y
        x, y = geom.rotate(x, y, rot)
        return (x0 + x, y0 + y)
    return tf


def _symbol(s: Node, libs: dict[str, Node], inst_path: str) -> Symbol:
    props = props_of(s)
    lib_id = str(s.value("lib_id", ""))
    lib_name = s.value("lib_name")
    lib = libs.get(str(lib_name)) if lib_name is not None else None
    lib = lib if lib is not None else libs.get(lib_id)
    at = s.child("at")
    xyz = (at.nums() + [0, 0, 0]) if at is not None else [0, 0, 0]
    unit = int(s.num("unit", 1) or 1)
    style = int(s.num("body_style", s.num("convert", 1)) or 1)
    mirror = str(s.value("mirror", "") or "")
    ref = props.get("Reference", "")
    inst = s.child("instances")
    if inst is not None:  # KiCad 7+: per-instance reference
        for proj in inst.children("project"):
            for p in proj.children("path"):
                if str(p.arg(0, "")) == inst_path:
                    ref = str(p.value("reference", ref))
                    unit = int(p.num("unit", unit) or unit)
    tf = symbol_transform(xyz[0], xyz[1], xyz[2], mirror)
    pts = [tf(p) for p in _lib_points(lib, unit, style, libs)] if lib is not None else []
    box = geom.box_of(pts) or [xyz[0] - 1.27, xyz[1] - 1.27, xyz[0] + 1.27, xyz[1] + 1.27]
    fields = {k: v for k, v in props.items() if k not in ("Reference", "Value", "Footprint", "Datasheet",
                                                              "Description") and not k.startswith("ki_")}
    power = lib is not None and lib.child("power") is not None or ref.startswith("#")
    return Symbol(uuid=str(s.value("uuid", "") or ""), lib_id=lib_id, ref=ref, value=props.get("Value", ""),
                  footprint=props.get("Footprint", ""), fields=fields, unit=unit, x=rnd(xyz[0]),
                  y=rnd(xyz[1]), rot=rnd(xyz[2] % 360), mirror=mirror,
                  in_bom=s.flag("in_bom") if s.child("in_bom") is not None else True,
                  on_board=s.flag("on_board") if s.child("on_board") is not None else True,
                  dnp=s.flag("dnp"), exclude_from_sim=s.flag("exclude_from_sim"), box=box,
                  lib_key=dumps(lib, drop=("uuid",)) if lib is not None else "", power=power,
                  datasheet=props.get("Datasheet", ""), description=props.get("Description", ""))


def _element(n: Node) -> Element | None:
    nm = n.name
    if nm in ("wire", "bus"):
        pts = shape_points(n)
        ends = sorted((rnd(x), rnd(y)) for x, y in pts)
        return Element("wire", nm, f"{nm} {ends}", geom.box_of(pts))
    if nm == "bus_entry":
        x, y = pt(n.child("at"))
        w, h = ((n.nums("size") or []) + [2.54, 2.54])[:2]
        return Element("wire", nm, f"{nm} {rnd(x)} {rnd(y)} {rnd(w)} {rnd(h)}", geom.box_of([(x, y), (x + w, y + h)]))
    if nm in ("junction", "no_connect"):
        x, y = pt(n.child("at"))
        return Element("wire", nm, f"{nm} {rnd(x)} {rnd(y)}", [x - 0.6, y - 0.6, x + 0.6, y + 0.6], pos=(x, y))
    if nm in LABELS:
        x, y = pt(n.child("at"))
        text = str(n.arg(0, "")) if nm != "netclass_flag" else str(n.arg(0, ""))
        shape = n.value("shape", "")
        props = props_of(n)
        extra = " ".join(f"{k}={v}" for k, v in sorted(props.items()) if k != "Intersheetrefs")
        key = f"{nm} {text!r} {rnd(x)} {rnd(y)} {shape} {extra}"
        w = max(len(text), 1) * 1.1
        return Element("label", nm, key, [x - 1, y - 1.5, x + w, y + 1.5], text=text, pos=(x, y))
    if nm in TEXTS:
        text = str(n.arg(0, "")) if nm != "table" else ""
        if nm == "text_box" and n.child("start") is not None:
            box = geom.box_of([pt(n.child("start")), pt(n.child("end"))])
        elif nm == "text_box" and n.child("at") is not None:
            x, y = pt(n.child("at"))
            w, h = (n.nums("size") or [10, 5])[:2]
            box = [x, y, x + w, y + h]
        elif nm == "table":
            pts = []
            for c in n.children("cells"):
                for cell in c.children("table_cell"):
                    x, y = pt(cell.child("at"))
                    w, h = (cell.nums("size") or [0, 0])[:2]
                    pts += [(x, y), (x + w, y + h)]
            box = geom.box_of(pts)
            text = " | ".join(str(cell.arg(0, "")) for c in n.children("cells") for cell in c.children("table_cell"))
        else:
            x, y = pt(n.child("at"))
            lines = text.split("\n")
            w = max((len(ln) for ln in lines), default=1) * 1.1
            box = [x, y - 1.5, x + w, y + len(lines) * 2.2]
        return Element("text", nm, dumps(n, drop=("uuid",)), box, text=text)
    if nm in GRAPHICS:
        if nm == "image":
            x, y = pt(n.child("at"))
            return Element("graphic", nm, f"image {rnd(x)} {rnd(y)} {n.num('scale', 1)} {hash(str(n.child('data')))}",
                           [x - 5, y - 5, x + 5, y + 5])
        if nm == "circle":
            cx, cy = pt(n.child("center"))
            r = n.num("radius")
            box = geom.circle_box(cx, cy, r)
        elif nm == "rectangle":
            box = geom.box_of([pt(n.child("start")), pt(n.child("end"))])
        else:
            box = geom.box_of(shape_points(n))
        return Element("graphic", nm, dumps(n, drop=("uuid",)), box)
    return None


def _subsheet(n: Node) -> SheetRef:
    props = props_of(n)
    name = props.get("Sheetname", props.get("Sheet name", ""))
    file = props.get("Sheetfile", props.get("Sheet file", ""))
    x, y = pt(n.child("at"))
    w, h = (n.nums("size") or [0, 0])[:2]
    pins = sorted(f"{p.arg(0, '')}:{p.arg(1, '')}@{rnd(pt(p.child('at'))[0])},{rnd(pt(p.child('at'))[1])}"
                  for p in n.children("pin"))
    key = f"{rnd(x)} {rnd(y)} {rnd(w)} {rnd(h)} {pins} " + " ".join(
        f"{k}={v}" for k, v in sorted(props.items()))
    return SheetRef(uuid=str(n.value("uuid", "") or ""), name=name, file=file, box=[x, y, x + w, y + h], key=key)


def _pages(root: Node) -> dict[str, str]:
    """Legacy (KiCad 6) root `sheet_instances`: {path: page}."""
    out = {}
    si = root.child("sheet_instances")
    if si is not None:
        for p in si.children("path"):
            out[str(p.arg(0, ""))] = str(p.value("page", ""))
    return out


class SchematicSet:
    """All sheet instances of a project, walked from the root schematic."""

    def __init__(self, sheets: list[Sheet], errors: list[str]):
        self.sheets = sheets
        self.errors = errors

    def by_id(self):
        return {s.id: s for s in self.sheets}

    def components(self) -> dict[str, dict]:
        """{ref: {...}} of real (non-power) symbols, units merged, for BOM/component diffs."""
        out: dict[str, dict] = {}
        for sh in self.sheets:
            for s in sh.symbols:
                if s.power or not s.ref or s.ref.startswith("#"):
                    continue
                c = out.get(s.ref)
                if c is None:
                    out[s.ref] = {"ref": s.ref, "value": s.value, "footprint": s.footprint, "lib_id": s.lib_id,
                                  "fields": dict(s.fields), "dnp": s.dnp, "in_bom": s.in_bom,
                                  "on_board": s.on_board, "datasheet": s.datasheet,
                                  "description": s.description, "sheet": sh.id, "units": [s.unit]}
                else:
                    c["units"].append(s.unit)
        return out


def load_hierarchy(read, root_file: str, project_name: str = "") -> SchematicSet:
    """Walk the hierarchy. `read(relpath) -> str | None` returns a file's text (paths relative to
    the project dir, as written in the Sheetfile property)."""
    errors: list[str] = []
    cache: dict[str, Node | None] = {}
    sheets: list[Sheet] = []
    used_ids: set[str] = set()

    def get(path):
        if path not in cache:
            text = read(path)
            if text is None:
                cache[path] = None
                errors.append(f"schematic file not found: {path}")
            else:
                try:
                    cache[path] = parse(text)
                except ValueError as e:
                    cache[path] = None
                    errors.append(f"cannot parse {path}: {e}")
        return cache[path]

    root = get(root_file)
    if root is None:
        return SchematicSet([], errors)
    legacy_pages = _pages(root)
    root_uuid = str(root.value("uuid", "") or "")

    def walk(file, sid, names, path, page, depth, stack):
        node = get(file)
        sheet = Sheet(id=sid, name=names[-1], file=file, path=path, page=page, names=names,
                      size_mm=page_size(node) if node is not None else PAPER["A4"])
        sheets.append(sheet)
        if node is None:
            sheet.error = f"missing {file}"
            return
        libs = _lib_symbols(node)
        tb = node.child("title_block")
        if tb is not None:
            sheet.title = {c.name: str(c.arg(0 if c.name != "comment" else 1, "")) for c in tb.children()}
        body = []
        for c in node.children():
            if c.name == "symbol":
                sym = _symbol(c, libs, path)
                sheet.symbols.append(sym)
            elif c.name == "sheet":
                sheet.subsheets.append(_subsheet(c))
            else:
                el = _element(c)
                if el is not None:
                    sheet.elements.append(el)
            if c.name not in SKIP_HEADER:
                body.append(dumps(c, drop=("uuid", "instances")))
        sheet.digest = str(hash("\n".join(body)))
        if depth > 32 or file in stack:
            errors.append(f"recursive sheet {file}")
            return
        for sub in sheet.subsheets:
            base = f"{sid}/{slug(sub.name)}"
            cid, k = base, 2
            while cid in used_ids:
                cid, k = f"{base}-{k}", k + 1
            used_ids.add(cid)
            sub_path = f"{path}/{sub.uuid}"
            sub_page = legacy_pages.get(sub_path, legacy_pages.get(sub_path.replace(f"/{root_uuid}", "", 1), ""))
            snode = next((c for c in node.children("sheet") if str(c.value("uuid", "")) == sub.uuid), None)
            if snode is not None and snode.child("instances") is not None:
                for proj in snode.child("instances").children("project"):
                    for p in proj.children("path"):
                        if str(p.arg(0, "")) == path:
                            sub_page = str(p.value("page", sub_page))
            sub_file = posixpath.normpath(posixpath.join(posixpath.dirname(file), sub.file)) if sub.file else ""
            walk(sub_file, cid, names + [sub.name], sub_path, sub_page, depth + 1, stack | {file})

    used_ids.add("root")
    walk(root_file, "root", [project_name or "Root"], f"/{root_uuid}" if root_uuid else "",
         legacy_pages.get("/", "1") or "1", 0, frozenset())
    return SchematicSet(sheets, errors)
