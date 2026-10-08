# Project review data contract (v1)

`kipr project --repo R --base B --head H --out OUT` writes everything a viewer or report needs
into `OUT/`. The viewer and report read only `OUT/`; they never run KiCad. All paths inside the
JSON are relative to `OUT/`, use `/`, and are safe to serve statically. The backend owner
(`kipr/project/`) may extend this; any change must be reflected here in the same commit.

Conventions used everywhere below:

- **Coordinates** (`*_mm`, `x`, `y`, `pos_mm`, `bbox_mm`) are millimetres in KiCad's frame
  (y grows **down**): board mm for PCB data, sheet mm for schematic data.
- **Boxes** `bbox_mm` are `[x, y, w, h]` with `(x, y)` the top-left corner. Schematic boxes are
  padded by 0.5 mm so they can be drawn as highlights directly.
- **Sides** are `"base"` and `"head"`. A side is `null` when the thing does not exist there
  (project/sheet/layer added or removed, or an export failed; failures are listed in `errors`).
- **Statuses** are `added | removed | modified | unchanged` unless a section says otherwise.
- Every list may be empty; every optional key may be absent or `null`. Readers must ignore
  keys they don't know (the backend adds diagnostics such as `timings_s`, `exports`).

## Files in `OUT/`

```
project-review.json
p/<slug>/sch/{base,head}/<sheet id>.svg      kicad-cli schematic SVG per sheet (id "root/power" -> sch/base/root/power.svg)
p/<slug>/pcb/{base,head}/<Layer>.gbr         Gerber X2 per enabled layer, name = layer id with "." -> "_" (F_Cu.gbr)
p/<slug>/pcb/{base,head}/<Layer>.svg         kicad-cli per-layer SVG (page mode, no drawing sheet)
p/<slug>/pcb/{base,head}/PTH.drl, NPTH.drl   Excellon drill, plated / non-plated, mm, absolute origin
p/<slug>/pcb/{base,head}/board.gbrjob        Gerber job file (its FilesAttributes[].Path point at the renamed files)
p/<slug>/pcb/{base,head}/pos.csv             placement file (kicad-cli csv, mm, both sides)
p/<slug>/3d/{base,head}.glb                  board + component models (optional: .step with --step)
p/<slug>/bom/{base,head}.csv                 kicad-cli BOM, grouped by Value/Footprint/MPN/DNP
p/<slug>/netlist/{base,head}.net             kicad-cli netlist (kicadsexpr)
p/<slug>/checks/{erc,drc}.{base,head}.json   raw kicad-cli ERC/DRC reports (--severity-all)
```

### Geometry of the exported files

- **Gerbers and drill files** are plotted in absolute KiCad coordinates without the aux/drill
  origin, so gerber `(x, y)` = `(kicad_x, -kicad_y)`. `pcb.board.gerber_origin_mm` states the KiCad
  coordinates of gerber `(0, 0)` and is always `[0, 0]` today.
- **PCB SVGs** are plotted in page mode (`--page-size-mode 0 --exclude-drawing-sheet`): the
  `viewBox` is in mm and equals KiCad board coordinates (e.g. `viewBox="0 0 297.0022 210.0072"`
  for an A4 page; an Edge.Cuts corner at (87.9, 51.8) is at `M87.9000 51.8000`).
- **Schematic SVGs** are the kicad-cli page with drawing sheet; `viewBox` mm = sheet coordinates,
  the size is `sheets[].size_mm`.
- **GLB** (`pcba3d.frame`): metres, glTF +Y up, exported with `--user-origin 0x0mm`, so
  `x_m = kicad_x / 1000`, `z_m = kicad_y / 1000`, `y_m` = height above the bottom of the board.
  Each component is a node named by its reference designator (`C1`, `U6`, …) with an opaque child
  mesh node; board bodies are unnamed nodes whose meshes are named `<board>_PCB`, `_pad`,
  `_silkscreen`, `_soldermask`. VRML models are substituted by same-named STEP models.

## `OUT/project-review.json`

```jsonc
{
  "version": 1,
  "tool": {"name": "kipr", "version": "0.1.0", "kicad": "10.0.6",    // kicad: null without kicad-cli
           "stock_3d_models": true,     // KiCad's stock 3D library was found (for the model fallback); null without kicad-cli
           "significant_fields": ["mpn", "lcsc*", …],   // the field patterns in effect (see "Change classification")
           "grid_check": "changed", "sch_grid_mil": 50.0},   // --grid-check, --sch-grid-mil (see "Grid")
  "base": {"sha": "…", "ref": "main", "short": "abc1234"},
  "head": {"sha": "…", "ref": "feature", "short": "def5678"},
  "repo": {"url": "https://github.com/o/r", "blob": "https://github.com/o/r/blob/{sha}/{path}"},  // nulls if not GitHub
  "projects": [ /* Project, one per changed .kicad_pro directory */ ],
  "fonts": Fonts | null,                                              // null without kicad-cli exports
  "errors": ["kicad-cli not found: …"]                                 // run-level problems
}
```

