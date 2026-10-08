"""End-to-end run against the public kipr-fixtures repo with a real kicad-cli.

Skipped unless both exist (an error instead with $KIPR_REQUIRE_INTEGRATION=1, as in CI).
Locations: $KIPR_FIXTURES (default: ../kipr-fixtures next to this checkout, then
/workspace/projects/kipr-fixtures) and $KIPR_KICAD_CLI / kicad-cli on PATH. Build the fixture repo
with tests/project/fixtures/build.sh DEST (expected changes: tests/project/fixtures/CHANGES.md).
Set $KIPR_CACHE_DIR to reuse exports between runs (a cold run takes ~1-2 minutes, mostly ERC/DRC).
"""

import json
import os
import subprocess

import pytest

from kipr.project import review
from kipr.common.kicad_cli import find as find_kicad_cli

HERE = os.path.dirname(os.path.abspath(__file__))


def fixtures_repo():
    for cand in (os.environ.get("KIPR_FIXTURES"), os.path.join(HERE, "..", "..", "..", "kipr-fixtures"),
                 "/workspace/projects/kipr-fixtures"):
        if cand and os.path.isdir(os.path.join(cand, ".git")) or (cand and os.path.isfile(os.path.join(cand, ".git"))):
            return os.path.abspath(cand)
    return None


def rev(repo, ref):
    r = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", ref + "^{commit}"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


REPO = fixtures_repo()
CLI = find_kicad_cli()
if os.environ.get("KIPR_REQUIRE_INTEGRATION") and (not REPO or not CLI):  # CI: fail instead of skipping
    raise RuntimeError(f"KIPR_REQUIRE_INTEGRATION is set but fixtures={REPO} kicad-cli={CLI}")
pytestmark = pytest.mark.skipif(not REPO or not CLI, reason="needs kipr-fixtures and kicad-cli")


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    base = rev(REPO, "base")
    head = rev(REPO, "head") or rev(REPO, "HEAD")
    if not base or not head or base == head:
        pytest.skip("fixtures repo has no base/head pair yet")
    out = tmp_path_factory.mktemp("out")
    cache = os.environ.get("KIPR_CACHE_DIR") or str(tmp_path_factory.mktemp("cache"))
    d = review.run(REPO, base, head, str(out), kicad_cli=CLI, cache_dir=cache, log=lambda *_: None)
    d["_out"] = str(out)
    return d


def proj(doc, slug):
    return next(p for p in doc["projects"] if p["slug"] == slug)


def test_json_written_and_only_changed_projects(doc):
    out = doc["_out"]
    with open(os.path.join(out, "project-review.json")) as fh:
        assert json.load(fh)["version"] == 1
    assert doc["errors"] == []
    assert "pic_programmer" in [p["slug"] for p in doc["projects"]]
    p = proj(doc, "pic_programmer")
    assert p["errors"] == []
    # every referenced file exists
    paths = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and x.startswith("p/"):
            paths.append(x)

    walk(p)
    assert len(paths) > 40
    assert [x for x in paths if not os.path.isfile(os.path.join(out, x))] == []


def test_schematic_changes(doc):
    p = proj(doc, "pic_programmer")
    sheets = {s["id"]: s for s in p["schematic"]["sheets"]}
    assert set(sheets) == {"root", "root/pic_sockets"}
    root = sheets["root"]
    assert root["status"] == "modified" and root["base"].endswith("sch/base/root.svg")
    got = {(c["what"], c.get("ref")) for c in root["changes"] if c["kind"] == "symbol" and not c.get("power")}
    assert {("value", "R7"), ("footprint", "C9"), ("removed", "P103"), ("added", "C10")} <= got
    assert all(c["bbox_mm"] and c["bbox_mm"][2] > 0 for c in root["changes"] if c["kind"] == "symbol")


