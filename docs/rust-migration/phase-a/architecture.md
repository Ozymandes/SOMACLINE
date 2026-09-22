# Phase A — Architecture Audit (Python → Rust migration map)

**Project:** Abyssal Organism Monitor, `/home/seeno/abyssal-organism-monitor`
**Audit role:** architecture-audit (Phase A). **Date of audit:** this session.
**Method:** full-source reading of `abyssal/` (all 21 modules) + `docs/ARCHITECTURE.md`, `docs/VALIDATION.md`, and the QA harness entry points (`qa/gates.py`, `qa/perf_live.py`, `qa/shots.py`, `qa/torture.py`, `qa/offscreen.py`).

**Evidence conventions used throughout this report**

- **FACT** — read directly from source, cited `file:line`. Line numbers were pinned by targeted reads during this audit.
- **MEASUREMENT** — a number reported by the project's own docs/QA harnesses (`docs/VALIDATION.md`, `docs/ARCHITECTURE.md`), with its method noted. **No command was executed during this audit** — this environment has no shell tool, so every runtime number below is attributed to its documented source, not to a run performed here. The project machine for those numbers: AMD Ryzen 7 5800H, Hyprland 0.56/Wayland, 2560×1600@120 Hz panel at **1.6× scale**.
- **INFERENCE** — the auditor's judgment, labeled explicitly.

---

## 1. Module inventory, responsibilities, dependency directions

### 1.1 Data flow (the one rule: the simulation never sees a pixel)

```
telemetry/source.py::TelemetrySource.sample()      (5 Hz, /proc + /sys only)
      │ produces core/signals.py::Telemetry        (frozen dataclass, signals.py:14-40)
      ▼
core/physiology.py::PhysiologyModel.update(dt,tel) (per tick, 60/30 Hz)
      │ produces core/signals.py::Physiology       (signals.py:43-77)
      ▼
organism/mathforms.py::SourceBody.update(dt,phys)  (world units, allocation-free)
      │   └─ organism/sources.py::_c01.._c05       (the verbatim source equations)
      │ produces (x, y, weight) + (excite, hue) point arrays in WORLD units
      ▼
organism/render.py::draw_organism(cr, vp, org)     (THE world→pixel boundary)
      │   └─ organism/pointfield.py                (bincount accumulate + color map)
      ▼
ui/console.py::layer_under / regions / layer_over  (static chrome + live regions)
      ▼
app.py::Monitor._on_snapshot → GtkSnapshot         (textures + one cairo node)
```

Side flows:

- `telemetry/history.py::History` — fed by the host at 5 Hz (`app.py:285-288`), read only by the console's graph wells (`console._rack_trace`). Nothing in the draw path writes it (FACT: `ConsoleModel.history` docstring, `console.py:155-157`).
- Organism readouts → UI: `app.Monitor._refresh_field(vp)` (`app.py:441-461`) reads `org.time` and `phys_model.current` and writes scalar presentation fields into `ConsoleModel` (phase, rotation, behavior, flux, surge, magnification, field_mm). The console never touches the organism object itself.
- Species metadata: `organism/species.py::CATALOGUE` (5 `Species` records) is consumed by the console for labels and by the selector; `Species.build()` (`species.py:52-54`) constructs the `SourceBody`.

### 1.2 Inventory and dependency direction (all FACT, from imports read at the cited files)

