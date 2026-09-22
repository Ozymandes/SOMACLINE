# POC validation report

All numbers below were measured on this machine, not estimated.
Reproduce with the commands in `README.md`.

**Machine:** Omarchy, Hyprland 0.56.2, Wayland, AMD Ryzen 7 5800H (16 threads),
Radeon Vega iGPU (+ RTX 3050 Mobile, unused), eDP-1 2560x1600@120Hz at
**scale 1.6**. The fractional scale matters: every logical pixel is 2.56 device
pixels of Cairo rasterisation, and fractional scaling is a classic source of
"fullscreen looks right, tiled does not".

## GATE 0 — host stability  **PASS**

`python3 qa/torture.py --cycles 6 --shots`

Six cycles of: tile -> 5 tiled resizes -> fullscreen -> unfullscreen ->
maximize -> unmaximize -> float -> 8 floating resizes -> retile, then a rapid
resize storm, then a final tile.

| | |
|---|---|
| transitions driven | **122** |
| probe events | 371 |
| distinct window sizes | 126 |
| resizes seen by the app | 222 |
| rapid storm | 109 resizes in 6 s, survived |
| crashes | **0** |
| simulation clock | 0.02 s -> 84.17 s, **monotonic** (never reset) |
| `rebuilds` counter | **0** |
| worst isotropy error | **1.42e-13 px** |
| clipping events | **0** |
| states exercised | COMPACT, INSTRUMENT, ARCHIVE |

## GATE 1 — geometry  **PASS**

`python3 qa/gates.py`, 20 sizes from 180x120 to 2560x1600 including hostile
aspect ratios (2000x400, 300x900, 1800x420).

* isotropy error: worst **1.7e-13 px** (float noise; a circle stays a circle)
* centring error: **exactly 0.0** at every size
* quadrant balance: **exactly 0.00%** at every size — structural, because the
  organism is authored in polar coordinates and only the *angle* carries
  per-lobe phase, so the radial profile is bit-identical across all four lobes
* clipping: none. Organism max extent 414.6 of the 460 design radius (90.1%)
* breakpoints verified at 699/700 and 1099/1100

Visual proof: `docs/shots/calibration.png` (F2 overlay).

## Resize invariance — **PASS**

Two organisms fed identical timesteps for 400 frames; one is rendered at a
random window size every single frame, the other is never rendered.

```
max |dx| 0.000e+00   max |dy| 0.000e+00   dt 0.000e+00
```

Bit-identical. Rendering cannot touch simulation.

## GATE 2 — responsive UI  **PASS**

Three genuinely different layouts, not one scaled poster. Font sizes are fixed
per state, not interpolated. Arrangement is additionally **aspect-aware**:
above a body aspect of 1.45 the readouts move to a side column so a short wide
tile keeps a near-square stage instead of a starved letterbox strip.

Chrome was swept over `range(120,2600,37) x range(100,1700,41)` = **2720 sizes,
0 exceptions**, plus a pixel-level overflow proof (every pixel with alpha > 24
lies inside an allotted rect) over 3510 configs x 3 telemetry sets, **0 leaks**.

Shots: `docs/shots/compact.png`, `instrument.png`, `archive.png`.

## GATE 3 — performance

Live, in the real window under Hyprland, steady state (`python3 qa/perf.py`):

| logical | device @1.6 | state | fps |
|---|---|---|---|
| 420x340 | 672x544 | COMPACT | **120** (display cap) |
| 700x560 | 1120x896 | INSTRUMENT | **83** |
| **900x700** | 1440x1120 | **INSTRUMENT** | **68** |
| 1150x720 | 1840x1152 | ARCHIVE | 56 |
| 1400x860 | 2240x1376 | ARCHIVE | 41 |
| 1600x1000 | 2560x1600 | ARCHIVE (fullscreen) | 33 |

Frame cost breakdown at the canonical tile: simulation **0.17 ms** (flat, and
independent of window size by construction), background 1.0 ms, organism
9.7 ms, chrome 0.6 ms.

Footprint at the canonical tile, measured over 15 s of steady animation:

* **RSS 156 MB**, growth **+0.43 MB / 15 s** (allocator noise, not a leak —
  `tracemalloc` shows ~0 bytes net over 500 steady-state `update()` calls)
