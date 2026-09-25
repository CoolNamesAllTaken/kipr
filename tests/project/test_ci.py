import json

import pytest

from kipr.cli import main as kipr_main
from kipr.project.ci import job_summary, limit_size, make_comment, post_comment, pr_meta
from kipr.project.ci.common import MARKER, load_review

SHA = "a" * 40
EVIL = '<script>alert(1)</script> @octocat ![x](https://evil/x.png) | `tick`'


def violation(kind="track_width", severity="error", uuid="3221cb6a-381f-4645-aae4-cf1c02ac5acb"):
    return {"severity": severity, "type": kind, "description": "Track width (actual 0.15 mm)",
            "items": ["Track [/LED_A] on B.Cu"], "uuids": [uuid], "pos_mm": [193.04, 66.04], "sheet": None}


def review_doc(**over):
    p = {"slug": "board", "name": "board", "path": "hw/board", "status": "modified",
         "summary": {"sheets_changed": 1, "layers_changed": 3,
                     "components": {"added": 1, "removed": 0, "moved": 2, "changed": 1}, "nets_changed": 2,
                     "erc": {"new": 0, "fixed": 1}, "drc": {"new": 1, "fixed": 0}},
         "schematic": {"sheets": [{"id": "root", "title": "board", "status": "modified",
                                   "changes": [{"kind": "symbol", "what": "value", "ref": "R1",
                                                "detail": "value 10k -> 4.7k"},
                                               {"kind": "symbol", "what": "added", "ref": "#PWR01", "power": True}]}]},
         "pcb": {"changes": [{"kind": "footprint", "what": "moved", "ref": "U1", "detail": "moved 1 mm"}],
                 "layers": []},
         "checks": {"erc": {"new": [], "fixed": [violation("pin_not_connected")]},
                    "drc": {"new": [violation()], "fixed": []}},
         "errors": []}
    p.update(over)
    return {"version": 1, "tool": {"kicad": "10.0.6"}, "base": {"short": "1111111", "sha": "1" * 40},
            "head": {"short": "2222222", "sha": SHA}, "projects": [p], "errors": []}


def write(tmp_path, doc, name="project-review.json"):
    f = tmp_path / name
    f.write_text(json.dumps(doc))
    return f


# --- comment ---------------------------------------------------------------------------

def test_comment_has_marker_table_links_and_details(tmp_path):
    doc = load_review(write(tmp_path, review_doc()))
    body = make_comment.build_comment(doc, run_url="https://github.com/o/r/actions/runs/5",
                                      report_url="https://github.com/o/r/actions/runs/5/artifacts/9")
    assert body.startswith(MARKER + "\n## KiCad project review")
    assert "between <code>1111111</code> (merge base) and <code>2222222</code>" in body
    assert "| <code>board</code><br><sub>hw/board</sub> | ✏️ modified | 1 | 3 | +1 −0 ↔2 ~1 | 2 | 0 / 1 | 🔴 1 / 0 |" in body
    assert "[**📄 project-review.html**](https://github.com/o/r/actions/runs/5/artifacts/9)" in body
    assert "project-review-site" not in body  # no URL given -> no link
    assert "🔴 DRC <code>track_width</code>" in body and "@ (193.04, 66.04) mm" in body
    assert "✅ fixed: ERC 1" in body
    assert "symbol value <code>R1</code>: value 10k -&gt; 4.7k" in body
    assert "#PWR01" not in body  # power symbols are noise in a comment


def test_comment_escapes_untrusted_text(tmp_path):
    d = review_doc(name=EVIL, errors=[EVIL])
    d["projects"][0]["pcb"]["changes"][0]["detail"] = EVIL
    d["projects"][0]["checks"]["drc"]["new"][0]["description"] = EVIL
    body = make_comment.build_comment(load_review(write(tmp_path, d)))
    assert "<script>" not in body and "@octocat" not in body and "![x]" not in body
    assert "&lt;script&gt;" in body and "&#64;octocat" in body


