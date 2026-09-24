"""End-to-end run against the public kipr-fixtures repo with a real kicad-cli.

Skipped unless both exist. Locations: $KIPR_FIXTURES (default: ../kipr-fixtures next to this
checkout, then /workspace/projects/kipr-fixtures) and $KIPR_KICAD_CLI / kicad-cli on PATH.
Set $KIPR_CACHE_DIR to reuse exports between runs (a cold run takes ~1-2 minutes, mostly ERC/DRC).
"""

import json
import os
import subprocess

import pytest

from kipr.project import review
from kipr.project._compat import find_kicad_cli

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