* **CPU 81% of one core** = 5.1% of the 16-thread machine, at ~68 fps

The RSS is dominated by the Python + GTK4 + NumPy runtime, not by the
simulation (the organism's arrays total under 1 MB and are allocated once).

**The 60 fps target is met at the canonical tile and below. It is not met at
large sizes** — see Known limitations.

## GATE 4 — telemetry  **PASS**

`python3 -m abyssal.telemetry.source`

* sensor selected: **k10temp / Tctl** (`/sys/class/hwmon/hwmon5/temp1_input`)
* live reading during test: 60–88 °C, normalised 0.46–0.90
* CPU load verified to actually move: idle **1.4%** -> 6 busy loops **46.3%**
  -> killed **3.4%** (16 threads, 6 busy ~ 37.5%, so this tracks)
* memory: 9.1 / 13.5 GiB via `1 - MemAvailable/MemTotal`
* cost: **78 µs/sample**, pure stdlib, no subprocess, non-blocking
* failure path: file reads monkeypatched to raise -> degrades to
  `temp_available=False` with no exception
* telemetry cannot affect layout: it feeds only the physiology smoother and
  readout strings, never a rect

## Known limitations

1. **Large-window framerate.** Cairo is a CPU rasteriser; cost scales with
   device pixels covered by strokes. At 2560x1600 the organism pass alone is
   ~19 ms. Measured and rejected as fixes: batching filaments into shared paths
   (*worse* — 9.3 -> 14.9 ms, Cairo tessellation is superlinear) and Python
   path-build overhead (only 0.33 ms, not the bottleneck). `ANTIALIAS_FAST` was
   adopted for a real 16% win. Going beyond this means moving the stroke pass to
   the GPU, which the architecture already permits: the renderer is a pure
   function of world-space geometry and the simulation would not change.
2. **Omarchy renders every window at 98.5% opacity**, so the wallpaper faintly
   shows through the near-black field. Not an app bug; opt-out rule is in the
   README.
3. 2000x400 COMPACT is survivable rather than beautiful — nothing clips, but a
   190px column on a 2000px window is inherently sparse.
4. At 180x120 the meters are dropped entirely and only labels plus values
   survive. Pure triage, by design.
5. **It renders every frame the compositor offers.** At ~68 fps that is 81% of
   one core, which is fine for a focused window but wasteful for a widget left
   open all day. The organism moves slowly enough that a frame cap would be
   invisible; the cheap fix is to skip ticks in `Monitor._on_tick` against a
   target interval (a `--fps` flag). Not done here because the gates had
   already passed and it is not needed to prove the foundation.
6. Single organism, single specimen, no persistence, no settings — deliberate
   POC scope.

---

# Visual-integration pass — measured results

Machine: Omarchy / Hyprland 0.56, eDP-1 1600x1000 logical at scale 1.6.

## Gates

```
GATE 1 geometry        PASS   20 sizes, iso_err < 2e-13 px, centre_err 0,
                              shape residual < 9e-13 world units
GATE 3 performance     PASS   every benched size inside the 16.6 ms budget
resize invariance      PASS   400 frames, |dx| = |dy| = 0
GATE 4 species         PASS   5 specimens, extent within world radius,
                              closest morphological pair 0.665 (floor 0.350)
GATE 5 skin            PASS   6 panels x 6 sizes, content always inside
GATE 6 selector        PASS   10 key plates, identical 256x267 footprint,
                              50 key centres hit-test to their own index,
                              gaps between keys reject
GATE 7 switching       PASS   120 switches, no clock rewound
GATE 8 static layer    PASS   warm vs cold render, max pixel delta 0
```

## Frame cost

Headless, full frame (simulation + organism + console), before and after the
caching work in this pass:

| surface | sim | organism | console | total | cap |
|---|---|---|---|---|---|
| 420x340 | 0.49 | 1.27 | 1.69 | **3.45 ms** | 290 fps |
| 900x700 | 0.49 | 1.74 | 3.20 | **5.43 ms** | 184 fps |
| 1400x860 | 0.49 | 2.30 | 4.07 | **6.85 ms** | 146 fps |
| 1920x1080 | 0.49 | 3.07 | 5.55 | **9.11 ms** | 110 fps |
| 2560x1600 | 0.49 | 4.98 | 7.42 | **12.89 ms** | 78 fps |

Every size is now inside the 60 fps budget; at the start of the pass only the
two smallest were.

## In the real application

Measured from the app's own probe log, so these include GTK and the frame
clock:

| window | surface | our draw | our sim | presented |
|---|---|---|---|---|
| 781x468 tiled | 1250x750 | — | — | **78–80 fps** |
| 1400x880 floated | 2151x1352 | 3.3 ms | 1.3 ms | 30 fps |
| 1576x950 tiled | 2521x1520 | 3.4 ms | 1.3 ms | 26 fps |

The canonical tile runs well above target. At large sizes the application
uses **4.6 ms of its 16.6 ms budget** and the presented rate is bounded
elsewhere: a ~15 MB CPU-rendered surface has to reach the compositor every
frame, and Hyprland settles on half-rate presentation for it. Changing
renderer (`GSK_RENDERER=cairo|gl|ngl`) moves this by 1–3 fps, which is what
identifies it as presentation rather than drawing.

The FRAME / RENDER module reports that honestly rather than flattering it —
it is showing the real presented rate, which is the point of the readout.

## Alpha verification

All 65 runtime sprites re-checked pixel-wise. The ten specimen keys:
`alpha.min() == 0`, corner alpha 0, 2.3–3.5% partially transparent edge
pixels, no matte, identical footprint. Full audit in
`assets/hardware_v2/INVENTORY.md`.

## Asset usage

51 of 65 sprites referenced. Every unused asset is accounted for in the
inventory audit, including the two deliberate omissions — the telemetry rack
(duplicates the module shell's own mounting and would cost ~20% of the graph
width) and the chassis master (a fixed composition that cannot follow an
arbitrary window aspect).


---

# Final convergence pass — measured results

## Gates

```
GATE 0 live Hyprland     PASS   62 transitions, 146 resizes, 104 sizes, all
                                three states, 0 rebuilds, clock monotonic,
                                worst isotropy 1.1e-13 px
GATE 1 geometry          PASS   projection is the identity, residual < 1e-12
GATE 3 performance       PASS   every benched size inside 16.6 ms
resize invariance        PASS   400 frames at random sizes, |dx| = |dy| = 0
GATE 4 species           PASS   5 source equations; all reduce EXACTLY to
                                their source at rest; closest pair 1.01
                                (floor 0.35); mirror asymmetry 0.14 at rest,
                                0.85 at the thermal limit
GATE 5 skin              PASS
GATE 6 selector          PASS   10 plates, one footprint, 50 hit tests
GATE 7 switching         PASS   120 switches, no clock rewound
GATE 8 static layer      PASS   warm vs cold render, max pixel delta 0
```

## Frame cost (headless, full frame)

```
=== GATE 3 — PERFORMANCE ===
         size      sim  organism  console    total  fps_cap
    420x340      0.25ms     0.15ms    1.75ms    2.15ms     466
    900x700      0.28ms     0.68ms    3.25ms    4.20ms     238
   1400x860      0.27ms     0.83ms    4.16ms    5.26ms     190
   1920x1080     0.27ms     1.75ms    5.99ms    8.02ms     125
   2560x1600     0.31ms     7.14ms    8.40ms   15.84ms      63
```

## Source-equation validation

`python3 qa/creature_validate.py` renders each equation in its own 400×400
canvas with its original sample count, time step and compositing. Output in
`docs/creature_validation/`; `comparison.png` sets each beside its engraved
key.

## Known limitations

- **Presented frame rate at large windows.** The app spends ~4.6 ms of its
  16.6 ms budget; above roughly 1400×880 logical on this 1.6× display the
  compositor presents at half rate. The FRAME / RENDER module reports that
  truthfully. Tiled at the canonical size it presents at 78–80 fps.
- **Source 05's orientation.** The equation draws its bulb at the lower end
  of the rachis; the engraved key shows it at the top. The equation is
  authoritative, so it is not flipped.
- **Coordinates are planar.** The specimen field is 2-D, so the VECTOR FIELD
  zone reports x and y; z was always zero and cost the width that let the
  reading be shown whole.

---

# Final production pass — measured results

Machine: Omarchy / Hyprland 0.56, 2560×1600 panel at **1.6× scale, 120 Hz**,
NVIDIA GL. All numbers below were measured, not derived from a frame cap.

## Gates

| gate | result |
|---|---|
| GATE 0 live Hyprland torture (62 transitions, 108-resize storm) | **PASS** — sim clock monotonic 0.01→37.22 s, worst isotropy 9.6e-14 px, 0 rebuilds |
| GATE 1 geometry (17 sizes) | **PASS** |
| GATE 3 performance (headless) | **PASS** |
| resize invariance (400 random resizes) | **PASS** — bit-identical |
| GATES 4–8 species / skin / selector / switching / static layer | **PASS** |
| P1 six module assets exist | **PASS** |
| P2 valid alpha | **PASS** — rounded corners alpha 0, master borders ≤ 1 |
| P3 apertures transparent | **PASS** — 8 apertures, alpha exactly 0 |
| P4 wells accept the real keys | **PASS** — one size, pitch 200.66 px exact, labels clear |
| P5 no active-key matte | **PASS** — green lift outside the lit key 2/255 |
| P6 no baked runtime values | **PASS** — 31 data bays, max luminance 25.5 |
| P7 no duplicate outer fasteners | **PASS** — nearest cross-module screws 2.2 diameters apart |
| P8 rack seating | **PASS** — 1.8 px from reference, right margin 35.9 / 36 px |
| P9 deterministic cache | **PASS** — byte-identical cold renders at 1.0× and 1.6× |
| P10 bounded history | **PASS** — 4 × 300 samples, 4800 B before and after 20 000 pushes |
| P11 compact without selector bank | **PASS** |
| P12 reduced cadence | **PASS** — 60 / 30 / 0 FPS |

## Reference agreement (`qa/compare.py`, 1448×1086)

| module | reference | application | max edge Δ |
|---|---|---|---|
| header | 55,55 1340×145 | 55,55 1340×145 | 0.3 px |
| observation | 52,222 770×568 | 52,222 771×573 | 4.8 px |
| selector | 52,800 770×152 | 52,805 771×145 | 4.8 px |
| rack | 838,222 534×730 | 839,222 533×729 | 1.8 px |
| footer | 48,962 1350×78 | 48,960 1350×78 | 2.0 px |

## Real application resource use (`qa/perf_live.py`, 10 s dwell per state)

CPU is process utime+stime over the dwell; draw is the app's windowed mean
snapshot time; sim is simulation per frame; tel is one /proc+/sys sample.

| layout | window | CPU % | FPS | draw ms | sim ms | tel ms | RSS MB |
|---|---|---|---|---|---|---|---|
| compact 600×480 | focused | 16.2 | 59.9 | 1.05 | 0.52 | 0.36 | 278 |
| compact | hidden | 1.0 | 0 | – | – | – | 278 |
| instrument 900×700 | focused | **20.1** | 59.0 | 1.49 | 0.55 | 0.33 | 311 |
| instrument | unfocused | **11.7** | 30.1 | 1.83 | 0.56 | 0.38 | 313 |
| instrument | hidden | **0.2** | 0 | – | – | – | 311 |
| archive 1440×900 | focused | 26.8 | 60.0 | 1.96 | 0.65 | 0.35 | 358 |
| archive | unfocused | 16.5 | 29.9 | 2.67 | 0.63 | 0.38 | 359 |
| archive | hidden | 0.9 | 0 | – | – | – | 359 |

Before this pass, same harness: instrument focused **87.1 %** at 58 FPS,
archive focused **89.5 %** at only 34 FPS — GTK re-uploading a full-window
cairo surface every frame. Hidden-state rows report the last FPS sample before
suspension in the raw log; the frame clock is stopped and CPU confirms it.

Unfocused measurements on a desktop in use are affected by Hyprland's
follow-mouse focus. The harness parks the focus sink under the pointer, but
pointer movement can still return focus. The instrument unfocused row is from
a run where focus held for the whole dwell; the cadence policy itself is
proven by gate P12.

**RSS** at instrument is ~240–310 MB; `smaps` shows ~96 MB of that is
file-backed shared libraries (NVIDIA GL 34 MB, LLVM 19 MB, GTK 8 MB). The
process's own anonymous memory is ~130 MB (Python, NumPy, decoded module
sprites, device-resolution caches).

