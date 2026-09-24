"""kipr command line: `kipr library ...` reviews symbol/footprint/3D-model libraries,
`kipr project ...` reviews KiCad projects (schematic, layout, 3D PCBA)."""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kipr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    # `kipr library ...` has its own parser (kipr.library.cli), so pass everything after it through
    sub.add_parser("library", help="review changed symbols, footprints and 3D models", add_help=False)
    pp = sub.add_parser("project", help="review changed KiCad projects")
    pp.add_argument("--repo", default=".", help="git repository (default: .)")
    pp.add_argument("--base", required=True, help="base commit/ref")
    pp.add_argument("--head", default="HEAD", help="head commit/ref (default: HEAD)")
    pp.add_argument("--out", required=True, help="output directory (project-review.json + exports)")
    pp.add_argument("--projects", action="append", metavar="GLOB",
                    help="only projects whose directory (or its name) matches; repeatable")
    pp.add_argument("--kicad-cli", help="kicad-cli executable (default: $KIPR_KICAD_CLI, then PATH)")
    pp.add_argument("--no-export", action="store_true", help="semantic diffs only, don't run kicad-cli")
    pp.add_argument("--step", action="store_true", help="also export STEP models of the boards")
    pp.add_argument("--no-glb", action="store_true", help="skip the GLB (3D PCBA) export")
    pp.add_argument("--fast-checks", action="store_true",
                    help="run ERC/DRC without the global KiCad libraries (much faster; library "
                         "mismatch checks are skipped)")
    pp.add_argument("--jobs", type=int, default=4, help="parallel kicad-cli processes (default: 4)")
    pp.add_argument("--cache-dir", help="export cache (default: $KIPR_CACHE_DIR or ~/.cache/kipr)")
    pp.add_argument("--repo-url", help="https URL of the repo for source links (default: origin if GitHub)")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["library"]:
        from kipr.library.cli import main as library_main
        return library_main(argv[1:])
    args = parser.parse_args(argv)
    if args.command == "project":
        from kipr.project.review import run
        doc = run(args.repo, args.base, args.head, args.out, patterns=args.projects, kicad_cli=args.kicad_cli,
                  jobs=args.jobs, cache_dir=args.cache_dir, step=args.step, glb=not args.no_glb,
                  repo_url=args.repo_url, no_export=args.no_export, fast_checks=args.fast_checks)
        n_err = sum(len(p.get("errors") or []) for p in doc["projects"]) + len(doc["errors"])
        print(f"wrote {args.out}/project-review.json: {len(doc['projects'])} project(s), {n_err} error(s)")
        return 0
    parser.error(f"'{args.command}' is not implemented yet")


if __name__ == "__main__":
    sys.exit(main())
