"""Smart schematic diff: changes that only move things keep `move_only` when connectivity is unchanged."""

from kipr.project import classify, diff_sch, sch

from .test_sch_diff import LIB, child, load, sym, wire

PWR = """(symbol "power:GND" (power) (property "Reference" "#PWR" (at 0 0 0)) (property "Value" "GND" (at 0 0 0))
    (symbol "GND_0_1" (polyline (pts (xy 0 0) (xy 0 -1.27)) (stroke (width 0)) (fill (type none))))
    (symbol "GND_1_1" (pin power_in line (at 0 0 270) (length 0) (name "GND") (number "1"))))
"""
LIBS = LIB.rstrip()[:-1] + PWR + ")\n"  # LIB with power:GND


def root(*parts):
    return f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "root-uuid") (paper "A4")\n{LIBS}' + \
        "".join(parts) + '(sheet_instances (path "/" (page "1"))))'


def gnd(x, y, ref="#PWR01", uuid="g1"):
    return f"""(symbol (lib_id "power:GND") (at {x} {y} 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
  (uuid "{uuid}") (property "Reference" "{ref}" (at 0 0 0)) (property "Value" "GND" (at 0 0 0))
  (instances (project "demo" (path "/root-uuid" (reference "{ref}") (unit 1)))))
"""


def label(text, x, y, u="l"):
    return f'(label "{text}" (at {x} {y} 0) (uuid "{u}"))\n'


def nc(x, y):
    return f'(no_connect (at {x} {y}) (uuid "nc"))\n'


def block(dx=0, r2_label="VIN"):
    """R1 and R2: pins 1 tied by a wire with label VIN, R2 pin 2 to GND, R1 pin 2 no-connect."""
    x1, x2 = 100 + dx, 120 + dx
    return [sym("R1", x=x1, uuid="s1"), sym("R2", x=x2, uuid="s2"),
            wire(x1, 46.19, x1, 40), wire(x1, 40, x2, 40), wire(x2, 40, x2, 46.19),
            label(r2_label, x1 + 5, 40), gnd(x2, 53.81), nc(x1, 53.81)]


NETS = {"VIN": {"R1.1", "R2.1"}, "GND": {"R2.2"}, "unconnected-(R1-Pad2)": {"R1.2"}}


def diff(base_parts, head_parts, nets=(NETS, NETS)):
    b = load({"demo.kicad_sch": root(*base_parts)})
    h = load({"demo.kicad_sch": root(*head_parts)})
    return diff_sch.diff_schematics(b, h, {"base": nets[0], "head": nets[1]} if nets else None)


def kinds(sheet):
    return sorted((c["kind"], c["what"], c.get("ref"), bool(c.get("move_only"))) for c in sheet["changes"])


def test_classifier_move_only():
    assert classify.is_move_only(["moved"], True)
    assert classify.is_move_only(["rotated", "mirrored", "fields_minor"], True)
    assert not classify.is_move_only(["moved"], False)
    assert not classify.is_move_only(["moved", "value"], True)
    assert not classify.is_move_only(["fields_minor"], True)  # minor, not moved
    assert not classify.is_move_only([], True)


def test_connectivity_groups_and_anchors():
    s = load({"demo.kicad_sch": root(*block())}).sheets[0]
    c = sch.Connectivity(s)
    r1, r2, g = s.symbols
    assert c.pin_groups(r1)["1"] == c.pin_groups(r2)["1"] == c.anchor_group["label:VIN"]
    assert c.pin_groups(r2)["2"] == c.pin_groups(g)["1"] == c.anchor_group["power:GND"]
    assert c.anchors[c.pin_groups(r1)["1"]] == {"R1.1", "R2.1", "label:VIN"}
    assert c.anchors[c.pin_groups(r1)["2"]] == {"R1.2"}
    (flag,) = [e for e in s.elements if e.sub == "no_connect"]
    assert c.direct(flag) == {"R1.2"}


