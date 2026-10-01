"""Re-encode detection (kipr/library/render/reencode.py): KiCad re-saves vs real edits.

Fixtures (tests/library/fixtures/reencode, from PantsForBirds/kicad-libs, MIT):
  Custom_RF_Amplifier.k8.kicad_sym    the library at kicad-libs 0dc129f (KiCad 8, format 20231120)
  Custom_RF_Amplifier.k10.kicad_sym   `kicad-cli sym upgrade` of it by KiCad 10.0.6; byte-identical to what
                                      the KiCad 10 symbol editor wrote for these symbols in kicad-libs PR #13
  SOP-8_3.76x4.96mm_P1.27mm.k7.kicad_mod / .k10.kicad_mod   a KiCad 7 footprint and its `fp upgrade`

The tests use a fake upgrader that returns the committed KiCad output, so they need no KiCad;
test_fixtures_match_real_kicad_cli checks the fixtures against a real kicad-cli when one is found.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from kipr.common import kicad_cli
from kipr.common.sexpr import parse
from kipr.library.render import reencode as rc
from kipr.library.render.sym import parse_library

FX = Path(__file__).parent / "fixtures" / "reencode"
SYM8 = (FX / "Custom_RF_Amplifier.k8.kicad_sym").read_text(encoding="utf-8")
SYM10 = (FX / "Custom_RF_Amplifier.k10.kicad_sym").read_text(encoding="utf-8")
FP7 = (FX / "SOP-8_3.76x4.96mm_P1.27mm.k7.kicad_mod").read_text(encoding="utf-8")
FP10 = (FX / "SOP-8_3.76x4.96mm_P1.27mm.k10.kicad_mod").read_text(encoding="utf-8")
FP_PATH = "lib_fp/Custom_Package_SO.pretty/SOP-8_3.76x4.96mm_P1.27mm.kicad_mod"


class FakeUpgrader:
    """Stands in for rc.Upgrader: 'upgrades' known texts to the committed KiCad 10 output."""

    def __init__(self, table: dict[str, str] | None = None, version: str = "10.0.6"):
        self.table = {SYM8: SYM10, FP7: FP10} if table is None else table
        self.version = version
        self.available = True
        self.calls: list[str] = []

    def symbol_library(self, text):
        self.calls.append("symbol")
        out = self.table.get(text)
        return (out, "") if out is not None else (None, "fake: unknown input")

    def footprint(self, text, filename):
        self.calls.append("footprint")
        out = self.table.get(text)
        return (out, "") if out is not None else (None, "fake: unknown input")


def classify(name, base=SYM8, head=SYM10, upgrader="fake", render=None):
    br, hr = parse(base), parse(head)
    up = FakeUpgrader() if upgrader == "fake" else upgrader
    return rc.classify_symbol(name, br, parse_library(br), hr, parse_library(hr), base, up,
                              render_identical=render, head_text=head)


def edit(text: str, old: str, new: str, count: int = 1) -> str:
    assert text.count(old) >= 1, old
    return text.replace(old, new, count)


def sym_block(text: str, name: str) -> str:
    root = parse(text)
    node = parse_library(root)[name]
    return text[node.start:node.end]


def edit_in(text: str, name: str, old: str, new: str) -> str:
    """Edit inside one symbol only (the other symbols stay pure re-encodes)."""
    blk = sym_block(text, name)
    assert old in blk, old
    return text.replace(blk, blk.replace(old, new, 1), 1)


# --------------------------------------------------------------------------- pure re-encodes

@pytest.mark.parametrize("name", ["AD8314", "BLB01"])
def test_pure_reencode_is_reencoded(name):
    r = classify(name)
    assert r["reencoded"] is True and r["method"] == "reference-upgrade"
    assert r["differences"] == []
    assert r["explanation"] == "file format upgraded 20231120 → 20251024 by KiCad 10.0; no content change"
    assert (r["base_format"], r["head_format"]) == ("20231120", "20251024")


def test_reformatted_head_is_still_reencoded():
    """Whitespace and number spelling are not content."""
    head = SYM10.replace("\t", "  ").replace("(length 2.54)", "(length 2.5400)")
    assert classify("AD8314", head=head)["reencoded"] is True


def test_footprint_pure_reencode_with_uuid_churn():
    # KiCad gives every object a fresh uuid when it loads a KiCad 7 file: not a change
    churned = re.sub(r'\(uuid "[^"]+"\)', '(uuid "00000000-0000-0000-0000-000000000000")', FP10)
    assert churned != FP10
    for head in (FP10, churned):
        r = rc.classify_footprint(FP_PATH, parse(FP7), parse(head), FP7, FakeUpgrader())
        assert r["reencoded"] is True and r["method"] == "reference-upgrade", r
        assert r["explanation"].startswith("file format upgraded 20221018 → 20260206 by KiCad 10.0")


# --------------------------------------------------------------------------- re-encode + a real edit

# AD8314 pin 1 (RFIN) is at (-10.16 -1.27 0); its Value field is "AD8314" at (5.08 -13.97 0);
# its body is the rectangle (start -7.62 0) (end 7.62 -12.7).
EDITS = [
    ("moved pin", "(at -10.16 -1.27 0)", "(at -10.16 -3.81 0)", 'pin "1"', "at"),
    ("changed pin name", '(name "RFIN"', '(name "RF_IN"', 'pin "1"', "RF_IN"),
    ("changed property value", '(property "Value" "AD8314"', '(property "Value" "AD8313"', 'property "Value"', "AD8313"),
    ("moved property text", "(at 5.08 -13.97 0)", "(at 5.08 -16.51 0)", 'property "Value"', "-16.51"),
    ("changed graphic", "(end 7.62 -12.7)", "(end 7.62 -15.24)", "rectangle", "-15.24"),
    ("hid a pin", "(length 2.54)\n\t\t\t\t(name \"RFIN\"", "(length 2.54)\n\t\t\t\t(hide yes)\n\t\t\t\t(name \"RFIN\"",
     'pin "1"', "hide"),
    ("changed pin type", "(pin input line\n\t\t\t\t(at -10.16 -1.27 0)", "(pin passive line\n\t\t\t\t(at -10.16 -1.27 0)",
     'pin "1"', "passive"),
    ("excluded from BOM", "(in_bom yes)", "(in_bom no)", "in_bom", "no"),
]


@pytest.mark.parametrize("what,old,new,where,token", EDITS, ids=[e[0] for e in EDITS])
def test_reencode_plus_edit_is_modified(what, old, new, where, token):
    head = edit_in(SYM10, "AD8314", old, new)
    r = classify("AD8314", head=head)
    assert r["reencoded"] is False and r["method"] == "reference-upgrade"
    joined = "\n".join(r["differences"])
    assert where in joined and token in joined, joined
    # the untouched neighbour in the same re-saved file is still a pure re-encode
    assert classify("BLB01", head=head)["reencoded"] is True


def test_footprint_reencode_plus_edits_are_modified():
    for old, new, token in (("(at -2.5375 -1.905)", "(at -2.6 -1.905)", "-2.6"),      # moved pad 1
                            ("(size 1.975 0.65)", "(size 1.975 0.7)", "0.7"),            # resized a pad
                            ('(layer "F.SilkS")', '(layer "B.SilkS")', "B.SilkS")):     # a silk line to the back
        head = edit(FP10, old, new)
        r = rc.classify_footprint(FP_PATH, parse(FP7), parse(head), FP7, FakeUpgrader())
        assert r["reencoded"] is False, (old, r)
        assert token in "\n".join(r["differences"]), r["differences"]


def test_safety_net_wins_over_the_reference():
    """If 'KiCad's upgrade' equals the head but the base really differs, it stays modified."""
    base = edit_in(SYM8, "AD8314", '(name "RFIN"', '(name "RF_IN"')
    up = FakeUpgrader({base: SYM10})          # e.g. a wrong or stale upgrade output
    r = classify("AD8314", base=base, upgrader=up)
    assert r["reencoded"] is False
    assert "semantic comparison found differences" in r["note"]
    assert any("RF_IN" in d for d in r["differences"])


