"""kipr library: review the symbols, footprints and 3D models changed between two commits.

    kipr library --repo . --base <sha> --head <sha> --out OUT      render + checks + site + report
    kipr library render|checks|site|report ...                      one stage
    kipr library ci <tool> ...                                      GitHub glue (see `kipr library ci -h`)

The end-to-end run writes OUT/manifest.json, OUT/items/, OUT/review.json, OUT/review.md, the
interactive viewer (OUT/index.html; opens from disk too) and the self-contained HTML report
(OUT/component-review.html, or --report FILE).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

STAGES = ("render", "checks", "site", "report")
CI_TOOLS = {
    "job-summary": "job_summary",
    "sanitize-site": "sanitize_site",
    "resolve-pr": "resolve_pr",
    "deploy-pages": "deploy_pages",
    "make-comment": "make_comment",
    "post-review": "post_review",
}
CI_DIR = Path(__file__).resolve().parent / "ci"


def log(msg: str) -> None:
    print(f"[kipr library] {msg}", file=sys.stderr, flush=True)


def _stage_module(name: str):
    if name == "render":
        from .render import main as m
    elif name == "checks":
        from .checks import main as m
    elif name == "site":
        from . import site as m
    else:
        from . import report as m
    return m


def run_all_parser() -> argparse.ArgumentParser:
    from .render import main as render
    ap = argparse.ArgumentParser(prog="kipr library", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    render.add_arguments(ap)  # --repo --base --head --out --pr --lib-* ... (the render stage's options)
    g = ap.add_argument_group("checks, site and report")
    g.add_argument("--klc-utils", default=os.environ.get("CR_KLC_UTILS"),
                   help="kicad-library-utils checkout: also run KiCad's KLC checkers "
                        "(see `kipr library ci fetch-klc-utils`)")
    g.add_argument("--site-url", default=os.environ.get("CR_SITE_URL"), help="viewer URL to link from review.md")
    g.add_argument("--no-offline", action="store_true", help="viewer without the file:// support files")
    g.add_argument("--report", type=Path, default=None,
                   help="HTML report file (default OUT/component-review.html)")
    g.add_argument("--max-mb", type=float, default=20.0, help="report size cap in MB (default 20)")
    g.add_argument("--server-url", default="https://github.com", help="GitHub server for source links")
    g.add_argument("--skip", action="append", choices=STAGES[1:], default=[],
                   help="skip a stage after render (repeatable)")
    return ap


def run_all(argv) -> int:
    """render, then checks, site and report. A failing later stage doesn't stop the others;
    the exit status is 1 if any stage failed."""
    args = run_all_parser().parse_args(argv)
    from .checks import main as checks
    from . import report, site
    from .render import main as render

    rc = render.run(args)
    if rc:
        log(f"render failed (exit {rc})")
        return rc
    out = Path(args.out)
    failed = []
    if "checks" not in args.skip:
        a = checks.parse_args(["--out", str(out), "--lib-3d", args.lib_3d]
                              + (["--klc-utils", args.klc_utils] if args.klc_utils else [])
                              + (["--site-url", args.site_url] if args.site_url else []))
        if checks.run(a):
            failed.append("checks")
    if "site" not in args.skip:
        if site.run(argparse.Namespace(out=out, no_offline=args.no_offline)):
            failed.append("site")
    if "report" not in args.skip:
        if report.run(argparse.Namespace(out=out, output=args.report, max_mb=args.max_mb,
                                         server_url=args.server_url)):
            failed.append("report")
    if failed:
        log("failed: " + ", ".join(failed))
        return 1
    return 0


def ci_main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: kipr library ci {" + ",".join([*CI_TOOLS, "fetch-klc-utils"]) + "} ...\n\n"
              "GitHub glue for the library review workflows (docs/library.md):\n"
              "  job-summary      annotations + job summary from OUT\n"
              "  sanitize-site    copy an untrusted review artifact into a clean site dir\n"
              "  resolve-pr       verify the PR number of a workflow_run against the API\n"
              "  deploy-pages     publish/remove pr/<N>/ on gh-pages\n"
              "  make-comment     print the sticky PR comment markdown\n"
              "  post-review      sticky comment + inline review + check run\n"
              "  fetch-klc-utils  DIR: shallow-fetch kicad-library-utils at the pinned commit, print DIR")
        return 0 if argv else 2
    tool, rest = argv[0], argv[1:]
    if tool == "fetch-klc-utils":
        return subprocess.call(["bash", str(CI_DIR / "fetch_klc_utils.sh"), *rest])
    if tool not in CI_TOOLS:
        print(f"kipr library ci: unknown tool {tool!r}", file=sys.stderr)
        return 2
    import importlib
    return importlib.import_module(f".ci.{CI_TOOLS[tool]}", __package__).main(rest)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in STAGES:
        return _stage_module(argv[0]).main(argv[1:])
    if argv and argv[0] == "ci":
        return ci_main(argv[1:])
    if argv and argv[0] == "run":
        argv = argv[1:]
    return run_all(argv)


if __name__ == "__main__":
    sys.exit(main())
