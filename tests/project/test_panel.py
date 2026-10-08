"""Panels and boards without a schematic: discovery, copy-aware matching, panel detection and
features, order-insensitive layer comparison, review JSON."""

import json

from boarddd.io.kicad import pcb

from kipr.common.git import Git
from kipr.project import diff_pcb, discover, export, panel, review

from .test_discover_review import commit, repo, write  # noqa: F401  (repo: fixture)
from .test_pcb_diff import HEADER, fp


def copy_fp(n, ref="R1", x=10, y=10, uuid=None, **kw):
    """A footprint of board copy `n` in a KiKit panel: nets renamed Board_<n>-…"""
    t = fp(ref=ref, x=x, y=y, uuid=uuid or f"u-{ref}-{n}", **kw)
    return t.replace('(net 1 "GND")', f'(net 1 "Board_{n}-GND")').replace('(net 2 "VCC")', f'(net 2 "Board_{n}-VCC")')


def kikit_fp(ref, x, y, name="NPTH"):
    return f"""(footprint "kikit:{name}" (layer "F.Cu") (uuid "k-{ref}-{x}-{y}") (at {x} {y})
    (property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS") (hide yes) (uuid "kr-{ref}-{x}"))
    (property "Value" "{name}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "kv-{ref}-{x}"))
    (pad "" np_thru_hole circle (at 0 0) (size 0.5 0.5) (drill 0.5) (layers "F&B.Cu" "*.Mask") (uuid "kp-{ref}-{x}-{y}"))
  )
"""


def board(*parts):
    return pcb.load(HEADER + "".join(parts) + ")")


def test_discover_lone_boards_and_panels(repo):
    r, base = repo
    git = Git(str(r))
    write(r, "boards/a/a-panel.kicad_pcb", HEADER + copy_fp(0) + copy_fp(1, x=60) + ")")
    write(r, "panelized/p.kicad_pcb", HEADER + ")")
    head = commit(r, "panels")
    got = {(p.path, p.name): p for p in discover.find_projects(git, base, head)}
    # a-panel is its own unit: changing it does not re-review project a; panelized/ is reviewed
    assert set(got) == {("boards/a", "a-panel"), ("panelized", "p")}
    pa = got[("boards/a", "a-panel")]
    assert (pa.status, pa.board_only, pa.head_pro, pa.reasons) == ("added", True, "boards/a/a-panel.kicad_pcb",
                                                                   ["boards/a/a-panel.kicad_pcb"])
    assert discover.checkout_paths(git, head, pa, "head") == ["boards/a/a-panel.kicad_pcb", "boards/a/fp-lib-table"]
    write(r, "boards/a/a.kicad_pcb", HEADER + fp(x=12) + ")")
    head2 = commit(r, "move R1")
    (pr,) = discover.find_projects(git, head, head2)
    assert (pr.name, pr.board_only) == ("a", False)
    assert "boards/a/a-panel.kicad_pcb" not in discover.checkout_paths(git, head2, pr, "head")
    assert [p.name for p in discover.find_projects(git, base, head, ["a-panel"])] == ["a-panel"]


def test_copies_pair_nearest_and_get_copy_labels():
    base = board(copy_fp(0, uuid="b0"), copy_fp(1, x=60, uuid="b1"), fp(ref="U1", x=30, uuid="u1"))
    # regenerated: new uuids, copy 1's R1 moved 1 mm, copies listed in the other order
    head = board(copy_fp(1, x=61, uuid="h1"), copy_fp(0, uuid="h0"), fp(ref="U1", x=30, uuid="u1"))
    changes, comps = diff_pcb.diff_boards(base, head)
    assert [(c["kind"], c["what"], c["ref"]) for c in changes] == [("footprint", "moved", "R1·1")]
    assert sorted(c["ref"] for c in comps) == ["R1·0", "R1·1", "U1"]
    # without KiKit's net prefix: the copies' rank, top to bottom and left to right
    plain = board(fp(uuid="p0"), fp(x=60, uuid="p1"), fp(y=30, uuid="p2"))
    assert sorted(diff_pcb.copy_labels(plain).values()) == ["R1·0", "R1·1", "R1·2"]
    assert diff_pcb.copy_labels(board(fp())) == {}


