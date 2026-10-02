"""Library review: kicad-cli reference SVGs (--use-kicad-cli) of a footprint whose text uses an
outline font. With the font missing the item gets a warning and the fonts report lists it;
with --fonts it is installed for kicad-cli. Needs kicad-cli ($KIPR_KICAD_CLI / PATH) and fontconfig.
"""

from __future__ import annotations

import os
import shutil
from types import SimpleNamespace

import pytest

from kipr.common import fonts
from kipr.common.kicad_cli import find as find_kicad_cli
from kipr.library.render import kicad_cli_export

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "project", "fixtures", "fonts")
CLI = find_kicad_cli()
pytestmark = pytest.mark.skipif(not CLI or not shutil.which("fc-match"), reason="needs kicad-cli and fc-match")

FP = '''(footprint "Label"
	(version 20241229)
	(generator "kipr-test")
	(layer "F.Cu")
	(fp_text user "PE4302"
		(at 0 0 0)
		(layer "F.SilkS")
		(uuid "bbecef3e-27d2-4093-801d-5e287bbc9f1b")
		(effects
			(font
				(face "Poppins")
				(size 1 1)
				(thickness 0.2)
				(bold yes)
			)
		)
	)
	(pad "1" smd rect
		(at 0 2)
		(size 1 1)
		(layers "F.Cu")
	)
)
'''


def export(tmp_path, **kw):
    out = tmp_path / "out"
    (out / "items" / "label").mkdir(parents=True)
    it = SimpleNamespace(id="fp:Lib:Label", kind="footprint", library="Lib", name="Label", head_text=FP, base_text=None)
    entry = {"id": it.id, "slug": "label"}
    rep = kicad_cli_export.export(CLI, None, "h", "b", [it], [entry], str(out), log=lambda *_: None, **kw)
    return entry, rep


def test_missing_font_warns(tmp_path):
    if fonts.fc_match("Poppins"):
        pytest.skip("Poppins is installed on this machine")
    entry, rep = export(tmp_path, fetch_fonts=False)
    assert entry["renders_kicad_cli"]["head"]
    assert rep["missing"] == ["Poppins"] and "not available in CI" in rep["warning"]
    assert any("font 'Poppins' is not available" in w for w in entry.get("warnings", []))


def test_fonts_dir_installs_it(tmp_path):
    entry, rep = export(tmp_path, font_dirs=[FONTS], fetch_fonts=False)
    assert entry["renders_kicad_cli"]["head"] and rep["missing"] == []
    assert not any("font" in w for w in entry.get("warnings", []))
