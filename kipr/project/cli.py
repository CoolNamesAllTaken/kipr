"""kipr project: review the KiCad projects changed between two commits.

    kipr project --repo . --base <sha> --head <sha> --out OUT      review + site + report
    kipr project review|site|report ...                             one stage
    kipr project ci <tool> ...                                      GitHub glue (see `kipr project ci -h`)

The end-to-end run writes OUT/project-review.json and the exports under OUT/p/<slug>/ (review:
kicad-cli exports + semantic schematic/layout/BOM/netlist/ERC/DRC diffs), the interactive viewer
(OUT/index.html; opens from disk too) and the self-contained HTML report
(OUT/project-review.html, or --report FILE).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

STAGES = ("review", "site", "report")


def log(msg: str) -> None:
    print(f"[kipr project] {msg}", file=sys.stderr, flush=True)


def add_review_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--repo", default=".", help="git repository (default: .)")
    ap.add_argument("--base", required=True, help="base commit/ref")
    ap.add_argument("--head", default="HEAD", help="head commit/ref (default: HEAD)")
    ap.add_argument("--out", required=True, type=Path, help="output directory (project-review.json + exports)")
    ap.add_argument("--projects", action="append", metavar="GLOB",
                    help="only projects whose directory (or its name) matches; repeatable")
    ap.add_argument("--kicad-cli", help="kicad-cli executable (default: $KIPR_KICAD_CLI, then PATH)")
    ap.add_argument("--no-export", action="store_true", help="semantic diffs only, don't run kicad-cli")
    ap.add_argument("--step", action="store_true", help="also export STEP models of the boards")
    ap.add_argument("--no-glb", action="store_true", help="skip the GLB (3D PCBA) export")
    ap.add_argument("--fast-checks", action="store_true",
                    help="run ERC/DRC without the global KiCad libraries (much faster; library "
                         "mismatch checks are skipped)")
    ap.add_argument("--jobs", type=int, default=4, help="parallel kicad-cli processes (default: 4)")
    ap.add_argument("--cache-dir", help="export cache (default: $KIPR_CACHE_DIR or ~/.cache/kipr)")
    ap.add_argument("--repo-url", help="https URL of the repo for source links (default: origin if GitHub)")
    ap.add_argument("--significant-fields", metavar="PATTERNS",
                    help="symbol/footprint fields whose changes count as real changes: comma-separated, "
                         "case-insensitive globs over the field name without spaces/_/-/. (default: part "
                         "numbers: MPN, manufacturer, LCSC, Digi-Key, Mouser, ... see docs/project.md); a "
                         "leading + adds to the defaults. Other field changes are listed as minor.")


def run_review(args) -> int:
    from .review import run
    doc = run(args.repo, args.base, args.head, str(args.out), patterns=args.projects, kicad_cli=args.kicad_cli,
              jobs=args.jobs, cache_dir=args.cache_dir, step=args.step, glb=not args.no_glb,
              repo_url=args.repo_url, no_export=args.no_export, fast_checks=args.fast_checks,
              significant_fields=args.significant_fields)
    n_err = sum(len(p.get("errors") or []) for p in doc["projects"]) + len(doc["errors"])
    print(f"wrote {args.out}/project-review.json: {len(doc['projects'])} project(s), {n_err} error(s)")
    return 0


def review_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="kipr project review",
                                 description="kicad-cli exports + semantic diffs -> OUT/project-review.json")
    add_review_arguments(ap)
    return run_review(ap.parse_args(argv))


def run_all_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kipr project", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_review_arguments(ap)
    g = ap.add_argument_group("site and report")
    g.add_argument("--no-offline", action="store_true", help="viewer without the file:// support files")
    g.add_argument("--report", type=Path, default=None, help="HTML report file (default OUT/project-review.html)")
    g.add_argument("--max-mb", type=float, default=25.0, help="report size cap in MB (default 25)")
    g.add_argument("--skip", action="append", choices=STAGES[1:], default=[],
                   help="skip a stage after review (repeatable)")
    return ap


def run_all(argv) -> int:
    """review, then site and report. A failing later stage doesn't stop the others; the exit
    status is 1 if any stage failed."""
    args = run_all_parser().parse_args(argv)
    from . import report, site

    rc = run_review(args)
    if rc:
        log(f"review failed (exit {rc})")
        return rc
    failed = []
    if "site" not in args.skip:
        if site.main(["--out", str(args.out)] + (["--no-offline"] if args.no_offline else [])):
            failed.append("site")
    if "report" not in args.skip:
        if report.main(["--out", str(args.out), "--max-mb", str(args.max_mb)]
                       + (["--output", str(args.report)] if args.report else [])):
            failed.append("report")
    if failed:
        log("failed: " + ", ".join(failed))
        return 1
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "ci":
        from .ci import main as ci_main
        return ci_main(argv[1:])
    if argv and argv[0] == "review":
        return review_main(argv[1:])
    if argv and argv[0] == "site":
        from . import site
        return site.main(argv[1:])
    if argv and argv[0] == "report":
        from . import report
        return report.main(argv[1:])
    if argv and argv[0] == "run":
        argv = argv[1:]
    return run_all(argv)


if __name__ == "__main__":
    sys.exit(main())
