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
