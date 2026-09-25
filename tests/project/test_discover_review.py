import json
import os
import subprocess

import pytest

from kipr.project import discover, review
from kipr.common.git import Git

from .test_pcb_diff import HEADER, fp
from .test_sch_diff import root, sym


def sh(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def write(repo, path, text):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo, msg):
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", msg)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


FP_TABLE = '(fp_lib_table (version 7) (lib (name "shared") (type "KiCad") (uri "${KIPRJMOD}/../../libs/shared.pretty") (options "") (descr "")))'


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q")
    write(r, "boards/a/a.kicad_pro", "{}")
    write(r, "boards/a/a.kicad_sch", root(sym()))
    write(r, "boards/a/a.kicad_pcb", HEADER + fp() + ")")
    write(r, "boards/a/fp-lib-table", FP_TABLE)
    write(r, "boards/b/b.kicad_pro", "{}")
    write(r, "boards/b/b.kicad_pcb", HEADER + ")")
    write(r, "libs/shared.pretty/X.kicad_mod", "(footprint X)")
    write(r, "README.md", "hi")
    base = commit(r, "base")
    return r, base


def test_find_projects_changes_noise_and_filters(repo):
    r, base = repo
    git = Git(str(r))
    write(r, "README.md", "changed")
    write(r, "boards/b/b-backups/b.kicad_pcb", "noise")
    write(r, "boards/b/.history/b.kicad_sch", "noise")
    assert discover.find_projects(git, base, commit(r, "noise only"), None) == []
    write(r, "boards/a/a.kicad_pcb", HEADER + fp(x=12) + ")")
    head = commit(r, "move R1")
    projs = discover.find_projects(git, base, head)
    assert [(p.path, p.name, p.status, p.reasons) for p in projs] == [
        ("boards/a", "a", "modified", ["boards/a/a.kicad_pcb"])]
    assert discover.find_projects(git, base, head, ["boards/b*"]) == []
    assert len(discover.find_projects(git, base, head, ["a"])) == 1


def test_project_library_outside_dir_counts_as_change(repo):
    r, base = repo
    git = Git(str(r))
    write(r, "libs/shared.pretty/X.kicad_mod", "(footprint X (descr changed))")
    head = commit(r, "lib change")
    projs = discover.find_projects(git, base, head)
    assert [(p.path, p.reasons) for p in projs] == [("boards/a", ["libs/shared.pretty/X.kicad_mod"])]
    paths = discover.checkout_paths(git, head, projs[0], "head")
    assert "libs/shared.pretty/X.kicad_mod" in paths and "boards/a/a.kicad_pcb" in paths


def test_added_and_removed_projects(repo):
    r, base = repo
    git = Git(str(r))
    sh(r, "rm", "-rq", "boards/b")
    write(r, "boards/c/c.kicad_pro", "{}")
    head = commit(r, "swap")
    got = {p.path: p.status for p in discover.find_projects(git, base, head)}
    assert got == {"boards/b": "removed", "boards/c": "added"}


def test_resolve():
    assert discover.resolve("${KIPRJMOD}/../../libs/x.pretty", "boards/a") == "libs/x.pretty"
    assert discover.resolve("${KICAD10_3DMODEL_DIR}/R.step", "boards/a") is None
    assert discover.resolve("/abs/x.step", "boards/a") is None
    assert discover.resolve("../../../outside", "boards/a") is None
    assert discover.resolve("3d/x.step", "") == "3d/x.step"


def test_review_without_kicad_cli(repo, tmp_path):
    r, base = repo
    write(r, "boards/a/a.kicad_sch", root(sym(value="4.7k")))
    write(r, "boards/a/a.kicad_pcb", HEADER + fp(x=12, value="4.7k") + ")")
    head = commit(r, "change R1")
    out = tmp_path / "out"
    doc = review.run(str(r), base, head, str(out), no_export=True, log=lambda *_: None)
    on_disk = json.loads((out / "project-review.json").read_text())
    assert on_disk == json.loads(json.dumps(doc))
    assert doc["version"] == 1 and doc["base"]["sha"] == base and doc["head"]["short"] == head[:7]
    (p,) = doc["projects"]
    assert (p["slug"], p["path"], p["status"]) == ("a", "boards/a", "modified")
    assert p["errors"] == []
    s = p["summary"]
    assert s["sheets_changed"] == 1 and s["components"]["changed"] == 1 and s["components"]["moved"] == 0 and s["erc"] is None
    (sheet,) = p["schematic"]["sheets"]
    assert sheet["base"] is None and sheet["changes"][0]["what"] == "value"
    assert p["pcb"]["board"]["size_mm"] == [50, 40] and p["pcb"]["board"]["mask_color"] == "green"
    status = {ly["id"]: ly["status"] for ly in p["pcb"]["layers"]}
    assert status["F.Cu"] == "modified" and status["B.Cu"] == "unchanged" and status["Edge.Cuts"] == "unchanged"
    c = p["pcba3d"]["components"][0]
    assert c["what"] == ["position", "value"] and c["status"] == "changed"  # a new value wins over the move
    assert [row["key"] for row in p["bom"]["rows"] if row["status"] == "changed"] == ["R1"]
    assert p["netlist"]["source"] == "board" and p["netlist"]["changes"] == []
    assert p["checks"] == {"erc": None, "drc": None}


