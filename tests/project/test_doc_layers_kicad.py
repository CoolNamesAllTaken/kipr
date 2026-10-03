"""Documentation-layer text outside the board, through a real kicad-cli: a synthetic board whose head
adds a fab note on Dwgs.User 50 mm right of the outline. The layer must be `modified`, its
`extent_mm` must cover the note, and the report's Dwgs.User head image must show it (it used to be
cropped to the board). Skipped without kicad-cli (an error with $KIPR_REQUIRE_INTEGRATION=1, as in CI).
"""
import base64
import io
import os
import re
import subprocess

import pytest

from kipr.common.kicad_cli import find as find_kicad_cli
from kipr.project import report, review

CLI = find_kicad_cli()
if os.environ.get("KIPR_REQUIRE_INTEGRATION") and not CLI:
    raise RuntimeError("KIPR_REQUIRE_INTEGRATION is set but there is no kicad-cli")
pytestmark = pytest.mark.skipif(not CLI, reason="needs kicad-cli")

BOARD = """(kicad_pcb (version 20250114) (generator "pcbnew") (generator_version "10.0")
  (general (thickness 1.6) (legacy_teardrops no))
  (paper "A4")
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (1 "F.Mask" user) (3 "B.Mask" user) (5 "F.SilkS" user "F.Silkscreen")
          (7 "B.SilkS" user "B.Silkscreen") (17 "Dwgs.User" user "User.Drawings") (25 "Edge.Cuts" user)
          (35 "F.Fab" user) (31 "F.CrtYd" user "F.Courtyard"))
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
  (gr_rect (start 30 30) (end 80 60) (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts") (uuid "e1"))
  (gr_text "BOARD" (at 55 45 0) (layer "F.SilkS") (uuid "s1") (effects (font (size 2 2) (thickness 0.3))))
{extra})
"""
NOTE = '  (gr_text "FAB NOTE" (at 150 45 0) (layer "Dwgs.User") (uuid "n1") (effects (font (size 3 3) (thickness 0.4))))\n'
NOTE_X, NOTE_Y = 150, 45  # centre of the note; the board ends at x 80


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    repo = tmp_path_factory.mktemp("repo")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "kipr tests")
    git(repo, "config", "user.email", "kipr-tests@example.invalid")
    (repo / "notes.kicad_pro").write_text("{}\n")
    for tag, extra in (("base", ""), ("head", NOTE)):
        (repo / "notes.kicad_pcb").write_text(BOARD.replace("{extra}", extra))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", tag)
        git(repo, "tag", tag)
    out = tmp_path_factory.mktemp("out")
    doc = review.run(str(repo), "base", "head", str(out), kicad_cli=CLI, glb=False, fast_checks=True,
                     cache_dir=str(tmp_path_factory.mktemp("cache")), fetch_fonts=False, log=lambda *_: None)
    return out, doc


def dwgs(doc):
    pcb = doc["projects"][0]["pcb"]
    return pcb, next(l for l in pcb["layers"] if l["id"] == "Dwgs.User")


def test_status_and_extent(out):
    _, doc = out
    pcb, l = dwgs(doc)
    assert l["kind"] == "user" and l["status"] == "modified"
    x, y, w, h = l["extent_mm"]
    assert x < NOTE_X < x + w and y < NOTE_Y < y + h, l["extent_mm"]
    assert x > 80  # only the note: the base side draws nothing on Dwgs.User
    assert pcb["board"]["origin_mm"] == [30, 30] and pcb["board"]["size_mm"] == [50, 30]


def test_report_image_shows_the_note(out):
    path, doc = out
    if report.Image is None or report.cairosvg is None:
        pytest.skip("needs Pillow + cairosvg")
    from PIL import Image
    html = report.make_report(path).read_text()
    pcb, l = dwgs(doc)
    x, y, w, h = report.layer_crop(pcb, l)
    sec = html.split("<h4>Dwgs.User", 1)[1].split("<h4>", 1)[0]
    uris = re.findall(r'src="data:image/png;base64,([^"]+)"', sec)
    assert len(uris) == 3
    head = Image.open(io.BytesIO(base64.b64decode(uris[1]))).convert("RGBA")
    s = head.width / w
    ex, ey, ew, eh = l["extent_mm"]
    box = tuple(int(v) for v in ((ex - x) * s, (ey - y) * s, (ex + ew - x) * s, (ey + eh - y) * s))
    px = head.crop(box).tobytes()
    lit = sum(1 for i in range(0, len(px), 4) if px[i] + px[i + 1] + px[i + 2] > 200)
    assert lit > 20, f"{lit} lit pixels where the note is"
