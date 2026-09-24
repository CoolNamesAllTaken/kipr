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
  "tool": {"name": "kipr", "version": "0.1.0", "kicad": "10.0.6"},    // kicad: null without kicad-cli
  "base": {"sha": "…", "ref": "main", "short": "abc1234"},
  "head": {"sha": "…", "ref": "feature", "short": "def5678"},
  "repo": {"url": "https://github.com/o/r", "blob": "https://github.com/o/r/blob/{sha}/{path}"},  // nulls if not GitHub
  "projects": [ /* Project, one per changed .kicad_pro directory */ ],
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
  "name": "adsbee_1090u",              // .kicad_pro stem
  "path": "projects/adsbee/kicad/adsbee_1090u",   // dir of the .kicad_pro, repo-relative ("" = repo root)
  "status": "modified",                // added | removed | modified
  "reasons": ["projects/…/adsbee_1090u.kicad_pcb"],   // changed files that made it count
  "summary": {"sheets_changed": 2, "layers_changed": 5,
              "components": {"added": 1, "removed": 0, "moved": 3, "changed": 2},  // from the board (moved includes rotated); from the BOM if there is no board
              "nets_changed": 4,
              "erc": {"new": 0, "fixed": 1}, "drc": {"new": 2, "fixed": 0}},      // null when the check could not run
  "schematic": Schematic | null,
  "pcb": Pcb | null,
  "pcba3d": Pcba3d | null,
  "bom": Bom | null,
  "netlist": Netlist | null,
  "checks": {"erc": CheckDelta | null, "drc": CheckDelta | null},
  "info": {"base": {"title": "…", "rev": "E", "date": "…", "company": "…", "comment1": "…"}, "head": {…}},  // title blocks
  "errors": ["kicad-cli glb failed for head: …"],   // non-fatal problems, shown in the UI
  "timings_s": {"checkout": 0.4, "parse": 7.9, "diff": 0.03, "export_wait": 71.0, "assemble": 5.0, "total": 84.4},
  "exports": {"gerbers": {"base": {"ok": true, "cached": false, "seconds": 4.3}, "head": {…}}, …}
}
```

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
    "changes": [SchChange]
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
 "base_bbox_mm": […], "head_bbox_mm": […],   // only when the symbol moved
 "power": true}                          // power symbols (#PWR…) only
```

| kind | what | notes |
|---|---|---|
| `symbol` | `added`, `removed`, `symbol` (lib id), `reference`, `value`, `footprint`, `fields`, `dnp`, `in_bom`, `on_board`, `exclude_from_sim`, `moved`, `rotated`, `mirrored`, `unit`, `library` (the embedded library symbol's graphics/pins changed) | symbols are matched by uuid, then reference, then lib id + position (so a re-annotation is `reference`, not add+remove) |
| `wire` | `added`, `removed`, `modified` | wires, buses, bus entries, junctions and no-connect flags, clustered spatially; `count: {added, removed}`, `detail: "+2 wire, -1 no_connect"` |
| `label` | `added`, `removed`, `renamed`, `moved`, `modified` | local/global/hierarchical labels, net-class/directive flags; `base`/`head` hold the texts |
| `text` | `added`, `removed`, `edited`, `moved`, `modified` | text, text boxes, tables |
| `graphic` | `added`, `removed`, `modified` | lines, rectangles, circles, arcs, images, clustered |
| `sheet` | `added`, `removed`, `modified` | sub-sheet boxes on this sheet |
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
    "head": {"gerber": "…", "svg": "…"}  // drill layers: {"gerber": "…/PTH.drl", "svg": null}
  }],
  "gbrjob": {"base": "p/<slug>/pcb/base/board.gbrjob", "head": "…"},
  "pos": {"base": "p/<slug>/pcb/base/pos.csv", "head": "…"},
  "changes": [PcbChange]
}
```

Layers are every layer enabled in the board (head order, then base-only layers), followed by
`PTH` and `NPTH` when the board has such holes. A layer's `status` compares the exported gerber
(or drill) files ignoring creation dates and the revision in `%TF.ProjectId`; without exports it
falls back to "some semantic change touches this layer".

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
 "detail": "moved 0.200 mm (54.5, 53.2) -> (54.5, 53.4); 3D model a.wrl -> a.step"}
```

