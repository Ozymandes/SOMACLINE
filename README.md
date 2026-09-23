<div align="center">

# ABYSSAL ORGANISM MONITOR

**A biocomputational observation console for Linux.**
Five procedural specimens live behind its glass. Their physiology is your machine's.

<img src="docs/shots/instrument.png" alt="The console in its INSTRUMENT layout" width="860">

`Rust` · `winit + softbuffer` · `cairo + pango` · `no GPU` · `no GTK` · `2 threads` · `~46 MB`

</div>

---

## What it is

A fictional laboratory instrument that monitors specimens that do not exist,
using telemetry that does.

The console is manufactured from six generated hardware modules — shell,
header, observation chamber, telemetry rack, selector, status rail. The modules
are the metal. Everything that changes is drawn live, in code, into recesses
measured off the module: the seven-segment readouts, the sixty-second graphs,
the lamps, the engraved specimen keys, the clock. **Nothing on the machine is a
baked reading.**

Behind the glass, a specimen. Each one is a published generative sketch — a
compact p5.js program — ported verbatim, first to NumPy and then to Rust. The
equation on the screen is the equation in the source. At rest every specimen
reduces *exactly* to its published form; a parity gate asserts it.

Then the machine reads `/proc` and `/sys`, and the specimen responds. CPU load
becomes a ripple travelling down the body. Thermal pressure breaks a mirror
plane, or warms a pulse from cyan toward amber. Memory pressure crowds the
field. Physiology shapes the creature. It never replaces it.

## The catalogue

| # | Specimen | Form | Your machine reads as |
|---|---|---|---|
| 01 | **PLUMARIA SIGMATA** | a sigmoid plume | a ripple down the body |
| 02 | **DIPLOSOMA CONIUGATA** | two coupled bodies | I/O crossing between them |
| 03 | **SYMMETRA ROSTRATA** | a mirrored alien | heat breaking the mirror plane |
| 04 | **QUADRIPLUMA ARTICULATA** | four separate plumes | load desynchronising the four |
| 05 | **PENNARIA SOLITARIA** | a single feather | a whip growing toward the tip |

<div align="center">
<img src="docs/shots/specimen-01.png" alt="Specimen 01" width="420">
<img src="docs/shots/specimen-03.png" alt="Specimen 03" width="420">
</div>

## Features

- **Five specimens**, each a faithful port of its source equation, selectable
  from the engraved keys or the keyboard.
- **Live physiology** from real CPU, thermal, memory and I/O telemetry,
  sampled at 5 Hz, with every graph showing the last sixty seconds.
- **Three responsive layouts** — COMPACT, INSTRUMENT, ARCHIVE — that rebuild
  nothing when the window resizes.
- **Fractional display scaling**, gated at 1.0, 1.25, 1.5, 1.6, 1.75 and 2.0.
- **Adaptive frame cadence**: 60 FPS focused, 30 visible but unfocused, and
  nothing at all when the compositor stops asking — measured at 0.12 % of one
  core on another workspace.
- **Tiny.** ~46 MB resident, two threads, forty shared objects, no GPU driver
  loaded, and a window on screen 0.21 s after launch.

## Screenshots

| | |
|---|---|
| ![COMPACT](docs/shots/compact.png) | ![ARCHIVE](docs/shots/archive.png) |
| **COMPACT** — the specimen and a condensed readout | **ARCHIVE** — the full six-module machine |
| ![Telemetry rack](docs/shots/closeup-telemetry.png) | ![Selector](docs/shots/closeup-selector.png) |
| **The rack** — segment readouts and 60 s graphs, drawn per frame | **The bank** — five engraved keys, mode and cycle |

## Install

Requires cairo, pango and fontconfig — all of which a desktop Linux system
already has. Building needs a Rust toolchain. Developed and verified on
Wayland (Hyprland); winit's X11 backend is compiled in but untested here.

```sh
git clone <this repository>
cd abyssal-organism-monitor
./install.sh                       # builds, installs into ~/.local
```

That gives you `abyssal-monitor` on your `PATH`, a desktop entry and an icon.

```sh
./install.sh --prefix /usr/local   # system-wide
./install.sh --uninstall           # take it back out
DESTDIR=/tmp/pkg ./install.sh --prefix /usr    # stage for a package
```

On Arch and derivatives the runtime dependencies are:

```sh
sudo pacman -S --needed cairo pango fontconfig
```

To run from a checkout without installing anything:

```sh
./run-rust-winit.sh                # builds on demand, runs in place
```

## Usage

```sh
abyssal-monitor                              # 900x700
abyssal-monitor --width 1400 --height 880    # ARCHIVE
abyssal-monitor --specimen 2                 # open on a specimen (0-4)
abyssal-monitor --debug                      # with the diagnostic overlay
```

### Controls

