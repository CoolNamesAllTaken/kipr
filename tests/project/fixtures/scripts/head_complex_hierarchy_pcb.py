"""Head PCB edits for complex_hierarchy. Run with kipr-tools/bin/kicad-python from the repo
root after head_complex_hierarchy_sch.py.

pcbnew's save reorders this board's tracks on every save (not byte-stable), so the edits are
made with pcbnew on a temporary copy and then only the new/changed top-level blocks (matched by
uuid) are transplanted into the original text. That keeps the base..head diff minimal.
"""
import os, re, sys, tempfile, uuid
import pcbnew
from pcbnew import FromMM as M, VECTOR2I as V

sys.path.insert(0, 'scripts')
from sexpr import block_at

P = 'complex_hierarchy/complex_hierarchy.kicad_pcb'
U = lambda name: str(uuid.uuid5(uuid.NAMESPACE_URL, 'kipr-fixtures/complex_hierarchy/' + name))
FP = os.environ['KICAD10_FOOTPRINT_DIR']
IO = pcbnew.PCB_IO_KICAD_SEXPR()   # keep alive: footprints it returns die with a temporary

b = pcbnew.LoadBoard(P)

# 1. board outline change: right edge moves from x=188.595 to x=198.755 (+10.16 mm)
for d in b.GetDrawings():
    if d.GetLayer() == pcbnew.Edge_Cuts:
        for get, set_ in ((d.GetStart, d.SetStart), (d.GetEnd, d.SetEnd)):
            p = get()
            if abs(pcbnew.ToMM(p.x) - 188.595) < 1e-3:
                set_(V(M(198.755), p.y))

# 2. footprints of the new status_led sheet, in the new strip of board
led_a = pcbnew.NETINFO_ITEM(b, '/status_led/LED_A'); b.Add(led_a)
VCC, GND = b.FindNet('VCC'), b.FindNet('GND')


def add_fp(lib, name, ref, value, x, y, rot, nets):
    f = IO.FootprintLoad(os.path.join(FP, lib + '.pretty'), name)
    f.SetFPID(pcbnew.LIB_ID(lib, name))
    f.SetReference(ref); f.SetValue(value)
    f.SetPath(pcbnew.KIID_PATH('/%s/%s' % (U('sheet'), U(ref))))
    f.SetSheetname('/status_led/'); f.SetSheetfile('status_led.kicad_sch')
    f.SetPosition(V(M(x), M(y))); f.SetOrientationDegrees(rot)
    for num, n in nets.items():
        f.FindPadByNumber(num).SetNet(n)
    b.Add(f)


add_fp('Resistor_THT', 'R_Axial_DIN0204_L3.6mm_D1.6mm_P7.62mm_Horizontal', 'R401', '1K',
       193.04, 58.42, -90, {'1': VCC, '2': led_a})
add_fp('LED_THT', 'LED_D3.0mm', 'D401', 'GREEN', 193.04, 72.39, 90, {'1': GND, '2': led_a})


def track(pts, layer, net, w=0.6096):
    for a, c in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b); t.SetStart(V(M(a[0]), M(a[1]))); t.SetEnd(V(M(c[0]), M(c[1])))
        t.SetWidth(M(w)); t.SetLayer(layer); t.SetNet(net); b.Add(t)


# 3. new tracks. VCC from U102 pin 8 along the top of the board to R401 (top copper, through the
#    GND pour, which is refilled); GND from D401 to P302 pin 2 (bottom copper).
track([(145.41, 64.52), (145.41, 54.61), (193.04, 54.61), (193.04, 58.42)], pcbnew.F_Cu, VCC)
track([(193.04, 72.39), (193.04, 78.74), (190.5, 81.28), (181.2, 81.3)], pcbnew.B_Cu, GND)
# 4. the one new DRC violation: LED_A track is 0.15 mm, below the board's 0.2032 mm minimum
track([(193.04, 66.04), (193.04, 69.85)], pcbnew.B_Cu, led_a, w=0.15)

pcbnew.ZONE_FILLER(b).Fill(b.Zones())
tmp = tempfile.mkdtemp()
b.Save(os.path.join(tmp, 'x.kicad_pcb'))
new = open(os.path.join(tmp, 'x.kicad_pcb')).read()

# ---- transplant into the original text ----
orig = open(P).read()


def blocks(s):
    """top-level blocks keyed by their own uuid"""
    out = {}
    for m in re.finditer(r'\n\t\((\w+)[\s\n]', s):
        blk = block_at(s, m.start() + 2)
        u = re.search(r'\n\t\t\(uuid "([^"]+)"\)', blk)
        if u:
            out[u.group(1)] = (m.group(1), blk)
    return out


ob, nb = blocks(orig), blocks(new)
changed = added = 0
for u, (kind, blk) in nb.items():
    if u in ob:
        if ob[u][1] != blk:
            assert kind in ('gr_line', 'zone'), (kind, u)
            orig = orig.replace(ob[u][1], blk, 1); changed += 1
    else:
        assert kind in ('footprint', 'segment'), (kind, u)
        i = orig.rindex('\n)')
        orig = orig[:i] + '\n\t' + blk + orig[i:]; added += 1
assert set(ob) <= set(nb), 'items disappeared'
open(P, 'w').write(orig)
print('changed', changed, 'added', added)
