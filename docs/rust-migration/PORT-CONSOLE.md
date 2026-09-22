# PORT-CONSOLE — ui/console.py → rust/src/ui/console.rs

Lane: the console machine (last port piece). Owner file: `rust/src/ui/console.rs`
(~2,150 lines), gate: `rust/tests/parity_console.rs` (10 tests).

## Ported items — everything console.py does

**Pure geometry (shared by draw, hit test, QA — one source of truth each):**
`control_geometry`, `cycle_rect`, `stage_content` (glass = OBSERVATION module's
`aperture` bay), `hit_controls` (key / mode / cycle classification via
`selector::hit`), `header_panel` (header + status rail as one fascia),
`field_area`, `Scope` + `scope_of` (specimen-registered graticule frame),
`six_module`, `shell_k`.

**Cached text:** `show()` — the glyph-run cache (`_TXT`, LRU 700) keyed on the
complete input set (text, size, weight, tracking, align, max_w, colour, alpha,
device scale, family); rendering via `chrome::show`, blit rounded to whole
pixels. Plus `elide` (measured binary search + `…`), `fit_first`, `bay_line`
(shrink-then-elide, cap-height centring), `bay_pair`, `bay_stack`, `bay_inner`.

**Drawing primitives:** `bargraph` (segmented meter), `rings` (cached dashed
circles, LRU 8), `polar_grid` (specimen-centred graticule: rings, crosshair,
ticks, cardinal crosses, ruler-gutter-aware x_min), `cardinals`, `rail`
(procedural machined recess), `divider`, `hairline`, `corner_brackets`,
`radius_ruler` (R (mm) axis graduated off the scope; returns its gutter).

**Six-module machine:**
- MODULE 01 shell (`draw_chassis`, `shell_k` scaling)
- header fascia: title/epithet bays (Astro hero stack, epithet rule marks),
  LIVE lamp boss + light, LIVE window, segment-display clock + cyan glow,
  status rail (three bays, whole-pairs packing rule) — plus the COMPACT
  arrangement `draw_header_compact` (static/live pass split with the
  measuring-only null context, live clock via `segment::draw_right`)
- MODULE 03 observation bezel (`draw_stage`) + glass spill
- observation field static furniture (`draw_field_static`: dark chamber
  gradient, brackets, radius ruler, graticule, cardinals)
- MODULE 04 rack: `draw_rack_static` (plate engravings, title bays + short
  subs, graph grids/scales/captions, 60 s divisions, 60 FPS reference line)
  and `draw_rack_live` (four lamp states + warning-only light, cached traces,
  numeric + unit segment displays, meter troughs, state words with short
  forms) — the four `_Channel`s and `_channel_values` verbatim
- condensed strip (`draw_condensed`) for module-less layouts
- MODULE 05 selector: plate + F1 service ledge labels (static), key plates via
  `selector::draw_keys`, rocker + mode sprites, active-key lamp bloom (live)
- MODULE 06 footer: archive rail (one rail-wide type size, full-form shrink
  loop, terminal bay SYSTEM BUS/ONLINE + whole-or-nothing motto) and the
  plain narrow rail

**Live machinery:** `layer_under` / `layer_over` / `regions` with the exact
Python cache keys and LRU limits (under/over 2 each, regions 8, traces 16,
rings 8, glyph runs 700); region rects snapped to whole device pixels (+6 px
pad); each region composites under+over, renders its live content, then
paints its own light clipped to the region. Cadences identical: clock 1 s,
rack on telemetry sample / FPS change / ring versions, keys on input only.

**Lighting:** identical architecture to Python — constant emitters (glass
spill, LIVE lamp) are painted into `layer_over`; per-region emitters are
painted per region, clipped. **The host needs no new call**: `layer_over` +
`regions` are self-contained. (Python's `app.light` is vestigial in exactly
the same way as the Rust host's.)

## Deviations (all deliberate, none behavioural)

1. **`Layer::id` replaces `id(surface)`.** Monotonic counter: same id while
   the key matches, fresh id + fresh surface on re-render. Stronger than
   Python (id reuse after free is impossible).
2. **Cycle hit direction is 0 (back) / 1 (forward)** — `CtlKind` carries
   `usize`; the host maps 0→−1, 1→+1 (documented on `hit_controls`).
3. **`Region` exposes `{layer, rect}`** — Python's `name`/`key` fields were
   internal bookkeeping; the name lives inside the region key.
4. **Cache keys** hash quantised float bit-patterns (same rounding grid as
   Python's `round(...)` in tuples); `-0.0` normalised.
5. **Glyph-run cache surfaces** use `ceil` sizing (hidpi helper) where Python
   truncated — differs only inside the cache padding margin.
6. **Clock** via `glib::DateTime::now_local()`, same `%H:%M:%S` format.
7. `_Channel.axis/lamps/span` and `_PLAQUE_MIN_H` were declared-but-unused in
   Python and are dropped; `History` access goes through the existing
   `telemetry::history::{Channel, Ring}` API. `window(&mut)` mutates only the
   ring's index cache, exactly like Python.
8. **Latent-bug class fixed at port time:** every Python
   `max(MIN_TEXT, min(x, lim))` is ported as `.min(lim).max(MIN_TEXT)`
   (NOT Rust `clamp`, which panics when `lim < 7px` — small bays hit this;
   the gate suite covers both layout families).

## Host integration notes

- No pinned signature changed; `app.rs` needed zero edits.
- `Renderer::new()` calls `chrome::init()` (font install) before any Pango
  use; idempotent alongside the host's own `fonts::ensure_user_fonts()`.
- `clear_static_cache()` is now a `Renderer` method (also clears the
  thread-local glyph/ring/trace caches) for the memory-optimisation phase.
- Device scale: the host sets `hidpi::set_scale()` per frame as before;
  every cache key carries it.

## Test summary (rust/tests/parity_console.rs, headless)

- `stage_content_matches_python_at_golden_sizes` — 8 golden sizes, values
  dumped from `abyssal.ui.console` (≤1e-2 px).
- `stage_content_invariants` — glass inside stage, >25% of stage area at 6
  sizes across all three states.
- `hit_controls_matches_python_geometry` — key centres, mode key rect
  (Python-exact to 1e-6), cycle halves, gap rejection, outside rejection.
- `hit_controls_compact_is_inert` — COMPACT rejects everywhere.
- `layers_render_cache_and_invalidate` — dims = logical × ds, warm-hit id
  stability, specimen switch re-renders both layers, mem-scale changes over.
- `regions_cadence_and_identity` — 3 regions at INSTRUMENT; stable on warm
  pass; new sample re-renders rack; mode press re-renders keys; history push
  re-renders traces.
- `compact_has_no_keys_region`, `region_without_layers_recomputes_them`,
  `condensed_and_compact_paths_render`, `layout_state_naming_matches_python`.

Full crate: `cargo check` clean (0 warnings), `cargo test` 35/35.

## Open risks for the visual comparison pass

- Text metrics come from the same Pango/fontconfig stack; expect sub-pixel
  shaping drift only. Colours, bevels, elision and layout are transcription-
  faithful but not yet pixel-diffed against Python (G-R41 headless screenshot
  gate is the next milestone).
- The compact header / condensed strip paths are exercised structurally
  (they render, cache, and invalidate) but not pixel-compared.
- Trace-cache keys use the ring's address; replacing the whole `History`
  (never happens in the app) would leave ≤16 stale entries until LRU eviction.
