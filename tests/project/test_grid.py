"""Schematic connection-grid check (kipr.project.grid) on synthetic sheets."""

import pytest

from kipr.project import grid, sch

# Device:R: pins at (0, +-3.81); Q: an asymmetric FET (G left, S/D right), as in Transistor_FET:AO3401A
LIBS = """(lib_symbols
  (symbol "Device:R"
    (symbol "R_1_1" (pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))
                    (pin passive line (at 0 -3.81 90) (length 1.27) (name "~") (number "2"))))
  (symbol "Q:FET"
    (symbol "FET_1_1" (pin input line (at -5.08 0 0) (length 2.54) (name "G") (number "1"))
                      (pin passive line (at 2.54 -5.08 90) (length 2.54) (name "S") (number "2"))
                      (pin passive line (at 2.54 5.08 270) (length 2.54) (name "D") (number "3"))))
  (symbol "Q:FET2" (extends "FET"))
  (symbol "U:DUAL"
    (symbol "DUAL_1_1" (pin input line (at -2.54 0 0) (length 2.54) (name "A") (number "1")))
    (symbol "DUAL_2_1" (pin input line (at -2.54 0.635 0) (length 2.54) (name "B") (number "2")))))
"""


def sym(ref="R1", x=101.6, y=50.8, rot=0, uuid=None, lib="Device:R", mirror=None, unit=1):
    m = f"(mirror {mirror})" if mirror else ""
    return f"""(symbol (lib_id "{lib}") (at {x} {y} {rot}) {m} (unit {unit}) (in_bom yes) (on_board yes)
  (uuid "{uuid or 'u-' + ref}")
  (property "Reference" "{ref}" (at 0 0 0)) (property "Value" "v" (at 0 0 0)))
"""


def wire(x0, y0, x1, y1, uuid="w1"):
    return f'(wire (pts (xy {x0} {y0}) (xy {x1} {y1})) (stroke (width 0) (type default)) (uuid "{uuid}"))\n'


def sheet_file(*parts, uuid="root-uuid"):
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uuid}") (paper "A4")\n{LIBS}'
            + "".join(parts) + '(sheet_instances (path "/" (page "1"))))')


def load(*parts, files=None):
    fs = {"demo.kicad_sch": sheet_file(*parts)}
    fs.update(files or {})
    return sch.load_hierarchy(fs.get, "demo.kicad_sch", "demo")


def run(base_parts, head_parts, mode="changed", **kw):
    b = load(*base_parts) if base_parts is not None else None
    return grid.check(b, load(*head_parts), "boards/demo", mode, **kw)


def test_off_grid():
    g = 1.27
    assert grid.off_grid(101.6, g) == 0.0 and grid.off_grid(-2.54, g) == 0.0
    assert grid.off_grid(101.6004, g) == 0.0  # float noise
    assert grid.off_grid(100.965, g) == pytest.approx(0.635) or grid.off_grid(100.965, g) == pytest.approx(-0.635)
    assert grid.off_grid(101.8, g) == pytest.approx(0.2)


def test_on_grid_symbol_is_clean_and_off_grid_symbol_is_one_finding():
    res = run(None, [sym("R1"), sym("R2", x=110.0, y=50.8)], mode="all")
    assert res["mode"] == "all" and res["grid_mm"] == 1.27 and res["checked"] == 4
    (f,) = res["items"]
    assert (f["kind"], f["ref"], f["change"], f["severity"], f["off_count"]) == ("symbol", "R2", "added", "warning", 2)
    assert [p["name"] for p in f["points"]] == ["1", "2"]
    assert f["points"][0]["pos_mm"] == [110.0, 46.99] and f["points"][0]["off_mm"] == [pytest.approx(-0.49), 0]
    assert f["file"] == "boards/demo/demo.kicad_sch" and f["line"] == 17 and f["uuid"] == "u-R2"
    assert f["sheet"] == "root" and f["sheets"] == ["root"] and f["pos_mm"] == [110.0, 50.8]
    assert f["detail"].startswith("2 of 2 pins off the 50 mil grid, e.g. pin 1 at (110, 46.99)")
    assert res["sheets"] == [{"id": "root", "file": "boards/demo/demo.kicad_sch", "count": 1}]
    assert res["count"] == 1 and res["points"] == 2


