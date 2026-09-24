"""The viewer (web/library): node unit tests, and headless-Chromium smoke/screenshot and XSS checks
on a mock site and on a site rendered by `kipr library` from the fixture repo.

The browser tests need the `playwright` Python package and its Chromium; they are skipped
otherwise. The 3D view loads three.js/occt-import-js from cdn.jsdelivr.net: without network
access the screenshot tests skip the 3D tab. Screenshots go to $KIPR_SHOTS_DIR if set.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VIEWER_TESTS = HERE / "viewer"
sys.path.insert(0, str(HERE))

import fixture_repo  # noqa: E402

from kipr.library import cli as library_cli  # noqa: E402
from kipr.library import site as build_site  # noqa: E402


def _have_playwright() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001 - no browser installed
        return False


def _have_cdn() -> bool:
    try:
        urllib.request.urlopen("https://cdn.jsdelivr.net/npm/three@0.185.1/package.json", timeout=5).close()
        return True
    except OSError:
        return False


HAVE_PW = _have_playwright()
HAVE_CDN = HAVE_PW and _have_cdn()
_tmp = None
_sites: dict[str, Path] = {}


def setUpModule():
    global _tmp
    _tmp = Path(tempfile.mkdtemp(prefix="kipr-viewer-test-"))


def tearDownModule():
    shutil.rmtree(_tmp, ignore_errors=True)


def fixture_repo_dir() -> Path:
    repo = _tmp / "repo"
    if not repo.exists():
        fixture_repo.build(repo)
    return repo


def mock_site() -> Path:
    """tests/library/viewer/make_mock.py output + the viewer (every viewer feature, synthetic data)."""
    if "mock" not in _sites:
        out = _tmp / "mock-out"
        subprocess.run([sys.executable, str(VIEWER_TESTS / "make_mock.py"), "--repo", str(fixture_repo_dir()),
                        "--out", str(out)], check=True, capture_output=True, cwd=ROOT)
        build_site.build(out)
        build_site.build_offline(out)
        _sites["mock"] = out
    return _sites["mock"]


def rendered_site() -> Path:
    """`kipr library` end to end on the fixture repo (real renders, checks, viewer, report)."""
    if "real" not in _sites:
        out = _tmp / "real-out"
        rc = library_cli.main(["--repo", str(fixture_repo_dir()), "--base", "origin/main", "--head", "HEAD",
                               "--out", str(out), "--pr", "9", "--repo-name", "PantsForBirds/kicad-libs"])
        assert rc == 0
        _sites["real"] = out
    return _sites["real"]


def shots_dir(name: str) -> Path:
    base = Path(os.environ["KIPR_SHOTS_DIR"]) if os.environ.get("KIPR_SHOTS_DIR") else _tmp / "shots"
    return base / name


def run_tool(script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(VIEWER_TESTS / script), *args], capture_output=True, text=True,
                          cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=1800)


class NodeUnitTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_viewer_unit_tests(self):
        r = subprocess.run(["node", "--test", str(VIEWER_TESTS / "unit.test.mjs")], capture_output=True, text=True,
                           cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


@unittest.skipUnless(HAVE_PW, "needs playwright + chromium (python -m playwright install chromium)")
class ScreenshotTests(unittest.TestCase):
    def check(self, site: Path, name: str, *extra):
        if not HAVE_CDN:
            extra += ("--no-3d",)
        r = run_tool("screenshot.py", "--site", str(site), "--shots", str(shots_dir(name)), *extra)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-4000:])
        self.assertNotIn("PROBLEM", r.stdout)

    def test_mock_http(self):
        self.check(mock_site(), "mock-http")

    def test_mock_file(self):
        """Opened from disk like a downloaded artifact (data.js, js/bundle.js, offline packs)."""
        self.check(mock_site(), "mock-file", "--mode", "file")

    def test_mock_serve_dark(self):
        """Through the site's own serve.py, dark theme."""
        self.check(mock_site(), "mock-serve-dark", "--mode", "serve", "--dark")

    def test_rendered_http(self):
        self.check(rendered_site(), "rendered-http")

    def test_rendered_file(self):
        self.check(rendered_site(), "rendered-file", "--mode", "file")


@unittest.skipUnless(HAVE_PW, "needs playwright + chromium (python -m playwright install chromium)")
class XssTests(unittest.TestCase):
    def check(self, site: Path, *extra):
        r = run_tool("xss_check.py", "--site", str(site), *extra)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-4000:])
        self.assertIn("XSS check: ok", r.stdout)

    def test_http(self):
        self.check(mock_site())

    def test_file(self):
        self.check(mock_site(), "--mode", "file")

    def test_rendered_file(self):
        self.check(rendered_site(), "--mode", "file")


if __name__ == "__main__":
    unittest.main()
