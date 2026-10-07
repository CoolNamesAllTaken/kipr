#!/bin/bash
# Refresh web/vendor/: the third-party code both viewers (web/library, web/project) load at runtime,
# so they work offline and from file:// with no CDN. One copy each, shared through symlinks
# (web/library/vendor -> ../vendor, web/project/vendor/{boarddd,three} -> ../../vendor/...).
# A thin wrapper around boarddd's scripts/vendor.mjs (boarddd README, "Using boarddd in your project"):
#
#     bash web/vendor/sync_vendor.bash [--check] [BOARDDD_CHECKOUT]
#
#   boarddd         CoolNamesAllTaken/boarddd (MIT) at tag BOARDDD_REF: the subpaths below plus what they
#                   import (gerber brings the wasm renderer core under third_party/), bare `three` imports
#                   rewritten to ../three (the viewers' CSPs forbid an inline importmap). Taken with
#                   `git archive` from BOARDDD_CHECKOUT (default $BOARDDD or ../boarddd next to this repo;
#                   `git -C ../boarddd fetch --tags` first). vendor.mjs itself runs from the checkout at
#                   that tag too.
#   three           three.js THREE_VERSION (MIT), from the npm registry (sha512 checked).
#   occt-import-js  OCCT_VERSION (LGPL-2.1; OpenCascade LGPL-2.1 + exception), unmodified dist/*.js + .wasm.
#
# --check writes nothing and fails if any of the three directories differs (offline; kipr's CI runs it).
# Everything here is generated (VENDORED.json lists every file): bump a pin below and re-run.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo_root=$(cd "$here/../.." && pwd)
BOARDDD_REF=v0.3.3
BOARDDD_SUBPATHS=geom,board,footprint,models,scene,gerber,view2d
THREE_VERSION=0.185.1
OCCT_VERSION=0.0.23

check=()
if [ "${1:-}" = "--check" ]; then check=(--check); shift; fi
boarddd=${1:-${BOARDDD:-$repo_root/../boarddd}}

git -C "$boarddd" rev-parse --verify -q "$BOARDDD_REF^{commit}" >/dev/null \
    || { echo "sync_vendor: $boarddd has no $BOARDDD_REF (git -C $boarddd fetch --tags)" >&2; exit 2; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# run the vendor script of the pinned tag, whatever the checkout has checked out
git -C "$boarddd" archive "$BOARDDD_REF" scripts | tar -x -C "$tmp"
cd "$repo_root"
node "$tmp/scripts/vendor.mjs" "${check[@]}" --source "$boarddd" --ref "$BOARDDD_REF" \
    --out web/vendor/boarddd --subpaths "$BOARDDD_SUBPATHS" \
    --three "$THREE_VERSION" --three-dir web/vendor/three \
    --occt "$OCCT_VERSION" --occt-dir web/vendor/occt-import-js
