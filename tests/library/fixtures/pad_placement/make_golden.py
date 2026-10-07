"""Regenerate golden.json: where KiCad itself puts each pad's copper and hole, and each 3D model.

    /workspace/projects/kipr-tools/bin/kicad-python tests/library/fixtures/pad_placement/make_golden.py

Needs KiCad's `pcbnew` Python module (KiCad 10). Values are KiCad footprint coordinates, mm, y down.
"""
import json
import os
import shutil
import tempfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
mm = pcbnew.ToMM


def main():
    out = {}
    tmp = tempfile.mkdtemp()
    try:
        lib = os.path.join(tmp, "fx.pretty")
        os.mkdir(lib)
        for f in sorted(os.listdir(HERE)):
            if f.endswith(".kicad_mod"):
                shutil.copy(os.path.join(HERE, f), lib)
        for f in sorted(os.listdir(lib)):
            name = f[:-len(".kicad_mod")]
            fp = pcbnew.FootprintLoad(lib, name)
            pads = []
            for p in fp.Pads():
                bb = p.GetEffectiveShape(pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu).BBox()
                drill = p.GetDrillSize()
                pads.append({
                    "number": p.GetNumber(),
                    "at": [mm(p.GetPosition().x), mm(p.GetPosition().y)],
                    "copper_center": [mm(p.ShapePos(pcbnew.F_Cu).x), mm(p.ShapePos(pcbnew.F_Cu).y)],
                    "copper_bbox": [mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())],
                    "hole_center": [mm(p.GetPosition().x), mm(p.GetPosition().y)] if drill.x else None,
                })
                if p.GetShape(pcbnew.F_Cu) == pcbnew.PAD_SHAPE_TRAPEZOID:
                    # bboxes can't tell rect_delta's sign apart: keep KiCad's own corners (GetEffectivePolygon)
                    ring = p.GetEffectivePolygon(pcbnew.F_Cu).Outline(0)
                    pads[-1]["copper_polygon"] = [[round(mm(ring.CPoint(i).x), 6), round(mm(ring.CPoint(i).y), 6)]
                                                  for i in range(ring.PointCount())]
            models = [{"offset": [m.m_Offset.x, m.m_Offset.y, m.m_Offset.z],
                       "rotate": [m.m_Rotation.x, m.m_Rotation.y, m.m_Rotation.z],
                       "scale": [m.m_Scale.x, m.m_Scale.y, m.m_Scale.z]} for m in fp.Models()]
            out[name] = {"pads": pads, "models": models}
    finally:
        shutil.rmtree(tmp)
    with open(os.path.join(HERE, "golden.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
        fh.write("\n")


if __name__ == "__main__":
    main()