def test_comment_rejects_bad_urls_and_types(tmp_path):
    d = review_doc()
    d["projects"][0]["summary"] = {"sheets_changed": True, "components": "lots", "erc": [1]}
    body = make_comment.build_comment(load_review(write(tmp_path, d)), run_url="javascript:alert(1)",
                                      site_url="https://x/)[evil](https://y")
    assert "javascript:" not in body
    assert "](https://x/%29%5Bevil%5D%28https://y)" in body or "https://x/%29" in body
    assert "| 0 |" in body  # junk counts render as 0


def test_comment_no_projects_and_size_cap(tmp_path):
    empty = load_review(write(tmp_path, {"projects": [], "head": {"sha": SHA}}))
    assert "No KiCad project changed" in make_comment.build_comment(empty)
    d = review_doc()
    d["projects"][0]["pcb"]["changes"] = [{"kind": "track", "what": "added", "net": f"N{i}", "detail": "x" * 150}
                                          for i in range(40)]
    d["projects"] = [dict(d["projects"][0], slug=f"b{i}", name=f"b{i}") for i in range(30)]
    body = make_comment.build_comment(load_review(write(tmp_path, d)))
    assert len(body) <= make_comment.MAX_COMMENT
    assert "Details were too long" in body and "<details>" not in body


def test_make_comment_cli(tmp_path, capsys):
    f = write(tmp_path, review_doc())
    assert kipr_main(["project", "ci", "make-comment", "--data", str(f)]) == 0
    assert capsys.readouterr().out.startswith(MARKER)


# --- job summary / annotations ---------------------------------------------------------

def test_annotations_locate_items_by_uuid(tmp_path):
    repo = tmp_path / "repo"
    (repo / "hw" / "board").mkdir(parents=True)
    (repo / "hw" / "board" / "board.kicad_pcb").write_text(
        '(kicad_pcb\n  (segment (start 0 0) (end 1 1)\n    (uuid "3221cb6a-381f-4645-aae4-cf1c02ac5acb"))\n)\n')
    doc = review_doc()
    doc["projects"][0]["checks"]["drc"]["new"].append(violation("silk_overlap", "warning", uuid="nope"))
    doc["projects"][0]["checks"]["drc"]["new"].append(violation("excluded_thing", "exclusion"))
    lines = job_summary.annotations(load_review(write(tmp_path, doc)), repo)
    assert len(lines) == 2
    assert lines[0].startswith("::error file=hw/board/board.kicad_pcb,line=3,title=New DRC error%3A track_width (board)::")
    assert lines[0].endswith("(at 193.04, 66.04 mm)")
    assert lines[1].startswith("::warning file=hw/board/board.kicad_pcb,line=1,")  # unknown uuid -> main file


def test_annotations_escape_workflow_commands(tmp_path):
    doc = review_doc()
    v = doc["projects"][0]["checks"]["drc"]["new"][0]
    v["description"] = "bad\n::error::injected%"
    v["type"] = "a,b:c"
    (line,) = job_summary.annotations(load_review(write(tmp_path, doc)), None)
    assert "\n" not in line and "::error::injected%25" in line  # newline gone, % escaped
    assert "title=New DRC error%3A a%2Cb%3Ac" in line


def test_annotations_skip_unsafe_project_names_and_paths(tmp_path):
    doc = review_doc(name="../../x;rm", path="/etc")
    assert job_summary.annotations(load_review(write(tmp_path, doc)), tmp_path) == []


