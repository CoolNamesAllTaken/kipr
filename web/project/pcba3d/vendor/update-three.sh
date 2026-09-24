#!/bin/sh
# Re-vendor three.js into ./three from the npm tarball. Usage: sh update-three.sh 0.185.1
#
# Only the files the 3D PCBA viewer needs are kept. The addons import the bare specifier
# 'three'; that one line is rewritten to a relative path so the module works without an
# importmap on the host page (the project viewer shell imports us, it should not have to know
# about our dependencies). Everything else is byte-identical to upstream.
set -eu
VERSION=${1:?three.js version}
HERE=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
(cd "$TMP" && npm pack "three@$VERSION" -q >/dev/null && tar xzf "three-$VERSION.tgz")
P="$TMP/package"
OUT="$HERE/three"
rm -rf "$OUT"
mkdir -p "$OUT/addons" "$OUT/utils"
cp "$P/LICENSE" "$P/build/three.module.js" "$P/build/three.core.js" "$OUT/"
cp "$P/examples/jsm/loaders/GLTFLoader.js" "$P/examples/jsm/controls/TrackballControls.js" "$OUT/addons/"
cp "$P/examples/jsm/utils/BufferGeometryUtils.js" "$P/examples/jsm/utils/SkeletonUtils.js" "$OUT/utils/"
for f in "$OUT"/addons/*.js "$OUT"/utils/*.js; do
    sed -i "s#from 'three';#from '../three.module.js';#" "$f"
done
echo "three.js $VERSION vendored into $OUT"