def test_detect_signals():
    kikit = board(copy_fp(0), copy_fp(1, x=60), kikit_fp("KiKit_MB_1_1", 30, 1))
    assert panel.detect(kikit, "x", "boards/x") == {"signals": ["kikit", "copies"], "copies": 2}
    copies = board(fp(uuid="a"), fp(x=60, uuid="b"))
    assert panel.detect(copies, "x", "") == {"signals": ["copies"], "copies": 2}
    assert panel.detect(board(fp()), "board-panelized", "") == {"signals": ["name"], "copies": None}
    assert panel.detect(board(fp()), "x", "hw/Panels/v2")["signals"] == ["name"]
    assert panel.detect(board(fp(), fp(ref="R2", uuid="b")), "x", "hw") is None
    assert panel.feature(kikit.footprints[2]) == "mousebite"


def test_annotate_panel_features():
    bites = [kikit_fp(f"KiKit_MB_1_{i}", 20 + i * 0.8, 0) for i in range(3)]
    moved = [kikit_fp(f"KiKit_MB_1_{i}", 30 + i * 0.8, 0) for i in range(3)]
    base = board(copy_fp(0), copy_fp(1, x=60), *bites, kikit_fp("KiKit_TO_1", 2, 2))
    head = board(copy_fp(0), copy_fp(1, x=60), *moved, kikit_fp("KiKit_TO_1", 2, 2),
                 kikit_fp("KiKit_FID_T_1", 48, 2, "Fiducial"))
    changes, comps = diff_pcb.diff_boards(base, head)
    changes, comps = panel.annotate(changes, comps, base, head)
    kinds = sorted((c["kind"], c["what"]) for c in changes)
    assert kinds == [("fiducial", "added"), ("mousebites", "moved")]
    mb = next(c for c in changes if c["kind"] == "mousebites")
    assert mb["detail"] == "3 holes moved 10.000 mm" and mb["refs"] == ["KiKit_MB_1_0", "KiKit_MB_1_1", "KiKit_MB_1_2"]
    assert mb["base_bbox_mm"][0] < 21 and mb["head_bbox_mm"][0] > 29
    assert sorted(c["ref"] for c in comps) == ["R1·0", "R1·1"]  # holes and fiducials aren't parts
    assert panel.summary(changes) == {"fiducial": 1, "mousebites": 1}
    edge = {"kind": "outline", "what": "modified", "bbox_mm": [0, 10, 5, 5]}
    inner = {"kind": "outline", "what": "modified", "bbox_mm": [20, 10, 5, 5]}
    out, _ = panel.annotate([edge, inner], [], base, head)
    assert [c["feature"] for c in out] == ["frame", "tabs"]


GBR = """%FSLAX46Y46*%
%MOMM*%
%TF.CreationDate,2026-01-01T00:00:00+00:00*%
G04 Created by KiCad*
%ADD10C,0.100000*%
%ADD11R,1.000000X2.000000*%
%TO.C,R1*%
D10*
X0Y0D02*
X1000000Y0D01*
%TD*%
D11*
X5000000Y5000000D03*
G36*
X0Y0D02*
G01*
X1000000Y0D01*
X0Y1000000D01*
X0Y0D01*
G37*
M02*
"""
# the same drawing: other aperture numbers, attributes and object order
GBR2 = """%FSLAX46Y46*%
%MOMM*%
%TF.CreationDate,2026-02-02T00:00:00+00:00*%
%ADD10R,1.000000X2.000000*%
%ADD12C,0.100000*%
G36*
X0Y0D02*
G01*
X1000000Y0D01*
X0Y1000000D01*
X0Y0D01*
G37*
D10*
%TO.C,R1_1*%
X5000000Y5000000D03*
D12*
X0Y0D02*
X1000000Y0D01*
M02*
"""