| kind | what | notes |
|---|---|---|
| `footprint` | `added`, `removed`, `footprint` (lib id), `flipped`, `moved`, `rotated`, `pads`, `graphics`, `value`, `reference`, `model`, `dnp`, `attributes`, `fields`, `locked` | matched by uuid, then reference, then lib id + position; bbox = courtyard (else pads + graphics); pads are compared relative to the footprint, so a rotation is not a pad change |
| `track` | `added`, `removed`, `rerouted` | segments and arcs, grouped per (layer, net) and clustered spatially; detail has segment counts and the length delta |
| `via` | `added`, `removed`, `modified` | per net, clustered; `layers` = every copper layer the via spans |
| `zone` | `added`, `removed`, `outline`, `layers`, `net`, `settings`, `fill` | zones and rule areas, matched by uuid; `whats` lists all; `fill` alone means only the filled copper changed |
| `text` | `added`, `removed`, `modified` | board texts; detail `'REV A' -> 'REV B'` |
| `graphic` | `added`, `removed`, `modified` | board graphics and dimensions, per layer, clustered |
| `outline` | `added`, `removed`, `modified` | Edge.Cuts graphics |
| `board` | `stackup`, `setup`, `thickness`, `layers` | board-wide settings; `bbox_mm: null`, `layers: []` |

### Pcba3d

```jsonc
{
  "base": {"glb": "p/<slug>/3d/base.glb", "step": "p/<slug>/3d/base.step"},   // step only with --step
  "head": {"glb": "p/<slug>/3d/head.glb"},
  "frame": {"units": "m", "up": "+y", "x": "kicad_x / 1000", "z": "kicad_y / 1000", "origin_mm": [0, 0]},
  "components": [{                      // every footprint of both boards, natural ref order
    "ref": "U3", "status": "moved",     // added | removed | moved | rotated | changed | unchanged
    "base": {"x": 1.0, "y": 2.0, "rot": 90, "side": "top", "footprint": "Lib:Fp", "value": "…",
             "model": "${KICAD10_3DMODEL_DIR}/….step",   // first model; all of them in "models"
             "models": [{"path": "…", "offset": [0, 0, 0], "scale": [1, 1, 1], "rotate": [0, 0, 0], "hide": true}],
             "dnp": false, "bbox_mm": [x, y, w, h], "uuid": "…"},
    "head": {…},
    "what": ["position", "rotation", "footprint", "value", "model", "side", "dnp"]   // subset; other footprint whats (pads, fields, …) when none of these apply
  }]
}
```

### Bom

From the schematic symbols (`in_bom` only; power symbols and `#` refs excluded; units merged).

```jsonc
{"rows": [{"key": "R5", "refs": ["R5"], "status": "changed",        // added | removed | changed | unchanged
           "base": {"value": "10k", "footprint": "…", "fields": {"MPN": "…"}, "dnp": false, "in_bom": true,
                    "lib_id": "Device:R", "sheet": "root/power", "mpn": "…"},
           "head": {…}, "what": ["value", "footprint", "dnp", "lib_id", "fields"]}],
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
          "pos_mm": [x, y],             // first item's position; board mm (DRC) or sheet mm (ERC)
          "sheet": "/",                 // ERC: sheet path as KiCad prints it; DRC: null
          "category": "violation"}],    // violation | unconnected | parity (DRC schematic parity)
 "fixed": [ … ],
 "report": {"base": "p/<slug>/checks/drc.base.json", "head": "…"},
 "pos_scale_fixed": 100}                // present when ERC positions were corrected (see below)
```

Matching base to head, in passes: type + items + description within 2 mm; then type + items
anywhere; then type within 0.5 mm. Numbers in item descriptions (track lengths, …) are ignored.
KiCad 10.0.x writes ERC positions in its JSON 100x too small; when every position fits in 1/50
of the page but x100 still lands on it, they are scaled and `pos_scale_fixed` is set. ERC/DRC run
with the global KiCad libraries (library mismatch checks included), which is most of the run time
on real boards (~30-50 s per check and side).
