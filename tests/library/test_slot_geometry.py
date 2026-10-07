"""Slotted holes (KiCad `(drill oval w h)`) are stadiums -- two semicircles joined by straight flanks, the long
axis along the larger of w/h and turned with the pad -- in the GLB export (board cutout, copper, plated barrel)
and the 2D SVG, never ellipses. The viewer's JS side is covered in tests/library/viewer/unit.test.mjs."""
from __future__ import annotations

import math
import re
import unittest

from shapely.geometry import LineString, Point, Polygon

from kipr.library.render import fp as fpmod
from kipr.library.render import model3d


def pad(w, h, angle, x=3.0, y=-2.0, kind="thru_hole"):
    return dict(x=x, y=y, angle=angle, w=w + 0.6, h=h + 0.6, shape="oval", offset=(0.0, 0.0),
                drill=dict(w=w, h=h, oval=True), type=kind, layers=["*.Cu", "*.Mask"], primitives=[],
                anchor="circle", chamfer=[], rratio=0.25, chamfer_ratio=0.0, delta=[0, 0], number="1")


def axis(p):
    """The slot's centre segment, PCB frame (KiCad turns pads CCW on screen, y down)."""
    half = abs(p["drill"]["w"] - p["drill"]["h"]) / 2
    a = math.radians(p["angle"]) + (0.0 if p["drill"]["w"] >= p["drill"]["h"] else math.pi / 2)
    dx, dy = half * math.cos(a), -half * math.sin(a)
    return LineString([(p["x"] - dx, p["y"] - dy), (p["x"] + dx, p["y"] + dy)])


class SlotGeometry(unittest.TestCase):
    def assert_stadium(self, g, seg, r, tol=2e-3):
        for x, y in g.exterior.coords:
            self.assertAlmostEqual(Point(x, y).distance(seg), r, delta=tol)
        exact = math.pi * r * r + 2 * r * seg.length
        self.assertAlmostEqual(g.area, exact, delta=exact * 0.01)
        # an ellipse of the same 2 x 1 extent has area pi*1*0.5 = 1.571; the stadium has 1.785
        self.assertGreater(g.area, math.pi * (r + seg.length / 2) * r * 1.05)

    def test_drill_1x2_every_orientation(self):
        for w, h in ((2.0, 1.0), (1.0, 2.0)):
            for angle in (0, 45, 90, 135, -30):
                p = pad(w, h, angle)
                seg = axis(p)
                self.assertAlmostEqual(seg.length, 1.0)  # straight flank length
                with self.subTest(w=w, h=h, angle=angle):
                    self.assert_stadium(model3d.drill_geom(p), seg, 0.5)

    def test_barrel_is_a_stadium_shell(self):
        p = pad(2.0, 1.0, 45)
        dg = model3d.drill_geom(p)
        ring = dg.buffer(0.025).difference(dg)  # what _build_scene extrudes for the plated barrel
        seg = axis(p)
        self.assert_stadium(Polygon(ring.exterior), seg, 0.525)
        self.assert_stadium(Polygon(ring.interiors[0]), seg, 0.5)

    def test_svg_hole_is_a_stadium(self):
        d = fpmod._hole_svg(pad(2.0, 1.0, 0))
        # two semicircular arcs of radius 0.5 joined by straight flanks, not one elliptical arc
        radii = {tuple(map(float, m)) for m in re.findall(r"A([-\d.]+)[ ,]([-\d.]+)", d)}
        self.assertEqual(radii, {(0.5, 0.5)}, d)


if __name__ == "__main__":
    unittest.main()