A project is included when a file it loads changed between base and head: its `.kicad_pro`,
`.kicad_sch` (any sheet), `.kicad_pcb`, `.kicad_dru`, `sym-lib-table`/`fp-lib-table`, project-local
libraries (`.kicad_sym`, `.pretty/*.kicad_mod`) or 3D models inside its directory, and libraries or
3D models outside it that the lib tables / board reference through relative or `${KIPRJMOD}` paths.
Like the old kiri workflow, paths under `.history/`, `*-backups/` and `panelized/` are ignored.
`--projects GLOB` (repeatable) keeps only projects whose directory or directory name matches.

### Project

```jsonc
{
  "slug": "adsbee_1090u",              // unique, [a-z0-9_-], names the OUT/p/<slug>/ dir
  "name": "adsbee_1090u",              // .kicad_pro stem (a board without one: its .kicad_pcb stem)
  "path": "projects/adsbee/kicad/adsbee_1090u",   // dir of the .kicad_pro, repo-relative ("" = repo root)
  "status": "modified",                // added | removed | modified
  "kind": "project",                   // project (has a schematic) | panel | board (no schematic); see Panel
  "panel": Panel | null,               // kind "panel" only
  "reasons": ["projects/…/adsbee_1090u.kicad_pcb"],   // changed files that made it count
  "summary": {"sheets_changed": 2, "layers_changed": 5,   // sheets_changed leaves out sheets_moved
              "sheets_moved": 1, "sch_moved": 14,   // sheets whose changes all only move things (moved_only); move_only changes
              "components": {"added": 1, "removed": 0, "moved": 3, "changed": 2,   // from the board (moved includes rotated); from the BOM if there is no board
                             "minor": 105},  // minor: true components (see PcbChange); not in "changed"
              "nets_changed": 4,
              "erc": {"new": 0, "fixed": 1}, "drc": {"new": 2, "fixed": 0},       // null when the check could not run
              "grid": {"count": 1, "points": 23},    // checks.grid count/points; null when off or no schematic
              "impedance": {"rows": 3, "violations": 1, "length_out_mm": 2.8,   // checks.impedance.count + solver; null without a board
                            "new_violations": 1, "stackup_shifts": 0, "width_changes": 1, "solver": "field"},
              "fonts_missing": 1,                    // len(fonts.missing)
              "panel": {"fiducial": 2, "tooling": 0, "mousebites": 1, "tabs": 1, "frame": 0}},   // panel changes by kind (non-zero only); null unless kind "panel"
  "schematic": Schematic | null,
  "pcb": Pcb | null,
  "pcba3d": Pcba3d | null,
  "bom": Bom | null,
  "netlist": Netlist | null,
  "checks": {"erc": CheckDelta | null, "drc": CheckDelta | null, "grid": Grid | null, "impedance": Impedance | null},
  "fonts": {"faces": ["Poppins"], "missing": ["Poppins"],   // faces this project uses / kicad-cli didn't have
            "warning": "Font 'Poppins' is not available in CI; …"} | null,   // null: no outline fonts (or no exports)
  "info": {"base": {"title": "…", "rev": "E", "date": "…", "company": "…", "comment1": "…"}, "head": {…}},  // title blocks
  "errors": ["kicad-cli glb failed for head: …"],   // non-fatal problems, shown in the UI
  "timings_s": {"checkout": 0.4, "parse": 7.9, "diff": 0.03, "export_wait": 71.0, "assemble": 5.0, "total": 84.4},
  "exports": {"gerbers": {"base": {"ok": true, "cached": false, "seconds": 4.3}, "head": {…}}, …}
}
```

### Panel

A board without a schematic is a `panel` when it has KiKit markers (`kikit:` footprints, `KiKit_*`
references, `Board_<n>-` nets for two or more `n`), repeated references (half of its footprints or
more), or "panel" in its file or directory name; else a `board`. Panels and boards have no
`schematic`, `bom` or `netlist` and `checks.erc` / `checks.grid` are null; the viewer hides those tabs.

```jsonc
{
  "signals": ["kikit", "copies", "name"],   // why it counts as a panel
  "copies": 4,                               // board copies (KiKit's Board_<n> nets, else the most repeated reference)
  "sources": [{"path": "boards/x/x.kicad_pcb", "copies": 4}],   // boards of the repo it holds k times (by footprint libraries, ≥ 90 %)
  "config": "panels/x/kikit.json",           // a KiKit preset next to it, or null
  "fiducials": 8, "tooling": 4, "mousebites": 112   // its own features (footprints)
}
```

