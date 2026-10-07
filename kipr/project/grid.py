"""Schematic connection-grid check: flag connection points off the grid (50 mil by default).

Checked on the head side, per sheet file: symbol pin connection points (placement + library pin
positions with rotation/mirror, per unit and body style), wire and bus endpoints, bus entries,
junctions, no-connect flags, label anchors (local, global, hierarchical, net-class/directive) and
sheet pins. By default only what the PR added or moved is flagged (`mode="changed"`): an item is
matched to base by uuid, else by position (same file and kind), and only its points that are new
in head count. `mode="all"` checks everything.

Findings are grouped: one symbol (or sheet box) with all its off-grid pins is one finding, and
off-grid wires, junctions and labels that touch it are listed as `related`; off-grid wiring that
touches no symbol is grouped by shared off-grid points. See docs/CONTRACT-project.md (checks.grid).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from . import geom, sch
from boarddd.io.kicad.pcb import pt

MIL_MM = 0.0254
TOLERANCE_MM = 0.001  # float noise; KiCad stores schematic coordinates in 100 nm steps
MODES = ("changed", "all", "off")
MAX_POINTS = 50  # points listed per finding
LABELS = {"label": "label", "global_label": "global label", "hierarchical_label": "hierarchical label",
          "netclass_flag": "net class flag", "directive_label": "directive label"}
# which item of a group of touching off-grid items names the finding
PRIORITY = {"symbol": 0, "sheet": 1, "global_label": 2, "hierarchical_label": 2, "label": 2, "netclass_flag": 2,
            "directive_label": 2, "bus_entry": 3, "bus": 4, "wire": 5, "junction": 6, "no_connect": 7}
ANCHORS = ("symbol", "sheet")  # never merged with each other
NOUN = {"symbol": "pin", "sheet": "sheet pin", "wire": "end", "bus": "end", "bus_entry": "end"}


@dataclass
class Item:
    kind: str  # symbol | sheet | wire | bus | bus_entry | junction | no_connect | label | global_label | ...
    uuid: str
    sheet: str  # sheet id of the first instance
    file: str  # sheet file, relative to the project dir
    line: int
    points: list  # [(name, x, y)]; name: pin number / sheet pin name / "" for wiring
    ref: str = ""  # symbol reference, sheet name
    text: str = ""  # label text, symbol value
    box: list | None = None  # [x0, y0, x1, y1]
    anchor: tuple | None = None
    sheets: list = field(default_factory=list)  # every sheet instance showing it
    power: bool = False  # power symbol: grouped like a label with the part whose pin it sits on

    @property
    def anchor_kind(self) -> bool:
        return self.kind in ANCHORS and not self.power


def _key(x, y) -> tuple[int, int]:
    return (round(x * 10000), round(y * 10000))


def _items_of_sheet(sheet: sch.Sheet) -> list[Item]:
    node = sheet.node
    if node is None:
        return []
    libs = sch._lib_symbols(node)
    syms = {s.uuid: s for s in sheet.symbols}
    out = []
    for c in node.children():
        nm, uid, line = c.name, str(c.value("uuid", "") or ""), c.line_start
        mk = lambda kind, pts, **kw: Item(kind, uid, sheet.id, sheet.file, line, pts, **kw)  # noqa: E731
        if nm == "symbol":
            s = syms.get(uid)
            lib = sch.symbol_lib(c, libs)
            if s is None or lib is None:
                continue
            style = int(c.num("body_style", c.num("convert", 1)) or 1)
            at = (c.nums("at") or []) + [0.0, 0.0, 0.0]
            tf = sch.symbol_transform(at[0], at[1], at[2], s.mirror)
            pts = [(num or name, *tf((x, y))) for num, name, x, y, _hidden in sch.lib_pins(lib, s.unit, style, libs)]
            out.append(mk("symbol", pts, ref=s.ref, text=s.value, box=s.box, anchor=(s.x, s.y), power=s.power))
        elif nm == "sheet":
            props = sch.props_of(c)
            pts = [(str(p.arg(0, "")), *pt(p.child("at"))) for p in c.children("pin")]
            x, y = pt(c.child("at"))
            w, h = (c.nums("size") or [0, 0])[:2]
            out.append(mk("sheet", pts, ref=props.get("Sheetname", props.get("Sheet name", "")),
                          box=[x, y, x + w, y + h], anchor=(x, y)))
        elif nm in ("wire", "bus"):
            pts = c.child("pts")
            xy = [pt(p) for p in pts.children("xy")] if pts is not None else []
            if xy:
                out.append(mk(nm, [("", *xy[0]), ("", *xy[-1])] if len(xy) > 1 else [("", *xy[0])]))
        elif nm == "bus_entry":
            x, y = pt(c.child("at"))
            w, h = ((c.nums("size") or []) + [2.54, 2.54])[:2]
            out.append(mk(nm, [("", x, y), ("", x + w, y + h)]))
        elif nm in ("junction", "no_connect"):
            out.append(mk(nm, [("", *pt(c.child("at")))]))
        elif nm in LABELS:
            x, y = pt(c.child("at"))
            out.append(mk(nm, [("", x, y)], text=str(c.arg(0, "")), anchor=(x, y)))
    return out


def collect(schem: sch.SchematicSet | None) -> list[Item]:
    """Every checkable item of a hierarchy, each file once (sheets used twice share their items;
    an item whose points differ between instances, e.g. another unit, is kept per instance)."""
    if schem is None:
        return []
    seen: dict[tuple, Item] = {}
    out = []
    for sheet in schem.sheets:
        for it in _items_of_sheet(sheet):
            k = (it.file, it.uuid or it.line, it.kind, tuple(_key(x, y) for _, x, y in it.points))
            if k in seen:
                seen[k].sheets.append(sheet.id)
                continue
            it.sheets = [sheet.id]
            seen[k] = it
            out.append(it)
    return out


def off_grid(v: float, grid: float, tol: float = TOLERANCE_MM) -> float:
    """Signed distance of v to the nearest grid line; 0.0 when within `tol`."""
    d = v - round(v / grid) * grid
    return 0.0 if abs(d) <= tol else d


class _Base:
    """What the base side had: points per uuid, and per (file, kind) for items without a match."""

    def __init__(self, items: list[Item], base_sheets, head_sheets):
        self.by_uuid: dict[str, set] = {}
        self.by_file: dict[tuple, set] = {}
        for it in items:
            keys = {_key(x, y) for _, x, y in it.points}
            if it.uuid:
                self.by_uuid.setdefault(it.uuid, set()).update(keys)
            self.by_file.setdefault((it.file, it.kind), set()).update(keys)
        # a head file's base counterparts: the same path, and the file of the base sheet with the same id
        bfile = {s.id: s.file for s in base_sheets}
        self.files: dict[str, set] = {}
        for s in head_sheets:
            self.files.setdefault(s.file, {s.file}).add(bfile.get(s.id, s.file))

    def match(self, it: Item) -> tuple[str, set]:
        """(change, keys of the item's points that already existed in base)."""
        if it.uuid and it.uuid in self.by_uuid:
            old = self.by_uuid[it.uuid]
            keys = {_key(x, y) for _, x, y in it.points}
            return ("unchanged" if keys <= old else "moved"), old
        old = set()
        for f in self.files.get(it.file, {it.file}):
            old |= self.by_file.get((f, it.kind), set())
        keys = {_key(x, y) for _, x, y in it.points}
        if keys and keys <= old:
            return "unchanged", old
        return "added", old


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _xywh(b, pad=0.5):
    b = geom.grow(b, pad)
    return [round(b[0], 4), round(b[1], 4), round(b[2] - b[0], 4), round(b[3] - b[1], 4)]


def check(base: sch.SchematicSet | None, head: sch.SchematicSet | None, pdir: str = "", mode: str = "changed",
          grid_mil: float = 50.0, tol: float = TOLERANCE_MM) -> dict | None:
    """The `checks.grid` section (None when mode is "off" or head has no schematic)."""
    if mode == "off" or head is None:
        return None
    if mode not in MODES:
        raise ValueError(f"grid check mode must be one of {', '.join(MODES)}")
    g = grid_mil * MIL_MM
    items = collect(head)
    known = _Base(collect(base), base.sheets, head.sheets) if base is not None else None
    only_new = mode == "changed"
    findings = []
    checked = 0
    for it in items:
        change, old = known.match(it) if known is not None else ("added", set())
        if only_new and change == "unchanged":
            continue
        pts = []
        for name, x, y in it.points:
            if only_new and _key(x, y) in old:
                continue  # the point was there before the PR
            checked += 1
            dx, dy = off_grid(x, g, tol), off_grid(y, g, tol)
            if dx or dy:
                pts.append((name, x, y, dx, dy))
        if pts:
            findings.append((it, change, pts))
    groups = _group(findings)
    order = {s.id: i for i, s in enumerate(head.sheets)}
    res = []
    for grp in groups:
        it, change, pts = grp[0]
        res.append(_finding(it, change, pts, grp[1:], pdir, grid_mil, g))
    res.sort(key=lambda f: (order.get(f["sheet"], 1 << 30), f["pos_mm"][1], f["pos_mm"][0]))
    sheets = []
    for f in res:
        if not sheets or sheets[-1]["id"] != f["sheet"]:
            sheets.append({"id": f["sheet"], "file": f["file"], "count": 0})
        sheets[-1]["count"] += 1
    return {"grid_mil": grid_mil, "grid_mm": round(g, 6), "tolerance_mm": tol, "mode": mode,
            "checked": checked, "count": len(res), "points": sum(f["off_count"] + sum(r["off_count"] for r in f["related"]) for f in res),
            "sheets": sheets, "items": res}


def _group(findings):
    """Union findings of one file that share an off-grid point; two symbols/sheet boxes are never
    merged (power symbols are not anchors: they join the part they sit on, like labels). Each
    group is sorted so its first entry names it."""
    parent = list(range(len(findings)))
    anchors = [1 if f[0].anchor_kind else 0 for f in findings]

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    at: dict[tuple, list[int]] = {}
    for i, (it, _, pts) in enumerate(findings):
        for _, x, y, _, _ in pts:
            at.setdefault((it.file, _key(x, y)), []).append(i)
    # anchors first, so wiring joins the symbol it touches before joining other wiring
    for (_, _), idx in sorted(at.items(), key=lambda kv: -max(anchors[i] for i in kv[1])):
        for j in idx[1:]:
            a, b = root(idx[0]), root(j)
            if a == b or anchors[a] and anchors[b]:
                continue
            if anchors[b]:
                a, b = b, a
            parent[b] = a
            anchors[a] = anchors[a] or anchors[b]
    groups: dict[int, list] = {}
    for i, f in enumerate(findings):
        groups.setdefault(root(i), []).append(f)
    return [sorted(v, key=lambda f: (_priority(f[0]), f[0].line)) for v in groups.values()]


def _priority(it: Item) -> int:
    return PRIORITY["label"] if it.power else PRIORITY.get(it.kind, 9)


def _noun(it: Item) -> str:
    return "power symbol" if it.power else LABELS.get(it.kind, it.kind.replace("_", " "))


def _finding(it: Item, change: str, pts, related, pdir: str, grid_mil: float, g: float) -> dict:
    noun = NOUN.get(it.kind, "point")
    shown = [{"name": n, "pos_mm": [round(x, 4), round(y, 4)], "off_mm": [round(dx, 4), round(dy, 4)]}
             for n, x, y, dx, dy in pts[:MAX_POINTS]]
    first = pts[0]
    where = f"({_fmt(first[1])}, {_fmt(first[2])})"
    offs = " ".join(f"{a} {'+' if v > 0 else ''}{_fmt(v)}" for a, v in (("x", first[3]), ("y", first[4])) if v)
    if it.kind in ANCHORS:  # a lone power symbol reads like a symbol too
        what = f"{len(pts)} of {len(it.points)} {noun}s" if len(it.points) > 1 else f"its {noun}"
        name = f" {first[0]}" if first[0] else ""
        detail = f"{what} off the {_fmt(grid_mil)} mil grid, e.g. {noun}{name} at {where} ({offs} mm)"
    else:
        detail = (f"{len(pts)} {noun}s off" if len(pts) > 1 else "off") + f" the {_fmt(grid_mil)} mil grid at {where} ({offs} mm)"
    rel = []
    for r_it, r_change, r_pts in related:
        rel.append({"kind": r_it.kind, "uuid": r_it.uuid or None, "line": r_it.line, "ref": r_it.ref or None,
                    "text": r_it.text or None, "change": r_change, "off_count": len(r_pts)}
                   | ({"power": True} if r_it.power else {}))
    if rel:
        counts: dict[str, int] = {}
        for r_it, _, _ in related:
            k = _noun(r_it)
            counts[k] = counts.get(k, 0) + 1
        detail += "; also " + ", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in counts.items()) + \
            (" attached" if it.kind in ANCHORS else " at those points")
    box = geom.box_of([(x, y) for _, x, y, _, _ in pts])
    for r_it, _, r_pts in related:
        box = geom.union(box, geom.box_of([(x, y) for _, x, y, _, _ in r_pts]))
    if it.box and it.anchor_kind:
        box = geom.union(box, it.box)
    anchor = it.anchor or (first[1], first[2])
    return {"kind": it.kind, "severity": "warning", "change": change, **({"power": True} if it.power else {}),
            "ref": it.ref or None, "text": it.text or None,
            "sheet": it.sheet, "sheets": it.sheets, "file": posixpath.normpath(posixpath.join(pdir, it.file)) if pdir else it.file,
            "line": it.line, "uuid": it.uuid or None,
            "pos_mm": [round(anchor[0], 4), round(anchor[1], 4)], "bbox_mm": _xywh(box, max(0.5, g)),
            "off_count": len(pts), "points": shown, "related": rel,
            "detail": detail}