## Headless frame cost, steady state (`qa/gates.py` GATE 3)

| size | sim | organism | console | total |
|---|---|---|---|---|
| 900×700 | 0.26 ms | 0.66 ms | 0.37 ms | **1.29 ms** (was 4.26) |
| 1400×860 | 0.26 ms | 0.78 ms | 0.74 ms | 1.79 ms (was 5.29) |
| 1920×1080 | 0.27 ms | 1.15 ms | 1.49 ms | 2.91 ms (was 8.27) |
| 2560×1600 | 0.28 ms | 2.54 ms | 3.39 ms | 6.20 ms (was 14.86) |

A rack region rebuild (on a telemetry sample) costs ~6 ms at 1440×900 @1.6×,
five times a second.

## Known limitations

- The generator cannot hold an exact pitch; the selector wells are trued
  deterministically at build time (max correction 0.99 runtime px), hidden
  under the keys.
- The telemetry rack's state wells are narrower than the reference's (12 % vs
  19 % of the rack width); state words use deliberate short forms below
  ~70 px (`NOM.`, `ELEV.`).
- The shell's generated perimeter is thicker than the reference's; it is drawn
  at 0.17 of the chassis scale, which stretches the edge-band texture along
  its length (reads as brushed metal).
- The organism point field is rendered at logical resolution and upscaled on
  HiDPI (by choice, for CPU).
