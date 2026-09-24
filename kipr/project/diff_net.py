"""Netlist diff (with best-effort rename detection) and ERC/DRC deltas."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict

from ._compat import parse

# --- netlist ---------------------------------------------------------------------------


def parse_kicad_netlist(text: str) -> dict[str, set[str]]:
    """kicad-cli `sch export netlist --format kicadsexpr` -> {net name: {"REF.PIN", ...}}."""
    root = parse(text)
    out: dict[str, set[str]] = {}
    nets = root.child("nets")
    if nets is None:
        return out
    for n in nets.children("net"):
        name = str(n.value("name", ""))
        nodes = {f"{nd.value('ref', '')}.{nd.value('pin', '')}" for nd in n.children("node")}
        out.setdefault(name, set()).update(nodes)
    return out


def netlist_from_board(board) -> dict[str, set[str]]:
    """Fallback netlist from a parsed board's pad nets (no kicad-cli needed)."""
    out: dict[str, set[str]] = defaultdict(set)
    for fp in board.footprints:
        if not fp.ref:
            continue
        for pad, net in fp.pad_nets.items():
            if net:
                out[net].add(f"{fp.ref}.{pad}")
    return dict(out)


AUTO_NET = re.compile(r"^(Net-\(|unconnected-\()")


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def diff_netlists(base: dict[str, set[str]] | None, head: dict[str, set[str]] | None,
                  rename_threshold: float = 0.5):
    """-> {"changes": [...], "moved_pins": [...]}.

    A net that exists only in base and one that exists only in head are paired as a rename when
    their node sets are similar enough (Jaccard >= threshold); auto-named nets (Net-(…),
    unconnected-(…)) that are renamed with identical pins are not reported at all.
    """
    base, head = base or {}, head or {}
    only_b = {n: s for n, s in base.items() if n not in head}
    only_h = {n: s for n, s in head.items() if n not in base}
    renames = {}  # head name -> base name
    cands = sorted(((_jaccard(sb, sh), nb, nh) for nb, sb in only_b.items() for nh, sh in only_h.items()
                    if sb & sh), reverse=True)
    used_b, used_h = set(), set()
    for score, nb, nh in cands:
        if score < rename_threshold or nb in used_b or nh in used_h:
            continue
        renames[nh] = nb
        used_b.add(nb)
        used_h.add(nh)
    changes = []
    for name in sorted(set(base) | set(head)):
        if name in used_b:
            continue
        if name in base and name in head:
            b, h = base[name], head[name]
            if b == h:
                continue
            changes.append({"net": name, "status": "modified", "added": sorted(h - b), "removed": sorted(b - h),
                            "renamed_from": None})
        elif name in head:
            src = renames.get(name)
            if src is not None:
                b, h = base[src], head[name]
                if b == h and AUTO_NET.match(src) and AUTO_NET.match(name):
                    continue
                changes.append({"net": name, "status": "renamed" if b == h else "modified",
                                "added": sorted(h - b), "removed": sorted(b - h), "renamed_from": src})
            else:
                changes.append({"net": name, "status": "added", "added": sorted(head[name]), "removed": [],
                                "renamed_from": None})
        else:
            changes.append({"net": name, "status": "removed", "added": [], "removed": sorted(base[name]),
                            "renamed_from": None})
    # pins that changed net (net identity follows renames)
    pin_b = {p: n for n, s in base.items() for p in s}
    pin_h = {p: renames.get(n, n) for n, s in head.items() for p in s}
    moved = []
    for p in sorted(set(pin_b) & set(pin_h)):
        if pin_b[p] != pin_h[p]:
            head_name = next((n for n, s in head.items() if p in s), pin_h[p])
            moved.append({"pin": p, "from": pin_b[p], "to": head_name})
    return {"changes": changes, "moved_pins": moved}


# --- ERC / DRC -------------------------------------------------------------------------


