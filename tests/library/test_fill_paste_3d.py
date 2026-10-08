"""Static 3D (GLB + preview PNG) options: `--3d-fill-holes MM` fills and caps plated round pad holes up to that
drill (no hole in the board or copper, no barrel), `--3d-hide-paste` leaves the paste out of the preview PNG.
The browser viewers' side is in tests/library/viewer/screenshot.py and tests/web-3d."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import trimesh

from boarddd.io.kicad.sexpr import parse
from boarddd.render import footprint as fpmod
from kipr.library.render import model3d

FIX = Path(__file__).resolve().parent / "fixtures" / "pad_placement"
USB = "USB_C_Receptacle_CNCTech_C-ARA1-AK51X"   # 0.4 mm round PTH pads, oval shell slots, SMD paste


def footprint():
    return fpmod.Footprint(parse((FIX / f"{USB}.kicad_mod").read_text()))


class FilledHoles(unittest.TestCase):
    def test_filled_is_plated_round_up_to_the_drill(self):
        fp = footprint()
        round_pth = [p for p in fp.pads if p["type"] == "thru_hole" and not p["drill"]["oval"]]
        slots = [p for p in fp.pads if p["type"] == "thru_hole" and p["drill"]["oval"]]
        self.assertTrue(round_pth and slots)
        self.assertTrue(all(model3d.filled(p, 0.4) for p in round_pth))
        self.assertFalse(any(model3d.filled(p, 0.39) for p in round_pth))
        self.assertFalse(any(model3d.filled(p, 5.0) for p in slots))
        self.assertFalse(any(model3d.filled(p, None) for p in fp.pads))
        self.assertTrue(all(model3d.drill_geom(p, 0.4) is None for p in round_pth))

    def test_glb_has_fewer_holes_and_barrels(self):
        fp = footprint()
        with tempfile.TemporaryDirectory() as td:
            open_glb, filled_glb = Path(td, "open.glb"), Path(td, "filled.glb")
            model3d.build_glb(fp, [], str(open_glb), include_models=False)
            model3d.build_glb(fp, [], str(filled_glb), include_models=False, fill_up_to=0.4)
            a, b = (trimesh.load(str(g), force="scene").geometry for g in (open_glb, filled_glb))
            # every filled hole closes the board: same outline, more board area (vertices drop with the holes)
            self.assertLess(len(b["PCB"].vertices), len(a["PCB"].vertices))
            self.assertLess(len(b["Holes"].faces), len(a["Holes"].faces))
            self.assertIn("F.Paste", a)


class PreviewHidesPaste(unittest.TestCase):
    def test_hide_paste_changes_the_preview(self):
        fp = footprint()
        with tempfile.TemporaryDirectory() as td:
            glb = Path(td, "fp.glb")
            model3d.build_glb(fp, [], str(glb), include_models=False)
            shown, hidden = Path(td, "shown.png"), Path(td, "hidden.png")
            model3d.render_preview(str(glb), str(shown), size=300)
            model3d.render_preview(str(glb), str(hidden), size=300, hide=(".Paste",))
            self.assertNotEqual(shown.read_bytes(), hidden.read_bytes())


if __name__ == "__main__":
    unittest.main()
