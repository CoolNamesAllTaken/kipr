"""Job summary and annotations for a project-review run (no token needed).

    kipr project ci job-summary --out OUT [--annotate] [--repo-dir DIR] [--summary FILE]
        [--link NAME=URL ...]

--annotate prints `::error file=…,line=…::` / `::warning …::` workflow commands for the NEW
ERC/DRC violations (fixed ones and exclusions are not annotated). Each is placed on the line of
the first violating item in the head checkout (`--repo-dir`, found by its KiCad uuid), else on
line 1 of the project's schematic/board. GitHub shows at most 10 errors and 10 warnings per
step, so errors come first. --summary appends markdown to FILE (normally $GITHUB_STEP_SUMMARY).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .common import (CHECK_KINDS, d, load_review, lst, safe_http_url, safe_repo_path, text)
from .make_comment import build_comment

MAX_ANNOTATIONS = 50
UUID_RE = re.compile(r'\(uuid "?([0-9A-Fa-f-]{36})"?\)')
EXT = {"erc": ".kicad_sch", "drc": ".kicad_pcb"}


def _data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _prop(s: str) -> str:
    return _data(s).replace(":", "%3A").replace(",", "%2C")


class Locator:
    """uuid -> (repo path, line) for the KiCad files of one project dir (scanned lazily)."""

    def __init__(self, repo_dir: Path | None):
        self.repo_dir = repo_dir
        self._index: dict[tuple[str, str], dict[str, tuple[str, int]]] = {}

    def files(self, pdir: str, ext: str) -> list[str]:
        if self.repo_dir is None:
            return []
        root = self.repo_dir / pdir if pdir else self.repo_dir
        out = []
        for dirpath, dirnames, filenames in os.walk(root):
            rel_depth = len(Path(dirpath).relative_to(root).parts)
            dirnames[:] = [x for x in dirnames if not x.startswith(".") and not x.endswith("-backups")
                           and rel_depth < 3]
            for f in sorted(filenames):
                if f.endswith(ext):
                    out.append(Path(dirpath, f).relative_to(self.repo_dir).as_posix())
        return out

    def index(self, pdir: str, ext: str):
        key = (pdir, ext)
        if key not in self._index:
            idx: dict[str, tuple[str, int]] = {}
            for rel in self.files(pdir, ext):
                try:
                    with open(self.repo_dir / rel, encoding="utf-8", errors="replace") as fh:
                        for no, line in enumerate(fh, 1):
                            for m in UUID_RE.finditer(line):
                                idx.setdefault(m.group(1).lower(), (rel, no))
                except OSError:
                    continue
            self._index[key] = idx
        return self._index[key]

    def locate(self, pdir: str, name: str, kind: str, uuids) -> tuple[str | None, int]:
        ext = EXT[kind]
        idx = self.index(pdir, ext)
        for u in uuids:
            if isinstance(u, str) and u.lower() in idx:
                return idx[u.lower()]
        main = f"{pdir}/{name}{ext}" if pdir else f"{name}{ext}"
        return (main if safe_repo_path(main) else None), 1


def annotations(doc: dict, repo_dir: Path | None = None) -> list[str]:
    loc = Locator(repo_dir)
    rows = []
    for p in doc["projects"]:
        pdir = safe_repo_path(text(p.get("path"))) or ""
        name = text(p.get("name"))
        if not re.fullmatch(r"[A-Za-z0-9 ._+-]{1,120}", name or ""):
            continue
        for kind in CHECK_KINDS:
            for v in lst(d(d(p.get("checks")).get(kind)).get("new")):
                if not isinstance(v, dict):
                    continue
                level = {"error": "error", "warning": "warning"}.get(text(v.get("severity")))
                if level:
                    rows.append((level, kind, pdir, name, v))
    rows.sort(key=lambda r: r[0] != "error")
    out = []
    for level, kind, pdir, name, v in rows[:MAX_ANNOTATIONS]:
        path, line = loc.locate(pdir, name, kind, lst(v.get("uuids")))
        props = []
        if path:
            props += [f"file={_prop(path)}", f"line={line}"]
        props.append("title=" + _prop(f"New {kind.upper()} {level}: {text(v.get('type'))[:40]} ({name})"[:120]))
        items = "; ".join(i for i in lst(v.get("items"))[:4] if isinstance(i, str))
        msg = re.sub(r"\s+", " ", f"{text(v.get('description'))}. {items}").strip()[:900]
        pos = lst(v.get("pos_mm"))
        if len(pos) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pos):
            msg += f" (at {pos[0]:.2f}, {pos[1]:.2f} mm)"
        out.append(f"::{level} {','.join(props)}::{_data(msg)}")
    return out


def summary(doc: dict, links: dict[str, str]) -> str:
    body = build_comment(doc, run_url=links.get("run"), site_url=links.get("site"),
                         report_url=links.get("report"), data_url=links.get("data"), marker=False)
    extra = [f"- [{k}]({safe})" for k, u in links.items() if k not in ("run", "site", "report", "data")
             and (safe := safe_http_url(u))]
    return body + ("\n" + "\n".join(extra) + "\n" if extra else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kipr project ci job-summary", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="the `kipr project` OUT dir")
    ap.add_argument("--annotate", action="store_true")
    ap.add_argument("--repo-dir", type=Path, default=Path("."), help="head checkout (to find item lines)")
    ap.add_argument("--summary", type=Path)
    ap.add_argument("--link", action="append", default=[], metavar="NAME=URL",
                    help="artifact links: run, site, report, data (others are listed as is)")
    a = ap.parse_args(argv)
    doc = load_review(a.out / "project-review.json")
    if a.annotate:
        for line in annotations(doc, a.repo_dir):
            print(line)
    if a.summary:
        links = {}
        for kv in a.link:
            k, _, v = kv.partition("=")
            if k and v:
                links[k.strip()] = v.strip()
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(summary(doc, links))
    return 0


if __name__ == "__main__":
    sys.exit(main())
