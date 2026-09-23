# Runtime optimisation pass — winit + softbuffer host

Status: **COMPLETE AND MEASURED.** No framework changed, no renderer rewritten,
no backend experiment started. The GTK host is untouched and remains the
reference.

    ./run-rust.sh         Rust + GTK4      (reference, canonical)
    ./run-rust-winit.sh   Rust + winit + softbuffer
    ./run-python.sh       Python + GTK4    (golden reference)

## Headline

781x468 logical floating window, monitor scale 1.6 (1250x749 device),
INSTRUMENT, specimen 0, `rebuilds = 0`. CPU is utime+stime over a 10 s dwell
with the window asserted focused for the whole dwell; RSS/PSS from
`smaps_rollup`.

| | before | after |
|---|---:|---:|
| PSS | 83.4 MB | **44.6 – 47.8 MB** |
| RSS | 93.5 MB | **53.6 – 56.5 MB** |
| anonymous | 69.7 MB | **29.4 – 32.6 MB** |
| CPU, focused | 23.6 % | **15.1 – 16.0 %** |
| CPU, unfocused visible (30 FPS) | 13.4 % | **9.5 %** |
| CPU, covered by a window | 0.25 % | **0.12 – 0.25 %** |
| CPU, off-workspace | 0.12 % | **0.12 %** |
| draw, windowed mean | 2.66 ms | **1.09 – 1.29 ms** |
| FPS (cadence 60) | 60.5 | **60.0** |
| threads | 2 | **2** |
| startup to mapped window | 0.26 s | **0.21 s** |

Against the GTK reference the winit build is now **below it on memory by
140 MB** (184.9 -> ~46) and **below it on CPU** (17.5 % -> ~15.5 %), which the
migration had traded away.

## Where the time was going

`cargo run --release --example framebench -- --width 781 --height 468
--cache-ds 1.5 --target-ds 1.6`:

    compose_frame  whole    0.928 ms   (simulation, separately: 0.642 ms)
    compose_damaged age 2   0.392 ms   148174 px/frame of 936250 (15.8 %)
      of which black clear  0.055 ms
      of which organism     0.311 ms

Before this pass `compose_frame` was 1.418 ms and there was no damaged path.
The frame was recomposing **86 % of the window at 60 Hz to produce the same
bytes**: the glass is 129 740 of 936 250 device pixels, and everything outside
it is static chrome that had a black scrub and two full-window source-over
composites run across it sixty times a second.

## What was done

### 1. One opaque `base`, composed once

`DeviceLayers` holds `base` - black + `under` + `over` at the target's device
resolution, fully opaque - plus two crops of the glass rectangle
(`black+under`, and `over`) so the organism can still be sandwiched where it
belongs. A whole frame is one 1:1 opaque copy instead of a clear and two
full-window composites.

Bit-identical by construction: `base` is built by exactly the operations a
frame used to perform, in the same order, into a surface of the same format
and device scale, and a 1:1 unscaled copy of the result is a byte copy.
Gated by `the_precomposed_base_is_bit_identical_to_composing_every_frame`.

It also costs less than what it replaced: 4.70 MB against 7.14 MB for the two
separate full-window device layers.

### 2. Dirty-region composition, driven by the buffer's age

Softbuffer's Wayland backend keeps exactly two persistent shm buffers and
exposes `Buffer::age()` - how many frames ago this same buffer was last
presented, 0 when its contents are undefined. `Core::compose_damaged` takes
that age and repaints only the union of the dirty sets of that many frames:
the glass always (the organism moves), plus any region whose layer id changed,
plus the footprint a region has left. Steady state that is 15.8 % of the
buffer instead of 300 %.

`compose_frame` is the same function at age 0 and every parity harness still
drives it.

Gates: `a_damaged_frame_is_bit_identical_to_a_whole_one` (four cache/window
scale pairs, 24 frames each, byte for byte) and
`damage_covers_every_pixel_a_partial_frame_changed`, which drives two
alternating buffers exactly as softbuffer pools them.