- Omarchy's default window opacity lets the wallpaper show faintly through
  the glass; see README for the per-window rule.

---

# Final surgical polish + physiology pass (this revision)

## Typography — Astro & Microgramma  **PASS**

`assets/fonts/`: `astro.ttf` (family **Astro**, Regular) and
`microgrammanormal.ttf` (family **Microgramma**, Normal). Both are installed
into `~/.local/share/fonts/abyssal/` by `abyssal/ui/fonts.py`
(`ensure_user_fonts()`), which runs once at `chrome` import — before the
Pango font map is created — and refreshes fontconfig only when bytes change.

| role | family | where |
|---|---|---|
| major display titles | **Astro** | "ABYSSAL ORGANISM MONITOR", specimen scientific name, hero epithet |
| technical / support type | **Microgramma** | header status rail, bottom archive rail, rack channel titles + captions, state wells, selector service labels, field microcopy |
| numerics | procedural seven-segment | clock, telemetry numerics, units — untouched |
| micro labels, axis ticks | JetBrains Mono (unchanged) | graph scales, rulers, tiny axis values |

Caching: nothing regressed. Static type renders into the cached static layers
(deterministic, byte-identical across cold renders — GATE 8 re-verified);
live type goes through the same `_show` raster cache as before, whose key now
includes the family. Font descriptions, widths and cap heights are cached per
(size, weight, family). No font face is created per frame.

