# CURRENT STATE — Abyssal Rust Port (handoff snapshot)

STATUS:
RUST CORE HEALTHY
VISUAL PARITY RESTORED
HIDPI PATH GATED
WINIT + SOFTBUFFER HOST LANDED AND MEASURED
OPTIMIZATION STILL PAUSED

Updated 2026-09-23 after the winit + softbuffer host migration. The section
below the parity table is the GTK repair snapshot and still stands; the host
migration has its own document.

## Hosts

    ./run-rust.sh         Rust + GTK4                  CANONICAL, oracle, fallback
    ./run-rust-winit.sh   Rust + winit + softbuffer    no GTK linked
    ./run-python.sh       Python + GTK4                GOLDEN REFERENCE

The GTK host is untouched and remains canonical. See
**`WINIT-SOFTBUFFER.md`** for the full migration, its parity evidence and its
one clean performance baseline. Headline: PSS **184.9 -> 83.4 MB** (-55 %),
threads 11 -> 2, shared objects 113 -> 40, startup 0.51 -> 0.26 s; focused CPU
17.5 % -> 23.6 %, which is the GPU compositing GSK did for free now being done
on the CPU. Parity is unchanged and the 37 original tests all still pass
(39 with gtk-host, 43 with winit-host, including six new HiDPI gates).

## What was wrong, and what fixed it

Two bugs, both invisible at device scale 1.0 — which is why every headless gate
passed while the live window was malformed.

1. **`app.rs::texture` dropped the device scale on the upload copy.** A console
   cache surface is allocated at device resolution and carries
   `set_device_scale(ds, ds)`, so cairo treats it as a LOGICAL `w/ds × h/ds`
   image when it is used as a *source*. Blitting it into an unscaled surface of
   the same pixel size put the whole picture in the top-left `1/ds` of the
   texture. At the user's scale (raw 1.6 → quantised 1.5) every layer and every
   live region drew at 2/3 size inside its own texture while its ORIGIN stayed
   correct — the detached rack, floating selector, wrong chamber proportions and
   large empty areas were all this one error. The copy now goes through
   `skin::hidpi::copy_exclusive`, which preserves the device scale.
   `examples/offscreen.rs` had the same trap and uses the same helper now.

2. **`segment.rs::cell_segments` kept Python's dict-insertion order** (A, D, G,
   F, E, B, C) while `draw` looks geometry up positionally as `geo[sid]`. Every
   seven-segment glyph was permuted: `60` drew as `A2`, `13.5Gb` as `=5.9PH`.
   Advance widths were unaffected, so the existing `measure()` gate was blind to
   it. Entries are now in `SEG_A..SEG_G` order.

Typography, found while diffing: `chrome.rs::size_key` quantised the font-
description cache key to 1/4 px, so a label could be shaped with a description
built for a neighbouring size; it now keys on the truncated Pango absolute size,
which is what `chrome.py`'s exact-size key resolves to. `console.rs::show`
rounds half-to-EVEN like Python's `round()` and truncates the glyph-cache
surface size like Python's `int()`.

## Parity evidence (offscreen pair, Rust vs Python, same fixture)

| size | ds | state | mean abs | p99 | within 12 |
|---|---|---|---:|---:|---:|
| 900×700 | 1.0 | INSTRUMENT | 0.085/255 | 0 | 99.93 % |
| 900×700 | 1.5 | INSTRUMENT | 0.061 | 0 | 99.96 % |
| 900×700 | 2.0 | INSTRUMENT | 0.041 | 0 | 99.98 % |
| 1400×880 | 1.5 | ARCHIVE | 0.042 | 0 | 99.97 % |
| 600×520 | 1.5 | COMPACT | 0.015 | 1 | 100.00 % |
| 1000×420 | 1.25 | COMPACT wide | 0.011 | 0 | 100.00 % |

(The ds = 1.0 baseline before the repair was mean 0.46 / 99.63 %.)

The only residual region above ±40 is the header clock, which is wall time: two
*Python* runs two seconds apart differ in the same rectangle by the same amount.

Live: captured under Hyprland at the real fractional scale (monitor scale 1.6,
window 781×468 logical = 1249×748 device) against the Python app in the same
tiling slot. The six modules read as one console; composition, type, segment
readouts and lamps match.

## GTK reference performance baseline (one sample each, not a sweep)

Window 781×468 logical at ds 1.5, specimen 0, INSTRUMENT, `rebuilds = 0`.
CPU is utime+stime over a 6 s dwell; RSS/PSS from `smaps_rollup`.