def test_schematic_moved_block_is_quiet(doc):
    """pic_sockets: the P2/P3 socket block moved 12.7 mm left (same connections), P2's value changed."""
    p = proj(doc, "pic_programmer")
    sh = {s["id"]: s for s in p["schematic"]["sheets"]}["root/pic_sockets"]
    by_ref = {c.get("ref"): c for c in sh["changes"] if c["kind"] == "symbol"}
    if "P3" not in by_ref:
        pytest.skip("fixtures head predates the pic_sockets block move")
    assert by_ref["P3"]["what"] == "moved" and by_ref["P3"]["move_only"] is True
    assert by_ref["P2"]["what"] == "value" and not by_ref["P2"].get("move_only")
    assert all(c.get("move_only") for c in sh["changes"] if c.get("ref") != "P2"), \
        [c for c in sh["changes"] if not c.get("move_only")]
    kinds = {c["kind"] for c in sh["changes"] if c.get("move_only")}
    assert {"symbol", "wire", "label"} <= kinds
    assert sh["counts"]["changed"] == 1 and sh["counts"]["moved"] >= 10 and not sh.get("moved_only")
    root = {s["id"]: s for s in p["schematic"]["sheets"]}["root"]
    assert not any(c.get("move_only") for c in root["changes"])  # real edits only there
    assert p["summary"]["sch_moved"] == sh["counts"]["moved"]


def test_pcb_changes_and_layers(doc):
    p = proj(doc, "pic_programmer")
    ch = p["pcb"]["changes"]
    fps = {c["ref"]: c for c in ch if c["kind"] == "footprint"}
    assert fps["D9"]["what"] == "moved"
    assert "rotated" in fps["D12"]["whats"]
    assert fps["C10"]["what"] == "added" and fps["P103"]["what"] == "removed"
    assert fps["C9"]["what"] == "footprint" and fps["R7"]["what"] == "value"
    assert any(c["kind"] == "track" and c["net"] == "Net-(D8-A)" for c in ch)
    assert any(c["kind"] == "zone" and c["net"] == "GND" and "outline" in c["whats"] for c in ch)
    assert any(c["kind"] == "text" and "REV B" in c["detail"] for c in ch)
    layers = {ly["id"]: ly for ly in p["pcb"]["layers"]}
    assert layers["F.Cu"]["status"] == "modified" and layers["F.SilkS"]["status"] == "modified"
    assert layers["Edge.Cuts"]["status"] == "unchanged"
    assert layers["PTH"]["kind"] == "drill" and layers["PTH"]["head"]["gerber"].endswith("PTH.drl")
    assert p["pcb"]["gbrjob"]["head"].endswith("board.gbrjob")
    board = p["pcb"]["board"]
    assert board["copper_layers"] == 2 and board["size_mm"][0] > 50


def test_bom_netlist_checks_3d(doc):
    p = proj(doc, "pic_programmer")
    rows = {r["key"]: r for r in p["bom"]["rows"]}
    assert rows["R7"]["status"] == "changed" and rows["C10"]["status"] == "added"
    assert p["netlist"]["source"] == "schematic"
    assert {"pin": "J1.9", "from": "unconnected-(J1-P9-Pad9)", "to": "GND"} in p["netlist"]["moved_pins"]
    erc = p["checks"]["erc"]
    assert [v["type"] for v in erc["fixed"]] == ["pin_not_connected"] and erc["new"] == []
    assert p["checks"]["drc"] is not None
    comps = {c["ref"]: c for c in p["pcba3d"]["components"]}
    assert comps["D9"]["status"] == "moved" and comps["C10"]["status"] == "added"
    assert p["pcba3d"]["head"]["glb"].endswith("3d/head.glb")
    s = p["summary"]
    assert s["components"]["added"] == 1 and s["components"]["removed"] == 1 and s["erc"] == {"new": 0, "fixed": 1}


