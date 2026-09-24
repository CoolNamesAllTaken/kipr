# Vendored third-party code

Everything the 3D PCBA viewer loads at runtime lives here, so it works offline and needs no CDN.

## three.js 0.185.1 (`three/`)

MIT, see `three/LICENSE`. From the npm tarball `three@0.185.1` (<https://github.com/mrdoob/three.js>).
Only what the viewer uses: `three.module.js` and the `three.core.js` it re-exports,
`addons/GLTFLoader.js`, `addons/TrackballControls.js`, and the two utils GLTFLoader imports
(`utils/BufferGeometryUtils.js`, `utils/SkeletonUtils.js`; the viewer also uses
`mergeGeometries` from the former).

One line in each addon/util is changed: `from 'three'` becomes `from '../three.module.js'`, so
the module needs no importmap on the page that mounts it. Re-vendor with
`sh update-three.sh <version>` (it downloads, copies and applies that rewrite). Nothing else is
modified. The same release is vendored in gentoo (PantsForBirds/internal, branch john/gentoo,
`fab/static/fab/vendor/three`).

occt-import-js is not needed: kicad-cli's GLB already contains the component models as meshes.
