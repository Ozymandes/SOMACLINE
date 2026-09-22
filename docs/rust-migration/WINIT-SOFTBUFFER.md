# Host migration — GTK4 → winit + softbuffer

Status: **COMPLETE AND MEASURED.** The GTK host is untouched and remains the
canonical launcher, the oracle and the fallback.

    ./run-rust.sh         Rust + GTK4      (reference, canonical)
    ./run-rust-winit.sh   Rust + winit + softbuffer
    ./run-python.sh       Python + GTK4    (golden reference)

## What moved, and what did not

GTK touched exactly one file. A grep for `gtk4|gdk4|gio` across 13,031 lines
of Rust returned 40 hits, all in `src/app.rs`; the two `glib::translate` uses
in `ui/chrome.rs` and `ui/console.rs` come from pango, not GTK.

| | before | after |
|---|---|---|
| pure Rust core (signals, physiology, telemetry, mathforms, sources, species, pointfield, world, viewport, layout, theme, lighting) | — | unchanged |
| pure cairo/pango renderer (skin/\*, ui/console 2819 ll, ui/chrome, segment, selector, debug, render) | — | unchanged |
| application core | inside `app.rs` | `src/host.rs` |
| GTK adaptation | `app.rs` | `app.rs`, now a thin adapter |
| presentation boundary | GSK | `src/present.rs` |
| winit host | — | `src/winit_host.rs` |

Cairo and Pango stayed, exactly as the brief allowed. They depend on `glib`,
never on `gtk4`/`gdk4`/`gsk`/`graphene`, so removing GTK cost the renderer
nothing. **No renderer code was rewritten.**

### The feature split is load-bearing

`gtk4`/`gdk4`/`gio` are optional behind `gtk-host` (default); `winit` and
`softbuffer` behind `winit-host`. This is not tidiness. A crate-level `gtk4`
dependency maps and relocates the entire GTK/GDK/GSK stack — ~52 MB PSS
before GTK is even initialised, per `framework-floor.md` — so a single binary
carrying both hosts could never produce an honest memory number.

    binary            shared objects   libgtk/libgdk/libgsk/libgraphene
    abyssal (GTK)          113                      3
    abyssal-winit           40                      0

## What each new layer owns

**`src/present.rs` — `FramePresenter { resize, dimensions, present }`.** Three
methods, no framework. It is the seam a wgpu presenter could take later
without the machine noticing.

`SoftbufferPresenter` wraps the compositor's shared-memory buffer in a cairo
`ImageSurface` through `create_for_data_unsafe`, so the composed pixels are
never staged anywhere. **There is exactly one copy in the whole path** — the
one softbuffer makes when it hands the buffer to the compositor. Cairo's
native-endian premultiplied ARGB32 is byte-identical to softbuffer's 0RGB for
an opaque frame, which is why the composition clears to opaque black.

**`src/winit_host.rs`** owns the event loop, the window, the pacer
`GdkFrameClock` used to provide, the two timers glib used to provide
(telemetry 5 Hz, probe 2 s), and input translation. Nothing else.

## The three scales (read before touching the HiDPI path)

The previous visual-parity bug was a lost device scale. Three scales are in
play and confusing any two of them reproduces it:

1. **the window's scale factor** — 1.6 under Hyprland fractional scaling;
2. **the cache scale** — `skin::hidpi::scale()`, which QUANTISES that to
   quarter steps (1.6 → 1.5) so a fractional scale cannot churn the caches.
   Every cached layer is rendered at this scale and carries it;
3. **the target's device scale** — exactly `physical / logical`, per axis.

