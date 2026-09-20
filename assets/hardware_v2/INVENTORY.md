# Abyssal Organism Monitor — Hardware Asset Library v2

Bespoke UI hardware components generated from `monitor_ref.png`, which was attached as an
actual image reference to **every** generation call.

- **Model:** `gpt_image_2_5` (GPT Image 2 family, OpenAI), variant `flare`
- **Resolution / quality:** 2K, `quality: high`
- **Background:** `background: transparent` — true alpha, verified per file
- **Reference media id:** `cc4e864d-41ca-444c-b2dd-872d8625121b`
- **Camera:** straight-on front elevation, orthographic, centred, no cast shadow

All assets are hardware only. No text, numerals, telemetry, graphs, organism or logos are
baked in — every display cavity, label plate and information bay is blank and ready to be
painted programmatically.

## Approved assets

| # | Asset | File | Size |
|---|-------|------|------|
| 01 | Outer chassis (master) | `chassis/chassis_master_2k.png` | 2688×1520 |
| 02 | Main observation bezel | `bezels/observation_bezel_2k.png` | 2336×1744 |
| 03 | Right telemetry rack | `racks/telemetry_rack_2k.png` | 1792×2240 |
| 04 | Telemetry module shell | `modules/telemetry_module_shell_2k.png` | 2688×1152 |
| 05 | Top header / command fascia | `bezels/header_fascia_2k.png` | 2688×1152 |
| 06 | Bottom archive / status rail | `bezels/footer_archive_rail_2k.png` | 2688×1152 |
| 07a | Segment display housing (standard) | `displays/segment_display_housing_2k.png` | 2688×1520 |
| 07b | Segment display housing (wide) | `displays/segment_display_housing_wide_2k.png` | 2688×1152 |
| 08 | Oscilloscope / sparkline well | `displays/graph_well_2k.png` | 2688×1520 |
| 09 | Bargraph / level-meter trough | `meters/bargraph_trough_2k.png` | 2688×1152 |
| 10 | Auxiliary specimen view frame | `bezels/aux_view_frame_2k.png` | 2048×2048 |
| 11 | Annunciator / LED kit (3 housings × 4 states) | `indicators/annunciator_led_kit_2k.png` | 2336×1744 |
| 12 | Fastener / hardware kit (6 parts) | `fasteners/fastener_kit_2k.png` | 2336×1744 |
| 13 | Control kit (6 controls) | `controls/control_kit_2k.png` | 2336×1744 |
| 14 | Blank plaque kit (2 plates) | `plaques/blank_plaque_kit_2k.png` | 2336×1744 |

## Rejected

| File | Reason |
|------|--------|
| `candidates/REJECTED_a02_bezel_whole-console.png` | Model rebuilt the entire console instead of isolating the bezel |
| `candidates/REJECTED_a11_ledkit_v1_oversaturated.png` | Lamp glows too bright/saturated vs reference; row-1 diameters inconsistent |

Remaining files in `candidates/` are the accepted originals, kept pre-alpha-normalisation.

## Sheet layouts (for slicing in code)

**11 — LED kit**, 3 rows × 4 columns.
Rows top→bottom: small round, larger round, rectangular annunciator.
Columns left→right: **off**, **nominal green**, **amber warning**, **dim red critical**.

**12 — Fastener kit**, 2 rows × 3.
Row 1: large slotted screw in counterbore · smaller slotted screw · domed rivet.
Row 2: retaining tab · L mounting bracket · panel handle bar.

**13 — Control kit**, 2 rows × 3.
Row 1: knurled rotary knob · toggle switch · guarded toggle.
Row 2: rotary selector · rocker switch · recessed push button.

**14 — Plaque kit**, 2 rows × 1.
Row 1: wide equipment tag with end screws. Row 2: small plate with recessed field.

## Alpha

Every asset carries genuine per-pixel alpha. The generator emitted a bimodal but dithered
alpha channel (background ≈0–1, body ≈253–254); a contrast stretch (≤8→0, ≥248→255) was
applied so backgrounds are fully clear and part bodies fully opaque, while real antialiased
edges were preserved as a soft ramp.

## GTK4 / Cairo implementation notes

- Load once into `GdkTexture` / `cairo_surface_t`; these are large. Downsample to the target
  layout size at load and cache — do not rescale per frame.
- Composite with `CAIRO_OPERATOR_OVER` onto the dark ground. Assets are **not** premultiplied
  by the generator; `gdk_texture_new_from_file()` handles conversion, but if you decode
  manually into a Cairo `ARGB32` surface you must premultiply RGB by alpha yourself or edges
  will show light fringing.
