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


# A symbol complete enough for kicad-library-utils' parser (every property has (effects)).
SYM_KLC = """(kicad_symbol_lib (version 20251024) (generator "kicad_symbol_editor")
  (symbol "AMP1" (in_bom yes) (on_board yes)
    (property "Reference" "U" (at 0 10.16 0) (effects (font (size 1.27 1.27))))
    (property "Value" "AMP1" (at 0 -10.16 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Description" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (symbol "AMP1_0_1"
      (rectangle (start -7.62 7.62) (end 7.62 -7.62) (stroke (width 0.254) (type default)) (fill (type background))))
    (symbol "AMP1_1_1"
      (pin input line (at -10.16 0 0) (length 2.54) (name "IN" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      (pin output line (at 10.16 0 180) (length 2.54) (name "OUT" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))))))
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

    @unittest.skipUnless(os.environ.get("CR_KLC_UTILS"), "set CR_KLC_UTILS to a kicad-library-utils checkout")
    def test_real_checker(self):
        path = os.path.join(FIXTURES, "lib_fp/Custom_Package_SO.pretty/SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm.kicad_mod")
        if not os.path.isfile(path):
            self.skipTest("demo footprint not in this checkout")
        with open(path) as fh:
            text = fh.read()
        r = klc_utils.run(os.environ["CR_KLC_UTILS"], "footprint", "Custom_Package_SO",
                          "SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm", text)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.attempts, 1)
        self.assertTrue(any("F6.3" in x["message"] for x in r.findings), r.findings)
        # unparseable input is reported as an error, not as a clean pass
        r = klc_utils.run(os.environ["CR_KLC_UTILS"], "footprint", "Custom_Test", "Bad", FP_BAD)
        self.assertEqual(r.findings, [])
        self.assertIn("could not parse", r.error)

    @unittest.skipUnless(os.environ.get("CR_KLC_UTILS"), "set CR_KLC_UTILS to a kicad-library-utils checkout")
    def test_real_checker_crash_is_not_a_pass(self):
        # a symbol property without (effects) crashes kicad-library-utils' parser (IndexError)
        r = klc_utils.run(os.environ["CR_KLC_UTILS"], "symbol", "Custom_Test", "AMP1", SYM_KLC.replace(
            '(property "Value" "AMP1" (at 0 -10.16 0) (effects (font (size 1.27 1.27))))',
            '(property "Value" "AMP1" (at 0 -10.16 0))'))
        self.assertFalse(r.ok)
        self.assertEqual(r.findings, [])
        self.assertIn("KLC checker crashed: IndexError", r.error)
        self.assertIn("effects", r.stderr_tail)
        self.assertNotIn("/tmp", r.stderr_tail)
        self.assertEqual(r.attempts, 1)              # deterministic crash: not retried
        # the same symbol with its (effects) is checked normally
        ok = klc_utils.run(os.environ["CR_KLC_UTILS"], "symbol", "Custom_Test", "AMP1", SYM_KLC)
        self.assertTrue(ok.ok, ok.error)
        # an older file version is not checked either (the pinned checker accepts only its own)
        old = klc_utils.run(os.environ["CR_KLC_UTILS"], "symbol", "Custom_Test", "AMP1",
                            SYM_KLC.replace("(version 20251024)", "(version 20241209)"))
        self.assertFalse(old.ok)
        self.assertIn('could not parse the item: Version of symbol file is "20241209"', old.error)


# A fake kicad-library-utils: klc-check/check_*.py behave as FAKE_KLC_MODE says (comma list, one
# entry per attempt; the last repeats). Attempts are counted in FAKE_KLC_COUNTER.
FAKE_CHECKER = r"""
import os, signal, sys, time
args = sys.argv[1:]
junit = args[args.index("--junit") + 1]
cnt = os.environ["FAKE_KLC_COUNTER"]
n = int(open(cnt).read()) if os.path.exists(cnt) else 0
open(cnt, "w").write(str(n + 1))
modes = os.environ["FAKE_KLC_MODE"].split(",")
mode = modes[min(n, len(modes) - 1)]
CASE = '<testcase name="X"/>'
FAIL = ('<testcase name="X - Errors"><failure message="F5.3" type="FAILURE">F5.3: Courtyard layer requirements'
        '\n    https://klc.kicad.org/footprint/f5/f5.3/\n    Missing courtyard</failure></testcase>')
