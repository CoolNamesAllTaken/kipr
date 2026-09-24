# kipr

Pronounced "keeper". Think KiCad-PR, but also "those design changes look all right, that's a keeper".

Automated review of KiCad changes in pull requests, in one place:

- **Library review** (`kipr library`): changed symbols, footprints and 3D models — before/after/diff
  renders, per-layer views, 3D previews, KLC and deterministic checks.
- **Project review** (`kipr project`): changed KiCad projects — schematic sheet diffs, per-layer
  layout diffs rendered from gerbers, and a full 3D PCBA diff, plus BOM, netlist and ERC/DRC deltas.

Each produces a self-contained HTML report and an interactive viewer, and ships as reusable GitHub
Actions workflows used by other repositories.

## Layout

| Path            | What                                                         |
|-----------------|--------------------------------------------------------------|
| `kipr/common/`  | shared Python: s-expression parsing, kicad-cli, git helpers  |
| `kipr/library/` | library (symbol / footprint / 3D model) review               |
| `kipr/project/` | project (schematic / layout / 3D PCBA) review                |
| `web/`          | the interactive viewer (`shared/`, `library/`, `project/`)   |
| `docs/`         | data contracts between the backends and the viewer           |
