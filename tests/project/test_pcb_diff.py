from kipr.project import diff_pcb, pcb

HEADER = """(kicad_pcb (version 20250114) (generator "pcbnew")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (2 "B.Cu" signal) (1 "F.Mask" user) (3 "B.Mask" user)
          (5 "F.SilkS" user "F.Silkscreen") (25 "Edge.Cuts" user) (31 "F.CrtYd" user "F.Courtyard"))
  (setup (stackup (layer "F.Mask" (type "Top Solder Mask") (color "Green")) (copper_finish "ENIG")))
  (net 0 "") (net 1 "GND") (net 2 "VCC")
  (gr_rect (start 0 0) (end 50 40) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "e1"))
"""


def fp(ref="R1", x=10, y=10, rot=0, value="10k", layer="F.Cu", lib="R:R_0603", uuid="u-r1", pad_rot=None,
       extra=""):
    a = rot if pad_rot is None else pad_rot
    at = f"{x} {y} {rot}" if rot else f"{x} {y}"
    return f"""(footprint "{lib}" (layer "{layer}") (uuid "{uuid}") (at {at})
    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "p1-{uuid}"))
    (property "Value" "{value}" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "p2-{uuid}"))
    (property "MPN" "RC0603" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "p3-{uuid}"))
    (attr smd) {extra}
    (fp_line (start -1.5 -0.8) (end 1.5 -0.8) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") (uuid "c1-{uuid}"))
    (fp_line (start -1.5 0.8) (end 1.5 0.8) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") (uuid "c2-{uuid}"))
    (pad "1" smd rect (at -0.8 0 {a}) (size 0.8 0.9) (layers "F.Cu" "F.Mask") (net 1 "GND") (uuid "pa-{uuid}"))
    (pad "2" smd rect (at 0.8 0 {a}) (size 0.8 0.9) (layers "F.Cu" "F.Mask") (net 2 "VCC") (uuid "pb-{uuid}"))
    (model "${{KICAD10_3DMODEL_DIR}}/R.3dshapes/R_0603.step" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
  )
"""


def seg(x1, y1, x2, y2, net=1, layer="F.Cu", w=0.25, uuid="s"):
    return f'(segment (start {x1} {y1}) (end {x2} {y2}) (width {w}) (layer "{layer}") (net {net}) (uuid "{uuid}"))\n'


def board(*parts):
    return pcb.load(HEADER + "".join(parts) + ")")


def diff(a, b):
    return diff_pcb.diff_boards(board(*a), board(*b))


def only(changes, kind):
    return [c for c in changes if c["kind"] == kind]


def test_parse_basics():
    b = board(fp(), seg(0, 0, 10, 0))
    assert b.copper == ["F.Cu", "In1.Cu", "B.Cu"]
    assert b.edge_box == [0, 0, 50, 40]
    assert b.thickness == 1.6
    assert b.stackup["copper_finish"] == "ENIG"
    f = b.footprints[0]
    assert (f.ref, f.value, f.side, f.fields["MPN"]) == ("R1", "10k", "top", "RC0603")
    assert f.pad_nets == {"1": "GND", "2": "VCC"}
    assert f.box == [8.5, 9.2, 11.5, 10.8]  # courtyard
    assert {"F.Cu", "F.Mask", "F.CrtYd"} <= f.layers


def test_unchanged_ignores_uuids_and_formatting():
    changes, comps = diff([fp(), seg(0, 0, 10, 0, uuid="a")], [fp(uuid="u-r1"), seg(10, 0, 0, 0, uuid="b")])
    assert changes == []
    assert [c["status"] for c in comps] == ["unchanged"]


def test_footprint_moved_value_changed():
    changes, comps = diff([fp()], [fp(x=12, value="4.7k")])
    (c,) = only(changes, "footprint")
    assert c["ref"] == "R1" and c["what"] == "moved" and c["whats"] == ["moved", "value"]
    assert c["layer"] == "F.Cu" and "F.Cu" in c["layers"]
    assert c["bbox_mm"] == [8.5, 9.2, 5.0, 1.6]  # union of base and head courtyards
    assert c["base_bbox_mm"] == [8.5, 9.2, 3.0, 1.6] and c["head_bbox_mm"] == [10.5, 9.2, 3.0, 1.6]
    assert comps[0]["status"] == "moved" and comps[0]["what"] == ["position", "value"]
    assert comps[0]["base"]["x"] == 10 and comps[0]["head"]["x"] == 12


def test_rotation_is_not_a_pad_change():
    # KiCad stores pad angles including the footprint rotation
    changes, comps = diff([fp()], [fp(rot=90)])
    (c,) = only(changes, "footprint")
    assert c["whats"] == ["rotated"]
    assert comps[0]["status"] == "rotated"


