"""checks.impedance (kipr.project.impedance) and how the comment, ping and report show it.

The boards are tiny hand-written KiCad 10 files (a 4-layer stackup, a 50 Ω class, a 90 Ω pair); the
end-to-end run on the kipr-fixtures demo boards is in test_integration_fixtures.py.
"""
import json

import pytest

from kipr.project import impedance as im
from kipr.project import report
from kipr.project.ci import make_comment
from kipr.project.ci.common import impedance_cell, impedance_lines, impedance_summary, load_review

from .test_ci import review_doc, write

STACKUP = """
  (setup
    (stackup
      (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness {h}) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.2) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
    )
  )"""


def seg(x0, y0, x1, y1, w, layer, net):
    return f'  (segment (start {x0} {y0}) (end {x1} {y1}) (width {w}) (layer "{layer}") (net "{net}") (uuid "u-{net}-{x0}-{y0}-{layer}"))\n'


def pad(x, y, net, w=1.5, h=1.0, layer="F.Cu"):
    return (f'  (footprint "J:Pad" (layer "{layer}") (uuid "fp-{net}-{x}-{y}") (at {x} {y})\n'
            f'    (pad "1" smd rect (at 0 0) (size {w} {h}) (layers "{layer}" "F.Mask") (net "{net}") (uuid "p-{net}-{x}-{y}")))\n')


def board(h=0.2104, w_rf=0.36, gap=0.15, w_pair=0.2, zone=False, extra="") -> str:
    t = ('(kicad_pcb (version 20250114) (generator "pcbnew") (generator_version "10.0")\n'
         '  (general (thickness 1.6))\n'
         '  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))\n'
         + STACKUP.format(h=h) + "\n")
    t += seg(10, 10, 40, 10, w_rf, "F.Cu", "/RF")
    t += seg(40, 10, 42, 10, 0.2, "F.Cu", "/RF")  # a neck-down onto a connector pad: a launch, left out
    t += pad(42.5, 10, "/RF")
    t += seg(10, 20, 40, 20, w_pair, "F.Cu", "/USB_D+")
    t += seg(10, 20 + w_pair + gap, 40, 20 + w_pair + gap, w_pair, "F.Cu", "/USB_D-")
    t += seg(10, 30, 40, 30, 0.1, "In1.Cu", "/RF")  # the class also on an inner layer
    if zone:
        t += ('  (zone (net "GND") (layer "F.Cu") (uuid "z1") (connect_pads (clearance 0.2)) (min_thickness 0.25)\n'
              '    (polygon (pts (xy 0 0) (xy 60 0) (xy 60 40) (xy 0 40))))\n')
    return t + extra + ")\n"