**Trap found in softbuffer 0.4.8:** `WaylandBuffer::resize` reallocates the shm
buffer without clearing its `age`, so for one frame per pooled buffer after a
size change the reported age is a lie. The presenter distrusts the age for
three frames after every resize.

**`present_with_damage` is deliberately NOT used.** It was built, gated and
measured: 13.9 % focused CPU against 15.6 %, so it is worth 1.7 points. It is
not taken, because this compositor already mis-shows this window occasionally
(see *Known, pre-existing* below) and partial damage cannot be honestly
evaluated against a background that noisy. The partial repaint keeps the whole
CPU saving regardless: what is withheld is the hint, not the work.

## Where the memory was

`cargo run --release --example memreport` is the ownership inventory the brief
asked for. At 781x468, cache ds 1.5, headless, steady:

| owner | count | MB | lifetime |
|---|---:|---:|---|
| sprite sources, full res | 16 | **20.92** | was: process, never evicted |
| console under layers | 1 | 3.14 | until size/state/specimen change |
| console over layers | 1 | 3.14 | until size/state/specimen change |
| device frame cache | 3 | 4.70 | until layer/size change |
| console region surfaces | 3 | 1.27 | until their own inputs change |
| glyph run surfaces | 43 | 0.60 | process (bounded LRU, 700) |
| lamp/ring surfaces | 1 | 0.23 | process (bounded LRU, 8) |
| graph trace surfaces | 4 | 0.10 | until the trace changes |
| point-field accumulators | 1 | 0.30 | until evicted (LRU, 3) |
| sprite derived sizes | 11 | 0.11 | bounded LRU, 64 |
| deep-field wash discs | 1 | 0.08 | FIFO, 6 |
| organism point arrays | 1 | 0.53 | process (never freed) |

The three biggest sources are `module/shell` 1600x1172 (7.15 MB),
`module/observation` 1300x948 (4.70 MB) and `module/rack` 900x1213 (4.16 MB).

Every byte of it is allocated during the **first frame** and never returned:
`--frames 0` is 1.5 MB anonymous, `--frames 1` is 48.7 MB, `--frames 600` is
49.7 MB. Nothing leaks; everything is simply kept.

### 3. Hand the sprite sources back

A source sprite exists to be RENDERED INTO a derived size. Once a layout has
settled, what every frame blits is the derived surface - 0.11 MB of them - and
the 20.9 MB of masters behind them are dead weight that also pins an arena
that cannot otherwise be trimmed.

`skin::surface::release_unused_sources` is a **second-chance** policy, not a
flush. Three sprites (`module/header` and the two small lamps) are drawn
straight from their source rather than through the derived cache, so a flush
just makes the next second decode them again - measured as a 2.5 MB sawtooth
every 4 s with a PNG decode inside a frame each time. A source read since the
last review keeps its place; one that was not is let go.

`Core::settle` runs the review every `SETTLE_S` = 4 s from the host's idle
path and then asks glibc to return the pages. Without the trim the arena keeps
them and the process looks exactly as large as before (worth 0.9 MB with the
sources held, 10.7 MB with them released).

It changes no pixel - the file on disk is the source of truth - and that is
gated by `releasing_the_sprite_sources_changes_no_pixel`, which composes,
releases, composes again and compares byte for byte at two layouts.
`settle_gives_back_only_what_nothing_asked_for` gates the policy itself.

**The cost, stated plainly:** the next layout change decodes them again. A
resize frame already rebuilds both full-window layers and costs 56 ms; cold it
costs 161 ms. That is one hitch at the start of a drag, at most once per 4 s
of stillness, for ~30 MB held for as long as the window is open.

### 4. Drop caches that belong to a size the window has left

`--resizes` showed the caches keeping a whole spare layout: two under layers,
two over layers and three point-field accumulators where one of each was in
use - 12 MB at 781x468, and 13.7 MB *per* full-window layer at 1600x1000. The
window is one size at a time and cannot return to a size it has left without
re-resolving the layout, so those entries are dead.

