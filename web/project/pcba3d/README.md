# 3D PCBA diff viewer (`web/project/pcba3d/`)

Shows a KiCad project's assembled board in 3D at base and head. It uses the GLBs that
`kicad-cli pcb export glb` writes (components included) and the component list in
`project.pcba3d` (docs/CONTRACT-project.md).

```js
import { mountPcba3d } from './pcba3d/index.js';
const h = await mountPcba3d(el, project, baseUrl, { baseLabel: 'Base · abc123', headLabel: 'Head · def456', mode: 'side' });
await h.ready;            // GLBs loaded (never rejects; problems are shown in the viewer and in h.errors)
h.setMode('overlay');     // 'side' | 'overlay' | 'highlight'
h.focus('U3');            // select in the list and both views, frame it
h.dispose();              // (alias: destroy) frees the WebGL context and empties el
```

- `el`: an element with a height. The module owns its contents until `dispose()`.
- `project`: one Project from `OUT/project-review.json`. The module reads `pcba3d`, `pcb.board`
  (including `board.base`/`board.head` if present), `project.errors` and `project.status`.
- `baseUrl`: the OUT directory's URL. Relative paths in the contract are resolved against it.
- Options: `{baseLabel, headLabel, mode, fabBoard}`. `fabBoard: false` keeps the GLB's own board.
  The handle also has `setBoardSource('gerber' | 'glb')`.
- The module takes its styles from the host's `--bg --panel --text --muted --border --accent --add --del`,
  with fallbacks, and follows `data-theme` on `<html>`. It loads `pcba3d.css` itself. It needs
  no importmap and no CDN: three.js is vendored in `vendor/` with relative imports. The gerber
  renderer is the project viewer's shared copy in `../vendor/wasm-gerber-renderer/` (see below).
- It is CSP-clean for the project viewer (`style-src 'self'`): no inline style attributes or
  `<style>` tags, only classes and `element.style`.

## What it does

- **Modes**:
  - *Side by side*: one canvas and one camera drawn into two viewports, so the two views
    always stay in sync.
  - *Overlay*: base in translucent red, head in translucent green, unchanged parts in neutral grey.
  - *Changes*: the head in its own colours, with changed parts tinted by status and ghosts
    where removed and moved parts used to be.
- **Change markers**: a box in the status colour around every changed part, so a moved 0402 is
  easy to find.
- **Change list**: filter chips per status, plus a text filter on ref, value and footprint. Click
  a row to select and frame the part; hover a row to highlight it. Hovering a part in 3D shows
  ref, status and base→head for value, footprint, side, position, rotation, model and DNP.
- **View**: top, bottom and iso presets, fit, and a view cube (click a face). The trackball has
  no poles, so the board turns over freely.
- **Toggles**: components, board (substrate, mask and copper), silk, and markers. An explode
  slider lifts parts and layers off the board.
- **Loading**: per-side progress bars. If one GLB is missing or broken, the other side still
  works and the error is shown. A project that is added or removed shows one side only.

## The board from the fab outputs (`gerberboard.js`, `boardgeom.js`)

When the project has `pcb.layers`, each side's board is rebuilt from its fab files and replaces
the GLB's board bodies. The **Fab / GLB** switch goes back to the GLB's bodies, and so does any
failure, which is reported in the status line.

- **Outline**: the fork's `outline.boardOutline()` stitches the Edge.Cuts strokes and arcs into
  the board loop and its cutouts (in mm). If a side has no usable outline, the contract's board
  box is used and the status line says so.
- **Solid**: the outline is extruded to `thickness_mm`, bottom face at z = 0 and top at the
  thickness, which is where kicad-cli mounts the parts. Holes from `PTH.drl`/`NPTH.drl`
  (`drills.parseExcellon`) are drilled through. Plated holes get copper barrels. A hole is only
  drilled if it clears the outline and the cutouts by its own radius. Only the 400 largest
  openings are drilled, because triangulation cost grows with the square of the hole count;
  the rest stay painted and the status line says how many. Ported from gentoo's
  `buildBoard`/`usableDrills`/`buildBarrels`/`planarUVs`/`splitCaps`.
