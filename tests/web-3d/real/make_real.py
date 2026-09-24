#!/usr/bin/env python3
"""Real-KiCad OUT dir for the 3D PCBA viewer: GLBs exported by kicad-cli, components read from the
boards, laid out as docs/CONTRACT-project.md's Pcba3d.

    python3 tests/web-3d/real/make_real.py [--out tests/web-3d/out/real]
        [--base-pcb A.kicad_pcb --head-pcb B.kicad_pcb]   # any two boards (e.g. kipr-fixtures base/head)
        [--kicad-cli /workspace/projects/kipr-tools/bin/kicad-cli]

Without --base-pcb/--head-pcb it takes KiCad's own pic_programmer demo as base and derives a head
by editing a copy: a part moved, one rotated, one re-valued, one deleted, one added (a copy of
another), one footprint swapped. Only public KiCad demo data is used, and nothing is committed:
the output goes under tests/web-3d/out/ (git-ignored).

This stands in for the project backend (kipr/project) until it writes the real thing; it is a
test tool, not the backend's implementation.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
KICAD_CLI = os.environ.get("KICAD_CLI", "/workspace/projects/kipr-tools/bin/kicad-cli")
DEMOS = Path("/workspace/projects/kipr-tools/kicad10-rootfs/usr/share/kicad/demos")


# ── A small s-expression reader that remembers where each list starts and ends ──

TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    """Returns the top list as nested Python lists; each list is `L` with .span=(start, end)."""
    stack, pos = [], 0
    root = None
    while True:
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            node = L()
            node.start = m.start(1)
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif m.group(2):
            node = stack.pop()
            node.end = m.end(2)
            if not stack:
                root = node
                break
        elif m.group(3) is not None:
            stack[-1].append(Str(m.group(3).replace('\\"', '"')))
        else:
            stack[-1].append(m.group(4))
    return root


class L(list):
    start = end = 0

    def find(self, head):
        for x in self:
            if isinstance(x, L) and x and x[0] == head:
                return x
        return None

    def findall(self, head):
        return [x for x in self if isinstance(x, L) and x and x[0] == head]


class Str(str):
    pass


def footprints(board):
    out = []
    for fp in board.findall("footprint"):
        props = {p[1]: p[2] for p in fp.findall("property") if len(p) > 2}
        at = fp.find("at")
        layer = fp.find("layer")
        model = fp.find("model")
        attr = fp.find("attr")
        out.append({
            "node": fp,
            "ref": props.get("Reference", "?"),
            "value": props.get("Value"),
            "footprint": str(fp[1]),
            "x": float(at[1]), "y": float(at[2]), "rot": float(at[3]) if len(at) > 3 else 0.0,
            "side": "bottom" if layer and layer[1] == "B.Cu" else "top",
            "model": str(model[1]) if model else None,
            "dnp": bool(attr and "dnp" in attr[1:]),
        })
    return out


def board_bbox(board):
    xs, ys = [], []
    for kind in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
        for g in board.findall(kind):
            layer = g.find("layer")
            if not layer or layer[1] != "Edge.Cuts":
                continue
            for key in ("start", "end", "mid", "center"):
                p = g.find(key)
                if p:
                    xs.append(float(p[1])); ys.append(float(p[2]))
            pts = g.find("pts")
            if pts:
                for xy in pts.findall("xy"):
                    xs.append(float(xy[1])); ys.append(float(xy[2]))
    if not xs:
        return None
    return [min(xs), min(ys)], [max(xs) - min(xs), max(ys) - min(ys)]


# ── The synthetic head ──

def set_at(text, fp, x=None, y=None, rot=None):
    """Rewrite a footprint's own (at …) — the first one inside its span."""
    seg = text[fp.start:fp.end]
    at = fp.find("at")
    cur = [float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0]
    new = [x if x is not None else cur[0], y if y is not None else cur[1], rot if rot is not None else cur[2]]
    new_at = f"(at {new[0]:g} {new[1]:g}{'' if new[2] == 0 else f' {new[2]:g}'})"
    seg = re.sub(r"\(at [^()]*\)", new_at, seg, count=1)
    return text[:fp.start] + seg + text[fp.end:]


def derive_head(base_text):
    """Edits applied one at a time, re-parsing in between (spans move as text changes)."""
    edits = []

    def fp_of(text, ref):
        for f in footprints(parse(text)):
            if f["ref"] == ref:
                return f
        raise KeyError(ref)

    text = base_text
    f = fp_of(text, "R1"); text = set_at(text, f["node"], x=f["x"] + 3.0); edits.append("R1 moved +3 mm x")
    f = fp_of(text, "U3"); text = set_at(text, f["node"], rot=(f["rot"] + 90) % 360); edits.append("U3 rotated 90")
    f = fp_of(text, "R2")
    seg = text[f["node"].start:f["node"].end]
    seg = re.sub(r'\(property "Value" "[^"]*"', '(property "Value" "22k"', seg, count=1)
    text = text[:f["node"].start] + seg + text[f["node"].end:]; edits.append("R2 value -> 22k")
    f = fp_of(text, "C2"); text = text[:f["node"].start] + text[f["node"].end:]; edits.append("C2 deleted")
    # Added: a copy of R3 as R99, placed in free space below it.
    f = fp_of(text, "R3")
    seg = text[f["node"].start:f["node"].end]
    seg = re.sub(r'\(property "Reference" "R3"', '(property "Reference" "R99"', seg, count=1)
    seg = re.sub(r'\(uuid "[^"]*"\)', "", seg)
    seg = re.sub(r"\(net [^()]*\)", "", seg)
    fake = parse(seg)
    at = fake.find("at")
    seg = re.sub(r"\(at [^()]*\)", f"(at {float(at[1]):g} {float(at[2]) + 16:g})", seg, count=1)
    text = text[:f["node"].end] + "\n\t" + seg + text[f["node"].end:]; edits.append("R99 added (copy of R3)")
    # Footprint swapped: D10 gets a different 3D model (the footprint id changes too).
    f = fp_of(text, "D10")
    seg = text[f["node"].start:f["node"].end]
    seg = re.sub(r'LED_D5\.0mm\.(wrl|step)"', r'LED_D3.0mm.\1"', seg)
    seg = re.sub(r'^\(footprint "[^"]*"', '(footprint "LED_THT:LED_D3.0mm"', seg, count=1)
    text = text[:f["node"].start] + seg + text[f["node"].end:]; edits.append("D10 footprint LED_D5.0mm -> LED_D3.0mm")
    return text, edits


