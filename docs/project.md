# Project review (`kipr project`)

`kipr project` reviews every KiCad project that changed between two commits: schematic sheets,
PCB layers and the 3D board as before/after exports from kicad-cli, plus semantic diffs parsed
from the files themselves (components, footprints, tracks, zones, outline, netlist, BOM) and the
ERC/DRC violations that are new or fixed. Everything goes into one output directory that the
viewer (`web/project/`) and the HTML report read; they never run KiCad. The data format is
[CONTRACT-project.md](CONTRACT-project.md).

It replaces the kiri-based `.github/workflows/kicad-diff.yml` of PantsForBirds/internal.

## Running it

Needs Python >= 3.10, git, the system cairo library (`libcairo2`, for the report images) and
KiCad 10's `kicad-cli` for the exports (without it you still get the semantic diffs, with the
netlist taken from the board and no ERC/DRC). No node or network is needed.

```sh
pip install "kipr @ git+https://github.com/CoolNamesAllTaken/kipr@<ref>"
kipr project --repo . --base origin/main --head HEAD --out review/
python3 review/serve.py             # the viewer; or open review/index.html from disk
                                    # review/project-review.html is the report (no JavaScript)
```

`kipr project` runs three stages; each is also a subcommand (`python3 -m kipr.project.site|report`
work too):

| Command | Writes |
|---|---|
| `kipr project review --repo . --base B --head H --out OUT [options below]` | `OUT/project-review.json`, `OUT/p/<slug>/…` (kicad-cli exports + semantic diffs) |
| `kipr project site --out OUT [--no-offline]` | the viewer (`index.html`, `js/`, `vendor/`, `pcba3d/`, `data.js`, `offline/`, `serve.py`) |
| `kipr project report --out OUT [--output FILE] [--max-mb 25]` | `OUT/project-review.html` |

The end-to-end run takes the review options below plus `--skip site|report` (repeatable),
`--no-offline`, `--report FILE` and `--max-mb`. CI runs `kipr project … --skip site --skip report`,
fits the data into its size budget, then runs `site` and `report`.

