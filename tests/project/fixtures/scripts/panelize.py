"""Panelize a rectangular board into a KiKit-style grid panel (fixture generator; needs pcbnew).

    kicad-python scripts/panelize.py CONFIG.json SRC.kicad_pcb OUT.kicad_pcb [--shift-tab N:DX]

Reads the subset of a KiKit preset the fixture uses: layout grid (rows, cols, hspace, vspace,
renamenet), framing frame (width, space), tabs fixed (width, hcount = vcount = 1), cuts mousebites
(drill, spacing), tooling 4hole (hoffset, voffset, size) and fiducials 3fid/4fid (hoffset,
voffset, coppersize, opening). The output carries KiKit's markers: nets renamed `Board_{n}-…`,
refs unchanged (duplicates), `kikit:NPTH` / `kikit:Fiducial` footprints named `KiKit_MB_*`,
`KiKit_TO_*`, `KiKit_FID_[TB]_*`, one merged Edge.Cuts outline, and the source `.kicad_pro` copied
next to it. `--shift-tab N:DX` moves tab N (and its mousebites) by DX mm along its edge.
"""

import json
import os
import shutil
import sys
import tempfile

import pcbnew

MM = pcbnew.FromMM
NPTH = """(footprint "NPTH" (version 20240108) (generator "kipr-fixture") (layer "F.Cu")
  (property "Reference" "REF**" (at 0 0.5 0) (layer "F.SilkS") (hide yes) (effects (font (size 1 1) (thickness 0.15))))
  (property "Value" "NPTH" (at 0 -0.5 0) (layer "F.Fab") (hide yes) (effects (font (size 1 1) (thickness 0.15))))
  (attr exclude_from_pos_files exclude_from_bom)
  (pad "" np_thru_hole circle (at 0 0) (size 1 1) (drill 1) (layers "F&B.Cu" "*.Mask"))
)
"""
FIDUCIAL = """(footprint "Fiducial" (version 20240108) (generator "kipr-fixture") (layer "F.Cu")
  (descr "Circular Fiducial") (tags "fiducial")
  (property "Reference" "REF**" (at 0 -1.5 0) (layer "F.SilkS") (hide yes) (effects (font (size 1 1) (thickness 0.15))))
  (property "Value" "Fiducial" (at 0 1.5 0) (layer "F.Fab") (hide yes) (effects (font (size 1 1) (thickness 0.15))))
  (attr smd exclude_from_bom)
  (fp_circle (center 0 0) (end 0.6 0) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))
  (pad "" smd circle (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (solder_mask_margin 0.5) (clearance 0.5))
)
"""


def mm(v) -> float:
    return float(str(v).removesuffix("mm"))


def edge_box(board):
    xs, ys = [], []
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            for p in (d.GetStart(), d.GetEnd()):
                xs.append(pcbnew.ToMM(p.x))
                ys.append(pcbnew.ToMM(p.y))
    return min(xs), min(ys), max(xs), max(ys)


def rename_nets(board, prefix):
    info = board.GetNetInfo()
    names = [str(n) for n in info.NetsByName() if str(n)]
    new = {"": info.GetNetItem("")}
    for name in names:
        net = pcbnew.NETINFO_ITEM(board, prefix + name)
        board.Add(net)
        new[name] = net
    for coll in (board.GetPads(), board.GetTracks(), board.Zones()):
        for it in coll:
            it.SetNet(new[it.GetNetname()])
    for name in names:
        board.RemoveNative(info.GetNetItem(name))


def outline(fill, xs, ys):
    """Edge segments around the filled cells of an axis-aligned grid (fill[i][j]: x cell i, y cell j)."""
    nx, ny = len(xs) - 1, len(ys) - 1
    full = lambda i, j: 0 <= i < nx and 0 <= j < ny and fill[i][j]
    segs = []
    for j in range(ny + 1):  # horizontal edges between rows j-1 and j
        run = None
        for i in range(nx + 1):
            on = i < nx and full(i, j - 1) != full(i, j)
            if on and run is None:
                run = i
            elif not on and run is not None:
                segs.append(((xs[run], ys[j]), (xs[i], ys[j])))
                run = None
    for i in range(nx + 1):
        run = None
        for j in range(ny + 1):
            on = j < ny and full(i - 1, j) != full(i, j)
            if on and run is None:
                run = j
            elif not on and run is not None:
                segs.append(((xs[i], ys[run]), (xs[i], ys[j])))
                run = None
    return segs


