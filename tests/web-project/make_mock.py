#!/usr/bin/env python3
"""Write a synthetic, contract-conformant `kipr project` OUT directory for viewer/report tests.

    python3 tests/web-project/make_mock.py --out /tmp/kipr-mock [--site]

Three projects (docs/CONTRACT-project.md):
  demo_board      modified: 5 sheets (modified/added/removed/unchanged), 2-layer PCB with real
                  RS-274X gerbers + Excellon drills + per-layer SVGs, BOM, netlist, ERC/DRC deltas
  sensor_breakout added: head only (1 sheet, PCB)
  old_adapter     removed: base only (schematic only)
Everything is generated here from a small board model; nothing is copied from any real design.
--site also copies the viewer in (kipr.project.site) so the OUT can be opened directly.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# ─── board model ───────────────────────────────────────────────────────────────────────────────
# KiCad frame, mm, y down. The board: 60 x 40 mm at (100, 70).
BOARD = (100.0, 70.0, 60.0, 40.0)


def soic8(ref, x, y, value="MCU", rot=0):
    pads = []
    for i in range(4):
        pads.append((x - 2.7, y - 1.905 + i * 1.27, 1.55, 0.6))
        pads.append((x + 2.7, y + 1.905 - i * 1.27, 1.55, 0.6))
    return {"ref": ref, "value": value, "fp": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "x": x, "y": y, "rot": rot,
            "side": "top", "pads": pads, "body": (x - 2.0, y - 2.5, 4.0, 5.0)}


def r0603(ref, x, y, value, side="top", fp="Resistor_SMD:R_0603_1608Metric"):
    return {"ref": ref, "value": value, "fp": fp, "x": x, "y": y, "rot": 0, "side": side,
            "pads": [(x - 0.8, y, 0.8, 0.95), (x + 0.8, y, 0.8, 0.95)], "body": (x - 1.45, y - 0.7, 2.9, 1.4)}


def header2(ref, x, y):
    return {"ref": ref, "value": "Conn_01x02", "fp": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            "x": x, "y": y, "rot": 0, "side": "top", "tht": True,
            "pads": [(x, y, 1.7, 1.7), (x, y + 2.54, 1.7, 1.7)], "drills": [(x, y, 1.0), (x, y + 2.54, 1.0)],
            "body": (x - 1.3, y - 1.3, 2.6, 5.1)}


def board(side: str) -> dict:
    """base / head variants of the demo board."""
    head = side == "head"
    comps = [
        soic8("U1", 118.0 + (3.0 if head else 0.0), 85.0),
        r0603("R1", 130.0, 80.0, "10k"),
        r0603("R2", 130.0, 84.0, "4.7k" if head else "10k"),
        r0603("C1", 130.0, 88.0, "100n", fp="Capacitor_SMD:C_0603_1608Metric"),
        header2("J1", 106.0, 88.0),
        r0603("R10", 140.0, 100.0, "1k", side="bottom"),
    ]
    if head:
        comps.append(r0603("C2", 136.0, 92.0, "1u", fp="Capacitor_SMD:C_0603_1608Metric"))
    ux = 118.0 + (3.0 if head else 0.0)
    tracks_top = [
        [(106.0, 88.0), (112.0, 88.0), (ux - 2.7, 83.095)],
        [(ux + 2.7, 83.095), (129.2, 80.0)],
        [(ux + 2.7, 84.365), (129.2, 84.0)],
        [(131.0, 80.0), (134.0, 80.0), (134.0, 88.0), (130.8, 88.0)],
    ]
    if head:
        tracks_top.append([(130.8, 88.0), (135.2, 92.0)])
    else:
        tracks_top.append([(ux + 2.7, 85.635), (126.0, 86.0), (129.2, 88.0)])
    tracks_bottom = [[(106.0, 90.54), (106.0, 100.0), (139.2, 100.0)], [(140.8, 100.0), (150.0, 100.0), (150.0, 90.0)]]
    vias = [(150.0, 90.0)] + ([(126.0, 95.0)] if head else [])
    zone_bottom = [(145.0, 74.0), (157.0, 74.0), (157.0, 84.0), (145.0, 84.0)] if head else None
    npth = [(104.0, 74.0, 3.2), (156.0, 74.0, 3.2), (104.0, 106.0, 3.2), (156.0, 106.0, 3.2)]
    return {"comps": comps, "tracks": {"top": tracks_top, "bottom": tracks_bottom}, "vias": vias,
            "zone": {"bottom": zone_bottom}, "npth": npth}


# ─── gerber / excellon writers ─────────────────────────────────────────────────────────────────

class Gerber:
    """Minimal RS-274X in mm, 4.6 format. Input in KiCad frame; y is negated (KiCad's plot convention)."""

    def __init__(self, name):
        self.lines = ["G04 kipr mock gerber*", f"%TF.FileFunction,{name}*%", "%FSLAX46Y46*%", "%MOMM*%", "%LPD*%"]
        self.apertures = {}
        self.body = []

    @staticmethod
    def c(v):
        return str(int(round(v * 1e6)))

    def xy(self, x, y):
        return f"X{self.c(x)}Y{self.c(-y)}"

    def ap(self, shape, w, h=None):
        key = (shape, round(w, 4), round(h or w, 4))
        if key not in self.apertures:
            code = 10 + len(self.apertures)
            self.apertures[key] = code
            self.lines.append(f"%ADD{code}C,{w:.4f}*%" if shape == "C" else f"%ADD{code}R,{w:.4f}X{h:.4f}*%")
        return self.apertures[key]

    def use(self, code):
        self.body.append(f"D{code}*")

    def flash(self, shape, x, y, w, h=None):
        self.use(self.ap(shape, w, h))
        self.body.append(f"{self.xy(x, y)}D03*")

    def path(self, pts, width):
        self.use(self.ap("C", width))
        self.body.append(f"{self.xy(*pts[0])}D02*")
        for p in pts[1:]:
            self.body.append(f"{self.xy(*p)}D01*")

    def rect_outline(self, x, y, w, h, width):
        self.path([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], width)

    def region(self, pts):
        self.body.append("G36*")
        self.body.append(f"{self.xy(*pts[0])}D02*")
        for p in pts[1:] + [pts[0]]:
            self.body.append(f"G01{self.xy(*p)}D01*")
        self.body.append("G37*")

    def text(self):
        return "\n".join(self.lines + self.body + ["M02*"]) + "\n"


def excellon(holes, plated):
    tools = sorted({round(d, 3) for (_, _, d) in holes})
    out = ["M48", f"; kipr mock {'PTH' if plated else 'NPTH'} drill", "FMAT,2", "METRIC"]
    out += [f"T{i + 1}C{d:.3f}" for i, d in enumerate(tools)]
    out += ["%", "G90", "G05"]
    for i, d in enumerate(tools):
        out.append(f"T{i + 1}")
        out += [f"X{x:.3f}Y{-y:.3f}" for (x, y, dd) in holes if round(dd, 3) == d]
    out.append("M30")
    return "\n".join(out) + "\n"


# ─── SVG writers ───────────────────────────────────────────────────────────────────────────────

PAGE = (297.0, 210.0)


def svg_doc(body, w=PAGE[0], h=PAGE[1]):
    return (f'<?xml version="1.0" standalone="no"?>\n<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">\n{body}</svg>\n')


def layer_svg(b: dict, layer: str, color: str) -> str:
    """Per-layer SVG in page mode: viewBox units = KiCad mm, like `kicad-cli pcb export svg`."""
    parts = []
    side = "top" if layer.startswith("F.") else "bottom"
    kind = layer.split(".")[1] if "." in layer else layer
    for c in b["comps"]:
        on_side = c["side"] == side or c.get("tht")
        if kind in ("Cu", "Mask", "Paste") and on_side:
            if kind == "Paste" and c.get("tht"):
                continue
            grow = 0.1 if kind == "Mask" else 0.0
            for (x, y, w, h) in c["pads"]:
                parts.append(f'<rect x="{x - w / 2 - grow:.4f}" y="{y - h / 2 - grow:.4f}" width="{w + 2 * grow:.4f}" height="{h + 2 * grow:.4f}" fill="{color}"/>')
        if kind == "SilkS" and c["side"] == side:
            x, y, w, h = c["body"]
            parts.append(f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" fill="none" stroke="{color}" stroke-width="0.15"/>')
    if kind == "Cu":
        for t in b["tracks"][side]:
            pts = " ".join(f"{x:.4f},{y:.4f}" for x, y in t)
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="0.25" stroke-linecap="round" stroke-linejoin="round"/>')
        for (x, y) in b["vias"]:
            parts.append(f'<circle cx="{x}" cy="{y}" r="0.4" fill="{color}"/>')
        z = b["zone"].get(side)
        if z:
            parts.append(f'<polygon points="{" ".join(f"{x},{y}" for x, y in z)}" fill="{color}" fill-opacity="0.8"/>')
    if layer == "Edge.Cuts":
        x, y, w, h = BOARD
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{color}" stroke-width="0.1"/>')
    return svg_doc("\n".join(parts) + "\n")


def layer_gerber(b: dict, layer: str) -> str:
    g = Gerber(layer.replace(".", "_"))
    side = "top" if layer.startswith("F.") else "bottom"
    kind = layer.split(".")[1] if "." in layer else layer
    if layer == "Edge.Cuts":
        g.rect_outline(*BOARD, 0.1)
        return g.text()
    for c in b["comps"]:
        on_side = c["side"] == side or c.get("tht")
        if kind in ("Cu", "Mask", "Paste") and on_side:
            if kind == "Paste" and c.get("tht"):
                continue
            grow = 0.1 if kind == "Mask" else 0.0
            for (x, y, w, h) in c["pads"]:
                if c.get("tht"):
                    g.flash("C", x, y, w + 2 * grow)
                else:
                    g.flash("R", x, y, w + 2 * grow, h + 2 * grow)
        if kind == "SilkS" and c["side"] == side:
            g.rect_outline(*c["body"], 0.15)
    if kind == "Cu":
        for t in b["tracks"][side]:
            g.path(t, 0.25)
        for (x, y) in b["vias"]:
            g.flash("C", x, y, 0.8)
        z = b["zone"].get(side)
        if z:
            g.region(z)
    if kind == "Mask":
        for (x, y) in b["vias"]:
            pass  # tented vias
        for (x, y, d) in b["npth"]:
            g.flash("C", x, y, d + 0.2)
    return g.text()


def drills(b: dict):
    pth = [(x, y, d) for c in b["comps"] for (x, y, d) in c.get("drills", [])] + [(x, y, 0.4) for (x, y) in b["vias"]]
    return excellon(pth, True), excellon(b["npth"], False)


LAYERS = [
    ("F.Cu", "copper", "top", "#C83434"), ("B.Cu", "copper", "bottom", "#4D7FC4"),
    ("F.Mask", "mask", "top", "#D864FF"), ("B.Mask", "mask", "bottom", "#02FFEE"),
    ("F.Paste", "paste", "top", "#B4A0A0"), ("F.SilkS", "silk", "top", "#F2EDA1"), ("B.SilkS", "silk", "bottom", "#E8B2A7"),
    ("Edge.Cuts", "outline", "none", "#D0D200"),
]


def write_pcb(out: Path, slug: str, sides: list[str], changed: set[str]) -> dict:
    layers = []
    for (lid, kind, side, color) in LAYERS:
        entry = {"id": lid, "kind": kind, "side": side, "status": status_for(sides, lid in changed), "base": None, "head": None}
        for s in sides:
            b = board(s)
            d = out / "p" / slug / "pcb" / s
            d.mkdir(parents=True, exist_ok=True)
            fn = lid.replace(".", "_")
            (d / f"{fn}.gbr").write_text(layer_gerber(b, lid))
            (d / f"{fn}.svg").write_text(layer_svg(b, lid, color))
            entry[s] = {"gerber": f"p/{slug}/pcb/{s}/{fn}.gbr", "svg": f"p/{slug}/pcb/{s}/{fn}.svg"}
        layers.append(entry)
    for did, plated in (("PTH", True), ("NPTH", False)):
        entry = {"id": did, "kind": "drill", "side": "none", "status": status_for(sides, did in changed), "base": None, "head": None}
        for s in sides:
            pth, npth = drills(board(s))
            d = out / "p" / slug / "pcb" / s
            (d / f"{did}.drl").write_text(pth if plated else npth)
            entry[s] = {"gerber": f"p/{slug}/pcb/{s}/{did}.drl", "svg": None}
        layers.append(entry)
    return {
        "board": {"size_mm": [BOARD[2], BOARD[3]], "origin_mm": [BOARD[0], BOARD[1]], "gerber_origin_mm": [0, 0],
                  "thickness_mm": 1.6, "copper_layers": 2, "mask_color": "green", "silk_color": "white", "finish": "ENIG"},
        "layers": layers,
        "gbrjob": {"base": None, "head": None},
        "changes": [],
    }


def status_for(sides, changed):
    if sides == ["head"]:
        return "added"
    if sides == ["base"]:
        return "removed"
    return "modified" if changed else "unchanged"


# ─── schematic sheets ──────────────────────────────────────────────────────────────────────────

def sheet_svg(title: str, symbols: list[tuple], wires: list[list[tuple]], texts: list[tuple] = ()) -> str:
    """A4 sheet like `kicad-cli sch export svg`: drawing-sheet frame + title block + symbols."""
    w, h = PAGE
    p = [f'<rect x="10" y="10" width="{w - 20}" height="{h - 20}" fill="none" stroke="#840000" stroke-width="0.15"/>',
         f'<rect x="{w - 120}" y="{h - 40}" width="110" height="30" fill="none" stroke="#840000" stroke-width="0.15"/>',
         f'<text x="{w - 115}" y="{h - 28}" font-size="4" fill="#840000" font-family="sans-serif">{title}</text>',
         f'<text x="{w - 115}" y="{h - 18}" font-size="2.5" fill="#840000" font-family="sans-serif">kipr mock sheet</text>']
    for (ref, value, x, y) in symbols:
        p.append(f'<rect x="{x}" y="{y}" width="10" height="4" fill="#FFFFC2" stroke="#840000" stroke-width="0.25"/>')
        p.append(f'<text x="{x}" y="{y - 1.2}" font-size="2" fill="#006464" font-family="sans-serif">{ref}</text>')
        p.append(f'<text x="{x + 1}" y="{y + 7.2}" font-size="2" fill="#006464" font-family="sans-serif">{value}</text>')
    for wpts in wires:
        p.append(f'<polyline points="{" ".join(f"{a},{b}" for a, b in wpts)}" fill="none" stroke="#009600" stroke-width="0.25"/>')
    for (t, x, y) in texts:
        p.append(f'<text x="{x}" y="{y}" font-size="2.5" fill="#000084" font-family="sans-serif">{t}</text>')
    return svg_doc("\n".join(p) + "\n")


def sheets(side: str) -> dict:
    head = side == "head"
    root = sheet_svg("Root", [("U1", "MCU", 120 + (15 if head else 0), 80), ("R1", "10k", 60, 60), ("R2", "4.7k" if head else "10k", 60, 90)],
                     [[(70, 62), (120 + (15 if head else 0), 82)], [(70, 92), (100, 92), (100, 84)]])
    power = sheet_svg("Power", [("U2", "LDO", 100, 100), ("C1", "100n", 150, 100)],
                      [[(110, 102), (150, 102)]] + ([[(160, 102), (200, 102), (200, 130)]] if head else []),
                      [("+3V3", 200, 132)] if head else [])
    conn = sheet_svg("Connectors", [("J1", "Conn_01x02", 50, 50)], [[(60, 52), (90, 52)]])
    out = {"root": root, "power": power, "connectors": conn}
    if head:
        out["sensors"] = sheet_svg("Sensors", [("U5", "BME280", 80, 70), ("C2", "1u", 120, 70)], [[(90, 72), (120, 72)]])
    else:
        out["legacy"] = sheet_svg("Legacy", [("U9", "OLD", 80, 70)], [[(90, 72), (140, 72)]])
    return out


# ─── project records ───────────────────────────────────────────────────────────────────────────

def write_sheets(out, slug, sides, meta):
    result = []
    content = {s: sheets(s) for s in sides}
    for sid, title, file, page, status, changes in meta:
        entry = {"id": sid, "title": title, "file": file, "page": page, "status": status, "base": None, "head": None,
                 "size_mm": list(PAGE), "changes": changes}
        for s in sides:
            key = sid.split("/")[-1]
            if key in content[s]:
                d = out / "p" / slug / "sch" / s
                d.mkdir(parents=True, exist_ok=True)
                fn = sid.replace("/", "__") + ".svg"
                (d / fn).write_text(content[s][key])
                entry[s] = f"p/{slug}/sch/{s}/{fn}"
        result.append(entry)
    return {"sheets": result}


def demo_board(out: Path) -> dict:
    slug = "demo_board"
    sch = write_sheets(out, slug, ["base", "head"], [
        ("root", "Root", "demo_board.kicad_sch", "1", "modified", [
            {"kind": "symbol", "ref": "R2", "what": "value", "base": "10k", "head": "4.7k", "bbox_mm": [58.5, 87.5, 13, 11]},
            {"kind": "symbol", "ref": "U1", "what": "moved", "base": [120, 80], "head": [135, 80], "bbox_mm": [118, 77.5, 29, 11]},
            {"kind": "wire", "what": "rerouted", "bbox_mm": [69, 61, 67, 22]},
        ]),
        ("root/power", "Power", "power.kicad_sch", "2", "modified", [
            {"kind": "wire", "what": "added", "bbox_mm": [159, 101, 42, 30]},
            {"kind": "label", "ref": "+3V3", "what": "added", "bbox_mm": [199, 129.5, 8, 3.5]},
        ]),
        ("root/connectors", "Connectors", "connectors.kicad_sch", "3", "unchanged", []),
        ("root/sensors", "Sensors", "sensors.kicad_sch", "4", "added", [
            {"kind": "symbol", "ref": "U5", "what": "added", "bbox_mm": [78, 67, 14, 12]},
            {"kind": "symbol", "ref": "C2", "what": "added", "bbox_mm": [118, 67, 14, 12]},
        ]),
        ("root/legacy", "Legacy", "legacy.kicad_sch", "4", "removed", [
            {"kind": "symbol", "ref": "U9", "what": "removed", "bbox_mm": [78, 67, 14, 12]},
        ]),
    ])
    pcb = write_pcb(out, slug, ["base", "head"], {"F.Cu", "B.Cu", "F.Mask", "F.Paste", "F.SilkS", "PTH"})
    for side in ("base", "head"):
        write_glb(out / "p" / slug / "3d" / f"{side}.glb", board(side))
    pcb["changes"] = [
        {"kind": "footprint", "ref": "U1", "what": "moved", "layer": "F.Cu", "bbox_mm": [115.3, 82.2, 8.4, 5.6], "detail": "+3.00 mm x"},
        {"kind": "footprint", "ref": "C2", "what": "added", "layer": "F.Cu", "bbox_mm": [134.6, 91.3, 2.9, 1.4]},
        {"kind": "track", "what": "rerouted", "layer": "F.Cu", "bbox_mm": [120, 85, 12, 5], "detail": "net /SDA"},
        {"kind": "zone", "what": "added", "layer": "B.Cu", "bbox_mm": [145, 74, 12, 10], "detail": "GND pour"},
        {"kind": "via", "what": "added", "layer": "B.Cu", "bbox_mm": [125.6, 94.6, 0.8, 0.8]},
        {"kind": "text", "what": "value changed", "ref": "R2", "detail": "no geometry change"},
        {"kind": "footprint", "what": "model_format", "whats": ["model_format"], "ref": "J1", "layer": "F.Cu", "layers": [],
         "bbox_mm": [138.7, 76.7, 2.6, 5.1], "minor": True, "detail": "3D model format .wrl -> .step"},
        {"kind": "footprint", "what": "model_format", "whats": ["model_format"], "ref": "C1", "layer": "F.Cu", "layers": [],
         "bbox_mm": [104.6, 86.3, 2.9, 1.4], "minor": True, "detail": "3D model format .wrl -> .step"},
    ]
    pcb["minor_groups"] = [{"what": "model_format", "detail": "3D model format .wrl -> .step", "count": 2, "refs": ["C1", "J1"]}]
    comps = []
    for ref, st, what in (("U1", "moved", ["position"]), ("R2", "changed", ["value"]), ("C2", "added", []),
                          ("R1", "unchanged", []), ("C1", "minor", ["model_format"]), ("J1", "minor", ["model_format"])):
        def pos(side):
            if st == "added" and side == "base":
                return None
            c = next((c for c in board(side)["comps"] if c["ref"] == ref), None)
            return c and {"x": c["x"], "y": c["y"], "rot": c["rot"], "side": c["side"], "footprint": c["fp"], "value": c["value"], "model": None}
        c = {"ref": ref, "status": "changed" if st == "minor" else st, "base": pos("base"), "head": pos("head"), "what": what}
        if st == "minor":
            c["minor"] = True
            c["base"] = c["base"] and {**c["base"], "model": f"${{KICAD6_3DMODEL_DIR}}/Mock.3dshapes/{ref}.wrl"}
            c["head"] = c["head"] and {**c["head"], "model": f"${{KICAD6_3DMODEL_DIR}}/Mock.3dshapes/{ref}.step"}
        comps.append(c)
    return {
        "slug": slug, "name": "demo_board", "path": "boards/demo_board", "status": "modified",
        "summary": {"sheets_changed": 4, "layers_changed": 6, "components": {"added": 1, "removed": 0, "moved": 1, "changed": 1, "minor": 2},
                    "nets_changed": 3, "erc": {"new": 1, "fixed": 1}, "drc": {"new": 2, "fixed": 1}},
        "schematic": sch, "pcb": pcb,
        "pcba3d": {"base": {"glb": f"p/{slug}/3d/base.glb"}, "head": {"glb": f"p/{slug}/3d/head.glb"}, "components": comps},
        "bom": {"rows": [
            {"key": "R2", "refs": ["R2"], "status": "changed", "base": {"value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric", "fields": {"MPN": "RC0603FR-0710KL"}},
             "head": {"value": "4.7k", "footprint": "Resistor_SMD:R_0603_1608Metric", "fields": {"MPN": "RC0603FR-074K7L"}}, "what": ["value", "MPN"]},
            {"key": "C2", "refs": ["C2"], "status": "added", "base": None, "head": {"value": "1u", "footprint": "Capacitor_SMD:C_0603_1608Metric", "fields": {"MPN": "GRM188R61A105KA61D"}}, "what": []},
            {"key": "U9", "refs": ["U9"], "status": "removed", "base": {"value": "OLD", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "fields": {}}, "head": None, "what": []},
            {"key": "C1", "refs": ["C1"], "status": "changed", "minor": True, "what": ["fields_minor"],
             "fields_changed": {"significant": [], "minor": ["Standard Cost"]},
             "base": {"value": "100n", "footprint": "Capacitor_SMD:C_0603_1608Metric", "fields": {"MPN": "CL10B104", "Standard Cost": "0.01"}},
             "head": {"value": "100n", "footprint": "Capacitor_SMD:C_0603_1608Metric", "fields": {"MPN": "CL10B104", "Standard Cost": "0.02", "Sim.Library": ""}}},
            {"key": "R1", "refs": ["R1"], "status": "unchanged", "base": {"value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric", "fields": {"MPN": "RC0603FR-0710KL"}},
             "head": {"value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric", "fields": {"MPN": "RC0603FR-0710KL"}}, "what": []},
            {"key": "U1", "refs": ["U1"], "status": "unchanged", "base": {"value": "MCU", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "fields": {}},
             "head": {"value": "MCU", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "fields": {}}, "what": []},
        ]},
        "netlist": {"changes": [
            {"net": "/SDA", "status": "modified", "added": ["C2.1"], "removed": ["R2.2"], "renamed_from": None},
            {"net": "/+3V3", "status": "added", "added": ["U2.3", "C2.2"], "removed": [], "renamed_from": None},
            {"net": "/LEGACY_EN", "status": "removed", "added": [], "removed": ["U9.1"], "renamed_from": None},
            {"net": "/SCL", "status": "renamed", "added": [], "removed": [], "renamed_from": "/I2C_CLK"},
        ]},
        "checks": {
            "erc": {"base_count": 3, "head_count": 3,
                    "new": [{"severity": "warning", "type": "pin_not_connected", "description": "Pin not connected", "items": ["U5 pin 3 (SDO)"], "pos_mm": [85.0, 72.0], "sheet": "root/sensors"}],
                    "fixed": [{"severity": "error", "type": "power_pin_not_driven", "description": "Input power pin not driven", "items": ["U9 pin 8"], "pos_mm": [80.0, 70.0], "sheet": "root/legacy"}],
                    "report": {"base": None, "head": None}},
            "drc": {"base_count": 5, "head_count": 6,
                    "new": [{"severity": "error", "type": "clearance", "description": "Clearance violation (0.15 mm < 0.2 mm)", "items": ["Track /SDA", "Pad C2.1"], "pos_mm": [135.3, 91.6]},
                            {"severity": "warning", "type": "silk_overlap", "description": "Silkscreen overlap", "items": ["U1 silk", "R2 silk"], "pos_mm": [124.0, 84.0]}],
                    "fixed": [{"severity": "warning", "type": "track_dangling", "description": "Track has unconnected end", "items": ["Track /OLD"], "pos_mm": [126.0, 86.0]}],
                    "report": {"base": None, "head": None}},
        },
        "errors": ["kicad-cli: STEP export skipped (mock)"],
    }


def write_glb(path: Path, b: dict) -> None:
    """A tiny GLB laid out like `kicad-cli pcb export glb` (docs/CONTRACT-project.md): metres, +Y up,
    x = kicad_x / 1000, z = kicad_y / 1000; a root node with one node per component named by its
    ref (the model mesh is a child node) and the board body (mesh name `<board>_PCB`)."""
    import struct
    bx, by, bw, bh = BOARD
    t = 1.6

    def box(x0, y0, z0, x1, y1, z1):
        v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        f = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6, 1, 2, 6, 1, 6, 5, 3, 0, 4, 3, 4, 7]
        return [c / 1000 for p in v for c in p], f

    meshes = [("mock_PCB", box(bx, 0, by, bx + bw, t, by + bh), 0)]
    for c in b["comps"]:
        x, y, w, h = c["body"]
        y0, y1 = (t, t + 1.0) if c["side"] == "top" else (-1.0, 0)
        cx, cz = c["x"], c["y"]  # model boxes relative to the component node
        meshes.append((c["ref"], box(x - cx, y0, y - cz, x + w - cx, y1, y + h - cz), 1))
    buf, views, accessors, gl_meshes = b"", [], [], []
    for name, (pos, idx), mat in meshes:
        for data, fmt, typ, comp, target in ((pos, "f", "VEC3", 5126, 34962), (idx, "I", "SCALAR", 5125, 34963)):
            raw = struct.pack(f"<{len(data)}{fmt}", *data)
            views.append({"buffer": 0, "byteOffset": len(buf), "byteLength": len(raw), "target": target})
            acc = {"bufferView": len(views) - 1, "componentType": comp, "count": len(data) // (3 if typ == "VEC3" else 1), "type": typ}
            if typ == "VEC3":
                acc["min"] = [min(data[i::3]) for i in range(3)]
                acc["max"] = [max(data[i::3]) for i in range(3)]
            accessors.append(acc)
            buf += raw + b"\0" * (-len(raw) % 4)
        gl_meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1, "material": mat}]})
    nodes = [{"name": "mock", "children": []}, {"name": "=>[0:1:1:900]", "mesh": 0}]
    nodes[0]["children"].append(1)
    for i, c in enumerate(b["comps"], start=1):
        nodes.append({"name": c["ref"], "translation": [c["x"] / 1000, 0, c["y"] / 1000], "children": [len(nodes) + 1]})
        nodes[0]["children"].append(len(nodes) - 1)
        nodes.append({"name": f"=>[0:1:1:{i}]", "mesh": i})
    doc = {"asset": {"version": "2.0", "generator": "kipr tests/web-project mock"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": nodes, "meshes": gl_meshes, "accessors": accessors, "bufferViews": views,
           "buffers": [{"byteLength": len(buf)}],
           "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.1, 0.35, 0.2, 1]}},
                         {"pbrMetallicRoughness": {"baseColorFactor": [0.15, 0.15, 0.17, 1]}}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(buf), b"BIN\0") + buf
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def sensor_breakout(out: Path) -> dict:
    slug = "sensor_breakout"
    sch = write_sheets(out, slug, ["head"], [("root/sensors", "Sensors", "sensor_breakout.kicad_sch", "1", "added", [])])
    pcb = write_pcb(out, slug, ["head"], set())
    return {"slug": slug, "name": "sensor_breakout", "path": "boards/sensor_breakout", "status": "added",
            "summary": {"sheets_changed": 1, "layers_changed": 10, "components": {"added": 7, "removed": 0, "moved": 0, "changed": 0},
                        "nets_changed": 5, "erc": {"new": 0, "fixed": 0}, "drc": {"new": 0, "fixed": 0}},
            "schematic": sch, "pcb": pcb, "pcba3d": None,
            "bom": {"rows": [{"key": "U5", "refs": ["U5"], "status": "added", "base": None, "head": {"value": "BME280", "footprint": "Package_LGA:Bosch_LGA-8", "fields": {}}, "what": []}]},
            "netlist": {"changes": []}, "checks": {"erc": None, "drc": None}, "errors": []}


def old_adapter(out: Path) -> dict:
    slug = "old_adapter"
    sch = write_sheets(out, slug, ["base"], [("root/legacy", "Legacy", "old_adapter.kicad_sch", "1", "removed", [])])
    return {"slug": slug, "name": "old_adapter", "path": "boards/old_adapter", "status": "removed",
            "summary": {"sheets_changed": 1, "layers_changed": 0, "components": {"added": 0, "removed": 1, "moved": 0, "changed": 0},
                        "nets_changed": 0, "erc": {"new": 0, "fixed": 0}, "drc": {"new": 0, "fixed": 0}},
            "schematic": sch, "pcb": None, "pcba3d": None, "bom": None, "netlist": None, "checks": {"erc": None, "drc": None}, "errors": []}


def make(out: Path) -> dict:
    if (out / "p").exists():
        shutil.rmtree(out / "p")
    out.mkdir(parents=True, exist_ok=True)
    review = {
        "version": 1,
        "tool": {"name": "kipr", "version": "0.1.0", "kicad": "10.0.6"},
        "base": {"sha": "1111111aaaaaaa2222222bbbbbbb3333333ccccc", "ref": "main", "short": "1111111"},
        "head": {"sha": "4444444ddddddd5555555eeeeeee6666666fffff", "ref": "feature/sensors", "short": "4444444"},
        "repo": {"url": "https://github.com/example/boards", "blob": "https://github.com/example/boards/blob/{sha}/{path}"},
        "projects": [demo_board(out), sensor_breakout(out), old_adapter(out)],
    }
    (out / "project-review.json").write_text(json.dumps(review, indent=1))
    return review


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--site", action="store_true", help="also copy the viewer in (kipr.project.site)")
    a = ap.parse_args(argv)
    make(a.out)
    if a.site:
        sys.path.insert(0, str(ROOT))
        from kipr.project import site
        site.build_site(a.out)
        site.build_offline(a.out)
    print(f"make_mock: wrote {a.out}/project-review.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