| Option | Default | Meaning |
|---|---|---|
| `--repo R` | `.` | git repository |
| `--base B` / `--head H` | – / `HEAD` | commits to compare (CI uses the merge base as `B`) |
| `--out OUT` | – | output directory (`project-review.json`, `p/<slug>/…`) |
| `--projects GLOB` | all | only projects whose directory, directory name, file stem or `dir/stem` matches; repeatable |
| `--kicad-cli PATH` | `$KIPR_KICAD_CLI`, then `PATH` | kicad-cli to use |
| `--fast-checks` | off | ERC/DRC without the global KiCad libraries: several times faster, library-mismatch checks dropped |
| `--step` | off | also export STEP models |
| `--no-glb` | off | skip the GLB (3D PCBA) export |
| `--no-export` | off | semantic diffs only, never run kicad-cli |
| `--jobs N` | 4 | parallel kicad-cli processes |
| `--cache-dir D` | `$KIPR_CACHE_DIR` or `~/.cache/kipr` | export cache |
| `--repo-url URL` | `origin` if on GitHub | for source links |
| `--significant-fields PATTERNS` | part numbers (MPN, manufacturer, LCSC, Digi-Key, Mouser, …) | which symbol/footprint fields count as real changes; other field changes are minor. Comma-separated globs, `+` extends the defaults. See [Change classification](CONTRACT-project.md#change-classification) |
| `--grid-check changed\|all\|off` | `changed` | [schematic grid check](#schematic-grid-check): warn about connection points the PR added or moved off the grid (`changed`), about every off-grid point on the head side (`all`), or not at all |
| `--sch-grid-mil MIL` | `50` | the schematic connection grid in mil (50 mil = 1.27 mm) |

What counts as a changed project: a directory with a `.kicad_pro` in which a `.kicad_sch`,
`.kicad_pcb`, `.kicad_pro`, `.kicad_dru`, lib table, project library or 3D model changed, or that
references (through relative or `${KIPRJMOD}` paths in its lib tables / board) a library or 3D
model elsewhere in the repo that changed. A `.kicad_pcb` without a `.kicad_pro` of the same name
(a panel next to its board, a KiKit output) is reviewed on its own, and is not a change of the
project in its directory. `.history/`, `*-backups/` and `_autosave-*` paths are ignored.

### Panels

A board without a schematic is reviewed like any board (layers, 3D, semantic diff, DRC) with no
schematic, BOM, netlist or ERC. It is a **panel** (▦ in the viewer, report and PR comment) when any
of these hold, else a plain board (▭):

- KiKit markers: footprints from the `kikit` library or named `KiKit_*` (mousebites, tooling holes,
  fiducials), or nets renamed `Board_<n>-…` for two or more `n`;
- repeated references: half of its footprints or more share their reference with another one (a board
  placed several times; a board drawn from a schematic has unique references);
- "panel" in the file or a directory name.

The first two hold for KiKit output and for most hand-made panels; the name catches a panel with a
single copy. A schematic always makes it a project.

For a panel, kipr also names the boards it holds: a board of the repository whose footprints it
contains k times over (by footprint library, 90 % of them, so a panel of an older revision still
counts) and a KiKit preset next to it (`<name>.json`, `kikit.json`, `panel*.json`); KiKit itself is
not needed. Its changes read as panel changes: fiducials and tooling holes, one `mousebites` entry
per tab ("7 holes moved 20 mm"), outline changes at the `frame` or at `tabs`; the copies of a part
are told apart as `R7·0` … `R7·3` (KiKit's board number, else top to bottom, left to right) and pair
up nearest first, so a regenerated panel (new uuids, new item order) shows only what moved. Layer
status ignores the order of objects in the gerbers and drill files for the same reason.

How it works: each side is extracted with `git archive` (only the paths the project needs);
kicad-cli exports for base and head run in parallel and are cached by the git blob ids each export
reads, so unchanged inputs (e.g. an untouched schematic, or the base side on a re-run) are never
exported twice. Failures of single exports end up in the project's `errors` instead of stopping
the run. Layer status comes from comparing the gerbers (ignoring timestamps and the revision
attribute); the semantic diffs come from parsing the s-expression files.

3D models: KiCad 10's stock 3D library ships STEP only, so boards that name `….wrl` models used to
export bare boards. When a model path doesn't resolve and the same path with the other extension
does (`.wrl`/`.wrz` <-> `.step`/`.stp`, stock `${KICADn_3DMODEL_DIR}` and project-local paths alike),
the export uses that one; only the temporary export checkout is rewritten, never the files the diffs
read, and `pcba3d.models` records every substitution (shown in the 3D tab and the report). Models
behind your own path variables (e.g. `${KICAD_LIBS_DIR}`) resolve when the variable is set in the
environment of `kipr project`; the stock library is found through `$KIPR_KICAD_3DMODEL_DIR`,
`$KICADn_3DMODEL_DIR` or next to kicad-cli.

What counts as changed is decided by one classifier for the BOM, the 3D view, the schematic and
layout change lists, the counts, the report and the comment (see [Change
classification](CONTRACT-project.md#change-classification)): empty fields that appear, `Sim.*`
fields, whitespace, field positions and uuids are ignored; fields that don't name the part (cost,
description, datasheet, notes) are minor; value, footprint, DNP and part-number fields are
significant. The viewer's BOM tab opens on "Changes" (no unchanged or minor rows), and the 3D view
marks and tints only the kinds whose chips are active (added, removed and changed by default;
moved/rotated one click away).

Minor changes: a footprint whose only difference is a 3D model path that swaps the file format
(same directory and stem) or a library nickname rename of an identical footprint is marked
`minor`. Such parts stay listed, but are not counted as changed, are grouped and collapsed in the
viewer, report and PR comment ("105 parts: 3D model format .wrl -> .step") and aren't tinted in the
3D Changes view. Fields that appear or disappear empty (KiCad upgrades add `Sim.Library ""` and
the like) are not reported at all.

### Smart schematic diff

Moving a block of a sheet without touching its connections would light up the whole area. kipr
compares each sheet's connectivity (named by the netlist) instead: symbols, labels, texts, sheet
boxes and wiring that only moved, with every pin on a net of the same name, are `move_only`. The
schematic view's smart diff (default; the move-arrows button or `s` switches to the raw diff,
remembered per browser, `smart=0` in the URL) outlines them faintly, washes them out of the ink
diff and lists them in one collapsed "moved" group; the sheet list and counts leave out sheets with
nothing else. A moved symbol whose value or connections changed stays a change. The report and PR
comment use the smart diff (moved items collapsed and counted).

### Schematic grid check

Symbol pins should sit on a 100 mil grid in the libraries, and everything that connects in a
schematic on the 50 mil connection grid; a part dropped 0.635 mm off it still looks connected but
its wires end next to the pin. `kipr project` checks the head side's connection points against
the grid (`--sch-grid-mil`, 50 by default, with a 0.001 mm tolerance for float noise):

- symbol pin connection points: the placement plus the library pin positions with rotation and
  mirror, for the placed unit and body style (pins of the embedded library symbol, `extends`
  included);
- wire and bus endpoints, bus entries (both ends), junctions, no-connect flags;
- label anchors: local, global and hierarchical labels, net-class and directive flags;
- sheet pins.

So that legacy sheets don't flood a review, the default (`--grid-check changed`) only flags what
the PR **added or moved**: an item is matched to base by its uuid, else by position (same sheet
file and kind), and only its points that are new in head are checked, so a wire whose far end was
dragged off the grid is flagged for that end only. `--grid-check all` checks every point (each
finding still says whether it was `added`, `moved` or `unchanged`); `off` skips the check.

Findings are warnings, grouped per sheet: one symbol (or sheet box) with all its off-grid pins is
one finding, and the off-grid wires, junctions, labels and power symbols that touch its pins are
listed with it (`also 3 wires, 1 power symbol attached`), so a misplaced part with 20 pins reads
as one part. Other off-grid wiring is grouped by shared off-grid points. They show up in
`checks.grid` of the JSON ([contract](CONTRACT-project.md#grid)), the summary (`Off grid`), the
viewer's ERC/DRC tab (a row zooms to the spot on the sheet), the report, the PR comment and, in
CI, as warning annotations on the item's line of the `.kicad_sch` file.

KiCad 10's own ERC has a similar `endpoint_off_grid` warning (pins and wire ends only, one per
symbol, on the project's connection grid); new ones also appear in the ERC delta.

### Impedance check

Net classes with an impedance target get their impedance evaluated on every copper layer their tracks use, on
base and head, so a review shows what a stackup, width or gap change does to controlled lines. Targets come from
the `.kicad_pro`: a KiCad 10 tuning profile, else the class name (`SE_50_CP`, `DP_90_MS`, `BAL_D90_C30_CPWG`,
`90ohm`…; MS/SL/CP/CPWG name the structure), read by [boarddd](https://github.com/CoolNamesAllTaken/boarddd)'s
KiCad reader; the tolerance is the profile's, else ±10 %.

**Solver.** With boarddd's `[field]` extra installed (numpy, scipy; `pip install "kipr[field]"`, which the review
workflow does by default) every cross-section goes through boarddd's tier-2 2D field solver: the real stackup
(every layer's εr, the solder mask on any outer structure, differential coplanar too), with the solver's own error
estimate on each number (typically ±0.3–0.9 %) and the tier-1 closed-form value alongside for comparison.
Without it, or with `impedance-solver: closedform` / `KIPR_IMPEDANCE_SOLVER=closedform`, kipr falls back to
boarddd's closed-form models (Hammerstad-Jensen microstrip with mask, Cohn stripline, Ghione-Naldi CPWG with mask,
Kirschning-Jansen / Cohn coupled lines with mask; about ±2 % of a field solver inside their validity ranges, see
boarddd's `docs/impedance.md`) and says so. The viewer, report and comment name the solver used.

**Geometry.** Per class × layer kipr groups the tracks by width (and, for a pair, by the gap measured between its
parallel segments; else the class's gap), evaluates every group and weights it by its routed length; the coplanar
gap is the zone clearance. Left out, and listed: **launches** (a run of another width than the class's main one,
at most 3 mm long, ending on a pad of its net: a connector launch or a neck-down into a pad), pair **breakouts**
(sections at more than twice the class's main gap, at most 3 mm long) and **uncoupled** pair stretches, runs
shorter than 0.5 mm, and tracks **covered** by a wider one of the same net. The row reports the **controlled length
out of tolerance** (any width), not one width's verdict; width, Z and deviation shown are the longest-routed group's.

Rows are flagged when they are **newly out of tolerance** (🔴), when a **stackup change** moved Z by 0.5 % or
more (computed on the base geometry, so it shows what the stackup alone did), and when the **width or gap**
changed. It never fails the run: fabs hold ±10 %, and Er at frequency, pressed thickness and mask thickness usually
matter more than the solver. The results are in `checks.impedance` ([contract](CONTRACT-project.md#impedance)),
the summary (`Z out / checked`), the ERC/DRC tab of the viewer (symbols, every width group and the inputs in
tooltips), the report, the PR comment (one line plus the flagged rows) and the ping.

### Fonts

KiCad text can use any installed outline font (`(font (face "Poppins"))`). kicad-cli looks the
face up with fontconfig; when it is not installed it substitutes another font (`Font 'Poppins' not
found; substituting 'DejaVu Sans Bold'`), the text gets a different size, and the silkscreen
plots, renders, 3D export and text-dependent DRC results (`silk_edge_clearance`, `silk_overlap`,
text clearance, …) no longer match the designer's machine. Before the first export `kipr project`
therefore:

1. collects every face used by the changed projects' `.kicad_pcb`, `.kicad_sch` and `.kicad_wks`
   files, both sides (footprints and symbols are embedded in them). Fonts embedded in the file
   itself (KiCad 10 `(embedded_files (file … (type font)))`) are used by kicad-cli and need
   nothing; the font file's own names decide which faces they cover;
2. checks each remaining face with `fc-match` against the fontconfig kicad-cli will use
   (`$FONTCONFIG_FILE`, else `/etc/fonts/fonts.conf`);
3. installs the missing ones into a private directory, exposed to kicad-cli through a generated
   `FONTCONFIG_FILE` that includes the previous configuration: all font files under each
   `--fonts DIR` (repeatable; fonts committed to the repository), then, unless `--no-fetch-fonts`,
   the family from [Google Fonts](https://github.com/google/fonts) at the commit pinned in
   `kipr/common/google_fonts_ref.txt`: `ofl/<family>/` or `apache/<family>/` only (the
   METADATA.pb license must agree), every style the family has, cached in `--font-cache`
   (default `$KIPR_CACHE_DIR/fonts` or `~/.cache/kipr/fonts`). A face like `Poppins ExtraBold`
   maps to `ofl/poppins` (only style words are dropped). Nothing else is ever downloaded.

The installed fonts are part of every export's cache key. A face that is still missing (and any
face kicad-cli reports as substituted, whatever fc-match said) is listed in `fonts.missing`, and
the job summary, report, viewer and PR comment show one warning ("Font 'Poppins' is not
available in CI; KiCad substituted it, …"). DRC violations whose items are text in such a face
are marked `font_dependent` (a *font-dependent* badge). Each face's status (`system`, `provided`,
`fetched`, `embedded`, `missing`) is in the JSON ([contract](CONTRACT-project.md#fonts)).

Typical cost: the two public KiCad demo boards of the fixture repo take under a minute cold
(mostly ERC/DRC) and ~3 s with a warm cache. A 4-layer, 160-component board with a 3.5 MB `.kicad_pcb` took 52 s cold
(24 s with `--fast-checks`, 6 s warm) and produced 33 MB of output (0.6 MB JSON).

## GitHub Actions

kipr ships two reusable workflows. The security model is the one of the library review
([library.md](library.md)): an unprivileged job runs PR code and only produces artifacts; a
privileged job, triggered by `workflow_run` from the default branch, turns them into a comment
without ever running PR code.

```
 PR opened / pushed
        │ pull_request
        ▼
 caller "KiCad project review" ──uses──► kipr project-review.yml
   UNPRIVILEGED: contents: read, no secrets. Container kicad/kicad:10.0.x. Checks out the PR head
   (full history), uses the merge base, pip-installs kipr@<ref>, runs `kipr project`, the viewer
   and the report. Annotations for new ERC/DRC errors/warnings and off-grid schematic items, job summary. Artifacts:
   project-review.html (not zipped), project-review-site, project-review-data, pr-meta.
        │ workflow_run: completed (success or failure)
        ▼
 caller "KiCad project review (publish)" ──uses──► kipr project-review-publish.yml
   PRIVILEGED: pull-requests: write, actions: read. No secrets, no checkout.
   Runs from the caller's DEFAULT BRANCH, so the kipr ref comes from merged code.
   resolve-pr (PR number verified via the API) → downloads only project-review.json (data)
   → sticky comment with the summary table and links to the run's artifacts.
```

There is no GitHub Pages preview (internal is private): the comment links to the artifacts of
the run, which expire after `retention-days`. `project-review.html` opens directly in the
browser from the run page; the viewer zip is unzipped and started with `python3 serve.py`
(opening `index.html` from disk works too: every tab including 3D; the layout tab then shows the
per-layer SVG exports instead of the WebGL gerber renderer).

Security notes (same as the library review):

- **Pin kipr.** Use the same immutable ref (tag or full commit sha) in the `uses:` line and in
  `kipr-ref`; the reusable workflow cannot know which ref it was called at.
- The publish caller runs from the default branch, so a PR that edits it (or changes the kipr
  ref) changes nothing until merged. Review changes to these files like code.
- The publish job treats everything from the PR run as untrusted: pr.json is re-checked against
  the API (PR open, in this repo, same head sha), project-review.json is size-capped and
  type-checked, and all its text is escaped (no HTML, @-mentions or images) before it reaches the
  comment. It downloads neither the viewer nor the report.
- The viewer and report are built in the unprivileged job; open them from PRs you don't trust
  with the same care as any downloaded HTML file.

### Reusable workflow inputs

`project-review.yml` (unprivileged):

| Input | Default | Meaning |
|---|---|---|
| `kipr-ref` | `main` | kipr ref to install (use the ref of the `uses:` line) |
| `kipr-repository` | `CoolNamesAllTaken/kipr` | where kipr is installed from |
| `projects` | all | project globs (space/comma/newline separated), as `--projects` |
| `fast-checks` | `false` | `--fast-checks` (skip global libraries in ERC/DRC) |
| `significant-fields` | `""` | `--significant-fields` (which fields count as real changes; empty = part numbers) |
| `fonts` | – | directories in the repository with font files (`.ttf`/`.otf`/`.ttc`) for kicad-cli, as `--fonts` (see [Fonts](#fonts)) |
| `fetch-fonts` | `true` | download faces that are still missing from Google Fonts (pinned google/fonts commit, OFL/Apache only), cached with `actions/cache`; `false` = `--no-fetch-fonts` |
| `step` | `false` | also export STEP models |
| `grid-check` | `changed` | `--grid-check` (schematic connection grid: `changed`, `all` or `off`) |
| `sch-grid-mil` | `50` | `--sch-grid-mil` (the grid in mil) |
| `boarddd-ref` | – | boarddd tag/sha for the impedance check (default: the commit kipr's `pyproject.toml` pins) |
| `kicad-image` | `kicad/kicad:10.0.6-amd64-full` | job container; pins the KiCad version (the `-full` images have the stock 3D models) |
| `jobs` | `4` | parallel kicad-cli processes |
| `max-artifact-mb` | `250` | size budget of the viewer artifact and the report; optional exports are dropped to fit |
| `retention-days` | `14` | artifact retention |
| `libraries-repository` | – | extra KiCad library repo (`owner/repo`): every `*.kicad_sym` / `*.pretty` in it is added to the global symbol/footprint tables (named after the file), so ERC/DRC library checks see the same libraries as the designers. Checked out without credentials and only read as data |
| `libraries-ref` | default branch | ref of `libraries-repository` |
| `libraries-path-var` | – | environment variable set to that checkout, for board paths like `${KICAD_LIBS_DIR}/lib_3d/…` (3D models) |

`project-review-publish.yml` (privileged): `kipr-ref`, `kipr-repository`, `ping` (`true`).

The sticky comment is edited in place and stays near the top of the PR. With `ping: true`, an
update for a new head commit also posts one line at the bottom, e.g.
"🔁 KiCad review updated for `abc1234`: 2 new / 1 fixed DRC, 0 ERC, 5 components changed,
1 off-grid, Z 1/3 out of tolerance (1 new) · results · run", and minimizes the previous one as outdated (deletes it if
minimizing fails). No ping when the comment was just created or when the run is for the commit
the comment already reports (a re-run). Only the bot's own pings (hidden
`<!-- kipr-ping:project -->` marker) are touched. `ping: false` turns it off.

Size budget: the exports get half of `max-artifact-mb` (the viewer adds an offline copy of the
SVGs). When they don't fit, `kipr project ci limit-size` drops, in order: STEP models, SVGs of
unchanged layers, SVGs of unchanged sheets, gerbers of unchanged layers, GLB models, then all
other exports; the JSON then has `null` paths and a note in the project's `errors`. A viewer
that is still too large is not uploaded (a warning says so); the report and the data always are.

The export cache (`actions/cache`, keyed by kicad-cli version + image) keeps only the entries the
last run used; a PR's second run skips the unchanged base side.

### Caller workflows (PantsForBirds/internal)

These replace `.github/workflows/kicad-diff.yml` (and `.github/docker/kiri-kicad10.Dockerfile`,
if nothing else uses it). Replace `KIPR_SHA` with a kipr commit sha (or tag) in both files. The
path filters are the old workflow's; the negated paths are its noise filter, so PRs that only
touch backups don't start a run (kipr applies the same filter again when it looks for projects).

`.github/workflows/kicad-review.yml`:

```yaml
# KiCad project review on PRs to main: schematic, layout and 3D PCBA diffs, BOM, netlist and
# ERC/DRC deltas (kipr). The comment with the results comes from kicad-review-publish.yml.
name: KiCad project review   # kicad-review-publish.yml triggers on this name

on:
  pull_request:
    branches: [main]
    paths:
      - '**/*.kicad_pcb'
      - '**/*.kicad_sch'
      - '**/*.kicad_pro'
      - '!**/.history/**'
      - '!**/*-backups/**'
      - '!**/panelized/**'

permissions:
  contents: read

concurrency:
  group: kicad-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    uses: CoolNamesAllTaken/kipr/.github/workflows/project-review.yml@KIPR_SHA
    with:
      kipr-ref: KIPR_SHA
      fast-checks: ${{ vars.KIPR_FAST_CHECKS == 'true' }}
      # the boards use global libraries and 3D models (${KICAD_LIBS_DIR}/lib_3d/...) from kicad-libs
      libraries-repository: PantsForBirds/kicad-libs
      libraries-path-var: KICAD_LIBS_DIR
```

`.github/workflows/kicad-review-publish.yml`:

```yaml
# Posts the sticky "KiCad project review" comment for kicad-review.yml runs. Runs from main
# (workflow_run), never checks out PR code; only kipr at the pinned ref runs here.
name: KiCad project review (publish)

on:
  workflow_run:
    workflows: ["KiCad project review"]
    types: [completed]

permissions: {}

jobs:
  publish:
    permissions:
      pull-requests: write
      actions: read
    uses: CoolNamesAllTaken/kipr/.github/workflows/project-review-publish.yml@KIPR_SHA
    with:
      kipr-ref: KIPR_SHA
```

Without `libraries-repository`, parts from libraries that only exist in the designers' global
tables show up as `lib_symbol_issues` / `lib_footprint_issues` warnings in the ERC/DRC delta
(new whenever such a part is added) and their `${KICAD_LIBS_DIR}` 3D models are missing. The
workflow adds the libraries' commit to the export cache key; for local runs with other global
tables, use a separate `--cache-dir`.

Notes for internal: the old workflow's sticky comment (header `kicad-diff`) is not touched; the
new one is a separate comment with the hidden marker `<!-- kipr-project-review -->`. Set the
repository variable `KIPR_FAST_CHECKS=true` to trade library-mismatch checks for speed. Private
repositories can call these public reusable workflows; the job needs no secrets (the checkout
uses the run's token).

### CI tools

The GitHub glue is `kipr project ci <tool>` (each has `--help`):

| Tool | Where | What |
|---|---|---|
| `job-summary --out OUT [--annotate] [--repo-dir .] [--summary FILE] [--link NAME=URL]` | review | annotations for new ERC/DRC errors/warnings (placed on the item's line, found by its KiCad uuid) and off-grid schematic items (warnings on their `.kicad_sch` line) + job summary |
| `limit-size --out OUT --max-mb N` | review | drop optional exports to fit a budget |
| `pr-meta --pr N --head-sha … --base-sha … --merge-base … --out DIR` | review | write pr.json |
| `resolve-pr --meta pr.json --repo o/r --run-head-sha SHA` | publish | verify the PR (same tool as the library review) |
| `make-comment --data project-review.json [--run-url …] [--report-url …]` | – | print the comment |
| `post-comment --data … --repo o/r --pr N --head-sha SHA [--no-ping] [--dry-run]` | publish | create/update the sticky comment, ping on a new commit |

Preview the comment for a local review: `kipr project ci make-comment --data review/project-review.json`.

## Layout of the code

| Path | What |
|---|---|
| `kipr/project/review.py` | orchestration, `project-review.json` |
| `kipr/project/discover.py` | changed projects, dependencies, checkout |
| `kipr/project/export.py` | kicad-cli jobs, export cache, file-name mapping |
| `boarddd.io.kicad.pcb` (`load`), `kipr/project/sch.py` | s-expression models of boards (boarddd's KiCad reader) and schematic hierarchies |
| `kipr/project/diff_pcb.py`, `diff_sch.py`, `diff_net.py` | semantic diffs, BOM, netlist, ERC/DRC deltas |
| `kipr/project/grid.py` | schematic connection grid check (`checks.grid`) |
| `kipr/project/impedance.py` | controlled-impedance check (`checks.impedance`, boarddd's KiCad reader, field solver and closed-form models) |
| `kipr/project/ci/` | GitHub glue (above) |
| `kipr/project/cli.py` | `kipr project [review\|site\|report\|ci]` |
| `kipr/project/site.py`, `report.py` | viewer copy + file:// support, no-JS HTML report |
| `kipr/project/web` | symlink to `web/project/` (the viewer, shipped in the wheel as package data) |
| `kipr/common/git.py`, `kicad_cli.py` | shared git (renames, blob ids, `git archive`), kicad-cli (option probing); s-expressions are `boarddd.io.kicad.sexpr` |
| `.github/workflows/project-review*.yml` | reusable workflows |

## Tests

```sh
pytest tests/project                                              # unit tests, no KiCad needed
KIPR_KICAD_CLI=/path/to/kicad-cli pytest tests/project/test_fonts_kicad.py   # fonts with a real kicad-cli
bash tests/project/fixtures/build.sh /tmp/fx                      # fixture repo from KiCad's demos (needs KiCad)
KIPR_FIXTURES=/tmp/fx KIPR_KICAD_CLI=/path/to/kicad-cli pytest tests/project   # + end-to-end
bash tests/web-project/run_all.sh [--real OUT]                    # viewer, site, report (node + Chromium)
bash tests/web-3d/run.sh --browser                                # 3D module (node + Chromium)
node web/project/pcba3d/build_offline.mjs --check                 # committed 3D file:// bundle is fresh
```

The integration test runs `kipr project` on a public fixture repo (`$KIPR_FIXTURES`, tags
`base`/`head`) and is skipped without kicad-cli. No KiCad demo boards are committed to kipr:
`tests/project/fixtures/build.sh` builds the repo from the demos shipped with KiCad
(`/usr/share/kicad/demos` in the `kicad/kicad` image) and the scripted edits in
`tests/project/fixtures/scripts/`; the expected changes are in
[`tests/project/fixtures/CHANGES.md`](../tests/project/fixtures/CHANGES.md).

CI (`.github/workflows/project-tests.yml`): the unit, viewer and 3D tests on ubuntu-latest
(`playwright install --with-deps chromium`); the fixture build, integration tests and a full
`kipr project` run inside `kicad/kicad:10.0.6-amd64-full`; then that real output is opened in
Chromium over http and from disk, every tab including 3D.

### The 3D viewer from disk (file://)

Browsers refuse ES modules and `fetch()` on `file://`. The 2D viewer is bundled into a classic
script by `site.py` itself (a small Python transform). The 3D module (three.js + boarddd, whose
`boarddd/gerber` is the gerber renderer) is bundled with esbuild, and that bundle, `web/project/pcba3d/pcba3d.bundle.js`, is
**committed** instead of built at review time: building a site then needs no node, npx or
network (the KiCad CI image has none of them and a reviewer's machine may not either), and the
wheel ships the exact file that was tested. The bundle also carries the gerber renderer, which the
layout tab uses from disk, so gerbers render from `file://` as well. `site.py` writes the data
packs in Python: `offline/<slug>.js` (the SVG, gerber and drill texts), `offline/pcba3d-<slug>.js`
(GLBs as base64) and `offline/pcba3d-vendor.js` (the renderer's WASM); a test checks they hold the
same bytes as `build_offline.mjs` writes. After changing anything under
`web/project/pcba3d/` or `web/vendor/` (re-vendored with `bash web/vendor/sync_vendor.bash`, a wrapper
around boarddd's `scripts/vendor.mjs` pinned to a boarddd tag), run `node web/project/pcba3d/build_offline.mjs
--no-packs` and commit the bundle; CI fails with `--check` when it is stale (esbuild is pinned
and runs from `web/project/`, so the build is reproducible). `tests/project/test_workflows.py` checks the workflows' security properties
and runs actionlint when it is installed.