def test_complex_hierarchy_new_sheet_outline_and_drc(doc):
    if "complex_hierarchy" not in [p["slug"] for p in doc["projects"]]:
        pytest.skip("fixtures head predates the complex_hierarchy changes")
    p = proj(doc, "complex_hierarchy")
    assert p["errors"] == []
    sheets = {s["id"]: s for s in p["schematic"]["sheets"]}
    new = sheets["root/status_led"]
    assert new["status"] == "added" and new["base"] is None and new["head"].endswith("sch/head/root/status_led.svg")
    assert {c["ref"] for c in new["changes"]} == {"R401", "D401"}
    assert any(c["kind"] == "sheet" and c["what"] == "added" for c in sheets["root"]["changes"])
    fps = {c["ref"]: c["what"] for c in p["pcb"]["changes"] if c["kind"] == "footprint"}
    assert fps == {"R401": "added", "D401": "added"}
    assert {c["net"] for c in p["pcb"]["changes"] if c["kind"] == "track"} >= {"VCC", "GND", "/status_led/LED_A"}
    assert any(c["kind"] == "outline" for c in p["pcb"]["changes"])
    layers = {ly["id"]: ly["status"] for ly in p["pcb"]["layers"]}
    assert layers["Edge.Cuts"] == "modified"
    b = p["pcb"]["board"]
    assert round(b["head"]["size_mm"][0] - b["base"]["size_mm"][0], 2) == 10.16
    nets = {c["net"]: c["status"] for c in p["netlist"]["changes"]}
    assert nets["/status_led/LED_A"] == "added"
    assert [v["type"] for v in p["checks"]["drc"]["new"]] == ["track_width"]
    assert p["checks"]["drc"]["fixed"] == []


def test_schematic_grid_check(doc):
    """Default mode "changed": only what head added or moved. pic_programmer's new C10 and power
    symbols are on the 50 mil grid; complex_hierarchy's new LED_A label is 0.635 mm off it."""
    p = proj(doc, "pic_programmer")
    g = p["checks"]["grid"]
    assert (g["mode"], g["grid_mil"], g["count"]) == ("changed", 50.0, 0) and g["checked"] > 0
    assert p["summary"]["grid"] == {"count": 0, "points": 0}
    if "complex_hierarchy" not in [x["slug"] for x in doc["projects"]]:
        return
    g = proj(doc, "complex_hierarchy")["checks"]["grid"]
    (f,) = g["items"]
    assert (f["kind"], f["text"], f["change"], f["sheet"]) == ("label", "LED_A", "added", "root/status_led")
    assert f["file"] == "complex_hierarchy/status_led.kicad_sch" and f["pos_mm"] == [101.6, 70.485]
    assert f["points"][0]["off_mm"] == [0, -0.635]
    text = subprocess.run(["git", "-C", REPO, "show", f"{doc['head']['sha']}:{f['file']}"], capture_output=True,
                          text=True, check=True).stdout
    line = text.split("\n")[f["line"] - 1]
    assert line.strip().startswith('(label "LED_A"')


def test_impedance_check(doc):
    """CHANGES.md "Impedance": SE_50_MS on pic_programmer's Net-(D8-A) (out of tolerance on both sides; width
    0.5 -> 0.4 mm and core 1.51 -> 1.2 mm on head), and a new SE_50_MS class on complex_hierarchy's LED_A."""
    pic = proj(doc, "pic_programmer")
    z = pic["checks"]["impedance"]
    assert z["solver"] in ("field", "closedform") and z["classes"] == ["SE_50_MS"]
    assert z["stackup_changes"] == [{"layer": "dielectric 1", "field": "thickness", "base": 1.51, "head": 1.2}]
    (r,) = z["rows"]
    assert (r["class"], r["layer"], r["status"]) == ("SE_50_MS", "B.Cu", "changed")
    assert (r["base"]["width"], r["head"]["width"]) == (0.5, 0.4)
    assert r["base"]["model"] == "coated_microstrip" and r["base"]["params"]["h"] == 1.51 and r["head"]["params"]["h"] == 1.2
    assert set(r["flags"]) == {"width_change", "stackup_shift", "violation"} and r["severity"] == "warn"
    assert r["shift_pct"] < -5 and r["head"]["within"] is False
    out = r["head"]["length_out_mm"]
    assert out > 0 and out == r["head"]["length_mm"]  # the whole controlled length is out
    assert pic["summary"]["impedance"] == {"rows": 1, "violations": 1, "length_out_mm": out, "new_violations": 0,
                                           "stackup_shifts": 1, "width_changes": 1, "solver": z["solver"]}
    ch = proj(doc, "complex_hierarchy")["checks"]["impedance"]
    (r,) = ch["rows"]
    assert (r["class"], r["layer"], r["status"], r["base"]) == ("SE_50_MS", "B.Cu", "added", None)
    assert r["head"]["width"] == 0.15 and r["flags"] == ["new_violation"] and r["severity"] == "bad"
    assert r["head"]["Z"] > 100  # 0.15 mm on 1.6 mm FR4: far from 50 Ω
    if ch["solver"] == "closedform":
        assert r["head"]["validity"]  # outside the mask fit
    else:
        assert not r["head"]["validity"] and 0 < r["head"]["error_pct"] < 2 and r["head"]["Z_closedform"] > 100


