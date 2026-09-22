# Framework memory floor — Rust + GTK4 on this machine (fast-path measurement)

**Date:** 2026-09-22 · **Probe:** `rust/floor` (release build, gtk4-rs 0.9 / GTK 4.22, Wayland/Hyprland session).
**Method:** one 8 s dwell per stage (no sweeps, per the fast-path brief). Each stage is the same binary run with a
stage argument; after the dwell it reads `/proc/self/smaps_rollup` and prints one JSON line. A supplementary
bare-Rust control (`/tmp/trueempty`: `fn main() { sleep(8s) }`, no GTK linked) pins the language floor. CPU% is a
single `ps -o %cpu` sample at t≈6 s of the dwell — indicative only.

| stage                 | RSS MB | PSS MB | Anon MB | Swap MB | CPU% @6s |
|-----------------------|-------:|-------:|--------:|--------:|---------:|
| true empty (no GTK linked, control) | 2.2  | **0.4**   | 0.1  | 0 | 0.0 |
| empty (GTK linked, not initialized) | 58.8 | **52.0**  | 12.3 | 0 | 1.0 |
| gtk (live window, never drawn)      | 108.2| **98.2**  | 21.0 | 0 | 3.5 |
| gtk-cairo (60 fps cairo loop)       | 109.1| **98.9**  | 21.0 | 0 | 61.8 |
| gtk-cairo-pango                     | 113.4| **102.0** | 22.8 | 0 | 59.0 |
| gtk-cairo-pango-fonts (+Astro/Microgramma) | 113.7 | **102.2** | 23.3 | 0 | 59.6 |

Raw JSON (one line per stage, `rss_kb/pss_kb/anon_kb/swap_kb`):

    {"stage":"empty","dwell_s":8,"rss_kb":60184,"pss_kb":53280,"anon_kb":12636,"swap_kb":0}
    {"stage":"gtk","dwell_s":8,"rss_kb":110824,"pss_kb":100607,"anon_kb":21504,"swap_kb":0}
    {"stage":"gtk-cairo","dwell_s":8,"rss_kb":111684,"pss_kb":101268,"anon_kb":21456,"swap_kb":0}
    {"stage":"gtk-cairo-pango","dwell_s":8,"rss_kb":116100,"pss_kb":104454,"anon_kb":23392,"swap_kb":0}
    {"stage":"gtk-cairo-pango-fonts","dwell_s":8,"rss_kb":116416,"pss_kb":104620,"anon_kb":23824,"swap_kb":0}

**Reading notes (honesty):**
- The `empty` stage is the same binary as the others, so the GTK/GL shared libraries are **mapped and relocated**
  even though GTK is never initialized. It therefore measures "GTK stack resident but idle", not a bare Rust floor
  — that is what the control row is for.
- The CPU figures are the probe's own paint cost (a full-window radial gradient + two Pango layouts per frame at
  ~60 fps), which is far heavier than Abyssal's real frame (cached textures + one modest cairo node). They are a
  property of the probe, not an Abyssal CPU estimate.

## VERDICT

**(a) Minimal live-GTK framework PSS (gtk − empty): ≈ 47 MB.** Total PSS for a live, never-drawn GTK4 window on
this machine is ≈ **98–101 MB**; of that, ≈ 52 MB is the mapped/relocated-but-idle stack and ≈ 47 MB is live
initialization (fontconfig, GDK, GSK's GL renderer and the GPU userspace it loads — consistent with the Phase A
smaps finding of ~96 MB file-backed shared libs at instrument in the Python app).

**(b) What a cairo+pango render loop adds: ≈ +4 MB PSS (98.2 → 102.2 MB), and it is flat.** The per-frame cairo
loop itself adds ~0.7 MB; Pango shaping ~3.2 MB; loading the bundled display fonts ~0.2 MB. The render loop does
not meaningfully move memory. (Order-of-magnitude conclusion of the brief: confirmed — these are small,
one-digit-MB additions.)

**(c) Is ≤20 MB total PSS plausible with GTK4? NO — not remotely.** The floor is a hard bound: a live GTK window
costs ≈ 98 MB PSS before the app allocates a single buffer, and even the *uninitialized but linked* stack is
≈ 52 MB. The ≤20 MB target fails the framework floor by ~5×; ≤32 MB and ≤50 MB fail it too. The achievable floor
with GTK4 on this machine is ≈ **100 MB total PSS**, and an actual Abyssal build should land ≈ 100–110 MB
(framework ≈ 98 MB + ≈ 5 MB data-model floor + render buffers, i.e. still well under the Python app's 278–359 MB
RSS).

**(d) Implication for the port, one sentence:** build and optimize the Rust+GTK app exactly as planned — parity
and CPU are where the real wins are (Rust replaces the interpreter/NumPy hot path), but treat ~100 MB PSS as the
GTK-imposed floor and note that any target below ~100 MB total is a *backend* decision (lightweight Wayland), not
an application-optimization one, which per the fast-path brief is only to be revisited after the GTK version
exists and has been profiled.