def test_layer_compare_ignores_object_order(tmp_path):
    a, b, c = tmp_path / "a.gbr", tmp_path / "b.gbr", tmp_path / "c.gbr"
    a.write_text(GBR)
    b.write_text(GBR2)
    c.write_text(GBR2.replace("X5000000Y5000000D03", "X5000000Y5100000D03"))
    assert export.same_content(str(a), str(b)) is True
    assert export.same_content(str(a), str(c)) is False
    da = "M48\nMETRIC\nT1C0.500\nT2C1.000\n%\nG90\nT1\nX1.0Y2.0\nX3.0Y4.0\nT2\nX5.0Y6.0\nM30\n"
    db = "M48\nMETRIC\nT1C1.000\nT2C0.500\n%\nG90\nT1\nX5.0Y6.0\nT2\nX3.0Y4.0\nX1.0Y2.0\nM30\n"
    (tmp_path / "a.drl").write_text(da)
    (tmp_path / "b.drl").write_text(db)
    (tmp_path / "c.drl").write_text(db.replace("X5.0", "X5.5"))
    assert export.same_content(str(tmp_path / "a.drl"), str(tmp_path / "b.drl")) is True
    assert export.same_content(str(tmp_path / "a.drl"), str(tmp_path / "c.drl")) is False


def test_review_panel_without_kicad_cli(repo, tmp_path):
    r, base = repo
    fid = kikit_fp("KiKit_FID_T_1", 48, 2, "Fiducial")
    u1 = lambda n, x: copy_fp(n, ref="U1", x=x, y=20, lib="U:SOIC-8")  # noqa: E731
    write(r, "boards/c/c.kicad_pcb", HEADER + fp() + fp(ref="U1", y=20, lib="U:SOIC-8", uuid="c-u1") + ")")
    two = copy_fp(0) + u1(0, 10) + copy_fp(1, x=60) + u1(1, 60)
    write(r, "panels/a2/a2.kicad_pcb", HEADER + two + ")")
    write(r, "panels/a2/kikit.json", "{}")
    mid = commit(r, "panel")
    write(r, "panels/a2/a2.kicad_pcb", HEADER + two + fid + ")")
    head = commit(r, "fiducial")
    out = tmp_path / "out"
    doc = review.run(str(r), mid, head, str(out), no_export=True, log=lambda *_: None)
    (p,) = doc["projects"]
    assert (p["slug"], p["kind"], p["errors"]) == ("a2", "panel", [])
    # boards/a (one footprint) is too small to count as a source
    assert p["panel"] == {"signals": ["kikit", "copies", "name"], "copies": 2, "config": "panels/a2/kikit.json",
                          "sources": [{"path": "boards/c/c.kicad_pcb", "copies": 2}],
                          "fiducials": 1, "tooling": 0, "mousebites": 0}
    assert p["schematic"] is None and p["bom"] is None and p["netlist"] is None
    assert p["checks"]["erc"] is None and p["checks"]["grid"] is None
    assert p["summary"]["panel"] == {"fiducial": 1} and p["summary"]["erc"] is None
    assert [(c["kind"], c["what"], c["ref"]) for c in p["pcb"]["changes"]] == [("fiducial", "added", "KiKit_FID_T_1")]
    assert json.loads((out / "project-review.json").read_text())["projects"][0]["kind"] == "panel"
    # a plain board without a schematic
    write(r, "mech/plate.kicad_pcb", HEADER + ")")
    doc = review.run(str(r), head, commit(r, "plate"), str(tmp_path / "o2"), no_export=True, log=lambda *_: None)
    (p,) = doc["projects"]
    assert (p["kind"], p["panel"], p["schematic"], p["netlist"], p["errors"]) == ("board", None, None, None, [])


def test_project_kind_and_old_board_only_project(repo, tmp_path):
    r, base = repo
    write(r, "boards/a/a.kicad_pcb", HEADER + fp(x=12) + ")")
    write(r, "boards/b/b.kicad_pcb", HEADER + fp(ref="R9") + ")")
    head = commit(r, "both")
    doc = review.run(str(r), base, head, str(tmp_path / "o"), no_export=True, log=lambda *_: None)
    kinds = {p["slug"]: (p["kind"], p["netlist"] is None) for p in doc["projects"]}
    # b has a .kicad_pro but no schematic: a board (its nets came from a schematic it doesn't have)
    assert kinds == {"a": ("project", False), "b": ("board", True)}