def test_moved_block_is_quiet():
    (sheet,) = diff(block(), block(dx=25.4))
    assert sheet["status"] == "modified" and sheet["moved_only"] is True
    assert all(c.get("move_only") for c in sheet["changes"])
    assert sheet["counts"] == {"changed": 0, "minor": 0, "moved": len(sheet["changes"])}
    got = kinds(sheet)
    assert ("symbol", "moved", "R1", True) in got and ("symbol", "moved", "#PWR01", True) in got
    assert ("wire", "rerouted", None, True) in got and ("label", "moved", None, True) in got
    # what to fade: each moved symbol's old and new place (with its field texts), each rerouted item
    r1 = next(c for c in sheet["changes"] if c.get("ref") == "R1")
    (b0, h0) = r1["parts_mm"]
    assert round((h0[0] + h0[2]) - (b0[0] + b0[2]), 3) == 25.4  # the body's right edge moved
    w = next(c for c in sheet["changes"] if c["kind"] == "wire")
    assert len(w["parts_mm"]) == w["count"]["added"] + w["count"]["removed"]
    # no netlist: nets are named by their labels / power symbols / pins, same result
    (sheet,) = diff(block(), block(dx=25.4), nets=None)
    assert all(c.get("move_only") for c in sheet["changes"])


def test_reroute_with_same_connections_is_quiet():
    head = block()
    head[3:6] = [wire(100, 46.19, 100, 35), wire(100, 35, 120, 35), wire(120, 35, 120, 46.19)]
    head.insert(3, label("VIN", 105, 35))
    (sheet,) = diff(block(), head)
    assert kinds(sheet) == [("label", "moved", None, True), ("wire", "rerouted", None, True)]


def test_moved_and_rewired_is_a_change():
    # R2 moves 2.54 mm down: its pin 1 now hangs in the air (the wire still ends at the old place)
    head = block()
    head[1] = sym("R2", x=120, y=52.54, uuid="s2")
    head[6] = gnd(120, 56.35)
    nets = {"VIN": {"R1.1"}, "GND": {"R2.2"}, "unconnected-(R1-Pad2)": {"R1.2"}, "unconnected-(R2-Pad1)": {"R2.1"}}
    (sheet,) = diff(block(), head, nets=(NETS, nets))
    got = {(c["ref"]): bool(c.get("move_only")) for c in sheet["changes"] if c["kind"] == "symbol"}
    assert got == {"R2": False, "#PWR01": True}
    assert not sheet.get("moved_only") and sheet["counts"]["changed"] == 1


def test_net_rename_keeps_moved_items_highlighted():
    renamed = {"VOUT" if k == "VIN" else k: v for k, v in NETS.items()}
    (sheet,) = diff(block(), block(dx=25.4, r2_label="VOUT"), nets=(NETS, renamed))
    flags = {(c["kind"], c.get("ref")): bool(c.get("move_only")) for c in sheet["changes"]}
    assert flags[("symbol", "R1")] is False and flags[("symbol", "R2")] is False  # pin 1 on a renamed net
    assert flags[("symbol", "#PWR01")] is True  # GND is GND
    assert any(c["kind"] == "label" and not c.get("move_only") for c in sheet["changes"])


def test_bridging_two_nets_is_a_change():
    base = [sym("R1", uuid="s1"), sym("R2", x=120, uuid="s2"), wire(100, 46.19, 100, 40), wire(120, 46.19, 120, 40)]
    head = base + [wire(100, 40, 120, 40)]
    two = {"Net-(R1-Pad1)": {"R1.1"}, "Net-(R2-Pad1)": {"R2.1"}}
    one = {"Net-(R1-Pad1)": {"R1.1", "R2.1"}}
    (sheet,) = diff(base, head, nets=(two, one))
    assert kinds(sheet) == [("wire", "added", None, False)]
    (sheet,) = diff(base, head, nets=None)
    assert kinds(sheet) == [("wire", "added", None, False)]


def test_no_connect_flag_must_stay_on_its_pin():
    moved = block(dx=25.4)
    (sheet,) = diff(block(), moved)
    assert all(c.get("move_only") for c in sheet["changes"])
    dropped = [p for p in moved if not p.startswith("(no_connect")]
    (sheet,) = diff(block(), dropped)
    assert any(c["kind"] == "wire" and "no_connect" in c["detail"] and not c.get("move_only") for c in sheet["changes"])


