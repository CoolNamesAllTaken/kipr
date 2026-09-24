"""Static checks of the project-review workflows (security properties + actionlint when available)."""
import os
import shutil
import subprocess

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WF = os.path.join(ROOT, ".github", "workflows")


def load(name):
    with open(os.path.join(WF, name)) as fh:
        return yaml.safe_load(fh)


def steps(wf):
    return [s for job in wf["jobs"].values() for s in job["steps"]]


def test_review_is_unprivileged_and_has_the_inputs():
    wf = load("project-review.yml")
    call = wf[True]["workflow_call"]  # YAML 1.1 reads the `on:` key as True
    assert set(call["inputs"]) >= {"kipr-ref", "projects", "fast-checks", "kicad-image", "max-artifact-mb"}
    assert call["inputs"]["fast-checks"]["default"] is False
    assert call["inputs"]["kicad-image"]["default"].startswith("kicad/kicad:10.0.")
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["review"]
    assert "permissions" not in job and job["container"]["image"] == "${{ inputs.kicad-image }}"
    co = next(s for s in steps(wf) if s.get("uses", "").startswith("actions/checkout"))
    assert co["with"]["fetch-depth"] == 0 and co["with"]["persist-credentials"] is False
    names = {s.get("with", {}).get("name") for s in steps(wf) if "upload-artifact" in s.get("uses", "")}
    assert names >= {"project-review-site", "project-review-data", "pr-meta"}
    report = next(s for s in steps(wf) if s.get("id") == "report")
    assert report["with"]["archive"] is False and report["with"]["path"].endswith("/project-review.html")
    assert "{{ secrets." not in open(os.path.join(WF, "project-review.yml")).read().replace("${{ ", "{{ ")  # no secret is used


def test_publish_never_runs_pr_code():
    wf = load("project-review-publish.yml")
    assert wf["permissions"] == {}
    job = wf["jobs"]["publish"]
    assert job["permissions"] == {"pull-requests": "write", "actions": "read"}
    assert "workflow_run" in job["if"]
    for s in job["steps"]:
        assert not s.get("uses", "").startswith("actions/checkout")
        for line in s.get("run", "").splitlines():  # downloaded files are only ever arguments, never commands
            first = line.strip().split(" ")[0]
            assert "UNTRUSTED" not in first and "untrusted" not in first, line
    downloads = [s["with"]["name"] for s in job["steps"] if "download-artifact" in s.get("uses", "")]
    assert downloads == ["pr-meta", "project-review-data"]  # never the site or the HTML report


def test_third_party_actions_pinned_or_official():
    for name in ("project-review.yml", "project-review-publish.yml"):
        for s in steps(load(name)):
            u = s.get("uses")
            if u and "upload-artifact" in u:
                assert len(u.split("@")[1]) == 40  # pinned by sha, like the library review
            elif u:
                assert u.split("/")[0] == "actions", u


@pytest.mark.skipif(not (shutil.which("actionlint") or os.path.exists("/workspace/projects/kipr-tools/bin/actionlint")),
                    reason="actionlint not installed")
def test_actionlint():
    exe = shutil.which("actionlint") or "/workspace/projects/kipr-tools/bin/actionlint"
    env = dict(os.environ, PATH=os.path.dirname(exe) + os.pathsep + os.environ.get("PATH", ""))  # + shellcheck
    r = subprocess.run([exe, os.path.join(WF, "project-review.yml"), os.path.join(WF, "project-review-publish.yml")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
