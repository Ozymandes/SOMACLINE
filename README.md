# ABYSSAL ORGANISM MONITOR

A biocomputational observation console for Linux. Five procedural specimens
live behind its glass, each a different set of point equations, and their
physiology responds to real machine telemetry.

The console is manufactured from six generated hardware modules - shell,
header, observation chamber, telemetry rack, selector, status rail - matched
to `references/Abbysal_Final.png` and decomposed per
`references/Abbysal_Module_Map.png`. The modules are the metal; everything
that changes is drawn live, in code, into a recess measured off the module.
Nothing on the machine is a baked reading.

## The catalogue

Each specimen is a published generative sketch — a compact p5.js program —
ported verbatim to NumPy. The equation on screen is the equation in the
source; see `docs/CREATURE_EQUATIONS.md`.

| # | Specimen | Source | Form | Telemetry reads as |
|---|---|---|---|---|
| 01 | PLUMARIA SIGMATA | s01 | sigmoid plume | a ripple down the body |
| 02 | DIPLOSOMA CONIUGATA | s02 | two coupled bodies | I/O crossing between them |
| 03 | SYMMETRA ROSTRATA | s03 | mirrored alien | heat breaking the mirror plane |
| 04 | QUADRIPLUMA ARTICULATA | s04 | four separate plumes | load desynchronising the four |
| 05 | PENNARIA SOLITARIA | s05 | single feather | a whip growing toward the tip |

At rest every specimen reduces **exactly** to its published equation;
`qa/console_gates.py` GATE 4 asserts it. Physiology shapes the creature, it
never replaces it.

## Requirements

Arch / Omarchy (or any distro with these):

```sh
sudo pacman -S --needed python gtk4 python-gobject python-cairo python-numpy
```

Everything else is stdlib. No virtualenv needed, no build step, no
third-party Python packages. Telemetry reads `/proc` and `/sys` directly.

## Run

```sh
./run.sh                         # 900x700, the canonical tile
./run.sh --debug                 # with the diagnostic overlay on
./run.sh --width 1400 --height 860
./run.sh --qa-temp 86             # QA: substitute the temperature reading
./run.sh --fps-cap 30             # QA: force a draw rate (default adaptive)
```

The monitor draws at up to 60 FPS while focused, 30 while visible but
unfocused, and not at all on another workspace. Telemetry is sampled at 5 Hz;
every graph shows the last 60 seconds.

| key | |
|---|---|
| `1`–`5` | select a specimen (or click its engraved key) |
| `←` `→` | previous / next specimen |
| `m` | cycle the mode key |
| `F1` | diagnostic overlay (size, scale, viewport, FPS, sim clock) |
| `F2` | calibration geometry — circles and quadrant spokes |
| `F`  | toggle fullscreen |
| `Q` / `Esc` | quit |

## Test

```sh
python3 qa/gates.py              # GATE 1 geometry + GATE 3 performance
python3 qa/console_gates.py      # GATES 4-8 species, skin, selector, cache
python3 qa/production_gates.py   # P1-P12 six-module assets, alpha, seating, cadence
python3 qa/compare.py OUT        # side-by-side + module deltas vs the reference
python3 qa/perf_live.py          # real CPU / RSS / FPS, focused/unfocused/hidden
python3 qa/shots.py              # real-window screenshots (docs/shots)
python3 qa/torture.py --shots    # GATE 0: real Hyprland resize torture test
python3 qa/offscreen.py out.png --width 1400 --height 880
python3 qa/specimen_sheet.py catalogue.png
python3 qa/creature_validate.py  # each equation in its own 400x400 canvas
python3 -m abyssal.telemetry.source      # live telemetry
```

`qa/torture.py` drives the real window through tile / resize / fullscreen /
float / retile cycles on a scratch workspace and then asserts, from the app's
own probe log, that the simulation never reset, nothing was rebuilt, geometry
stayed isotropic and nothing clipped.

## Layout

```
abyssal/
  app.py              host: window, frame loop, input, probe
  core/
    world.py          the 1000x1000 logical world
    viewport.py       the ONLY world -> pixel transform
    layout.py         responsive states (COMPACT / INSTRUMENT / ARCHIVE)
    signals.py        Telemetry and Physiology value types
    physiology.py     Telemetry -> Physiology, smoothed
    lighting.py       restrained spill from lit displays
    theme.py          palette and type
  organism/
    sources.py        the five source equations, verbatim + NumPy port
    mathforms.py      specimens: source clock, world seat, physiology
    pointfield.py     accumulating point-field rasteriser
    species.py        the catalogue: source + archive metadata
    render.py         cairo drawing of any specimen
  telemetry/
    source.py         /proc and /sys sampling, with graceful fallback
    history.py        bounded 60 s rings for the graphs
  skin/
    modules.py        THE SIX MODULES: bays, stretch bands, fasteners
    hidpi.py          device-scale surfaces for every cache
    surface.py        sprite loading, 9-slice, bounded surface cache
    catalog.py        approved parts: keys, lamps, rocker, mode key
    fascia.py         earlier bay-mapped panels (compact fallbacks)
  ui/
    console.py        the six modules + live content; static layers, regions
    selector.py       the five engraved specimen keys
    segment.py        procedural seven-segment display, with units
    chrome.py         text primitives and the background
    debug.py          F1 diagnostic overlay
qa/
  gates.py            headless geometry and performance gates
  console_gates.py    species, skin, selector, switching, static layer
  offscreen.py        headless single-frame capture
  specimen_sheet.py   the catalogue as one contact sheet
  torture.py          Hyprland resize torture harness
  shots.py            product screenshots from the real app
tools/
  build_modules.py    masters -> runtime modules (alpha, crop, trueing)
  module_qa.py        alpha / aperture / recess measurement of one PNG
assets/modules/
  masters/            the six accepted 2K generations (source of truth)
  GENERATIONS.md      every Higgsfield generation, settings, references
  INVENTORY.md        the six modules: size, alpha, apertures, seating
docs/
  ARCHITECTURE.md     why this stack, the resize contract, composition
  VALIDATION.md       measured results
  shots/              real-window screenshots + reference comparison
```

See `docs/ARCHITECTURE.md` for the resize contract and why the stack was
chosen.

## Notes for Omarchy / Hyprland

**Window translucency.** Omarchy applies `opacity = "0.985 0.96"` to every
window (`/usr/share/omarchy/default/hypr/windows.lua`). Against a near-black
app the wallpaper shows faintly through. That is the desktop's setting, not
this app. To make the abyss truly opaque, add to your Hyprland config:

```lua
o.window("dev.abyssal.OrganismMonitor", { tag = "-default-opacity", opacity = "1 1" })
```

**hyprctl is Lua here.** Hyprland 0.56 on Omarchy fronts `hyprctl dispatch`
(and the IPC socket) with a Lua dispatcher API. The classic string form is a
syntax error:

```sh
hyprctl dispatch resizeactive exact 900 700                      # FAILS
hyprctl dispatch "hl.dsp.window.resize({ x = 900, y = 700, exact = true })"   # works
```

`qa/torture.py` documents the forms it needs (`hl.dsp.window.float`,
`.fullscreen`, `.resize` with `relative = true`, `hl.dsp.focus`).