# ── Contract ──

def side_of(f):
    return {k: f[k] for k in ("x", "y", "rot", "side", "footprint", "value", "model", "dnp")}


def components(base_fps, head_fps):
    b = {f["ref"]: f for f in base_fps}
    h = {f["ref"]: f for f in head_fps}
    out = []
    for ref in sorted(set(b) | set(h)):
        bf, hf = b.get(ref), h.get(ref)
        what = []
        if bf and hf:
            if math.hypot(hf["x"] - bf["x"], hf["y"] - bf["y"]) > 1e-3:
                what.append("position")
            if abs(((hf["rot"] - bf["rot"] + 180) % 360) - 180) > 1e-2:
                what.append("rotation")
            for k in ("footprint", "value", "model", "side", "dnp"):
                if bf[k] != hf[k]:
                    what.append(k)
        if not bf:
            status = "added"
        elif not hf:
            status = "removed"
        elif any(w in what for w in ("footprint", "value", "model", "side", "dnp")):
            status = "changed"
        elif "position" in what:
            status = "moved"
        elif "rotation" in what:
            status = "rotated"
        else:
            status = "unchanged"
        out.append({"ref": ref, "status": status, "base": side_of(bf) if bf else None,
                    "head": side_of(hf) if hf else None, "what": what})
    return out


def export_glb(pcb, out, errors, label):
    cmd = [KICAD_CLI, "pcb", "export", "glb", "--subst-models", "--include-silkscreen", "--include-soldermask",
           "--include-pads", "-f", "-o", str(out), str(pcb)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if not out.exists():
        errors.append(f"kicad-cli failed to export {label} GLB (rc={r.returncode}): {(r.stderr or r.stdout)[-300:]}")
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "tests/web-3d/out/real"))
    ap.add_argument("--base-pcb")
    ap.add_argument("--head-pcb")
    ap.add_argument("--slug", default="pic_programmer")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("KIPR_KICAD_HOME", str(REPO / "tests/web-3d/out/kicad-home"))

    edits = []
    if args.base_pcb:
        base_pcb, head_pcb = Path(args.base_pcb), Path(args.head_pcb)
    else:
        demo = DEMOS / "pic_programmer"
        for side in ("base", "head"):
            d = work / side
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(demo, d)
        base_pcb = work / "base/pic_programmer.kicad_pcb"
        head_pcb = work / "head/pic_programmer.kicad_pcb"
        text, edits = derive_head(base_pcb.read_text())
        head_pcb.write_text(text)

    base_board, head_board = parse(base_pcb.read_text()), parse(head_pcb.read_text())
    errors = []
    d3 = out / "p" / args.slug / "3d"
    d3.mkdir(parents=True, exist_ok=True)
    sides = {}
    for side, pcb in (("base", base_pcb), ("head", head_pcb)):
        ok = export_glb(pcb, d3 / f"{side}.glb", errors, side)
        sides[side] = {"glb": f"p/{args.slug}/3d/{side}.glb"} if ok else None
    comps = components(footprints(base_board), footprints(head_board))
    bbox = board_bbox(head_board)
    counts = {k: sum(1 for c in comps if c["status"] == k) for k in ("added", "removed", "moved", "changed")}
    project = {
        "slug": args.slug, "name": args.slug, "path": f"demos/{args.slug}", "status": "modified",
        "summary": {"components": counts}, "schematic": None,
        "pcb": {"board": {"origin_mm": bbox[0], "size_mm": bbox[1], "thickness_mm": 1.6} if bbox else None,
                "layers": [], "changes": []},
        "pcba3d": {"base": sides["base"], "head": sides["head"], "components": comps},
        "bom": None, "netlist": None, "checks": {"erc": None, "drc": None}, "errors": errors,
    }
    review = {
        "version": 1, "tool": {"name": "kipr", "version": "0.1.0", "kicad": "10.0.6"},
        "base": {"sha": "", "ref": "base", "short": "base"}, "head": {"sha": "", "ref": "head", "short": "head"},
        "repo": {"url": "", "blob": None}, "projects": [project],
    }
    (out / "project-review.json").write_text(json.dumps(review, indent=1))
    print(f"real OUT written to {out}")
    for e in edits:
        print("  edit:", e)
    print("  statuses:", {s: sum(1 for c in comps if c["status"] == s) for s in
                          ("added", "removed", "moved", "rotated", "changed", "unchanged")})
    for e in errors:
        print("  ERROR:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