In a panel's `pcb.changes`, fiducial and tooling-hole footprints get kinds `fiducial` and `tooling`,
mousebite holes are clustered into one `mousebites` change per tab (`refs`, detail "7 holes moved
20.000 mm"), and `outline` changes carry `feature`: `frame` (on the panel's outer edge) or `tabs`.
Panel features are not in `pcba3d.components`. Repeated references get a copy label in `ref`
(`R7·2`: KiKit's board number from its `Board_<n>-` nets, else the copy's rank top to bottom, left to
right), the reference itself in `designator`; copies pair up across revisions nearest first.

### Schematic

```jsonc
{
  "sheets": [{                          // head hierarchy order, then sheets that only exist in base
    "id": "root/power",                 // stable across base/head: slugified sheet names from the root
    "title": "Power", "file": "power.kicad_sch", "page": "2",
    "base_file": "power.kicad_sch", "head_file": "power.kicad_sch",   // null on the side without the sheet
    "status": "modified",
    "base": "p/<slug>/sch/base/root/power.svg", "head": "p/<slug>/sch/head/root/power.svg",
    "size_mm": [297, 210],
    "changes": [SchChange],
    "counts": {"changed": 1, "minor": 0, "moved": 14},   // moved: move_only changes (not in changed / minor)
    "moved_only": true                  // only when every change is move_only or minor (and one is move_only)
  }]
}
```

Sheet ids: `"root"` for the root sheet, then `/`-joined slugs (`[a-z0-9_-]`) of the sheet names;
two instances of the same file get different ids (`root/amp-left`, `root/amp-right`), a duplicate
name gets `-2`, `-3`. A sheet is `modified` when a semantic change was found, or when its
file differs in anything else (title block, field positions, …; then one `{"kind": "other"}` change
is listed), or when the rendered SVGs differ.

`SchChange`:

```jsonc
{"kind": "symbol", "what": "value", "ref": "R5", "base": "10k", "head": "4.7k",
 "whats": ["value", "fields"],          // every difference; "what" is the most important one
 "detail": "value 10k -> 4.7k; MPN 'A' -> 'B'",
 "bbox_mm": [x, y, w, h],               // union of base and head position
 "base_bbox_mm": […], "head_bbox_mm": […],   // only when the symbol, label, text or sheet box moved
 "power": true,                          // power symbols (#PWR…) only
 "move_only": true,                      // only moved / rerouted, same connections (below); omitted otherwise
 "parts_mm": [[x, y, w, h], …]}         // move_only: what moved (symbol body + field texts, base and head; each rerouted item)
```

| kind | what | notes |
|---|---|---|
| `symbol` | `added`, `removed`, `symbol` (lib id), `reference`, `value`, `footprint`, `fields`, `dnp`, `in_bom`, `on_board`, `exclude_from_sim`, `moved`, `rotated`, `mirrored`, `unit`, `library` (the embedded library symbol's graphics/pins changed) | symbols are matched by uuid, then reference, then lib id + position (so a re-annotation is `reference`, not add+remove) |
| `wire` | `added`, `removed`, `modified`, `rerouted` | wires, buses, bus entries, junctions and no-connect flags, clustered spatially; `count: {added, removed}`, `detail: "+2 wire, -1 no_connect"`. Wiring that keeps every connection is clustered apart as `rerouted` (`move_only`; `parts_mm`: the box of each item, up to 400) |
| `label` | `added`, `removed`, `renamed`, `moved`, `modified` | local/global/hierarchical labels, net-class/directive flags; `base`/`head` hold the texts |
| `text` | `added`, `removed`, `edited`, `moved`, `modified` | text, text boxes, tables |
| `graphic` | `added`, `removed`, `modified` | lines, rectangles, circles, arcs, images, clustered |
| `sheet` | `added`, `removed`, `modified`, `moved` | sub-sheet boxes on this sheet (`moved`: same size, pins, fields and pin nets) |
| `other` | `modified` | anything else; `bbox_mm: null` |

### Pcb

```jsonc
{
  "board": {"size_mm": [w, h], "origin_mm": [x, y],   // Edge.Cuts bounding box (head, else base); origin = top-left
            "gerber_origin_mm": [0, 0],
            "thickness_mm": 1.6, "copper_layers": 4,
            "mask_color": "green", "silk_color": "white", "finish": "ENIG",   // from the stackup, lower-cased; null if unset
            "base": {"size_mm": […], "origin_mm": […], "thickness_mm": 1.6, "copper_layers": 4},   // per side
            "head": {…}},
  "layers": [{
    "id": "F.Cu",                       // KiCad canonical layer name; "PTH" / "NPTH" for drill files
    "kind": "copper",                   // copper | mask | paste | silk | outline | fab | courtyard | adhesive | user | drill
    "side": "top",                      // top | bottom | inner | none
    "status": "modified",
    "semantic_changes": 5,              // number of `changes` that touch this layer
    "base": {"gerber": "p/<slug>/pcb/base/F_Cu.gbr", "svg": "p/<slug>/pcb/base/F_Cu.svg"},
    "head": {"gerber": "…", "svg": "…"}, // drill layers: {"gerber": "…/PTH.drl", "svg": null}
    "extent_mm": [x, y, w, h]           // fab / user layers only: box of what the layer draws (both sides'
  }],                                   // gerbers, KiCad mm), null if empty; notes often lie off the board
  "gbrjob": {"base": "p/<slug>/pcb/base/board.gbrjob", "head": "…"},
  "pos": {"base": "p/<slug>/pcb/base/pos.csv", "head": "…"},
  "changes": [PcbChange],
  "minor_groups": [{"what": "model_format", "detail": "3D model format .wrl -> .step",   // minor changes by kind,
                    "count": 105, "refs": ["C1", "C2", …]}]                              // largest first
}
```

Layers are every layer enabled in the board (copper first in stack order F.Cu, In1.Cu, …, B.Cu; then
the rest in head order, then base-only layers), followed by
`PTH` and `NPTH` when the board has such holes. A layer's `status` compares the exported gerber
(or drill) files ignoring creation dates and the revision in `%TF.ProjectId`; without exports it
falls back to "some semantic change touches this layer". Files that differ only in the order of
their objects, aperture numbers or attributes count as unchanged (a regenerated panel).

`PcbChange`:

```jsonc
{"kind": "footprint", "what": "moved", "ref": "U3",
 "whats": ["moved", "rotated", "model"],
 "layer": "F.Cu",                        // main layer (a footprint's copper side)
 "layers": ["F.Cu", "F.Mask", "F.SilkS", …],   // every layer the change can alter
 "holes": ["PTH"],                       // drill layers it can alter, if any
 "net": "GND",                           // tracks, vias, zones
 "bbox_mm": [x, y, w, h],                // union of base and head
 "base_bbox_mm": […], "head_bbox_mm": […],   // footprints
 "count": {"added": 3, "removed": 1},    // clustered kinds
 "detail": "moved 0.200 mm (54.5, 53.2) -> (54.5, 53.4); 3D model a.wrl -> b.step",
 "minor": true,                          // only when every what is minor (below); omitted otherwise
 "group": "routing"}                     // routing | properties | minor: a collapsible bucket (below); omitted otherwise
```

**Order and groups.** `changes` is sorted by significance: parts added, removed or replaced
(footprint, side, pads, value, DNP), then the outline and board setup, moves/rotations, zone
outlines, other footprint changes (graphics, 3D model), zone refills, routing (tracks, vias),
texts and graphics, footprints whose fields/attributes changed and nothing else, then minor
changes; within a rank by kind, then reference or net. `group` marks the bulky, low-signal ones
that the viewer, report and PR comment fold into collapsed buckets: `routing` (tracks, vias),
`properties` (fields/attributes only; the BOM tab has them) and `minor`. They are still changes;
only `minor` ones are left out of the counts.

**Minor changes.** Two footprint differences don't change the assembled board and are marked
`minor: true` when they are all there is: `model_format` (every 3D model path differs only in its
extension, same directory and stem, e.g. `.wrl -> .step`, offsets/scale/rotation equal) and
`footprint_library` (the lib id's nickname changed, the footprint name, pads and graphics are
identical). They stay in `changes` / `components` but are not counted in
`summary.components.changed` (see `minor`), are grouped in `pcb.minor_groups`, collapsed in the
viewer, report and PR comment, and not tinted in the 3D Changes view by default. A minor what next
to a real change (a moved part whose model also went `.wrl -> .step`) is just listed in `whats`.

| kind | what | notes |
|---|---|---|
| `footprint` | `added`, `removed`, `footprint` (lib id), `flipped`, `moved`, `rotated`, `pads`, `graphics`, `value`, `reference`, `model`, `dnp`, `attributes`, `fields`, `locked`, `footprint_library`, `model_format` | matched by uuid, then reference, then lib id + position; bbox = courtyard (else pads + graphics); pads are compared relative to the footprint, so a rotation is not a pad change |
| `track` | `added`, `removed`, `rerouted` | segments and arcs, grouped per (layer, net) and clustered spatially; detail has segment counts and the length delta |
| `via` | `added`, `removed`, `modified` | per net, clustered; `layers` = every copper layer the via spans |
| `zone` | `added`, `removed`, `outline`, `layers`, `net`, `settings`, `fill` | zones and rule areas, matched by uuid; `whats` lists all; `fill` alone means only the filled copper changed |
| `text` | `added`, `removed`, `modified` | board texts; detail `'REV A' -> 'REV B'` |
| `graphic` | `added`, `removed`, `modified` | board graphics and dimensions, per layer, clustered |
| `outline` | `added`, `removed`, `modified` | Edge.Cuts graphics |
| `board` | `stackup`, `setup`, `thickness`, `layers` | board-wide settings; `bbox_mm: null`, `layers: []` |
| `fiducial`, `tooling` | as `footprint` | panels only (see Panel) |
| `mousebites` | `added`, `removed`, `moved`, `modified` | panels only: the mousebite holes of one tab; `refs`, `holes: ["NPTH"]` |

### Pcba3d

```jsonc
{
  "base": {"glb": "p/<slug>/3d/base.glb", "step": "p/<slug>/3d/base.step"},   // step only with --step
  "head": {"glb": "p/<slug>/3d/head.glb"},
  "frame": {"units": "m", "up": "+y", "x": "kicad_x / 1000", "z": "kicad_y / 1000", "origin_mm": [0, 0]},
  "components": [{                      // every footprint of both boards, natural ref order
    "ref": "U3", "status": "moved",     // added | removed | moved | rotated | changed | unchanged
    "designator": "U3",                 // only when ref is a copy label ("U3·1", panels)
    "base": {"x": 1.0, "y": 2.0, "rot": 90, "side": "top", "footprint": "Lib:Fp", "value": "…",
             "model": "${KICAD10_3DMODEL_DIR}/….step",   // first model; all of them in "models"
             "models": [{"path": "…", "offset": [0, 0, 0], "scale": [1, 1, 1], "rotate": [0, 0, 0], "hide": true}],
             "dnp": false, "bbox_mm": [x, y, w, h], "uuid": "…"},
    "head": {…},
    "what": ["position", "rotation", "footprint", "value", "model", "side", "dnp", "pads", "graphics", "fields",
             "footprint_library", "model_format", "fields_minor"],   // subset; other footprint whats (attributes, …) when none of these apply
    "minor": true                       // status "changed" but only minor whats (see PcbChange); omitted otherwise
  }],
  "models": {                           // 3D model fallback of the export (null without kicad-cli / board)
    "base": {"found": 12, "substituted": 22, "missing": 1, "unknown": 0,   // distinct model paths of the board
             "substitutions": [{"from": "${KICAD8_3DMODEL_DIR}/R.3dshapes/R_0402.wrl",
                                "to": "${KICAD8_3DMODEL_DIR}/R.3dshapes/R_0402.step", "refs": ["R1", "R2"]}]},
    "head": {…}
  }
}
```

**3D model fallback.** Before the GLB/STEP export, every model path of the board is resolved like
KiCad does (`${KIPRJMOD}` and relative paths against the project directory, `${KICADn_3DMODEL_DIR}`
against the stock library: `$KIPR_KICAD_3DMODEL_DIR`, `$KICADn_3DMODEL_DIR`, `<kicad-cli
prefix>/share/kicad/3dmodels` or the usual install paths; other `${VAR}` from the environment). A
path that doesn't exist while the same path with another model extension does (`.wrl`/`.wrz` <->
`.step`/`.stp`) is rewritten in the temporary export checkout only, so KiCad 10 (whose stock
library ships STEP only) still exports the bodies of boards that name `.wrl` models. The diffs
read the files as committed. `found` / `missing` count paths that resolve / don't (even with the
other extension), `unknown` those that depend on an unknown variable (set it in the environment,
e.g. `KICAD_LIBS_DIR`).