def report(body):
    open(junit, "w").write(f'<?xml version="1.0"?><testsuites><testsuite name="KLC">{body}</testsuite></testsuites>')
if mode == "pass":
    report(CASE); sys.exit(0)
if mode == "errors":
    report(FAIL); sys.exit(3)
if mode == "crash":
    print('Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nIndexError: list index out of range',
          file=sys.stderr)
    sys.exit(1)
if mode == "parse":
    print('Could not parse library: x.kicad_sym. (Version of symbol file is "20241209", not "20251024")')
    report(""); sys.exit(0)
if mode == "empty":          # the check_symbol.py worker race: results lost, exit 0, no test case
    report(""); sys.exit(0)
if mode == "inconsistent":   # errors in the report but exit 0
    report(FAIL); sys.exit(0)
if mode == "noreport":
    sys.exit(0)
if mode == "garbage":
    open(junit, "w").write("<testsuites><testsuite"); sys.exit(0)
if mode == "killed":
    print("partial output", file=sys.stderr); sys.stderr.flush()
    os.kill(os.getpid(), signal.SIGKILL)
if mode == "hang":
    time.sleep(30)
raise SystemExit("unknown mode " + mode)
"""


class KlcFailSafeTests(unittest.TestCase):
    """klc_utils.run never reports a pass unless the checker demonstrably checked the item."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.klu = os.path.join(self.tmp, "klu")
        os.makedirs(os.path.join(self.klu, "klc-check"))
        for script in ("check_footprint.py", "check_symbol.py"):
            with open(os.path.join(self.klu, "klc-check", script), "w") as f:
                f.write(FAKE_CHECKER)
        self.counter = os.path.join(self.tmp, "count")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fake(self, mode, kind="footprint"):
        from unittest import mock
        if os.path.exists(self.counter):
            os.remove(self.counter)
        with mock.patch.dict(os.environ, {"FAKE_KLC_MODE": mode, "FAKE_KLC_COUNTER": self.counter}):
            r = klc_utils.run(self.klu, kind, "Custom_Test", "X", FP_OK)
        calls = int(open(self.counter).read()) if os.path.exists(self.counter) else 0
        self.assertEqual(calls, r.attempts)
        return r

    def test_real_results_not_retried(self):
        r = self.run_fake("pass")
        self.assertTrue(r.ok)
        self.assertEqual((r.findings, r.attempts, r.retried_because), ([], 1, None))
        r = self.run_fake("errors", "symbol")
        self.assertTrue(r.ok)
        self.assertEqual(r.attempts, 1)
        self.assertIn("Missing courtyard", r.findings[0]["message"])

    def test_crash(self):
        r = self.run_fake("crash")
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "KLC checker crashed: IndexError: list index out of range")
        self.assertIn("IndexError", r.stderr_tail)
        self.assertEqual(r.attempts, 1)              # deterministic: no retry

    def test_could_not_parse(self):
        r = self.run_fake("parse", "symbol")
        self.assertFalse(r.ok)
        self.assertEqual(r.error, 'KLC checker could not parse the item: Version of symbol file is "20241209", '
                                  'not "20251024"')
        self.assertEqual(r.attempts, 1)

    def test_timeout(self):
        from unittest import mock
        with mock.patch.object(klc_utils, "TIMEOUT_S", 1):
            r = self.run_fake("hang")
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "KLC checker timed out after 1 s")
        self.assertEqual(r.attempts, 1)

    def test_flakes_retried_once_then_error(self):
        for mode, reason in (("empty", "reported no result for the item (exit 0, empty report)"),
                             ("inconsistent", "exit 0 disagrees with its report (1 error(s), 0 warning(s))"),
                             ("noreport", "wrote no report (exit 0)"),
                             ("garbage", "KLC report unreadable"),
                             ("killed", "was killed or exited abnormally (exit -9)")):
            with self.subTest(mode=mode):
                r = self.run_fake(mode, "symbol")
                self.assertFalse(r.ok)
                self.assertIn(reason, r.error)
                self.assertTrue(r.error.endswith("(after 2 attempts)"), r.error)
                self.assertEqual(r.attempts, 2)
        self.assertIn("partial output", self.run_fake("killed").stderr_tail)

    def test_flake_then_success(self):
        r = self.run_fake("empty,errors", "symbol")
        self.assertTrue(r.ok)
        self.assertEqual(r.attempts, 2)
        self.assertIn("empty report", r.retried_because)
        self.assertEqual(len(r.findings), 1)
        r = self.run_fake("killed,pass")
        self.assertTrue(r.ok)
        self.assertEqual((r.findings, r.attempts), ([], 2))

    def test_checks_stage(self):
        """End to end through `kipr library checks`: status, finding, verdict, summary, severity option."""
        from unittest import mock
        out = os.path.join(self.tmp, "out")
        build_synthetic_out(out)
        fp = "footprint:Custom_Test:R_0603_1608Metric"

        def stage(mode, *extra):
            if os.path.exists(self.counter):
                os.remove(self.counter)
            with mock.patch.dict(os.environ, {"FAKE_KLC_MODE": mode, "FAKE_KLC_COUNTER": self.counter}):
                os.environ.pop("CR_KLC_ERROR_SEVERITY", None)
                self.assertEqual(cr.run(cr.parse_args(["--out", out, "--klc-utils", self.klu, *extra])), 0)
            with open(os.path.join(out, "review.json")) as f:
                return json.load(f)

        ok = stage("pass")
        self.assertEqual(ok["items"][fp]["klc"], {"status": "ok", "attempts": 1})
        self.assertEqual(ok["items"][fp]["verdict"], "pass")
        self.assertNotIn("KLC could not check", ok["summary_markdown"])

        bad = stage("crash")
        e = bad["items"][fp]
        self.assertEqual(e["klc"]["status"], "error")
        self.assertEqual(e["klc"]["reason"], "KLC checker crashed: IndexError: list index out of range")
        self.assertEqual(e["verdict"], "warn")                      # default: not checked = warn
        f = next(x for x in e["findings"] if x["message"].startswith("KLC could not check this item"))
        self.assertEqual((f["severity"], f["category"]), ("warning", "klc"))
        self.assertIn("`IndexError: list index out of range`", f["message"])   # stderr tail
        chk = next(c for c in e["checks"] if c["name"].startswith("KiCad KLC checker"))
        self.assertEqual(chk["result"], "error")
        self.assertIn("**KLC could not check 2 item(s)**", bad["summary_markdown"])   # footprint + symbol
        md = open(os.path.join(out, "review.md")).read()
        self.assertIn("KLC could not check this item", md)

        self.assertEqual(stage("crash", "--klc-error-severity", "error")["items"][fp]["verdict"], "fail")
        self.assertEqual(stage("crash", "--klc-error-severity", "info")["items"][fp]["verdict"], "pass")
        with mock.patch.dict(os.environ, {"CR_KLC_ERROR_SEVERITY": "bogus", "FAKE_KLC_MODE": "pass",
                                          "FAKE_KLC_COUNTER": self.counter}):
            self.assertEqual(cr.run(cr.parse_args(["--out", out, "--klc-utils", self.klu])), 2)

        retried = stage("empty,pass")["items"][fp]
        self.assertEqual(retried["klc"]["status"], "ok")
        self.assertEqual(retried["klc"]["attempts"], 2)
        self.assertIn("retried once", next(c for c in retried["checks"] if c["name"].startswith("KiCad KLC"))["detail"])


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
        self.assertIn("Pin number 2 is used by 2 pins", msgs)
        self.assertIn("Power pin 3 (VDD) is hidden", msgs)
        # line mapping: symbol opens at source line 4 -> file line 50
        hidden = next(x for x in f if "hidden" in x["message"])
        self.assertEqual(hidden["line"], 50 + (SYM.splitlines().index(next(l for l in SYM.splitlines() if '"VDD"' in l)) + 1) - 4)

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
                self.assertIn(c["result"], ("pass", "fail", "unknown", "error"))

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
