"""Tests for kipr/project/site.py (viewer copy, file:// bundle, data.js, packs) and report.py.

    python3 -m unittest discover -s tests/web-project -p 'test_*.py'
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kipr.project import report, site  # noqa: E402
import make_mock  # noqa: E402


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kipr-site-"))
        self.out = self.tmp / "out"
        make_mock.make(self.out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_copies_viewer_and_keeps_data(self):
        before = (self.out / "project-review.json").read_bytes()
        copied = site.build_site(self.out)
        self.assertIn("index.html", copied)
        self.assertIn("js/app.js", copied)
        self.assertIn("vendor/wasm-gerber-renderer/index.js", copied)
        self.assertIn("vendor/wasm-gerber-renderer/wasm/wasm_gerber_processor_bg.wasm", copied)
        self.assertTrue((self.out / "js" / "bundle.js").is_file())
        self.assertTrue((self.out / "serve.py").is_file())
        self.assertEqual((self.out / "project-review.json").read_bytes(), before)
        self.assertTrue((self.out / "p" / "demo_board" / "sch" / "base" / "root.svg").is_file())

    def test_no_offline_removes_file_support(self):
        site.build_site(self.out)
        site.build_offline(self.out)
        site.build_site(self.out, offline=False)
        for stale in ("data.js", "serve.py", "README.txt", "offline"):
            self.assertFalse((self.out / stale).exists(), stale)

    def test_bundle_is_valid_classic_script(self):
        bundle = site.make_bundle(site.WEB / "js")
        self.assertNotRegex(bundle, r"(?m)^\s*(import|export)\s")
        for name in ("app.js", "layout.js", "schematic.js", "util.js"):
            self.assertIn(f'__defs["./{name}"]', bundle)
        f = self.tmp / "bundle.js"
        f.write_text(bundle)
        subprocess.run(["node", "--check", str(f)], check=True)

    def test_bundle_refuses_unknown_module_syntax(self):
        with self.assertRaises(ValueError):
            site.transform_module("x.js", "export default function () {}\n")
        with self.assertRaises(ValueError):
            site.transform_module("x.js", "import * as y from './y.js';\n")

    def test_data_js_is_escaped(self):
        r = json.loads((self.out / "project-review.json").read_text())
        r["projects"][0]["name"] = "</script><script>alert(1)</script> &"
        (self.out / "project-review.json").write_text(json.dumps(r))
        site.build_site(self.out)
        site.build_offline(self.out)
        data = (self.out / "data.js").read_text()
        self.assertNotIn("</script", data.lower())
        self.assertNotIn("<", data)
        self.assertNotIn(" ", data)
        m = re.search(r"window\.KIPR_DATA = (.*);\n$", data)
        self.assertEqual(json.loads(m.group(1))["review"]["projects"][0]["name"], r["projects"][0]["name"])

    def test_packs_hold_only_confined_svgs(self):
        (self.out / "secret.svg").write_text("<svg>SECRET</svg>")
        r = json.loads((self.out / "project-review.json").read_text())
        sheets = r["projects"][0]["schematic"]["sheets"]
        sheets[0]["base"] = "secret.svg"
        sheets[1]["base"] = "p/demo_board/../../secret.svg"
        sheets[2]["base"] = "p/sensor_breakout/sch/head/root__sensors.svg"  # another project's file
        r["projects"].append({"slug": "../evil", "schematic": {"sheets": [{"base": "secret.svg"}]}})
        (self.out / "project-review.json").write_text(json.dumps(r))
        site.build_site(self.out)
        stats = site.build_offline(self.out)
        packs = sorted(p.name for p in (self.out / "offline").iterdir() if not p.name.startswith("pcba3d-"))
        self.assertEqual(packs, ["demo_board.js", "old_adapter.js", "sensor_breakout.js"])
        self.assertEqual(stats["packs"], 3)
        demo = (self.out / "offline" / "demo_board.js").read_text()
        self.assertNotIn("SECRET", demo)
        self.assertNotIn("p/sensor_breakout/", demo)
        self.assertIn("p/demo_board/sch/head/root.svg", demo)
        self.assertIn("p/demo_board/pcb/base/F_Cu.svg", demo)

    @staticmethod
    def pack_files(path: Path) -> dict:
        """{path: entry} of a pcba3d pack (each `o.files[k] = v;` line is JSON)."""
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.fullmatch(r"  o\.files\[(.*?)\] = (.*);", line)
            if m:
                out[json.loads(m.group(1))] = json.loads(m.group(2))
        return out

    @staticmethod
    def shell_pack(path: Path) -> dict:
        m = re.search(r"\] = (\{.*\});\n$", path.read_text(encoding="ascii"))
        return json.loads(m.group(1))["files"]

    def test_pcba3d_constants_match_the_module(self):
        src = (site.WEB / "pcba3d" / "gerberboard.js").read_text(encoding="utf-8")
        self.assertIn(f"OFFLINE_WASM_KEY = '{site.PCBA3D_WASM}'", src)
        self.assertTrue((site.WEB / "pcba3d" / site.PCBA3D_BUNDLE).is_file(), "committed 3D bundle missing")

    def test_pcba3d_packs(self):
        r = json.loads((self.out / "project-review.json").read_text())
        (self.out / "secret.glb").write_bytes(b"SECRET")
        glb = self.out / "p" / "demo_board" / "3d" / "head.glb"  # the mock has no GLBs
        glb.parent.mkdir(parents=True, exist_ok=True)
        glb.write_bytes(b"glTF\x02\x00\x00\x00")
        p = r["projects"][2]  # a GLB path outside p/<slug>/ must not be packed
        p["pcba3d"] = {**(p.get("pcba3d") or {}), "base": {"glb": "secret.glb"}}
        (self.out / "project-review.json").write_text(json.dumps(r))
        site.build_site(self.out)
        site.build_offline(self.out)
        data = json.loads(re.search(r"window\.KIPR_DATA = (.*);\n$", (self.out / "data.js").read_text()).group(1))
        self.assertTrue(data["pcba3d"])
        demo = self.pack_files(self.out / "offline" / "pcba3d-demo_board.js")
        self.assertEqual(demo["p/demo_board/3d/head.glb"], {"b64": "Z2xURgIAAAA="})
        self.assertFalse(any(k.endswith((".gbr", ".drl")) for k in demo))  # the fab files are in the project pack
        shell = self.shell_pack(self.out / "offline" / "demo_board.js")
        self.assertIn("p/demo_board/pcb/head/F_Cu.gbr", shell)
        self.assertIn("p/demo_board/pcb/head/PTH.drl", shell)
        for f in (self.out / "offline").glob("pcba3d-*.js"):
            self.assertNotIn("U0VDUkVU", f.read_text())  # base64("SECRET")
        vendor = self.pack_files(self.out / "offline" / "pcba3d-vendor.js")
        self.assertEqual(list(vendor), [site.PCBA3D_WASM])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_pcba3d_packs_match_build_offline_mjs(self):
        """site.py writes the same 3D packs as the module's own node build."""
        site.build_site(self.out)
        site.build_offline(self.out)
        py = {f.name: self.pack_files(f) for f in (self.out / "offline").glob("pcba3d-*.js")}
        node_out = self.tmp / "node"
        shutil.copytree(self.out, node_out)
        shutil.rmtree(node_out / "offline")
        subprocess.run(["node", str(site.WEB / "pcba3d" / "build_offline.mjs"), "--no-bundle", "--out", str(node_out)],
                       check=True, capture_output=True)
        js = {f.name: self.pack_files(f) for f in (node_out / "offline").glob("pcba3d-*.js")}
        # the node build also packs the fab files for demo.html; site.py leaves them to the project
        # pack, where the module's assets.js finds them: same bytes either way
        for name, files in list(js.items()):
            if name == "pcba3d-vendor.js":
                continue
            shell = self.shell_pack(self.out / "offline" / (name[len("pcba3d-"):]))
            for k, v in list(files.items()):
                if k.endswith((".gbr", ".drl")):
                    self.assertEqual(shell[k], v, k)
                    del files[k]
            if not files:
                del js[name]
        self.assertEqual(py, js)

    def test_confined_file(self):
        (self.out / "x.txt").write_text("x")
        self.assertIsNotNone(site.confined_file(self.out, "x.txt"))
        for bad in ("../x.txt", "/etc/passwd", "a\\b", "javascript:x", "p//x", "./x.txt", "", None, 5):
            self.assertIsNone(site.confined_file(self.out, bad), bad)
        self.assertIsNone(site.confined_file(self.out, "x.txt", "demo_board"))


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kipr-report-"))
        cls.out = cls.tmp / "out"
        make_mock.make(cls.out)
        cls.path = report.make_report(cls.out)
        cls.html = cls.path.read_text()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_written_self_contained(self):
        self.assertEqual(self.path, self.out / "project-review.html")
        self.assertNotIn("<script", self.html)
        srcs = re.findall(r'src="([^"]*)"', self.html)
        self.assertTrue(srcs)
        self.assertTrue(all(s.startswith("data:image/") for s in srcs), srcs[:3])
        self.assertIn("default-src 'none'", self.html)

    def test_content(self):
        for text in ("demo_board", "sensor_breakout", "old_adapter", "R2", "4.7k", "/SDA", "clearance", "Unchanged sheets: Connectors"):
            self.assertIn(text, self.html)
        if report.Image is not None and report.cairosvg is not None:
            # base / head / diff for 4 changed sheets of demo_board, 1 + 1 sheets of the others, 6 changed layers
            self.assertGreaterEqual(self.html.count("data:image/png"), 3 * 4)

    def test_size_cap_drops_images(self):
        small = report.make_report(self.out, self.tmp / "small.html", max_mb=0.01).read_text()  # smaller than the page without images: last level
        self.assertNotIn("data:image", small)
        self.assertIn("all images left out", small)

    def test_parallel_raster_same_page(self):
        """The worker-process prefetch changes nothing but speed."""
        strip = lambda t: re.sub(r"generated [^<]*UTC", "", t)  # noqa: E731
        serial = self.tmp / "serial.html"
        old = os.environ.get("KIPR_REPORT_JOBS")
        os.environ["KIPR_REPORT_JOBS"] = "1"
        try:
            report.make_report(self.out, serial)
        finally:
            os.environ.pop("KIPR_REPORT_JOBS") if old is None else os.environ.__setitem__("KIPR_REPORT_JOBS", old)
        imgs = report.Images(self.out)
        review = json.loads((self.out / "project-review.json").read_text())
        wanted = report.wanted_images(review["projects"], 1600, 1000)
        self.assertTrue(len(wanted) > 4)
        imgs.prefetch(wanted, jobs=2)
        self.assertTrue(imgs._png)
        self.assertEqual(strip(serial.read_text()), strip(self.path.read_text()))

    def test_unpadded_embedded_bitmap(self):
        """KiCad 10 schematic images: base64 with line breaks and no `=` padding."""
        import base64, io
        from PIL import Image
        for w in range(3, 12):  # a PNG whose base64 needs padding
            buf = io.BytesIO()
            Image.new("RGB", (w, 2), (200, 30, 30)).save(buf, "PNG")
            if len(buf.getvalue()) % 3:
                break
        b64 = base64.b64encode(buf.getvalue()).decode().rstrip("=")
        self.assertNotEqual(len(b64) % 4, 0)
        b64 = b64[:20] + "\n" + b64[20:]
        svg = self.tmp / "img.svg"
        svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 30 20">'
                       f'<image x="0" y="0" width="30" height="20" xlink:href="data:image/png;base64,{b64}"/></svg>')
        png = report.render_png(str(svg), 60, None)
        self.assertEqual(Image.open(io.BytesIO(png)).convert("RGB").getpixel((30, 20)), (200, 30, 30))

    def test_ink_diff_classes(self):
        if report.Image is None:
            self.skipTest("Pillow not installed")
        from PIL import Image
        imgs = report.Images(self.out)
        base = Image.new("RGBA", (20, 1), (255, 255, 255, 255))
        head = base.copy()
        for x in (2, 10):
            base.putpixel((x, 0), (0, 0, 0, 255))
        for x in (3, 16):
            head.putpixel((x, 0), (0, 0, 0, 255))
        d = imgs.diff(base, head, "ink")
        self.assertEqual(d.getpixel((10, 0)), report.DIFF["removed"])
        self.assertEqual(d.getpixel((16, 0)), report.DIFF["added"])
        self.assertEqual(d.getpixel((2, 0))[:3], report.DIFF["common"][:3])  # within 1 px of head ink
        self.assertEqual(d.getpixel((5, 0))[3], 0)

    def test_with_view_box(self):
        svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" viewBox="0 0 297 210"><rect/></svg>'
        out = report.with_view_box(svg, (10, 20, 30, 40))
        self.assertIn(b'viewBox="10.0000 20.0000 30.0000 40.0000"', out)
        self.assertIn(b'width="30.0000mm"', out)
        self.assertIn(b"<rect/>", out)
        self.assertEqual(report.view_box(out), (10.0, 20.0, 30.0, 40.0))

    def test_no_review(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertIn("No readable project-review.json", report.make_report(empty).read_text())


if __name__ == "__main__":
    unittest.main()