## Selector seat & production ledge  **PASS**

Measured off the authored module (source px): trough interior y 27–235,
aperture hole 65.5–203.5, key bay 36–204, identifier ledge 207.5. At bay
height a key left **10.6 px** of trough above it and only **4.8 px** below
before the ledge divider — it read pushed up out of its well. Fix: one shared
downward translation, `SEAT_DROP = 3.0/168` of key height (selector.py),
which balances the reveals (~7.6 / ~7.8) while keeping the key shadow clear
of the ledge. Pure translation: equal scale, baseline, pitch untouched; hit
testing, drawing, labels and lamp points all read `key_rect`, so they cannot
disagree. P4 (wells/keys/labels at 5 sizes) and GATE 6 re-verified.

The grey `01 AQS-0042` ledge identifiers are now **service type**: drawn only
when the F1 diagnostics overlay is up (`ConsoleModel.diag`, part of the
static-layer cache key). Production views show clean metal; keyboard 1–5
still selects.

## Telemetry physiology (propagating pulse)  **PASS**

`python3 qa/physiology_gates.py` — seven deterministic gates (PH1–PH7),
all through the REAL `PhysiologyModel` and the REAL pulse layer:

- **PH1** the condition (activity 0..1) is normalised against ranges where
  each sensor means something (CPU concave over 0–85 %, thermal stress
  onset 77 °C full 93 °C, memory top 20 %, I/O flux, frame-rate shortfall),
  hysteresis deadband 0.015, asymmetric EMA (rousing τ 1.4 s, calming τ 3.2 s
  — rouse 93 frames vs calm 444 frames to half amplitude), rate limit
  0.30/s. Hue mapping `state_hue` monotone blue→orange.