| | Rust | Python (reference) |
|---|---:|---:|
| FPS (cadence 60) | 59.4–60.5 | 60 |
| draw, windowed mean | **0.80 ms** | 2.28 ms |
| sim per frame | 0.91 ms | 1.10 ms |
| CPU, focused | **17.8 %** | 21.2 % |
| CPU, unfocused visible (30 FPS) | **11.0 %** | 13.3 % |
| CPU, hidden/occluded (suspended) | **0.5 %** | 1.3 % |
| RSS | **188.8 MB** | 238.4 MB |
| PSS | **176.7 MB** | 222.0 MB |
| threads | **11** | 26 |

Reading note: PSS 177 MB sits well above the ~100 MB GTK framework floor in
`framework-floor.md`. The difference is application data — the organism point
arrays and the device-resolution cache surfaces (a 1172×702 ARGB layer is
3.3 MB, and there are two static layers plus up to eight cached regions). That
is the first thing to look at when optimisation resumes; it is NOT a framework
cost. No optimisation work was done in this session.

## Pointers

- Rust run: `./run-rust.sh` · Python (GOLDEN REFERENCE): `./run-python.sh`
- `cd rust && cargo test` — 39/39 green (gtk-host); `--no-default-features
  --features winit-host` — 43/43; `cargo check` 0 warnings (lib) either way
- Headless pair, now with a device scale:
  - `python3 qa/offscreen.py /tmp/py.png --width 900 --height 700 --ds 1.5`
  - `cargo run --release --example offscreen -- /tmp/rs.png --width 900 --height 700 --ds 1.5`
  - Both write at DEVICE resolution (w·ds × h·ds), so the images diff directly.
  - `cargo run --release --example compose -- /tmp/rs.png …` renders the same
    frame through the WINIT host's real composition path.
  - `python3 qa/imgdiff.py A.png B.png --map d.png` gives the parity numbers.
- Perf: `python3 qa/perf_hosts.py run-rust-winit.sh winit --dwell 8`
- `cargo run --release --example framebench` — where a frame's time goes.
- New gates that cover what ds = 1.0 cannot see:
  - `skin::hidpi::tests::copy_exclusive_preserves_device_scaled_content`
  - `parity_chrome::segment_glyphs_light_the_python_segments`
- Docs: `OPUS_VISUAL_PARITY_HANDOFF.md` (the repair brief; sections C and D are
  now historical), `phase-a/parity-spec.md`, `PORT-CHROME.md`, `PORT-CONSOLE.md`,
  `framework-floor.md`.

## Capture under Hyprland (0.56.2)

`hyprctl dispatch` in 0.56.2 evaluates its arguments as Lua, so the classic
`dispatch setfloating address:0x…` form fails. Working forms are
`dispatch hl.dsp.focus{window="address:0x…"}` and
`dispatch hl.dsp.window.float{window="address:0x…", enable=false}`. Forced
floating resizes were still not honoured reliably, so live capture reads the
real client rect from `hyprctl -j clients` and grims exactly that — and
**pixel-level parity is done offscreen**, never from a screenshot.

Working Lua forms found during the winit migration (floating resize DOES work
in this form, and the perf harness relies on it):

    hl.dsp.window.float{window="address:0x…", enable=true}
    hl.dsp.window.resize{window="address:0x…", x=W, y=H, exact=true}
    hl.dsp.window.move{window="address:0x…", workspace="N"}
    hl.dsp.window.fullscreen{window="address:0x…"}
    hl.dsp.focus{window="address:0x…"}

**Trap:** `hl.dsp.workspace.change_id` RENAMES a workspace's id; it does not
switch to one. Using it to "hide" a window silently does nothing, and a perf
harness that does so will report the unfocused figure as the hidden figure.
Assert `app_workspace != active_workspace` before dwelling. After a resize,
wait for Hyprland's resize ANIMATION to settle before grabbing a screenshot,
or the capture rect and the surface disagree and the shot shows black margins.

## Remaining known gaps

- Sub-pixel Pango shaping drift remains possible in principle; at the sizes and
  scales measured above nothing exceeds ±12 outside the clock.
- The live clock cannot be pinned from either harness, so a fully byte-exact
  cross-language frame comparison would need a clock override in both.
- Allocator and SIMD work are still deliberately untouched.
- Winit/Softbuffer is now DONE — see `WINIT-SOFTBUFFER.md`. What remains open
  there: cairo/pango are still in (measured at ~13.7 MB file-backed, so the
  case for replacing them is now a numbers question, not an ideological one),
  and dirty-rectangle composition is the named next move if the +6 points of
  focused CPU ever outweigh the 101 MB of memory it bought.
