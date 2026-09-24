"""Apply the head schematic edits to pic_programmer (run from the repo root on the base tree)."""
import sys
sys.path.insert(0, 'scripts')
from sexpr import *

P = 'pic_programmer/pic_programmer.kicad_sch'
s = open(P).read()

# 1. changed resistor value: R7 10K -> 4.7K
st, b = find_symbol(s, 'R7')
s = s[:st] + set_prop(b, 'Value', '4.7K') + s[st + len(b):]

# 2. changed footprint: C9 disc 5.1x3.2 -> 5.0x2.5 (same 5 mm pitch)
st, b = find_symbol(s, 'C9')
s = s[:st] + set_prop(b, 'Footprint', 'Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm') + s[st + len(b):]
c9 = b

# 3. removed component: mounting hole P103
st, b = find_symbol(s, 'P103')
s = remove_block(s, st, b)

# 4. added component: C10 100nF VCC/GND decoupling next to the mounting holes,
#    power symbols sit directly on the pin ends (no wires)
cx, cy = 185.42, 180.34
c10 = shift_at(c9, cx - 144.78, cy - 25.4)
c10 = set_ref(new_uuids(c10, 'kipr-fixtures/C10'), 'C9', 'C10')
c10 = set_prop(set_prop(c10, 'Value', '100nF'), 'Footprint', 'Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm')
_, vcc = find_symbol(s, '#PWR033')          # VCC at 87.63 31.75
_, gnd = find_symbol(s, '#PWR035')          # GND at 102.87 48.26
vcc10 = set_ref(new_uuids(shift_at(vcc, cx - 87.63, cy - 3.81 - 31.75), 'kipr-fixtures/PWR0140'), '#PWR033', '#PWR0140')
gnd10 = set_ref(new_uuids(shift_at(gnd, cx - 102.87, cy + 3.81 - 48.26), 'kipr-fixtures/PWR0141'), '#PWR035', '#PWR0141')

# 5. changed connection: J1 pin at (43.18, 99.06) was no-connect, now tied to GND
nc = '''	(no_connect
		(at 43.18 99.06)
		(uuid "8cbd52e7-5bc6-4f19-a7e5-ceccbda86f47")
	)
'''
assert nc in s
s = s.replace(nc, '')
gndj1 = set_ref(new_uuids(shift_at(gnd, 43.18 - 102.87, 99.06 - 48.26), 'kipr-fixtures/PWR0142'), '#PWR035', '#PWR0142')
gndj1 = gndj1.replace('(at 43.18 99.06 0)', '(at 43.18 99.06 90)', 1)

# 6. fixed ERC issue: restore the no-connect flag on U4 pin 4 (S/S)
nc_u4 = '''	(no_connect
		(at 171.45 143.51)
		(uuid "d4608387-9919-4315-81a5-c0c3cd57f062")
	)
'''

s = insert_before(s, '\t(sheet\n', nc_u4 + '\t' + '\n\t'.join([c10, vcc10, gnd10, gndj1]) + '\n')
open(P, 'w').write(s)
print('ok')