def test_derived_symbol_follows_its_parent():
    """A derived symbol is drawn from its parent: an edit there is not a re-encode of the child."""
    def add_child(text):
        child = ('\t(symbol "AD8314_ALT"\n\t\t(extends "AD8314")\n'
                 '\t\t(property "Reference" "U"\n\t\t\t(at 0 0 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
                 '\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n')
        i = text.rindex(")")
        return text[:i] + child + text[i:]
    base, head = add_child(SYM8), add_child(SYM10)
    up = FakeUpgrader({base: head})
    assert classify("AD8314_ALT", base=base, head=head, upgrader=up)["reencoded"] is True
    edited = edit_in(head, "AD8314", '(name "RFIN"', '(name "RF_IN"')
    r = classify("AD8314_ALT", base=base, head=edited, upgrader=up)
    assert r["reencoded"] is False
    assert any(d.startswith("parent symbol `AD8314`") and "RF_IN" in d for d in r["differences"])


# --------------------------------------------------------------------------- when the reference is unavailable

def test_no_kicad_cli_symbol_needs_semantics_and_pixels():
    assert classify("AD8314", upgrader=None, render=lambda: True)["method"] == "semantic+render"
    assert classify("AD8314", upgrader=None, render=lambda: True)["reencoded"] is True
    for pix in (False, None):
        r = classify("AD8314", upgrader=None, render=lambda: pix)
        assert r["reencoded"] is False and "reference upgrade was not available" in r["differences"][0]
    # a real edit is caught by the semantic comparison alone, renders are not even looked at
    head = edit_in(SYM10, "AD8314", "(at 5.08 -13.97 0)", "(at 5.08 -16.51 0)")
    looked = []
    r = classify("AD8314", head=head, upgrader=None, render=lambda: looked.append(1) or True)
    assert r["reencoded"] is False and not looked and any("-16.51" in d for d in r["differences"])