### Change classification

One classifier (`kipr/project/classify.py`) decides for schematic symbols, footprints, BOM rows,
3D components, the summary counts, the report and the PR comment whether something changed:

- **noise**, never reported: fields that appear or disappear empty, `Sim.*` fields, `ki_*`
  keywords, whitespace-only edits, field order, field text position / visibility / font, uuids.
  A sheet whose file differs only in such things gets one `{"kind": "other", "minor": true}`.
- **minor** (`minor: true`, a `*_minor` / minor what): listed, collapsed, not counted as changed,
  never highlighted. Fields that don't name the part (cost, description, datasheet URL, notes,
  generator tags, …: `fields_minor`), `exclude_from_sim`, the symbol's lib id (`lib_id`) or cached
  library graphics (`library`), `model_format`, `footprint_library`.
- **moved** (schematics, `move_only: true`): a symbol, label, text or sheet box that was only moved,
  rotated or mirrored (plus minor differences), or wiring that was only rerouted, with the same
  connections: every pin of a symbol on a net of the same name, every sheet pin too; a wire,
  junction, no-connect flag or label on a net whose pins, labels, power symbols and sheet pins keep
  that net's name, with every one it touches present on both sides (a no-connect flag stays on the
  same pins). Nets are each sheet's local connectivity named by kicad-cli's netlist (by labels and
  power symbols without one). Counted apart (`counts.moved`, `summary.sch_moved`); the viewer's smart
  diff (default) and the report outline them faintly, wash them out of the ink diff and collapse them;
  the viewer's raw diff shows them as changes.
