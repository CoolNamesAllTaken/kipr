import json

from kipr.project import diff_net, export
from kipr.project.sch import Sheet

NETLIST = """(export (version "E")
  (components (comp (ref "R1") (value "10k")))
  (nets
    (net (code "1") (name "/SDA") (class "Default") (node (ref "R1") (pin "1") (pintype "passive")) (node (ref "U1") (pin "5")))
    (net (code "2") (name "GND") (node (ref "U1") (pin "4")) (node (ref "C1") (pin "2")))))
"""


def test_parse_kicad_netlist():
    n = diff_net.parse_kicad_netlist(NETLIST)
    assert n == {"/SDA": {"R1.1", "U1.5"}, "GND": {"U1.4", "C1.2"}}


def test_netlist_modified_added_removed_and_moved_pins():
    base = {"/A": {"R1.1", "U1.1"}, "/B": {"R2.1", "U1.2"}, "/OLD": {"R9.1", "R9.2"}}
    head = {"/A": {"R1.1", "U1.1", "R2.1"}, "/B": {"U1.2"}, "/NEW": {"C5.1", "C5.2"}}
    d = diff_net.diff_netlists(base, head)
    by = {c["net"]: c for c in d["changes"]}
    assert by["/A"]["status"] == "modified" and by["/A"]["added"] == ["R2.1"]
    assert by["/B"]["removed"] == ["R2.1"]
    assert by["/NEW"]["status"] == "added" and by["/OLD"]["status"] == "removed"
    assert d["moved_pins"] == [{"pin": "R2.1", "from": "/B", "to": "/A"}]


def test_rename_detection():
    base = {"/SDA": {"R1.1", "U1.5", "J1.3"}, "Net-(R2-Pad1)": {"R2.1", "C3.1"}}
    head = {"/I2C_SDA": {"R1.1", "U1.5", "J1.3", "TP1.1"}, "Net-(C3-Pad1)": {"R2.1", "C3.1"}}
    d = diff_net.diff_netlists(base, head)
    # auto-named net renamed with the same pins: not a change at all
    assert [(c["net"], c["status"], c["renamed_from"], c["added"]) for c in d["changes"]] == [
        ("/I2C_SDA", "modified", "/SDA", ["TP1.1"])]
    assert d["moved_pins"] == []


def test_pure_rename():
    d = diff_net.diff_netlists({"/X": {"A.1", "B.1"}}, {"/Y": {"A.1", "B.1"}})
    assert d["changes"] == [{"net": "/Y", "status": "renamed", "added": [], "removed": [], "renamed_from": "/X"}]


def drc(*violations):
    return json.dumps({"coordinate_units": "mm", "violations": [
        {"type": t, "severity": "error", "description": d,
         "items": [{"description": i, "pos": {"x": x, "y": y}, "uuid": "u"} for i in items]}
        for t, d, x, y, items in violations], "unconnected_items": [], "schematic_parity": []})


def test_check_delta_matches_with_tolerance():
    base = diff_net.parse_report(drc(
        ("clearance", "Clearance violation (0.1500 mm)", 10, 10, ["Track [GND] on F.Cu, length 1.2345 mm", "Pad 1 of R1"]),
        ("silk_overlap", "Silkscreen overlap", 50, 50, ["Text R1", "Text R2"]),
        ("hole_clearance", "Hole clearance", 70, 70, ["Via [GND]"])), "drc")
    head = diff_net.parse_report(drc(
        # same clearance violation, the track got a bit longer and it moved 0.5 mm
        ("clearance", "Clearance violation (0.1500 mm)", 10.5, 10, ["Track [GND] on F.Cu, length 1.9 mm", "Pad 1 of R1"]),
        # the silk overlap moved far but involves the same items: still the same violation
        ("silk_overlap", "Silkscreen overlap", 80, 10, ["Text R1", "Text R2"]),
        ("courtyards_overlap", "Courtyards overlap", 30, 30, ["Footprint U1", "Footprint C1"])), "drc")
    d = diff_net.check_delta(base, head)
    assert (d["base_count"], d["head_count"]) == (3, 3)
    assert [v["type"] for v in d["new"]] == ["courtyards_overlap"]
    assert [v["type"] for v in d["fixed"]] == ["hole_clearance"]
    assert d["new"][0]["pos_mm"] == [30, 30] and d["new"][0]["items"] == ["Footprint U1", "Footprint C1"]


def test_erc_report_and_scale_fix():
    rep = json.dumps({"coordinate_units": "mm", "sheets": [{"path": "/", "uuid_path": "/x", "violations": [
        {"type": "pin_not_connected", "severity": "error", "description": "Pin not connected",
         "items": [{"description": "Symbol U4 Pin 4", "pos": {"x": 1.7145, "y": 1.4351}}]}]}]})
    v = diff_net.parse_report(rep, "erc")
    assert v[0]["sheet"] == "/" and v[0]["pos_mm"] == [1.7145, 1.4351]
    assert diff_net.fix_erc_scale(v, 297)
    assert v[0]["pos_mm"] == [171.45, 143.51]
    assert not diff_net.fix_erc_scale(v, 297)  # already plausible: untouched


def test_layer_and_sheet_file_maps():
    layers = [{"name": "F.Cu", "user_name": "top_copper"}, {"name": "F.SilkS", "user_name": "F.Silkscreen"},
              {"name": "Edge.Cuts", "user_name": None}, {"name": "In1.Cu", "user_name": None}]
    files = ["b-top_copper.gbr", "b-F_Silkscreen.gbr", "b-Edge_Cuts.gbr", "b-In1_Cu.gbr", "b-job.gbrjob"]
    assert export.layer_file_map(files, "b", layers, ".gbr") == {
        "F.Cu": "b-top_copper.gbr", "F.SilkS": "b-F_Silkscreen.gbr", "Edge.Cuts": "b-Edge_Cuts.gbr",
        "In1.Cu": "b-In1_Cu.gbr"}
    sheets = [Sheet("root", "demo", "demo.kicad_sch", "/r", "1", (297, 210), names=["demo"]),
              Sheet("root/amp", "Amp 1", "a.kicad_sch", "/r/a", "2", (297, 210), names=["demo", "Amp 1"]),
              Sheet("root/amp/x", "x", "x.kicad_sch", "/r/a/x", "3", (297, 210), names=["demo", "Amp 1", "x"])]
    got = export.sheet_file_map(["demo.svg", "demo-Amp 1.svg", "demo-Amp 1-x.svg"], "demo", sheets)
    assert got == {"root": "demo.svg", "root/amp": "demo-Amp 1.svg", "root/amp/x": "demo-Amp 1-x.svg"}


def test_same_content_ignores_dates_and_revision(tmp_path):
    a = tmp_path / "a.gbr"
    b = tmp_path / "b.gbr"
    c = tmp_path / "c.gbr"
    a.write_text("%TF.CreationDate,2026-09-24T20:57:52+00:00*%\n%TF.ProjectId,x,1234,E*%\n"
                 "G04 Created by KiCad (PCBNEW 10.0.6) date 2026-09-24 20:57:52*\nX1Y1D03*\n")
    b.write_text("%TF.CreationDate,2026-09-25T01:00:00+00:00*%\n%TF.ProjectId,x,1234,F*%\n"
                 "G04 Created by KiCad (PCBNEW 10.0.6) date 2026-09-25 01:00:00*\nX1Y1D03*\n")
    c.write_text(b.read_text().replace("X1Y1", "X2Y1"))
    assert export.same_content(str(a), str(b)) is True
    assert export.same_content(str(a), str(c)) is False
    assert export.same_content(str(a), None) is None
