"""`kipr library` end to end on the fixture repo (tests/library/fixture_repo.py): render, checks,
site and report, relocated library directories, and the optional kicad-cli reference SVGs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fixture_repo  # noqa: E402

from kipr.common import kicad_cli  # noqa: E402
from kipr.library import cli as library_cli  # noqa: E402
from kipr.library.layout import Layout  # noqa: E402

EXPECTED = {
    "footprint:Custom_Button_Switch_SMD:SW_SPST_Same-Sky_TS32_with-boss": "modified",
    "footprint:Custom_Buzzer_Beeper:MagneticBuzzer_9.6mm_5mm_right-angle": "added",
    "footprint:Custom_Connector_Card:microSD_SHOU-HAN_TF-PUSH": "added",
    "footprint:Custom_Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm": "modified",
    "footprint:Custom_Package_SO:SOP-8_3.76x4.96mm_P1.27mm": "deleted",
    "symbol:Custom_Audio:NS4168": "modified",
}


class Base(unittest.TestCase):
    dirs: dict | None = None
    extra: tuple = ()

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kipr-e2e-"))
        cls.repo = cls.tmp / "repo"
        cls.base, cls.head = fixture_repo.build(cls.repo, cls.dirs)
        cls.out = cls.tmp / "out"
        cls.rc = library_cli.main(["--repo", str(cls.repo), "--base", cls.base, "--head", cls.head,
                                   "--out", str(cls.out), "--pr", "9", *cls.extra])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def manifest(self):
        return json.loads((self.out / "manifest.json").read_text())

    def review(self):
        return json.loads((self.out / "review.json").read_text())


class EndToEndTests(Base):
    def test_exit_status(self):
        self.assertEqual(self.rc, 0)

    def test_manifest(self):
        m = self.manifest()
        self.assertEqual(m["schema"], 1)
        self.assertEqual((m["base_sha"], m["head_sha"], m["pr"]), (self.base, self.head, 9))
        self.assertEqual({i["id"]: i["status"] for i in m["items"]}, EXPECTED)
        self.assertEqual(m["unreferenced_changed_3d_files"], ["lib_3d/Custom_Module/SH1421-C.step"])
        by_id = {i["id"]: i for i in m["items"]}
        soic = by_id["footprint:Custom_Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm"]
        for side in ("head", "base"):
            for key in ("svg", "png"):
                self.assertTrue((self.out / soic["renders"][side][key]).is_file())
            self.assertIn("F.Cu", soic["renders"][side]["layers"])
        self.assertTrue((self.out / soic["diff_png"]).is_file())
        self.assertTrue((self.out / soic["text_diff"]).read_text().startswith("--- a/lib_fp/"))
        buzzer = by_id["footprint:Custom_Buzzer_Beeper:MagneticBuzzer_9.6mm_5mm_right-angle"]
        mdl = buzzer["model3d_by_side"]["head"][0]
        self.assertTrue(mdl["exists"])
        self.assertTrue((self.out / mdl["file"]).read_bytes().startswith(b"ISO-10303-21"))
        sym = by_id["symbol:Custom_Audio:NS4168"]
        self.assertEqual(sym["datasheet"]["local"], "datasheets/NS4168.pdf")
        self.assertTrue((self.out / sym["datasheet"]["file"]).is_file())
        self.assertIn('"LRCLK"', (self.out / sym["text_diff"]).read_text())

    def test_review(self):
        r = self.review()
        self.assertEqual(set(r["items"]), set(EXPECTED))
        self.assertEqual(r["generator"], "deterministic checks")
        self.assertEqual([f["path"] for f in r["pr_findings"]], ["lib_3d/Custom_Module/SH1421-C.step"])
        self.assertIn("| Component | Status | Verdict | Top findings |", (self.out / "review.md").read_text())

    def test_site_and_report(self):
        for f in ("index.html", "viewer.css", "js/app.js", "js/bundle.js", "data.js", "serve.py", "README.txt",
                  ".nojekyll"):
            self.assertTrue((self.out / f).is_file(), f)
        self.assertTrue(list((self.out / "offline").glob("footprint__*.js")))
        page = (self.out / "component-review.html").read_text()
        self.assertIn("NS4168", page)
        self.assertNotIn("<script", page.lower())


class RelocatedLayoutTests(Base):
    """--lib-fp/--lib-sch/--lib-3d/--datasheets with non-default directories."""
    dirs = {"fp": "hw/footprints", "sym": "hw/symbols", "models": "hw/3d", "datasheets": "docs/ds"}
    extra = ("--lib-fp", "hw/footprints", "--lib-sch", "hw/symbols/", "--lib-3d", "hw/3d", "--datasheets", "docs/ds")

    def test_same_items(self):
        self.assertEqual(self.rc, 0)
        m = self.manifest()
        self.assertEqual({i["id"]: i["status"] for i in m["items"]}, EXPECTED)
        self.assertEqual(m["unreferenced_changed_3d_files"], ["hw/3d/Custom_Module/SH1421-C.step"])
        by_id = {i["id"]: i for i in m["items"]}
        self.assertTrue(by_id["footprint:Custom_Buzzer_Beeper:MagneticBuzzer_9.6mm_5mm_right-angle"]
                        ["model3d_by_side"]["head"][0]["exists"])
        self.assertEqual(by_id["symbol:Custom_Audio:NS4168"]["datasheet"]["local"], "docs/ds/NS4168.pdf")

    def test_messages_use_models_dir(self):
        msgs = [f["message"] for e in self.review()["items"].values() for f in e["findings"]]
        self.assertTrue(any("`${KICAD_LIBS_DIR}/hw/3d/...`" in m for m in msgs), msgs)
        self.assertFalse(any("lib_3d" in m for m in msgs))

    def test_default_layout_finds_nothing(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(library_cli.main(["render", "--repo", str(self.repo), "--base", self.base,
                                               "--head", self.head, "--out", out]), 0)
            self.assertEqual(json.loads((Path(out) / "manifest.json").read_text())["items"], [])


class LayoutTests(unittest.TestCase):
    def test_regexes(self):
        lay = Layout()
        self.assertTrue(lay.fp_re.match("lib_fp/A.pretty/B.kicad_mod"))
        self.assertFalse(lay.fp_re.match("other/A.pretty/B.kicad_mod"))
        root = Layout(fp=".", sym="", models="")
        self.assertEqual(root.fp_re.match("A.pretty/B.kicad_mod").group("name"), "B")
        self.assertTrue(root.sym_re.match("Lib.kicad_sym"))
        self.assertTrue(root.is_model("x/y.STEP"))
        self.assertFalse(root.is_model("A.pretty/B.kicad_mod"))
        self.assertEqual(root.diff_paths(), [".", ".", "."])
        with self.assertRaises(ValueError):
            Layout(fp="../outside")


@unittest.skipUnless(kicad_cli.find(), "needs kicad-cli ($KIPR_KICAD_CLI, $KICAD_CLI or PATH)")
class KicadCliTests(Base):
    extra = ("--use-kicad-cli", "--no-3d", "--skip", "report")

    def test_reference_svgs(self):
        self.assertEqual(self.rc, 0)
        items = {i["id"]: i for i in self.manifest()["items"]}
        soic = items["footprint:Custom_Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm"]
        for side in ("head", "base"):
            p = self.out / soic["renders_kicad_cli"][side]
            self.assertIn("<svg", p.read_text())
        self.assertIn("head", items["symbol:Custom_Audio:NS4168"].get("renders_kicad_cli", {}))


class CliTests(unittest.TestCase):
    def test_module_entry_point(self):
        r = subprocess.run([sys.executable, "-m", "kipr.cli", "library", "ci", "--help"], capture_output=True,
                           text=True, cwd=HERE.parent.parent, env={**os.environ, "PYTHONPATH": str(HERE.parent.parent)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sanitize-site", r.stdout)

    def test_stage_help(self):
        for stage in ("render", "checks", "site", "report"):
            with self.assertRaises(SystemExit) as cm:
                library_cli.main([stage, "--help"])
            self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
