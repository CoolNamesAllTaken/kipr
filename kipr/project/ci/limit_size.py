"""Keep a `kipr project` OUT dir under a size budget by dropping optional files.

    kipr project ci limit-size --out OUT --max-mb N

Removes, in this order, until the directory fits: STEP models; SVGs of unchanged PCB layers;
SVGs of unchanged schematic sheets; gerbers of unchanged layers; GLB models; then every
remaining export (only project-review.json is left). Paths of removed files are set to null in
project-review.json and a note is added to the project's `errors`, so viewers show "not
available" rather than a broken link. Writes step outputs `pruned=<n files>` and `mb=<size>`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .common import d, load_review, log, lst, write_outputs


def dir_size(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath, f)
            if not p.is_symlink():
                total += p.stat().st_size
    return total


def _slots(doc: dict):
    """(stage, container, key, project) for every file reference in the review."""
    for p in doc["projects"]:
        pcb = d(p.get("pcb"))
        for ly in lst(pcb.get("layers")):
            if not isinstance(ly, dict):
                continue
            unchanged = ly.get("status") == "unchanged"
            for side in ("base", "head"):
                s = d(ly.get(side))
                if s:
                    yield (1 if unchanged else 5), s, "svg", p
                    yield (3 if unchanged else 5), s, "gerber", p
        for sh in lst(d(p.get("schematic")).get("sheets")):
            if isinstance(sh, dict):
                for side in ("base", "head"):
                    yield (2 if sh.get("status") == "unchanged" else 5), sh, side, p
        p3d = d(p.get("pcba3d"))
        for side in ("base", "head"):
            s = d(p3d.get(side))
            if s:
                yield 0, s, "step", p
                yield 4, s, "glb", p
        for sect, key in ((pcb, "gbrjob"), (pcb, "pos"), (d(p.get("bom")), "csv"),
                          (d(p.get("netlist")), "files")):
            s = d(sect.get(key))
            for side in ("base", "head"):
                if s:
                    yield 5, s, side, p
        for kind in ("erc", "drc"):
            s = d(d(d(p.get("checks")).get(kind)).get("report"))
            for side in ("base", "head"):
                if s:
                    yield 5, s, side, p


STAGE_NAMES = ["STEP models", "SVGs of unchanged layers", "SVGs of unchanged sheets",
               "gerbers of unchanged layers", "GLB models", "all other exports"]


def limit(out: Path, max_bytes: int) -> tuple[int, int]:
    """-> (files removed, final size in bytes)."""
    size = dir_size(out)
    if size <= max_bytes:
        return 0, size
    doc = load_review(out / "project-review.json")
    root = out.resolve()
    removed = 0
    slots = sorted(_slots(doc), key=lambda t: t[0])
    for stage in range(len(STAGE_NAMES)):
        if size <= max_bytes:
            break
        n = 0
        for st, holder, key, proj in slots:
            if st != stage or not isinstance(holder.get(key), str):
                continue
            f = (out / holder[key]).resolve()
            try:
                f.relative_to(root)
            except ValueError:
                continue
            if f.is_file():
                size -= f.stat().st_size
                f.unlink()
                n += 1
            holder[key] = None
            note = f"artifact size limit: removed {STAGE_NAMES[stage]}"
            errs = proj.setdefault("errors", [])
            if isinstance(errs, list) and note not in errs:
                errs.append(note)
        if n:
            log(f"limit-size: removed {n} file(s): {STAGE_NAMES[stage]}")
        removed += n
    with open(out / "project-review.json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    return removed, dir_size(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kipr project ci limit-size", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--max-mb", required=True, type=float)
    a = ap.parse_args(argv)
    removed, size = limit(a.out, int(a.max_mb * 1024 * 1024))
    mb = round(size / 1024 / 1024, 1)
    log(f"limit-size: {a.out} is {mb} MB ({removed} file(s) removed, budget {a.max_mb} MB)")
    write_outputs(pruned=removed, mb=mb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