- **PH2** five species × QUIESCENT/NORMAL/STRESSED: finite everywhere, mean
  hue strictly ordered (0.114 → 0.417 → ~0.98), activity ordered, extents
  inside the world.
- **PH3** wave speed = PULSE_HZ·(1+PULSE_GAIN·excite) per species (e.g.
  sigmata 0.229 → 0.385 cyc/s under load); front travels forward along the
  body coordinate by the integrated distance; identical inputs give bitwise
  identical state.
- **PH4** personalities: 01 tips lag spine 0.055 and the front turns ragged
  under stress (17-rad modulation 0.050 vs exactly 0 at rest); 02 bodies
  alternate at lag 0.500, stress desynchronises and warms one body first;
  03 mirror symmetric at rest (0.004), heat lags one side 0.028; 04 chase
  0→1→2→3, stress pulls the four flares together (spread 0.042 → 0.003);
  05 ribs lag shaft 0.070, stress curls the feather (0.41 rad mean).
- **PH5** rest identity: with the five drives zeroed (density full) and the
  condition zero, resting geometry is bitwise independent of the pulse
  phase; resting colour is subtle abyssal blue; zero telemetry yields zero
  condition with the idle floors intact.
- **PH6** rendering at 4 viewports never mutates physiology state; raster
  deterministic (trail species rendered to steady state first).
- **PH7** pulse palette covers blue→cyan→aqua→green→lime→amber→orange with
  no purple or red stop.

Visual proof: `docs/physiology_validation/physiology_5x3.png` (15 renders,
rows = species, columns = QUIESCENT/NORMAL/STRESSED; per-cell PNGs beside
it) — states clearly differ, every row stays unmistakably the same organism.

## Live verification under Hyprland  **PASS**

Real app, real telemetry. Idle: cyan body, subtle green. 10-thread CPU load
for 50 s: lit creature pixels +68 %, warm (amber/orange) share 6.8 % →
18.9 %, concentrated along the stressed spine/tail — spatial, never a
whole-body tint (docs/shots/live-stressed-glass.png). 40 s after load:
warm 0.00 %, smooth recovery (live-recovered-glass.png). Machine
temperature was allowed to move only within ordinary bands.

## Performance re-measurement (`qa/perf_live.py`, 8 s dwell)

| layout | state | CPU % | FPS | draw ms | sim ms | tel ms | cadence |
|---|---|---|---|---|---|---|---|
| compact 600×480 | focused | 17.5 | 61.2 | 0.95 | 0.76 | 0.36 | 60 |
| compact | unfocused | 10.0 | 30.4 | 1.14 | 0.80 | 0.40 | 30 |
| instrument 900×700 | focused | **20.7** | 61.0 | 1.30 | 0.81 | 0.41 | 60 |
| instrument | unfocused | **12.2** | 30.3 | 1.64 | 0.84 | 0.42 | 30 |
| instrument | hidden | **0.2** | — | — | — | — | 0 |
| archive 1440×900 | focused | **25.5** | 60.2 | 1.75 | 0.78 | 0.38 | 60 |
| archive | unfocused | 15.7 | 29.6 | 2.59 | 0.83 | 0.38 | 30 |
| archive | hidden | **0.1** | — | — | — | — | 0 |

Against the previous envelope: canonical focused ~20 → **20.7 %**, unfocused
~12 → **12.2 %**, hidden ~0.2 → **0.1–0.2 %**, archive ~27 → **25.5 %**.
The physiology accumulators and typography cost within noise; no envelope
regression. GATE 0 torture re-run after the pass: 62 transitions, 140
resizes, 0 rebuilds, isotropy 1.1e-13 px, monotonic clock — PASS.

## Keybinding  **PASS**

`~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + A", "Abyssal Organism Monitor",
       "/home/seeno/abyssal-organism-monitor/run.sh")
```

Conflict check (`omarchy menu keybindings --print`): SUPER+ALT+A was free
(only SUPER+SHIFT+ALT+A → Grok exists). Plain exec launch — the app is
deliberately NON-UNIQUE and keeps no daemon, so single-instance logic was
deliberately not added. `hyprctl configerrors` clean.
