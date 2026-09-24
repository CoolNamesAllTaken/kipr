#!/bin/bash
# Every test of the project viewer, site builder and report.
#
#     bash tests/web-project/run_all.sh [--quick] [--real OUT]
#
# Needs node 22, Python 3.11 (+ Pillow and cairosvg for the report images) and, for the browser
# tests, playwright (`npm install` in this directory) with a Chromium that can start. On the fleet
# container `source /workspace/projects/kipr-tools/bin/pw-env` provides the libraries.
# Screenshots go to $SHOTS (default: a temp dir, printed at the end).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
quick=""
real=""
while [ $# -gt 0 ]; do
    case "$1" in
        --quick) quick="--quick" ;;
        --real) real="$2"; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done
work=$(mktemp -d)
shots=${SHOTS:-$work/shots}
mock="$work/mock"

echo "== node unit tests"
node --test "$here/unit.test.mjs"
echo "== python: site builder + report"
(cd "$root" && python3 -m unittest discover -s tests/web-project -p 'test_*.py')
echo "== mock OUT + site + report"
python3 "$here/make_mock.py" --out "$mock" --site
(cd "$root" && python3 -m kipr.project.report --out "$mock")
echo "== screenshots (http)"
node "$here/screens.mjs" --site "$mock" --shots "$shots/http" $quick
echo "== screenshots (file://, like an unzipped artifact)"
node "$here/screens.mjs" --site "$mock" --shots "$shots/file" --mode file --quick
echo "== xss"
node "$here/xss_check.mjs" --site "$mock" --mode both
if [ -n "$real" ]; then
    echo "== real OUT: $real (copied)"
    cp -r "$real" "$work/real"
    (cd "$root" && python3 -m kipr.project.site --out "$work/real" && python3 -m kipr.project.report --out "$work/real")
    node "$here/screens.mjs" --site "$work/real" --shots "$shots/real" $quick
fi
echo "all tests passed; screenshots in $shots"
