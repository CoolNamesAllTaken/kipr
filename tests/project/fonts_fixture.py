"""Shared fixtures for the font tests: a synthetic board repo and a file:// Google Fonts mirror.

The board has a gr_text 'PE4302' in Poppins Bold 0.22 mm inside the Edge.Cuts line (the case
from a real PR): with Poppins installed DRC is clean, with KiCad's substitute (DejaVu Sans Bold)
the text crosses the edge and DRC reports silk_edge_clearance. tests/project/fixtures/fonts holds
Poppins-Bold.ttf and its OFL license (google/fonts ofl/poppins).
"""

import base64
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fixtures", "fonts")
POPPINS_BOLD = os.path.join(FONTS, "Poppins-Bold.ttf")

TEXT = '''	(gr_text "PE4302"
		(at 38.7 62.3 90)
		(layer "F.SilkS")
		(uuid "bbecef3e-27d2-4093-801d-5e287bbc9f1b")
		(effects
			(font
				(face "{face}")
				(size 1 1)
				(thickness 0.2)
				(bold yes)
			)
		)
	)
'''

BOARD = '''(kicad_pcb
	(version 20241229)
	(generator "kipr-test")
	(general
		(thickness 1.6)
	)
	(paper "A4")
	(layers
		(0 "F.Cu" signal)
		(2 "B.Cu" signal)
		(5 "F.SilkS" user "F.Silkscreen")
		(25 "Edge.Cuts" user)
	)
	(setup
		(pad_to_mask_clearance 0)
	)
	(net 0 "")
	(gr_rect
		(start 30 50)
		(end 45 65)
		(stroke
			(width 0.05)
			(type default)
		)
		(fill no)
		(layer "Edge.Cuts")
		(uuid "11111111-1111-4111-8111-111111111111")
	)
{text}{embedded})
'''

PRO = '{\n  "meta": {\n    "filename": "fonts_test.kicad_pro",\n    "version": 3\n  }\n}\n'


def board(face="Poppins", text=True, embed: bytes | None = None, embed_name="Poppins-Bold.ttf") -> str:
    emb = ""
    if embed is not None:
        data = base64.b64encode(embed).decode()
        emb = (f'\t(embedded_fonts no)\n\t(embedded_files\n\t\t(file\n\t\t\t(name "{embed_name}")\n\t\t\t(type font)\n'
               f'\t\t\t(data |{data}|)\n\t\t)\n\t)\n')
    return BOARD.format(text=TEXT.format(face=face) if text else "", embedded=emb)


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def make_repo(path, head_board: str) -> str:
    """Repo with base = the board without text, head = `head_board` (tags base / head)."""
    os.makedirs(os.path.join(path, "fonts_test"), exist_ok=True)
    git(path, "init", "-q")
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(path, "config", k, v)
    with open(os.path.join(path, "fonts_test", "fonts_test.kicad_pro"), "w") as fh:
        fh.write(PRO)
    for tag, text in (("base", board(text=False)), ("head", head_board)):
        with open(os.path.join(path, "fonts_test", "fonts_test.kicad_pcb"), "w") as fh:
            fh.write(text)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", tag)
        git(path, "tag", tag)
    return path


def make_mirror(path, license="OFL", files=("Poppins-Bold.ttf",)) -> str:
    """A google/fonts-like tree with ofl/poppins (METADATA.pb, OFL.txt, the fixture font); returns
    its file:// URL."""
    d = os.path.join(path, "ofl", "poppins")
    os.makedirs(d, exist_ok=True)
    for f in files:
        shutil.copyfile(POPPINS_BOLD, os.path.join(d, f))
    shutil.copyfile(os.path.join(FONTS, "OFL.txt"), os.path.join(d, "OFL.txt"))
    fonts = "".join(f'fonts {{\n  name: "Poppins"\n  style: "normal"\n  weight: 700\n  filename: "{f}"\n'
                    f'  post_script_name: "Poppins-Bold"\n  full_name: "Poppins Bold"\n}}\n' for f in files)
    with open(os.path.join(d, "METADATA.pb"), "w") as fh:
        fh.write(f'name: "Poppins"\ndesigner: "Indian Type Foundry"\nlicense: "{license}"\ncategory: "SANS_SERIF"\n'
                 + fonts + 'subsets: "latin"\n')
    return "file://" + os.path.abspath(path)