def test_job_summary_cli(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    write(out, review_doc())
    summ = tmp_path / "summary.md"
    rc = kipr_main(["project", "ci", "job-summary", "--out", str(out), "--annotate", "--repo-dir", str(tmp_path),
                    "--summary", str(summ), "--link", "run=https://github.com/o/r/actions/runs/1",
                    "--link", "site=", "--link", "extra=https://example.com/x"])
    assert rc == 0
    assert capsys.readouterr().out.startswith("::error ")
    text = summ.read_text()
    assert MARKER not in text and "## KiCad project review" in text
    assert "[**⚙️ workflow run**](https://github.com/o/r/actions/runs/1)" in text
    assert "- [extra](https://example.com/x)" in text


# --- limit-size ------------------------------------------------------------------------

def make_out(tmp_path):
    out = tmp_path / "out"

    def f(rel, kb):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * kb * 1024)
        return rel

    doc = review_doc()
    p = doc["projects"][0]
    p["pcb"]["layers"] = [
        {"id": "F.Cu", "status": "modified", "base": {"gerber": f("p/b/pcb/base/F_Cu.gbr", 100), "svg": f("p/b/pcb/base/F_Cu.svg", 100)},
         "head": {"gerber": f("p/b/pcb/head/F_Cu.gbr", 100), "svg": f("p/b/pcb/head/F_Cu.svg", 100)}},
        {"id": "B.Cu", "status": "unchanged", "base": {"gerber": f("p/b/pcb/base/B_Cu.gbr", 100), "svg": f("p/b/pcb/base/B_Cu.svg", 300)},
         "head": {"gerber": f("p/b/pcb/head/B_Cu.gbr", 100), "svg": f("p/b/pcb/head/B_Cu.svg", 300)}}]
    p["pcba3d"] = {"base": {"glb": f("p/b/3d/base.glb", 400), "step": f("p/b/3d/base.step", 500)}, "head": None}
    p["schematic"]["sheets"][0].update({"base": f("p/b/sch/base/root.svg", 50), "head": f("p/b/sch/head/root.svg", 50)})
    write(out, doc)
    return out


def test_limit_size_noop_when_small(tmp_path):
    out = make_out(tmp_path)
    before = (out / "project-review.json").read_text()
    assert limit_size.limit(out, 10 * 1024 * 1024)[0] == 0
    assert (out / "project-review.json").read_text() == before


def test_limit_size_prunes_in_order_and_nulls_paths(tmp_path):
    out = make_out(tmp_path)
    removed, size = limit_size.limit(out, 1200 * 1024)  # total ~2.2 MB
    assert removed == 3 and size <= 1200 * 1024  # STEP, then the two unchanged-layer SVGs
    doc = json.loads((out / "project-review.json").read_text())
    p = doc["projects"][0]
    assert p["pcba3d"]["base"] == {"glb": "p/b/3d/base.glb", "step": None}
    b_cu = p["pcb"]["layers"][1]
    assert b_cu["base"]["svg"] is None and b_cu["base"]["gerber"] == "p/b/pcb/base/B_Cu.gbr"
    assert p["pcb"]["layers"][0]["head"]["svg"] == "p/b/pcb/head/F_Cu.svg"  # changed layer kept
    assert not (out / "p/b/3d/base.step").exists() and (out / "p/b/3d/base.glb").exists()
    assert "artifact size limit: removed SVGs of unchanged layers" in p["errors"]


def test_limit_size_last_resort_and_traversal(tmp_path):
    out = make_out(tmp_path)
    outside = tmp_path / "keep.txt"
    outside.write_text("precious")
    doc = json.loads((out / "project-review.json").read_text())
    doc["projects"][0]["pcb"]["layers"][0]["base"]["svg"] = "../keep.txt"
    write(out, doc)
    removed, size = limit_size.limit(out, 1)
    assert outside.read_text() == "precious"  # never deletes outside OUT
    left = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert left == ["p/b/pcb/base/F_Cu.svg", "project-review.json"]  # only the file no longer referenced


# --- pr-meta / post-comment ------------------------------------------------------------

