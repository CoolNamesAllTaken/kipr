"""kipr command line: `kipr library ...` reviews symbol/footprint/3D-model libraries,
`kipr project ...` reviews KiCad projects (schematic, layout, 3D PCBA)."""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kipr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("library", help="review changed symbols, footprints and 3D models")
    sub.add_parser("project", help="review changed KiCad projects")
    args = parser.parse_args(argv)
    parser.error(f"'{args.command}' is not implemented yet")


if __name__ == "__main__":
    sys.exit(main())
