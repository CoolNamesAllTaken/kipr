"""Head schematic edits for complex_hierarchy: add a 4th sheet 'status_led' (R401 + D401 from
VCC to GND, creating the new net /status_led/LED_A). Run from the repo root on the base tree."""
import re, sys, uuid
sys.path.insert(0, 'scripts')
from sexpr import *

ROOT = 'complex_hierarchy/complex_hierarchy.kicad_sch'
AMPLI = 'complex_hierarchy/ampli_ht.kicad_sch'
NEW = 'complex_hierarchy/status_led.kicad_sch'
DEVICE = sys.argv[1]  # path to KiCad's stock Device.kicad_sym

U = lambda name: str(uuid.uuid5(uuid.NAMESPACE_URL, 'kipr-fixtures/complex_hierarchy/' + name))
root = open(ROOT).read()
ampli = open(AMPLI).read()
ROOT_UUID = re.search(r'\(uuid "([^"]+)"\)', root).group(1)
SHEET_UUID = U('sheet')
PATH = '/%s/%s' % (ROOT_UUID, SHEET_UUID)


def libsym(text, name):
    i = text.index('\t\t(symbol "%s"\n' % name)
    return '\t\t' + block_at(text, i + 2)


dev = open(DEVICE).read()
led = block_at(dev, dev.index('\n\t(symbol "LED"\n') + 2)
led = '\t\t' + led.replace('\n', '\n\t').replace('(symbol "LED"', '(symbol "Device:LED"', 1)
lib = '\n'.join([libsym(root, 'complex_hierarchy:GND'), libsym(ampli, 'complex_hierarchy:R'),
                 libsym(root, 'complex_hierarchy:VCC'), led])


def prop(name, value, x, y, hide=False, size=1.27):
    return '''		(property "%s" "%s"
			(at %s %s 0)%s
			(show_name no)
			(do_not_autoplace no)
			(effects
				(font
					(size %s %s)
				)
			)
		)''' % (name, value, fmt(x), fmt(y), '\n\t\t\t(hide yes)' if hide else '', size, size)


def symbol(lib_id, ref, value, fpid, x, y, pins, ref_at, val_at, power=False):
    key = ref.lstrip('#')
    props = [prop('Reference', ref, *ref_at, hide=power), prop('Value', value, *val_at),
             prop('Footprint', fpid, x, y, hide=True), prop('Datasheet', '', x, y, hide=True),
             prop('Description', '', x, y, hide=True)]
    pins = '\n'.join('\t\t(pin "%s"\n\t\t\t(uuid "%s")\n\t\t)' % (p, U(key + '/pin' + p)) for p in pins)
    return '''	(symbol
		(lib_id "%s")
		(at %s %s 0)
		(unit 1)
		(body_style 1)
		(exclude_from_sim no)
		(in_bom %s)
		(on_board %s)
		(in_pos_files %s)
		(dnp no)
		(uuid "%s")
%s
%s
		(instances
			(project "complex_hierarchy"
				(path "%s"
					(reference "%s")
					(unit 1)
				)
			)
		)
	)''' % (lib_id, fmt(x), fmt(y), *(['no' if power else 'yes'] * 3), U(key), '\n'.join(props), pins, PATH, ref)


def wire(name, x0, y0, x1, y1):
    return '''	(wire
		(pts
			(xy %s %s) (xy %s %s)
		)
		(stroke
			(width 0)
			(type solid)
		)
		(uuid "%s")
	)''' % (fmt(x0), fmt(y0), fmt(x1), fmt(y1), U(name))


# R401 vertical at (101.6, 64.77): pin 1 (101.6, 60.96) to VCC, pin 2 (101.6, 68.58)
# D401 horizontal at (97.79, 72.39): A (pin 2) at (101.6, 72.39), K (pin 1) at (93.98, 72.39)
items = [
    symbol('complex_hierarchy:VCC', '#PWR0401', 'VCC', '', 101.6, 60.96, ['1'], (101.6, 58.42), (101.6, 58.42), power=True),
    symbol('complex_hierarchy:R', 'R401', '1K', 'Resistor_THT:R_Axial_DIN0204_L3.6mm_D1.6mm_P7.62mm_Horizontal',
           101.6, 64.77, ['1', '2'], (104.14, 63.5), (104.14, 66.04)),
    symbol('Device:LED', 'D401', 'GREEN', 'LED_THT:LED_D3.0mm', 97.79, 72.39, ['1', '2'], (97.79, 68.58), (97.79, 76.2)),
    symbol('complex_hierarchy:GND', '#PWR0402', 'GND', '', 93.98, 76.2, ['1'], (93.98, 76.2), (93.98, 78.74), power=True),
    wire('w1', 101.6, 68.58, 101.6, 72.39),
    wire('w2', 93.98, 72.39, 93.98, 76.2),
    '''	(label "LED_A"
		(at 101.6 70.485 0)
		(effects
			(font
				(size 1.27 1.27)
			)
			(justify left bottom)
		)
		(uuid "%s")
	)''' % U('label'),
]
sheet = '''(kicad_sch
	(version 20260306)
	(generator "eeschema")
	(generator_version "10.0")
	(uuid "%s")
	(paper "A4")
	(title_block
		(title "Complex hierarchy: status LED")
		(rev "2")
	)
	(lib_symbols
%s
	)
%s
)
''' % (U('file'), lib, '\n'.join(items))
open(NEW, 'w').write(sheet)

# the sheet symbol on the root page, right of the two amplifier sheets
_, tmpl = next(top_blocks(root, 'sheet'))
blk = shift_at(tmpl, 218.44 - 71.12, 0)
blk = blk.replace('(size 50.8 36.83)', '(size 38.1 20.32)')
blk = blk.replace('(at 218.44 149.2001 0)', '(at 218.44 132.6801 0)')
blk = blk.replace('"ampli_ht_vertical"', '"status_led"').replace('"ampli_ht.kicad_sch"', '"status_led.kicad_sch"')
blk = re.sub(r'\(uuid "[^"]+"\)', '(uuid "%s")' % SHEET_UUID, blk, count=1)
blk = blk.replace('(page "2")', '(page "4")')
assert '(pin ' not in blk
root = insert_before(root, '\t(sheet_instances', '\t' + blk + '\n')
open(ROOT, 'w').write(root)
print(SHEET_UUID, U('R401'), U('D401'))
