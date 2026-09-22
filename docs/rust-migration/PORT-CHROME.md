# PORT-CHROME — chrome layer port (skin/, ui/fonts, chrome, segment, selector, debug)

Lane: chrome port. Python sources: `abyssal/skin/{surface,catalog,fascia,modules,hidpi}.py`,
`abyssal/ui/{fonts,chrome,segment,selector,debug}.py` (2,635 lines).

## Files ported (all gates green)

| Rust file | Python source | Notes |
|---|---|---|
| `rust/src/skin/hidpi.rs` | skin/hidpi.py | thread-local `_DS`, quarter-step quantisation, `surface()` at device res, `from_context()` |
| `rust/src/ui/fonts.rs` | ui/fonts.py | byte-compare instead of sha256 (same outcome), fc-cache refresh |
| `rust/src/skin/surface.rs` | skin/surface.py | sprite cache, interned dynamic names, single LRU for nine+scaled (64), stats, full 9-slice math (`_map_pad`, `_shrink`, `_render_nine`) |
| `rust/src/skin/catalog.rs` | skin/catalog.py | all NineSlice statics with identical insets/pads, lamp/mode/cycle/specimen_key name builders |
| `rust/src/skin/fascia.rs` | skin/fascia.py | Fascia + Placed with the three-band `_axis` maps, all measured bay tables (HEADER/FOOTER/MODULE/RACK/SELECTOR_BANK) |
| `rust/src/skin/modules.rs` | skin/modules.py | AxisMap, multi-band slicing `draw()` with seam overlap hair, six modules with exact bands/bays/screws, `_rack_bays`/`_selector_bays` |
| `rust/src/ui/segment.rs` | ui/segment.py | GLYPHS/NARROW tables, chamfered seg polys, ghost/bloom passes, narrow glyphs incl. degree ring, unit layer |
| `rust/src/ui/selector.rs` | ui/selector.py | BankGeometry, `from_module`, `layout`, `hit`, `key_state`, `plate`, `draw_keys`, SEAT_DROP |
| `rust/src/ui/chrome.rs` | ui/chrome.py | full pango plumbing (fd/attrs/cap/text_w/bg caches with Python cache keys and limits), `_fit` search, `_show` ellipsize guard, header/meta/readouts-row+column/mode-word |
| `rust/src/ui/debug.rs` | ui/debug.py | DebugInfo struct unchanged (pinned), cairo toy-text overlay |

## Gates

- `cargo check` clean for all lane files (whole crate compiles; all suites green).
- `cargo test --test parity_chrome` — **12/12 pass**, against golden values produced by the
  Python implementation on this checkout:
  - OBSERVATION_BEZEL.content(10,20,500,400) = (53.2, 58.25, 411.8, 319.9)
  - GRAPH_WELL.content(0,0,300,200) = (22.95, 13.5, 253.65, 166.7)
  - PLATE.content(5,5,100,26) = (16.859459…, 13.884, 77.075675…, 8.362666…)
  - selector layout(770x152,5): key_x 84.7, key_y 13.4764, key_w 105.7394, key_h 110.2829,
    pitch 123.7151, ledge 18.7030; hit() center round-trip + gap rejection
  - natural_aspect(5) = 5.312984615…
  - segment measure("12.4",20) = 51.8; sprite (1280,900) loads; hidpi round-trips
- Font-map (pango) tests were left out of the gate: fontconfig resolves headless, but
  `text_w`/`cap` parity is a *visual* question — it is exercised by the console lane and the
  screenshot comparison, not by unit numbers.

## Deliberate deviations (all behaviour-preserving)

1. `NineSlice.name` and `Fascia`/`Module` names are `&'static str` so the registries can be
   `static`s. Runtime-built sprite names (`lamp/…`, `specimen/key_NN_…`) go through
   `sprite_dyn`, which interns the string (alphanumeric `/` `_` only) — same cache as Python.
2. Asset root resolution: Python used `Path(__file__)`. Rust probes, in order:
   `CARGO_MANIFEST_DIR/../assets/sprites`, exe-relative (`../../../assets/sprites`),
   `./assets/sprites` — first candidate containing `frame/observation_bezel.png` wins.
   Same fallback chain for fonts.
3. `chrome.py` calls `ensure_user_fonts()` at import time, before the module-level Pango
   context is built. Rust has no import side effects: `chrome::init()` must be called once
   before the first font map; the console renderer's constructor should call it.
4. Pango font fallback families (`A,B,monospace`) are hardcoded consts with a `const`
   assertion that they match `theme::FONT_*`, instead of an unsafe compile-time splice.
5. Cache stats are a `SkinStats` struct, not a dict; caches are thread-local (the UI is
   single-threaded) rather than module globals.
6. `selector::layout(box_: Rect, n)` — `box` renamed (Rust keyword). `_show`'s `align`
   parameter is a `char` ('l'/'r'/'c'), not a str.
7. `modules::Placed` is `Clone` but not `Copy` (its `AxisMap` owns Vecs); `fascia::Placed`
   is `Copy`.
8. `fd.set_absolute_size((size * SCALE) as i32 as f64)` reproduces Python's
   `int(size * pango.SCALE)` truncation through the Rust API's f64 parameter.
9. cairo-rs needs the `png` feature for `create_from_png` — added to `rust/Cargo.toml`.
10. `_rack_bays`/`_selector_bays` build their tables lazily into a `OnceLock` (keys interned);
    Python built dicts at import. Content is byte-identical to the Python tables.

## API handed to the console lane

Everything console.py imports is public: `chrome::{show, text_w, cap, fit, W_NORMAL,
W_MEDIUM, FAMILY_HERO, FAMILY_LABEL, draw_background, hairline, LABELS, WARNS}`,
`segment::{CYAN, PALE, AMBER, RED, measure, measure_unit, draw, draw_right, draw_unit,
fit_height, SegmentStyle}`, `selector::{BankGeometry, layout, from_module, hit, draw_keys,
key_state, plate, sprite_name}`, `skin::surface::{draw_nine, draw_sprite, draw_sprite_fit,
draw_sprite_rot90, nine_surface, NineSlice, sprite, sprite_size, skin_stats}`,
`skin::hidpi::{set_scale, scale}`, `skin::{catalog, fascia, modules}`.

## Open risks

- Text-heavy chrome parity is untested beyond geometry: letter-spacing conversion
  (px → Pango units ×1024) and the ellipsize guard are transcribed 1:1 but the proof is the
  side-by-side screenshot (host integration phase).
- The console lane must remember `hidpi::scale()` is folded into every cache key and draw
  text at device scale exactly as Python does, or type will land off the bevels.
- `chrome::init()` is not yet called anywhere (console renderer not ported yet).