| Module | Responsibility | Depends on (project) | Depends on (external) |
|---|---|---|---|
| `core/world.py` | World constants: `WORLD_SIZE=1000`, `WORLD_RADIUS=460`; binding contract "no pixels in organism/ or telemetry/" (world.py:1-17) | — | — |
| `core/signals.py` | Frozen value types `Telemetry`, `Physiology` crossing every seam | — | stdlib dataclasses |
| `core/viewport.py` | **The only** world→pixel mapping; `Viewport.for_stage` letterboxes the world square, uniform `scale` float (viewport.py:35-66) | `core.world` | stdlib |
| `core/layout.py` | Pure `resolve(w,h)` → immutable `Layout` (3 states COMPACT/INSTRUMENT/ARCHIVE, breakpoints 700/1100, aspect gate 0.72–2.2); module rects laid out from `REF_*` constants measured off the reference image (layout.py:135-160) | — | stdlib |
| `core/theme.py` | Palette (4 ink ranks, CYAN/AMBER/…), font family names | — | — |
| `core/lighting.py` | `LightField` emitter collector; additive A8-mask stamping, caps `MAX_STRENGTH/MAX_TOTAL/MAX_RADIUS` (lighting.py:14-17) | — | cairo |
| `core/physiology.py` | `PhysiologyModel`: EMA smoothers, asymmetric surge envelope, hysteresis + rate-limited `activity` (physiology.py:128-160); `condition()` pure normalizer (physiology.py:94-105) | `core.signals` | stdlib math |
| `telemetry/source.py` | Stdlib-only `/proc/stat`, `/proc/meminfo`, hwmon discovery w/ chip-label priority (source.py:41-47), `/proc/diskstats` + `/proc/net/dev` rates; never raises (source.py:289-330) | `core.signals` | stdlib |
| `telemetry/history.py` | 4 preallocated float32 rings, 300 samples (60 s @5 Hz); `window(cols)` column-mean reduction w/ cached edge table + `version` counter (history.py:21-121) | — | numpy |
| `organism/sources.py` | The five published p5.js equations ported verbatim (`_c01`–`_c05`, sources.py:158-247); `Source` record w/ clock `dt`, ink alpha, persist, measured frame box (`frame_cx/cy/half`, sources.py:100-104) | — | numpy |
| `organism/mathforms.py` | `SourceBody` base + 5 species subclasses: clock, anatomy (`_u/_lat/_grp`), propagating pulse (`_pulse`, mathforms.py:349-390), per-species perturbation; world seating + safety radius (mathforms.py:240-310) | `core.signals`, `core.world`, `organism.sources` | numpy |
| `organism/pointfield.py` | `np.bincount` accumulation into 3 buffers (density, excitation, hue), lit-pixel-only color map, blit via `ImageSurface.create_for_data`; **output image allocated fresh per frame** (pointfield.py:36-52, 129-170, 183-262) | — | numpy, cairo |
| `organism/render.py` | `draw_organism` (wash disc cache, field sizing `MAX_FIELD_PX=760`, ink/exposure math, render.py:133-193), `draw_calibration` (F2 overlay); builds `PULSE_LUT` with `np.interp` (render.py:60-77) | `core.theme`, `core.viewport`, `core.world`, `pointfield`, `mathforms` | numpy, cairo |
| `organism/species.py` | `Species` metadata records (names, archive codes, response summaries) + `CATALOGUE`; `build()` → `mathforms.build` | `mathforms` | — |
| `skin/hidpi.py` | **Module-global** device scale, quantized to ¼ steps, clamp 1..4; `surface()` allocator at device resolution (hidpi.py:24-38) | — | cairo |
| `skin/surface.py` | Sprite PNG load-once; 9-slice with border-shrink (`MIN_BORDER_SCALE=0.45`, surface.py:44-51); `_scaled` LRU-64 keyed by exact integer size + hidpi scale (surface.py:243-262) | `skin.hidpi` | cairo |
| `skin/catalog.py` | Registry of `NineSlice` frames (bezel, housings, wells, plate) w/ source-space content pads | `skin.surface` | — |
| `skin/modules.py` | The six structural modules; multi-band `AxisMap` piecewise scaling; `Placed.bay(name)` maps measured recesses through the same map as pixels (modules.py:34-90) | `core.layout` (Rect), `skin.surface` | cairo |
| `skin/fascia.py` | Header fascia sprite + measured recess rectangles, three-band slicing | `skin.surface` | cairo |
| `ui/fonts.py` | Installs `assets/fonts/{astro,microgrammanormal}.ttf` into `~/.local/share/fonts/abyssal/` + `fc-cache`, once, before first Pango font map (fonts.py:41-64) | — | stdlib |
| `ui/chrome.py` | Pango plumbing: cached font descriptions/attr lists/cap heights; memoized `_text_w` (chrome.py:151-175); `draw_background`; raw `_show` glyph renderer | `core.{layout,signals,theme}`, `ui.fonts` | cairo, Pango, PangoCairo |
| `ui/console.py` (1932 ln) | The whole machine: pure geometry (`control_geometry`, `stage_content`, `hit_controls`, console.py:186-232), drawing primitives, six-module draw passes, **static layer + region + trace caches**, live regions | `core.*`, `organism.species`, `skin.*`, `telemetry.history`, `ui.{segment,selector,chrome}` | cairo |
| `ui/segment.py` | Procedural seven-segment digits (paths, not a font), unit glyphs, ghost segments | — | — |
| `ui/selector.py` | `layout()` pure rect→key-rectangles (draw + hit + QA from one answer, selector.py:34-44); key plate drawing w/ authored aspect | `core.layout`, `skin.{catalog,surface}` | — |
| `ui/debug.py` | F1 diagnostics overlay (fed `_debug_info` dict from app.py:463-488) | core, chrome | cairo |
| `app.py` (581 ln) | GTK host: `MonitorView` (the one widget, `do_snapshot` delegates, app.py:80-91), `Monitor` window, tick/telemetry/probe timers, input controllers, texture bridge, CLI | everything above | PyGObject GTK4/GDK/GLib/Graphene |

**Enforced layering (FACT, contracts in source):**

- Nothing in `organism/` or `telemetry/` imports pixels, cairo, GTK, or widget sizes (`core/world.py:12-14`, `mathforms.py` module docstring "CONTRACT").
- `skin/`, `ui/console.py`, `ui/segment.py` "can reach the simulation" only through `ConsoleModel` scalars set by the host (docs/ARCHITECTURE.md "The hardware skin").
- One organism instance per specimen, built lazily, kept for process life; switching selects, never rebuilds (app.py:112-118, 186-196) — switching allocates nothing on the hot path and never rewinds the clock (GATE 7).

---

## 2. The frame loop

### 2.1 The two clocks (FACT)

