#!/bin/bash
# Refresh web/vendor/: the third-party code both viewers (web/library, web/project) load at runtime,
# so they work offline and from file:// with no CDN. One copy each, shared through symlinks
# (web/library/vendor -> ../vendor, web/project/vendor/{boarddd,three} -> ../../vendor/...).
#
#     bash web/vendor/sync_vendor.bash [all|boarddd|three|occt] [BOARDDD_CHECKOUT] [BOARDDD_REF]
#
#   boarddd         3D PCB library (CoolNamesAllTaken/boarddd), MIT: src/ at the pinned commit below,
#                   exported with `git archive` from BOARDDD_CHECKOUT (default $BOARDDD or ../boarddd next
#                   to this repo; fetch first if the commit is missing). Its bare `three` imports are
#                   rewritten to relative paths into ../three, so no page needs an importmap (the viewers'
#                   CSPs forbid inline scripts, and an importmap is one).
#   three           three.js THREE_VERSION (MIT) from the npm tarball: three.module.js + three.core.js and
#                   the addons boarddd uses, in upstream's examples/jsm layout under addons/; the same
#                   one-line `from 'three'` rewrite.
#   occt-import-js  OCCT_VERSION (LGPL-2.1; OpenCascade LGPL-2.1 + exception) from the npm tarball:
#                   dist/occt-import-js.js + .wasm, unmodified. The library viewer passes their URLs to
#                   boarddd's STEP loader ({occt: {js, wasm}}).
# Everything here is generated: do not edit it by hand; bump a pin below and re-run.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo_root=$(cd "$here/../.." && pwd)
what=${1:-all}
# pinned: boarddd main 5dc205f (PRs #1-#5: incl. panes, footprint decals, preserveDrawingBuffer, stepToObject fix)
BOARDDD_PINNED=5dc205f
THREE_VERSION=0.185.1
OCCT_VERSION=0.0.23
boarddd=${2:-${BOARDDD:-$repo_root/../boarddd}}
ref=${3:-$BOARDDD_PINNED}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# `from 'three'` / `from 'three/addons/x/y.js'` -> relative paths from a file at depth $2 below web/vendor/$3
rewrite_three() {
    local file=$1 up=$2
    sed -i -e "s#from 'three';#from '${up}three/three.module.js';#" \
           -e "s#from 'three/addons/#from '${up}three/addons/#" "$file"
}

sync_three() {
    (cd "$tmp" && npm pack --silent "three@$THREE_VERSION" >/dev/null && tar -xzf "three-$THREE_VERSION.tgz")
    local p="$tmp/package" out="$here/three"
    rm -rf "$out.new"
    mkdir -p "$out.new"
    cp "$p/LICENSE" "$p/build/three.module.js" "$p/build/three.core.js" "$out.new/"
    # what boarddd imports, plus what those import (GLTFLoader -> BufferGeometryUtils, SkeletonUtils)
    for f in controls/TrackballControls.js controls/OrbitControls.js loaders/GLTFLoader.js \
             environments/RoomEnvironment.js utils/BufferGeometryUtils.js utils/SkeletonUtils.js; do
        mkdir -p "$out.new/addons/$(dirname "$f")"
        cp "$p/examples/jsm/$f" "$out.new/addons/$f"
        sed -i "s#from 'three';#from '../../three.module.js';#" "$out.new/addons/$f"
    done
    rm -rf "$out"
    mv "$out.new" "$out"
    rm -rf "$p"
    echo "three.js $THREE_VERSION -> $out"
}

sync_occt() {
    (cd "$tmp" && npm pack --silent "occt-import-js@$OCCT_VERSION" >/dev/null && tar -xzf "occt-import-js-$OCCT_VERSION.tgz")
    local p="$tmp/package" out="$here/occt-import-js"
    rm -rf "$out"
    mkdir -p "$out/dist"
    cp "$p/dist/occt-import-js.js" "$p/dist/occt-import-js.wasm" "$out/dist/"
    cp "$p/LICENSE"* "$out/" 2>/dev/null || cp "$p/license"* "$out/"
    rm -rf "$p"
    echo "occt-import-js $OCCT_VERSION -> $out"
}

sync_boarddd() {
    git -C "$boarddd" rev-parse --verify "$ref^{commit}" >/dev/null
    local commit out="$here/boarddd"
    commit=$(git -C "$boarddd" rev-parse --short "$ref^{commit}")
    mkdir -p "$tmp/boarddd"
    git -C "$boarddd" archive "$ref" src LICENSE README.md package.json | tar -x -C "$tmp/boarddd"
    rm -rf "$out.new"
    mkdir -p "$out.new"
    cp -r "$tmp/boarddd/src" "$tmp/boarddd/LICENSE" "$tmp/boarddd/README.md" "$out.new/"
    # src/<module>/<file>.js: web/vendor/three is three levels up
    find "$out.new/src" -name '*.js' | while read -r f; do rewrite_three "$f" ../../../; done
    if grep -rn "from 'three" "$out.new/src" --include='*.js'; then
        echo "sync_vendor: bare three imports left in boarddd" >&2; exit 1
    fi
    echo "$commit" > "$out.new/COMMIT"
    rm -rf "$out"
    mv "$out.new" "$out"
    echo "boarddd $commit ($ref) -> $out"
}

case "$what" in
    all) sync_three; sync_occt; sync_boarddd ;;
    boarddd) sync_boarddd ;;
    three) sync_three ;;
    occt) sync_occt ;;
    *) echo "usage: sync_vendor.bash [all|boarddd|three|occt] [BOARDDD_CHECKOUT] [BOARDDD_REF]" >&2; exit 2 ;;
esac