winit reports PHYSICAL pixels and a float scale factor where GTK reported an
integer LOGICAL size, so the conversion happens exactly once, in
`winit_host::metrics()`:

    logical = round(physical / scale_factor)     (recovers GTK's integer size)
    ds      = physical / logical                 (per axis, EXACT)

`logical * ds` therefore lands on the last physical pixel and nothing can be
cropped, stretched or fringed by a rounding disagreement. Gated by
`metrics_map_logical_onto_the_whole_buffer` at scales 1.0/1.25/1.5/1.6/1.75/2.0.

## Two deliberate differences from the GSK path

Both are forced by the absence of a GPU compositor. Neither changes a pixel.

1. **The buffer is scrubbed to opaque black first.** A presentation buffer
   comes back with undefined contents, and softbuffer discards the alpha byte,
   so a premultiplied pixel with `a < 255` would present darkened. The `under`
   layer is *not* fully opaque at every layout — measured opaque at 600×520
   and 1400×880, not at 781×468 — so this is load-bearing. Cost: 0.056 ms.

2. **Full-window layers are blitted with `Extend::Pad`.** GSK's texture
   sampler clamps; cairo's default does not, and at cache scale 1.5 into a
   1.6 window the bilinear resample reads past the last row and column and
   leaves a transparent seam (measured: corner alpha 239 instead of 255).
   Gated by `a_padded_layer_fills_the_whole_target_at_every_scale`, which was
   verified to FAIL without the pad.

Regions are the exception: they are blitted with an unbounded `paint()`, so
padding would smear a patch across the window. Gated separately by
`an_unpadded_region_stays_inside_its_rect`.

## The device-resolution layer cache

Under GTK this cache did not need to exist: GSK uploaded each cached layer
once and the GPU resampled the 1.5-scale texture into the 1.6-scale window
every frame, for free. Cairo does that resample on the CPU.

    781x468 logical, cache ds 1.5 -> window ds 1.6 (1250x749 device)
      resample under, per frame     0.974 ms
      resample over,  per frame     1.078 ms

`host::DeviceLayers` does it ONCE per layer change instead. `Layer::id` is
stable for the lifetime of its pixels, so the cache key is exact. When a layer
is already at the target's device resolution — whole-number scales, and every
headless render — nothing is cached and the original blits 1:1.

It is **bit-identical**, not an approximation: the same cairo filter at the
same scale, and compositing a layer onto a transparent surface then
compositing that is exactly source-over associativity on premultiplied alpha.
Asserted byte for byte by
`the_device_cache_is_bit_identical_to_resampling_every_frame`.

    compose_frame, 781x468 at ds 1.6:   3.58 ms  ->  1.31 ms
    cost:                               7.1 MB (two ARGB32 surfaces)

## Occlusion

winit 0.30 exposes no occlusion signal on Wayland — `Occluded` is never
emitted by its Wayland backend, and `suspended` appears nowhere in it, so the
`xdg_toplevel` SUSPENDED state GTK read is simply unavailable.

The host uses the protocol underneath instead. winit defers `RedrawRequested`
to the compositor's frame callback, and a surface nobody is looking at stops
receiving callbacks. The host therefore never runs ahead of the compositor: a
frame in flight blocks the simulation, and when the callbacks stop the loop
settles into one no-op wakeup every 250 ms. On resume, `Core::due` clamps the
dt, so the organism cannot teleport — the same contract GTK's suspended path
had.

Measured, it matches the reference: **0.25 % covered, 0.12 % off-workspace**
against GTK's 0.12 % and 0.50 %.

## Parity

`examples/compose.rs` drives the real `Core::compose_frame` — the exact
function the winit host calls every frame — over the same deterministic
fixture as `qa/offscreen.py`, and diffs against Python.

| size | ds | state | mean abs | p99 | within 12 |
|---|---|---|---:|---:|---:|
| 900×700 | 1.0 | INSTRUMENT | 0.016/255 | 0 | 99.989 % |
| 900×700 | 1.5 | INSTRUMENT | 0.024 | 0 | 99.984 % |
| 900×700 | 2.0 | INSTRUMENT | 0.039 | 0 | 99.976 % |
| 1400×880 | 1.5 | ARCHIVE | 0.009 | 0 | 99.995 % |
| 600×520 | 1.5 | COMPACT | 0.0001 | 0 | 100.000 % |
| 1000×420 | 1.25 | COMPACT wide | 0.0001 | 0 | 100.000 % |

At or better than the GTK reference's own table. Every tile that exceeds 12
is the header clock: two *Python* runs two seconds apart differ in the same
band by the same amount (mean 0.017, same tiles).

**Live**: both hosts floated to the same 900×700 window under Hyprland at
scale 1.6 and photographed. Diffing the two frames, everything that differs
is live data — the organism's sim phase, the clock, the graph traces and the
readout digits. The chassis, screws, bezels, rails, module frames, typography,
footer and selector bank are identical.

## One clean baseline

781×468 logical floating window, scale 1.6, INSTRUMENT, specimen 0,
`rebuilds = 0`, 8 s dwells. CPU is utime+stime over the dwell; RSS/PSS from
`smaps_rollup`. Both Rust hosts measured back to back in the same session at
identical geometry. The Python column is the earlier measurement from
`CURRENT_STATE.md` — a different session and window, so read it as the order
of magnitude, not a matched pair.

| | Python + GTK | Rust + GTK | **Rust + winit + softbuffer** |
|---|---:|---:|---:|
| FPS (cadence 60) | 60 | 60.1 | 60.5 |
| draw, windowed mean | 2.28 ms | **0.65 ms** | 2.66 ms |
| sim per frame | 1.10 ms | 1.25 ms | 1.20 ms |
| CPU, focused | 21.2 % | **17.5 %** | 23.6 % |
| CPU, unfocused visible (30 FPS) | 13.3 % | **10.7 %** | 13.4 % |
| CPU, covered by a window | — | **0.12 %** | 0.25 % |
| CPU, off-workspace | 1.3 % | 0.50 % | **0.12 %** |
| RSS | 238.4 MB | 194.7 MB | **93.5 MB** |
| PSS | 222.0 MB | 184.9 MB | **83.4 MB** |
| anonymous | — | 103.0 MB | **69.7 MB** |
| threads | 26 | 11 | **2** |
| startup to mapped window | — | 0.51 s | **0.26 s** |
| shared objects linked | — | 113 | **40** |

### Reading the memory number

**PSS 184.9 → 83.4 MB: −101.5 MB, −55 %.** The whole winit build now sits
*below* the ~98–102 MB PSS that `framework-floor.md` measured as the floor
for a live GTK4 window that has never drawn anything.

The saving splits in two:

- **file-backed −68 MB** (≈82 MB → ≈13.7 MB). This is what GTK/GDK/GSK and
  the GL userspace they load actually cost: 113 shared objects down to 40.
- **anonymous −33 MB** (103.0 → 69.7 MB). GSK's texture uploads and the GL
  driver's own buffers are gone; every cached layer now exists once, as the
  cairo surface the renderer already made.

**What the remaining 83 MB is.** ~13.7 MB is file-backed (cairo, pango,
fontconfig, freetype, libwayland, the binary). The other ~69.7 MB is
application data, exactly as `CURRENT_STATE.md` predicted it would be: the
organism point arrays, the console's device-resolution layer and region cache
surfaces (a 1172×702 ARGB layer is 3.3 MB, and there are two static layers
plus up to eight cached regions), and the 7.1 MB device-resolution cache this
migration added. That is application cache policy, not a framework cost, and
it is where any further work belongs.

### Reading the CPU number

**Focused CPU 17.5 % → 23.6 %, and this is the migration's real trade.** GTK
composited on the GPU: `draw_ms 0.65` measured only the cost of building a GSK
node list, and the actual compositing of two full-window layers happened on
hardware that did not appear in the process's CPU time. Softbuffer has no GPU,
so those blits are now CPU work inside `draw_ms 2.66`.

The device-resolution cache already removed the largest part of that
(`compose_frame` 3.58 → 1.31 ms at the live scale; focused CPU 35.5 % → 23.6 %
end to end). What remains is intrinsic to CPU presentation: one opaque-black
scrub, two unscaled full-window blits, the organism raster, and the live
regions, every frame.

If that 6 points of CPU ever matters more than the 101 MB of memory it bought,
the next move is dirty-rectangle composition — only the glass and the region
rects change between frames — not another backend. It was not done here
because the brief said to stop before overengineering, and because it is a
cache-policy change, which is the thing the brief deferred.

### One measurement caveat, stated plainly

An earlier draft of the perf harness used `hl.dsp.workspace.change_id` to hide
the window. In Hyprland 0.56.2 that dispatcher *renames* a workspace; it does
not switch to one, so the window was never hidden and both hosts reported
~13 %. The figures above use `hl.dsp.window.move{window=…, workspace=…}` and
assert `app_workspace != active_workspace` before dwelling. Working Lua forms
found in this session, added to the notes in `CURRENT_STATE.md`:

    hl.dsp.window.float{window="address:0x…", enable=true}
    hl.dsp.window.resize{window="address:0x…", x=W, y=H, exact=true}
    hl.dsp.window.move{window="address:0x…", workspace="N"}
    hl.dsp.window.fullscreen{window="address:0x…"}
    hl.dsp.focus{window="address:0x…"}

## Gates

    cargo test                                                39/39   (gtk-host)
    cargo test --no-default-features --features winit-host    43/43
    cargo check (both feature sets)                            0 warnings (lib)

New gates, all covering behaviour that is invisible at device scale 1.0:

- `host::paint_tests::a_padded_layer_fills_the_whole_target_at_every_scale`
- `host::paint_tests::an_unpadded_region_stays_inside_its_rect`
- `host::paint_tests::the_device_cache_is_bit_identical_to_resampling_every_frame`
- `winit_host::tests::metrics_map_logical_onto_the_whole_buffer`
- `winit_host::tests::metrics_survive_degenerate_input`
- `winit_host::tests::winit_keys_speak_gdk_names`

No existing gate was weakened or removed.

## Interaction, verified live

Driven with `wtype` and `hyprctl` against the real window at scale 1.6:
specimen selection by digit, cycle by `n`/`p`/arrows, mode by `m`, debug
overlay by `F1` (which reported `900 x 700 logical / 1440 x 1120 device /
scale 1.60 / INSTRUMENT` — correct), calibration by `F2`, pointer hover and
click hit-testing on the selector bank, resize through COMPACT (600×520) and
ARCHIVE (1400×880), fullscreen to 1600×1000 logical (2560×1600 device) and
back, and a clean exit on `q`.

## Known gaps

- **Not removed, by design:** cairo and pango. The brief said to measure first
  and they have now been measured — together with fontconfig and freetype they
  account for most of the ~13.7 MB file-backed residue. Replacing them is a
  separate decision with a separate parity cost.
- **The header clock** still cannot be pinned from either harness, so a fully
  byte-exact cross-language frame comparison would need a clock override in
  both. It is the only region above ±12 in every parity run.
- **One unreproduced test failure.** A single aggregate run reported
  31 passed / 1 failed under `winit-host` while a GTK-feature build was
  competing for the same target directory. It did not recur in five
  consecutive clean runs (43/43 each) and the failing test was not captured.
  Recorded here rather than omitted.