def main(argv):
    shift = {}
    if "--shift-tab" in argv:
        k = argv.index("--shift-tab")
        n, dx = argv[k + 1].split(":")
        shift[int(n)] = float(dx)
        del argv[k:k + 2]
    cfg_path, src, out = argv
    cfg = json.load(open(cfg_path))
    lay, frm, tabs, cuts = cfg["layout"], cfg["framing"], cfg["tabs"], cfg["cuts"]
    rows, cols = int(lay["rows"]), int(lay["cols"])
    hs, vs, fw, fs = mm(lay["hspace"]), mm(lay["vspace"]), mm(frm["width"]), mm(frm["space"])
    tw = mm(tabs["width"])

    lib = tempfile.mkdtemp(suffix=".pretty")
    for name, text in (("NPTH", NPTH), ("Fiducial", FIDUCIAL)):
        open(os.path.join(lib, name + ".kicad_mod"), "w").write(text)

    def kikit_fp(board, name, ref, x, y, size=None, bottom=False):
        fp = pcbnew.FootprintLoad(lib, name)
        fp.SetFPID(pcbnew.LIB_ID("kikit", name))
        board.Add(fp)
        fp.SetReference(ref)
        fp.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
        if size:
            for pad in fp.Pads():
                pad.SetDrillSize(pcbnew.VECTOR2I(MM(size), MM(size)))
                pad.SetSize(pcbnew.F_Cu, pcbnew.VECTOR2I(MM(size), MM(size)))
        if bottom:
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
        return fp

    x0, y0, x1, y1 = edge_box(pcbnew.LoadBoard(src))
    w, h = x1 - x0, y1 - y0
    px, py = 20.0, 20.0  # panel top-left
    pw, ph = 2 * (fw + fs) + cols * w + (cols - 1) * hs, 2 * (fw + fs) + rows * h + (rows - 1) * vs
    origins = [(px + fw + fs + c * (w + hs), py + fw + fs + r * (h + vs)) for r in range(rows) for c in range(cols)]

    panel = None
    for n, (ox, oy) in enumerate(origins):
        b = pcbnew.LoadBoard(src)
        rename_nets(b, lay.get("renamenet", "Board_{n}-{orig}").split("{orig}")[0].replace("{n}", str(n)))
        move = pcbnew.VECTOR2I(MM(ox - x0), MM(oy - y0))
        for d in list(b.GetDrawings()):
            if d.GetLayer() == pcbnew.Edge_Cuts:
                b.Remove(d)
        items = list(b.GetFootprints()) + list(b.GetTracks()) + list(b.Zones()) + list(b.GetDrawings())
        for it in items:
            it.Move(move)
        if panel is None:
            panel = b
            continue
        for net in b.GetNetInfo().NetsByName():
            if str(net) and not panel.FindNet(str(net)):
                panel.Add(pcbnew.NETINFO_ITEM(panel, str(net)))
        for it in items:
            try:
                dup = it.Duplicate()
            except TypeError:  # FOOTPRINT / ZONE take addToParentGroup in KiCad 10
                dup = it.Duplicate(False)
            panel.Add(dup)
            if hasattr(dup, "GetNetname") and dup.GetNetname():
                dup.SetNet(panel.FindNet(dup.GetNetname()))
            if isinstance(dup, pcbnew.FOOTPRINT):
                for pad in dup.Pads():
                    if pad.GetNetname():
                        pad.SetNet(panel.FindNet(pad.GetNetname()))

    # tabs: one per board edge, centred, bridging to the neighbour board or the frame
    tab_rects, bites = [], []  # (x0, y0, x1, y1); (x, y) mousebite holes
    spacing, drill = mm(cuts["spacing"]), mm(cuts["drill"])
    n_holes = int(tw // spacing) + 1
    for k, (ox, oy) in enumerate(origins):
        r, c = divmod(k, cols)
        for side in ("top", "bottom", "left", "right"):
            if side == "bottom" and r < rows - 1 or side == "right" and c < cols - 1:
                continue  # the tab from the neighbour's top / left edge covers this gap
            t = len(tab_rects)
            d = shift.get(t, 0.0)
            if side in ("top", "bottom"):
                cx = ox + w / 2 + d
                gap = vs if side == "top" and r > 0 else fs
                ya, yb = (oy - gap, oy) if side == "top" else (oy + h, oy + h + fs)
                tab_rects.append((cx - tw / 2, ya, cx + tw / 2, yb))
                edges = [oy if side == "top" else oy + h] + ([oy - gap] if side == "top" and r > 0 else [])
                bites += [[(cx - (n_holes - 1) * spacing / 2 + i * spacing, e) for i in range(n_holes)] for e in edges]
            else:
                cy = oy + h / 2 + d
                gap = hs if side == "left" and c > 0 else fs
                xa, xb = (ox - gap, ox) if side == "left" else (ox + w, ox + w + fs)
                tab_rects.append((xa, cy - tw / 2, xb, cy + tw / 2))
                edges = [ox if side == "left" else ox + w] + ([ox - gap] if side == "left" and c > 0 else [])
                bites += [[(e, cy - (n_holes - 1) * spacing / 2 + i * spacing) for i in range(n_holes)] for e in edges]

    # merged outline: frame + boards + tabs on a grid of all rectangle edges
    rects = [(px, py, px + pw, py + fw), (px, py + ph - fw, px + pw, py + ph), (px, py, px + fw, py + ph),
             (px + pw - fw, py, px + pw, py + ph)]
    rects += [(ox, oy, ox + w, oy + h) for ox, oy in origins] + tab_rects
    xs = sorted({round(v, 4) for r in rects for v in (r[0], r[2])})
    ys = sorted({round(v, 4) for r in rects for v in (r[1], r[3])})
    fill = [[any(r[0] <= (xs[i] + xs[i + 1]) / 2 <= r[2] and r[1] <= (ys[j] + ys[j + 1]) / 2 <= r[3] for r in rects)
             for j in range(len(ys) - 1)] for i in range(len(xs) - 1)]
    for (ax, ay), (bx, by) in outline(fill, xs, ys):
        s = pcbnew.PCB_SHAPE(panel, pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(MM(0.1))
        s.SetStart(pcbnew.VECTOR2I(MM(ax), MM(ay)))
        s.SetEnd(pcbnew.VECTOR2I(MM(bx), MM(by)))
        panel.Add(s)

    for t, row in enumerate(bites):
        for i, (x, y) in enumerate(row):
            kikit_fp(panel, "NPTH", f"KiKit_MB_{t + 1}_{i + 1}", x, y, drill)
    tool = cfg["tooling"]
    th, tv = mm(tool["hoffset"]), mm(tool["voffset"])
    corners = lambda ho, vo: [(px + ho, py + vo), (px + pw - ho, py + vo), (px + ho, py + ph - vo), (px + pw - ho, py + ph - vo)]
    for i, (x, y) in enumerate(corners(th, tv)):
        kikit_fp(panel, "NPTH", f"KiKit_TO_{i + 1}", x, y, mm(tool["size"]))
    fid = cfg["fiducials"]
    nfid = {"3fid": 3, "4fid": 4}[fid["type"]]
    for i, (x, y) in enumerate(corners(mm(fid["hoffset"]), mm(fid["voffset"]))[:nfid]):
        for bottom in (False, True):
            kikit_fp(panel, "Fiducial", f"KiKit_FID_{'B' if bottom else 'T'}_{i + 1}", x, y, bottom=bottom)

    pcbnew.ZONE_FILLER(panel).Fill(panel.Zones())
    pcbnew.SaveBoard(out, panel)
    with open(out) as fh:  # the panel doesn't fit the source's A4 sheet
        text = fh.read()
    with open(out, "w") as fh:
        fh.write(text.replace('(paper "A4")', '(paper "A3")', 1))
    pro = os.path.splitext(src)[0] + ".kicad_pro"
    if os.path.isfile(pro):  # KiKit copies the source project (design rules) next to the panel
        shutil.copyfile(pro, os.path.splitext(out)[0] + ".kicad_pro")
    for f in (os.path.splitext(out)[0] + ".kicad_prl",):
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree(lib, ignore_errors=True)
    print(f"panelize: {rows}x{cols} panel {pw:.2f} x {ph:.2f} mm, {len(tab_rects)} tabs, "
          f"{sum(map(len, bites))} mousebites, {nfid} fiducials per side -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
