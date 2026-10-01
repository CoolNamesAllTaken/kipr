"""Re-encoded parts through the whole `kipr library` pipeline: render, checks, comment, job summary,
annotations, check-run verdict, report and viewer.

The repository has one library file re-saved by KiCad 10 in which one symbol was really edited,
and a KiCad 7 footprint re-saved by KiCad 10 without edits. A fake kicad-cli replays the committed
KiCad 10.0.6 output (tests/library/fixtures/reencode), so no KiCad is needed.
"""
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
FX = HERE / "fixtures" / "reencode"
sys.path.insert(0, str(HERE))

from kipr.library import cli as library_cli  # noqa: E402
from kipr.library.ci import job_summary, make_comment, post_review  # noqa: E402
from kipr.library.ci.common import load_site, overall_verdict  # noqa: E402

SYM_PATH = "lib_sch/Custom_RF_Amplifier.kicad_sym"
FP_PATH = "lib_fp/Custom_Package_SO.pretty/SOP-8_3.76x4.96mm_P1.27mm.kicad_mod"

FAKE_KICAD_CLI = r'''#!PYTHON
"""Fake kicad-cli: 'version', and 'sym|fp upgrade --force --output DST SRC' for known inputs."""
import os, shutil, sys
FX = FXDIR
table = {}
for old, new in (("Custom_RF_Amplifier.k8.kicad_sym", "Custom_RF_Amplifier.k10.kicad_sym"),
                 ("SOP-8_3.76x4.96mm_P1.27mm.k7.kicad_mod", "SOP-8_3.76x4.96mm_P1.27mm.k10.kicad_mod")):
    table[open(os.path.join(FX, old), encoding="utf-8").read()] = os.path.join(FX, new)
a = sys.argv[1:]
if a == ["version"]:
    print("10.0.6"); sys.exit(0)
kind, dst, src = a[0], a[a.index("--output") + 1], a[-1]
if kind == "sym":
    text = open(src, encoding="utf-8").read()
    if text not in table: sys.exit("unknown input")
    shutil.copyfile(table[text], dst)
else:
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        text = open(os.path.join(src, f), encoding="utf-8").read()
        if text not in table: sys.exit("unknown input")
        shutil.copyfile(table[text], os.path.join(dst, f))
'''


def git(repo: Path, *args) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def build_repo(repo: Path) -> tuple[str, str]:
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "lib_sch").mkdir()
    (repo / "lib_fp" / "Custom_Package_SO.pretty").mkdir(parents=True)
    shutil.copyfile(FX / "Custom_RF_Amplifier.k8.kicad_sym", repo / SYM_PATH)
    shutil.copyfile(FX / "SOP-8_3.76x4.96mm_P1.27mm.k7.kicad_mod", repo / FP_PATH)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base: KiCad 8 symbols, KiCad 7 footprint")
    base = git(repo, "rev-parse", "HEAD")
    # head: everything re-saved by KiCad 10; the designer renamed one pin of AD8314
    sym = (FX / "Custom_RF_Amplifier.k10.kicad_sym").read_text(encoding="utf-8")
    assert sym.count('(name "RFIN"') == 1
    (repo / SYM_PATH).write_text(sym.replace('(name "RFIN"', '(name "RF_IN"'), encoding="utf-8")
    shutil.copyfile(FX / "SOP-8_3.76x4.96mm_P1.27mm.k10.kicad_mod", repo / FP_PATH)
    git(repo, "commit", "-qam", "head: re-saved by KiCad 10, AD8314 pin 1 renamed")
    return base, git(repo, "rev-parse", "HEAD")


class ReencodePipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kipr-reenc-"))
        cli = cls.tmp / "kicad-cli"
        cli.write_text(FAKE_KICAD_CLI.replace("PYTHON", sys.executable).replace("FXDIR", repr(str(FX))))
        cli.chmod(0o755)
        cls.base, cls.head = build_repo(cls.tmp / "repo")
        cls.out = cls.tmp / "out"
        old = os.environ.get("KIPR_KICAD_CLI")
        os.environ["KIPR_KICAD_CLI"] = str(cli)
        try:
            cls.rc = library_cli.main(["--repo", str(cls.tmp / "repo"), "--base", cls.base, "--head", cls.head,
                                       "--out", str(cls.out), "--pr", "13", "--no-3d", "--no-preview",
                                       "--repo-name", "PantsForBirds/kicad-libs"])
        finally:
            if old is None:
                os.environ.pop("KIPR_KICAD_CLI", None)
            else:
                os.environ["KIPR_KICAD_CLI"] = old

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def manifest(self):
        return json.loads((self.out / "manifest.json").read_text())

    def review(self):
        return json.loads((self.out / "review.json").read_text())

    def test_statuses(self):
        self.assertEqual(self.rc, 0)
        st = {i["id"]: i["status"] for i in self.manifest()["items"]}
        self.assertEqual(st, {
            "symbol:Custom_RF_Amplifier:AD8314": "modified",
            "symbol:Custom_RF_Amplifier:BLB01": "re-encoded",
            "footprint:Custom_Package_SO:SOP-8_3.76x4.96mm_P1.27mm": "re-encoded",
        })

    def test_reencode_records(self):
        items = {i["id"]: i for i in self.manifest()["items"]}
        blb = items["symbol:Custom_RF_Amplifier:BLB01"]["reencode"]
        self.assertEqual(blb["explanation"], "file format upgraded 20231120 → 20251024 by KiCad 10.0; no content change")
        self.assertEqual((blb["method"], blb["kicad_cli_version"]), ("reference-upgrade", "10.0.6"))
        ad = items["symbol:Custom_RF_Amplifier:AD8314"]
        self.assertFalse(ad["reencode"]["reencoded"])
        self.assertTrue(any('pin "1"' in d and "RF_IN" in d for d in ad["reencode"]["differences"]), ad["reencode"])
        # the raw text diff stays available for the re-encoded parts
        self.assertTrue((self.out / items["symbol:Custom_RF_Amplifier:BLB01"]["text_diff"]).is_file())
        fp = items["footprint:Custom_Package_SO:SOP-8_3.76x4.96mm_P1.27mm"]["reencode"]
        self.assertEqual(fp["explanation"], "file format upgraded 20221018 → 20260206 by KiCad 10.0; no content change")

    def test_library_notes(self):
        files = {f["path"]: f for f in self.manifest()["reencoded_files"]}
        self.assertEqual(files[SYM_PATH]["note"], "Custom_RF_Amplifier.kicad_sym was re-saved by a newer KiCad "
                                                  "(format 20231120 → 20251024, KiCad 8.0 → KiCad 10.0)")
        self.assertEqual(files[SYM_PATH]["reencoded"], ["symbol:Custom_RF_Amplifier:BLB01"])
        self.assertEqual(files[SYM_PATH]["modified"], ["symbol:Custom_RF_Amplifier:AD8314"])
        self.assertIn(FP_PATH, files)

    def test_checks_skip_reencoded(self):
        rv = self.review()
        self.assertEqual(set(rv["items"]), {"symbol:Custom_RF_Amplifier:AD8314"})
        self.assertIn("1 footprint and 1 symbol re-encoded by KiCad, no changes (not reviewed)", rv["summary_markdown"])
        self.assertIn("Reviewed **1** item(s)", rv["summary_markdown"])
        self.assertNotIn("BLB01", (self.out / "review.md").read_text())

    def test_ci_counts_annotations_and_verdict(self):
        manifest, review = load_site(self.out)
        self.assertEqual([i["id"] for i in manifest["items"]], ["symbol:Custom_RF_Amplifier:AD8314"])
        self.assertEqual(len(manifest["reencoded_items"]), 2)
        self.assertEqual(overall_verdict(manifest, review), review["items"]["symbol:Custom_RF_Amplifier:AD8314"]["verdict"])
        for line in job_summary.annotations(manifest, review):
            self.assertNotIn("BLB01", line)
            self.assertNotIn("SOP-8", line)
        s = job_summary.summary(self.out, manifest, review, [])
        self.assertIn("**1** changed component(s): 1 modified", s)
        self.assertIn("not counted: 1 footprint and 1 symbol re-encoded by KiCad, no changes", s)
        self.assertIn("> Custom_RF_Amplifier.kicad_sym was re-saved by a newer KiCad", s)
        inline, rest = post_review.inline_candidates(manifest, review, {SYM_PATH: set(range(1, 2000)), FP_PATH: set(range(1, 2000))})
        self.assertFalse([f for item, f, _ in inline + rest if item.get("status") == "re-encoded"])

    def test_comment(self):
        manifest, review = load_site(self.out)
        ctx = make_comment.Ctx(self.out, "PantsForBirds/kicad-libs", 13, self.head, "https://p.github.io/kicad-libs/", None)
        md = make_comment.build_comment(ctx, manifest, review)
        self.assertIn("1 changed component (1 symbol)", md)
        self.assertIn("Not counted: 1 footprint and 1 symbol re-encoded by KiCad, no changes.", md)
        self.assertIn("<details><summary>♻️ 1 footprint and 1 symbol re-encoded by KiCad, no changes</summary>", md)
        self.assertIn("- Custom_RF_Amplifier.kicad_sym was re-saved by a newer KiCad (format 20231120 → 20251024", md)
        self.assertIn("#symbol__Custom_RF_Amplifier__BLB01) <sub>symbol</sub>: file format upgraded 20231120 → 20251024", md)
        self.assertIn("identical to KiCad's own upgrade of the base version", md)
        table = md.split("<details>")[0]
        self.assertIn("Custom_RF_Amplifier:AD8314", table)
        self.assertNotIn("BLB01", table.split("♻️")[0])

    def test_report(self):
        from kipr.library import report
        page = report.build(self.out, level=3)
        comps = page.split('id="re-encoded"')[0]
        self.assertIn("<h3>Components (1)</h3>", comps)
        self.assertNotIn("BLB01", comps)
        reenc = page.split('id="re-encoded"')[1]
        self.assertIn("1 footprint and 1 symbol re-encoded by KiCad, no changes", reenc)
        self.assertIn("Custom_RF_Amplifier.kicad_sym was re-saved by a newer KiCad", reenc)
        self.assertIn("Text diff", reenc)
        self.assertIn("Changed beyond the file format upgrade 20231120 → 20251024", comps)
        self.assertIn("RF_IN", comps)

    def test_viewer_smoke(self):
        """Every item, including the re-encoded ones, opens in the viewer without JS errors."""
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("needs playwright")
        r = subprocess.run([sys.executable, "-c", "import sys; from kipr.cli import main; sys.exit(main())",
                            "library", "site", "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        script = r'''
import sys, json
from playwright.sync_api import sync_playwright
site = sys.argv[1]
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(args=__import__("os").environ.get("PW_CHROMIUM_ARGS", "").split())
    pg = b.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("file://" + site + "/index.html")
    pg.wait_for_selector("#re-encoded")
    out = {"overview": pg.inner_text("#main"), "sidebar": pg.inner_text("#sidebar"),
           "group_open": pg.eval_on_selector_all(".reenc-group", "es => es.some(e => e.open)")}
    pg.goto("file://" + site + "/index.html#symbol__Custom_RF_Amplifier__BLB01")
    pg.wait_for_selector("section.reencoded")
    out["item"] = pg.inner_text("section.reencoded")
    out["group_open_on_item"] = pg.eval_on_selector('a[data-slug="symbol__Custom_RF_Amplifier__BLB01"]',
                                                    "e => e.closest('details').open")
    pg.goto("file://" + site + "/index.html#symbol__Custom_RF_Amplifier__AD8314")
    pg.wait_for_selector("#re-encode")
    out["modified"] = pg.inner_text("#re-encode")
    b.close()
print(json.dumps({"errors": errors, **out}))
'''
        r = subprocess.run([sys.executable, "-c", script, str(self.out)], capture_output=True, text=True, timeout=180)
        if r.returncode and "Executable doesn't exist" in r.stderr:
            self.skipTest("playwright chromium not installed")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(res["errors"], [])
        self.assertIn("1 footprint and 1 symbol re-encoded by KiCad, no changes", res["overview"])
        self.assertIn("1 items:", res["overview"])
        self.assertEqual(res["sidebar"].count("1 re-encoded by KiCad, no changes"), 2)   # per kind
        self.assertIn("(none changed)", res["sidebar"].lower())                                 # footprints
        self.assertFalse(res["group_open"])
        self.assertTrue(res["group_open_on_item"])
        self.assertIn("file format upgraded 20231120 → 20251024 by KiCad 10.0; no content change", res["item"])
        self.assertIn("Changed beyond the file format upgrade 20231120 → 20251024", res["modified"])
        self.assertIn("RF_IN", res["modified"])


if __name__ == "__main__":
    unittest.main()