def test_no_kicad_cli_footprint_stays_modified():
    r = rc.classify_footprint(FP_PATH, parse(FP7), parse(FP10), FP7, None)
    assert r["reencoded"] is False
    assert "reference upgrade was not available" in r["differences"][0]


def test_other_kicad_version_is_not_a_reference():
    """kicad-cli writing another format than the head's is not 'the KiCad the head was saved with'."""
    k9 = SYM10.replace("(version 20251024)", "(version 20241209)").replace('"10.0"', '"9.0"')
    up = FakeUpgrader({SYM8: SYM10, k9: SYM10})
    r = classify("AD8314", head=k9, upgrader=up, render=lambda: True)
    assert r["method"] == "semantic+render" and r["reencoded"] is True
    assert "writes symbol format 20251024, the head is 20241209" in r["note"]
    r = classify("AD8314", head=k9, upgrader=up, render=lambda: False)
    assert r["reencoded"] is False
    fp9 = FP10.replace("(version 20260206)", "(version 20241229)")
    r = rc.classify_footprint(FP_PATH, parse(FP7), parse(fp9), FP7, FakeUpgrader())
    assert r["reencoded"] is False and "writes footprint format 20260206" in r["note"]


def test_failed_upgrade_is_not_a_reference():
    up = FakeUpgrader({})
    r = classify("AD8314", upgrader=up, render=lambda: True)
    assert r["method"] == "semantic+render" and "fake: unknown input" in r["note"]
    assert rc.classify_footprint(FP_PATH, parse(FP7), parse(FP10), FP7, up)["reencoded"] is False


# --------------------------------------------------------------------------- the normalisations, one by one

def sem_equal(a: str, b: str) -> bool:
    return rc.same_semantics(parse(a), parse(b))[0]


SYM = '(symbol "X" {flags} (property "Value" "X" {pid} (at 0 0 0) {phide} (effects (font (size 1.27 1.27) {font}) {ehide})) ' \
      '(symbol "X_1_1" (rectangle (start 0 0) (end 1 1) (stroke (width 0) (type default) {color}) (fill (type none))) ' \
      '(pin input line (at 0 0 0) (length 2.54) {pinhide} (name "A" (effects (font (size 1.27 1.27)))) ' \
      '(number "1" (effects (font (size 1.27 1.27)))))))'


def S(**kw):
    d = dict(flags="", pid="", phide="", font="", ehide="", color="", pinhide="")
    d.update(kw)
    return SYM.format(**d)


@pytest.mark.parametrize("a,b", [
    (S(pid="(id 1)"), S()),                                                        # property-id
    (S(ehide="hide"), S(ehide="(hide yes)")),                                      # flag-spelling
    (S(font="bold"), S(font="(bold yes)")),
    (S(pinhide="hide"), S(pinhide="(hide yes)")),
    (S(ehide="(hide yes)"), S(phide="(hide yes)")),                                # hide-location
    (S(flags="(in_bom yes) (on_board yes) (exclude_from_sim no) (in_pos_files yes) "
             "(duplicate_pin_numbers_are_jumpers no) (embedded_fonts no)"), S()),  # defaults
    (S(phide="(show_name no) (do_not_autoplace no) (hide no)"), S()),
    (S(font="(bold no) (italic no)"), S()),
    (S(color="(color 0 0 0 0)"), S()),                                             # default-color
    (S().replace("(at 0 0 0) (length 2.54)", "(at 0.0 -0 0.000) (length 2.540)"), S()),   # numbers
    (S().replace('(rectangle', '(uuid "abc") (rectangle'), S()),                   # uuid
    (S().replace('(property "Value"', '(property "Description" "" (at 0 0 0) (hide yes)) (property "Value"'), S()),  # empty-description
    (S().replace("(rectangle", "(arc (start 1 0) (mid 0 1) (end -1 0)) (rectangle"),                       # arc-direction
     S().replace("(rectangle", "(arc (start -1 0) (mid 0 1) (end 1 0)) (rectangle")),
])
def test_normalisation_equal(a, b):
    assert sem_equal(a, b), rc.same_semantics(parse(a), parse(b))[1]


