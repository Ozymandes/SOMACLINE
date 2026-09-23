# Changelog

## v1.0.0 — unreleased

First public release of **SOMACLINE**, a computational morphology instrument.
A native Rust application on winit + softbuffer, with cairo and pango doing the
drawing and no GTK linked.

### Identity

The product is SOMACLINE. The Rust crate stays named `abyssal` internally —
that is the name every module path, golden fixture and parity gate is written
against, and renaming it would churn the tree for no outward benefit. The
instrument's own faceplate carries its model designation, `AOM-1`.

Application id `dev.somacline.Somacline`, window title `Somacline`, binary
`somacline`, installed assets under `<prefix>/share/somacline/`.

### Typography

**No font files are distributed.** Somacline's type is designed around Astro
and Microgramma, two third-party faces the project has no redistribution
rights to. It runs on fontconfig substitutions without them, and registers
them if the operator supplies them. See README, *Optional typography*.

### The instrument

- Five procedural specimens, each a published generative sketch ported
  verbatim. At rest every one reduces exactly to its source equation.
- Physiology driven by real machine telemetry from `/proc` and `/sys`,
  sampled at 5 Hz; every graph shows the last 60 seconds.
- Six generated hardware modules — shell, header, observation chamber,
  telemetry rack, selector, status rail — with everything live drawn in code
  into recesses measured off the module. Nothing on the instrument is a baked
  reading.
- Three responsive layouts: COMPACT, INSTRUMENT, ARCHIVE.
- Fractional display scaling, verified at 1.0, 1.25, 1.5, 1.6, 1.75 and 2.0.

### Performance

Measured at 781×468 logical on a 1.6-scale display (1250×749 device),
INSTRUMENT, one core, against the GTK4 build of the same renderer:

| | Rust + GTK4 | this release |
|---|---:|---:|
| PSS | 184.9 MB | **42.2–45.6 MB** |
| focused CPU | 17.5 % | **14.8–16.3 %** |
| off-workspace CPU | 0.50 % | **0.12 %** |
| draw, windowed mean | 0.65 ms | **1.09–1.29 ms** |
| threads | 11 | **2** |
| shared objects | 113 | **40** |
| startup to mapped window | 0.51 s | **0.21 s** |

The GTK draw figure measured only the cost of building a node list; its
compositing happened on the GPU, outside the process. This build composites
on the CPU and still uses less of it.

### How it gets there

- **Dirty-region composition.** The static picture is composed once into one
  opaque device-resolution surface; each frame repaints the glass and any
  region whose contents changed — measured at 15.8 % of the buffer — driven
  by the presentation buffer's age.
- **Sprite residency review.** The full-resolution sprite masters are handed
  back four seconds after the machine stops asking for them, on a
  second-chance policy, and the freed pages are returned to the OS.
- **Layout-scoped caches.** Surfaces rendered for a window size that has been
  left are dropped when the new size arrives.

Every one of these is gated by a byte-for-byte comparison against a frame
composed the straightforward way. Visual parity with the Python reference
implementation is measured at six size/scale/layout fixtures; the only region
that differs beyond tolerance is the header clock, which is wall time, and two
runs of the *reference itself* differ there by more.

### Fixed late

- **Specimen-name flicker.** Switching specimen made the engraved name
  alternate between the new specimen and the previous one at the refresh rate.
  A frame that rebuilt the static picture recorded only the glass and the
  changed regions as its dirty set; when the static layers change, every pixel
  changed, and the understatement was paid one frame later in the other pooled
  buffer. Gated now by a test that drives two alternating buffers through a
  specimen switch and requires every frame to be byte-identical to a whole one.

### Known

- Under Hyprland 0.56.2 at fractional scale, a screenshot taken shortly after
  a float/resize/move sequence can come back with pixels of the window behind
  showing on the chassis. It reproduces identically on builds predating any of
  this work and is not affected by what the application presents.
- A window resize after four seconds of stillness costs one ~160 ms frame
  instead of ~56 ms, because the sprite masters are decoded again. Deliberate.