def _items(v):
    out = []
    for it in v.get("items", []) or []:
        pos = it.get("pos") or {}
        out.append({"description": it.get("description", ""), "pos": [pos.get("x"), pos.get("y")]
                    if "x" in pos else None, "uuid": it.get("uuid")})
    return out


def parse_report(text: str, kind: str) -> list[dict]:
    """Flatten a kicad-cli ERC or DRC JSON report into violations."""
    data = json.loads(text)
    out = []

    def add(v, sheet=None, category="violation"):
        items = _items(v)
        pos = next((i["pos"] for i in items if i["pos"] and i["pos"][0] is not None), None)
        out.append({"severity": v.get("severity", "error"), "type": v.get("type", ""),
                    "description": v.get("description", ""), "items": [i["description"] for i in items],
                    "uuids": [i["uuid"] for i in items if isinstance(i.get("uuid"), str) and i["uuid"]],
                    "pos_mm": [round(pos[0], 4), round(pos[1], 4)] if pos else None, "sheet": sheet,
                    "category": category})

    if kind == "erc":
        for sh in data.get("sheets", []) or []:
            for v in sh.get("violations", []) or []:
                add(v, sheet=sh.get("path"))
    else:
        for key, cat in (("violations", "violation"), ("unconnected_items", "unconnected"),
                         ("schematic_parity", "parity")):
            for v in data.get(key, []) or []:
                add(v, category=cat)
    return out


def fix_erc_scale(violations: list[dict], page_max_mm: float) -> bool:
    """KiCad 10.0.x writes ERC positions in the JSON report 100x too small (schematic IU mix-up).
    If every position fits in 1/50 of the page but x100 still lands on the page, scale them.
    Returns True when a correction was applied."""
    ps = [v["pos_mm"] for v in violations if v.get("pos_mm")]
    if not ps or page_max_mm <= 0:
        return False
    m = max(max(abs(p[0]), abs(p[1])) for p in ps)
    if m < page_max_mm / 50 and m * 100 <= page_max_mm * 1.05:
        for v in violations:
            if v.get("pos_mm"):
                v["pos_mm"] = [round(v["pos_mm"][0] * 100, 4), round(v["pos_mm"][1] * 100, 4)]
        return True
    return False


_NUM = re.compile(r"-?\d+(\.\d+)?")


def _norm(s: str) -> str:
    """Item descriptions contain lengths/coordinates that change with small edits."""
    return _NUM.sub("#", s) if re.search(r"\d\.\d", s) else s


def check_delta(base: list[dict] | None, head: list[dict] | None, tol: float = 2.0):
    """Match violations by type + items (+ position within `tol` mm), then by type + position."""
    base, head = list(base or []), list(head or [])

    def dist(a, b):
        if a["pos_mm"] is None or b["pos_mm"] is None:
            return 0.0 if a["pos_mm"] == b["pos_mm"] else math.inf
        return math.dist(a["pos_mm"], b["pos_mm"])

    passes = [
        (lambda v: (v["type"], v["sheet"], tuple(sorted(_norm(i) for i in v["items"])), v["description"]), tol),
        (lambda v: (v["type"], v["sheet"], tuple(sorted(_norm(i) for i in v["items"]))), math.inf),
        (lambda v: (v["type"], v["sheet"]), 0.5),
    ]
    rem_b, rem_h = list(range(len(base))), list(range(len(head)))
    for key, max_d in passes:
        idx = defaultdict(list)
        for j in rem_h:
            idx[key(head[j])].append(j)
        left_b = []
        taken = set()
        for i in rem_b:
            cands = [j for j in idx.get(key(base[i]), []) if j not in taken]
            best = min(cands, key=lambda j: dist(base[i], head[j]), default=None)
            if best is not None and dist(base[i], head[best]) <= max_d:
                taken.add(best)
            else:
                left_b.append(i)
        rem_b, rem_h = left_b, [j for j in rem_h if j not in taken]
    return {"base_count": len(base), "head_count": len(head), "new": [head[j] for j in rem_h],
            "fixed": [base[i] for i in rem_b]}
