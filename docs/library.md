# Library review (`kipr library`)

Automated review of changes to KiCad **footprints**, **symbols** and **3D models** in a library
repository. Ported from PantsForBirds/kicad-libs `tools/component-review/` (kicad-libs PRs #9 and
#10); output, report and viewer are the same. For each changed part it:

- renders before/after images, a red/green diff overlay for modified parts, per-layer SVGs and
  3D previews (GLB + a software-rendered 2×2 preview sheet; STEP models for the browser);
- runs deterministic checks: pad counts, KLC-style rules, properties, 3D-model paths and names,
  symbol pins vs. footprint pads, and optionally KiCad's official KLC checker
  (kicad-library-utils). No network access or secrets needed;
- writes a self-contained HTML report (no JavaScript, images embedded) and an interactive viewer
  (static site; also opens from disk, e.g. an unzipped CI artifact).

## Running it

```sh
pip install "kipr[3d] @ git+https://github.com/CoolNamesAllTaken/kipr@main"   # [3d]: GLB + 3D previews
kipr library --repo . --base "$(git merge-base origin/main HEAD)" --head HEAD --out cr-out
python3 cr-out/serve.py          # or open cr-out/index.html; cr-out/component-review.html is the report
```

Needs Python 3.10+, the system cairo library (`libcairo2`) and ideally DejaVu fonts.
`kicad-cli` is optional (`--use-kicad-cli` adds reference SVGs exported by KiCad).

`kipr library` runs the four stages below. Each is also a subcommand:

| Command | Writes |
|---|---|
| `kipr library render --repo . --base B --head H --out OUT [--pr N]` | `OUT/manifest.json`, `OUT/items/<slug>/…` |
| `kipr library checks --out OUT [--klc-utils DIR] [--site-url URL]` | `OUT/review.json`, `OUT/review.md` |
| `kipr library site --out OUT [--no-offline]` | the viewer (`index.html`, `js/`, `data.js`, `offline/`, `serve.py`) |
| `kipr library report --out OUT [--output FILE] [--max-mb 20]` | `OUT/component-review.html` |

Useful options of the end-to-end run (see `kipr library --help`):

- **Library layout** (defaults are kicad-libs'): `--lib-fp lib_fp` (`<Lib>.pretty/*.kicad_mod`),
  `--lib-sch lib_sch` (`*.kicad_sym`), `--lib-3d lib_3d` (models referenced as
  `${KICAD_LIBS_DIR}/<lib-3d>/…`), `--datasheets datasheets` (PDFs matched to parts). `.` means
  the repository root.
- `--klc-utils DIR`: also run the official KLC checker; `kipr library ci fetch-klc-utils DIR`
  fetches kicad-library-utils at the pinned commit (`kipr/library/ci/klc_utils.ref`).
- `--fetch-stock-models [--stock-models-dir DIR]`: download `${KICADn_3DMODEL_DIR}` models from
  kicad-packages3D at the tag pinned in `kipr/library/render/stock_models_tag.txt`.
- `--no-3d`, `--no-preview`, `--png-size`, `--repo-name owner/repo` (default
  `$GITHUB_REPOSITORY`, else the github.com `origin` remote), `--report FILE`, `--skip STAGE`.

The data formats (`manifest.json` schema 1, `review.json` schema 1) are unchanged from kicad-libs.
The viewer is documented in [`web/library/README.md`](../web/library/README.md).

## GitHub Actions

kipr ships three reusable workflows. A library repository calls them from three ~15-line
workflows. The security model is the one kicad-libs already had:

```
 PR opened / pushed (fork or branch)
        │ pull_request
        ▼
 caller "Component review" ──uses──► kipr library-review.yml
   UNPRIVILEGED: contents: read, no secrets. Checks out the PR head, pip-installs kipr@<ref>,
   renders + checks + viewer + report. Artifacts: component-review.html (not zipped),
   component-review-site, component-review-data, pr-meta. Job summary + annotations.
        │ workflow_run: completed + success
        ▼
 caller "Component review (publish)" ──uses──► kipr library-review-publish.yml
   PRIVILEGED: contents/pull-requests/checks: write, actions: read. No secrets.
   Runs from the caller's DEFAULT BRANCH (workflow_run always does), so the kipr ref comes
   from merged code. Never checks out or runs PR code: the only code is kipr@<ref>.
   resolve-pr (PR number verified via the API) → sanitize-site (allow-listed data only)
   → checks re-run with trusted code → trusted viewer → gh-pages pr/<N>/ → sticky comment,
   inline review and "Component review" check.
        ▼
 gh-pages ──(GitHub Pages)──► https://<owner>.github.io/<repo>/pr/<N>/#<slug>

 PR closed ── pull_request_target ──► caller "Component review (cleanup)" ──uses──►
   kipr library-review-cleanup.yml: removes pr/<N>/ from gh-pages, notes it on the comment.
```

Security notes:

- **Pin kipr.** Use the same immutable ref (a tag or a full commit sha) in the `uses:` line and in
  `kipr-ref`. The reusable workflow can't find out which ref it was called at, so `kipr-ref`
  says which kipr code gets `pip install`ed. The publish and cleanup callers run from the
  default branch, so a PR that edits them (or changes `kipr-ref`) changes nothing until it is
  merged. Review changes to these files and to the pinned ref like code.
- The unprivileged run executes the PR's caller file (it may point at any kipr) but only gets a
  read-only token; its outputs are treated as untrusted data by the publish stage, exactly as
  before (see the list in `kipr/library/ci/sanitize_site.py`).
- The HTML report and the viewer zip are built in the unprivileged job; open reports from PRs you
  don't trust with the same care as a downloaded HTML file.

### Reusable workflow inputs

`library-review.yml` (unprivileged):

| Input | Default | Meaning |
|---|---|---|
| `kipr-ref` | `main` | kipr ref to install (use the ref of the `uses:` line) |
| `kipr-repository` | `CoolNamesAllTaken/kipr` | where kipr is installed from |
| `lib-fp`, `lib-sch`, `lib-3d`, `datasheets` | `lib_fp`, `lib_sch`, `lib_3d`, `datasheets` | library layout |
| `kicad-image` | `kicad/kicad:10.0` | job container; `none` runs on the plain runner |
| `fetch-stock-models` | `true` | download stock KiCad 3D models (cached with actions/cache) |
| `klc` | `true` | run the official KLC checker (cached) |
| `use-kicad-cli` | `false` | also export reference SVGs with kicad-cli |
| `retention-days` | `30` | artifact retention |

`library-review-publish.yml` (privileged): `kipr-ref`, `kipr-repository`, `lib-3d` (finding
messages), `klc` (`true`), `pages-url` (default `https://<owner>.github.io/<repo>/`),
`fail-conclusion` (`neutral`; `failure` lets you require the check, or `success`).

`library-review-cleanup.yml`: `kipr-ref`, `kipr-repository`.

Artifact names, the sticky-comment marker (`<!-- component-review -->`) and the check-run name
(**Component review**) are unchanged from kicad-libs, so existing PR comments are updated in place.

### Caller workflows (kicad-libs)

Replace `KIPR_SHA` with a kipr commit sha (or tag) in all three files. The repository variables
kicad-libs documents keep working: `CR_PUBLISH=false` turns off publish and cleanup,
`CR_KICAD_IMAGE`, `CR_FETCH_STOCK_MODELS`, `CR_KLC`, `CR_PAGES_URL`, `CR_FAIL_CONCLUSION`.

`.github/workflows/component-review.yml`:

```yaml
name: Component review   # the publish workflow below triggers on this name
on:
  pull_request:
    paths: ["lib_fp/**", "lib_sch/**", "lib_3d/**", ".github/workflows/component-review.yml"]
permissions:
  contents: read
concurrency:
  group: component-review-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
jobs:
  review:
    uses: CoolNamesAllTaken/kipr/.github/workflows/library-review.yml@KIPR_SHA
    with:
      kipr-ref: KIPR_SHA
      kicad-image: ${{ vars.CR_KICAD_IMAGE || 'kicad/kicad:10.0' }}
      fetch-stock-models: ${{ vars.CR_FETCH_STOCK_MODELS != 'false' }}
      klc: ${{ vars.CR_KLC != 'false' }}
```

`.github/workflows/component-review-publish.yml`:

```yaml
name: Component review (publish)
on:
  workflow_run:
    workflows: ["Component review"]
    types: [completed]
permissions: {}
jobs:
  publish:
    if: vars.CR_PUBLISH != 'false'
    permissions: { contents: write, pull-requests: write, checks: write, actions: read }
    uses: CoolNamesAllTaken/kipr/.github/workflows/library-review-publish.yml@KIPR_SHA
    with:
      kipr-ref: KIPR_SHA
      klc: ${{ vars.CR_KLC != 'false' }}
      pages-url: ${{ vars.CR_PAGES_URL }}
      fail-conclusion: ${{ vars.CR_FAIL_CONCLUSION || 'neutral' }}
```

`.github/workflows/component-review-cleanup.yml`:

```yaml
name: Component review (cleanup)
on:
  pull_request_target:
    types: [closed]
    paths: ["lib_fp/**", "lib_sch/**", "lib_3d/**"]
permissions: {}
jobs:
  cleanup:
    if: vars.CR_PUBLISH != 'false'
    permissions: { contents: write, pull-requests: write }
    uses: CoolNamesAllTaken/kipr/.github/workflows/library-review-cleanup.yml@KIPR_SHA
    with:
      kipr-ref: KIPR_SHA
```

Once these are merged, kicad-libs' `tools/component-review/` can be deleted. Repository setup
(GitHub Pages from `gh-pages`, *Read and write* workflow permissions, fork-PR approval) is the
same as described in kicad-libs' `tools/component-review/README.md`.

### CI tools

The GitHub glue is `kipr library ci <tool>` (each has `--help`):

| Tool | What |
|---|---|
| `job-summary --out OUT [--annotate] [--summary FILE] [--link NAME=URL]` | annotations + job summary |
| `sanitize-site --src UNTRUSTED --dst CLEAN` | copy an untrusted artifact into a clean site dir |
| `resolve-pr --meta pr.json --repo o/r --run-head-sha SHA` | verify the PR of a workflow_run |
| `deploy-pages --repo o/r --pr N --site DIR [--push] [--remote URL]` / `--delete` | gh-pages `pr/<N>/` |
| `make-comment --site DIR --repo o/r --pr N --head-sha SHA` | print the sticky comment |
| `post-review --site DIR --repo o/r --pr N --head-sha SHA [--dry-run] [--files-json F]` | comment + inline review + check |
| `fetch-klc-utils DIR` | kicad-library-utils at the pinned commit |

Preview what CI would post without writing anything (`--dry-run` only makes GET requests; add
`--files-json FILE` to run fully offline):

```sh
kipr library ci sanitize-site --src cr-out --dst /tmp/cr-site
kipr library ci post-review --site /tmp/cr-site --repo PantsForBirds/kicad-libs --pr 8 \
    --head-sha "$(git rev-parse HEAD)" --dry-run
```

## Layout of the code

| Path | What |
|---|---|
| `kipr/common/sexpr.py` | s-expression parser with source spans (KiCad 5–10, `\|base64\|` data) |
| `kipr/common/git.py`, `kipr/common/kicad_cli.py` | read-only git access; finding/running kicad-cli |
| `kipr/library/layout.py` | the configurable library directories |
| `kipr/library/render/` | change detection, footprint/symbol SVG renderers, PNG/diff, 3D (GLB, STEP copies, stock models) |
| `kipr/library/checks/` | KLC-style rules (`kicad_checks.py`), official KLC checker glue (`klc_utils.py`) |
| `kipr/library/report.py`, `kipr/library/site.py` | the HTML report; copies the viewer + file:// support into OUT |
| `kipr/library/ci/` | GitHub glue (above) |
| `web/library/` | the static viewer (shipped as package data via the `kipr/library/web` symlink) |
| `tests/library/` | pytest suite, node unit tests (`viewer/unit.test.mjs`), Playwright smoke/XSS checks |

## Tests

```sh
pip install -e ".[3d,test]" && python -m playwright install chromium
python -m pytest tests/library          # python + node + headless Chromium (browser tests skip without playwright)
KIPR_SHOTS_DIR=/tmp/shots python -m pytest tests/library/test_library_viewer.py   # keep the screenshots
CR_KLC_UTILS=/path/to/kicad-library-utils python -m pytest tests/library -k klc   # the real KLC checker
```

Fixtures are public kicad-libs parts (`tests/library/fixtures/kicad-libs`, MIT);
`tests/library/fixture_repo.py DIR` builds a two-commit repository from them with added,
modified and deleted footprints, a modified symbol, a 3D model and an unreferenced model.