def test_panel(doc):
    """CHANGES.md "Panel": a KiKit-style 2x2 panel of pic_programmer, regenerated on head (new uuids) with
    a fourth fiducial pair and tab 1 moved +20 mm; no schematic."""
    p = next((x for x in doc["projects"] if x["slug"] == "pic_programmer_panel"), None)
    if p is None:
        pytest.skip("fixtures repo predates the panel (rebuild it with build.sh)")
    assert (p["kind"], p["status"], p["errors"]) == ("panel", "modified", [])
    pn = p["panel"]
    assert pn["sources"] == [{"path": "pic_programmer/pic_programmer.kicad_pcb", "copies": 4}] and pn["copies"] == 4
    assert (pn["config"], pn["fiducials"], pn["tooling"], pn["mousebites"]) == ("pic_programmer_panel/kikit.json", 8, 4, 112)
    assert set(pn["signals"]) == {"kikit", "copies", "name"}
    assert p["schematic"] is None and p["bom"] is None and p["netlist"] is None
    assert p["checks"]["erc"] is None and p["checks"]["grid"] is None and p["summary"]["erc"] is None
    # only what really changed: regenerated item order and uuids are no change
    changed = {ly["id"] for ly in p["pcb"]["layers"] if ly["status"] != "unchanged"}
    assert changed == {"F.Cu", "B.Cu", "F.Mask", "B.Mask", "F.CrtYd", "B.CrtYd", "Edge.Cuts", "NPTH"}
    got = sorted((c["kind"], c["what"], c.get("ref") or c.get("feature")) for c in p["pcb"]["changes"])
    assert got == [("fiducial", "added", "KiKit_FID_B_4"), ("fiducial", "added", "KiKit_FID_T_4"),
                   ("mousebites", "moved", None), ("outline", "modified", "tabs")]
    mb = next(c for c in p["pcb"]["changes"] if c["kind"] == "mousebites")
    assert mb["detail"] == "7 holes moved 20.000 mm" and len(mb["refs"]) == 7
    comps = p["pcba3d"]["components"]
    assert len(comps) == 4 * 63 and all(c["status"] == "unchanged" for c in comps)
    assert {c["ref"] for c in comps if c["ref"].startswith("R7·")} == {"R7·0", "R7·1", "R7·2", "R7·3"}
    assert p["pcba3d"]["head"]["glb"]
    assert p["summary"]["panel"] == {"fiducial": 2, "mousebites": 1, "tabs": 1}
    drc = p["checks"]["drc"]
    assert any(v["type"] == "npth_inside_courtyard" and any("KiKit_MB_1_" in i for i in v["items"]) for v in drc["new"])
    assert len(drc["fixed"]) == 1
    # the panel's files are not a change of pic_programmer
    assert not [r for r in proj(doc, "pic_programmer")["reasons"] if "panel" in r]