1. **Telemetry timeout** — `GLib.timeout_add(TELEMETRY_INTERVAL_MS, self._on_telemetry)` (app.py:160), `TELEMETRY_HZ = 5.0` → **200 ms** (app.py:57-58). `_on_telemetry` (app.py:274-291) samples `/proc`+`/sys`, pushes 4 channels into `History` (`cpu_pct`, `temp_c`, `mem_used_gb`, `_frame_ms`), records `_tel_ms`. `/proc` is read **here and only here** (app.py:53-56 comment). This timeout keeps firing even while the window is suspended — graphs stay current; documented hidden-state CPU of 0.1–0.2 % (MEASUREMENT, VALIDATION.md perf tables) confirms it does essentially no work.
2. **Frame clock tick** — `self.area.add_tick_callback(self._on_tick)` (app.py:159). `_on_tick` (app.py:315-350):
   - `cadence()` (app.py:301-306): `SUSPENDED` toplevel state → **0.0** (return immediately: no simulation, no draw); `--fps-cap` override if > 0; else **60 FPS focused / 30 unfocused** (`FPS_FOCUSED/FPS_UNFOCUSED`, app.py:67-68).
   - **Phase-accumulator pacing** (app.py:320-330): fixed schedule `now_us >= _next_us − tol` with `tol = min(interval/2, 4000 µs)`; the comment records that "time since last frame" pacing aliased 60 FPS to 40 on a 120 Hz panel.
   - `dt` from frame time, **clamped to [0, 0.1] s** so a suspended window cannot teleport the organism on resume; first tick uses `dt = 1/60` (app.py:331-338).
   - Sim step: `phys = phys_model.update(dt, telemetry)` then `org.update(dt, phys)` — measured together as `_sim_ms` (app.py:340-344).
   - `self.area.queue_draw()`.

### 2.2 The snapshot (FACT, app.py:376-437)

`_on_snapshot` is a pure function of (sim state, width, height):

1. `hidpi.set_scale(device_scale)` (from `native.surface.get_scale()`, fallback `get_scale_factor()`, app.py:369-374; hidpi.py:24-29).
2. **The entire resize response** (app.py:385-390): `layout = resolve(w,h)`; `glass = console.stage_content(layout)` (the bezel aperture, not the raw stage); `vp = Viewport.for_stage(glass…)`. No allocation, no rebuild, no reset — `rebuilds` counter must stay 0 forever (GATE 0).
3. Press-feedback expiry on wall clock (0.13 s, `PRESS_FEEDBACK_S`, app.py:62-63, 408-410).
4. `_refresh_field(vp)` pushes live readouts into `ConsoleModel` (app.py:441-461).
5. Composition (app.py:412-430):
   - LAYER 0-1 `layer_under` texture (static);
   - LAYER 2 the organism: **the only per-frame cairo node**, clipped to the glass rect (`snapshot.append_cairo`), `draw_organism` (+ `draw_calibration` if F2);
   - LAYER 3 `layer_over` texture (static);
   - LAYERS 4-6 `regions(...)` textures (clock / rack / keys), each re-made only on its own inputs;
   - optional F1 debug overlay node.
6. FPS/`_frame_ms` stats: 0.5 s windowed mean (app.py:463-... `_tick_fps`).

**Texture bridge** (app.py:353-368): `id(surface)`-keyed LRU of **8** `Gdk.MemoryTexture`s, `B8G8R8A8_PREMULTIPLIED` + stride, `surf.flush()` before `bytes(surf.get_data())` copy; appended at logical rect `surf_size / ds`. Surfaces are never mutated after upload — this is why `pointfield` allocates its output image fresh every frame (pointfield.py:36-52: GSK may snapshot a painted surface; marking it dirty aborts Cairo; caught only by the live GATE 0 harness, not headless gates).

### 2.3 Event-driven vs continuous (FACT)

| Cadence | Driver | What runs |
|---|---|---|
| 60 FPS (focused) / 30 (unfocused) / 0 (suspended) | frame-clock tick, phase-paced | physiology + organism update, then snapshot |
| 5 Hz | GLib timeout | telemetry sample + history push (+ rack region rebuild) |
| 1 Hz | region cache key = `"%H:%M:%S"` | header clock region |
| ~0.13 s | wall clock | selector key press feedback |
| 2 s | GLib timeout (only with `--probe`) | probe "sample" JSONL event |
| on input | key/click/motion controllers | specimen select / cycle / mode, hover focus, queue_draw |
| on resize/state change | snapshot itself | probe "resize"/"state" events; layer cache miss rebuild |

**Unfocused/hidden behavior:** unfocused → 30 FPS (still simulating and drawing); suspended (other workspace, minimized, occluded) → tick returns early, **no simulation, no draw**; resume uses the clamped dt. MEASUREMENT (VALIDATION.md, `qa/perf_live.py`, 8 s dwells): instrument 900×700 focused 20.7 % CPU @ 61.0 FPS / unfocused 12.2 % @ 30.3 / hidden 0.2 % @ 0; archive 1440×900 25.5 % / 15.7 % / 0.1 %. Gate P12 asserts the 60/30/0 policy.

---

## 3. Hot paths for CPU (ranked)

