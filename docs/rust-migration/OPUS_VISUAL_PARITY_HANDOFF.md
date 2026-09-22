# OPUS HANDOFF — Rust Visual Parity Repair

**Date:** 2026-09-23 · **From:** GLM-5.3 Flash session (fast-path port) · **To:** Opus
**Repo:** `/home/seeno/abyssal-organism-monitor` · **WIP commit:** `b6e0e14`

Read this together with `docs/rust-migration/CURRENT_STATE.md`.

---

## A. ORIGINAL GOAL

The Rust version must reproduce the existing **Python Abyssal Organism Monitor** with
extremely high visual and behavioral fidelity. The Python application is the
**GOLDEN REFERENCE**. Rust is not permitted to reinterpret, redesign, simplify,
approximate, or restyle the interface. Same creatures, same machine, same type,
same behavior — or it is wrong.

## B. WHAT IS ALREADY PROVEN GOOD (do not redo, do not doubt)

These are machine-verified against the Python oracle and live probes:

| Item | Evidence |
|---|---|
| Rust host launches, presents, runs | live smoke run: 60.05 FPS at cadence 60, `rebuilds=0`, clean exit (`--quit-after`), probe JSONL matches Python contract field-for-field (`/tmp/abyssal-smoke.jsonl` protocol; emitter: `rust/src/app.rs::log_probe`) |
| Telemetry sampling at 5 Hz | live run `tel_ms` ≈ 0.3; `cargo test --test parity_telemetry` |
| **Telemetry + physiology replay BIT-EXACT** | 600 steps × 10 fields, max abs err **0.0** — `rust/tests/parity_telemetry.rs` |
| Five equations + mathforms vs oracle | x/y ≤ 9.7e-4 on ±400 canvas (~2 ULP trig), w ≤ 1.05e-5, e ≤ 1.5e-7 — `rust/tests/parity_renderer.rs` |
| Anatomy (perm/u/lat/grp) bit-exact | same file, max err 0.0 |
| **Paint color-map bytes BIT-EXACT** | 120000/120000 bytes identical on both golden variants — same file |
| Chrome layer golden gates | 12/12 — `rust/tests/parity_chrome.rs` (9-slice, selector layout/hit, segment measure, sprite loads, hidpi) |
| Console machine golden gates | 10/10 — `rust/tests/parity_console.rs` (glass rects at 8 sizes ≤1e-2 px, hit-test to 1e-6, cache stability) |
| **Headless full-frame near-parity at ds=1.0** | `docs/rust-migration/artifacts/offscreen_{rust,python}_900_ds1.png`: mean |Δ| 0.46/255, p99 = 1, 99.63% of pixels within 12 — produced by `rust/examples/offscreen.rs` vs `qa/offscreen.py` |
| Framework GTK floor | ≈98–102 MB PSS; **≤20 MB impossible with GTK** — `docs/rust-migration/framework-floor.md` |
| Full crate gate state | 35/35 tests green, `cargo check` 0 warnings (before the WIP commit's instrumentation) |

Run commands: `./run-rust.sh` (builds release then execs), `./run-python.sh` (Python oracle).
Tests: `cd rust && cargo test`.

## C. WHAT IS CURRENTLY WRONG (do not minimize)

**The current integrated Rust UI is NOT visually acceptable.** The user has seen it
running and reports it badly malformed relative to the Python golden application.
Observed problems include at minimum:

- major module placement/layout mismatch between the six modules
- telemetry rack detached/overlapping instead of integrated into the machine
- selector placement/scale mismatch
- observation chamber proportions wrong
- large unused/blank regions
- overall machine composition does not read as the Python console
- scaling behavior under Hyprland (fractional scale 1.6) is incorrect
- the render reads as independently positioned pieces, not one coherent six-module console

**Contradiction to resolve:** the headless ds=1.0 render is near-pixel-perfect, yet
the live composited window is malformed. Therefore the defect is almost certainly in
the **device-scale / live compositing path**, not in the console's geometry logic.
Prime suspect (stated as hypothesis, not conclusion): a **`set_device_scale`
convention mismatch** — `rust/src/skin/hidpi.rs::surface()` sets a device scale on
every cache surface; if any drawing code then ALSO multiplies coordinates by
`hidpi::scale()` manually (or vice versa), content is double-scaled (×2.25 at ds 1.5)
or half-scaled, which is exactly "independently positioned pieces". Python's rule:
cache surfaces are allocated at device resolution with `set_device_scale(ds, ds)`
and every draw is in LOGICAL coordinates (cairo applies the scale). Audit every
surface creation + every `Context` in `rust/src/ui/console.rs`, `rust/src/ui/chrome.rs`,
`rust/src/skin/*` for which convention it uses. Also audit `app.rs::put_layer`
(texture dims ÷ scale) and the region rect snapping path.

State of the last code change (WIP): `put_layer` was switched to divide by the
**quantized** `hidpi::scale()` (matching `app.py`'s `ds = hidpi.scale()`). The user
observed the UI malformed BEFORE and AFTER this change; do not assume either variant
is correct — verify empirically.

## D. CAPTURE-HARNESS FAILURE (previous image diffs are NOT authoritative)

The recent screenshot comparison was unreliable:

- Hyprland did **not** honor `resizewindowpixel exact 900 700` on the floating window;
  observed real client sizes were **381×468** and **781×468** (tiled layout with gaps)
- `grim -g "120,120 900x700"` captured a fixed desktop REGION, not a verified window,
  so captures contained large areas of bare desktop
- therefore earlier image-difference conclusions ("top two-thirds pixel-identical",
  "selector band broken") were **invalid** — they compared mostly desktop pixels
- `/tmp/cap_one.sh` repair attempts (float → resize → verify loop) were brittle:
  Hyprland 0.56 wraps hyprctl in Lua, addresses went stale, and the resize never stuck
- observed debug values from the live app: `widget=381x468`, `widget=781x468`,
  `under_dims=1172x702` (= ceil(781×1.5)×ceil(468×1.5), so the cache math itself is
  self-consistent), `ds_raw=1.6`, `hidpi=1.5` (quarter-step quantization; also a
  transient `ds_raw=2` on first frames before the compositor settles)
- the window may also start tiled at a transient size (790×477) before settling

**Do not trust** `docs/rust-migration/artifacts/live_*_UNRELIABLE_CAPTURE.png` or
`live_diff_map_UNRELIABLE.png` as evidence of anything except harness failure.
The screenshot harness must be rebuilt cleanly (see H), not incrementally patched.

## E. SOURCE OF TRUTH (hierarchy)

1. **The current Python application itself** (`abyssal/` — run with `./run-python.sh`)
2. Existing final reference imagery (`references/`, `docs/VALIDATION.md` images,
   `docs/creature_validation/`)
3. Existing layout/component documentation (`docs/ARCHITECTURE.md`,
   `docs/rust-migration/phase-a/parity-spec.md` — the acceptance contract)
4. Existing QA/golden gates (`qa/*.py`, `rust/tests/parity_*.rs`, golden fixtures
   in `rust/tests/golden/`, generator `tools/parity_dump.py`)

Do **not** infer final layout from the malformed Rust version. When Rust and Python
disagree, Python is right by definition.

## F. DESIGN PRINCIPLE

> **HIGGSFIELD BUILDS THE MACHINE. THE RUNTIME MAKES IT ALIVE.**

The six-module hardware composition must remain visually coherent. Static generated
assets (`assets/sprites/**`, the shell/header/bezel/rack/selector/footer plates) are
hardware/chrome. Dynamic runtime rendering (organisms, telemetry readouts, graphs,
clock, lamps, active selector state, mode rocker) is what the runtime animates.
Do not recreate the visual machine procedurally — the Python app already has the
correct composition logic and assets, and the Rust port consumes the same PNGs.

## G. RECOMMENDED ORDER FOR OPUS

1. Audit Python layout/composition code: `abyssal/core/layout.py`,
   `abyssal/ui/console.py` (layer_under/layer_over/regions, device-scale handling),
   `abyssal/app.py` (`_on_snapshot`, `_texture`, `put`, `_device_scale`)
2. Audit the Rust equivalents: `rust/src/ui/console.rs` (Renderer), `rust/src/app.rs`
   (`draw_frame`, `put_layer`, `texture`), `rust/src/skin/hidpi.rs`
3. Produce a direct mapping table: Python concept/function → Rust equivalent
   (most already exists; find the divergent ones)
4. Identify where geometry/scale diverged — **audit the `set_device_scale` convention
   first** (see C); then region snapping; then layer cache keys (must include ds)
5. Fix the **layout architecture FIRST** (correct module composition at ds=1.0 in the
   live window)
6. Only after layout parity, fix scale/DPI behavior (fractional 1.6 → quantized 1.5
   path, transient scale-2 first frames)
7. Then repair telemetry/selector layering (live regions)
8. Then typography
9. Then rendering polish
10. Only then rebuild the screenshot-diff harness (see H)

**No optimization work until visual parity is restored.**

## H. CAPTURE STRATEGY

Do NOT assume Hyprland will resize a tiled/floating window to exact dimensions.
A robust capture method must either:

- launch the app under a deterministic Hyprland **window rule**
  (`windowrulev2 = float,(title matching)`, `size`, `move`) set before launch, OR
- read back the ACTUAL client geometry from `hyprctl -j clients` and grim exactly
  that rect (verify `size` before capture), OR
- **prefer deterministic offscreen rendering** for pixel-level parity:
  `rust/examples/offscreen.rs` already renders the full console+organism frame
  headless with no compositor involvement and is the mirror of `qa/offscreen.py`.
  Extend the pair (same seed/specimen/seconds/telemetry fixture) and pixel-diff —
  this is the mechanism that produced the only trustworthy comparison so far.

Compositor screenshots are integration QA, never the golden-render mechanism.

## I. NO REDESIGN RULE

- DO NOT "improve" the Rust UI.
- DO NOT reinterpret proportions.
- DO NOT create a cleaner version.
- DO NOT approximate the hardware atlas.
- DO NOT simplify the six-module structure.

The goal is:

```
PYTHON ABYSSAL  →  SAME ABYSSAL  →  RUST
```

## J. CURRENT SCREENSHOTS

- `docs/rust-migration/artifacts/live_rust_900_UNRELIABLE_CAPTURE.png` — latest live
  Rust capture; demonstrates the present integration failure (note: capture itself
  unreliable, see D; the user has separately supplied/seen a screenshot of the
  malformed live layout — inspect the running app directly rather than trusting
  prior diff reports)
- `docs/rust-migration/artifacts/offscreen_rust_900_ds1.png` /
  `offscreen_python_900_ds1.png` — the trustworthy headless pair (near parity)

## Appendix: WIP commit contents (`b6e0e14`)

- `rust/src/app.rs` (modified, contains three distinct kinds of change):
  1. **Keep:** NonExclusive-safe texture upload — cairo-rs refuses `data()` while
     any other reference exists; console cache surfaces always have one. The fix
     blits into a fresh cairo-owned surface with the `Context` scoped out, then
     reads. Without it the app **panics (SIGABRT) on the first frame** with console
     layers present (`surface data: NonExclusive`).
  2. **Verify:** `put_layer` divides by quantized `hidpi::scale()` instead of raw
     `device_scale()` (faithful to `app.py`). Untested at ds≠1; either variant may
     be wrong pending the ds-convention audit.
  3. **Strip:** `eprintln!("DBG ...")` instrumentation in `draw_frame` (frames-60
     probe of under-layer dims/ds) and the `ds_logged`/`reg_logged` Cell fields in
     `MonitorViewImp`.
- `rust/examples/` (new): `offscreen.rs` (keeper — deterministic capture),
  `region_probe.rs`, `geo_probe.rs`, `sprite_probe.rs` (diagnostic; note
  `sprite_probe` asks for the invalid name `key_00_*` — real names are `key_01_*`…
  `key_05_*`, the code is correct).
- `docs/rust-migration/artifacts/` (new, labeled reliable/unreliable).