- **significant**: value, footprint, DNP, in BOM / on board, reference, placement, pads,
  and the fields that name the part to buy (`fields`). Those fields are matched by
  `tool.significant_fields`: case-insensitive globs over the field name with spaces, `_`, `-`,
  `.` and `/` removed. Defaults: MPN, manufacturer / mfr / mfg, part number / PN, LCSC, JLC,
  Digi-Key, Mouser, Farnell, Newark, Arrow, TME, RS, Octopart, supplier, vendor, SKU. Part-number
  fields compare case-insensitively. `kipr project --significant-fields "mpn,lcsc*"` replaces
  the list, `--significant-fields "+tolerance,voltage"` extends it.

### Bom

From the schematic symbols (`in_bom` only; power symbols and `#` refs excluded; units merged).

```jsonc
{"rows": [{"key": "R5", "refs": ["R5"], "status": "changed",        // added | removed | changed | unchanged
           "base": {"value": "10k", "footprint": "…", "fields": {"MPN": "…"}, "dnp": false, "in_bom": true,
                    "lib_id": "Device:R", "sheet": "root/power", "mpn": "…"},
           "head": {…}, "what": ["value", "footprint", "dnp", "lib_id", "fields", "fields_minor"],
           "fields_changed": {"significant": ["MPN"], "minor": ["Standard Cost"]},   // only rows with field changes
           "minor": true}],                                         // only minor whats (below); omitted otherwise
 "groups": [{"value": "10k", "footprint": "R:R_0402", "mpn": "RC0402…", "dnp": false,
             "status": "changed",                                   // added | removed | changed (refs differ) | unchanged
             "base": {"qty": 7, "refs": ["R1", …]}, "head": {"qty": 6, "refs": […]},
             "refs_added": [], "refs_removed": ["R1"]}],
 "csv": {"base": "p/<slug>/bom/base.csv", "head": "…"}}
```

`mpn` is the first non-empty of the fields MPN, Manufacturer Part Number, Mfr. Part Number, MFN, …
(case-insensitive).

### Netlist

