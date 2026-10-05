"""The "review updated" ping of both publish steps (kipr.library.ci.ping) against a fake GitHub."""
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lib_mock_site as make_mock  # noqa: E402

from kipr.library.ci import ping, post_review  # noqa: E402
from kipr.project.ci import post_comment  # noqa: E402

REPO = "o/r"
SHA1, SHA2 = "1" * 40, "2" * 40
BOT = {"login": "github-actions[bot]"}
HUMAN = {"login": "designer"}


class FakeGitHub:
    """Issue comments of PR 3 (REST) plus nodes / minimizeComment (GraphQL)."""
    def __init__(self, minimize_error=False):
        self.token = "t"
        self.comments: list[dict] = []
        self.minimized: set[str] = set()
        self.minimize_error = minimize_error
        self.calls: list[tuple] = []
        self.next_id = 100

    def add(self, body, user=BOT):
        self.next_id += 1
        c = {"id": self.next_id, "node_id": f"IC_{self.next_id}", "user": user, "body": body,
             "html_url": f"https://github.com/{REPO}/pull/3#issuecomment-{self.next_id}"}
        self.comments.append(c)
        return c

    def paginate(self, path, limit_pages=30):
        self.calls.append(("GET", path))
        if path.endswith("/issues/3/comments"):
            return [dict(c) for c in self.comments]
        if path.endswith("/pulls/3/comments") or path.endswith("/pulls/3/files"):
            return []
        raise AssertionError(path)

    def request(self, method, path, body=None, accept=None):
        self.calls.append((method, path))
        if method == "POST" and path == f"/repos/{REPO}/issues/3/comments":
            return dict(self.add(body["body"])), ""
        m = re.fullmatch(rf"/repos/{REPO}/issues/comments/(\d+)", path)
        if m:
            c = next(c for c in self.comments if c["id"] == int(m.group(1)))
            if method == "PATCH":
                c["body"] = body["body"]
                return dict(c), ""
            if method == "DELETE":
                self.comments.remove(c)
                return None, ""
        return {}, ""

    def graphql(self, query, variables=None):
        self.calls.append(("GRAPHQL", query.strip().split("(")[0]))
        if "minimizeComment" in query:
            if self.minimize_error:
                raise RuntimeError("GitHub GraphQL error: Resource not accessible by integration")
            self.minimized.add(variables["id"])
            return {}
        return {"nodes": [{"id": i, "isMinimized": i in self.minimized} for i in variables["ids"]]}

    def pings(self, kind):
        return [c for c in self.comments if ping.ping_marker(kind) in c["body"]]

    def live_pings(self, kind):
        return [c for c in self.pings(kind) if c["node_id"] not in self.minimized]


# --------------------------------------------------------------------------- project

def project_doc(sha):
    p = {"slug": "board", "name": "board", "path": "hw/board", "status": "modified",
         "summary": {"components": {"added": 1, "removed": 0, "moved": 3, "changed": 1, "minor": 9},
                     "erc": {"new": 0, "fixed": 0}, "drc": {"new": 2, "fixed": 1}, "grid": {"count": 1}},
         "errors": []}
    return {"head": {"sha": sha, "short": sha[:7]}, "projects": [p], "errors": []}


@pytest.fixture
def run_project(tmp_path, monkeypatch):
    def run(gh, sha, *extra, doc=None):
        f = tmp_path / "project-review.json"
        f.write_text(json.dumps(doc or project_doc(sha)))
        monkeypatch.setattr(post_comment.GitHub, "from_env", classmethod(lambda cls, read_only=False: gh))
        return post_comment.main(["--data", str(f), "--repo", REPO, "--pr", "3", "--head-sha", sha,
                                  "--run-url", "https://github.com/o/r/actions/runs/9", *extra])
    return run


def test_project_first_run_new_sha_rerun(run_project):
    gh = FakeGitHub()
    human = gh.add("nice board " + ping.ping_marker("project"), HUMAN)      # copied marker, not ours
    run_project(gh, SHA1)
    assert len(gh.comments) == 2 and gh.pings("project") == [human]      # first run: sticky only
    sticky = gh.comments[1]
    assert ping.reported_sha(sticky["body"]) == SHA1

    run_project(gh, SHA2)                                                # new commit: ping
    assert ping.reported_sha(sticky["body"]) == SHA2
    first = gh.comments[-1]
    assert first["body"] == (ping.ping_marker("project") + "\n🔁 KiCad review updated for `2222222`: "
                             "2 new / 1 fixed DRC, 0 ERC, 5 components changed, 1 off-grid · "
                             f"[results]({sticky['html_url']}) · [run](https://github.com/o/r/actions/runs/9)\n")

    gh.calls.clear()
    run_project(gh, SHA2)                                                # re-run of the same commit
    assert gh.comments[-1] is first and not any(c[0] in ("POST", "DELETE", "GRAPHQL") for c in gh.calls)

    run_project(gh, SHA1)                                                # another push: old ping minimized
    second = gh.comments[-1]
    assert second is not first and gh.live_pings("project") == [human, second]
    assert gh.minimized == {first["node_id"]}
    run_project(gh, SHA2)
    assert gh.minimized == {first["node_id"], second["node_id"]}
    assert sum(1 for c in gh.calls if c == ("GRAPHQL", "mutation")) == 2   # already-minimized ones skipped
    assert human["body"] == "nice board " + ping.ping_marker("project")