`layer_under`/`layer_over` drop entries rendered for a different `(w, h, ds)`;
`FieldCache::entry` drops accumulators counted into a different buffer size.
Entries that differ only in what the machine is SHOWING at the live geometry
are kept - the behaviour word and the selected specimen do flip back and
forth, which is what the bounds are for, and a trailing specimen still finds
its trail when you switch back to it.

After walking three layouts and returning, the inventory is now identical to
the steady one. Headless PSS after three layouts: 79.6 -> 66.9 MB.

## Two experiments measured and REVERTED

Recorded rather than quietly dropped, because the result is the useful part.

**Evicting stale region surfaces.** Same reasoning as (4), applied to the
cached region surfaces. It cost 6-15 MB rather than saving any: PSS 58.9-68.1
against 52.3 holding them. Evicting regions makes them rebuild, a rebuild asks
for sprites the derived cache has let go, and that keeps the decode counter
moving, so the residency review never settles and the 20.9 MB is never handed
back. The lesson is in the interaction, not in either change alone.

**Reusing the organism's per-frame scratch buffers.** `accumulate_physio`
allocates three full-size f64 scratch vectors per frame, `paint_physio` grows
a lit-pixel index vector from empty, and `draw_organism` allocates two f32
point arrays - together about 1 MB per frame at 162x162, more at larger
windows. Holding them in the cache instead measured 15.1-16.0 % focused CPU
against 15.7-16.0 %, and 0.416 ms against 0.392 ms in `framebench`: no
material change either way, in exchange for permanently resident scratch and
`mem::take` plumbing through two hot functions. Reverted.

## Where the remaining cost is, and why

**CPU.** At 15.5 % focused, 60 FPS, one core:

| | ms/frame | points |
|---|---:|---:|
| `Core::advance` — physiology + the source equations | ~1.2–1.5 | ~8 |
| `compose_damaged` — of which the organism raster is 0.31 ms of 0.39 ms | ~1.1–1.3 | ~7 |
| event loop, 5 Hz telemetry, 2 s probe | — | ~1 |

The composition is now dominated by the organism itself: outside the glass a
frame costs 0.08 ms. What remains is the simulation and the point-field
raster, which are the equations and the bit-exact numpy-equivalent
accumulation - both explicitly out of scope, and both already Rust. Going
below ~10 % means changing the mathematics or the rasteriser, not the host.

Offscreen `framebench` numbers run roughly half of the live ones for both sim
and draw. That is not a bug: a tight loop keeps the point arrays and the
buffers in cache, while a real frame runs once every 16.7 ms on a cold one.

**Memory.** At 44.6–47.8 MB PSS: ~14.7 MB file-backed (cairo, pango, harfbuzz,
fontconfig, freetype, libwayland, the binary, the fontconfig cache),
~3.7 MB PSS of softbuffer's two shm buffers (shared with the compositor),
~12 MB of console layer and device frame surfaces, and the rest pango and
fontconfig internals plus allocator overhead.

Surfaces scale with window area, and that is inherent to CPU composition: at
1600x1000 logical / 2560x1600 device a single full-window ARGB layer is
13.7 MB and `base` is 16.4 MB, so a fullscreen window runs 80-130 MB. That is
the price of the 140 MB the GPU compositor is not being asked for.

**Cairo and Pango are not worth touching.** Together with harfbuzz, fontconfig
and freetype they are ~3.1 MB of PSS out of ~14.7 MB file-backed. The case for
replacing them was a numbers question and the numbers say no.

## Production hardening

- **No GTK.** `ldd rust/target/release/abyssal-winit` shows 40 shared objects
  and no libgtk/libgdk/libgsk/libgraphene. (`libglib` is there: pango and cairo
  need it, GTK does not come with it.)
- **Repeated launch and quit.** Eight consecutive `--quit-after` runs, all exit
  0, **zero bytes on stdout and zero on stderr**. The only writes to stderr in
  the whole library are six `eprintln!("abyssal: …")` on real error paths.