```jsonc
{"source": "schematic",                 // "schematic" (kicad-cli netlists) or "board" (pad nets; fallback without kicad-cli)
 "changes": [{"net": "/VBUS", "status": "modified", "added": ["U3.4"], "removed": ["R5.1"],
              "renamed_from": null}],   // status: added | removed | modified | renamed
 "moved_pins": [{"pin": "J1.9", "from": "unconnected-(J1-P9-Pad9)", "to": "GND"}],
 "files": {"base": "p/<slug>/netlist/base.net", "head": "…"},
 "base_count": 120, "head_count": 121}
```

Nodes are `REF.PIN`. A net only in base and a net only in head are paired as a rename when
their pin sets overlap with Jaccard >= 0.5 (`renamed_from` set; `status` is `renamed` when the
pins are identical, else `modified`). Auto-named nets (`Net-(…)`, `unconnected-(…)`) that only
changed name are not reported.

### CheckDelta

```jsonc
{"base_count": 12, "head_count": 13,
 "new": [{"severity": "error", "type": "clearance", "description": "…", "items": ["…"],
          "uuids": ["…"],               // KiCad uuids of the items (locate them in the files)
          "pos_mm": [x, y],             // first item's position; board mm (DRC) or sheet mm (ERC)
          "sheet": "/",                 // ERC: sheet path as KiCad prints it; DRC: null
          "category": "violation",      // violation | unconnected | parity (DRC schematic parity)
          "font_dependent": ["Poppins"]}],   // DRC only, when present: items include text in a missing face
 "fixed": [ … ],
 "report": {"base": "p/<slug>/checks/drc.base.json", "head": "…"},
 "pos_scale_fixed": 100,                // present when ERC positions were corrected (see below)
 "libraries": "project"}                // present with --fast-checks (see below)
```

Matching base to head, in passes: type + items + description within 2 mm; then type + items
anywhere; then type within 0.5 mm. Numbers in item descriptions (track lengths, …) are ignored.
KiCad 10.0.x writes ERC positions in its JSON 100x too small; when every position fits in 1/50
of the page but x100 still lands on it, they are scaled and `pos_scale_fixed` is set. ERC/DRC run
with the global KiCad libraries (library mismatch checks included), which is most of the run time
on real boards (~30-50 s per check and side). `--fast-checks` runs them with empty global library
tables instead (~2-6 s) and drops the library violation types (`lib_footprint_issues`,
`lib_footprint_mismatch`, `footprint_link_issues`, `lib_symbol_issues`, `lib_symbol_mismatch`);
the delta then says `"libraries": "project"`.

DRC deltas also have `"font_dependent": N` (violations marked) when a face the board uses was
missing; new violations are matched against the head board's text items, fixed ones against
base's (by uuid, else by the quoted text in the item description).

### Fonts