Ranking uses the project's current headless GATE 3 breakdown (MEASUREMENT, VALIDATION.md "Final production pass", steady state): organism 0.66–2.54 ms, console 0.37–3.39 ms, sim 0.26–0.31 ms, total 1.29 ms @900×700 → 6.20 ms @2560×1600. INFERENCE on ranking where docs give per-component history instead of a current breakdown is labeled.

### Rank 1 — organism point field raster (`organism/render.py` + `organism/pointfield.py`)

- **Call site:** `draw_organism(cr, vp, org)` from `_on_snapshot` every paced frame (app.py:419-422); headless twin from `qa/gates.py`, `qa/offscreen.py`.
- **Per-frame work:**
  1. wash disc: cached (`render._WASH`, 6 entries, quantized radius, render.py:82-127) — warm cost ≈ one `paint()` blit;
  2. field sizing: `side_px = 2·R_FIELD·vp.scale`, `res = min(1, 760/side_px)` (render.py:140-147) — bounds the count at any window size;
  3. `PF.accumulate_physio` (pointfield.py:129-170): in-place persist decay (`acc *= persist` ×3), index math over up to **40 000** points (`astype(int32)`, bounds mask, `idx = iy*w+ix`), weight products, **three `np.bincount` calls** (density, excitation, hue-weighted);
  4. `PF.paint_physio` (pointfield.py:183-262): `np.flatnonzero(acc > thr)` lit-pixel pass — exp/power/clip alpha map, mix ramp, LUT gather, packed uint32 channel shifts; then `ImageSurface.create_for_data` + `cr.paint()` (scaled `FILTER_GOOD` when `res<1`).
- **MEASUREMENT:** organism pass 0.66 ms @900×700 → 2.54 ms @2560×1600 headless (VALIDATION.md final-production GATE 3). Historical priors that shaped it: full-stage field was 74 ms @2560×1600 before bounding to the creature disc + `MAX_FIELD_PX` (ARCHITECTURE.md "Bounded cost"); deep-field wash was 6.5 ms before the disc cache (ARCHITECTURE.md "visual-integration pass").
- **Per-frame allocations (deliberate, only one per pixel):** the output backing buffer — `np.zeros(stride*h)` in `paint_physio` (pointfield.py:194) / `np.empty` in `paint` (pointfield.py:246) — **must** stay fresh-per-frame for GSK correctness (pointfield.py:36-52). Accumulators/scratch are cached per `(species,w,h)` LRU-3 (pointfield.py:29-56).

### Rank 2 — the source equations + perturbations (`organism/sources.py`, `organism/mathforms.py`)

- **Call site:** `SourceBody.update` (mathforms.py:240-310) from `_on_tick` every paced frame. `_live = src.n · expression` (10k–40k points, `MIN_EXPRESSION=0.42`).
- **Work:** `src.sample(t, perm[:n])` runs one `_c0X` (sources.py:158-247: ~10–25 vectorized transcendentals per point — `hypot`, `sin`, `cos`, `where`), then `_pulse` (mathforms.py:349-390: ~25 in-place numpy ops incl. 4 `np.exp`), species `_phase/_shape/_tint/_perturb` (5–15 ops each, mathforms.py:395-740), seating + safety-radius mask + finite cleanup (mathforms.py:278-306). All buffers preallocated in `__init__` (mathforms.py:132-177) — "allocation-free" update.
- **MEASUREMENT:** sim (physiology + organism update) 0.26–0.31 ms/frame, flat across sizes (VALIDATION.md GATE 3 tables; also POC table "simulation 0.17 ms, independent of window size by construction").
- **INFERENCE:** cheap in absolute terms today, but it is pure float math over 10–40 k-element arrays — the *highest-leverage Rust win* (f32 SIMD, no numpy dispatch overhead per op). Each numpy op on 40 k floats pays ~µs-level fixed overhead × ~50 ops × 5 Hz-to-60 Hz.

### Rank 3 — console static layer rebuild (`ui/console.py`)

- **Call sites:** `layer_under` (console.py:1798-1803), `layer_over` (console.py:1805-...), cached by `_layer_key` (console.py:1713-1723) in LRUs of **2** entries each (console.py:1706-1710). Warm hit = one texture append. **Cold miss = full re-raster of the whole window at device scale** — happens on every integer size change during a resize drag and on any key-input change (`species.key`, `active`, `behavior`, show-flags, `mem_total`, hidpi scale, `diag` are all in the key).
- **MEASUREMENT:** the pass history brackets it: enriched uncached draw 14.2 ms @1400×860 (ARCHITECTURE.md "Why the console got faster"); steady-state console 0.37→3.39 ms after all caches (VALIDATION.md). GATE 8 proves warm ≡ cold byte-identical.
- **INFERENCE:** resize-drag cost is dominated by these rebuilds; a Rust port should keep the same cache design (it is a correctness-preserving, complete-input-keyed cache) but can raster the modules once per (module,size,scale) in parallel.

### Rank 4 — rack region rebuild at 5 Hz (`console.regions` → `_region`)

