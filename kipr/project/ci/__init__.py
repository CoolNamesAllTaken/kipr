"""GitHub glue for the project review: `kipr project ci <tool> ...` (each tool has --help)."""
from __future__ import annotations

import importlib
import sys

TOOLS = {
    "job-summary": ("kipr.project.ci.job_summary", "job summary + annotations for new ERC/DRC violations"),
    "limit-size": ("kipr.project.ci.limit_size", "drop optional exports until OUT fits a size budget"),
    "pr-meta": ("kipr.project.ci.pr_meta", "write pr-meta/pr.json in the unprivileged job"),
    "resolve-pr": ("kipr.library.ci.resolve_pr", "verify the PR of a workflow_run against the API"),
    "make-comment": ("kipr.project.ci.make_comment", "print the sticky comment markdown"),
    "post-comment": ("kipr.project.ci.post_comment", "create/update the sticky PR comment"),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: kipr project ci {" + ",".join(TOOLS) + "} ...\n")
        for name, (_mod, what) in TOOLS.items():
            print(f"  {name:13} {what}")
        return 0 if argv else 2
    tool, rest = argv[0], argv[1:]
    if tool not in TOOLS:
        print(f"kipr project ci: unknown tool {tool!r}", file=sys.stderr)
        return 2
    return importlib.import_module(TOOLS[tool][0]).main(rest) or 0
