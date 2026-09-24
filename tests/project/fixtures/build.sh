#!/usr/bin/env bash
# Build the base/head fixture git repo for kipr's project-review integration tests from the KiCad
# demo projects, so no demo boards need to be committed to kipr.
#
#     bash tests/project/fixtures/build.sh DEST
#
# DEST (must not exist) becomes a git repo with tags `base` and `head`; point the tests at it with
# KIPR_FIXTURES=DEST. The expected base -> head changes are listed in CHANGES.md next to this file.
# It reproduces the local kipr-fixtures repo (same history and commit messages): the demos
# pic_programmer and complex_hierarchy, upgraded to the KiCad 10 format, a base commit with one
# seeded ERC issue and a silkscreen text, then scripts/head_*.py.
#
# Runs as is inside kicad/kicad:10.0.6-amd64-full (kicad-cli, python3 with pcbnew, demos in
# /usr/share/kicad/demos). Elsewhere set:
#   KICAD_CLI      kicad-cli                     (default: kicad-cli)
#   KICAD_PYTHON   a python3 that imports pcbnew (default: python3)
#   KICAD_SHARE    KiCad's share dir with demos/, footprints/, symbols/ (default: /usr/share/kicad)
# e.g. on the fleet: KICAD_CLI=kipr-tools/bin/kicad-cli KICAD_PYTHON=kipr-tools/bin/kicad-python
#                    KICAD_SHARE=kipr-tools/kicad10-rootfs/usr/share/kicad
set -euo pipefail
dest=${1:?usage: build.sh DEST}
here=$(cd "$(dirname "$0")" && pwd)
KICAD_CLI=${KICAD_CLI:-kicad-cli}
KICAD_PYTHON=${KICAD_PYTHON:-python3}
KICAD_SHARE=$(cd "${KICAD_SHARE:-/usr/share/kicad}" && pwd)
export KICAD10_FOOTPRINT_DIR=${KICAD10_FOOTPRINT_DIR:-$KICAD_SHARE/footprints}
PROJECTS=(pic_programmer complex_hierarchy)

if [ -e "$dest" ]; then echo "build.sh: $dest exists" >&2; exit 2; fi
for p in "${PROJECTS[@]}"; do
    [ -f "$KICAD_SHARE/demos/$p/$p.kicad_pro" ] || { echo "build.sh: no demo $p in $KICAD_SHARE/demos" >&2; exit 2; }
done
mkdir -p "$dest"
cd "$dest"
git init -q -b main
git config user.name "kipr fixtures"
git config user.email "kipr-fixtures@example.invalid"
# fixed dates: the same inputs give the same commit ids for the non-pcbnew steps
export GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z"
commit() { git add -A && git commit -q -m "$1"; }

# 1. the demos, verbatim
for p in "${PROJECTS[@]}"; do
    cp -r "$KICAD_SHARE/demos/$p" "$p"
    chmod -R u+w "$p"
done
printf '%s\n' '*.kicad_prl' '*-backups/' 'fp-info-cache' '*.lck' '_autosave-*' '__pycache__/' > .gitignore
commit "Import KiCad demo projects pic_programmer and complex_hierarchy (verbatim)"

# 2. KiCad 10 file format
for f in */*.kicad_sch; do "$KICAD_CLI" sch upgrade --force "$f" >/dev/null; done
for f in */*.kicad_pcb; do "$KICAD_CLI" pcb upgrade --force "$f" >/dev/null; done
commit "Normalize fixtures to the KiCad 10 file format (kicad-cli upgrade --force)"

# 3. re-save the boards with pcbnew; KiCad 10's stock 3D library has .step models only
for p in "${PROJECTS[@]}"; do
    "$KICAD_PYTHON" -c 'import sys, pcbnew; b = pcbnew.LoadBoard(sys.argv[1]); pcbnew.SaveBoard(sys.argv[1], b)' "$p/$p.kicad_pcb"
done
sed -i -E 's#(\(model "\$\{KICAD6_3DMODEL_DIR\}/[^"]*)\.wrl"#\1.step"#' complex_hierarchy/complex_hierarchy.kicad_pcb
commit "Re-save boards with KiCad 10 pcbnew; point complex_hierarchy 3D models at .step (stock libs ship no .wrl)"

# 4. base: one ERC issue (U4 pin 4 loses its no-connect flag) and a REV A silkscreen text
python3 - <<'EOF'
import re
pcb = "pic_programmer/pic_programmer.kicad_pcb"
t = open(pcb).read()
text = ('\t(gr_text "REV A"\n\t\t(at 143 136.5 0)\n\t\t(layer "F.SilkS")\n'
        '\t\t(uuid "c6b4ad11-d2a8-4fd6-bd23-e8f850b42993")\n'
        '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.5 1.5)\n\t\t\t\t(thickness 0.25)\n\t\t\t)\n\t\t)\n\t)\n')
i = t.index("\n\t(segment") + 1
open(pcb, "w").write(t[:i] + text + t[i:])
sch = "pic_programmer/pic_programmer.kicad_sch"
t = open(sch).read()
t, n = re.subn(r'\t\(no_connect\n\t\t\(at 171\.45 143\.51\)\n\t\t\(uuid "[^"]+"\)\n\t\)\n', "", t)
assert n == 1, "U4 pin 4 no-connect flag not found"
open(sch, "w").write(t)
EOF
commit "Base: seed an ERC issue (U4 pin 4 missing no-connect flag) and a 'REV A' silkscreen text in pic_programmer"
git tag base

# 5. head: the scripted edits (they run from the repo root and import scripts/sexpr.py)
mkdir scripts
cp "$here"/scripts/sexpr.py "$here"/scripts/head_pic_programmer_*.py scripts/
python3 scripts/head_pic_programmer_sch.py
"$KICAD_PYTHON" scripts/head_pic_programmer_pcb.py
commit "Head (pic_programmer): R7 value, C9 footprint, -P103, +C10, move D9, rotate D12, reroute D8-A, GND zone chamfer, J1 pin 9 to GND, REV B silk, restore U4 no-connect (ERC fix)"
cp "$here"/scripts/head_complex_hierarchy_sch.py scripts/
python3 scripts/head_complex_hierarchy_sch.py "$KICAD_SHARE/symbols/Device.kicad_sym"
commit "Head (complex_hierarchy): add status_led sheet (R401 + D401 LED, new net /status_led/LED_A); edit scripts"
cp "$here"/scripts/head_complex_hierarchy_pcb.py scripts/
"$KICAD_PYTHON" scripts/head_complex_hierarchy_pcb.py
commit "Head (complex_hierarchy): extend board outline +10.16 mm, place R401/D401, route VCC/GND/LED_A (LED_A 0.15 mm: one new track_width DRC violation)"
git tag head
echo "build.sh: fixtures in $dest (base $(git rev-parse --short base), head $(git rev-parse --short head))"