def test_dangling_power_symbol_is_a_change():
    head = block()
    head[6] = gnd(130, 60)  # GND symbol moved off R2's pin
    nets = {"VIN": {"R1.1", "R2.1"}, "unconnected-(R2-Pad2)": {"R2.2"}, "unconnected-(R1-Pad2)": {"R1.2"}}
    (sheet,) = diff(block(), head, nets=(NETS, nets))
    assert kinds(sheet) == [("symbol", "moved", "#PWR01", False)]
    (sheet,) = diff(block(), head, nets=None)
    assert kinds(sheet) == [("symbol", "moved", "#PWR01", False)]


def test_match_by_uuid_then_reference():
    head = block(dx=25.4)
    head[0] = sym("R1", x=125.4, uuid="new-uuid")  # re-created symbol: matched by reference
    (sheet,) = diff(block(), head)
    (r1,) = [c for c in sheet["changes"] if c.get("ref") == "R1"]
    assert r1["what"] == "moved" and r1["move_only"] is True


def sheet_box(x, pin_y=25, pin="IN"):
    return f"""(sheet (at {x} 20) (size 30 20) (uuid "sa")
  (property "Sheetname" "Amp" (at {x} 19 0)) (property "Sheetfile" "amp.kicad_sch" (at {x} 41 0))
  (pin "{pin}" input (at {x} {pin_y} 180) (uuid "p1"))
  (instances (project "demo" (path "/root-uuid" (page "2")))))
"""


def test_hierarchical_sheet_box_and_pins():
    amp = child('(hierarchical_label "IN" (shape input) (at 50 50 180) (uuid "h1"))\n',
                sym("R5", x=60, y=53.81, uuid="s5", path="/root-uuid/sa"),
                wire(50, 50, 60, 50))

    def run(base_parts, head_parts, nets=None):
        b = load({"demo.kicad_sch": root(*base_parts), "amp.kicad_sch": amp})
        h = load({"demo.kicad_sch": root(*head_parts), "amp.kicad_sch": amp})
        return {s["id"]: s for s in diff_sch.diff_schematics(b, h, nets)}

    base = [sym("R1", uuid="s1"), wire(100, 46.19, 100, 25), wire(100, 25, 150, 25), sheet_box(150)]
    moved = [sym("R1", uuid="s1"), wire(100, 46.19, 100, 25), wire(100, 25, 160, 25), sheet_box(160)]
    sheets = run(base, moved)
    assert sheets["root/amp"]["status"] == "unchanged"
    assert kinds(sheets["root"]) == [("sheet", "moved", None, True), ("wire", "rerouted", None, True)]
    assert sheets["root"]["moved_only"] is True
    # the sheet pin moves down the box and away from the wire: a real change
    rewired = [sym("R1", uuid="s1"), wire(100, 46.19, 100, 25), wire(100, 25, 150, 25), sheet_box(150, pin_y=30)]
    got = kinds(run(base, rewired)["root"])
    assert ("sheet", "modified", None, False) in got
    # same with kicad-cli style hierarchical net names
    nets = {"/IN": {"R1.1", "R5.2"}}
    assert kinds(run(base, moved, {"base": nets, "head": nets})["root"]) == kinds(sheets["root"])


def test_text_move_is_quiet_but_edit_is_not():
    t = '(text "{s}" (at {x} 80 0) (effects (font (size 1.27 1.27))) (uuid "t"))\n'
    (sheet,) = diff([t.format(s="note", x=10)], [t.format(s="note", x=30)])
    assert kinds(sheet) == [("text", "moved", None, True)]
    (sheet,) = diff([t.format(s="note", x=10)], [t.format(s="note 2", x=10)])
    assert sheet["changes"] and not any(c.get("move_only") for c in sheet["changes"])


def test_label_box_follows_its_rotation():
    s = load({"demo.kicad_sch": root('(label "VCC_PIC" (at 100 50 180) (uuid "a"))\n',
                                     '(label "VCC_PIC" (at 100 60 0) (uuid "b"))\n')}).sheets[0]
    left, right = (e.box for e in s.elements)
    assert left[2] == 101 and left[0] < 95  # text runs left of the anchor
    assert right[0] == 99 and right[2] > 105
