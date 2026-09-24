Parts from [PantsForBirds/kicad-libs](https://github.com/PantsForBirds/kicad-libs) (MIT, see
LICENSE.txt), used as public test fixtures for `kipr library`:

- `head/`: the files PR #9 added (kicad-libs commit 45413e9), minus most STEP models:
  `CMT-9605-85T.step` is kept so the 3D path is exercised, `SH1421-C.step` is kept but no
  footprint here references it (a PR-level "unreferenced 3D model" finding).
- `base/`: `SOP-8_3.76x4.96mm_P1.27mm.kicad_mod` (kicad-libs main), deleted in the fixture's head.

`tests/library/fixture_repo.py` builds a two-commit git repository from them, with base
versions of some head files synthesised by small edits so the review sees added, modified and
deleted footprints and a modified symbol.
