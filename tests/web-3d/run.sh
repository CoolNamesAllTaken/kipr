#!/usr/bin/env bash
# All 3D PCBA viewer tests. Usage: sh tests/web-3d/run.sh [--real] [--browser]
#   (always)   node unit tests: matching, diff, board solid, offline packs, GLB preparation
#              (the mock OUT is generated if missing)
#   --real     also export a real OUT with kicad-cli: pic_programmer demo + scripted edits,
#              GLBs + gerbers + drills, ~2 min; and KiCad's royalblue54L_feather (base = head: SMD
#              paste, thermal vias) for the fab board's paste and filled-holes options, ~1 min
#   --browser  playwright screenshots of every mode -> tests/web-3d/out/shots/, over http and
#              (after build_offline.mjs) from file://. Uses kipr-tools' shared headless chromium
#              (bin/pw-env, pw-venv) when present; else set PYTHON to a python with playwright.
# The board-from-gerbers parts need boarddd vendored in web/vendor/boarddd (boarddd/gerber and its
# wasm, web/vendor/sync_vendor.bash); without it the viewer falls back to the GLB board.
set -eu
# kipr-tools' pw-env finds itself through BASH_SOURCE, so this runs under bash even as `sh run.sh`.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
cd "$(dirname "$0")/../.."
TOOLS=/workspace/projects/kipr-tools
if [ -z "${PYTHON:-}" ] && [ -x "$TOOLS/pw-venv/bin/python" ]; then
    PYTHON=$TOOLS/pw-venv/bin/python
    # shellcheck disable=SC1091
    . "$TOOLS/bin/pw-env"
fi
PYTHON=${PYTHON:-python3}
node tests/web-3d/mock/make_mock.mjs
for arg in "$@"; do
    if [ "$arg" = --real ]; then
        python3 tests/web-3d/real/make_real.py
        rb=/workspace/projects/kipr-tools/kicad10-rootfs/usr/share/kicad/demos/royalblue54L_feather/RoyalBlue54L-Feather.kicad_pcb
        rb=${ROYALBLUE_PCB:-$rb}
        if [ -f "$rb" ]; then
            python3 tests/web-3d/real/make_real.py --out tests/web-3d/out/real-smd --base-pcb "$rb" --head-pcb "$rb" --slug royalblue
        fi
    fi
done
node --test 'tests/web-3d/*.test.mjs'
for arg in "$@"; do
    if [ "$arg" = --browser ]; then
        $PYTHON tests/web-3d/screenshots.py --extra-project big --extra-project unnamed --extra-project newboard
        if [ -f tests/web-3d/out/real/project-review.json ]; then
            $PYTHON tests/web-3d/screenshots.py --out tests/web-3d/out/real --project pic_programmer --prefix real- \
                --case side --case overlay --case highlight --case hover --case focus --case explode \
                --case copper-diff --case glb-board --case bottom --case paste --case filled --case fab-ui
            if [ -f tests/web-3d/out/real-smd/project-review.json ]; then
                $PYTHON tests/web-3d/screenshots.py --out tests/web-3d/out/real-smd --project royalblue --prefix smd- \
                    --allow-missing --case side --case paste --case filled --case fab-ui
            fi
            node web/project/pcba3d/build_offline.mjs --out tests/web-3d/out/real
            $PYTHON tests/web-3d/screenshots.py --file --out tests/web-3d/out/real --project pic_programmer \
                --prefix file- --case side --case overlay
        fi
    fi
done
