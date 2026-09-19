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
