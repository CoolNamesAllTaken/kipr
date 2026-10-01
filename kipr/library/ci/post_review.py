"""Publish component-review results to a pull request.

  1. inline review (event COMMENT) on .kicad_mod/.kicad_sym lines for error/warning findings
     whose line is inside the PR diff; findings already posted earlier (hidden
     `<!-- cr-finding:<key> -->` marker in our own review comments) are not posted again;
     our earlier inline comments whose finding is no longer reported (fixed, item re-encoded
     or no longer reviewed) get a "no longer reported" note on top, the original text folded
     into <details>, and their review thread resolved; never deleted, humans' comments untouched;
  2. ONE sticky issue comment (hidden `<!-- component-review -->` marker), created or updated;
  3. a check run "Component review" on the head sha with the overall verdict.

--dry-run only issues GET requests (it still reads the PR's file list and existing comments
if a token / `gh auth` login is available; use --files-json to work fully offline) and prints
the comment markdown and the review/check payloads.

Usage:
  kipr library ci post-review --site SITE --repo owner/repo --pr N --head-sha SHA [--pages-sha SHA]
                 [--pages-url URL] [--artifact-url URL] [--run-url URL] [--note TEXT]
                 [--fail-conclusion neutral|failure|success] [--no-check] [--dry-run]
                 [--files-json FILE] [--comment-out FILE]
  kipr library ci post-review --mark-closed --repo owner/repo --pr N      # after the preview was deleted
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .common import (FINDING_MARKER_RE, MARKER, GitHub, check_repo, check_sha, findings_of,
                     item_review, load_site, log, md_block, md_inline, overall_verdict,
                     parse_pr_number, safe_repo_path)
from .make_comment import Ctx, build_comment, default_pages_url, finding_key, pr_findings_of

BOT_LOGIN = os.environ.get("CR_BOT_LOGIN", "github-actions[bot]")
INLINE_EXT = (".kicad_mod", ".kicad_sym")
MAX_INLINE = 40
CHECK_NAME = "Component review"
STALE_MARKER = "<!-- cr-stale -->"
CLOSED_BANNER = "> [!NOTE]\n> This PR is closed, so its live preview was removed. Images below still work.\n"


# --------------------------------------------------------------------------- diff lines

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def right_lines(patch: str) -> set[int]:
    """Line numbers on the RIGHT (head) side that a review comment may target."""
    lines: set[int] = set()
    new = None
    for ln in patch.splitlines():
        m = HUNK_RE.match(ln)
        if m:
            new = int(m.group(1))
            continue
        if new is None or ln.startswith("\\"):
            continue
        if ln.startswith("-"):
            continue
        lines.add(new)          # '+' or context line
        new += 1
    return lines


def commentable(files: list[dict]) -> dict[str, object]:
    """path -> set of RIGHT-side line numbers a review comment may target."""
    out: dict[str, object] = {}
    for f in files:
        name = f.get("filename")
        if not isinstance(name, str):
            continue
        if f.get("patch"):
            out[name] = right_lines(f["patch"])
        elif f.get("status") == "added" and isinstance(f.get("additions"), int) and f["additions"] > 0:
            # GitHub omits the patch of big files; every line of an added file is in the diff
            out[name] = set(range(1, f["additions"] + 1))
    return out


# --------------------------------------------------------------------------- building payloads

def inline_candidates(manifest, review, allowed: dict[str, object]):
    """(inline, rest): findings that can be inline comments, and the others."""
    inline, rest = [], []
    for item in manifest["items"]:
        for f in findings_of(item_review(review, item.get("id"))):
            path = safe_repo_path(f.get("path")) or safe_repo_path(item.get("path"))
            line = f.get("line")
            ok = (f.get("severity") in ("error", "warning") and path and path.endswith(INLINE_EXT)
                  and isinstance(line, int) and not isinstance(line, bool) and line > 0
                  and line in allowed.get(path, ()))
            (inline if ok else rest).append((item, f, path))
    return inline, rest


def inline_body(item: dict, f: dict) -> str:
    icon = {"error": "🔴", "warning": "🟠"}.get(f.get("severity"), "🔵")
    lines = [f"{icon} **Component review · {md_inline(f.get('severity'), 10)}** · "
             f"{md_inline(f.get('category'), 30)} · `{md_inline(item.get('name'), 80).replace('`', '')}`",
             "", md_block(f.get("message"), 1500)]
    if f.get("suggestion"):
        lines += ["", f"_Suggestion:_ {md_block(f.get('suggestion'), 800)}"]
    lines += ["", f"<!-- cr-finding:{finding_key(f)} -->"]
    return "\n".join(lines)


def build_review_payload(head_sha: str, inline, already: set[str]) -> dict | None:
    comments, seen = [], set()
    for item, f, path in inline:
        key = finding_key(f)
        if key in already or key in seen:
            continue
        seen.add(key)
        comments.append({"path": path, "line": f["line"], "side": "RIGHT", "body": inline_body(item, f)})
        if len(comments) >= MAX_INLINE:
            break
    if not comments:
        return None
    return {"commit_id": head_sha, "event": "COMMENT", "comments": comments,
            "body": f"Component review: {len(comments)} new finding(s) on changed lines. "
                    "The full report is in the component-review comment on this PR."}


def check_payload(head_sha: str, manifest, review, viewer_url: str, fail_conclusion: str) -> dict:
    ov = overall_verdict(manifest, review)
    conclusion = {"pass": "success", "warn": "neutral", "fail": fail_conclusion, None: "neutral"}[ov]
    counts = {"error": 0, "warning": 0, "info": 0}
    all_findings = [f for item in manifest["items"] for f in findings_of(item_review(review, item.get("id")))]
    all_findings += pr_findings_of(review)
    for f in all_findings:
        if f.get("severity") in counts:
            counts[f["severity"]] += 1
    title = (f"{len(manifest['items'])} component(s): verdict {ov or 'not reviewed'}; "
             f"{counts['error']} error(s), {counts['warning']} warning(s)")
    return {"name": CHECK_NAME, "head_sha": head_sha, "status": "completed", "conclusion": conclusion,
            "details_url": viewer_url,
            "output": {"title": title[:200],
                       "summary": f"[Open the viewer]({viewer_url})\n\n"
                                  + (md_block(review.get("summary_markdown"), 3000) if review else
                                     "No review was produced for this run.")}}


# --------------------------------------------------------------------------- GitHub side

def own_comments(gh: GitHub, path: str) -> list[dict]:
    return [c for c in gh.paginate(path) if (c.get("user") or {}).get("login") == BOT_LOGIN]


def find_sticky(gh: GitHub, repo: str, pr: int) -> dict | None:
    for c in own_comments(gh, f"/repos/{repo}/issues/{pr}/comments"):
        if MARKER in (c.get("body") or ""):
            return c
    return None


def finding_comments(gh: GitHub, repo: str, pr: int) -> list[dict]:
    """Our own inline review comments that carry a finding marker."""
    return [c for c in own_comments(gh, f"/repos/{repo}/pulls/{pr}/comments")
            if FINDING_MARKER_RE.search(c.get("body") or "")]


def posted_finding_keys(comments: list[dict]) -> set[str]:
    """Findings that already have a live inline comment (a stale-marked one doesn't count, so
    a finding that comes back is posted again)."""
    keys = set()
    for c in comments:
        body = c.get("body") or ""
        if STALE_MARKER not in body:
            keys.update(FINDING_MARKER_RE.findall(body))
    return keys


def current_finding_keys(manifest, review) -> set[str]:
    """Every finding of this review (any severity, inline or not): an earlier inline comment
    whose key is not in here is stale."""
    return {finding_key(f) for item in manifest["items"] for f in findings_of(item_review(review, item.get("id")))}


def stale_body(body: str, head_sha: str) -> str:
    """Our comment, marked as no longer reported; the original text folded into <details>."""
    return (f"{STALE_MARKER}\n✅ **No longer reported as of {head_sha[:7]}** (finding fixed, item re-encoded, "
            f"or no longer reviewed).\n\n<details><summary>Original comment</summary>\n\n{body.rstrip()}\n\n</details>\n")


THREADS_QUERY = """
query($owner: String!, $name: String!, $pr: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved comments(first: 1) { nodes { databaseId } } }
      }
    }
  }
}"""

RESOLVE_MUTATION = """
mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id isResolved } } }"""


def review_threads(gh: GitHub, repo: str, pr: int) -> dict[int, dict]:
    """databaseId of a thread's first comment -> {"id", "isResolved"}."""
    owner, name = repo.split("/", 1)
    out: dict[int, dict] = {}
    after = None
    for _ in range(30):
        data = gh.graphql(THREADS_QUERY, {"owner": owner, "name": name, "pr": pr, "after": after})
        threads = (((data.get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {})
        for t in threads.get("nodes") or []:
            first = ((t.get("comments") or {}).get("nodes") or [{}])[0] or {}
            if isinstance(first.get("databaseId"), int) and t.get("id"):
                out[first["databaseId"]] = {"id": t["id"], "isResolved": bool(t.get("isResolved"))}
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    return out


def stale_comments(comments: list[dict], current: set[str]) -> list[dict]:
    """Our finding comments not yet marked stale whose finding is not in `current`."""
    return [c for c in comments if STALE_MARKER not in (c.get("body") or "")
            and not set(FINDING_MARKER_RE.findall(c.get("body") or "")) & current]


def retire_stale(gh: GitHub, repo: str, pr: int, comments: list[dict], current: set[str], head_sha: str) -> int:
    """Mark our inline comments whose finding is gone and resolve their threads. Idempotent:
    a comment already marked is left alone. Returns the number of comments marked now."""
    stale = stale_comments(comments, current)
    if not stale:
        return 0
    try:
        threads = review_threads(gh, repo, pr)
    except RuntimeError as e:
        log(f"warning: cannot list review threads, stale comments are edited but not resolved: {e}")
        threads = {}
    n = 0
    for c in stale:
        try:
            gh.request("PATCH", f"/repos/{repo}/pulls/comments/{c['id']}", {"body": stale_body(c["body"], head_sha)})
            n += 1
        except RuntimeError as e:
            log(f"warning: cannot mark stale comment {c.get('id')}: {e}")
            continue
        t = threads.get(c["id"])
        if t and not t["isResolved"]:
            try:
                gh.graphql(RESOLVE_MUTATION, {"id": t["id"]})
            except RuntimeError as e:
                log(f"warning: cannot resolve thread of comment {c.get('id')} (edited only): {e}")
    log(f"marked {n} inline comment(s) as no longer reported")
    return n


def upsert_sticky(gh: GitHub, repo: str, pr: int, body: str) -> None:
    existing = find_sticky(gh, repo, pr)
    if existing:
        gh.request("PATCH", f"/repos/{repo}/issues/comments/{existing['id']}", {"body": body})
        log(f"updated sticky comment {existing.get('html_url')}")
    else:
        c, _ = gh.request("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})
        log(f"created sticky comment {c.get('html_url')}")


def mark_closed(gh: GitHub, repo: str, pr: int) -> None:
    existing = find_sticky(gh, repo, pr)
    if not existing or CLOSED_BANNER in existing.get("body", ""):
        return
    body = existing["body"].replace(MARKER + "\n", MARKER + "\n" + CLOSED_BANNER + "\n", 1)
    gh.request("PATCH", f"/repos/{repo}/issues/comments/{existing['id']}", {"body": body})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kipr library ci post-review", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", type=Path)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head-sha")
    ap.add_argument("--pages-url")
    ap.add_argument("--pages-sha")
    ap.add_argument("--artifact-url", help="the component-review-site artifact (zipped viewer)")
    ap.add_argument("--report-url", help="the component-review.html artifact (single HTML file)")
    ap.add_argument("--run-url")
    ap.add_argument("--note")
    ap.add_argument("--fail-conclusion", default=os.environ.get("CR_FAIL_CONCLUSION") or "neutral",
                    choices=["neutral", "failure", "success"])
    ap.add_argument("--no-check", action="store_true", help="don't create a check run")
    ap.add_argument("--no-inline", action="store_true", help="don't submit an inline review")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--files-json", type=Path, help="offline: PR files list (GET /pulls/N/files) as JSON")
    ap.add_argument("--comment-out", type=Path, help="also write the comment markdown here")
    ap.add_argument("--mark-closed", action="store_true")
    a = ap.parse_args(argv)

    repo = check_repo(a.repo)
    pr = parse_pr_number(a.pr)
    gh = GitHub.from_env(read_only=a.dry_run)
    if a.mark_closed:
        mark_closed(gh, repo, pr)
        return 0
    if not a.site or not a.head_sha:
        ap.error("--site and --head-sha are required")
    head_sha = check_sha(a.head_sha)
    pages_url = a.pages_url or default_pages_url(repo)
    ctx = Ctx(a.site, repo, pr, head_sha, pages_url, check_sha(a.pages_sha) if a.pages_sha else None,
              os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    manifest, review = load_site(a.site)

    online = bool(gh.token) and not a.files_json
    if a.files_json:
        files = json.loads(a.files_json.read_text())
    elif online:
        files = gh.paginate(f"/repos/{repo}/pulls/{pr}/files")
    else:
        log("no token and no --files-json: inline comments disabled")
        files = []
    already, posted = set(), None
    if online:
        try:
            posted = finding_comments(gh, repo, pr)
            already = posted_finding_keys(posted)
        except RuntimeError as e:
            log(f"warning: cannot list existing review comments: {e}")

    inline, _rest = inline_candidates(manifest, review, commentable(files))
    payload = None if a.no_inline else build_review_payload(head_sha, inline, already)
    inlined = set() if a.no_inline else {finding_key(f) for _i, f, _p in inline}
    body = build_comment(ctx, manifest, review, artifact_url=a.artifact_url, report_url=a.report_url, run_url=a.run_url,
                         note=a.note, inlined=inlined, n_inline=len(inlined))
    check = None if a.no_check else check_payload(head_sha, manifest, review, ctx.viewer, a.fail_conclusion)
    if a.comment_out:
        a.comment_out.write_text(body, encoding="utf-8")

    if a.dry_run:
        print("===== sticky comment (%d chars) =====" % len(body))
        print(body)
        print("===== review payload (POST /repos/%s/pulls/%d/reviews) =====" % (repo, pr))
        print(json.dumps(payload, indent=2, ensure_ascii=False) if payload else "(none: no new inline findings)")
        print("===== check run (POST /repos/%s/check-runs) =====" % repo)
        print(json.dumps(check, indent=2, ensure_ascii=False) if check else "(disabled)")
        print(f"===== {len(already)} finding(s) already posted inline =====")
        if posted and review and not a.no_inline:
            n = len(stale_comments(posted, current_finding_keys(manifest, review)))
            print(f"===== {n} inline comment(s) would be marked no longer reported =====")
        return 0

    if payload:
        try:
            gh.request("POST", f"/repos/{repo}/pulls/{pr}/reviews", payload)
            log(f"submitted review with {len(payload['comments'])} inline comment(s)")
        except RuntimeError as e:
            # e.g. 422 if a line fell outside the diff; never lose the sticky comment over it
            log(f"warning: inline review failed: {e}")
            inlined &= already
            body = build_comment(ctx, manifest, review, artifact_url=a.artifact_url, report_url=a.report_url, run_url=a.run_url,
                                 note=a.note, inlined=inlined, n_inline=len(inlined))
    # without a review every earlier finding would look stale; keep them as they are then
    if posted and review and not a.no_inline:
        retire_stale(gh, repo, pr, posted, current_finding_keys(manifest, review), head_sha)
    upsert_sticky(gh, repo, pr, body)
    if check:
        try:
            gh.request("POST", f"/repos/{repo}/check-runs", check)
        except RuntimeError as e:
            log(f"warning: check run failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
