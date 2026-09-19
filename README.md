# ABYSSAL ORGANISM MONITOR

A living computational instrument for Linux. It renders one procedural
specimen — **PLUMIRADIA QUADRILOBATA**, *the quartered plume* — whose
physiology responds to real machine telemetry.

This is the first POC. Its purpose is to prove the foundation: window
behaviour, responsive scaling and rendering stability, under a tiling
compositor, at a **normal tile size** rather than fullscreen.

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
```

| key | |
|---|---|
| `F1` | diagnostic overlay (size, scale, viewport, FPS, sim clock) |
| `F2` | calibration geometry — circles and quadrant spokes |
| `F`  | toggle fullscreen |
| `Q` / `Esc` | quit |

## Test

```sh
python3 qa/gates.py            # GATE 1 geometry + GATE 3 performance, headless
python3 qa/torture.py --shots  # GATE 0: real Hyprland resize torture test
python3 -m abyssal.telemetry.source     # GATE 4: live telemetry
python3 -m abyssal.organism.plumiradia  # organism self-test
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
    theme.py          palette and type
  organism/
    plumiradia.py     deterministic generator + simulator (world units only)
    render.py         cairo drawing of the organism
  telemetry/
    source.py         /proc and /sys sampling, with graceful fallback
  ui/
    chrome.py         instrument text and readouts
    debug.py          F1 diagnostic overlay
qa/
  gates.py            headless geometry and performance gates
  torture.py          Hyprland resize torture harness
docs/
  ARCHITECTURE.md     why this stack, and the resize contract
  VALIDATION.md       measured results
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
