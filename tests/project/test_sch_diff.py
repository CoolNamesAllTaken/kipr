from kipr.project import diff_sch, sch

LIB = """(lib_symbols
  (symbol "Device:R" (property "Reference" "R" (at 0 0 0)) (property "Value" "R" (at 0 0 0))
    (symbol "R_0_1" (rectangle (start -1 2.5) (end 1 -2.5) (stroke (width 0.25)) (fill (type none))))
    (symbol "R_1_1" (pin passive line (at 0 3.81 270) (length 1.31) (name "~") (number "1"))
                    (pin passive line (at 0 -3.81 90) (length 1.31) (name "~") (number "2")))))
"""


def sym(ref="R1", value="10k", x=100, y=50, rot=0, uuid="s1", path="/root-uuid", extra="", fp="R:R_0603"):
    return f"""(symbol (lib_id "Device:R") (at {x} {y} {rot}) (unit 1) (in_bom yes) (on_board yes) (dnp no) {extra}
  (uuid "{uuid}")
  (property "Reference" "{ref}" (at 0 0 0)) (property "Value" "{value}" (at 0 0 0))
  (property "Footprint" "{fp}" (at 0 0 0)) (property "MPN" "RC0603FR-{value}" (at 0 0 0))
  (instances (project "demo" (path "{path}" (reference "{ref}") (unit 1)))))
"""


def root(*parts, uuid="root-uuid", paper='"A4"'):
    return f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uuid}") (paper {paper})\n{LIB}' + \
        "".join(parts) + '(sheet_instances (path "/" (page "1"))))'


def load(files, root_file="demo.kicad_sch"):
    return sch.load_hierarchy(files.get, root_file, "demo")


def diff(base_parts, head_parts):
    b = load({"demo.kicad_sch": root(*base_parts)})
    h = load({"demo.kicad_sch": root(*head_parts)})
    return diff_sch.diff_schematics(b, h)


def test_symbol_bbox_from_library_graphics_and_pins():
    s = load({"demo.kicad_sch": root(sym())})
    (r1,) = s.sheets[0].symbols
    assert (r1.ref, r1.value) == ("R1", "10k")
    # body 2 x 5 mm + pins to +-3.81, lib y up -> sheet y down, centred on (100, 50)
    assert r1.box == [99.0, 46.19, 101.0, 53.81]
    r2 = load({"demo.kicad_sch": root(sym(rot=90))}).sheets[0].symbols[0]
    assert [round(v, 2) for v in r2.box] == [96.19, 49.0, 103.81, 51.0]


def test_value_change_with_bbox_and_bom():
    (sheet,) = diff([sym()], [sym(value="4.7k")])
    assert sheet["id"] == "root" and sheet["status"] == "modified" and sheet["size_mm"] == [297, 210]
    (c,) = sheet["changes"]
    assert c["kind"] == "symbol" and c["what"] == "value" and c["ref"] == "R1"
    assert (c["base"], c["head"]) == ("10k", "4.7k")
    assert "fields" in c["whats"]  # MPN changed too
    assert c["bbox_mm"] == [98.5, 45.69, 3.0, 8.62]


def test_unchanged_sheet():
    (sheet,) = diff([sym()], [sym()])
    assert sheet["status"] == "unchanged" and sheet["changes"] == []


def test_symbol_added_removed_or_reannotated():
    (sheet,) = diff([sym()], [sym(ref="R2", uuid="s2", x=150)])
    assert sorted((c["what"], c["ref"]) for c in sheet["changes"]) == [("added", "R2"), ("removed", "R1")]
    # new uuid but same symbol at the same place: re-annotation, not add + remove
    (sheet,) = diff([sym()], [sym(ref="R2", uuid="s2")])
    assert [(c["what"], c["base"], c["head"]) for c in sheet["changes"]] == [("reference", "R1", "R2")]


def test_dnp_and_moved():
    (sheet,) = diff([sym()], [sym(x=110).replace("(dnp no)", "(dnp yes)")])
    (c,) = sheet["changes"]
    assert c["whats"][:1] == ["dnp"] and "moved" in c["whats"]
    assert c["base_bbox_mm"][0] < c["head_bbox_mm"][0]


def wire(x1, y1, x2, y2, u="w"):
    return f'(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (stroke (width 0) (type default)) (uuid "{u}"))\n'


def test_wires_are_clustered_and_direction_independent():
    base = [wire(0, 0, 10, 0), wire(50, 50, 60, 50)]
    head = [wire(10, 0, 0, 0, "x"), wire(50, 50, 55, 50), wire(55, 50, 60, 50), wire(55, 50, 55, 55),
            '(junction (at 55 50) (diameter 0) (uuid "j"))\n']
    (sheet,) = diff(base, head)
    (c,) = sheet["changes"]
    assert c["kind"] == "wire" and c["what"] == "modified"
    assert c["count"] == {"added": 4, "removed": 1}
    assert "+1 junction" in c["detail"]