PRO = {"net_settings": {"classes": [
    {"name": "Default", "track_width": 0.2, "clearance": 0.2, "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "priority": 2147483647},
    {"name": "SE_50_MS", "track_width": 0.36, "clearance": 0.2, "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "priority": 0},
    {"name": "DP_90_MS", "track_width": 0.2, "clearance": 0.2, "diff_pair_width": 0.2, "diff_pair_gap": 0.15, "priority": 1}],
    "netclass_patterns": [{"netclass": "SE_50_MS", "pattern": "/RF"}, {"netclass": "DP_90_MS", "pattern": "/USB_D*"}]}}


def side(tmp_path, name, pro=PRO, **kw):
    d = tmp_path / name
    d.mkdir()
    text = board(**kw)
    (d / "b.kicad_pcb").write_text(text)
    (d / "b.kicad_pro").write_text(json.dumps(pro))
    return (str(d / "b.kicad_pcb"), str(d / "b.kicad_pro"), text)


def rows(z):
    return {(r["class"], r["layer"]): r for r in z["rows"]}


def test_head_only_rows_and_values(tmp_path):
    z = im.check({"base": None, "head": side(tmp_path, "h")})
    r = rows(z)
    assert sorted(r) == [("DP_90_MS", "F.Cu"), ("SE_50_MS", "F.Cu"), ("SE_50_MS", "In1.Cu")]
    ms = r[("SE_50_MS", "F.Cu")]["head"]
    assert ms["width"] == 0.36 and ms["structure"] == "microstrip" and ms["model"] == "coated_microstrip"
    assert ms["widths"] == [{"width": 0.36, "length_mm": 30.0}, {"width": 0.2, "length_mm": 2.0}]
    assert 48 < ms["Z"] < 51 and ms["within"] is True  # 0.36 mm on 0.21 mm 7628 under mask: about 50 Ω
    assert ms["length_mm"] == 30.0 and ms["routed_mm"] == 32.0 and ms["length_out_mm"] == 0
    assert ms["excluded"] == [{"width": 0.2, "reason": "launch", "length_mm": 2.0}]  # the neck onto the pad
    assert [x["width"] for x in ms["segments"]] == [0.36] and ms["segments"][0]["within"] is True
    sl = r[("SE_50_MS", "In1.Cu")]["head"]
    assert sl["structure"] == "stripline" and sl["params"]["h1"] == 0.2104 and sl["params"]["h2"] == 1.2
    assert any("inner layer" in n for n in sl["notes"])
    dp = r[("DP_90_MS", "F.Cu")]["head"]
    assert dp["model"] == "coupled_microstrip" and dp["gap"] == 0.15 and dp["gaps"][0]["length_mm"] == 30.0
    assert dp["Zcommon"] is not None
    assert r[("DP_90_MS", "F.Cu")]["target"] == {"kind": "differential", "target": 90.0, "tolerance_pct": 10.0,
                                                "tolerance_default": True, "common_mode": None,
                                                "structure": "microstrip", "source": "name"}
    assert all(x["status"] == "added" for x in z["rows"])
    c = z["count"]
    assert c["rows"] == 3 and c["new_violations"] == c["violations"] == sum(1 for x in z["rows"] if x["head"]["within"] is False)
    solver = z["solver"]
    assert z["method"] == im.METHODS[solver] and ms["solver"] == solver
    if solver == "field":  # its own error estimate; tier 1 alongside for comparison
        assert 0 < ms["error_pct"] < 1 and abs(ms["Z"] - ms["Z_closedform"]) < 0.03 * ms["Z"]
        assert z["field_solves"] >= 3
    else:
        assert ms["error_pct"] is None and ms["Z"] == ms["Z_closedform"] and z["field_solves"] == 0


def test_base_head_flags(tmp_path):
    z = im.check({"base": side(tmp_path, "b"), "head": side(tmp_path, "h", h=0.15, w_rf=0.2, gap=0.2)})
    r = rows(z)
    ms = r[("SE_50_MS", "F.Cu")]
    assert ms["status"] == "changed" and {"stackup_shift", "width_change", "new_violation"} <= set(ms["flags"])
    assert ms["severity"] == "bad" and ms["shift_pct"] < -5  # a thinner prepreg lowers Z
    assert z["stackup_changes"] == [{"layer": "dielectric 1", "field": "thickness", "base": 0.2104, "head": 0.15}]
    dp = r[("DP_90_MS", "F.Cu")]
    assert "width_change" in dp["flags"] and dp["head"]["gap"] == 0.2
    sl = r[("SE_50_MS", "In1.Cu")]  # inner layer: the outer prepreg is above it too
    assert sl["shift_pct"] is not None and "width_change" not in sl["flags"]
    assert z["count"]["stackup_shifts"] >= 2 and z["count"]["width_changes"] == 2


def test_unchanged_board_is_quiet(tmp_path):
    z = im.check({"base": side(tmp_path, "b"), "head": side(tmp_path, "h")})
    assert all(r["status"] == "same" and r["delta_pct"] == 0 for r in z["rows"])
    assert z["count"]["new_violations"] == z["count"]["stackup_shifts"] == z["count"]["width_changes"] == 0
    assert z["stackup_changes"] == []


def test_coplanar_class_uses_the_zone_clearance(tmp_path):
    pro = json.loads(json.dumps(PRO))
    pro["net_settings"]["classes"][1]["name"] = "SE_50_CP"
    pro["net_settings"]["netclass_patterns"][0]["netclass"] = "SE_50_CP"
    z = im.check({"base": None, "head": side(tmp_path, "h", pro=pro, zone=True)})
    cp = rows(z)[("SE_50_CP", "F.Cu")]["head"]
    assert cp["structure"] == "coplanar_grounded" and cp["model"] == "cpwg" and cp["coplanar_gap"] == 0.2
    # without a zone beside the track: microstrip, with a note
    z = im.check({"base": None, "head": side(tmp_path, "h2", pro=pro)})
    cp = rows(z)[("SE_50_CP", "F.Cu")]["head"]
    assert cp["structure"] == "microstrip" and any("no copper zone" in n for n in cp["notes"])


def test_no_classes_and_no_board(tmp_path):
    pro = {"net_settings": {"classes": [PRO["net_settings"]["classes"][0]], "netclass_patterns": []}}
    z = im.check({"base": None, "head": side(tmp_path, "h", pro=pro)})
    assert z["rows"] == [] and z["classes"] == [] and z["count"]["rows"] == 0
    assert im.check({"base": None, "head": None}) is None


def test_bad_board_is_an_error_not_a_crash(tmp_path):
    errs = []
    assert im.check({"base": None, "head": ("/nonexistent.kicad_pcb", None, "")}, errs.append) is None
    assert errs and "impedance check (head)" in errs[0]


def test_pair_gap_needs_parallel_overlap():
    a = [{"a": (0, 0), "b": (10, 0), "length": 10, "width": 0.2}]
    assert im.pair_gap(a, [{"a": (0, 0.35), "b": (10, 0.35), "length": 10, "width": 0.2}]) == {0.15: 10.0}
    assert im.pair_gap(a, [{"a": (20, 0.35), "b": (30, 0.35), "length": 10, "width": 0.2}]) == {}   # no overlap
    assert im.pair_gap(a, [{"a": (0, 0.35), "b": (10, 5), "length": 11, "width": 0.2}]) == {}       # not parallel


def test_a_neck_mid_route_counts_as_length_out_of_tolerance(tmp_path):
    """A 2 mm neck that does not end on a pad is controlled length: evaluated at its own width, and the row reports
    the length out of tolerance instead of judging the main width alone."""
    text = board().replace(pad(42.5, 10, "/RF"), "") + ""
    text = text[:-2] + seg(42, 10, 52, 10, 0.36, "F.Cu", "/RF") + ")\n"   # the line carries on after the neck
    d = tmp_path / "n"
    d.mkdir()
    (d / "b.kicad_pcb").write_text(text)
    (d / "b.kicad_pro").write_text(json.dumps(PRO))
    z = im.check({"base": None, "head": (str(d / "b.kicad_pcb"), str(d / "b.kicad_pro"), text)})
    ms = rows(z)[("SE_50_MS", "F.Cu")]["head"]
    assert [(x["width"], x["length_mm"], x["within"]) for x in ms["segments"]] == [(0.36, 40.0, True), (0.2, 2.0, False)]
    assert ms["width"] == 0.36 and ms["deviation_pct"] < 3 and ms["worst_deviation_pct"] > 20
    assert ms["length_out_mm"] == 2.0 and ms["within"] is False and ms["excluded"] == []
    assert z["count"]["length_out_mm"] == round(sum(x["head"]["length_out_mm"] or 0 for x in z["rows"]), 3) >= 2.0


def test_stub_rules():
    """covered (a thin track under a fat one), launch (short, other width, ends on a pad), short (< MIN_RUN_MM)."""
    def s(x0, x1, w, y=0.0, net="A"):
        return {"net": net, "layer": "F.Cu", "width": w, "a": (x0, y), "b": (x1, y), "length": abs(x1 - x0)}
    pads_ = [{"net": "A", "layers": ["F.Cu"], "box": (-1, -0.5, 0, 0.5)}]
    main = [s(3, 10, 0.26), s(10, 20, 0.26)]
    fat = s(0, 2.5, 0.8)            # the 0.8 mm launch off the pad (m1421 B.Cu)
    thin = s(0.5, 2.0, 0.26)        # drawn over it
    tiny = s(25, 25.3, 0.3)         # an isolated 0.3 mm piece
    keep, left = im.controlled(main + [fat, thin, tiny], 0.26, pads_)
    assert keep == main
    assert sorted((x["width"], x["reason"]) for x in left) == [(0.26, "covered"), (0.3, "short"), (0.8, "launch")]
    # the same fat run not on a pad, or longer than LAUNCH_MAX_MM: it counts
    keep, _ = im.controlled([fat], 0.26, [])
    assert keep == [fat]
    long_fat = s(0, im.LAUNCH_MAX_MM + 1, 0.8)
    keep, _ = im.controlled([long_fat], 0.26, pads_)
    assert keep == [long_fat]
    # chains: two 0.3 mm segments of one run are 0.6 mm together, not short
    keep, _ = im.controlled([s(30, 30.3, 0.26), s(30.3, 30.6, 0.26)], 0.26, [])
    assert len(keep) == 2


def test_pair_breakout_and_uncoupled(tmp_path):
    """Pair sections fanning out to their pads (gap > BREAKOUT_GAP x the main gap, short) and stretches without the
    other net beside them are left out; the coupled part at the main gap is what counts."""
    extra = (seg(40, 20, 41, 19, 0.2, "F.Cu", "/USB_D+")                     # D+ bends away ...
             + seg(41, 19, 42, 19, 0.2, "F.Cu", "/USB_D+")                     # ... and runs 1 mm at a wide gap
             + seg(40, 20.35, 42, 20.35, 0.2, "F.Cu", "/USB_D-"))
    d = tmp_path / "p"
    d.mkdir()
    text = board(extra=extra)
    (d / "b.kicad_pcb").write_text(text)
    (d / "b.kicad_pro").write_text(json.dumps(PRO))
    z = im.check({"base": None, "head": (str(d / "b.kicad_pcb"), str(d / "b.kicad_pro"), text)})
    dp = rows(z)[("DP_90_MS", "F.Cu")]["head"]
    assert [(x["gap"], x["length_mm"]) for x in dp["segments"]] == [(0.15, 30.0)]
    assert {x["reason"] for x in dp["excluded"]} == {"breakout", "uncoupled"}
    assert dp["length_out_mm"] == (0 if dp["segments"][0]["within"] else 30.0)  # the breakout is not judged


def test_solver_fallback(tmp_path, monkeypatch):
    """Without the [field] extra the check falls back to the closed-form models and says so."""
    monkeypatch.setattr(im, "field_available", lambda: "no scipy here")
    monkeypatch.delenv("KIPR_IMPEDANCE_SOLVER", raising=False)
    z = im.check({"base": None, "head": side(tmp_path, "h")})
    assert z["solver"] == "closedform" and "no scipy here" in z["solver_note"] and z["method"].startswith("closed-form")
    assert im.pick_solver("closedform")[0] == "closedform"


@pytest.mark.skipif(im.field_available() is not None, reason="boarddd[field] (numpy, scipy) not installed")
def test_field_solver(tmp_path):
    """With the field solver: differential coplanar is solved as such (tier 1 has no model), stackup shifts are
    re-solved, and the numbers carry the solver's error estimate."""
    pro = json.loads(json.dumps(PRO))
    pro["net_settings"]["classes"][2]["name"] = "DP_90_CPWG"
    pro["net_settings"]["netclass_patterns"][1]["netclass"] = "DP_90_CPWG"
    z = im.check({"base": side(tmp_path, "b", pro=pro, zone=True), "head": side(tmp_path, "h", pro=pro, zone=True, h=0.15)},
                 solver="field")
    r = rows(z)
    dp = r[("DP_90_CPWG", "F.Cu")]["head"]
    assert z["solver"] == "field" and dp["structure"] == "coplanar_grounded" and dp["model"] is None
    assert dp["Z_closedform"] is None and dp["Z"] and 0 < dp["error_pct"] < 1 and dp["Zcommon"]
    assert any("field solver only" in n for n in dp["notes"])
    assert r[("SE_50_MS", "F.Cu")]["shift_pct"] < -5 and "stackup_shift" in r[("DP_90_CPWG", "F.Cu")]["flags"]


# --- comment, ping, report ------------------------------------------------------------------

def imp_doc(tmp_path):
    z = im.check({"base": side(tmp_path, "b"), "head": side(tmp_path, "h", h=0.15, w_rf=0.2, gap=0.2)})
    doc = review_doc()
    doc["projects"][0]["checks"]["impedance"] = z
    doc["projects"][0]["summary"]["impedance"] = dict(z["count"])
    return doc, z


def test_comment_line_cell_and_details(tmp_path):
    doc, z = imp_doc(tmp_path)
    c = z["count"]
    body = make_comment.build_comment(load_review(write(tmp_path, doc)))
    assert f"| 🔴 {c['violations']} / {c['rows']} |" in body
    assert body.count("**Impedance** (" + ("boarddd field solver" if z["solver"] == "field" else "closed-form estimate, not a field solve") + ")") == 1
    assert f"{c['rows']} class × layer checked, {c['violations']} out of tolerance (🔴 {c['new_violations']} new)" in body
    assert "🔴 Z0 <code>SE_50_MS</code> on F.Cu (microstrip, w 0.2 mm):" in body and "vs 50 Ω ±10 %" in body
    ping = make_comment.ping_line(load_review(write(tmp_path, doc)), "a" * 40)
    assert f"Z {c['violations']}/{c['rows']} out of tolerance ({c['new_violations']} new)" in ping


def test_comment_without_the_check(tmp_path):
    body = make_comment.build_comment(load_review(write(tmp_path, review_doc())))
    assert "**Impedance**" not in body and impedance_cell(review_doc()["projects"][0]) == "n/a"
    assert impedance_summary(review_doc()["projects"]) == ""


def test_impedance_text_is_escaped():
    p = {"checks": {"impedance": {"rows": [{"class": "<b>@x</b>", "layer": "F.Cu|", "severity": "bad",
                                            "target": {"kind": "single", "target": 50, "tolerance_pct": 10},
                                            "head": {"Z": 70.0, "deviation_pct": 40.0, "width": 0.1, "structure": "<i>"},
                                            "flags": ["new_violation", "<script>"]}]}}}
    (line,) = impedance_lines(p)
    assert "<b>" not in line and "<i>" not in line and "<script>" not in line and "newly out of tolerance" in line


def test_report_section(tmp_path):
    _, z = imp_doc(tmp_path)
    html = report.impedance_section(z)
    assert ("field solver" if z["solver"] == "field" else "closed-form estimate") in html and "SE_50_MS" in html and "out of tolerance, new" in html
    assert "stackup alone" in html and "Stackup changes: dielectric 1 thickness 0.2104 → 0.15" in html
    assert "No net class has an impedance target" in report.impedance_section({"rows": [], "count": {}})
    assert report.impedance_section(None) == ""


@pytest.mark.parametrize("bad", [None, [], "x", {"rows": "x"}, {"rows": [None, 3, {"head": "x"}]}])
def test_report_survives_junk(bad):
    report.impedance_section(bad)