- Assets 01, 02 and 10 have genuinely transparent apertures — draw live Cairo content
  *underneath* the asset and let the bezel overlay it. That gives correct bevel occlusion for
  free.
- Asset 04 is designed to be blitted four times down asset 03's rack. Derive the bay pitch
  from the rack image rather than hardcoding, so the two stay aligned if either is regenerated.
- The LED/fastener/control/plaque sheets are even grids — slice with integer arithmetic from
  the image dimensions rather than hand-measured pixel offsets.
- Scale with `CAIRO_FILTER_GOOD` or better; `FILTER_FAST` visibly destroys the fine bevel
  highlights that carry the material read.
- Text, numerals, graphs, reticles and state colour remain entirely programmatic, as intended.

## Consistency limitations

- The generated metal reads slightly **lighter and cooler** than the reference's darker
  gunmetal, and the surface weathering is somewhat less pronounced. It is consistent *across
  the library*, so a single global colour-grade (slight darken + warm-green shift) applied at
  load time will seat the whole set against the reference.
- Small-part sheets (12, 13) render in a lighter, more silver alloy than the large panels.
  Consistent with each other, marginally brighter than the panels.
- LED kit row 1 still shows minor diameter variation between the four states; rows 2 and 3
  are uniform and are the better choice where exact consistency matters.
- Asset 12's L-bracket is drawn in slight 3/4 view rather than true front elevation.
- Assets 05 and 06 are proportionally shallower than the reference's header/footer; scale
  them to your layout rather than assuming the reference's exact aspect.

---

# Closure pass (session 2)

Five further assets, same model and settings, each generated with
`monitor_ref.png` attached plus the closest accepted asset as a secondary
consistency reference.

| # | Asset | File | Notes |
|---|-------|------|-------|
| F1 | Annunciator family | `indicators/annunciator_family_2k.png` | 2 sizes x 6 states: off, standby, active, nominal, warning, critical |
| F2a | Selector bank frame | `selector/selector_bank_frame_2k.png` | empty 5-position fascia |
| F2b | Selector bank, seated states | `selector/selector_bank_states_2k.png` | **canonical**: keys drawn already seated, 5 states |
| F2c | Cycle rocker | `selector/cycle_rocker_states_2k.png` | neutral / next / prev |
| F3 | Mode key | `controls/mode_key_states_2k.png` | inactive / armed / active / error |

`indicators/annunciator_led_kit_2k.png` from the first pass is superseded by F1
and moved to `candidates/`.

---

# Specimen key sheet (visual-integration pass)

| # | Asset | File | Size |
|---|-------|------|------|
| S1 | Specimen selector keys | `selector/creature_keys_2k.png` | 2688×1152 |

**Canonical.** A 5×2 grid of bespoke console keys, one per mathematical
organism: top row raised and unlit, bottom row seated and illuminated. The
engraved creature on each key IS that specimen's identity, so the artwork is
never tinted, recoloured or reinterpreted at runtime — selecting a specimen
swaps to that key's own authored active plate.

It supersedes F2a/F2b as the specimen control. Those remain built (and
`selector/bank_empty` is measured in `skin/fascia.py`) but are no longer the
selector.

Cell bounds, read from the alpha channel, not assumed:

- columns `(25,513) (557,1052) (1090,1590) (1628,2123) (2158,2655)`
- rows `(38,558) (596,1112)`

The ten keys were authored between 488 and 500 px wide. `_uniform_cells`
crops each to its own ink, scales all by ONE factor and centres them on a
shared 256×267 canvas, so a state swap is a pure pixel substitution at a
fixed rect — which is what lets drawing, hit testing and QA share one
geometry. `qa/console_gates.py` GATE 6 asserts the identical footprint and
that the drawn aspect matches the authored one.

Alpha, verified pixel-wise before and after the build: zero outside every
key, a soft ramp on the rounded edges, no matte, no fringe. The source's
dithered 0–2 / 250–254 channel was contrast-stretched exactly as the rest of
the library was.

## Why F2b is the canonical selector source

Asked for loose key caps, the model returned the keys already seated in the
bank. That is better: the fit is guaranteed by construction, and its five wells
are pitched to +/-2px against the empty frame's +/-10px. `build_sprites.py`
therefore composites each state's cap onto ONE canonical interior cell, so all
five cells differ only by cap state and any state can occupy any slot without
disturbing the surrounding metal or the divider ribs.

## Runtime sprites

