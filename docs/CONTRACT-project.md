# Project review data contract (draft v1)

`kipr project --repo R --base B --head H --out OUT` writes everything a viewer or report needs
into `OUT/`. The viewer and report read only `OUT/`; they never run KiCad. All paths inside the
JSON are relative to `OUT/`, use `/`, and are safe to serve statically. The backend owner may
extend this; any change must be reflected here in the same commit.

## `OUT/project-review.json`

```jsonc
{
  "version": 1,
  "tool": {"name": "kipr", "version": "0.1.0", "kicad": "10.0.x"},
  "base": {"sha": "…", "ref": "main", "short": "abc1234"},
  "head": {"sha": "…", "ref": "feature", "short": "def5678"},
  "repo": {"url": "https://github.com/o/r", "blob": "https://github.com/o/r/blob/{sha}/{path}"},
  "projects": [ /* Project, one per changed .kicad_pro directory */ ]
}
```

### Project

```jsonc
{
  "slug": "adsbee_1090u",              // unique, [a-z0-9_-], names the OUT/p/<slug>/ dir
  "name": "adsbee_1090u",
  "path": "projects/adsbee/kicad/adsbee_1090u",   // dir of the .kicad_pro, repo-relative
  "status": "modified",                // added | removed | modified
  "summary": {"sheets_changed": 2, "layers_changed": 5, "components": {"added": 1, "removed": 0, "moved": 3, "changed": 2}, "nets_changed": 4,
              "erc": {"new": 0, "fixed": 1}, "drc": {"new": 2, "fixed": 0}},
  "schematic": Schematic | null,
  "pcb": Pcb | null,
  "pcba3d": Pcba3d | null,
  "bom": Bom | null,
  "netlist": Netlist | null,
  "checks": {"erc": CheckDelta | null, "drc": CheckDelta | null},
  "errors": ["kicad-cli failed to export head GLB: …"]   // non-fatal problems, shown in the UI
}
```

Each side is `"base"` or `"head"`; a side is `null` when the project does not exist there.

### Schematic

```jsonc
{
  "sheets": [{
    "id": "root/power",                 // stable across base/head (sheet path by names)
    "title": "Power", "file": "power.kicad_sch", "page": "2",
    "status": "modified",               // added | removed | modified | unchanged
    "base": "p/<slug>/sch/base/<id>.svg", "head": "p/<slug>/sch/head/<id>.svg",   // kicad-cli SVGs
    "size_mm": [297, 210],
    "changes": [ {"kind": "symbol", "ref": "R5", "what": "value", "base": "10k", "head": "4.7k",
                  "bbox_mm": [x, y, w, h]} ]   // bbox in sheet mm, for highlighting in the viewer
  }]
}
```

### Pcb

```jsonc
{
  "board": {"size_mm": [w, h], "origin_mm": [x, y], "thickness_mm": 1.6, "copper_layers": 4,
            "mask_color": "green", "silk_color": "white", "finish": "ENIG"},
  "layers": [{
    "id": "F.Cu", "kind": "copper",     // copper | mask | paste | silk | outline | fab | courtyard | user | drill
    "side": "top",                      // top | bottom | inner | none
    "status": "modified",
    "base": {"gerber": "p/<slug>/pcb/base/F_Cu.gbr", "svg": "p/<slug>/pcb/base/F_Cu.svg"},
    "head": {"gerber": "…", "svg": "…"}
  }],
  "gbrjob": {"base": "p/<slug>/pcb/base/board.gbrjob", "head": "…"},
  "changes": [ {"kind": "footprint|track|via|zone|text|outline", "ref": "U3", "what": "moved",
                "layer": "F.Cu", "bbox_mm": [x, y, w, h], "detail": "…"} ]
}
```

### Pcba3d

```jsonc
{
  "base": {"glb": "p/<slug>/3d/base.glb"}, "head": {"glb": "p/<slug>/3d/head.glb"},
  "components": [{
    "ref": "U3", "status": "moved",     // added | removed | moved | rotated | changed | unchanged
    "base": {"x": 1.0, "y": 2.0, "rot": 90, "side": "top", "footprint": "Lib:Fp", "value": "…", "model": "…"},
    "head": {…},
    "what": ["position", "rotation", "footprint", "value", "model", "side", "dnp"]
  }]
}
```

Coordinates are board mm in KiCad's frame (y down); the viewer maps them into the GLB frame.

### Bom

```jsonc
{"rows": [{"key": "R5", "refs": ["R5"], "status": "changed", "base": {"value": "10k", "footprint": "…", "fields": {"MPN": "…"}},
           "head": {…}, "what": ["value"]}]}
```

### Netlist

```jsonc
{"changes": [{"net": "/VBUS", "status": "modified", "added": ["U3.4"], "removed": ["R5.1"],
              "renamed_from": null}]}
```

### CheckDelta

```jsonc
{"base_count": 12, "head_count": 13,
 "new": [{"severity": "error", "type": "clearance", "description": "…", "items": ["…"], "pos_mm": [x, y], "sheet": "root"}],
 "fixed": [ … ], "report": {"base": "p/<slug>/checks/drc.base.json", "head": "…"}}
```