- **Soak.** Six rounds of: all six specimens by key, cycle and mode, a
  fullscreen round trip, float plus three sizes, untile, settle. PSS 80.5 ->
  82.9 MB over five rounds; a second run, 83.1 -> 83.4 over two. Threads stay
  at 2. No runaway.
- **No temporary files.** 12 open fds; the only anonymous ones are
  `memfd:softbuffer` x2 and `memfd:smithay-client-toolkit`, which are shared
  memory, not files on disk.
- **No development-only instrumentation.** The only test-only code is
  `console::pin_clock`, which is `#[cfg(test)]`, so no clock override exists in
  a shipped binary. `examples/memreport` and `examples/framebench` are
  examples, not part of either binary.
- **Layouts, scaling, fullscreen.** COMPACT (600x520), INSTRUMENT (900x700) and
  ARCHIVE (1400x880) captured live at monitor scale 1.6, each more than
  `SETTLE_S` after its resize so the frame is drawn with the sources released
  and re-decoded. All three correct. Fullscreen 1600x1000 logical /
  2560x1600 device round-trips correctly.

### Known, pre-existing

Under Hyprland 0.56.2 at fractional scale, a capture of the window taken
shortly after a float/resize/move sequence can come back with faint pixels of
the window BEHIND ours on the chassis. This was first suspected to be the
damage work and it is **not**: the pre-change binary reproduces it in the same
places, in the same sequence, and a full-surface present does not heal it.
Captured side by side. It is outside this process - screen damage tracking,
the screencopy path, or the fractional-scale viewport - and it is the reason
`present_with_damage` was left on the table.

## Parity

`examples/compose` drives the real `Core::compose_damaged` over the same
deterministic fixture as `qa/offscreen.py`.

| size | ds | state | mean abs | p99 | within 12 | exact |
|---|---|---|---:|---:|---:|---:|
| 900x700 | 1.0 | INSTRUMENT | 0.0080/255 | 0 | 99.994 % | 99.983 % |
| 900x700 | 1.5 | INSTRUMENT | 0.0000 | 0 | 100.000 % | 99.996 % |
| 900x700 | 2.0 | INSTRUMENT | 0.0232 | 0 | 99.987 % | 99.968 % |
| 1400x880 | 1.5 | ARCHIVE | 0.0262 | 0 | 99.986 % | 99.968 % |
| 600x520 | 1.5 | COMPACT | 0.0001 | 0 | 100.000 % | 99.994 % |
| 1000x420 | 1.25 | COMPACT wide | 0.0001 | 0 | 100.000 % | 99.995 % |

Every fixture whose `max` is 209-211 differs only in the header clock band;
the ones reading `max 1` are runs where the two captures fell in the same
second. Two *Python* runs two seconds apart differ in that same band by more
(mean 0.0239, max 209).

## Gates

    cargo test --no-default-features --features winit-host    48/48
    cargo test                                                45/45   (gtk-host)
    cargo check, both feature sets                            0 warnings (lib)

New, all of them asserting bytes rather than appearance:

- `host::paint_tests::the_precomposed_base_is_bit_identical_to_composing_every_frame`
- `host::paint_tests::a_damaged_frame_is_bit_identical_to_a_whole_one`
- `host::paint_tests::damage_covers_every_pixel_a_partial_frame_changed`
- `host::paint_tests::releasing_the_sprite_sources_changes_no_pixel`
- `host::paint_tests::settle_gives_back_only_what_nothing_asked_for`

No existing gate was weakened or removed.

## Tools

    cargo run --release --example memreport [--width W --height H]
        [--frames N] [--settle] [--release] [--trim] [--resizes] [--all-specimens]
        [--release-bench]
    cargo run --release --example framebench -- --width 781 --height 468
        --cache-ds 1.5 --target-ds 1.6 --n 300

`qa/perf_hosts.py` has a hole worth knowing about: its `hl.dsp.focus` can fail
silently, and the app then runs its 30 FPS unfocused cadence for the whole
"focused" dwell, reporting about 13 % instead of about 24 %. Any harness that
measures focused CPU must assert `activewindow == addr` before AND after the
dwell, and reject a sample whose FPS is not the focused cadence.