- **Call sites:** `regions()` (console.py:1851-1893) builds region keys: clock `(time string,)`; rack `(tel_key, fps_i, hist_v)` — i.e. re-rendered when a telemetry sample lands **or** displayed FPS changes; keys `(active, pressed, focus, disabled, mode_state, cycle_state)`. `_region` (console.py:1826-1849) composites under+over into the region rect then paints live content + clipped light.
- **MEASUREMENT:** "A rack region rebuild costs ~6 ms at 1440×900 @1.6×, five times a second" (VALIDATION.md, final production pass).

### Rank 5 — text (Pango) (`ui/chrome.py`, `ui/console._show`)

- `_text_w` memo (chrome.py:151-175, 4096 entries) — layout decisions re-measure the same strings each frame; a miss is a full Pango shaping pass. `_show` glyph-run cache (console.py:96-146, LRU 700, key includes family since the Astro/Microgramma pass). ~140 strings/frame, most byte-identical between frames.
- **MEASUREMENT:** caching `_text_w` + `_show` + rings took the enriched draw from 14.2 → 8.0 ms @1400×860 (ARCHITECTURE.md); no separate current number.

### Rank 6 — lighting falloff stamping (`core/lighting.py`)

- ~20 emitters/frame, each a cached A8 mask stamp (`_falloff`, quantized radius step 8 px, LRU 24, lighting.py:186-210). **MEASUREMENT:** radial gradients 3.5 ms → 0.16 ms as masks; removing the intermediate group saved 9.3 ms @1920×1080 (lighting.py docstring; ARCHITECTURE.md).

### Rank 7 — graph trace re-render at 5 Hz (`console._rack_trace` + `History.window`)

- `_TRACE_CACHE` LRU 16 keyed by `(ring id, w, h, ring.version, scale, rgb, ds)` (console.py:1358-1399); `Ring.window` reduces 300 samples → cols via `np.add.reduceat` with cached edge tables (history.py:100-121). 4 graphs × 5 Hz.

### Rank 8 — everything else

- `PhysiologyModel.update`: scalar EMA math only (physiology.py:128-160) — sub-0.01 ms (INFERENCE from sim totals).
- Selector key drawing, seven-segment digits: region-cached / path-only.
- Telemetry sampling: **MEASUREMENT** 78 µs/sample (VALIDATION.md GATE 4).

---

## 4. The static/dynamic split and cache boundaries

### 4.1 Composition layers (FACT, console.py:1762-1780 comment block + app.py:412-430)

| Layer | Content | Cadence |
|---|---|---|
| `layer_under` | background gradient, MODULE 01 shell/chassis, glass fill, corner brackets, radius ruler, polar graticule, cardinal marks | static texture; rebuilt only on `_layer_key` change |
| organism | wash disc + point field, clipped to glass aperture | **the only per-frame raster** |
| `layer_over` | MODULE 03 bezel, 02 header, 04 rack metal + static labels, 05 selector plate, 06 rail, all static type, constant light emitters | static texture |
| `regions` | header clock (1 Hz) · rack readouts (5 Hz / FPS change) · selector keys (input only) | texture per region, own key |

Regions are **snapped to whole device pixels** (`_snap`, console.py:1792-1799) so they land seamlessly on the static layers. The headless path `draw_under`/`draw_over` (console.py:1896-1932) composes the same surfaces, so "a QA frame IS a live frame".

### 4.2 Cache inventory (FACT — every derived-pixel cache, its key, bound, invalidation)

| Cache | Site | Key | Bound | Invalidates when |
|---|---|---|---|---|
| `_base` sprites | skin/surface.py:75-92 | name | unbounded (≤41 PNGs) | never (load once) |
| `_scaled` panels | surface.py:243-262 | (kind, name, insets, w, h, **hidpi scale**) | LRU 64 | size/scale change (natural miss) |
| `_UNDER_CACHE`/`_OVER_CACHE` | console.py:1706-1707 | `_layer_key`: (w, h, state, species.key, active, **behavior**, show_chassis/footer/controls/status, mem_total, hidpi, diag) | LRU 2 each | any key input changes |
| `_REGION_CACHE` | console.py:1708 | (name, snapped rect, hidpi, under id, over id) + region inputs | LRU 8 | clock second / telemetry key / FPS int / control state |
| `_TRACE_CACHE` | console.py:1358 | (ring id, w, h, **ring.version**, lo, hi, rgb, hidpi) | LRU 16 | every telemetry push (5 Hz) |
| `_TXT` glyph runs | console.py:80-82, 96-146 | (text, size, weight, tracking, align, max_w, rgb, alpha, hidpi, family) | LRU 700 | text/value change |
| `_TW_CACHE`/`_FD`/`_ATTR`/`_CAP`/`_BG` | chrome.py:103-107, 151-175 | text/size/weight/tracking(/family) | 4096 / dicts | never cleared (pure functions) |
| `_RING_CACHE` | console.py:247-276 | (radius, alpha, hidpi) | LRU 8 | resize only |
| `_FALLOFF` masks | lighting.py:186-210 | quantized radius (8 px step) | LRU 24 | resize only |
| `_WASH` discs | render.py:82-127 | quantized radius (6 px step) | LRU 6 | resize only |
| `_BUFFERS` accumulators | pointfield.py:29-56 | (species key, w, h) | LRU 3 | field size change |
| `_textures` | app.py:156, 353-368 | `id(surface)` identity | LRU 8 | surface object replaced |
| `Ring._idx_cache` | history.py:96-103 | cols | 8/dict | — |