`tools/build_sprites.py` turns the masters into 41 sprites (12MB) under
`assets/sprites/`. That directory is committed; the 2K masters and the rejected
candidates are not (see `.gitignore`), but they remain on disk and are required
only to re-run the build.

---

# Asset-usage audit (visual-integration pass)

Every runtime sprite, and what it actually does. `51 / 65` sprites are
referenced by name (or by the family helper that builds the name) in
`abyssal/`; the remainder are accounted for below.

## Actively and appropriately used

| Asset | Where | How |
|---|---|---|
| `bezels/header_fascia` | `ui.console._draw_header` | **Structural.** Its four information bays and three-bay status rail are measured in `skin/fascia.py` and every line of header type is written INSIDE one of them. The lamp boss and window in bay 3 carry the LIVE annunciator. |
| `bezels/footer_archive_rail` | `ui.console._draw_footer` | **Structural.** Badge boss (reticle cast into the metal, no longer redrawn in code), eight key/value bays, three-line motto block. |
| `modules/telemetry_module_shell` | `ui.console._draw_module` | **Structural.** Identity plaque, title bay, four annunciator wells and three display recesses proportioned GRAPH \| NUMERIC \| STATUS. This asset is the whole of the telemetry-module layout. |
| `bezels/observation_bezel` | `ui.console._draw_stage` | 9-sliced over the specimen field; its transparent aperture gives correct bevel occlusion for free. |
| `meters/bargraph_trough` | numeric bays, condensed rail | Drawn at ~7.9:1 against an authored 7.7:1 — effectively its native aspect. |
| `displays/graph_well`, `displays/segment_display_housing` | `_draw_module_plain` | The discrete housings for layouts too small for a module shell. |
| `selector/creature_keys` → `specimen/key_0N_{inactive,active}` | `ui.selector` | **The hero control.** Five bespoke engraved specimen keys, two authored states each. Never tinted at runtime. |
| `selector/cycle_rocker`, `controls/mode_key` | selector trough ends | Auxiliary controls, seated in the metal left over at each end. |
| `indicators/annunciator_family` | module wells, LIVE bay | Lit by live state. |
| `fasteners/*` → `part/screw_large`, `part/screw_small`, `part/handle` | chassis, trough, module rails | Assembled, not stretched. |
| `plaques/blank_plaque_kit` → `frame/plate_recessed` | chassis body, module plaques | The generic console metal everything else is bolted to. |

## Generated, deliberately not drawn

| Asset | Why |
|---|---|
| `racks/telemetry_rack` | Measured and kept as `skin.fascia.RACK`, but not drawn. It duplicates the module shell's own rail-and-screw mounting, and nesting the two costs ~20% of the graph width for a second frame the approved reference does not have — the reference's modules sit unracked on the chassis. Using it would have made CRITICAL PROBLEM 04 worse. |
| `chassis/chassis_master` | A fixed composition with a viewport hole in a fixed place. It cannot follow an arbitrary window aspect, and stretching it ovalises its fasteners. The chassis is therefore assembled from `plate_recessed` + real screws + handle rails, which is what lets it be correct at every size. |
| `bezels/aux_view_frame` | No second viewport in this layout. |
| `frame/plate_wide` | Superseded by `plate_recessed`, whose smaller bevel survives the short rails this console actually asks for. |

## Contact sheets (sources, not sprites)

`kit/fasteners`, `kit/controls` and `frame/plaque_kit` are the whole sheets.
They are kept because `tools/build_sprites.py` slices them; the individual
parts are what the app loads.

## Unused parts

`part/button`, `part/knob`, `part/toggle`, `part/guarded`, `part/rivet`,
`part/bracket`, `part/tab`, `part/rocker`, `part/selector_rotary` — controls
this console has no function for. Built because the kit was generated as a
sheet; adding a switch with nothing behind it would be decoration.

## Alpha verification

Every sprite was re-checked pixel-wise, not assumed:

- All 65 have `alpha.min() == 0` and a genuine antialiased edge ramp
  (2–8% of pixels partially transparent).
- The ten specimen keys carry corner alpha 0, ~3% soft edge, no matte, and
  an identical 256×267 footprint (asserted by `qa/console_gates.py` GATE 6).
- `selector/cell_*` measure `alpha.min() == 58` — they are opaque metal cut
  from the seated-bank master by design, not a matte. They are no longer the
  specimen control regardless.
- The dark field that used to appear behind the selector was **not** a sprite
  matte: it was a procedural `_rail()` recess drawn under the cluster. It has
  been replaced by a gunmetal-floored mounting trough.
