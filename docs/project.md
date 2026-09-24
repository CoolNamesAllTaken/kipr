# Project review (`kipr project`)

`kipr project` reviews every KiCad project that changed between two commits: schematic sheets,
PCB layers and the 3D board as before/after exports from kicad-cli, plus semantic diffs parsed
from the files themselves (components, footprints, tracks, zones, outline, netlist, BOM) and the
ERC/DRC violations that are new or fixed. Everything goes into one output directory that the
viewer (`web/project/`) and the HTML report read; they never run KiCad. The data format is
[CONTRACT-project.md](CONTRACT-project.md).

It replaces the kiri-based `.github/workflows/kicad-diff.yml` of PantsForBirds/internal.

## Running it

Needs Python >= 3.10, git, and KiCad 10's `kicad-cli` for the exports (without it you still get
the semantic diffs, with the netlist taken from the board and no ERC/DRC).

```sh
pip install "kipr @ git+https://github.com/CoolNamesAllTaken/kipr@<ref>"
kipr project --repo . --base origin/main --head HEAD --out review/
python3 -m kipr.project.site --out review/          # add the interactive viewer
python3 -m kipr.project.report --out review/        # review/project-review.html (no JavaScript)
python3 review/serve.py                             # open the viewer
```

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
(opening `index.html` from disk works too, without the gerber/3D views).

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
| `kipr/project/_compat.py` | git / kicad-cli helpers, to be folded into `kipr.common` |
| `.github/workflows/project-review*.yml` | reusable workflows |

## Tests

```sh
pytest tests/project                                              # unit tests, no KiCad needed
KIPR_KICAD_CLI=/path/to/kicad-cli pytest tests/project            # + end-to-end against kipr-fixtures
```

The integration test runs `kipr project` on the public fixture repo (`$KIPR_FIXTURES`, default
`../kipr-fixtures`, tags `base`/`head`, expected changes in its `CHANGES.md`) and is skipped
without kicad-cli. `tests/project/test_workflows.py` checks the workflows' security properties
and runs actionlint when it is installed.
