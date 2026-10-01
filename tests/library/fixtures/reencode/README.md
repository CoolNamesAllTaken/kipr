Re-encode fixtures, from [PantsForBirds/kicad-libs](https://github.com/PantsForBirds/kicad-libs)
(MIT, see `../kicad-libs/LICENSE.txt`), for `tests/library/test_library_reencode*.py`:

- `Custom_RF_Amplifier.k8.kicad_sym`: `lib_sch/Custom_RF_Amplifier.kicad_sym` at kicad-libs
  0dc129f (KiCad 8, format 20231120), the base of kicad-libs PR #13.
- `Custom_RF_Amplifier.k10.kicad_sym`: `kicad-cli sym upgrade --force` of it by KiCad 10.0.6.
  Byte-identical to the first 490 lines of the file the KiCad 10 symbol editor saved in PR #13
  (the rest is the symbol that PR added).
- `SOP-8_3.76x4.96mm_P1.27mm.k7.kicad_mod`: the KiCad 7 footprint also in `../kicad-libs/base`.
- `SOP-8_3.76x4.96mm_P1.27mm.k10.kicad_mod`: `kicad-cli fp upgrade --force` of it by KiCad 10.0.6
  (KiCad gives every object a fresh uuid, so another run differs in uuids only).
