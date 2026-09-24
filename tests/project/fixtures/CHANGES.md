# Expected diff `base` → `head`

Two public KiCad 10.0.6 demo projects (from the official `kicad/kicad:10.0.6` image,
`/usr/share/kicad/demos/`). Everything below was verified with `kicad-cli` 10.0.6 on both tags
(ERC/DRC JSON, netlist, BOM, pos, gerbers, drill, SVG, GLB, STEP all export). Coordinates are
board mm (y down, as in the files; `.pos` CSVs negate y). Rotations are degrees as reported by
pcbnew/`.pos`.

Matching advice for diff tools: match footprints by **reference** or by **path** (symbol UUID
path), not by footprint UUID. A footprint swap (C9) gives the footprint a new UUID but keeps the
same path. Tracks keep their UUIDs when edited. KiCad 10 files carry net *names*, not net codes.

## pic_programmer (2 layers, THT, 3D models, 2 sheets: root + `pic_sockets`)

| # | Category | Item | base | head |
|---|---|---|---|---|
| 1 | changed resistor value | **R7** (sch + pcb) | `10K` | `4.7K` |
| 2 | added component | **C10** 100nF, `Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm`, root sheet (185.42, 180.34) with VCC/GND power symbols on its pins; pcb (115.57, 106.172) rot 0, pad 1 = VCC (sits on U2's VCC track), pad 2 = GND (pour) | — | added |
| 3 | removed component | **P103** mounting hole (`MountingHole:MountingHole_4.3mm_M4`, pcb (229.87, 135.89)), sch symbol + footprint | present | removed |
| 4 | moved footprint | **D9** | (156.21, 87.63) rot 180 | (154.94, 87.63) rot 180 (−1.27 mm x) |
| 5 | rotated footprint | **D12**, rotated +90° about its anode pad (153.67, 97.155) | (156.21, 97.155) rot 180 | (153.67, 94.615) rot −90 (= 270) |
| 6 | changed footprint | **C9** (sch Footprint field + pcb footprint) | `Capacitor_THT:C_Disc_D5.1mm_W3.2mm_P5.00mm` | `Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm` (same position/pads; new footprint UUID, same path) |
| 7 | rerouted tracks | net `Net-(D8-A)`, bottom_layer, 0.5 mm: straight segment `a783b4aa…` (153.67,77.47)→(149.225,77.47) removed; dogleg added: (153.67,77.47)→(151.765,75.565)→(149.86,75.565)→(149.225,77.47) (`57f5da80…`, `50b7e623…`, `50a6ff7d…`) | 1 segment | 3 segments |
| 7b | track follows move | net `Net-(D9-A)` segment `6b063485…` start | (153.67, 87.63) | (152.4, 87.63) |
| 8 | modified zone | GND pour on top_layer (`00000000-…-00005b22134f`): top-left chamfer enlarged, vertices (81.28, 41.91)→(88.9, 41.91) and (74.295, 48.895)→(74.295, 55.88); zones refilled | small chamfer | large chamfer |
| 9 | changed connection | **J1 pin 9**: sch no-connect flag at (43.18, 99.06) replaced by a GND power symbol; pcb pad 9 net | `unconnected-(J1-P9-Pad9)` | `GND` |
| 10 | changed silkscreen text | `gr_text` on F.Silkscreen at (143.0, 136.5), uuid `c6b4ad11-d2a8-4fd6-bd23-e8f850b42993` | `REV A` | `REV B` |
| 11 | fixed ERC issue | **U4 pin 4** (S/S): no-connect flag at (171.45, 143.51) restored | 1 ERC error `pin_not_connected` "Symbol U4 Pin 4 [S/S, Passive, Line]" | 0 ERC violations |

Netlist changes (pic_programmer): nets 111 → 110. `GND` + {C10.2, J1.9}; `VCC` + {C10.1};
net `unconnected-(J1-P9-Pad9)` removed. No other net membership changes.

BOM: C9 footprint changed; C10 added; R7 value changed. (P103 is `exclude_from_bom`, so it
does not appear in the BOM on either side.)
Position file: C10 added, P103 removed, C9 footprint, D9 moved, D12 moved+rotated, R7 value.

DRC (`--schematic-parity --severity-all`): base 0 violations / 0 unconnected / 0 parity;
head 0 / 0 / 0.

## complex_hierarchy (2 layers, THT, hierarchical: root + `ampli_ht.kicad_sch` used twice)

| # | Category | Item | base | head |
|---|---|---|---|---|
| 12 | added sheet | new sheet `status_led` → file `status_led.kicad_sch` (page 4), sheet UUID `18a26746-9a0d-50fc-86f6-8e7a51507824`, symbol at root (218.44, 111.76) size 38.1×20.32 | 3 sheets | 4 sheets |
| 13 | added components | **R401** 1K `Resistor_THT:R_Axial_DIN0204_L3.6mm_D1.6mm_P7.62mm_Horizontal` pcb (193.04, 58.42) rot −90; **D401** GREEN `LED_THT:LED_D3.0mm` pcb (193.04, 72.39) rot 90 | — | added |
| 14 | new net | `/status_led/LED_A` = {R401.2, D401.2} (sch label `LED_A`); also VCC + R401.1, GND + D401.1 | — | added |
| 15 | board outline change | Edge.Cuts right edge moved x 188.595 → 198.755 (+10.16 mm): lines `7cccdd15…` (top, end x), `d6ad3b43…` (right), `384fb7dd…` (bottom, start x). Board bbox right 188.6966 → 198.8566 | 100.9 × 80.2 mm | 111.1 × 80.2 mm |
| 16 | new tracks | VCC top_copper 0.6096 mm: (145.41,64.52)→(145.41,54.61)→(193.04,54.61)→(193.04,58.42); GND bottom_copper 0.6096: (193.04,72.39)→(193.04,78.74)→(190.5,81.28)→(181.2,81.3) (to P302 pad 2); LED_A bottom_copper **0.15 mm** (193.04,66.04)→(193.04,69.85) `3221cb6a-381f-4645-aae4-cf1c02ac5acb` | — | 7 segments |
| 17 | new DRC violation | exactly one: `track_width` "Track width (board setup constraints min width 0.2032 mm; actual 0.1500 mm)" on the LED_A track above | 0 violations | 1 violation |

The GND pour on top_copper is refilled around the new VCC track (its outline is unchanged).

Netlist changes (complex_hierarchy): nets 52 → 53 (new `/status_led/LED_A`; VCC + R401.1;
GND + D401.1).

ERC: 0 violations on both sides (4 sheets on head).
DRC (`--schematic-parity --severity-all`): base 0 violations / 0 unconnected / 68 parity; head
1 / 0 / 68. The 68 parity items come from the demo itself and are the same on both sides: its
footprints use the project library nickname `complex_hierarchy:` while the symbols name
`Capacitor_THT:` etc.

## Things that deliberately do NOT change

- pic_programmer: board outline (only complex_hierarchy changes its outline), everything in
  `pic_sockets.kicad_sch`, all other footprints and tracks.
- complex_hierarchy: `ampli_ht.kicad_sch` and all existing footprints and tracks.

## Known quirks in the fixtures

- pic_programmer has two project-local VRML-only 3D models (`libs/3d_shapes/adjustable_rx2v4.wrl`,
  `textool_40.wrl`). GLB includes them. STEP export skips them and `kicad-cli pcb export step`
  exits **rc=2** although the STEP file is written.
- complex_hierarchy's model paths were rewritten from `${KICAD6_3DMODEL_DIR}/…/*.wrl` to `.step`
  in the fixture's setup commit, because KiCad 10's stock 3D library ships STEP only
  (0 `.wrl` files). Without that rewrite the STEP/GLB exports contain no component bodies.
- pcbnew does not save complex_hierarchy byte-stably: every save reorders its tracks. The head
  edits were transplanted as text (see `scripts/`) so the git diff stays small. Diff tools must
  not rely on item order.