def test_label_rename_and_move():
    lab = '(label "{t}" (at {x} 20 0) (uuid "l"))\n'
    (sheet,) = diff([lab.format(t="SDA", x=10), lab.format(t="SCL", x=40)],
                    [lab.format(t="SDA_3V3", x=10), lab.format(t="SCL", x=45)])
    got = sorted((c["kind"], c["what"], c.get("base"), c.get("head")) for c in sheet["changes"])
    assert got == [("label", "moved", "SCL", "SCL"), ("label", "renamed", "SDA", "SDA_3V3")]


def test_other_changes_detected_by_digest():
    tb = '(title_block (title "X") (rev "{r}"))\n'
    (sheet,) = diff([tb.format(r="A")], [tb.format(r="B")])
    assert sheet["status"] == "modified" and sheet["changes"][0]["kind"] == "other"


def sub_sheet(name, file, uuid, page):
    return f"""(sheet (at 20 20) (size 30 20) (uuid "{uuid}")
  (property "Sheetname" "{name}" (at 20 19 0)) (property "Sheetfile" "{file}" (at 20 41 0))
  (instances (project "demo" (path "/root-uuid" (page "{page}")))))
"""


def child(*parts):
    return f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "child-file") (paper "A4")\n{LIB}' + \
        "".join(parts) + ")"


def test_hierarchy_ids_pages_and_per_instance_refs():
    amp = child(sym(ref="R1", path="/root-uuid/sa").replace(
        '(path "/root-uuid/sa" (reference "R1") (unit 1))',
        '(path "/root-uuid/sa" (reference "R101") (unit 1)) (path "/root-uuid/sb" (reference "R201") (unit 1))'))
    files = {"demo.kicad_sch": root(sub_sheet("Amp Left", "amp.kicad_sch", "sa", "2"),
                                    sub_sheet("Amp Right", "amp.kicad_sch", "sb", "3")),
             "amp.kicad_sch": amp}
    s = load(files)
    assert [(x.id, x.page, x.file, x.names) for x in s.sheets] == [
        ("root", "1", "demo.kicad_sch", ["demo"]),
        ("root/amp-left", "2", "amp.kicad_sch", ["demo", "Amp Left"]),
        ("root/amp-right", "3", "amp.kicad_sch", ["demo", "Amp Right"])]
    assert [x.symbols[0].ref for x in s.sheets[1:]] == ["R101", "R201"]
    assert sorted(s.components()) == ["R101", "R201"]


def test_sheet_added_and_missing_file_is_an_error_not_a_crash():
    base = load({"demo.kicad_sch": root()})
    head = load({"demo.kicad_sch": root(sub_sheet("Power", "power.kicad_sch", "sp", "2"))})
    assert head.errors == ["schematic file not found: power.kicad_sch"]
    sheets = diff_sch.diff_schematics(base, head)
    assert [(x["id"], x["status"]) for x in sheets] == [("root", "modified"), ("root/power", "added")]
    assert sheets[0]["changes"][0]["kind"] == "sheet"


def test_bom_rows_and_groups():
    b = load({"demo.kicad_sch": root(sym("R1", uuid="a"), sym("R2", uuid="b", x=120), sym("R3", uuid="c", x=140))})
    h = load({"demo.kicad_sch": root(sym("R1", uuid="a"), sym("R2", uuid="b", x=120, value="1k"),
                                     sym("R4", uuid="d", x=160))})
    bom = diff_sch.diff_bom(b.components(), h.components())
    rows = {r["key"]: (r["status"], r["what"]) for r in bom["rows"]}
    assert rows == {"R1": ("unchanged", []), "R2": ("changed", ["value", "fields"]), "R3": ("removed", []),
                    "R4": ("added", [])}
    groups = {(g["value"], g["status"]): g for g in bom["groups"]}
    g10k = groups[("10k", "changed")]
    assert g10k["mpn"] == "RC0603FR-10k" and g10k["base"]["qty"] == 3 and g10k["head"]["qty"] == 2
    assert g10k["refs_added"] == ["R4"] and g10k["refs_removed"] == ["R2", "R3"]
    assert groups[("1k", "added")]["head"]["refs"] == ["R2"]


def test_excluded_from_bom_is_absent():
    b = load({"demo.kicad_sch": root(sym())})
    h = load({"demo.kicad_sch": root(sym().replace("(in_bom yes)", "(in_bom no)"))})
    bom = diff_sch.diff_bom(b.components(), h.components())
    assert [(r["key"], r["status"]) for r in bom["rows"]] == [("R1", "removed")]