**No cache is ever explicitly invalidated on resize** — every key contains the complete input set, so a miss happens naturally; `clear_static_cache()`/`clear_cache()`/`clear_cache()` exist only for QA determinism (console.py:1754-1759; surface.py:317; pointfield.py:59). The safety property (GATE 8): a hit is always byte-identical to a miss.

**Cache boundary line (INFERENCE for the port):** the seam is exactly "pixels that depend only on discrete inputs" (everything in §4.2) vs "pixels that depend on the continuous clock" (organism field + the tiny region inputs). The Rust port must reproduce *key completeness*, not the LRUs' exact sizes, except: region snapping, hidpi quantization (¼ steps), and `MAX_FIELD_PX=760` are **visual** contracts, not just performance ones.

---

## 5. Rust translation boundaries — proposed crate seams

Proposed workspace (INFERENCE from §1 dependencies; each seam replaces exactly these Python entry points):

```
abyssal-telemetry  ──►  abyssal-core  ──►  abyssal-creature
                                   │              │
                                   ▼              ▼
                          abyssal-raster  ◄────────┘   (PULSE_LUT, species metadata)
                                   ▲
                          abyssal-host (gtk4-rs binary)
```

### Crate 1: `abyssal-telemetry` (pure std + tiny slice math)

- Replaces: `abyssal/telemetry/source.py` (`TelemetrySource::new/sample`, hwmon discovery + `_CHIP_LABEL_PRIORITY` ordering, `/proc/diskstats` whole-device filter incl. the nvme-partition carve-outs, `/proc/net/dev` lo-skip, never-raise degradation), `abyssal/telemetry/history.py` (`Ring`, `History`, `window()` column means + `version`).
- Risk: **low**. No GUI, no cairo. Ports 1:1. Keep the "never raises" contract (source.py:289-330) — the organism depends on graceful degradation.

### Crate 2: `abyssal-core` (pure value types + math)

- Replaces: `core/signals.py` (both structs), `core/world.py` (consts), `core/viewport.py` (`Viewport::for_stage`, `px`, `length`, `isotropy_error`), `core/layout.py` (`resolve`, `Rect`, `LayoutState`, `TypeScale`, `REF_*`), `core/physiology.py` (`PhysiologyModel::update`, `condition`, all τ constants), `core/theme.py` (palette consts), plus `core/lighting.py`'s *policy* constants.
- Risk: **low**, but parity-critical: `Physiology` floats drive every visual channel; the EMA/hysteresis/rate-limit constants (physiology.py:28-52) must match bit-for-bit-ish; PH1–PH7 gates are the acceptance tests.

### Crate 3: `abyssal-creature` (the simulation; no GUI deps)