def test_project_opt_out_and_escaping(run_project):
    gh = FakeGitHub()
    run_project(gh, SHA1)
    run_project(gh, SHA2, "--no-ping")
    assert gh.pings("project") == []
    gh = FakeGitHub()
    run_project(gh, SHA1)
    run_project(gh, SHA2, doc={"projects": [{"summary": {"drc": {"new": "<b>9</b>"}, "components": {"added": True}}}]})
    line = gh.pings("project")[0]["body"]
    assert "0 DRC, 0 components changed" in line and "<b>" not in line


def test_project_minimize_failure_deletes(run_project):
    gh = FakeGitHub(minimize_error=True)
    other = gh.add("unrelated bot comment")
    for sha in (SHA1, SHA2, SHA1):
        run_project(gh, sha)
    assert len(gh.pings("project")) == 1 and gh.minimized == set()
    assert other in gh.comments
    assert sum(1 for c in gh.calls if c[0] == "DELETE") == 1


def test_project_dry_run_shows_ping(run_project, capsys):
    gh = FakeGitHub()
    run_project(gh, SHA1)
    run_project(gh, SHA2, "--dry-run")
    out = capsys.readouterr().out
    assert "===== ping =====\n🔁 KiCad review updated for `2222222`" in out and gh.pings("project") == []


# --------------------------------------------------------------------------- library

@pytest.fixture
def run_library(tmp_path, monkeypatch):
    make_mock.main(tmp_path / "mock")
    site = tmp_path / "mock" / "site"

    def run(gh, sha, *extra):
        monkeypatch.setattr(post_review.GitHub, "from_env", classmethod(lambda cls, read_only=False: gh))
        return post_review.main(["--site", str(site), "--repo", REPO, "--pr", "3", "--head-sha", sha,
                                 "--no-check", "--pages-url", "https://o.github.io/r/", *extra])
    return run


def test_library_ping(run_library):
    gh = FakeGitHub()
    human = gh.add("LGTM", HUMAN)
    run_library(gh, SHA1)
    assert gh.pings("library") == []
    sticky = gh.comments[1]
    run_library(gh, SHA2)
    first = gh.pings("library")[0]
    line = first["body"].splitlines()[1]
    assert re.fullmatch(r"🔁 Component review updated for `2222222`: \d+ items: \d+ fail, \d+ warn, \d+ pass"
                        r"(, \d+ not reviewed)?" + re.escape(f" · [results]({sticky['html_url']})"
                                                             " · [viewer](https://o.github.io/r/pr/3/)"), line), line
    run_library(gh, SHA2)
    assert gh.pings("library") == [first]
    run_library(gh, SHA1)
    assert gh.minimized == {first["node_id"]} and len(gh.live_pings("library")) == 1
    assert human["body"] == "LGTM"
    run_library(gh, SHA2, "--no-ping")
    assert len(gh.pings("library")) == 2


def test_library_minimize_failure_deletes(run_library):
    gh = FakeGitHub(minimize_error=True)
    for sha in (SHA1, SHA2, SHA1, SHA2):
        run_library(gh, sha)
    assert len(gh.pings("library")) == 1


def test_ping_kinds_are_separate():
    gh = FakeGitHub()
    lib = gh.add(ping.ping_marker("library") + "\nold")
    ping.post(gh, REPO, 3, "project", "x")
    ping.post(gh, REPO, 3, "project", "y")
    assert lib["node_id"] not in gh.minimized and len(gh.minimized) == 1


def test_project_ping_for_failed_run_without_data(tmp_path, run_project, monkeypatch):
    gh = FakeGitHub()
    run_project(gh, SHA1)
    monkeypatch.setattr(post_comment.GitHub, "from_env", classmethod(lambda cls, read_only=False: gh))
    post_comment.main(["--data", str(tmp_path / "missing.json"), "--repo", REPO, "--pr", "3", "--head-sha", SHA2,
                       "--note", "The review run failed"])
    assert "updated for `2222222`: no results (⚠️ see the run)" in gh.pings("project")[0]["body"]