The run-level `fonts` object (see [docs/project.md](project.md#fonts)):

```jsonc
{"faces": [{"face": "Poppins",
            "status": "fetched",        // system | provided (--fonts) | fetched | embedded | missing | unknown (no fc-match)
            "files": ["head:projects/…/x.kicad_pcb"],   // <side>:<path> of the files using it
            "embedded_in": ["head:…"],                  // when other files embed it (they need nothing)
            "family": "Poppins", "license": "OFL", "source": "google/fonts@9710da1eacb3:ofl/poppins",  // fetched only
            "substitute": "DejaVu Sans Bold"}],         // missing: what kicad-cli used instead (when it said)
 "missing": ["…"],                      // faces kicad-cli had to substitute
 "warning": "Font '…' is not available in CI; …" | null,
 "errors": ["cannot download font '…' from Google Fonts: …"],
 "checked": true,                       // false: fc-match unavailable, availability known only from kicad-cli
 "fetch": true}                         // Google Fonts downloads were allowed
```

### Grid

Schematic connection points off the grid (`--grid-check changed|all|off`, default `changed`;
`--sch-grid-mil`, default 50 = 1.27 mm). `null` with `--grid-check off` or without a head
schematic. Checked on the head side: symbol pin connection points (placement + library pin
positions with rotation/mirror, for the placed unit/body style), wire and bus endpoints, bus
entries (both ends), junctions, no-connect flags, label anchors (`label`, `global_label`,
`hierarchical_label`, `netclass_flag`, `directive_label`) and sheet pins. A coordinate is on the
grid when it is within `tolerance_mm` of a multiple of `grid_mm`. Each sheet file is checked once
(a file used by several sheet instances reports on the first one, `sheets` lists all).

`changed` mode flags only points the PR added or moved: an item is matched to base by uuid, else
by position (the same sheet file, or the file of the base sheet with the same id, and the same
kind); points that existed in base are skipped. `all` checks every point.

```jsonc
{"grid_mil": 50.0, "grid_mm": 1.27, "tolerance_mm": 0.001, "mode": "changed",
 "checked": 412,                        // points checked (in "changed" mode: new or moved ones)
 "count": 2,                            // findings (items below)
 "points": 23,                          // off-grid points, related items included
 "sheets": [{"id": "root/power", "file": "boards/x/power.kicad_sch", "count": 1}],   // head hierarchy order
 "items": [GridFinding]}                // sorted by sheet (hierarchy order), then y, x
```

`GridFinding` (always a warning):

```jsonc
{"kind": "symbol",                      // symbol | sheet | wire | bus | bus_entry | junction | no_connect |
                                        // label | global_label | hierarchical_label | netclass_flag | directive_label
 "severity": "warning",
 "change": "moved",                     // added | moved | unchanged (unchanged only in "all" mode)
 "power": true,                         // power symbols only
 "ref": "U3",                           // symbol reference / sheet name; null otherwise
 "text": "LM1117",                      // symbol value / label text; null otherwise
 "sheet": "root/power", "sheets": ["root/power"],   // sheet id(s), see Schematic
 "file": "boards/x/power.kicad_sch",    // repo-relative sheet file
 "line": 1234,                          // 1-based line of the item's "(symbol" / "(wire" / … in head
 "uuid": "…",
 "pos_mm": [x, y],                      // the item's anchor (symbol/label position, sheet corner, first off-grid point)
 "bbox_mm": [x, y, w, h],               // the off-grid points (and the symbol body), padded: zoom here
 "off_count": 20,                       // off-grid points of this item
 "points": [{"name": "1",               // pin number / sheet pin name / "" for wiring; first 50 only
             "pos_mm": [x, y], "off_mm": [dx, dy]}],   // dx/dy: signed distance to the nearest grid line, 0 = on it
 "related": [{"kind": "wire", "uuid": "…", "line": 1301, "ref": null, "text": null, "change": "moved",
              "off_count": 1, "power": true}],          // power: power symbols only
 "detail": "20 of 24 pins off the 50 mil grid, e.g. pin 1 at (101.915, 64.77) (x +0.635 mm); also 18 wires, 1 power symbol attached"}
```

**Grouping.** Findings of one sheet file that share an off-grid point are one finding: a symbol
or sheet box names it and the wires, junctions, no-connects, labels and power symbols on its
off-grid pins (and the wiring connected to those through other off-grid points) are `related`.
Two symbols or sheet boxes are never merged; power symbols are not anchors (they join the part
they sit on). Off-grid wiring that touches no symbol is grouped by its shared points and named by
its most telling item (label, bus entry, bus, wire, junction, no-connect).

### Impedance

`checks.impedance`: the impedance of every net class that has an impedance target, on every copper layer its
tracks use, on base and head, evaluated per track width and judged by the controlled length out of tolerance. It is
a review aid: it never fails the run. `null` when neither side has a board (or boarddd could not be imported; see
`errors`).

**Solver** (`solver`): `field` is boarddd's tier-2 2D quasi-static field solver (`boarddd.impedance.fieldsolver`,
the `[field]` extra: numpy and scipy) on the real cross-section: every layer's εr, the solder mask on any outer
structure (single and differential, CPWG too), differential coplanar included. Each number carries the solver's
error estimate (`error_pct`, from its grid refinement). `closedform` is boarddd's tier-1 models (within about 2 % of
a field solver inside their validity ranges; inputs outside them are listed in `validity`). `auto` (the default)
uses the field solver when it is installed, else falls back to closed form and says why in `solver_note`;
`KIPR_IMPEDANCE_SOLVER=closedform` (the workflow's `impedance-solver` input) forces closed form.

Inputs, per side, from the committed files:

- **Targets**: `boarddd.io.kicad.read_kicad_pcb` reads the `.kicad_pro` net classes. The target is the class's
  KiCad 10 tuning profile, else the class name convention (`SE_50_CP`, `DP_90_MS`, `BAL_D90_C30_CPWG`, `90ohm`…:
  kind single/differential, target Ω, `MS`/`SL`/`CP`/`CPWG` structure). Tolerance: the profile's, else ±10 %.
  Nets belong to their effective class (explicit assignment or pattern).
- **Stackup**: the board's `(setup (stackup))` (thickness, εr; mask thickness and εr). Missing values take
  KiCad's defaults (εr 4.5, copper 35 µm, mask 10 µm / εr 3.3), noted in `notes`.
- **Geometry**: the class's tracks (segments and arcs, zero-length ones dropped) per layer, grouped into
  `segments` by width and, for a differential class, by `gap`, the edge-to-edge distance to the pair's other net's
  nearest parallel overlapping segment (each P-side segment's coupled length counts; else the class's
  `diff_pair_gap`, noted). For a coplanar class, `coplanar_gap` is max(class clearance, clearance of the other-net
  copper zones beside the track on that layer). Left out of the evaluation and listed in `excluded` with a reason:
  - `covered`: a segment lying inside a wider segment of the same net (the copper is the wider one);
  - `launch`: a *run* (connected segments of one net and width) whose width differs from the class's main width
    (the longest-routed one over all its layers, covered tracks aside), at most `rules.launch_max_mm` (3 mm) long,
    with an end on a pad of its net on that layer (a connector/castellation launch or a neck-down into a pad);
  - `breakout`: a pair section whose gap is more than 2× the class's main gap (the longest-coupled one over all
    its layers) and at most 3 mm long: the pair fanning out to its pads;
  - `uncoupled`: pair length with no parallel segment of the other net beside it;
  - `short`: a run shorter than `rules.min_run_mm` (0.5 mm).
  Everything else is controlled length: a neck-down that does not end on a pad, or a long wide stretch, is
  evaluated at its own geometry.
- **Structure**: outer layers are microstrip (coplanar: grounded CPW when a copper zone of another net is
  beside the track, else microstrip), inner layers stripline (with the field solver, a coplanar class with a zone
  beside it on an inner layer is embedded CPWG); when that differs from the class's structure the row says so in
  `notes`. Differential coplanar: with the field solver, solved as such (`model` null: tier 1 has none); in closed
  form it is evaluated as edge-coupled microstrip (an upper bound), noted.
