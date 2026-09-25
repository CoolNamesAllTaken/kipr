"""The shared change classification (kipr.project.classify) on every noise class, at the field,
footprint, symbol and BOM level. Synthetic snippets only."""
from kipr.project import classify, diff_sch

from .test_pcb_diff import diff as pcb_diff, fp
from .test_sch_diff import diff as sch_diff, load, root, sym


def fd(a, b, cfg=None):
    r = classify.field_diff(a, b, cfg)
    return r.significant_keys, r.minor_keys


def test_noise_fields_are_not_changes():
    assert fd({}, {"Sim.Library": "", "Sim.Name": ""}) == ([], [])          # appear empty
    assert fd({"Note": ""}, {}) == ([], [])                                 # disappear empty
    assert fd({}, {"Sim.Device": "R", "Sim.Pins": "1=+ 2=-"}) == ([], [])   # Sim.* even with a value
    assert fd({"ki_keywords": "a"}, {"ki_keywords": "b"}) == ([], [])
    assert fd({"MPN": " RC0603 "}, {"MPN": "RC0603"}) == ([], [])           # whitespace
    assert fd({"Description": "a  b"}, {"Description": "a b"}) == ([], [])
    assert fd({"MPN": "rc0603fr"}, {"MPN": "RC0603FR"}) == ([], [])         # part numbers: case-insensitive
    assert fd({"A": "1", "B": "2"}, {"B": "2", "A": "1"}) == ([], [])       # order


def test_significance_tiers():
    sig, minor = fd({"MPN": "a", "LCSC PN": "C1", "Manufacturer": "X", "Digi-Key_PN": "1", "Mouser Part Number": "2",
                     "Standard Cost": "0.1", "Datasheet": "u1", "Description": "d", "Note": "n", "KiLib_Generator": "g"},
                    {"MPN": "b", "LCSC PN": "C2", "Manufacturer": "Y", "Digi-Key_PN": "3", "Mouser Part Number": "4",
                     "Standard Cost": "0.2", "Datasheet": "u2", "Description": "e", "Note": "m", "KiLib_Generator": "h"})
    assert sorted(sig) == ["Digi-Key_PN", "LCSC PN", "MPN", "Manufacturer", "Mouser Part Number"]
    assert sorted(minor) == ["Datasheet", "Description", "KiLib_Generator", "Note", "Standard Cost"]
    assert fd({"Note": "OK"}, {"Note": "ok"}) == ([], ["Note"])             # other fields: case matters


def test_significant_fields_option():
    only_cost = classify.Config.from_option("standard*cost")
    assert fd({"MPN": "a", "Standard Cost": "1"}, {"MPN": "b", "Standard Cost": "2"}, only_cost) == (["Standard Cost"], ["MPN"])
    plus = classify.Config.from_option("+Tolerance")
    assert fd({"MPN": "a", "Tolerance": "1%"}, {"MPN": "b", "Tolerance": "5%"}, plus) == (["MPN", "Tolerance"], [])


def test_footprint_field_noise_and_minor():
    base = fp()
    sim = fp().replace("(attr smd)", '(property "Sim.Library" "" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "x")) (attr smd)')
    moved_text = fp().replace('(property "MPN" "RC0603" (at 0 0 0) (layer "F.Fab") (hide yes)',
                              '(property "MPN" "RC0603" (at 1 2 90) (layer "F.SilkS")')
    new_uuids = fp(uuid="u-r1").replace('(uuid "p3-u-r1")', '(uuid "zzz")')
    for head in (sim, moved_text, new_uuids):
        changes, comps = pcb_diff((base,), (head,))
        assert changes == [] and comps[0]["status"] == "unchanged"
    cost = fp().replace("(attr smd)", '(property "Standard Cost" "0.2" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "c")) (attr smd)')
    changes, comps = pcb_diff((base,), (cost,))
    assert comps[0]["minor"] is True and comps[0]["what"] == ["fields_minor"]
    assert changes[0]["minor"] is True and changes[0]["group"] == "minor"
    mpn = fp().replace('"MPN" "RC0603"', '"MPN" "RC0805"')
    _, comps = pcb_diff((base,), (mpn,))
    assert comps[0]["status"] == "changed" and "minor" not in comps[0]


def test_symbol_field_noise_and_minor():
    sim = sym(extra='(property "Sim.Library" "" (at 0 0 0) (hide yes)) (property "Sim.Name" "" (at 0 0 0) (hide yes))')
    hidden = sym().replace('(property "MPN" "RC0603FR-10k" (at 0 0 0))', '(property "MPN" "RC0603FR-10k" (at 5 5 90) (hide yes))')
    for head in (sim, hidden):
        (sheet,) = sch_diff([sym()], [head])
        assert all(c.get("minor") and c["kind"] == "other" for c in sheet["changes"])  # no symbol change
    (sheet,) = sch_diff([sym()], [sym(extra='(property "Datasheet" "http://x" (at 0 0 0))')])
    (c,) = sheet["changes"]
    assert c["what"] == "fields_minor" and c["minor"] is True
    (sheet,) = sch_diff([sym()], [sym(extra='(exclude_from_sim yes)')])
    assert all(c.get("minor") for c in sheet["changes"])


def test_bom_uses_the_same_rule():
    b = load({"demo.kicad_sch": root(sym("R1", uuid="a"), sym("R2", uuid="b", x=120), sym("R3", uuid="c", x=140))})
    h = load({"demo.kicad_sch": root(
        sym("R1", uuid="a", extra='(property "Sim.Library" "" (at 0 0 0)) (property "Sim.Name" "" (at 0 0 0))'),
        sym("R2", uuid="b", x=120, extra='(property "Standard Cost" "0.02" (at 0 0 0))'),
        sym("R3", uuid="c", x=140).replace("RC0603FR-10k", "RC0603JR-10k"))})
    rows = {r["key"]: r for r in diff_sch.diff_bom(b.components(), h.components())["rows"]}
    assert rows["R1"]["status"] == "unchanged" and rows["R1"]["what"] == []
    assert rows["R2"]["status"] == "changed" and rows["R2"]["minor"] is True and rows["R2"]["what"] == ["fields_minor"]
    assert rows["R3"]["status"] == "changed" and "minor" not in rows["R3"] and rows["R3"]["what"] == ["fields"]
