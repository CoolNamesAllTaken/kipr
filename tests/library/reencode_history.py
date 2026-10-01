#!/usr/bin/env python3
"""Run the re-encode check over the history of a real library repository (manual validation).

    python3 tests/library/reencode_history.py REPO [--rev main] [--max N] [--kicad-cli PATH]

For every commit on REV that modifies a .kicad_sym / .kicad_mod, and every part whose text
changed, it prints the verdict (re-encoded / modified, method, first difference), plus a ground
truth that does not depend on kipr's normalisations: kicad-cli loads and re-saves BOTH sides and
the results are compared. The summary line counts the disagreements; a part kipr calls re-encoded
while KiCad sees a difference ("FALSE RE-ENCODED") must never happen. Needs kicad-cli (KiCad 10).
Where KiCad's reference upgrade does not apply (the head was saved by another KiCad), renders are
assumed identical, so the symbol verdict rests on the semantic comparison alone: a harder test of
it than a real run, which also needs pixel-identical renders.
"""
from __future__ import annotations

import argparse
import collections
import subprocess
import sys

from kipr.common import kicad_cli
from kipr.common.sexpr import dumps, parse
from kipr.library.render import reencode as rc
from kipr.library.render.sym import parse_library


def git(repo, *a):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo")
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--max", type=int, default=0, help="stop after N commits (0: all)")
    ap.add_argument("--kicad-cli", default=None)
    a = ap.parse_args(argv)
    up = rc.Upgrader(kicad_cli.find(a.kicad_cli))
    if not up.available:
        print("needs kicad-cli", file=sys.stderr)
        return 2
    count = collections.Counter()
    commits = (git(a.repo, "log", "--format=%h", a.rev, "--", "*.kicad_sym", "*.kicad_mod") or "").split()
    for c in commits[: a.max or None]:
        for line in (git(a.repo, "diff", "--name-status", f"{c}^", c) or "").splitlines():
            st, _, path = line.partition("\t")
            if st != "M" or not path.endswith((".kicad_sym", ".kicad_mod")):
                continue
            bt, ht = git(a.repo, "show", f"{c}^:{path}"), git(a.repo, "show", f"{c}:{path}")
            try:
                br, hr = parse(bt), parse(ht)
            except Exception as e:  # noqa: BLE001
                print(f"{c} {path}: parse error {e}")
                continue
            if path.endswith(".kicad_sym"):
                bl, hl = parse_library(br), parse_library(hr)
                ub, uh = (up.symbol_library(t)[0] for t in (bt, ht))
                ubl, uhl = (parse_library(parse(t)) if t else {} for t in (ub, uh))
                pairs = [(n, bl, hl) for n in sorted(set(bl) & set(hl)) if dumps(bl[n]) != dumps(hl[n])]
                for n, _, _ in pairs:
                    v = rc.classify_symbol(n, br, bl, hr, hl, bt, up, head_text=ht, render_identical=lambda: True)
                    truth = n in ubl and n in uhl and rc.canonical(ubl[n]) == rc.canonical(uhl[n])
                    report(count, c, f"{path} {n}", v, truth)
            else:
                if dumps(br) == dumps(hr):
                    continue
                v = rc.classify_footprint(path, br, hr, bt, up)
                ub, uh = up.footprint(bt, path)[0], up.footprint(ht, path)[0]
                truth = bool(ub and uh) and rc.canonical(parse(ub)) == rc.canonical(parse(uh))
                report(count, c, path, v, truth)
    print("summary:", dict(count))
    return 1 if count["FALSE RE-ENCODED"] else 0


def report(count, c, what, v, truth):
    verdict = "re-encoded" if v["reencoded"] else "modified"
    if v["reencoded"] and not truth:
        tag = "FALSE RE-ENCODED"
    elif not v["reencoded"] and truth:
        tag = "missed re-encode"
    else:
        tag = "agree"
    count[tag] += 1
    first = (v["differences"] or [""])[0][:140]
    print(f"{c} {what}: {verdict} ({v['method']}) [{tag}; KiCad round trip {'equal' if truth else 'differs'}] {first}",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
