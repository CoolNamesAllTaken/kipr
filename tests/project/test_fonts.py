"""kipr.common.fonts without KiCad: face collector, embedded fonts, Google Fonts fetcher (file://
mirror), private fontconfig, DRC marking, and the warning in the CI glue / report."""

import json
import os
import shutil

import pytest

from kipr.common import fonts
from kipr.project.ci import job_summary, make_comment
from kipr.project.ci.common import font_warning
from kipr.project import report
from tests.project import fonts_fixture as fx


def test_faces_in_collects_faces_skips_stroke_font_and_unescapes():
    text = ('(kicad_sch (text "a" (effects (font (face "Poppins") (size 1 1))))'
            ' (symbol (property "Value" "x" (effects (font (face "Roboto Mono")))))'
            ' (text "b" (effects (font (face "KiCad Font"))))'
            ' (text "c" (effects (font (face "My \\"Quoted\\" Face")))))')
    assert fonts.faces_in(text) == {"Poppins", "Roboto Mono", 'My "Quoted" Face'}
    assert fonts.faces_in('(kicad_pcb (gr_text "x" (effects (font (size 1 1)))))') == set()


def test_footprint_and_symbol_texts_in_boards_and_schematics_count():
    pcb = fx.board("Poppins ExtraBold").replace(
        "(net 0 \"\")", '(net 0 "")\n\t(footprint "R" (property "Reference" "R1" (effects (font (face "Inter")))))')
    need, emb = fonts.needed_faces({"head:b.kicad_pcb": pcb})
    assert need == {"Inter": ["head:b.kicad_pcb"], "Poppins ExtraBold": ["head:b.kicad_pcb"]} and emb == {}


def test_font_names_reads_the_name_table():
    with open(fx.POPPINS_BOLD, "rb") as fh:
        names = fonts.font_names(fh.read())
    assert "Poppins" in names and "Poppins Bold" in names
    assert fonts.font_names(b"not a font") == set()


def test_embedded_font_covers_its_own_file_only():
    zstd = pytest.importorskip("zstandard")
    with open(fx.POPPINS_BOLD, "rb") as fh:
        data = zstd.ZstdCompressor().compress(fh.read())
    pcb = fx.board("Poppins", embed=data, embed_name="whatever.ttf")  # family read from the font itself
    assert fonts.embedded_families(pcb) >= {"poppins", "poppinsbold"}
    sch = '(kicad_sch (text "x" (effects (font (face "Poppins")))))'
    need, emb = fonts.needed_faces({"head:a.kicad_pcb": pcb, "head:a.kicad_sch": sch})
    assert emb == {"Poppins": ["head:a.kicad_pcb"]} and need == {"Poppins": ["head:a.kicad_sch"]}


def test_embedded_font_without_data_falls_back_to_the_file_name():
    text = ('(kicad_pcb (gr_text "x" (effects (font (face "Poppins ExtraBold"))))\n(embedded_files (file (name '
            '"Poppins-ExtraBold.ttf") (type font) (checksum "00"))))')
    need, emb = fonts.needed_faces({"p": text})
    assert need == {} and emb == {"Poppins ExtraBold": ["p"]}


def test_candidates_only_drop_style_words():
    assert fonts.candidates("Poppins ExtraBold") == [("poppinsextrabold", []), ("poppins", ["ExtraBold"])]
    assert fonts.candidates("Roboto Mono") == [("robotomono", [])]
    assert fonts.candidates("Open Sans SemiBold Italic")[-1] == ("opensans", ["SemiBold", "Italic"])


def test_parse_metadata():
    m = fonts.parse_metadata('name: "Poppins"\nlicense: "OFL"\nfonts {\n  name: "Poppins"\n  filename: "A.ttf"\n}\n'
                             'fonts {\n  filename: "B.ttf"\n}\n')
    assert m == {"name": "Poppins", "license": "OFL", "files": ["A.ttf", "B.ttf"]}


def test_google_fonts_fetch_from_a_mirror(tmp_path):
    url = fx.make_mirror(tmp_path / "mirror")
    g = fonts.GoogleFonts(str(tmp_path / "cache"), ref="0" * 40, base_url=url)
    m = g.fetch("Poppins ExtraBold")  # ofl/poppinsextrabold doesn't exist, ofl/poppins does
    assert m["family"] == "Poppins" and m["license"] == "OFL" and m["files"] == ["Poppins-Bold.ttf"]
    assert sorted(os.listdir(m["dir"])) == [".kipr-font.json", "OFL.txt", "Poppins-Bold.ttf"]
    assert g.fetch("Nope Sans") is None
    shutil.rmtree(tmp_path / "mirror")  # served from the cache now, misses too
    assert fonts.GoogleFonts(str(tmp_path / "cache"), ref="0" * 40, base_url=url).fetch("Poppins")["family"] == "Poppins"
    assert fonts.GoogleFonts(str(tmp_path / "cache"), ref="0" * 40, base_url=url).fetch("Nope Sans") is None


def test_google_fonts_rejects_other_licenses_and_unsafe_names(tmp_path):
    url = fx.make_mirror(tmp_path / "m1", license="UFL")
    assert fonts.GoogleFonts(str(tmp_path / "c1"), ref="1" * 40, base_url=url).fetch("Poppins") is None
    url = fx.make_mirror(tmp_path / "m2", files=("../evil.ttf",))
    assert fonts.GoogleFonts(str(tmp_path / "c2"), ref="2" * 40, base_url=url).fetch("Poppins") is None
    assert not (tmp_path / "c2" / "evil.ttf").exists()


def test_pinned_ref():
    ref = fonts.google_fonts_ref()
    assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref)


