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
