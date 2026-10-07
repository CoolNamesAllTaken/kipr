"""Panels: boards without a schematic that hold copies of other boards (KiKit or hand-made).

A board without a schematic is a **panel** when any of these hold, else a plain **board**:

- `kikit`: KiKit markers: footprints from the `kikit` library or named `KiKit_*` (mousebites, tooling
  holes, fiducials), or nets renamed `Board_<n>-…` for at least two `n`;
- `copies`: reference designators repeat: at least half of the footprints share their reference
  with another one (a board placed several times; a normal board has unique references);
- `name`: "panel" (also "panelized", "panelised") in the file name or a directory name.

A schematic always makes it a project: a panel with its own schematic is reviewed like any project.
The source boards are found by footprint libraries: a board of the same repository whose footprints
the panel contains k times over (90 % of them, so a panel made from an older revision still counts).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from boarddd.io.kicad.pcb import PcbFile as Board, PcbFootprint as Footprint

from . import discover, geom
from .diff_pcb import COPY_NET

NAME_RE = re.compile(r"panel", re.I)
FEATURES = (  # (feature, KiKit reference prefix, footprint name pattern)
    ("fiducial", "KiKit_FID_", re.compile(r"fiducial", re.I)),
    ("mousebite", "KiKit_MB_", re.compile(r"mouse.?bite", re.I)),
    ("tooling", "KiKit_TO_", re.compile(r"tooling", re.I)),
)
FP_RE = re.compile(r'^\s*\((?:footprint|module)\s+"?([^"\s)]+)', re.M)
MAX_SOURCES = 200  # boards of the repository looked at for the source match
MAX_SOURCE_BYTES = 30_000_000


def feature(f: Footprint) -> str | None:
    """fiducial | mousebite | tooling for a panel's own footprints, else None."""
    name = f.lib_id.rpartition(":")[2]
    for feat, prefix, pat in FEATURES:
        if (f.ref or "").startswith(prefix) or pat.search(name):
            return feat
    if f.lib_id.startswith("kikit:") and name == "NPTH":
        return "tooling" if (f.ref or "").startswith("KiKit_TO") else "mousebite"
    return None


def detect(board: Board | None, name: str, path: str) -> dict | None:
    """{"signals": [...], "copies": n | None} if the board looks like a panel (module doc), else None."""
    if board is None:
        return None
    signals = []
    nums = {m.group(1) for f in board.footprints for n in (f.pad_nets or {}).values() if n
            for m in [COPY_NET.match(n)] if m}
    if len(nums) >= 2 or any(f.lib_id.startswith("kikit:") or (f.ref or "").startswith("KiKit_")
                             for f in board.footprints):
        signals.append("kikit")
    parts = [f for f in board.footprints if not feature(f) and f.ref and not f.ref.startswith(("REF", "#"))]
    refs = Counter(f.ref for f in parts)
    dup = sum(n for n in refs.values() if n > 1)
    if parts and dup * 2 >= len(parts):
        signals.append("copies")
    if NAME_RE.search(name) or any(NAME_RE.search(d) for d in path.split("/")):
        signals.append("name")
    if not signals:
        return None
    copies = len(nums) if len(nums) >= 2 else (max(refs.values()) if dup else None)
    return {"signals": signals, "copies": copies}


def parts_of(board: Board) -> Counter:
    """Footprint libraries of a board's parts (panel features left out), with multiplicity."""
    return Counter(f.lib_id for f in board.footprints if not feature(f) and f.lib_id)


