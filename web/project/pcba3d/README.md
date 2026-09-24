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
- The module takes its styles from the host's `--bg --panel --text --muted --border --accent --add --del`,
  with fallbacks, and follows `data-theme` on `<html>`. It loads `pcba3d.css` itself. It needs
  no importmap and no CDN: three.js is vendored in `vendor/` with relative imports.

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
sh tests/web-3d/run.sh --real --browser # + real kicad-cli OUT + playwright screenshots
```

`tests/web-3d/mock/make_mock.mjs` writes synthetic KiCad-like GLBs: `demo` (every change kind),
`big` (480 parts), `unnamed` (no ref names, board-centre origin) and `newboard` (added
project). `tests/web-3d/real/make_real.py` exports base/head GLBs from any two `.kicad_pcb`
files, defaulting to KiCad's pic_programmer demo with scripted edits, and writes the contract.

## Known gaps

- `file://` (a downloaded report) is not supported: browsers block both ES modules and `fetch` there.
- Only the head board is drawn in the overlay modes. A changed board outline or copper shows in
  side-by-side, but is not diffed in 3D (the layout diff covers it).
- Parts are compared as whole parts: a model swapped for an identical-looking one shows only as
  `changed` in the list.