@pytest.mark.parametrize("a,b", [
    (S(ehide="(hide yes)"), S()),                         # hidden vs visible
    (S(phide="(hide yes)"), S(phide="(hide no)")),
    (S(flags="(in_bom no)"), S()),                         # non-defaults are kept
    (S(flags="(exclude_from_sim yes)"), S()),
    (S(phide="(show_name yes)"), S()),
    (S(font="(bold yes)"), S()),
    (S(color="(color 255 0 0 1)"), S()),                   # a real colour
    (S().replace("(start 0 0) (end 1 1)", "(start 0 0) (end 1 1.0001)"), S()),
    (S().replace('(name "A"', '(name "B"'), S()),
    (S().replace('(property "Value"', '(property "Description" "x" (at 0 0 0) (hide yes)) (property "Value"'), S()),
    (S().replace("(rectangle", "(arc (start 1 0) (mid 0 1) (end -1 0)) (rectangle"),                       # mirrored arc
     S().replace("(rectangle", "(arc (start 1 0) (mid 0 -1) (end -1 0)) (rectangle")),
])
def test_normalisation_keeps_differences(a, b):
    assert not sem_equal(a, b)


def test_ki_description_rule_is_narrow():
    old = '(symbol "X" (property "ki_description" "Opamp" (at 0 0 0) (effects (font (size 1.27 1.27)) hide)))'
    new = '(symbol "X" (property "Description" "Opamp" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27)))))'
    assert sem_equal(old, new)
    # an empty Description next to ki_description (KiCad 7 writes both) is replaced by the converted one
    both = old.replace('(symbol "X" ', '(symbol "X" (property "Description" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide)) ')
    assert sem_equal(both, new)
    assert not sem_equal(old, new.replace('"Opamp"', '"Comparator"'))
    with_desc = old.replace('(symbol "X" ', '(symbol "X" (property "Description" "Other" (at 0 0 0)) ')
    assert not sem_equal(with_desc, new)                  # a non-empty Description is never replaced


def test_item_order_only_where_order_is_meaningless():
    a = '(symbol "X_1_1" (polyline (pts (xy 0 0) (xy 1 1))) (circle (center 0 0) (radius 1)))'
    b = '(symbol "X_1_1" (circle (center 0 0) (radius 1)) (polyline (pts (xy 0 0) (xy 1 1))))'
    assert sem_equal(a, b)
    c = '(symbol "X_1_1" (circle (center 0 0) (radius 1)) (polyline (pts (xy 1 1) (xy 0 0))))'
    assert not sem_equal(a, c)                             # point order is geometry
    pin = '(pin input line (at 0 0 0) (length 2.54))'
    assert not sem_equal(pin, pin.replace("input line", "line input"))   # atoms keep their order


def test_normalisation_list_is_documented():
    names = [n for n, _ in rc.NORMALISATIONS]
    for n in ("whitespace", "numbers", "uuid", "header", "property-id", "flag-spelling", "hide-location",
              "defaults", "default-color", "ki-description", "empty-description", "arc-direction", "item-order"):
        assert n in names
    doc = (Path(__file__).parents[2] / "docs" / "library.md").read_text(encoding="utf-8")
    for n, _ in rc.NORMALISATIONS + rc.FP_NORMALISATIONS:
        assert f"`{n}`" in doc, f"docs/library.md does not document the normalisation {n}"