def sources(git, sha: str, own: str, board: Board) -> list[dict]:
    """Boards of the repository at `sha` that the panel holds copies of: [{"path", "copies"}],
    most footprints explained first. `own`: the panel's own path (left out)."""
    want = parts_of(board)
    if not want:
        return []
    cands = [p for p in git.ls_tree(sha) if p.endswith(".kicad_pcb") and p != own and not discover.is_noise(p)]
    found = []
    for p in cands[:MAX_SOURCES]:
        text = git.text(sha, p) or ""
        if not text or len(text) > MAX_SOURCE_BYTES:
            continue
        have = Counter(x for x in FP_RE.findall(text) if not x.startswith("kikit:"))
        n = sum(have.values())
        if n < 2:
            continue
        for k in range(max(want.values()) // max(1, min(have.values())), 0, -1):
            covered = sum(min(want[x], k * c) for x, c in have.items())
            if covered >= 0.9 * k * n:
                found.append({"path": p, "copies": k, "parts": n})
                break
    found.sort(key=lambda s: (-s["copies"] * s["parts"], s["path"]))
    out, left = [], Counter(want)
    for s in found:  # greedy: a board explained by a bigger one (its superset) is dropped
        have = Counter(x for x in FP_RE.findall(git.text(sha, s["path"]) or "") if not x.startswith("kikit:"))
        covered = sum(min(left[x], s["copies"] * c) for x, c in have.items())
        if covered >= 0.9 * s["copies"] * s["parts"]:
            out.append({"path": s["path"], "copies": s["copies"]})
            left -= Counter({x: s["copies"] * c for x, c in have.items()})
    return out


def config_file(tree, pdir: str, stem: str) -> str | None:
    """A KiKit preset next to the panel (`<stem>.json`, `kikit.json`, `panel*.json`), or None."""
    prefix = pdir + "/" if pdir else ""
    names = {p[len(prefix):]: p for p in tree if p.startswith(prefix) and "/" not in p[len(prefix):]
             and p.endswith(".json")}
    for n in (stem + ".json", "kikit.json", "kikit_config.json"):
        if n in names:
            return names[n]
    return next((names[n] for n in sorted(names) if n.lower().startswith(("panel", "kikit"))), None)


# --- semantic changes of a panel ---------------------------------------------------------------

def annotate(changes: list[dict], components: list[dict], base: Board, head: Board) -> tuple[list, list]:
    """Panel view of diff_boards' output: fiducial / tooling footprint changes get their own kind,
    mousebite holes become one `mousebites` change per tab, outline changes say whether they are in
    the frame (on the panel's outer edge) or at tabs and cuts; panel features leave the component
    list (they aren't parts)."""
    feats = {}
    for b in (base, head):
        for f in b.footprints:
            ft = feature(f)
            if ft:
                feats[f.ref] = ft
    out, bites = [], []
    for c in changes:
        ft = feats.get((c.get("ref") or "").split("·")[0]) if c.get("kind") == "footprint" else None
        if ft == "mousebite":
            bites.append(c)
        elif ft:
            out.append({**c, "kind": ft})
        elif c.get("kind") == "outline":
            out.append({**c, "feature": _outline_feature(c, base, head)})
        else:
            out.append(c)
    out += _bite_groups(bites)
    comps = [c for c in components if c["ref"].split("·")[0] not in feats]
    return out, comps


def _outline_feature(c: dict, base: Board, head: Board) -> str:
    box = c.get("bbox_mm")
    for b in (head, base):
        eb = b.edge_box
        if box and eb:
            x, y, w, h = box
            if min(abs(x - eb[0]), abs(y - eb[1]), abs(x + w - eb[2]), abs(y + h - eb[3])) < 0.5:
                return "frame"
    return "tabs"


def _bite_groups(bites: list[dict]) -> list[dict]:
    """Mousebite hole changes clustered (2 mm apart at most) into one change each."""
    boxed = [(_xyxy(c.get("bbox_mm")), c) for c in bites]
    out = []
    for box, grp in geom.cluster(boxed, 2.0):
        whats = Counter(c["what"] for c in grp)
        what = next(iter(whats)) if len(whats) == 1 else "modified"
        moved = [c for c in grp if c["what"] == "moved"]
        dist = [_dist(c) for c in moved]
        parts = [f"{n} hole{'s' if n != 1 else ''} {w}" for w, n in sorted(whats.items())]
        if dist and max(dist) - min(dist) < 0.01:
            parts[-1 if whats.get("moved") else 0] += f" {dist[0]:.3f} mm"
        sides = {k: geom.to_xywh(geom.union(*[_xyxy(c.get(k)) for c in grp if c.get(k)]))
                 for k in ("base_bbox_mm", "head_bbox_mm")}
        out.append({"kind": "mousebites", "what": what, "layer": None,
                    "layers": sorted({ly for c in grp for ly in c.get("layers") or []}),
                    "bbox_mm": geom.to_xywh(box), "holes": ["NPTH"], "detail": ", ".join(parts),
                    "refs": sorted((c.get("ref") or "?" for c in grp), key=_natural),
                    **{k: v for k, v in sides.items() if v}})
    return out


def _xyxy(b):
    return [b[0], b[1], b[0] + b[2], b[1] + b[3]] if b else None


def _dist(c) -> float:
    b, h = c.get("base_bbox_mm"), c.get("head_bbox_mm")
    if not b or not h:
        return 0.0
    return math.hypot(h[0] + h[2] / 2 - b[0] - b[2] / 2, h[1] + h[3] / 2 - b[1] - b[3] / 2)


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s or "")]


def summary(changes: list[dict]) -> dict:
    """{"fiducial": n, "tooling": n, "mousebites": n, "tabs": n, "frame": n}: panel changes by kind."""
    n = defaultdict(int)
    for c in changes:
        k = c.get("kind")
        if k in ("fiducial", "tooling", "mousebites"):
            n[k] += 1
        elif k == "outline":
            n[c.get("feature") or "tabs"] += 1
    return dict(n)


def info(det: dict, board: Board, srcs: list[dict], config: str | None) -> dict:
    """The `panel` object of a project (docs/CONTRACT-project.md)."""
    feats = Counter(feature(f) for f in board.footprints)
    copies = det.get("copies") or sum(s["copies"] for s in srcs) or None
    return {"signals": det["signals"], "copies": copies, "sources": srcs, "config": config,
            "fiducials": feats.get("fiducial", 0), "tooling": feats.get("tooling", 0),
            "mousebites": feats.get("mousebite", 0)}


