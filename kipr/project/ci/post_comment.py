"""Create or update the sticky "KiCad project review" comment on a pull request.

    kipr project ci post-comment --data project-review.json --repo owner/repo --pr N
        --head-sha SHA [--run-url URL] [--site-url URL] [--report-url URL] [--data-url URL]
        [--note TEXT] [--comment-out FILE] [--dry-run]

Runs in the privileged publish job: project-review.json is untrusted data from the PR run
(escaped by make_comment). The comment is found by its hidden `<!-- kipr-project-review -->`
marker among comments by CR_BOT_LOGIN (default github-actions[bot]). When no project changed
and there is no comment yet, nothing is posted. --dry-run only makes GET requests.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kipr.library.ci.post_review import own_comments

from .common import MARKER, GitHub, check_repo, check_sha, load_review, log, parse_pr_number
from .make_comment import build_comment


def find_sticky(gh: GitHub, repo: str, pr: int) -> dict | None:
    for c in own_comments(gh, f"/repos/{repo}/issues/{pr}/comments"):
        if MARKER in (c.get("body") or ""):
            return c
    return None


def publish(gh: GitHub, repo: str, pr: int, body: str, has_projects: bool, dry_run: bool = False) -> str:
    """-> "created" | "updated" | "skipped" (what was, or with dry_run would be, done)."""
    existing = find_sticky(gh, repo, pr) if gh.token else None
    if existing is None and not has_projects:
        return "skipped"
    if dry_run:
        return "updated" if existing else "created"
    if existing:
        gh.request("PATCH", f"/repos/{repo}/issues/comments/{existing['id']}", {"body": body})
        log(f"updated sticky comment {existing.get('html_url')}")
        return "updated"
    c, _ = gh.request("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})
    log(f"created sticky comment {(c or {}).get('html_url')}")
    return "created"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kipr project ci post-comment", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head-sha", required=True)
    for opt in ("--run-url", "--site-url", "--report-url", "--data-url", "--note"):
        ap.add_argument(opt)
    ap.add_argument("--comment-out", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    repo, pr, head = check_repo(a.repo), parse_pr_number(a.pr), check_sha(a.head_sha)
    note = a.note
    try:
        doc = load_review(a.data)
    except ValueError as e:
        log(f"post-comment: {e}")
        doc = {"projects": [], "errors": ["project-review.json missing or unreadable; see the workflow run"]}
    body = build_comment(doc, a.run_url, a.site_url, a.report_url, a.data_url, head, note)
    if a.comment_out:
        a.comment_out.write_text(body, encoding="utf-8")
    gh = GitHub.from_env(read_only=a.dry_run)
    what = publish(gh, repo, pr, body, bool(doc["projects"]), a.dry_run)
    if a.dry_run:
        print(f"===== sticky comment ({len(body)} chars), would be {what} =====")
        print(body)
    else:
        log(f"post-comment: {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