@pytest.mark.parametrize("a,b", [
    ('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))',
     '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask" "F.Paste"))'),               # fp-pad-layers
    ('(fp_arc (start 1 0) (mid 0 1) (end -1 0) (layer "F.SilkS") (width 0.12))',
     '(fp_arc (start -1 0) (mid 0 1) (end 1 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))'),  # fp-arc-direction
    ('(fp_text user "%R" (at 0 0 -180) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
     '(fp_text user "${REFERENCE}" (at 0 0 180) (unlocked yes) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))'),
    ('(property "Datasheet" "" (at 0 0 0) (layer "F.Fab") (hide yes))', ""),                       # fp-empty-props
])
def test_footprint_normalisations(a, b):
    fa, fb = parse(f'(footprint "F" (layer "F.Cu") (attr smd) {a})'), parse(f'(footprint "F" (layer "F.Cu") (attr smd) {b})')
    assert rc.same_footprint_facts(fa, fb)[0], rc.same_footprint_facts(fa, fb)[1]


def test_footprint_facts_see_real_changes():
    pad = '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))'
    base = parse(f'(footprint "F" (layer "F.Cu") (attr smd) {pad})')
    for new in (pad.replace("(at 0 0)", "(at 0 0.1)"), pad.replace('"1"', '"2"'), pad.replace(' "F.Paste"', ""),
                pad.replace("rect", "roundrect")):
        ok, d = rc.same_footprint_facts(base, parse(f'(footprint "F" (layer "F.Cu") (attr smd) {new})'))
        assert not ok and d
    ok, d = rc.same_footprint_facts(base, parse(f'(footprint "F" (layer "F.Cu") (attr through_hole) {pad})'))
    assert not ok and "attr" in d[0]


# --------------------------------------------------------------------------- against a real kicad-cli

def real_kicad_cli():
    exe = kicad_cli.find()
    if not exe:
        pytest.skip("kicad-cli not found (set KIPR_KICAD_CLI)")
    up = rc.Upgrader(exe)
    if not (up.available and up.version.startswith("10.")):
        pytest.skip(f"needs KiCad 10's kicad-cli, found {up.version}")
    return up


def test_fixtures_match_real_kicad_cli():
    up = real_kicad_cli()
    out, err = up.symbol_library(SYM8)
    assert out == SYM10, err
    out, err = up.footprint(FP7, FP_PATH)
    assert out is not None, err
    assert rc.canonical(parse(out)) == rc.canonical(parse(FP10))   # same up to the fresh uuids
    assert parse(out).arg(0) == "SOP-8_3.76x4.96mm_P1.27mm"


def test_real_kicad_cli_end_to_end():
    up = real_kicad_cli()
    assert classify("AD8314", upgrader=up)["reencoded"] is True
    head = edit_in(SYM10, "AD8314", '(name "RFIN"', '(name "RF_IN"')
    assert classify("AD8314", head=head, upgrader=up)["reencoded"] is False
    r = rc.classify_footprint(FP_PATH, parse(FP7), parse(FP10), FP7, up)
    assert r["reencoded"] is True, r


KICAD_LIBS = os.environ.get("KIPR_TEST_KICAD_LIBS")


@pytest.mark.skipif(not KICAD_LIBS, reason="set KIPR_TEST_KICAD_LIBS to a kicad-libs clone with PR #13 fetched as pr13")
def test_kicad_libs_pr13(tmp_path):
    """kicad-libs PR #13: three symbols re-saved by KiCad 10 without edits, two symbols added."""
    import json
    import subprocess
    import sys
    real_kicad_cli()
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-c", "import sys; from kipr.cli import main; sys.exit(main())",
                        "library", "render", "--repo", KICAD_LIBS, "--base", "0dc129f", "--head", "pr13",
                        "--out", str(out), "--no-3d", "--no-preview"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    m = json.loads((out / "manifest.json").read_text())
    st = {i["id"]: i["status"] for i in m["items"]}
    assert {k for k, v in st.items() if v == "re-encoded"} == {
        "symbol:Custom_RF_Amplifier:AD8314", "symbol:Custom_RF_Amplifier:BLB01",
        "symbol:Custom_Power_Management:LM73100RPWR"}
    assert "modified" not in st.values()
    assert st["symbol:Custom_RF_Amplifier:PA-SOCKET-MSOP-8-0.65_AD8313"] == "added"
    assert st["symbol:Custom_Power_Management:TPS22918DBV"] == "added"
    notes = {f["path"]: f["note"] for f in m["reencoded_files"]}
    assert notes["lib_sch/Custom_RF_Amplifier.kicad_sym"].startswith(
        "Custom_RF_Amplifier.kicad_sym was re-saved by a newer KiCad (format 20231120 → 20251024")
