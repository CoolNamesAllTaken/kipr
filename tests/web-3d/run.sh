#!/bin/sh
# All 3D PCBA viewer tests. Usage: sh tests/web-3d/run.sh [--real] [--browser]
#   (always)   node unit tests: matching, diff, GLB preparation (mock OUT is generated if missing)
#   --real     also export a real OUT with kicad-cli (pic_programmer demo + scripted edits, ~1 min)
#   --browser  playwright screenshots of every mode -> tests/web-3d/out/shots/ (needs python
#              playwright + chromium; set PYTHON to a venv's python, LD_LIBRARY_PATH if chromium
#              lacks system libs)
set -eu
cd "$(dirname "$0")/../.."
PYTHON=${PYTHON:-python3}
node tests/web-3d/mock/make_mock.mjs
for arg in "$@"; do
    [ "$arg" = --real ] && $PYTHON tests/web-3d/real/make_real.py
done
node --test 'tests/web-3d/*.test.mjs'
for arg in "$@"; do
    if [ "$arg" = --browser ]; then
        $PYTHON tests/web-3d/screenshots.py --extra-project big --extra-project unnamed --extra-project newboard
        if [ -f tests/web-3d/out/real/project-review.json ]; then
            $PYTHON tests/web-3d/screenshots.py --out tests/web-3d/out/real --project pic_programmer --prefix real- \
                --case side --case overlay --case highlight --case hover --case focus --case explode
        fi
    fi
done
