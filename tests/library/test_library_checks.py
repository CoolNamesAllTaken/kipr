"""Unit tests for the library review's checks stage (kipr.library.checks).

  python -m pytest tests/library
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "kicad-libs", "head")
sys.path.insert(0, HERE)

import fixture_repo  # noqa: E402
import lib_mock_out as make_mock_out  # noqa: E402

from kipr.common import sexpr  # noqa: E402
from kipr.library.checks import kicad_checks as kc  # noqa: E402
from kipr.library.checks import klc_utils  # noqa: E402
from kipr.library.checks import main as cr  # noqa: E402

FP_OK = """(footprint "R_0603_1608Metric"
	(version 20260206)
	(generator "pcbnew")
	(layer "F.Cu")
	(descr "Resistor 0603, https://example.com/r0603.pdf")
	(tags "resistor")
	(property "Reference" "REF**"
		(at 0 -1.43 0)
		(layer "F.SilkS")
	)
	(property "Value" "R_0603"
		(at 0 1.43 0)
		(layer "F.Fab")
	)
	(fp_line (start -0.8 -0.4) (end 0.8 -0.4) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
	(fp_line (start -0.8 0.4) (end 0.8 0.4) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
	(fp_rect (start -1.48 -0.73) (end 1.48 0.73) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
	(fp_line (start -0.24 -0.51) (end 0.24 -0.51) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
	(fp_text user "${REFERENCE}" (at 0 0 0) (layer "F.Fab"))
	(pad "1" smd roundrect (at -0.79 0) (size 0.88 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))
	(pad "2" smd roundrect (at 0.79 0) (size 0.88 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))
	(model "${KICAD_LIBS_DIR}/lib_3d/Custom_Resistor/R_0603.step"
		(offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0))
	)
)
"""

FP_BAD = """(footprint "Bad"
	(version 20260206)
	(layer "F.Cu")
	(property "Reference" "REF**" (at 0 -2 0) (layer "F.SilkS"))
	(property "Value" "Bad" (at 0 2 0) (layer "F.Fab"))
	(fp_line (start -2 0) (end 2 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
	(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Mask" "F.Paste"))
	(pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Mask"))
	(pad "2" smd rect (at 1 1.5) (size 1 1) (layers "F.Cu" "F.Mask"))
	(model "${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step"
		(offset (xyz 0 0 0)) (scale (xyz 2 1 1)) (rotate (xyz 0 0 45))
	)
)
"""

# THT, origin at the part centroid (between the pads) rather than on pad 1: KLC F7.2
FP_THT_CENTROID = """(footprint "PinHeader_1x02_P2.54mm_Vertical"
	(version 20260206)
	(generator "pcbnew")
	(layer "F.Cu")
	(descr "Through hole pin header, 1x02, 2.54mm pitch")
	(tags "Through hole pin header THT 1x02 2.54mm")
	(property "Reference" "REF**" (at 0 -2.8 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
	(property "Value" "PinHeader_1x02_P2.54mm_Vertical" (at 0 2.8 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
	(attr through_hole)
	(fp_rect (start -1.27 -2.54) (end 1.27 2.54) (stroke (width 0.1) (type solid)) (layer "F.Fab"))
	(fp_rect (start -1.8 -3.05) (end 1.8 3.05) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
	(fp_rect (start -1.33 -2.6) (end 1.33 2.6) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
	(fp_text user "${REFERENCE}" (at 0 0 90) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
	(pad "1" thru_hole rect (at 0 -1.27) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask"))
	(pad "2" thru_hole oval (at 0 1.27) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask"))
)
"""

SYM = """(kicad_symbol_lib
	(version 20251024)
	(generator "kicad_symbol_editor")
	(symbol "AMP1"
		(property "Reference" "U" (at 0 0 0))
		(property "Value" "AMP1" (at 0 0 0))
		(property "Footprint" "Custom_Test:R_0603_1608Metric" (at 0 0 0))
		(property "Datasheet" "" (at 0 0 0))
		(property "Description" "" (at 0 0 0))
		(symbol "AMP1_1_1"
			(pin input line (at -5.08 0 0) (length 2.54) (name "IN" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
			(pin power_in line (at 0 5.08 270) (length 2.54) (hide yes) (name "VDD") (number "3"))
			(pin output line (at 5.1 1.27 180) (length 2.54) (name "OUT") (number "2"))
			(pin passive line (at 5.08 -1.27 180) (length 2.54) (name "X") (number "2"))
		)
	)
)
"""


def _msgs(findings):
    return [f["message"] for f in findings]


class SexprTests(unittest.TestCase):
    def test_parse_lines_and_atoms(self):
        root = sexpr.parse(FP_OK)
        self.assertEqual(root.name, "footprint")
        self.assertEqual(root.atom(), "R_0603_1608Metric")
        pads = root.children("pad")
        self.assertEqual([p.atom() for p in pads], ["1", "2"])
        self.assertEqual(pads[0].line, FP_OK.splitlines().index(next(l for l in FP_OK.splitlines() if '(pad "1"' in l)) + 1)

    def test_escapes_and_flags(self):
        n = sexpr.parse('(a "x \\"y\\"" (hide yes) bare)')
        self.assertEqual(n.atom(0), 'x "y"')
        self.assertTrue(n.has_flag("hide"))
        self.assertTrue(sexpr.parse("(p hide)").has_flag("hide"))

    def test_unbalanced(self):
        with self.assertRaises(sexpr.ParseError):
            sexpr.parse("(a (b)")


class FootprintCheckTests(unittest.TestCase):
    def run_fp(self, text, model3d=None, start=10):
        fp = sexpr.parse(text)
        return kc.check_footprint(fp, kc.LineMap(fp.line, start), model3d)

    def test_clean_footprint(self):
        f, c = self.run_fp(FP_OK, [{"path_raw": "${KICAD_LIBS_DIR}/lib_3d/Custom_Resistor/R_0603.step", "exists": True}])
        self.assertEqual([x for x in f if x["severity"] != "info"], [], _msgs(f))
        self.assertTrue(all(x["result"] == "pass" for x in c), c)

    def test_bad_footprint(self):
        f, _ = self.run_fp(FP_BAD)
        msgs = " | ".join(_msgs(f))
        self.assertIn("no courtyard", msgs)
        self.assertIn("No fabrication-layer outline", msgs)
        self.assertIn("${REFERENCE}", msgs)
        self.assertIn("overlaps copper pad(s) 1, 2", msgs)
        self.assertIn("does not use `${KICAD_LIBS_DIR}", msgs)
        self.assertIn("scale", msgs)
        self.assertIn("not a multiple of 90", msgs)
        self.assertIn("Pad numbers used more than once: 2×2", msgs)
        self.assertIn("SMD pad 2 has no paste", msgs)
        self.assertIn("No datasheet URL", msgs)
        self.assertTrue(all(x["category"] == "klc" for x in f))
        # stock-library model paths cannot be verified -> no "does not exist" error
        self.assertNotIn("does not exist", msgs)

    def test_model_missing_in_repo(self):
        f, _ = self.run_fp(FP_OK, [{"path_raw": "${KICAD_LIBS_DIR}/lib_3d/Custom_Resistor/R_0603.step", "exists": False}])
        self.assertTrue(any(x["severity"] == "error" and "does not exist" in x["message"] for x in f))

    def test_line_mapping(self):
        f, _ = self.run_fp(FP_BAD, start=100)
        silk = next(x for x in f if "overlaps" in x["message"])
        self.assertEqual(silk["line"], 100 + 6 - 1)  # fp_line is on line 6 of the source

    def test_courtyard_too_small(self):
        text = FP_OK.replace("(start -1.48 -0.73) (end 1.48 0.73)", "(start -1.0 -0.5) (end 1.0 0.5)")
        f, _ = self.run_fp(text)
        self.assertTrue(any(x["severity"] == "error" and "outside the courtyard" in x["message"] for x in f), _msgs(f))

    def test_rotated_pad_silk(self):
        pad = {"number": "1", "shape": "rect", "x": 0, "y": 0, "w": 2, "h": 0.4, "rot": 90}
        self.assertTrue(kc._seg_hits_pad((-0.1, -0.9), (0.1, -0.9), 0.06, pad))  # inside once rotated
        self.assertFalse(kc._seg_hits_pad((-0.9, -0.1), (-0.9, 0.1), 0.06, pad))


class ModelNameTests(unittest.TestCase):
    def test_package_dimension_mismatch(self):
        msg = kc.model_name_mismatch("SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm",
                                     "${KICAD10_3DMODEL_DIR}/Package_SO.3dshapes/SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.29x3mm.step")
        self.assertIn("EP2.41x3.3mm", msg)
        self.assertIn("EP2.29x3mm", msg)

    def test_no_false_positives(self):
        self.assertIsNone(kc.model_name_mismatch("R_0603_1608Metric", "x/R_0603_1608Metric.step"))
        self.assertIsNone(kc.model_name_mismatch("SW_SPST_Same-Sky_TS32_with-boss", "x/TS32-7-35-BK-B-260-RA-SMT-TR.STEP"))
        self.assertIsNone(kc.model_name_mismatch("QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm_ThermalVias",
                                                 "x/QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm.step"))
        self.assertIsNotNone(kc.model_name_mismatch("R_0603_1608Metric", "x/R_0805_2012Metric.wrl"))

    def test_in_footprint_check(self):
        text = FP_OK.replace("R_0603.step", "R_0603_1608Metric_EP1x1mm.step")
        fp = sexpr.parse(text)
        f, _ = kc.check_footprint(fp, kc.LineMap(1, 1), None)
        hit = [x for x in f if x["category"] == "3d-model"]
        self.assertEqual(len(hit), 1, _msgs(f))
        self.assertEqual(hit[0]["severity"], "warning")


class KlcUtilsTests(unittest.TestCase):
    JUNIT = """<testsuites><testsuite name="Footprint KLC Checks">
<testcase name="X - Warnings"><failure message="F6.3" type="WARNING">F6.3: Pad requirements for SMD footprints
    https://klc.kicad.org/footprint/f6/f6.3/
    Pad(s) potentially missing layers
       - Pad '9' missing layer 'Paste'</failure></testcase>
<testcase name="X - Errors"><failure message="F9.3" type="FAILURE">F9.3: Footprint 3D model requirements
    https://klc.kicad.org/footprint/f9/f9.3/
    3D model directory is different from footprint directory (found 'a.3dshapes', should be 'b.3dshapes')</failure>
<failure message="F5.3" type="FAILURE">F5.3: Courtyard layer requirements
    https://klc.kicad.org/footprint/f5/f5.3/
    Missing courtyard</failure></testcase>
</testsuite></testsuites>"""

    def test_parse_junit_filters_repo_conventions(self):
        import xml.etree.ElementTree as ET
        f = klc_utils.parse_junit(ET.fromstring(self.JUNIT))
        self.assertEqual([x["severity"] for x in f], ["info", "warning"])
        self.assertIn("Pad '9' missing layer 'Paste'", f[0]["message"])
        self.assertIn("Missing courtyard", f[1]["message"])
        self.assertTrue(all("3dshapes" not in x["message"] for x in f))

    JUNIT_F72 = """<testsuites><testsuite name="Footprint KLC Checks">
<testcase name="X - Errors"><failure message="F7.2" type="FAILURE">F7.2: For through-hole components, footprint anchor is set on pad 1
    https://klc.kicad.org/footprint/f7/f7.2/
    Pad '1' not located at origin
     - Set origin to location of Pad '1'</failure>
<failure message="F5.3" type="FAILURE">F5.3: Courtyard layer requirements
    https://klc.kicad.org/footprint/f5/f5.3/
    Missing courtyard</failure></testcase>
</testsuite></testsuites>"""

    def test_parse_ignore(self):
        self.assertEqual(klc_utils.parse_ignore(""), ())
        self.assertEqual(klc_utils.parse_ignore(None), ())
        self.assertEqual(klc_utils.parse_ignore(["f7.2, S4.1", "F7_2", " G1.07 "]), ("F7.2", "G1.7", "S4.1"))
        for bad in ("F7", "7.2", "F7.2.1", "Pad1", "F7.2;rm"):
            with self.assertRaises(ValueError, msg=bad):
                klc_utils.parse_ignore(bad)

    def test_parse_junit_ignore(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.JUNIT_F72)
        f = klc_utils.parse_junit(root)
        self.assertEqual([x["message"].split(":")[0] for x in f], ["KLC F7.2", "KLC F5.3"])
        self.assertIn("Pad '1' not located at origin", f[0]["message"])
        ignored = []
        f = klc_utils.parse_junit(root, ("F7.2",), ignored)
        self.assertEqual([x["message"].split(":")[0] for x in f], ["KLC F5.3"])
        self.assertEqual(ignored, ["F7.2"])
        self.assertFalse(any("origin" in x["message"] for x in f))

    @unittest.skipUnless(os.environ.get("CR_KLC_UTILS"), "set CR_KLC_UTILS to a kicad-library-utils checkout")
    def test_real_checker_ignore_f72(self):
        args = (os.environ["CR_KLC_UTILS"], "footprint", "Custom_Connector", "PinHeader_1x02_P2.54mm_Vertical",
                FP_THT_CENTROID)
        f, err = klc_utils.run(*args)
        self.assertIsNone(err)
        self.assertTrue(any("KLC F7.2" in x["message"] and "not located at origin" in x["message"] for x in f), f)
        ignored = []
        g, err = klc_utils.run(*args, ignore=("F7.2",), ignored=ignored)
        self.assertIsNone(err)
        self.assertEqual(ignored, ["F7.2"])
        self.assertFalse(any("F7.2" in x["message"] or "origin" in x["message"] for x in g), g)
        self.assertEqual(len(g), len(f) - 1)   # everything else stays

    @unittest.skipUnless(os.environ.get("CR_KLC_UTILS"), "set CR_KLC_UTILS to a kicad-library-utils checkout")
    def test_real_checker(self):
        path = os.path.join(FIXTURES, "lib_fp/Custom_Package_SO.pretty/SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm.kicad_mod")
        if not os.path.isfile(path):
            self.skipTest("demo footprint not in this checkout")
        with open(path) as fh:
            text = fh.read()
        f, err = klc_utils.run(os.environ["CR_KLC_UTILS"], "footprint", "Custom_Package_SO",
                               "SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm", text)
        self.assertIsNone(err)
        self.assertTrue(any("F6.3" in x["message"] for x in f), f)
        # unparseable input is reported as an error, not as a clean pass
        f, err = klc_utils.run(os.environ["CR_KLC_UTILS"], "footprint", "Custom_Test", "Bad", FP_BAD)
        self.assertEqual(f, [])
        self.assertIn("could not parse", err)


class SymbolCheckTests(unittest.TestCase):
    def test_symbol(self):
        root = sexpr.parse(SYM)
        sym = kc.find_item_node(root, "symbol", "AMP1")
        f, c = kc.check_symbol(sym, kc.LineMap(sym.line, 50))
        msgs = " | ".join(_msgs(f))
        self.assertIn("Datasheet` property is empty", msgs)
        self.assertIn("Description` property is empty", msgs)
        self.assertIn("Pin 2 (OUT) at (5.1, 1.27) is off the 50 mil grid", msgs)
        self.assertIn("Pin 2 (X) at (5.08, -1.27) is on 50 mil but not 100 mil grid", msgs)
        grid = {x["message"].split(" at ")[0]: x["severity"] for x in f if "grid" in x["message"]}
        self.assertEqual(grid, {"Pin 2 (OUT)": "error", "Pin 2 (X)": "error"})
        self.assertIn("Pin number 2 is used by 2 pins", msgs)
        self.assertIn("Power pin 3 (VDD) is hidden", msgs)
        # line mapping: symbol opens at source line 4 -> file line 50
        hidden = next(x for x in f if "hidden" in x["message"])
        self.assertEqual(hidden["line"], 50 + (SYM.splitlines().index(next(l for l in SYM.splitlines() if '"VDD"' in l)) + 1) - 4)

    def test_pin_grid(self):
        def grid(*xy):
            pins = "".join(f'(pin passive line (at {x} {y} 0) (length 2.54) (name "P{i}") (number "{i}"))'
                           for i, (x, y) in enumerate(xy, 1))
            text = SYM.replace(SYM[SYM.index("\t\t(symbol \"AMP1_1_1\""):SYM.index("\t)\n)")],
                               f'\t\t(symbol "AMP1_1_1" {pins})\n')
            sym = kc.find_item_node(sexpr.parse(text), "symbol", "AMP1")
            f, c = kc.check_symbol(sym, kc.LineMap(sym.line, 1))
            return ([(x["severity"], x["message"]) for x in f if "grid" in x["message"]],
                    next(x for x in c if x["name"] == "Pins on 100 mil grid"))

        found, chk = grid((0, 0), (-5.08, 2.54), (7.62, -10.16))   # all on 100 mil
        self.assertEqual(found, [])
        self.assertEqual(chk["result"], "pass")
        found, chk = grid((0, 0), (1.27, 0), (2.54, -3.81))        # 50 mil only: errors (KLC S4.1)
        self.assertEqual([s for s, _ in found], ["error", "error"])
        self.assertIn("Pin 2 (P2) at (1.27, 0.0) is on 50 mil but not 100 mil grid.", [m for _, m in found])
        self.assertEqual((chk["result"], chk["detail"]), ("fail", "2, 3"))
        found, chk = grid((0.5, 0),)                                  # off 50 mil: still an error
        self.assertEqual(found, [("error", "Pin 1 (P1) at (0.5, 0.0) is off the 50 mil grid.")])
        self.assertEqual(chk["result"], "fail")

    def test_pairing(self):
        sym = kc.find_item_node(sexpr.parse(SYM), "symbol", "AMP1")
        pins = kc.parse_pins(sym)
        pads = kc.parse_pads(sexpr.parse(FP_OK))
        f, c = kc.check_pairing(pins, pads, "symbol:X:AMP1", "footprint:Y:R", None)
        self.assertIn("Symbol pins 3 have no matching pad", " ".join(_msgs(f)))
        self.assertEqual(c[0]["result"], "fail")


class PathSafetyTests(unittest.TestCase):
    def test_safe_join(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cr.safe_join(d, "../etc/passwd"))
            self.assertIsNone(cr.safe_join(d, "/etc/passwd"))
            self.assertIsNone(cr.safe_join(d, "items/../../x"))
            self.assertEqual(cr.safe_join(d, "items/a.png"), os.path.join(os.path.realpath(d), "items", "a.png"))

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            os.symlink(other, os.path.join(d, "link"))
            self.assertIsNone(cr.safe_join(d, "link/file"))


# ---------------------------------------------------------------------------
# end-to-end on a mock OUT built from this repo's PR range (or synthetic data)
# ---------------------------------------------------------------------------

def build_synthetic_out(out):
    """Minimal OUT without git: one footprint + one symbol pointing at it."""
    items = []
    for kind, lib, name, text, lr in (("footprint", "Custom_Test", "R_0603_1608Metric", FP_OK, [1, FP_OK.count("\n")]),
                                      ("symbol", "Custom_Test", "AMP1", SYM, [4, 19])):
        slug = f"{kind}__{lib}__{name}"
        d = os.path.join(out, "items", slug)
        os.makedirs(d, exist_ok=True)
        ext = "kicad_mod" if kind == "footprint" else "kicad_sym"
        with open(os.path.join(d, f"head.{ext}"), "w") as f:
            f.write(text)
        with open(os.path.join(d, "head.png"), "wb") as f:
            f.write(make_mock_out.tiny_png())
        with open(os.path.join(d, "datasheet.pdf"), "wb") as f:
            f.write(b"%PDF-1.4\n% fake\n")
        items.append({"id": f"{kind}:{lib}:{name}", "slug": slug, "kind": kind, "library": lib, "name": name,
                      "status": "added", "path": f"lib_x/{name}", "line_range": {"head": lr, "base": None},
                      "properties": {"head": {"Footprint": "Custom_Test:R_0603_1608Metric"} if kind == "symbol" else {}, "base": None},
                      "datasheet": {"url": None, "local": "datasheets/x.pdf", "file": f"items/{slug}/datasheet.pdf"},
                      "model3d": [{"path_raw": "${KICAD_LIBS_DIR}/lib_3d/Custom_Resistor/R_0603.step", "exists": True}],
                      "renders": {"head": {"png": f"items/{slug}/head.png"}, "base": None},
                      "source": {"head": f"items/{slug}/head.{ext}", "base": None}, "warnings": []})
    items.append({"id": "footprint:Custom_Test:Gone", "slug": "footprint__Custom_Test__Gone", "kind": "footprint",
                  "library": "Custom_Test", "name": "Gone", "status": "deleted", "path": "lib_x/Gone",
                  "line_range": {"head": None, "base": [1, 3]}, "source": {"head": None, "base": None}})
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump({"schema": 1, "items": items, "unreferenced_changed_3d_files": ["lib_3d/Custom_Module/Orphan.step"]}, f)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "out")
        build_synthetic_out(self.out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self, *extra):
        return cr.parse_args(["--out", self.out, *extra])

    def load(self):
        with open(os.path.join(self.out, "review.json")) as f:
            return json.load(f)

    def assert_contract(self, review):
        self.assertEqual(review["schema"], 1)
        for k in ("generator", "generated_at", "summary_markdown", "items"):
            self.assertIn(k, review)
        self.assertNotIn("model", review)
        self.assertNotIn("usage", review)
        for iid, e in review["items"].items():
            self.assertIn(e["verdict"], ("pass", "warn", "fail"))
            self.assertIsInstance(e["summary"], str)
            self.assertIn("datasheet_used", e)
            for f in e["findings"]:
                self.assertIn(f["severity"], ("error", "warning", "info"))
                self.assertIn(f["category"], ("klc", "3d-model"))
                self.assertTrue(f["message"])
                self.assertIn("path", f)
                self.assertTrue(f["line"] is None or isinstance(f["line"], int))
            for c in e["checks"]:
                self.assertIn(c["result"], ("pass", "fail", "unknown"))

    def _canned_klc(self, *extra, env=None):
        """Run the stage with a stand-in KLC checker that reports F7.2 + F5.3 for every footprint."""
        import xml.etree.ElementTree as ET
        from unittest import mock

        def fake_run(klu_dir, kind, library, name, text, ignore=(), ignored=None):
            if kind != "footprint":
                return [], None
            return klc_utils.parse_junit(ET.fromstring(KlcUtilsTests.JUNIT_F72), ignore, ignored), None

        with mock.patch.object(cr.klc_utils, "available", lambda d: bool(d)), \
             mock.patch.object(cr.klc_utils, "run", fake_run), \
             mock.patch.dict(os.environ, env or {}, clear=False):
            rc = cr.run(self.args("--klc-utils", "/fake", *extra))
        return rc, (self.load() if rc == 0 else None)

    def test_klc_ignore(self):
        fp = "footprint:Custom_Test:R_0603_1608Metric"
        os.environ.pop("CR_KLC_IGNORE", None)
        rc, before = self._canned_klc()
        self.assertEqual(rc, 0)
        self.assert_contract(before)
        msgs = [f["message"] for f in before["items"][fp]["findings"]]
        self.assertTrue(any("Pad '1' not located at origin" in m for m in msgs))
        self.assertEqual(before["klc_ignored"], [])
        self.assertNotIn("ignored by configuration", before["summary_markdown"])

        for extra, env in ((["--klc-ignore", "f7.2"], None), ([], {"CR_KLC_IGNORE": "F7.2"})):
            rc, after = self._canned_klc(*extra, env=env)
            self.assertEqual(rc, 0)
            self.assert_contract(after)
            e = after["items"][fp]
            msgs = [f["message"] for f in e["findings"]]
            self.assertFalse(any("origin" in m or "F7.2" in m for m in msgs), msgs)
            self.assertTrue(any("KLC F5.3" in m for m in msgs))           # other KLC findings remain
            self.assertEqual(len(e["findings"]), len(before["items"][fp]["findings"]) - 1)
            self.assertEqual(after["klc_ignored"], ["F7.2"])
            self.assertIn("1 KLC rule(s) ignored by configuration (F7.2; 1 violation(s) suppressed)",
                          after["summary_markdown"])
            chk = next(c for c in e["checks"] if c["name"].startswith("KiCad KLC checker"))
            self.assertEqual((chk["result"], chk["detail"]), ("fail", "1 violation(s); ignored by configuration: F7.2"))
            md = open(os.path.join(self.out, "review.md")).read()
            self.assertNotIn("not located at origin", md)

        # ignoring every rule that fired: the KLC check passes and no longer drives the verdict
        rc, both = self._canned_klc("--klc-ignore", "F7.2", "--klc-ignore", "F5.3")
        e = both["items"][fp]
        self.assertFalse(any(f["message"].startswith("KLC ") for f in e["findings"]))
        self.assertEqual(next(c for c in e["checks"] if c["name"].startswith("KiCad KLC"))["result"], "pass")
        self.assertIn("2 KLC rule(s) ignored by configuration (F5.3, F7.2; 2 violation(s) suppressed)",
                      both["summary_markdown"])
        self.assertEqual(e["verdict"], "pass")
        self.assertEqual(before["items"][fp]["verdict"], "warn")

    def test_klc_ignore_invalid(self):
        rc, _ = self._canned_klc("--klc-ignore", "origin")
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.out, "review.json")))

    def test_checks(self):
        self.assertEqual(cr.run(self.args()), 0)
        r = self.load()
        self.assert_contract(r)
        klc = cr.klc_utils.available(os.environ.get("CR_KLC_UTILS"))
        self.assertEqual(r["generator"], "deterministic checks + KLC" if klc else "deterministic checks")
        self.assertEqual(r["items"]["footprint:Custom_Test:Gone"]["verdict"], "pass")
        sym = r["items"]["symbol:Custom_Test:AMP1"]
        self.assertEqual(sym["verdict"], "fail")
        self.assertTrue(any("no matching pad" in f["message"] for f in sym["findings"]))
        self.assertEqual(sym["datasheet_used"], "items/symbol__Custom_Test__AMP1/datasheet.pdf")
        self.assertIsNone(r["items"]["footprint:Custom_Test:Gone"]["datasheet_used"])
        self.assertEqual(len(r["pr_findings"]), 1)
        self.assertEqual(r["pr_findings"][0]["path"], "lib_3d/Custom_Module/Orphan.step")
        md = open(os.path.join(self.out, "review.md")).read()
        self.assertIn("Orphan.step", md)
        self.assertIn("| Component | Status | Verdict | Top findings |", md)
        self.assertIn("Custom_Test:AMP1", md)
        self.assertIn(f"Checks: {r['generator']}.", md)

    def test_datasheet_url_when_no_local_copy(self):
        with open(os.path.join(self.out, "manifest.json")) as f:
            m = json.load(f)
        m["items"][0]["datasheet"] = {"url": "https://example.com/r.pdf", "local": None, "file": "items/missing.pdf"}
        with open(os.path.join(self.out, "manifest.json"), "w") as f:
            json.dump(m, f)
        self.assertEqual(cr.run(self.args()), 0)
        self.assertEqual(self.load()["items"][m["items"][0]["id"]]["datasheet_used"], "https://example.com/r.pdf")

    def test_old_flags_accepted(self):
        self.assertEqual(cr.run(self.args("--no-llm", "--no-download")), 0)
        self.assert_contract(self.load())

    def test_manifest_missing(self):
        os.remove(os.path.join(self.out, "manifest.json"))
        self.assertEqual(cr.run(self.args()), 2)


class RepoMockOutTests(unittest.TestCase):
    """Runs against real kicad-libs parts in a fixture repo (tests/library/fixture_repo.py)."""

    def test_repo_mock_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            REPO = os.path.join(tmp, "repo")
            fixture_repo.build(REPO)
            out = os.path.join(tmp, "out")
            m = make_mock_out.build(REPO, "origin/main", "HEAD", out)
            if not m["items"]:
                self.skipTest("no KiCad items changed in origin/main..HEAD")
            self.assertEqual(cr.run(cr.parse_args(["--out", out, "--repo", REPO])), 0)
            with open(os.path.join(out, "review.json")) as f:
                review = json.load(f)
            self.assertEqual(set(review["items"]), {i["id"] for i in m["items"]})


if __name__ == "__main__":
    unittest.main()
