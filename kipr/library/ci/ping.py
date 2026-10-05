"""Short "review updated" ping at the bottom of a PR, next to the sticky comment.

The sticky comment is edited in place and stays near the top of the conversation, so a new push
goes unnoticed. After an update for a NEW head sha, one short comment (hidden
`<!-- kipr-ping:<kind> -->` marker) is posted, and our earlier pings of that kind are minimized
as OUTDATED (deleted if minimizing fails). No ping when the sticky was just created (it already
is the latest comment) or when it already reported this sha (a re-run). Only comments by
CR_BOT_LOGIN carrying the marker are touched.
"""
from __future__ import annotations

import re

from .common import GitHub, log

SHA_MARKER_RE = re.compile(r"<!-- kipr-sha:([0-9a-f]{40}) -->")

MINIMIZED_QUERY = """
query($ids: [ID!]!) { nodes(ids: $ids) { ... on IssueComment { id isMinimized } } }"""

MINIMIZE_MUTATION = """
mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) { minimizedComment { isMinimized } } }"""


def sha_marker(sha: str) -> str:
    """Hidden line in the sticky comment: the head sha it reports (sha already validated)."""
    return f"<!-- kipr-sha:{sha} -->"


def reported_sha(body: str | None) -> str | None:
    m = SHA_MARKER_RE.search(body or "")
    return m.group(1) if m else None


def ping_marker(kind: str) -> str:
    return f"<!-- kipr-ping:{kind} -->"


def wanted(enabled: bool, previous: dict | None, head_sha: str) -> bool:
    """Ping after this update? `previous` is the sticky comment before it (None: just created)."""
    return enabled and previous is not None and reported_sha(previous.get("body")) != head_sha


def post(gh: GitHub, repo: str, pr: int, kind: str, line: str) -> dict | None:
    """Post the ping, then retire our earlier pings of this kind. Never raises."""
    from .post_review import own_comments   # lazy: post_review imports this module
    marker = ping_marker(kind)
    try:
        old = [c for c in own_comments(gh, f"/repos/{repo}/issues/{pr}/comments") if marker in (c.get("body") or "")]
        new, _ = gh.request("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": f"{marker}\n{line}\n"})
    except RuntimeError as e:
        log(f"warning: ping not posted: {e}")
        return None
    log(f"posted ping {(new or {}).get('html_url')}")
    retire(gh, repo, [c for c in old if c.get("id") != (new or {}).get("id")])
    return new


def retire(gh: GitHub, repo: str, old: list[dict]) -> None:
    ids = [c["node_id"] for c in old if isinstance(c.get("node_id"), str)]
    done: set[str] = set()
    if ids:
        try:
            nodes = gh.graphql(MINIMIZED_QUERY, {"ids": ids}).get("nodes") or []
            done = {n["id"] for n in nodes if isinstance(n, dict) and n.get("isMinimized")}
        except RuntimeError as e:
            log(f"warning: cannot look up minimized pings: {e}")
    for c in old:
        nid = c.get("node_id")
        if nid in done:
            continue
        if isinstance(nid, str):
            try:
                gh.graphql(MINIMIZE_MUTATION, {"id": nid})
                continue
            except RuntimeError as e:
                log(f"warning: cannot minimize ping {c.get('id')}, deleting it: {e}")
        try:
            gh.request("DELETE", f"/repos/{repo}/issues/comments/{c['id']}")
        except RuntimeError as e:
            log(f"warning: cannot delete ping {c.get('id')}: {e}")


def link(label: str, url: str | None) -> str:
    """` · [label](url)` for a URL already passed through safe_http_url, else ""."""
    return f" · [{label}]({url})" if url else ""
