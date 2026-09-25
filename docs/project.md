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
| `--projects GLOB` | all | only projects whose directory or directory name matches; repeatable |
| `--kicad-cli PATH` | `$KIPR_KICAD_CLI`, then `PATH` | kicad-cli to use |
| `--fast-checks` | off | ERC/DRC without the global KiCad libraries: several times faster, library-mismatch checks dropped |
| `--step` | off | also export STEP models |
| `--no-glb` | off | skip the GLB (3D PCBA) export |
| `--no-export` | off | semantic diffs only, never run kicad-cli |
| `--jobs N` | 4 | parallel kicad-cli processes |
| `--cache-dir D` | `$KIPR_CACHE_DIR` or `~/.cache/kipr` | export cache |
| `--repo-url URL` | `origin` if on GitHub | for source links |

What counts as a changed project: a directory with a `.kicad_pro` in which a `.kicad_sch`,
`.kicad_pcb`, `.kicad_pro`, `.kicad_dru`, lib table, project library or 3D model changed, or that
references (through relative or `${KIPRJMOD}` paths in its lib tables / board) a library or 3D
model elsewhere in the repo that changed. Like the old workflow, `.history/`, `*-backups/` and
`panelized/` paths are ignored.

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

Minor changes: a footprint whose only difference is a 3D model path that swaps the file format
(same directory and stem) or a library nickname rename of an identical footprint is marked
`minor`. Such parts stay listed, but are not counted as changed, are grouped and collapsed in the
viewer, report and PR comment ("105 parts: 3D model format .wrl -> .step") and aren't tinted in the
3D Changes view. Fields that appear or disappear empty (KiCad upgrades add `Sim.Library ""` and
the like) are not reported at all.

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
   and the report. Annotations for new ERC/DRC errors/warnings, job summary. Artifacts:
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
| `step` | `false` | also export STEP models |
| `kicad-image` | `kicad/kicad:10.0.6-amd64-full` | job container; pins the KiCad version (the `-full` images have the stock 3D models) |
| `jobs` | `4` | parallel kicad-cli processes |
| `max-artifact-mb` | `250` | size budget of the viewer artifact and the report; optional exports are dropped to fit |
| `retention-days` | `14` | artifact retention |
| `libraries-repository` | – | extra KiCad library repo (`owner/repo`): every `*.kicad_sym` / `*.pretty` in it is added to the global symbol/footprint tables (named after the file), so ERC/DRC library checks see the same libraries as the designers. Checked out without credentials and only read as data |
| `libraries-ref` | default branch | ref of `libraries-repository` |
| `libraries-path-var` | – | environment variable set to that checkout, for board paths like `${KICAD_LIBS_DIR}/lib_3d/…` (3D models) |

`project-review-publish.yml` (privileged): `kipr-ref`, `kipr-repository`.

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
| `job-summary --out OUT [--annotate] [--repo-dir .] [--summary FILE] [--link NAME=URL]` | review | annotations for new ERC/DRC errors/warnings (placed on the item's line, found by its KiCad uuid) + job summary |
| `limit-size --out OUT --max-mb N` | review | drop optional exports to fit a budget |
| `pr-meta --pr N --head-sha … --base-sha … --merge-base … --out DIR` | review | write pr.json |
| `resolve-pr --meta pr.json --repo o/r --run-head-sha SHA` | publish | verify the PR (same tool as the library review) |
| `make-comment --data project-review.json [--run-url …] [--report-url …]` | – | print the comment |
| `post-comment --data … --repo o/r --pr N --head-sha SHA [--dry-run]` | publish | create/update the sticky comment |

Preview the comment for a local review: `kipr project ci make-comment --data review/project-review.json`.

## Layout of the code

| Path | What |
|---|---|
| `kipr/project/review.py` | orchestration, `project-review.json` |
| `kipr/project/discover.py` | changed projects, dependencies, checkout |
| `kipr/project/export.py` | kicad-cli jobs, export cache, file-name mapping |
| `kipr/project/pcb.py`, `sch.py` | s-expression models of boards and schematic hierarchies |
| `kipr/project/diff_pcb.py`, `diff_sch.py`, `diff_net.py` | semantic diffs, BOM, netlist, ERC/DRC deltas |
| `kipr/project/ci/` | GitHub glue (above) |
| `kipr/project/cli.py` | `kipr project [review\|site\|report\|ci]` |
| `kipr/project/site.py`, `report.py` | viewer copy + file:// support, no-JS HTML report |
| `kipr/project/web` | symlink to `web/project/` (the viewer, shipped in the wheel as package data) |
| `kipr/common/git.py`, `kicad_cli.py`, `sexpr.py` | shared git (renames, blob ids, `git archive`), kicad-cli (option probing) and s-expression helpers |
| `.github/workflows/project-review*.yml` | reusable workflows |

## Tests

```sh
pytest tests/project                                              # unit tests, no KiCad needed
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
script by `site.py` itself (a small Python transform). The 3D module (three.js + the gerber
renderer) is bundled with esbuild, and that bundle, `web/project/pcba3d/pcba3d.bundle.js`, is
**committed** instead of built at review time: building a site then needs no node, npx or
network (the KiCad CI image has none of them and a reviewer's machine may not either), and the
wheel ships the exact file that was tested. The bundle also carries the gerber renderer, which the
layout tab uses from disk, so gerbers render from `file://` as well. `site.py` writes the data
packs in Python: `offline/<slug>.js` (the SVG, gerber and drill texts), `offline/pcba3d-<slug>.js`
(GLBs as base64) and `offline/pcba3d-vendor.js` (the renderer's WASM); a test checks they hold the
same bytes as `build_offline.mjs` writes. After changing anything under
`web/project/pcba3d/` or `web/project/vendor/`, run `node web/project/pcba3d/build_offline.mjs
--no-packs` and commit the bundle; CI fails with `--check` when it is stale (esbuild is pinned
and runs from `web/project/`, so the build is reproducible). `tests/project/test_workflows.py` checks the workflows' security properties
and runs actionlint when it is installed.
