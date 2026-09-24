"""Write pr-meta/pr.json for the publish workflow (unprivileged job).

    kipr project ci pr-meta --pr N --head-sha SHA --base-sha SHA --merge-base SHA --out DIR

The publish job treats this file as untrusted and re-checks it against the API
(`kipr project ci resolve-pr`, the same checks as the library review).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import check_sha, parse_pr_number


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kipr project ci pr-meta", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--merge-base", required=True)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    meta = {"pr": parse_pr_number(a.pr), "head_sha": check_sha(a.head_sha), "base_sha": check_sha(a.base_sha),
            "merge_base": check_sha(a.merge_base)}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "pr.json").write_text(json.dumps(meta), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