def test_rotated_and_mirrored_pins_follow_kicad():
    # Q2 of a real board: (at 502.92 41.91 270) (mirror x); its wires end at these three points
    res = run(None, [sym("Q2", x=502.92, y=41.91, rot=270, lib="Q:FET", mirror="x")], mode="all")
    assert res["count"] == 0 and res["checked"] == 3
    (it,) = grid.collect(load(sym("Q2", x=502.92, y=41.91, rot=270, lib="Q:FET", mirror="x")))
    assert [(n, round(x, 2), round(y, 2)) for n, x, y in it.points] == [
        ("1", 502.92, 46.99), ("2", 497.84, 39.37), ("3", 508.0, 39.37)]
    (it,) = grid.collect(load(sym("Q3", x=100, y=50, rot=90, lib="Q:FET", mirror="y")))
    assert [(n, round(x, 2), round(y, 2)) for n, x, y in it.points] == [
        ("1", 100.0, 55.08), ("2", 94.92, 47.46), ("3", 105.08, 47.46)]
    (it,) = grid.collect(load(sym("Q4", x=100, y=50, rot=180, lib="Q:FET2")))  # derived symbol: parent's pins
    assert [(n, round(x, 2), round(y, 2)) for n, x, y in it.points] == [
        ("1", 105.08, 50.0), ("2", 97.46, 44.92), ("3", 97.46, 55.08)]


def test_units_use_their_own_pins():
    res = run(None, [sym("U1", x=101.6, y=50.8, lib="U:DUAL", unit=1, uuid="a"),
                     sym("U1", x=101.6, y=63.5, lib="U:DUAL", unit=2, uuid="b")], mode="all")
    (f,) = res["items"]
    assert f["uuid"] == "b" and f["points"][0]["name"] == "2" and f["points"][0]["pos_mm"] == [99.06, 62.865]


def test_changed_mode_flags_only_added_or_moved_items():
    legacy = [sym("R1", x=100.5), sym("R2"), wire(100.5, 46.99, 100.5, 40.0, uuid="w-legacy")]
    head = [sym("R1", x=100.5), sym("R2", x=110.0), wire(100.5, 46.99, 100.5, 40.0, uuid="w-legacy"),
            sym("R3", x=120.0, uuid="new")]
    res = run(legacy, head)
    assert [(f["ref"], f["change"]) for f in res["items"]] == [("R2", "moved"), ("R3", "added")]
    everything = run(legacy, head, mode="all")
    assert {(f["ref"] or f["kind"], f["change"]) for f in everything["items"]} == {
        ("R1", "unchanged"), ("R2", "moved"), ("R3", "added")}
    assert everything["checked"] > res["checked"]
    # the legacy wire joins legacy R1's finding in "all" mode
    r1 = next(f for f in everything["items"] if f["ref"] == "R1")
    assert [(r["kind"], r["change"]) for r in r1["related"]] == [("wire", "unchanged")]
    assert run(legacy, head, mode="off") is None


def test_uuid_match_checks_only_new_points_and_position_fallback():
    base = [wire(100.33, 40.64, 120.65, 40.64, uuid="w1"), '(junction (at 100.33 40.64) (uuid "j-old"))']
    # w1 keeps its off-grid legacy end and gets a new off-grid end; the junction is re-created (new uuid, same place)
    head = [wire(100.33, 40.64, 121.0, 40.64, uuid="w1"), '(junction (at 100.33 40.64) (uuid "j-new"))']
    (f,) = run(base, head)["items"]
    assert (f["kind"], f["change"], f["off_count"], f["related"]) == ("wire", "moved", 1, [])
    assert f["points"][0]["pos_mm"] == [121.0, 40.64]
    assert run(base, base)["items"] == []


def test_labels_junctions_no_connects_bus_entries_and_sheet_pins():
    parts = [
        '(label "A" (at 100.965 50.8 0) (uuid "l1"))',
        '(global_label "B" (shape input) (at 101.6 60.96 0) (uuid "l2"))',
        '(hierarchical_label "C" (shape input) (at 101.6 70.0 0) (uuid "l3"))',
        '(junction (at 130.0 30.48) (uuid "j1"))',
        '(no_connect (at 140.97 30.0) (uuid "n1"))',
        '(bus_entry (at 150.0 30.48) (size 2.54 2.54) (uuid "b1"))',
        '(bus (pts (xy 160.02 30.48) (xy 160.02 40.0)) (uuid "bus1"))',
        '(sheet (at 50.8 101.6) (size 25.4 12.7) (uuid "sh1") (property "Sheetname" "sub" (at 0 0 0))'
        ' (property "Sheetfile" "sub.kicad_sch" (at 0 0 0)) (pin "IN" input (at 50.8 104.14 180) (uuid "p1"))'
        ' (pin "OUT" output (at 76.2 105.0 0) (uuid "p2")))',
    ]
    files = {"sub.kicad_sch": sheet_file(uuid="sub-uuid")}
    res = grid.check(None, load(*parts, files=files), "", "all")
    got = {(f["kind"], f["text"] or f["ref"]): f for f in res["items"]}
    assert set(got) == {("label", "A"), ("hierarchical_label", "C"), ("junction", None), ("no_connect", None),
                        ("bus_entry", None), ("bus", None), ("sheet", "sub")}
    assert got[("sheet", "sub")]["points"] == [{"name": "OUT", "pos_mm": [76.2, 105.0], "off_mm": [0, pytest.approx(-0.41)]}]
    assert got[("bus_entry", None)]["off_count"] == 2 and got[("bus", None)]["off_count"] == 1
    assert "sheet pin OUT" in got[("sheet", "sub")]["detail"] and got[("sheet", "sub")]["file"] == "demo.kicad_sch"