needs_fc = pytest.mark.skipif(not shutil.which("fc-match"), reason="needs fontconfig's fc-match")


@needs_fc
def test_font_dir_makes_a_face_available(tmp_path):
    assert fonts.fc_match("Poppins") is False or pytest.skip("Poppins is installed on this machine")
    fd = fonts.FontDir(str(tmp_path / "f"))
    fd.add(fx.POPPINS_BOLD, "x")
    assert fonts.fc_match("Poppins", fd.conf) is True
    assert fd.env == {"FONTCONFIG_FILE": fd.conf} and len(fd.fingerprint()) == 16


@needs_fc
def test_setup_statuses(tmp_path):
    if fonts.fc_match("Poppins"):
        pytest.skip("Poppins is installed on this machine")
    need = {"Poppins": ["head:a.kicad_pcb"], "Nope Sans": ["head:a.kicad_pcb"]}
    r = fonts.setup(need, {"Inter": ["head:b.kicad_pcb"]}, str(tmp_path / "a"), fetch=False, log=lambda *_: None)
    assert {f: v["status"] for f, v in r.faces.items()} == {"Poppins": "missing", "Nope Sans": "missing", "Inter": "embedded"}
    r = fonts.setup(need, {}, str(tmp_path / "b"), font_dirs=[fx.FONTS], fetch=False, log=lambda *_: None)
    assert r.faces["Poppins"]["status"] == "provided" and r.missing() == ["Nope Sans"] and r.env
    g = fonts.GoogleFonts(str(tmp_path / "cache"), ref="3" * 40, base_url=fx.make_mirror(tmp_path / "m"))
    r = fonts.setup(need, {}, str(tmp_path / "c"), fetch=True, fetcher=g, log=lambda *_: None)
    assert r.faces["Poppins"]["status"] == "fetched" and r.faces["Poppins"]["license"] == "OFL"
    assert r.missing() == ["Nope Sans"]
    assert fonts.fc_match("Poppins", r.fontdir.conf)


def test_substitutions():
    msg = "00:26:23: Font 'Poppins' not found; substituting 'DejaVu Sans Bold'.\nFound 13 violations"
    assert fonts.substitutions(msg) == {"Poppins": "DejaVu Sans Bold"}


def test_mark_font_dependent_by_uuid_and_by_text():
    idx = fonts.text_index(fx.board("Poppins"), ["Poppins"])
    assert idx["uuids"] == {"bbecef3e-27d2-4093-801d-5e287bbc9f1b": "Poppins"} and idx["texts"] == {"PE4302": "Poppins"}
    vs = [{"type": "silk_edge_clearance", "items": ["Rectangle on Edge.Cuts", "PCB text 'PE4302' on F.Silkscreen"],
           "uuids": ["11111111-1111-4111-8111-111111111111", "bbecef3e-27d2-4093-801d-5e287bbc9f1b"]},
          {"type": "silk_overlap", "items": ["PCB text 'PE4302' on F.Silkscreen"], "uuids": []},
          {"type": "clearance", "items": ["Track", "Pad 1"], "uuids": ["x"]}]
    assert fonts.mark_font_dependent(vs, idx) == 2
    assert [v.get("font_dependent") for v in vs] == [["Poppins"], ["Poppins"], None]
    assert fonts.text_index(fx.board("Poppins"), ["Inter"]) == {"uuids": {}, "texts": {}}


def _doc(missing=("Poppins",)):
    v = {"severity": "error", "type": "silk_edge_clearance", "description": "Silkscreen clipped by board edge",
         "items": ["Rectangle on Edge.Cuts", "PCB text 'PE4302' on F.Silkscreen"], "uuids": [], "pos_mm": [38.7, 64.8],
         "sheet": None, "category": "violation", "font_dependent": list(missing)}
    return {"version": 1, "tool": {"kicad": "10.0.6"}, "base": {"short": "aaaaaaa"}, "head": {"short": "bbbbbbb", "sha": "b" * 40},
            "errors": [], "fonts": {"faces": [], "missing": list(missing)},
            "projects": [{"slug": "t", "name": "fonts_test", "path": "fonts_test", "status": "modified",
                          "summary": {"drc": {"new": 1, "fixed": 0}},
                          "checks": {"drc": {"new": [v], "fixed": [], "base_count": 0, "head_count": 1}, "erc": None},
                          "fonts": {"faces": list(missing), "missing": list(missing)}}]}


def test_comment_and_summary_carry_the_warning_and_the_mark():
    doc = _doc()
    body = make_comment.build_comment(doc)
    assert "Font 'Poppins' is not available in CI; KiCad substituted it" in body
    assert "silk_edge_clearance, silk_overlap, text clearance" in body
    assert "*font-dependent* (Poppins missing)" in body
    assert body.count("not available in CI") == 1
    ann = job_summary.annotations(doc)
    assert ann[0].startswith("::warning title=Fonts missing in CI::") and "[font-dependent]" in ann[1]
    assert "not available in CI" not in make_comment.build_comment(_doc(missing=()))


def test_untrusted_face_names_are_escaped():
    w = font_warning(_doc(missing=("<img src=x>@team",)))
    assert "<img" not in w and "@team" not in w


def test_report_shows_the_warning_and_badge(tmp_path):
    with open(tmp_path / "project-review.json", "w") as fh:
        json.dump(_doc(), fh)
    page, _ = report.build(tmp_path, 800, 800, None)
    assert "<b>Fonts:</b> Font &#x27;Poppins&#x27; is not available in CI" in page or "<b>Fonts:</b> Font 'Poppins' is not" in page
    assert ">font-dependent</span>" in page