def test_pr_meta(tmp_path):
    assert pr_meta.main(["--pr", "12", "--head-sha", SHA, "--base-sha", "b" * 40, "--merge-base", "c" * 40,
                         "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "pr.json").read_text()) == {
        "pr": 12, "head_sha": SHA, "base_sha": "b" * 40, "merge_base": "c" * 40}
    with pytest.raises(ValueError):
        pr_meta.main(["--pr", "12; rm -rf /", "--head-sha", SHA, "--base-sha", SHA, "--merge-base", SHA,
                      "--out", str(tmp_path)])
    with pytest.raises(ValueError):
        pr_meta.main(["--pr", "1", "--head-sha", "HEAD", "--base-sha", SHA, "--merge-base", SHA, "--out", str(tmp_path)])


class FakeGitHub:
    def __init__(self, comments, token="t"):
        self.token = token
        self.comments = comments
        self.calls = []

    def paginate(self, path, limit_pages=30):
        return self.comments

    def request(self, method, path, body=None, accept=None):
        self.calls.append((method, path, body))
        return {"html_url": "u"}, ""


BOT = {"login": "github-actions[bot]"}


def test_post_comment_create_update_skip():
    gh = FakeGitHub([{"id": 1, "user": {"login": "someone"}, "body": MARKER}])  # not ours
    assert post_comment.publish(gh, "o/r", 3, "B", has_projects=True) == "created"
    assert gh.calls == [("POST", "/repos/o/r/issues/3/comments", {"body": "B"})]
    gh = FakeGitHub([{"id": 7, "user": BOT, "body": "x " + MARKER}])
    assert post_comment.publish(gh, "o/r", 3, "B", has_projects=False) == "updated"
    assert gh.calls == [("PATCH", "/repos/o/r/issues/comments/7", {"body": "B"})]
    gh = FakeGitHub([])
    assert post_comment.publish(gh, "o/r", 3, "B", has_projects=False) == "skipped" and gh.calls == []
    gh = FakeGitHub([])
    assert post_comment.publish(gh, "o/r", 3, "B", has_projects=True, dry_run=True) == "created" and gh.calls == []


def test_post_comment_dry_run_cli_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr("kipr.library.ci.common.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    f = write(tmp_path, review_doc())
    rc = post_comment.main(["--data", str(f), "--repo", "o/r", "--pr", "3", "--head-sha", SHA, "--dry-run",
                            "--comment-out", str(tmp_path / "c.md")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would be created" in out and MARKER in out
    assert (tmp_path / "c.md").read_text().startswith(MARKER)
    # missing data file (failed run): still a comment, saying so
    rc = post_comment.main(["--data", str(tmp_path / "nope.json"), "--repo", "o/r", "--pr", "3", "--head-sha", SHA,
                            "--dry-run", "--note", "The review run failed"])
    out = capsys.readouterr().out
    assert "project-review.json missing" in out and "The review run failed" in out


def test_ci_dispatch(capsys):
    assert kipr_main(["project", "ci", "--help"]) == 0
    assert "post-comment" in capsys.readouterr().out
    assert kipr_main(["project", "ci", "nope"]) == 2


def test_minor_changes_are_grouped_in_the_comment():
    from kipr.project.ci.common import change_lines, summary_table
    minor = [{"kind": "footprint", "what": "model_format", "whats": ["model_format"], "ref": f"R{i}", "minor": True,
              "detail": "3D model format .wrl -> .step"} for i in range(1, 41)]
    p = {"slug": "a", "name": "a", "path": "a", "status": "modified",
         "summary": {"components": {"added": 0, "removed": 0, "moved": 0, "changed": 1, "minor": 40}},
         "pcb": {"changes": [{"kind": "footprint", "what": "value", "ref": "C1", "detail": "value 1u -> 2u"}] + minor}}
    lines = change_lines(p)
    assert len(lines) == 2 and "C1" in lines[0]
    assert "40 part(s): 3D model format .wrl -&gt; .step" in lines[1] and "R30" in lines[1] and "R31" not in lines[1]
    assert "~1 (+40 minor)" in summary_table({"projects": [p]})


def test_model_path_resolution(tmp_path, monkeypatch):
    from kipr.project import models
    monkeypatch.delenv("KICAD_LIBS_DIR", raising=False)
    assert models.resolve("${KICAD_LIBS_DIR}/x.step", str(tmp_path), []) is None
    assert models.resolve("${KICAD9_3DMODEL_DIR}/x.step", str(tmp_path), []) is None
    assert models.resolve("kicad-embed://x.step", str(tmp_path), []) is None
    assert models.resolve("m/x.step", str(tmp_path), []) == [str(tmp_path / "m/x.step")]
    monkeypatch.setenv("KICAD_LIBS_DIR", str(tmp_path / "libs"))
    assert models.resolve("${KICAD_LIBS_DIR}/x.step", str(tmp_path), []) == [str(tmp_path / "libs/x.step")]
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "x.STEP").write_text("")
    text, subs, counts = models.substitute('(model "m/x.wrl") (model "m/y.wrl") (model "${NOPE}/z.wrl")', str(tmp_path), [])
    assert subs == {"m/x.wrl": "m/x.STEP"} and '(model "m/x.STEP")' in text
    assert counts == {"found": 0, "substituted": 1, "missing": 1, "unknown": 1}
