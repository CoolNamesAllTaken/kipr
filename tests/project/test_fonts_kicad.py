"""Fonts end to end with a real kicad-cli: a synthetic board whose gr_text in Poppins Bold sits
0.22 mm inside the board edge (see fonts_fixture.py).

* font missing        -> DRC silk_edge_clearance, marked font-dependent, fonts.missing + warning
* --fonts DIR         -> no violation
* Google Fonts mirror -> no violation (file:// mirror of google/fonts)
* Google Fonts        -> no violation (real download; $KIPR_FONTS_NETWORK=1 requires it, otherwise
                         skipped when github is unreachable)
* embedded in board   -> no violation, face reported as embedded ($KICAD_PYTHON with pcbnew embeds it)

Skipped without kicad-cli ($KIPR_KICAD_CLI / PATH), an error with $KIPR_REQUIRE_INTEGRATION=1.
kicad-cli must honour $FONTCONFIG_FILE (the official image's does; kipr-tools' wrapper needs a
copy that keeps it).
"""

import os
import shutil
import subprocess
import urllib.request

import pytest

from kipr.common import fonts
from kipr.common.kicad_cli import find as find_kicad_cli
from kipr.project import review
from tests.project import fonts_fixture as fx

CLI = find_kicad_cli()
if os.environ.get("KIPR_REQUIRE_INTEGRATION") and not CLI:
    raise RuntimeError("KIPR_REQUIRE_INTEGRATION is set but there is no kicad-cli")
pytestmark = pytest.mark.skipif(not CLI, reason="needs kicad-cli")


@pytest.fixture(scope="module")
def poppins_installed():
    if fonts.fc_match("Poppins"):
        pytest.skip("Poppins is installed on this machine; nothing would be missing")


def run(tmp_path, head_board, **kw):
    repo = fx.make_repo(str(tmp_path / "repo"), head_board)
    out = tmp_path / "out"
    doc = review.run(repo, "base", "head", str(out), kicad_cli=CLI, cache_dir=str(tmp_path / "cache"),
                     glb=False, log=lambda *_: None, **kw)
    (p,) = doc["projects"]
    return doc, p


def silk_edge(p):
    return [v for v in p["checks"]["drc"]["new"] if v["type"] == "silk_edge_clearance"]


def test_missing_font_is_flagged(tmp_path, poppins_installed):
    doc, p = run(tmp_path, fx.board("Poppins"), fetch_fonts=False)
    (v,) = silk_edge(p)
    assert v["font_dependent"] == ["Poppins"] and p["checks"]["drc"]["font_dependent"] == 1
    assert doc["fonts"]["missing"] == ["Poppins"] and p["fonts"]["missing"] == ["Poppins"]
    assert doc["fonts"]["faces"][0]["substitute"]  # what kicad-cli said it used instead
    assert "Font 'Poppins' is not available in CI" in doc["fonts"]["warning"]
    assert p["summary"]["fonts_missing"] == 1


def test_fonts_dir(tmp_path, poppins_installed):
    doc, p = run(tmp_path, fx.board("Poppins"), font_dirs=[fx.FONTS], fetch_fonts=False)
    assert silk_edge(p) == [] and doc["fonts"]["missing"] == []
    assert doc["fonts"]["faces"][0]["status"] == "provided" and doc["fonts"]["warning"] is None


def test_fetch_from_mirror(tmp_path, poppins_installed, monkeypatch):
    monkeypatch.setenv("KIPR_GOOGLE_FONTS_URL", fx.make_mirror(tmp_path / "mirror"))
    doc, p = run(tmp_path, fx.board("Poppins"), font_cache=str(tmp_path / "fonts"))
    assert silk_edge(p) == [] and doc["fonts"]["missing"] == []
    assert doc["fonts"]["faces"][0]["status"] == "fetched"


def test_export_cache_key_includes_the_fonts(tmp_path, poppins_installed):
    """Same files, fonts added: the cached DRC without the font must not be reused."""
    cache = str(tmp_path / "cache")
    repo = fx.make_repo(str(tmp_path / "repo"), fx.board("Poppins"))
    kw = dict(kicad_cli=CLI, cache_dir=cache, glb=False, log=lambda *_: None)
    a = review.run(repo, "base", "head", str(tmp_path / "a"), fetch_fonts=False, **kw)
    b = review.run(repo, "base", "head", str(tmp_path / "b"), font_dirs=[fx.FONTS], fetch_fonts=False, **kw)
    assert len(silk_edge(a["projects"][0])) == 1 and silk_edge(b["projects"][0]) == []


def _github_reachable() -> bool:
    try:
        urllib.request.urlopen("https://raw.githubusercontent.com/google/fonts/" + fonts.google_fonts_ref()
                               + "/ofl/poppins/METADATA.pb", timeout=10).read(100)
        return True
    except OSError:
        return False


def test_fetch_from_google_fonts(tmp_path, poppins_installed):
    if not os.environ.get("KIPR_FONTS_NETWORK") and not _github_reachable():
        pytest.skip("no network (set KIPR_FONTS_NETWORK=1 to require it)")
    doc, p = run(tmp_path, fx.board("Poppins"), font_cache=str(tmp_path / "fonts"))
    (face,) = doc["fonts"]["faces"]
    assert face["status"] == "fetched" and face["license"] == "OFL" and face["source"].endswith("ofl/poppins")
    assert silk_edge(p) == [] and doc["fonts"]["missing"] == []


def kicad_python():
    for cand in (os.environ.get("KICAD_PYTHON"), "/workspace/projects/kipr-tools/bin/kicad-python"):
        if cand and shutil.which(cand):
            r = subprocess.run([cand, "-c", "import pcbnew"], capture_output=True)
            if r.returncode == 0:
                return cand
    return None


def test_embedded_font(tmp_path, poppins_installed):
    """KiCad uses fonts embedded in the board (only KiCad writes valid checksums, so pcbnew
    embeds it here from a board with (embedded_fonts yes))."""
    py = kicad_python()
    if not py:
        pytest.skip("needs KiCad's python with pcbnew ($KICAD_PYTHON)")
    fd = fonts.FontDir(str(tmp_path / "fd"))
    fd.add(fx.POPPINS_BOLD, "p")
    src = tmp_path / "src.kicad_pcb"
    src.write_text(fx.board("Poppins").rstrip()[:-1] + "\t(embedded_fonts yes)\n)\n")
    dst = tmp_path / "embedded.kicad_pcb"
    subprocess.run([py, "-c", "import pcbnew, sys; b = pcbnew.LoadBoard(sys.argv[1]); b.EmbedFonts(); "
                    "pcbnew.SaveBoard(sys.argv[2], b)", str(src), str(dst)], check=True,
                   env={**os.environ, "FONTCONFIG_FILE": fd.conf}, capture_output=True)
    text = dst.read_text()
    assert '(type font)' in text
    doc, p = run(tmp_path, text, fetch_fonts=False)
    assert silk_edge(p) == [] and doc["fonts"]["missing"] == []
    assert doc["fonts"]["faces"][0]["status"] == "embedded"