| key | |
|---|---|
| <kbd>1</kbd>–<kbd>5</kbd> | select a specimen — or click its engraved key |
| <kbd>←</kbd> <kbd>→</kbd> <kbd>↑</kbd> <kbd>↓</kbd>, <kbd>n</kbd> <kbd>p</kbd> | previous / next specimen |
| <kbd>m</kbd> | cycle the mode key |
| <kbd>F1</kbd> | diagnostic overlay — size, scale, viewport, FPS, sim clock |
| <kbd>F2</kbd> | calibration geometry — world circles and quadrant spokes |
| <kbd>F11</kbd> | fullscreen |
| <kbd>q</kbd> / <kbd>Esc</kbd> | quit |

## How it is built

```
  telemetry  /proc, /sys, 5 Hz ──▶ physiology ──▶ specimen equations
                                                        │
  six hardware modules ──▶ static layers ──┐             │
                                           ▼             ▼
                              one opaque composed base   organism raster
                                           └──────┬──────┘
                                                  ▼
                                      dirty-region composition
                                                  ▼
                              cairo ──▶ shared-memory buffer ──▶ compositor
```

The renderer knows nothing about windows. It produces a frame in logical
coordinates; a `FramePresenter` — three methods, no framework — puts it on a
screen. That seam is why the same machine runs unchanged on two hosts: the
GTK4 build kept as a reference oracle, and this one.

There is no GPU in the path. Cairo composes straight into the compositor's
shared-memory buffer, so the frame is copied exactly once — by softbuffer,
when it hands the buffer over.

Three scales are in play and confusing any two of them is a whole bug class:
the window's scale factor, the quantised cache scale every cached surface is
rendered at, and the target's exact per-axis device scale. They are reconciled
in one function, gated at six scales.

**Read next:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the resize
contract and the composition model, and
[`docs/rust-migration/OPTIMIZATION.md`](docs/rust-migration/OPTIMIZATION.md)
for where every byte and every microsecond went.

## Performance

781×468 logical on a 1.6-scale display (1250×749 device), INSTRUMENT, one
core. The GTK4 column is the same renderer on the same machine.

| | Python + GTK4 | Rust + GTK4 | **this release** |
|---|---:|---:|---:|
| PSS | 222.0 MB | 184.9 MB | **44.6 – 47.8 MB** |
| RSS | 238.4 MB | 194.7 MB | **53.6 – 56.5 MB** |
| CPU, focused | 21.2 % | 17.5 % | **15.1 – 16.0 %** |
| CPU, unfocused | 13.3 % | 10.7 % | **9.5 %** |
| CPU, off-workspace | 1.3 % | 0.50 % | **0.12 %** |
| threads | 26 | 11 | **2** |
| shared objects | — | 113 | **40** |
| startup to mapped window | — | 0.51 s | **0.21 s** |

GTK composited two full-window layers on the GPU, outside the process, for
free. This build does that work on the CPU and still uses less of it, because
it stopped doing most of it: the static picture is composed once, and a frame
repaints the glass and whatever region actually changed — measured at 15.8 %
of the buffer.

Every optimisation is gated by a byte-for-byte comparison against a frame
composed the straightforward way. None of them is allowed to be an
approximation.

## On making it

The console was built against two fixed references: a target image and a
module map. The hardware was generated, then cut into a nine-sliceable sprite
set by `tools/build_sprites.py`; the generation log — every prompt, setting
and reference — is in `assets/modules/GENERATIONS.md`.

It was written twice. The Python + GTK4 implementation came first and still
lives in `abyssal/`; it is not a second product but the **parity oracle**. The
Rust port is diffed against it at six size/scale/layout fixtures, and the only
region that exceeds tolerance is the header clock, which is wall time — two
runs of the *reference itself* differ there by more.

That oracle is why the optimisation work could be aggressive. When a frame can
be proved byte-identical to a frame drawn the obvious way, the obvious way
stops being the only safe one.

## Development

```sh
cd rust
cargo test --no-default-features --features winit-host   # 49 gates
cargo test                                               # the GTK reference, 46

cargo run --release --example framebench -- --width 781 --height 468 \
    --cache-ds 1.5 --target-ds 1.6        # where a frame's time goes
cargo run --release --example memreport   # where every resident byte is
```

Parity against the reference, at a real device scale:

```sh
python3 qa/offscreen.py /tmp/py.png --width 900 --height 700 --ds 1.5
cargo run --release --example compose -- /tmp/rs.png --width 900 --height 700 --ds 1.5
python3 qa/imgdiff.py /tmp/rs.png /tmp/py.png
```

`docs/ASSETS.md` records what ships and what is source material.

## Licence

**Not yet settled — see [`LICENSING.md`](LICENSING.md) before republishing.**
There is no licence file, and the two bundled display typefaces have no stated
provenance. Both need a decision from a human.

## Acknowledgements

The five specimens are ports of published generative sketches; each source is
reproduced verbatim alongside its port in `rust/src/sources.rs`. The hardware
skin was generated with an image model against a fixed reference, and the
provenance of every module is logged in `assets/modules/GENERATIONS.md`.