def test_review_missing_kicad_cli_is_reported_not_fatal(repo, tmp_path, monkeypatch):
    r, base = repo
    monkeypatch.setattr("kipr.common.kicad_cli.shutil.which", lambda *_: None)
    monkeypatch.delenv("KIPR_KICAD_CLI", raising=False)
    write(r, "boards/b/b.kicad_pcb", "(kicad_pcb (unbalanced")
    head = commit(r, "broken board")
    doc = review.run(str(r), base, head, str(tmp_path / "o"), log=lambda *_: None)
    assert "kicad-cli not found" in doc["errors"][0]
    (p,) = doc["projects"]
    assert p["slug"] == "b" and any("cannot parse head board" in e for e in p["errors"])


FAKE_CLI = r"""#!/bin/sh
# kicad-cli stand-in: `pcb export glb` copies the board it was given to --output (so the test can see
# which model paths the export read); everything else fails.
case "$*" in
  version) echo 10.0.6; exit 0 ;;
  *--help*) echo "--output --subst-models --user-origin --force"; exit 0 ;;
esac
if [ "$1 $2 $3" = "pcb export glb" ]; then
  out=""; prev=""
  for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; done
  for a in "$@"; do last="$a"; done
  cp "$last" "$out"; exit 0
fi
exit 1
"""


def test_model_fallback_only_in_the_export_checkout(repo, tmp_path, monkeypatch):
    r, base = repo
    stock = tmp_path / "stock"
    (stock / "R.3dshapes").mkdir(parents=True)
    (stock / "R.3dshapes" / "R_0603.step").write_text("step")  # the stock library has STEP only
    (r / "boards/a/models").mkdir()
    (r / "boards/a/models/local.wrl").write_text("vrml")          # a project model that exists as .wrl
    monkeypatch.setenv("KIPR_KICAD_3DMODEL_DIR", str(stock))
    wrl = fp(x=12).replace("R_0603.step", "R_0603.wrl")
    local = fp("R2", 20, 10, uuid="u-r2").replace('${KICAD10_3DMODEL_DIR}/R.3dshapes/R_0603.step', "${KIPRJMOD}/models/local.step")
    write(r, "boards/a/a.kicad_pcb", HEADER + wrl + local + ")")
    head = commit(r, "wrl models")
    cli = tmp_path / "kicad-cli"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    out = tmp_path / "out"
    doc = review.run(str(r), base, head, str(out), kicad_cli=str(cli), cache_dir=str(tmp_path / "cache"),
                     patterns=["a"], log=lambda *_: None)
    (p,) = doc["projects"]
    assert doc["tool"]["stock_3d_models"] is True
    models = p["pcba3d"]["models"]["head"]
    assert {(s["from"], s["to"], tuple(s["refs"])) for s in models["substitutions"]} == {
        ("${KICAD10_3DMODEL_DIR}/R.3dshapes/R_0603.wrl", "${KICAD10_3DMODEL_DIR}/R.3dshapes/R_0603.step", ("R1",)),
        ("${KIPRJMOD}/models/local.step", "${KIPRJMOD}/models/local.wrl", ("R2",))}
    assert (models["substituted"], models["missing"], models["unknown"]) == (2, 0, 0)
    exported = (out / p["pcba3d"]["head"]["glb"]).read_text()
    assert "R_0603.step" in exported and "R_0603.wrl" not in exported and "models/local.wrl" in exported
    # the diffs read the committed board: R1's model is .step -> .wrl, a minor format swap
    r1 = next(c for c in p["pcba3d"]["components"] if c["ref"] == "R1")
    assert r1["head"]["model"].endswith("R_0603.wrl") and "model_format" in r1["what"]
    # the committed files are untouched
    assert "R_0603.wrl" in subprocess.run(["git", "-C", str(r), "show", f"{head}:boards/a/a.kicad_pcb"],
                                          capture_output=True, text=True).stdout
