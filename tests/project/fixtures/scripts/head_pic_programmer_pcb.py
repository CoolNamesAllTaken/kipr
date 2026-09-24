"""Apply the head PCB edits to pic_programmer. Run with kipr-tools/bin/kicad-python from the
repo root after head_pic_programmer_sch.py. pcbnew's save is byte-stable for this board."""
import os, sys, uuid
import pcbnew
from pcbnew import FromMM as M, VECTOR2I as V

P = 'pic_programmer/pic_programmer.kicad_pcb'
FPLIB = os.environ['KICAD10_FOOTPRINT_DIR']
# C10 found by a DRC-checked search along VCC tracks: next to U2's VCC pin, on its supply track
C10_POS = tuple(float(v) for v in os.environ.get('C10_POS', '115.57,106.172,0').split(','))
b = pcbnew.LoadBoard(P)
fp = b.FindFootprintByReference


def tracks(net):
    return [t for t in b.GetTracks() if t.GetNetname() == net]


def same(p, q):
    return abs(p.x - q.x) < 20000 and abs(p.y - q.y) < 20000


IO = pcbnew.PCB_IO_KICAD_SEXPR()   # keep alive: footprints it returns die with a temporary


def load_fp(lib, name):
    f = IO.FootprintLoad(os.path.join(FPLIB, lib + '.pretty'), name)
    f.SetFPID(pcbnew.LIB_ID(lib, name))
    return f


# 1. changed resistor value
fp('R7').SetValue('4.7K')

# 2. changed footprint: C9 C_Disc_D5.1mm_W3.2mm -> C_Disc_D5.0mm_W2.5mm (same pad positions)
old = fp('C9')
new = load_fp('Capacitor_THT', 'C_Disc_D5.0mm_W2.5mm_P5.00mm')
new.SetPosition(old.GetPosition()); new.SetOrientation(old.GetOrientation())
new.SetReference('C9'); new.SetValue(old.GetValue())
new.SetPath(old.GetPath()); new.SetSheetname(old.GetSheetname()); new.SetSheetfile(old.GetSheetfile())
for p in new.Pads():
    p.SetNet(old.FindPadByNumber(p.GetNumber()).GetNet())
new.Reference().SetPosition(old.Reference().GetPosition())
new.Value().SetPosition(old.Value().GetPosition())
b.Remove(old); b.Add(new)

# 3. removed component: mounting hole P103
b.Remove(fp('P103'))

# 4. added component C10 (VCC/GND); pad 1 sits on an existing VCC track, pad 2 in the GND pour
sch = open('pic_programmer/pic_programmer.kicad_sch').read()
c10_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'kipr-fixtures/C10/1'))
assert c10_uuid in sch
nets = {n: b.FindNet(n) for n in ('VCC', 'GND')}
c10 = load_fp('Capacitor_THT', 'C_Disc_D5.0mm_W2.5mm_P5.00mm')
c10.SetReference('C10'); c10.SetValue('100nF')
c10.SetPath(pcbnew.KIID_PATH('/' + c10_uuid)); c10.SetSheetname('/'); c10.SetSheetfile('pic_programmer.kicad_sch')
x, y, rot = C10_POS
c10.SetPosition(V(M(x), M(y))); c10.SetOrientationDegrees(rot)
c10.FindPadByNumber('1').SetNet(nets['VCC'])
c10.FindPadByNumber('2').SetNet(nets['GND'])
b.Add(c10)

# 5. moved footprint: D9 1.27 mm left; its anode track end follows the pad
d9 = fp('D9'); a_old = d9.FindPadByNumber('2').GetPosition()
d9.Move(V(M(-1.27), 0)); a_new = d9.FindPadByNumber('2').GetPosition()
for t in tracks('Net-(D9-A)'):
    if same(t.GetStart(), a_old): t.SetStart(a_new)
    if same(t.GetEnd(), a_old): t.SetEnd(a_new)

# 6. rotated footprint: D12 rotated 90 deg about its anode pad (anode track unchanged)
d12 = fp('D12'); a = d12.FindPadByNumber('2').GetPosition()
ref_pos = d12.Reference().GetPosition()
d12.Rotate(a, pcbnew.EDA_ANGLE(90.0, pcbnew.DEGREES_T))
d12.Reference().SetPosition(ref_pos)       # keep the designator clear of D9's silk

# 7. rerouted track: Net-(D8-A) straight bottom segment becomes a dogleg
(t,) = tracks('Net-(D8-A)')
s, e = t.GetStart(), t.GetEnd()            # (153.67,77.47) -> (149.225,77.47)
pts = [s, V(s.x - M(1.905), s.y - M(1.905)), V(e.x + M(0.635), e.y - M(1.905)), e]
for p0, p1 in zip(pts, pts[1:]):
    n = pcbnew.PCB_TRACK(b); n.SetStart(p0); n.SetEnd(p1); n.SetWidth(t.GetWidth())
    n.SetLayer(t.GetLayer()); n.SetNet(t.GetNet()); b.Add(n)
b.Remove(t)

# 8. modified zone: larger chamfer on the GND pour's top-left corner
(z,) = [z for z in b.Zones() if z.GetNetname() == 'GND']
o = z.Outline()
for i in range(o.TotalVertices()):
    v = o.CVertex(i)
    if (round(pcbnew.ToMM(v.x), 3), round(pcbnew.ToMM(v.y), 3)) == (81.28, 41.91):
        o.SetVertex(i, V(M(88.9), M(41.91)))
    elif (round(pcbnew.ToMM(v.x), 3), round(pcbnew.ToMM(v.y), 3)) == (74.295, 48.895):
        o.SetVertex(i, V(M(74.295), M(55.88)))

# 9. changed connection: J1 pad 9 (was unconnected) now GND
fp('J1').FindPadByNumber('9').SetNet(nets['GND'])

# 10. changed silkscreen text
(txt,) = [d for d in b.GetDrawings() if isinstance(d, pcbnew.PCB_TEXT) and d.GetText() == 'REV A']
txt.SetText('REV B')

pcbnew.ZONE_FILLER(b).Fill(b.Zones())
out = sys.argv[1] if len(sys.argv) > 1 else P
b.Save(out)
if len(sys.argv) > 2:
    pcbnew.WriteDRCReport(b, sys.argv[2], pcbnew.EDA_UNITS_MM, True)