- **Faces**: `board.renderFaceRaster()` paints each face as it comes back from the fab: laminate,
  copper, mask in the stackup's colour, finish on exposed copper, clipped silkscreen, and
  see-through holes. It uses one raster frame (the union of both sides' outlines) at
  24 px/mm, capped at 4096 px, with planar UVs over the same bounds.
- **Copper diff**: in *Overlay* and *Changes* the head board's faces show
  `diff.renderLayerDiff()`: removed copper red, added green, unchanged dim copper. That face's
  copper, the Edge.Cuts outline and the holes (`holesToGerber`) are rendered in the face
  raster's own `view`, so the two pictures share UVs. It is rendered on first use.
- **Outline changes**: when the base outline differs from the head's, it is drawn as a red edge
  on both faces in the overlaid modes. The copper diff also shows the outline's removed and
  added strokes.

Empty KiCad layers (a header-only `B_SilkS.gbr`, or an `NPTH.drl` with no holes) are skipped by
the renderer (fork PR #2); an empty mask is drawn as mask over the whole board.

### The shared gerber renderer

Both the layout viewer and this module import one vendored copy of our fork,
`web/project/vendor/wasm-gerber-renderer/`. It is owned by the project viewer (branch
claud/web-project), synced by `web/project/scripts/sync_vendored_renderer.bash`, and pinned to fork
main 9b7ade3 with the npm 0.6.0 WASM. This module uses `index` (`createGerberRenderer`, with
`wasmModule` and `wasmInitInput` passed explicitly so a bundle needs no dynamic import), `board`,
`diff`, `drills`, `layers`, `outline` and `raster`.

## Opening a report from disk (`file://`)

Browsers block ES modules and `fetch()` on `file://`. `build_offline.mjs` works around both,
following kicad-libs' component-review viewer:

```sh
node web/project/pcba3d/build_offline.mjs --out OUT   # needs npx (esbuild@0.28.2 is fetched once)
```

- It writes `pcba3d.bundle.js` (sets `window.KIPR_PCBA3D = {mountPcba3d}` for the shell) and
  `demo.bundle.js`. Both are classic IIFE scripts with `import.meta.url` replaced by the
  script's own URL, so relative assets resolve as before. They are git-ignored build output.
- It writes data packs in `OUT/offline/`:
  - `review.js`
  - `pcba3d-<slug>.js`: that project's GLBs as base64 and its fab files as text
  - `pcba3d-vendor.js`: the renderer's WASM

  All pack JSON is script-safe: `<`, `>`, `&`, U+2028 and U+2029 are escaped.
- `assets.js` reads through `fetch` over http, and through the packs on `file://`. Packs are
  loaded with `<script>` the first time a file from them is needed. `demo.html` boots with
  `demo-boot.js`: the module on http, the bundles and packs on `file://`.
- Cost: the packs repeat the GLBs at +33 %. pic_programmer's pack is 29 MB and the WASM pack
  is 1.3 MB. None of this is used over http.
- For the shell: on `file://`, load `pcba3d.bundle.js` plus the packs and call
  `window.KIPR_PCBA3D.mountPcba3d` instead of importing `index.js`.

## How meshes become refs (`match.js`, `scene.js`)

KiCad 10.0.6 GLBs (checked on the pic_programmer and complex_hierarchy demos) are in metres
with +Y up, x = KiCad x and z = KiCad y. Each component is a node named by its refdes, with an
opaque child mesh node. Board bodies are opaque nodes; their glTF mesh names give the kind
(`<board>_PCB`, `_pad`, `_silkscreen`, `_soldermask`).

`scene.js` works out the up axis (the board's thinnest axis) and the units from the file, unless
`pcba3d.frame` gives them. It classifies board bodies by name, then by size and colour. Then it
maps nodes to refs:

1. By name: exact, or with a copy suffix (`R5_1`, `R5 (2)`).
2. By position for the rest. It first fits the translation between the contract's KiCad
   coordinates and the GLB: the median offset from name matches, or a Hough vote over all
   (component, node) pairs. Then each component takes the nearest node, but only if that node is
   clearly nearer than the runner-up; pairs that are each other's nearest are settled after
   that. Ported from gentoo's `matchToDesignators`/`settleTies`.

This makes the export origin irrelevant (page, drill, grid or user origin), and base and head
line up even if they were exported with different origins. On the real pic_programmer GLB with
every name removed and the origin shifted, position matching still finds 56/56 modelled parts.
Parts with no 3D model (mounting holes, jumpers) are reported as such, not as failures.

For speed, each component's and each board layer's primitives are merged into one mesh per
material. kicad-cli writes about 7000 primitives for pic_programmer, which is ~7000 draw calls
per side before merging. Frames are only drawn when something changes.

## Prior art (credited in the code)

Camera handling (trackball without damping), lighting, the view cube and position matching are
ported from gentoo's `viewer3d.js`. The see-through two-colour comparison follows
`compare3d.js` (PantsForBirds/internal, branch john/gentoo, `fab/static/fab/`). The API shape
(`setMode`, groups, view presets) follows kicad-libs' `tools/component-review/viewer/js/view3d.js`.

## Develop and test

```sh
python3 -m http.server -d . 8000        # from the repository root; file:// cannot load modules/GLBs
# http://localhost:8000/web/project/pcba3d/demo.html?out=../../../tests/web-3d/out/mock/&project=demo
#   also: &mode=overlay|highlight &focus=R10 &theme=dark &explode=0.5, or type any OUT dir (relative URL)
sh tests/web-3d/run.sh                  # node unit tests (generates the mock OUT)
sh tests/web-3d/run.sh --real --browser # + real kicad-cli OUT + playwright screenshots (http and file://)
```

`tests/web-3d/mock/make_mock.mjs` writes synthetic KiCad-like GLBs: `demo` (every change kind),
`big` (480 parts), `unnamed` (no ref names, board-centre origin) and `newboard` (added
project). `tests/web-3d/real/make_real.py` exports base/head GLBs from any two `.kicad_pcb`
files, defaulting to KiCad's pic_programmer demo with scripted edits (including three deleted
tracks, so the copper diff has something red). It also exports the fab files (gerbers and drill
files, one layer per call) and writes the contract.

## Known gaps

- `file://` needs `build_offline.mjs` to run as part of building the report. Packs for boards with
  big GLBs are large.
- The silk toggle affects only the GLB's board. On the fab board the silkscreen is part of the
  face texture.
- Only the head board is drawn in the overlay modes. A changed board outline or copper shows in
  side-by-side, but is not diffed in 3D (the layout diff covers it).
- Parts are compared as whole parts: a model swapped for an identical-looking one shows only as
  `changed` in the list.
