# Six-module hardware — asset inventory

The machine is composed from exactly six generated structural modules
(`references/Abbysal_Module_Map.png`). Generation history, settings and the
references attached to every generation: [`GENERATIONS.md`](GENERATIONS.md).
Runtime registry (bays, stretch bands, fasteners): `abyssal/skin/modules.py`.
Build step: `python3 tools/build_modules.py`.

Every module is **cached**: it is drawn once per window size (and device scale)
into the static under/over layers, which the live widget hands to GSK as a
texture. Nothing here is repainted per frame.

| # | module | runtime file | px | alpha | apertures (runtime px) | runtime role | scaling policy | seating @ 1448×1086 |
|---|---|---|---|---|---|---|---|---|
| 01 | shell | `sprites/module/shell.png` | 1600×1172 | clear outside rounded corners; body 98.6% opaque | none — bottom layer, nothing passes through | master chassis; the recessed bed every child module sits in | fixed parts at 0.17 × chassis scale (thin reference perimeter); face + edges stretch in bands that skip the side lugs | 40,30 1368×1028 |
| 02 | header | `sprites/module/header.png` | 1800×185 | clear outside corners; 97.5% opaque | none (bays are blank recesses) | system fascia: title, epithet, LIVE lamp socket + window, clock, 3 status bays | uniform k; x-bands only where BOTH rows are recess floor, so no rib, screw or socket stretches | 55,55 1340×145 |
| 03 | observation | `sprites/module/observation.png` | 1300×948 | aperture alpha 0; 39.9% opaque | glass 128,128 1044×697 (rounded) | scope bezel; the live field renders beneath it | uniform k; bands clear of the two lip notches | 52,222 771×573 |
| 04 | rack | `sprites/module/rack.png` | 900×1213 | 99.1% opaque | none (4 × {plate, title, 4 lamp sockets, graph, numeric, meter, state}) | the four-row telemetry rack, one body | height-scaled at manufactured proportions; x-bands inside graph/numeric wells, y-bands inside each row's well block | 839,222 533×729 (target 838,222 534×730; right margin 35.9 / 36) |
| 05 | selector | `sprites/module/selector.png` | 1400×264 | wells + control openings alpha 0 | 5 wells 133×138 on pitch 200.66 (trued), rocker 89×33, mode 68×106 | dark key trough receiving the APPROVED keys, rocker and mode key | never stretched vertically; height from column width; x-bands only in the plain trough at each end | 52,805 771×145 |
| 06 | footer | `sprites/module/footer.png` | 1800×93 | clear outside corners; 93.8% opaque | none (7 bays + terminal) | bottom status rail; the cast badge is part of the metal | uniform k; x-bands inside each bay floor | 48,960 1350×78 |

## Approved runtime parts reused (not regenerated)

`sprites/specimen/key_0{1..5}_{active,inactive}.png` (the creature keys, 256×267),
`sprites/cycle/*.png` (rocker), `sprites/mode/*.png` (mode key),
`sprites/lamp/*.png` (annunciators, drawn into the rack's and header's sockets).

## Verified by `qa/production_gates.py`

P1 existence · P2 alpha · P3 apertures · P4 wells accept the real keys ·
P5 no active-key matte · P6 no baked runtime values (31 data bays, max
luminance ≤ 25.5 of a 60 limit) · P7 no duplicate outer fasteners · P8 rack
seating · P9 deterministic cache.