def test_wiring_on_an_off_grid_symbol_is_grouped_with_it():
    # R1 moved 0.5 mm off the grid with its wires and a label: one finding, not five
    head = [sym("R1", x=102.1, uuid="r1"), wire(102.1, 46.99, 102.1, 40.0, uuid="wa"),
            wire(102.1, 54.61, 102.1, 60.0, uuid="wb"), '(label "N" (at 102.1 40.0 0) (uuid "lab"))',
            wire(102.1, 40.0, 110.0, 40.0, uuid="wc"),
            sym("R2", x=120.1, uuid="r2"), wire(102.1, 60.0, 120.1, 54.61, uuid="wd")]
    res = run(None, head, mode="all")
    assert [(f["kind"], f["ref"]) for f in res["items"]] == [("symbol", "R1"), ("symbol", "R2")]
    r1, r2 = res["items"]
    assert sorted(r["kind"] for r in r1["related"]) == ["label", "wire", "wire", "wire"]
    assert "also 1 label, 3 wires attached" in r1["detail"]
    # wd touches R2's pin and wb (R1's group): it goes with R2, and the two symbols stay separate
    assert [(r["kind"], r["uuid"]) for r in r2["related"]] == [("wire", "wd")]
    assert res["points"] == sum(f["off_count"] + sum(r["off_count"] for r in f["related"]) for f in (r1, r2)) == 13


def test_shared_sheet_is_reported_once():
    sub = sheet_file(sym("R9", x=100.5, uuid="r9"), uuid="sub-uuid")
    parts = [f'(sheet (at {x} 50.8) (size 20.32 10.16) (uuid "{u}") (property "Sheetname" "{n}" (at 0 0 0))'
             f' (property "Sheetfile" "sub.kicad_sch" (at 0 0 0)))' for x, u, n in ((25.4, "s1", "left"), (76.2, "s2", "right"))]
    res = grid.check(None, load(*parts, files={"sub.kicad_sch": sub}), "", "all")
    (f,) = res["items"]
    assert f["sheet"] == "root/left" and f["sheets"] == ["root/left", "root/right"] and f["file"] == "sub.kicad_sch"


def test_grid_size_is_configurable():
    parts = [sym("R1", x=101.6 + 0.635)]
    assert run(None, parts, mode="all")["count"] == 1
    assert run(None, parts, mode="all", grid_mil=25)["count"] == 0
    assert run(None, [sym("R1", x=101.6 + 1.27)], mode="all", grid_mil=100)["count"] == 1
    with pytest.raises(ValueError):
        run(None, parts, mode="sometimes")


POWER = """(symbol "power:GND" (power)
    (symbol "GND_1_1" (pin power_in line (at 0 0 270) (length 0) (hide yes) (name "GND") (number "1"))))"""


def test_power_symbols_join_the_part_they_sit_on():
    parts = [sym("C10", x=186.055, y=180.34, uuid="c10"), sym("#PWR1", x=186.055, y=184.15, lib="power:GND", uuid="p1"),
             sym("#PWR2", x=150.5, y=100.0, lib="power:GND", uuid="p2")]
    head = sheet_file(*parts).replace("(lib_symbols", "(lib_symbols " + POWER, 1)
    res = grid.check(None, sch.load_hierarchy({"demo.kicad_sch": head}.get, "demo.kicad_sch", "demo"), "", "all")
    lone, c10 = res["items"]  # sorted by position on the sheet
    assert (c10["ref"], [r["ref"] for r in c10["related"]]) == ("C10", ["#PWR1"])
    assert c10["related"][0]["power"] is True and "also 1 power symbol attached" in c10["detail"]
    assert (lone["ref"], lone["power"]) == ("#PWR2", True) and lone["detail"].startswith("its pin off the 50 mil grid")
