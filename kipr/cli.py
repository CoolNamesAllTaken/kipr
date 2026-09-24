"""kipr command line: `kipr library ...` reviews symbol/footprint/3D-model libraries,
`kipr project ...` reviews KiCad projects (schematic, layout, 3D PCBA)."""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kipr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    # `kipr library ...` has its own parser (kipr.library.cli), so pass everything after it through
    sub.add_parser("library", help="review changed symbols, footprints and 3D models", add_help=False)
    sub.add_parser("project", help="review changed KiCad projects")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["library"]:
        from kipr.library.cli import main as library_main
        return library_main(argv[1:])
    args = parser.parse_args(argv)
    parser.error(f"'{args.command}' is not implemented yet")


if __name__ == "__main__":
    sys.exit(main())
