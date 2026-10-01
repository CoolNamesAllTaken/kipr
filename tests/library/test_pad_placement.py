"""Pads and 3D models land where KiCad puts them (kicad-libs PR #13: RP2040-Zero castellated pads were drawn
0.65 mm too far in, because the pad shape offset `(drill (offset x y))` was dropped / applied to the hole).

fixtures/pad_placement/golden.json holds KiCad's own numbers (pcbnew ShapePos, effective-shape bbox, hole
position, FP_3DMODEL offset/rotate/scale), made by fixtures/pad_placement/make_golden.py. The footprints:
RP2040-Zero_Castellated (SMD pads with shape offsets + a model offset), the stock R_0603_1608Metric,
SMA_Amphenol_132289_EdgeMount (rotated pads, model offset + rotation), USB_C CNCTech (THT pads whose
shape offset must move the copper but not the hole).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from kipr.common.sexpr import parse
from kipr.library.render import fp as fpmod
from kipr.library.render import model3d
from kipr.library.render.geom import rot

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "pad_placement"
GOLDEN = json.loads((FIX / "golden.json").read_text())
TOL = 0.01  # mm; KiCad's polygonised arcs vs ours


def geom_json(fp):
    return fpmod.geom_json(fp, fpmod.viewbox(fpmod.footprint_bbox(fp)))


def footprints():
    for name in sorted(GOLDEN):
        yield name, fpmod.Footprint(parse((FIX / f"{name}.kicad_mod").read_text())), GOLDEN[name]


class PadPlacement(unittest.TestCase):
    def assertNear(self, a, b, msg, tol=TOL):
        self.assertEqual(len(a), len(b), msg)
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, delta=tol, msg=f"{msg}: {list(a)} != {list(b)}")

    def test_golden_covers_fixtures(self):
        self.assertEqual(sorted(GOLDEN), sorted(p.stem for p in FIX.glob("*.kicad_mod")))
        rp = GOLDEN["RP2040-Zero_Castellated"]["pads"]
        # the case that was wrong: copper 0.65 mm outward of `at`, overhanging the module edge like in KiCad
        self.assertEqual(rp[0]["copper_center"], [-8.27, -10.16])

    def test_parser_and_geom_json(self):
        for name, fp, gold in footprints():
            geom = geom_json(fp)
            self.assertEqual(len(geom["pads"]), len(gold["pads"]), name)
            for p, g in zip(geom["pads"], gold["pads"]):
                where = f"{name} pad {p['number']}"
                self.assertEqual(p["number"], g["number"], where)
                self.assertNear(p["at"][:2], g["at"], where)
                dx, dy = rot(*p["offset"], p["angle"])
                self.assertNear([p["at"][0] + dx, p["at"][1] + dy], g["copper_center"], where + " copper")
            for m, g in zip(fp.models, gold["models"]):
                for k in ("offset", "rotate", "scale"):
                    self.assertNear(m[k], g[k], f"{name} model {k}", tol=1e-6)

    def test_server_side_shapes(self):
        """model3d.py (GLB / 3D preview PNG) copper and holes."""
        for name, fp, gold in footprints():
            for p, g in zip(fp.pads, gold["pads"]):
                where = f"{name} pad {p['number']}"
                if p["shape"] != "custom":
                    self.assertNear(model3d.pad_geom(p).bounds, g["copper_bbox"], where + " copper bbox")
                dg = model3d.drill_geom(p)
                if g["hole_center"] is None:
                    self.assertIsNone(dg, where)
                else:
                    c = dg.centroid
                    self.assertNear([c.x, c.y], g["hole_center"], where + " hole")

    def test_svg_moves_copper_not_hole(self):
        fp = fpmod.Footprint(parse((FIX / "RP2040-Zero_Castellated.kicad_mod").read_text()))
        svg = fpmod._pad_svg(fp.pads[0], "#c83434")
        self.assertIn('transform="translate(-7.62 -10.16) translate(-0.65 0)"', svg)
        usb = fpmod.Footprint(parse((FIX / "USB_C_Receptacle_CNCTech_C-ARA1-AK51X.kicad_mod").read_text()))
        sh = next(p for p in usb.pads if p["drill"] and p["offset"] != (0.0, 0.0))
        self.assertIn("translate(0 -0.14)", fpmod._pad_svg(sh, "#c83434"))
        self.assertNotIn("-0.14", fpmod._hole_svg(sh))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_viewer(self):
        """web/library/js: the browser 3D view's pads, holes and model origins."""
        data = {name: {"geom": geom_json(fp), "models": fp.models} for name, fp, _ in footprints()}
        src = FIX.parent.parent / "viewer" / "pad_placement.mjs"
        r = subprocess.run(["node", str(src), "/dev/stdin"], input=json.dumps(data), capture_output=True, text=True,
                           check=True)
        got = json.loads(r.stdout)
        for name, gold in GOLDEN.items():
            for p, g in zip(got[name]["pads"], gold["pads"]):
                where = f"{name} pad {p['number']}"
                self.assertNear(p["copper_center"], g["copper_center"], where + " copper")
                if p["number"] and not any(q["shape"] == "custom" for q in data[name]["geom"]["pads"]):
                    self.assertNear(p["copper_bbox"], g["copper_bbox"], where + " copper bbox", tol=0.02)
                if g["hole_center"] is None:
                    self.assertIsNone(p["hole_center"], where)
                else:
                    self.assertNear(p["hole_center"], g["hole_center"], where + " hole")
            for m, g in zip(got[name]["models"], gold["models"]):
                self.assertNear(m, g["offset"], f"{name} model origin")


if __name__ == "__main__":
    unittest.main()