- Replaces: `organism/sources.py` (`Source` records + `_c01`–`_c05`), `organism/mathforms.py` (`SourceBody` protocol + 5 species, anatomy, pulse, perturbations), `organism/pointfield.py` accumulate half (`accumulate`, `accumulate_physio`), `organism/render.py`'s `PULSE_LUT` builder + ink/exposure math.
- Porting rule (FACT, docs/CREATURE_EQUATIONS.md is the normative reference): keep variable names, operator order, constants, loop bounds — GATE 4 asserts each specimen reduces *exactly* to its source at rest (residual 0.0 for s03's mirror at rest).
- Numerics: float32 end-to-end (sources deliberately use float32 indices, sources.py:113-121); `0.3/k` spike handling + `errstate` suppression must be reproduced (`k*k>15` weights, sources.py:196-203).
- Risk: **medium** — numpy → `ndarray`/raw slices changes evaluation order unless done carefully; the resize-invariance and rest-identity gates will catch drift.

### Crate 4: `abyssal-raster` (skin + console drawing; cairo-rs)

- Replaces: `skin/{hidpi,surface,catalog,modules,fascia}.py`, `ui/{segment,selector,chrome,console,fonts}.py`, `organism/render.py` draw half, `core/lighting.py` paint half.
- Exact Python entry points each seam replaces:
  - `skin.surface::{sprite, nine_surface, draw_nine, draw_sprite, draw_sprite_fit, draw_sprite_rot90, fit_box}`
  - `skin.modules::Module.place/draw/bay`, `skin.fascia::Fascia.place`
  - `ui.console::{layer_under, layer_over, regions, draw_under, draw_over, control_geometry, stage_content, hit_controls}` — the QA-visible surface.
  - `ui.segment::draw/draw_unit/measure`, `ui.selector::{layout, hit, from_module}`
  - `ui.chrome::{draw_background, _show, _text_w, _cap, _fit}` → Pango via `pango`/`pangocairo` crates; font installation strategy must change (see risks).
- Risk: **high** — this is 1932 + 729 + ~1500 lines of pixel-exact drawing; GATE 8 / P9 (byte-identical cold renders at 1.0× and 1.6×) are the acceptance bars.
- **Hidden global risk (FACT):** `skin/hidpi.py` `_DS` is module-global state set by the host each frame and read by every cache key. In Rust, make it an explicit `ScaleCtx` threaded through the raster calls — but note every cache key then includes it; forgetting it in one key reproduces the "soft metal on HiDPI" bug the pass fixed.

### Crate 5: `abyssal-host` (gtk4-rs application)

- Replaces: `abyssal/app.py` — `Monitor`, `MonitorView.do_snapshot`, tick/pacing/cadence, texture bridge, input controllers, probe JSONL, CLI. See §6 for the behavioral contract.

### Shared-file / integration-risk flags (for the INTEGRATOR)

1. **`ConsoleModel` is written by the host and read by the raster** (app.py:441-461 writes; console reads). It must live in ONE crate (recommend `abyssal-raster`, host owns the instance) — otherwise a circular host↔raster dependency appears. **Integration risk: moderate.**
2. **`organism/species.py` is consumed by raster (labels) and creature (bodies)** — split the *metadata* (`Species` records) into `abyssal-core` (or `abyssal-catalog`) so raster does not depend on creature. FACT: console imports only `CATALOGUE`/`Species`/`by_index`, never bodies.
3. **`PULSE_LUT`** is built in `render.py` (raster side) but is semantically creature-side (palette positions ↔ `state_hue`). Recommend moving to `abyssal-creature` to keep creature self-contained; it is currently the one raster↔creature data dependency. (render.py:60-77)
4. **`ui/fonts.py` installs TTFs via filesystem + `fc-cache`** before the first Pango font map (chrome.py:71-73). In Rust either keep that behavior or load faces explicitly into a `PangoFontMap`; family strings "Astro", "Microgramma", "JetBrainsMono Nerd Font", "Noto Sans Mono" are load-bearing (theme.py:47-57). Parity risk if the port machine lacks JetBrainsMono.
5. **QA harness coupling:** `qa/*.py` import `abyssal.*` directly. Decide early (INTEGRATOR): keep Python gates as the oracle against the Python build for baselines, and port GATE 1/3/8-style checks to Rust unit/integration tests. No Python file needs editing for the port itself if the crates are additive — **no seam forces a shared-file edit inside `abyssal/`** as long as the Python app remains the reference implementation during Phase B/C.

---

## 6. GTK behaviors the Rust host must reproduce

All FACT unless noted; these are the behaviors the QA harnesses assert.

1. **One custom widget, no widget tree.** `MonitorView(Gtk.Widget)` whose `do_snapshot` delegates to the window (app.py:80-91). gtk4-rs: subclass `Gtk::Widget` (needs the `subclassing` feature) and override `snapshot`. There is deliberately no GL context, texture mutation, scene graph, or render thread (app.py docstring; ARCHITECTURE.md "Why this stack").
2. **Resize contract.** A resize changes only the `width/height` the next snapshot receives; `resolve()` + `Viewport.for_stage()` are computed fresh per frame (app.py:385-390). Asserted by `qa/gates.py::gate_resize_invariance` — 400 frames at random sizes vs never-rendered: **bit-identical** (MEASUREMENT, VALIDATION.md), and `rebuilds == 0` across the Hyprland torture (GATE 0: 122 transitions, 222 resizes, 109-resize storm, worst isotropy 1.42e-13 px).
3. **Tick pacing.** Frame-clock `add_tick_callback` + phase accumulator with `tol = min(interval/2, 4 ms)` (app.py:320-330). Do **not** port as "draw every tick" — the docstring records the 60→40 FPS aliasing failure on 120 Hz panels.
4. **Adaptive cadence.** `SUSPENDED` → 0 (no sim, no draw, early return *before* dt accumulation), unfocused → 30, focused → 60, `--fps-cap` overrides both (app.py:301-313). Suspended detection: `get_surface().get_state() & Gdk.ToplevelState.SUSPENDED` with try/except fallback.
5. **dt discipline.** Frame-time deltas, clamp `[0, 0.1] s`, first tick `1/60` (app.py:331-338) — teleports on resume are a gate failure, not a nicety.
6. **Input.** Key controller on the window: `1–5` select, `Right/Down/n/N` and `Left/Up/p/P` cycle, `m/M` mode-state cycle (`inactive→armed→active→error`), `q/Q/Escape` close, `F1` debug overlay toggle, `F2` calibration toggle, `f/F/F11` fullscreen toggle (app.py:238-263). `GestureClick` on the area routes through `console.hit_controls` (pure geometry, same rects as drawn — GATE 6); left/right halves of the cycle rocker step ∓1. `EventControllerMotion` drives key hover focus; `leave` clears it (app.py:217-236).
7. **Press feedback on wall clock.** `PRESS_FEEDBACK_S = 0.13 s` (app.py:62-63), expired inside `_on_snapshot` against `time.perf_counter()` (app.py:408-410) so duration is frame-rate independent.
8. **Fullscreen.** `fullscreen()`/`unfullscreen()` on `is_fullscreen()`; no other state changes — the layout system handles the rest via aspect gates (layout.py:117-133: aspect < 0.72 or > 2.2 → COMPACT; height < 420/560 demotes).
9. **HiDPI.** Read `native().surface().get_scale()` (fallback `get_scale_factor()`), quantize to ¼ steps clamp [1,4] into the hidpi context (app.py:369-374; hidpi.py:24-29). All caches allocate at device resolution but key on logical size + scale; textures appended at logical rect = surface size ÷ ds (app.py:404-407). The organism point field stays **logical** resolution by choice (soft points; ARCHITECTURE.md "Device scale"). Target desktop is fractional 1.6×.
10. **Texture bridge.** ≤ 8 `Gdk.MemoryTexture`s, `B8G8R8A8_PREMULTIPLIED`, stride-correct, keyed by surface identity, `flush()` before upload, never mutate an uploaded surface (app.py:353-368; pointfield.py:36-52 hazard note). In gtk4-rs, `Gdk::MemoryTexture::new` copies bytes — the "fresh image per frame" rule can be relaxed to "never reuse a texture's backing", but keep fresh output buffers until the port is measured.
11. **Offscreen/headless path.** The same draw must run without GTK (`console.draw_under/draw_over`, `qa/offscreen.py::frame`), deterministic per (seed, sim-time, size) — Rust should expose a hostless `render_frame(w, h, …) -> ImageSurface` used by tests and screenshot generation.
12. **Probe protocol.** `--probe FILE` appends JSONL on `resize` / `state` / 2 s `sample` events with fields incl. `sim_time` (must be monotonic across every resize), `iso_err`, `clips`, `rebuilds`, `fps`, `draw_ms`, `sim_ms`, `tel_ms`, `cadence`, `active` (app.py:490-517). `qa/torture.py` asserts against this file — emitting the same schema lets the existing harness validate the Rust host unchanged.
13. **Window/app identity.** `APP_ID dev.abyssal.OrganismMonitor`, `Gio.ApplicationFlags.NON_UNIQUE` (multiple instances allowed by design), title "Abyssal Organism Monitor", default 900×700; CLI: `--width --height --seed --specimen --debug --calibration --probe --quit-after --qa-temp --fps-cap` (app.py:546-581). `--qa-temp` rewrites only the temperature reading (dataclasses.replace, app.py:279-284) — needed for thermal screenshot reproducibility.
14. **Specimen lifecycle.** One `SourceBody` per specimen built lazily and cached forever; switching never resets clocks, history, or seeds (app.py:112-118, 186-196; GATE 7: 120 switches, no clock rewound).

---

## Contradictions / stale evidence found

1. **`docs/ARCHITECTURE.md` "Specimens" section is stale**: it describes `organism/form.py` + `organism/morphology.py`, which no longer exist — the final convergence pass replaced them with `sources.py` + `mathforms.py` (confirmed by the CODE inventory and by sources.py/mathforms.py docstrings). Later sections of the same doc describe the current design. Rust porters must follow the *later* sections + `docs/CREATURE_EQUATIONS.md`.
2. **`docs/VALIDATION.md` early ("POC") sections report old-architecture numbers** (e.g. "organism 9.7 ms" per frame, `qa/perf.py`, quadrant-balance GATE 1) that no longer match the current code path (current GATE 1 is projection-identity; current organism pass is 0.66 ms @900×700 headless). The **"Final production pass"** and **"Final surgical polish + physiology pass"** tables are the current truth. Any Rust-vs-Python baseline must be captured fresh with `qa/gates.py` GATE 3 + `qa/perf_live.py`, not copied from this doc.
3. Not a contradiction but a subtlety: `m.behavior` (STABLE/ACTIVE/AGITATED, derived from agitation, app.py:449-450) is part of the static-layer cache key (`_layer_key`, console.py:1713-1723), so a physiology crossing can rebuild a static layer mid-run. Reproduce the key faithfully in Rust.

## Missing evidence / open questions

- **No first-hand runtime measurements in this audit** (no shell tool available in this environment). Before Phase B baselining, run: `python3 qa/gates.py` (GATE 1+3), `python3 qa/console_gates.py` (GATES 4-8), `python3 qa/perf_live.py` (CPU/RSS/FPS matrix), `python3 qa/offscreen.py` (golden frames) — and archive outputs as the parity oracle.
- Cold vs warm static-layer cost at each size for the *current* code is only bracketed by history (14.2 ms uncached @1400×860, prior pass); no current number for a full cold frame.
- `_on_telemetry` firing while suspended is implied by design (timeout never gated) and by 0.1–0.2 % hidden CPU, but no doc states it explicitly — confirm intended during port (INFERENCE: intended, keeps 60 s graphs gapless).
- `skin/fascia.py` internals were not read line-by-line (header fascia placement only); no parity risk identified beyond the modules.py pattern it shares.

## Source files kept for this audit

Read in full or in targeted part: `abyssal/app.py` (581 ln), `core/{world,viewport,layout,theme,lighting,signals,physiology}.py`, `organism/{sources,mathforms,pointfield,render,species}.py`, `telemetry/{source,history}.py`, `skin/{hidpi,surface,catalog,modules}.py`, `ui/{console,chrome,fonts,segment,selector}.py` (debug.py skimmed via call sites), `docs/{ARCHITECTURE,VALIDATION}.md`, `qa/{gates,perf_live,shots,torture,offscreen}.py`.