- **Verdict**: each segment group is within tolerance or not; `length_out_mm` is the controlled length (any
  width) out of tolerance, and the side is `within` when that is 0. `width`, `gap`, `Z`, `deviation_pct`, `model`
  and `params` are the longest group's (what the tables show); `worst_deviation_pct` is the largest |deviation| of
  any group. A side with no controlled length (only launches/stubs) has `Z`/`within` null and a note.

```jsonc
{"method": "field solver (boarddd.impedance tier 2, 2D quasi-static)",   // or "closed-form estimate (boarddd.impedance tier 1, quasi-static)"
 "solver": "field",                  // field | closedform
 "solver_note": null,                // why closed form was used (fallback or requested); null with the field solver
 "boarddd": "0.4.0",                 // boarddd version used
 "tolerance_default_pct": 10.0,
 "rules": {"min_run_mm": 0.5, "launch_max_mm": 3.0},
 "field_solves": 7,                  // distinct cross-sections solved (cached); 0 in closed form
 "classes": ["DP_90_MS", "SE_50_CP"],   // classes with a target on either side
 "stackup_changes": [{"layer": "dielectric 1", "field": "thickness", "base": 1.51, "head": 1.2}],  // field: thickness | epsilon_r | layer (added/removed: base/head true/false)
 "count": {"rows": 3,                // head rows
           "violations": 1,          // head rows with controlled length out of tolerance
           "length_out_mm": 2.8,     // their total length out of tolerance
           "new_violations": 1,      // ... rows that were not out of tolerance on base (or are new)
           "stackup_shifts": 0, "width_changes": 1},
 "rows": [ImpedanceRow, …]}          // sorted by class, layer
```

**ImpedanceRow**:

```jsonc
{"class": "SE_50_CP", "layer": "F.Cu",
 "status": "changed",                // added | removed | same | changed (Z moved by >= 0.05 %)
 "target": {"kind": "single", "target": 50.0, "tolerance_pct": 10.0, "tolerance_default": true,
            "common_mode": null, "structure": "coplanar", "source": "name"},   // head's, else base's
 "base": ImpedanceSide | null, "head": ImpedanceSide | null,
 "delta_pct": -1.2,                  // Z head vs base, %
 "shift_pct": -7.7,                  // what the stackup change alone did: Z on head's stackup at base's geometry vs base Z (null below 0.5 %)
 "flags": ["new_violation"],         // new_violation | violation (also on base) | fixed | stackup_shift | width_change (width or gap) | target_change
 "severity": "bad"}                  // bad: new violation; warn: violation, stackup shift, width/target change, validity flags or an error; ok
```

**ImpedanceSide**:

```jsonc
{"class": "SE_50_CP", "layer": "F.Cu", "nets": ["/RF/ANT"],
 "width": 0.26,                      // the longest controlled group's width (and gap)
 "widths": [{"width": 0.26, "length_mm": 34.9}, {"width": 0.8, "length_mm": 4.8}],   // every routed width, excluded ones too
 "routed_mm": 41.5,                  // all tracks of the class on this layer
 "length_mm": 34.9,                  // controlled length (what was evaluated)
 "gap": null, "gaps": [{"gap": 0.15, "length_mm": 99.8}],   // differential only (gaps: every coupled gap)
 "coplanar_gap": 0.15,               // coplanar only
 "structure": "coplanar_grounded",   // microstrip | stripline | coplanar_grounded (what was evaluated)
 "solver": "field",                  // field | closedform
 "model": "cpwg",                    // boarddd.impedance tier-1 model id (null: no closed-form model, field only)
 "params": {"w": 0.26, "t": 0.035, "h": 0.2104, "er": 4.4, "c": 0.015, "erc": 3.8, "gap": 0.15},   // the inputs, mm
 "Z": 51.01,                         // Ω: Z0, or Zdiff for differential targets (longest group)
 "Zcommon": null,                    // differential: Zeven / 2
 "Z_closedform": 51.17,              // the tier-1 value at the same geometry (null without a model)
 "error_pct": 0.43,                  // field solver error estimate, the largest over the groups; null in closed form
 "deviation_pct": 2.03,              // (Z - target) / target, longest group
 "worst_deviation_pct": 2.03,        // largest |deviation| of any group (signed)
 "length_out_mm": 0,                 // controlled length out of tolerance
 "within": true,                     // length_out_mm == 0 (null: nothing controlled)
 "segments": [{"width": 0.26, "gap": null, "length_mm": 34.9, "Z": 51.01, "Zcommon": null, "deviation_pct": 2.03,
               "within": true, "error_pct": 0.43, "error": null}],   // one per (width, gap), longest first
 "excluded": [{"width": 0.8, "reason": "launch", "length_mm": 4.76}],   // covered | launch | breakout | uncoupled | short
 "validity": [],                     // closed form: inputs outside the model's validity range (none for the field solver)
 "notes": ["coplanar with a plane below: evaluated as grounded CPW"],  // structure choices, defaulted stackup values
 "error": null}                      // why there is no Z (no gap, no reference plane…)
```

