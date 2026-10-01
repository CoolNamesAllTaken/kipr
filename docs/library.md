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
| `kipr library checks --out OUT [--klc-utils DIR [--kicad-cli PATH]] [--site-url URL]` | `OUT/review.json`, `OUT/review.md` |
| `kipr library site --out OUT [--no-offline]` | the viewer (`index.html`, `js/`, `data.js`, `offline/`, `serve.py`) |
| `kipr library report --out OUT [--output FILE] [--max-mb 20]` | `OUT/component-review.html` |

Useful options of the end-to-end run (see `kipr library --help`):

- **Library layout** (defaults are kicad-libs'): `--lib-fp lib_fp` (`<Lib>.pretty/*.kicad_mod`),
  `--lib-sch lib_sch` (`*.kicad_sym`), `--lib-3d lib_3d` (models referenced as
  `${KICAD_LIBS_DIR}/<lib-3d>/…`), `--datasheets datasheets` (PDFs matched to parts). `.` means
  the repository root.
- `--klc-utils DIR`: also run the official KLC checker; `kipr library ci fetch-klc-utils DIR`
  fetches kicad-library-utils at the pinned commit (`kipr/library/ci/klc_utils.ref`).
- `--klc-error-severity error|warning|info` (or `CR_KLC_ERROR_SEVERITY`; default `warning`):
  severity of the "KLC could not check this item" finding, so whether an item the KLC checker
  could not check is `fail`, `warn` (default) or left to its other findings. See
  [When the KLC checker fails](#when-the-klc-checker-fails).
- `--no-reencode-check`, `--kicad-cli PATH`: see [Re-encoded by KiCad vs. edited](#re-encoded-by-kicad-vs-edited).
- `--fetch-stock-models [--stock-models-dir DIR]`: download `${KICADn_3DMODEL_DIR}` models from
  kicad-packages3D at the tag pinned in `kipr/library/render/stock_models_tag.txt`.
- `--no-3d`, `--no-preview`, `--png-size`, `--repo-name owner/repo` (default
  `$GITHUB_REPOSITORY`, else the github.com `origin` remote), `--report FILE`, `--skip STAGE`.

### When the KLC checker fails

An item only counts as KLC-checked when kicad-library-utils demonstrably checked it: its JUnit
report has a test case for the item and agrees with the exit code (0 clean, 2 warnings, 3 errors).
Otherwise the item gets KLC status `error` instead of a pass:

- `review.json`: `items[id].klc = {"status": "error", "reason": "…", "attempts": n}` (additive;
  `{"status": "ok", "attempts": n}` when it ran), the "KiCad KLC checker" check has result
  `error`, and there is a finding "KLC could not check this item: <reason>." with the last lines
  of the checker's output, at `--klc-error-severity` (default warning, so the verdict is at least
  `warn`).
- The summary says "**KLC could not check N item(s)**". The HTML report, the viewer and the PR
  comment mark the item "KLC not checked"; the job summary counts them.

Causes it reports: a crash (traceback; e.g. a symbol property without `(effects)`), "Could not
parse" that an upgraded copy doesn't fix (below), a timeout (120 s), a killed process, and a missing, unreadable or empty report or one that contradicts the exit code.
The last three, and a killed process, can be flakes (`check_symbol.py` can drop a worker's
results when the worker exits right after posting them), so they are retried once; a retry that
succeeds is noted in the check's detail. Crashes, parse errors and timeouts are not retried, and
neither are real passes.

### Older file formats: KLC on a KiCad-upgraded copy

The pinned checker reads only its own symbol file version (`20251024`) and can't load legacy
`(module …)` footprints. For those items kipr writes a temporary copy re-saved by KiCad
(`kicad-cli sym upgrade` / `fp upgrade --force`, `kipr.common.kicad_cli.upgrade_text`), runs KLC
on the copy and attaches the findings to the original item. The repository's files are never
changed.

- The item's `klc` record gets `"upgraded": {"from": "20241209", "to": "20251024", "kicad":
  "10.0.6"}` and the check's detail says "checked on a KiCad 10.0.6-upgraded copy (file version
  20241209 → 20251024)". Only if the upgrade fails (or the copy is still unreadable) is the item
  "KLC could not check".
- Line numbers: KLC findings carry no line and are shown at the item's first line. A symbol
  finding that names exactly one pin ("Pin GND (6) @ (0,-600)") gets that pin's line in the
  original file (matched by pin number and name), never a line of the copy.
- Footprints the checker can read are checked as they are, even at older versions: on an
  upgraded footprint this checker version misses e.g. an unlocked RefDes (F5.1/F5.2), which it
  does find in the KiCad 6/7 encoding. Symbols at the checker's version gave identical KLC
  results before and after a forced re-save (11/11 kicad-libs libraries).
- kicad-cli is found via `--kicad-cli`, `$KIPR_KICAD_CLI`, `$KICAD_CLI` or `PATH` (the
  library-review job runs in the KiCad image). The copy is saved next to the item's source in OUT
  (`items/<slug>/head.klc-upgraded.kicad_sym|.kicad_mod` + `.json` with the source's sha256), so
  the privileged publish job, which never runs KiCad on PR data, re-checks the same copy as
  data. A copy whose sha256 doesn't match the source is ignored.

### Re-encoded by KiCad vs. edited

A symbol library is one file, so a designer who edits one symbol in a newer KiCad re-saves all
of them in the new format (`(show_name no)`, `(do_not_autoplace no)`, `(hide yes)` moved out of
`(effects)`, …), and a footprint opened and saved in a newer KiCad changes text everywhere. The
render step tells such **re-encoded** parts from real edits (`kipr/library/render/reencode.py`).
It is deliberately conservative: a real edit shown as re-encoded would hide it from review, so
when in doubt a part stays `modified`.

For each `modified` symbol or footprint present at base and head (not ones changed only through
a 3D model file or a parent symbol):

1. **Reference upgrade (the criterion).** The base file is upgraded with `kicad-cli sym upgrade`
   / `fp upgrade --force`, i.e. KiCad's own loader and writer (the code the editors save with),
   and the part in the result is compared with the part at head. The comparison ignores only
   whitespace, number spelling, `uuid`s (KiCad generates fresh ones when it loads an old file)
   and the file header. It is only used when kicad-cli writes the head's file format (same
   `version`), i.e. it is the KiCad the head was saved with. On kicad-libs PR #13 the KiCad 10.0.6
   upgrade of the base was byte-identical to what the KiCad 10 symbol editor wrote, so no
   editor-specific defaults are needed.
2. **Semantic comparison (the safety net).** Both sides are compared field by field after the
   format normalisations below and nothing else: pins (number, name, type, shape, position,
   length, orientation, visibility, fonts), graphics, properties (value, position, visibility,
   effects), units/body styles, flags (`in_bom`, `on_board`, `power`, `exclude_from_sim`, …),
   `pin_names`/`pin_numbers`. Anything without a rule must match exactly. Footprints are compared
   on the version-independent model of kipr's footprint parser (pads, graphics, texts, zones,
   properties, attributes, 3D models, embedded files).

A part is `re-encoded` only if (1) says identical **and** (2) agrees. If (2) finds a difference
that KiCad's upgrade does not, the part stays `modified` (with a note). Without a usable
kicad-cli (none on `PATH`, `$KIPR_KICAD_CLI` / `$KICAD_CLI` / `--kicad-cli`, or one that writes
another format than the head), a **symbol** may still be `re-encoded` when (2) agrees, the base
and head renders are pixel identical and, if a kicad-cli of another version is available,
re-saving base and head with it gives identical results; a **footprint** then always stays
`modified`. `--no-reencode-check` turns the check off.

| Normalisation | Applies to | Why it cannot hide an edit |
|---|---|---|
| `whitespace` | both | KiCad gives whitespace no meaning outside strings |
| `numbers` | both | `1` = `1.0` = `1.000000`, `-0` = `0`; values rounded to 1e-6 (KiCad stores 1e-4 mm in symbols, 1e-6 mm in footprints) |
| `uuid` | both | identity, not content; KiCad generates uuids for objects that had none |
| `header` | both | `version`/`generator`/`generator_version` are file-level; reported as the library note |
| `property-id` | semantic | `(id N)` of KiCad 6/7 fields; KiCad 8 identifies fields by name |
| `flag-spelling` | semantic | bare `hide`/`bold`/`italic` (KiCad 6/7) = `(hide yes)`/`(bold yes)`/`(italic yes)` |
| `hide-location` | semantic | `(effects (hide yes))` (KiCad ≤ 9) and the field's own `(hide yes)` (KiCad 10) are one flag |
| `defaults` | semantic | `(hide no)`, `(bold no)`, `(italic no)`, `(show_name no)`, `(do_not_autoplace no)`, `(exclude_from_sim no)`, `(in_bom yes)`, `(on_board yes)`, `(in_pos_files yes)`, `(duplicate_pin_numbers_are_jumpers no)`, `(embedded_fonts no)` are KiCad's defaults; a non-default value is always compared |
| `default-color` | semantic | `(color 0 0 0 0)` in a stroke/fill means "default colour"; KiCad 8+ omits it |
| `ki-description` | semantic | KiCad 8 turned `ki_description` into the `Description` field (only when there is no non-empty `Description`; the text, position and effects are still compared) |
| `empty-description` | semantic | KiCad 8 adds an empty `Description` field to every symbol; an empty field draws nothing |
| `arc-direction` | semantic | an arc start→mid→end is the arc end→mid→start (KiCad 8 reverses some); all three points are compared |
| `item-order` | semantic | KiCad sorts graphic items and pins when it saves; point lists (`pts`) and atoms keep their order |
| `fp-name` | footprint net | KiCad names a footprint after its file (same file at base and head) |
| `fp-empty-props` | footprint net | empty `Footprint`/`Datasheet`/`Description` properties = absent |
| `fp-pad-layers` | footprint net | a pad's layer list is a set |
| `fp-arc-direction` | footprint net | as `arc-direction` |
| `fp-text-angle` | footprint net | text angles modulo 360° (`-180` = `180`) |
| `fp-text-vars` | footprint net | `%R`/`%V` (KiCad 5) = `${REFERENCE}`/`${VALUE}` |
| `fp-unlocked` | footprint net | the `unlocked` editing flag of texts (KiCad 8 changed its default) only affects editing |
| `fp-attr-tht` | footprint net | no footprint type in `attr` (KiCad 5) means through-hole |
| `fp-closed-poly` | footprint net | a zone outline whose last point repeats the first |

The `fp-*` rules only decide how often the footprint safety net raises a false alarm: a footprint
is never `re-encoded` without KiCad's reference upgrade. Each rule has a test in
`tests/library/test_library_reencode.py` showing what it equates and what it still tells apart.

Validated on kicad-libs: PR #13 (AD8314, BLB01, LM73100RPWR re-encoded; nothing else modified)
and the whole history of `main` (`tests/library/reencode_history.py REPO --rev main` compares
every changed part with a KiCad round trip of both sides): no part called re-encoded that KiCad
sees as different. Known false alarm: KiCad 6 → 7 recomputed some arc mid points (by 3 µm), so
such parts in KiCad 6 files stay `modified`.

Output (additive to the formats):

- `manifest.json` items: `status: "re-encoded"` and `reencode` = `{reencoded, method
  ("reference-upgrade" | "semantic+render" | "semantic"), explanation ("file format upgraded
  20231120 → 20251024 by KiCad 10.0; no content change"), base_format, head_format,
  base/head_generator_version, kicad_cli_version, differences [...], note?}`. Modified parts get
  the same record with `reencoded: false` and the list of what differs from KiCad's upgrade of
  the base (or from the normalised base).
- `manifest.json` `reencoded_files`: library files whose format version changed, with a `note`
  ("Custom_RF_Amplifier.kicad_sym was re-saved by a newer KiCad (format 20231120 → 20251024,
  KiCad 8.0 → KiCad 10.0)") and the ids of their re-encoded and modified parts.
- Re-encoded parts are not checked (no `review.json` entry), not counted as changes, and left out
  of the overall verdict, the check run, annotations and inline comments. The PR comment, job
  summary, HTML report and viewer list them collapsed ("3 symbols re-encoded by KiCad, no
  changes"), with the library notes; the viewer and report still show each one's raw text diff.
  A modified part in a re-saved file shows "Changed beyond the file format upgrade" with the list.
- Like the item list itself, the status comes from the unprivileged render job: the publish stage
  cannot re-check it (it has no PR checkout), so a PR that controls that job can hide a part
  either way.

The data formats (`manifest.json` schema 1, `review.json` schema 1) are unchanged from kicad-libs
apart from the additive fields above and the KLC status.
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
| `klc-error-severity` | `warning` | severity of "KLC could not check this item" (`error`, `warning`, `info`) |
| `use-kicad-cli` | `false` | also export reference SVGs with kicad-cli |
| `retention-days` | `30` | artifact retention |

`library-review-publish.yml` (privileged): `kipr-ref`, `kipr-repository`, `lib-3d` (finding
messages), `klc` (`true`), `klc-error-severity` (`warning`; same value as library-review.yml),
`pages-url` (default `https://<owner>.github.io/<repo>/`),
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
| `kipr/library/render/` | change detection, re-encode detection (`reencode.py`), footprint/symbol SVG renderers, PNG/diff, 3D (GLB, STEP copies, stock models) |
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
KIPR_KICAD_CLI=/path/to/kicad-cli KIPR_TEST_KICAD_LIBS=/path/to/kicad-libs \
    python -m pytest tests/library/test_library_reencode.py      # re-encode tests against real KiCad 10 + kicad-libs PR #13
```

The re-encode tests replay committed KiCad 10.0.6 output (`tests/library/fixtures/reencode`)
through a fake kicad-cli, so they need no KiCad; with a real KiCad 10 kicad-cli they also check
the fixtures against it, and with `KIPR_TEST_KICAD_LIBS` (a kicad-libs clone with
`git fetch origin pull/13/head:pr13`) they run PR #13.

Fixtures are public kicad-libs parts (`tests/library/fixtures/kicad-libs`, MIT);
`tests/library/fixture_repo.py DIR` builds a two-commit repository from them with added,
modified and deleted footprints, a modified symbol, a 3D model and an unreferenced model.