def test_value_only_change_touches_fab_silk_only():
    changes, _ = diff([fp()], [fp(value="1k")])
    (c,) = changes
    assert c["what"] == "value" and set(c["layers"]) <= {"F.Fab", "F.SilkS"}


def test_added_removed_replaced_uuid_matches_by_ref():
    changes, comps = diff([fp(), fp(ref="C1", uuid="u-c1", x=30)], [fp(uuid="new-uuid"), fp(ref="C2", uuid="u-c2", x=40)])
    fps = {(c["ref"], c["what"]) for c in only(changes, "footprint")}
    assert fps == {("C1", "removed"), ("C2", "added")}
    assert {c["ref"]: c["status"] for c in comps} == {"C1": "removed", "C2": "added", "R1": "unchanged"}


def test_flip_footprint_and_dnp_and_model():
    b = fp()
    h = fp(layer="B.Cu", extra="(dnp yes)").replace("R_0603.step", "R_0603_alt.step")
    changes, comps = diff([b], [h])
    (c,) = changes
    assert c["whats"][0] == "flipped" and "model" in c["whats"] and "dnp" in c["whats"]
    assert c["layer"] == "B.Cu"
    assert set(comps[0]["what"]) >= {"side", "model"}


def test_tracks_clustered_per_net_and_layer():
    base = [seg(0, 0, 10, 0, uuid="a")]
    head = [seg(0, 0, 5, 0, uuid="b"), seg(5, 0, 10, 1, uuid="c"), seg(30, 30, 40, 30, net=2, uuid="d")]
    changes, _ = diff(base, head)
    tr = only(changes, "track")
    assert len(tr) == 2
    gnd = next(c for c in tr if c["net"] == "GND")
    assert gnd["what"] == "rerouted" and gnd["count"] == {"added": 2, "removed": 1}
    assert gnd["bbox_mm"][0] <= 0 and gnd["bbox_mm"][0] + gnd["bbox_mm"][2] >= 10
    vcc = next(c for c in tr if c["net"] == "VCC")
    assert vcc["what"] == "added" and vcc["layers"] == ["F.Cu"]


def test_far_apart_changes_on_same_net_are_separate_clusters():
    changes, _ = diff([], [seg(0, 0, 1, 0, uuid="a"), seg(40, 30, 41, 30, uuid="b")])
    assert len(only(changes, "track")) == 2


def test_via_spans_copper_layers_and_kicad10_net_names():
    via = '(via (at 5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND") (uuid "v"))\n'
    changes, _ = diff([], [via])
    (c,) = changes
    assert c["kind"] == "via" and c["net"] == "GND"
    assert c["layers"] == ["B.Cu", "F.Cu", "In1.Cu"] and c["holes"] == ["PTH"]


def zone(pts, fill="(xy 0 0)", uuid="z1"):
    return f"""(zone (net 1) (net_name "GND") (layer "F.Cu") (uuid "{uuid}") (hatch edge 0.5)
      (polygon (pts {pts})) (filled_polygon (layer "F.Cu") (pts {fill})))\n"""


def test_zone_outline_vs_fill():
    sq = "(xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)"
    changes, _ = diff([zone(sq)], [zone(sq, fill="(xy 1 1)")])
    assert [c["what"] for c in changes] == ["fill"]
    changes, _ = diff([zone(sq)], [zone("(xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)")])
    (c,) = changes
    assert c["what"] == "outline" and c["bbox_mm"] == [0, 0, 20, 10] and c["net"] == "GND"


def test_text_edit_and_outline_change():
    t1 = '(gr_text "REV A" (at 20 30 0) (layer "F.SilkS") (uuid "t") (effects (font (size 1 1))))\n'
    t2 = t1.replace("REV A", "REV B")
    o2 = '(gr_line (start 50 0) (end 60 10) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "e2"))\n'
    changes, _ = diff([t1], [t2, o2])
    txt = only(changes, "text")[0]
    assert txt["what"] == "modified" and txt["detail"] == "'REV A' -> 'REV B'" and txt["layer"] == "F.SilkS"
    out = only(changes, "outline")[0]
    assert out["what"] == "added" and out["layers"] == ["Edge.Cuts"]
    assert diff_pcb.touched_layers(changes) == {"F.SilkS", "Edge.Cuts"}


def test_stackup_change_reported_as_board_change():
    b = board()
    h = pcb.load(HEADER.replace('(copper_finish "ENIG")', '(copper_finish "HAL SnPb")') + ")")
    changes, _ = diff_pcb.diff_boards(b, h)
    assert [(c["kind"], c["what"]) for c in changes] == [("board", "stackup")]
    assert "HAL SnPb" in changes[0]["detail"]
