# Phase A — CPU profile: where the Python app spends its time

Lane: `cpu-profile` · Project: Abyssal Organism Monitor (`/home/seeno/abyssal-organism-monitor`)
Date: 2026-07-20 (session-local analysis)

## Summary

The app's steady-state CPU at the canonical tile is already low: the most recent
recorded live run shows **20.7 % of one core focused / 12.2 % unfocused / 0.1–0.2 %
hidden** at 60/30/0 fps (`docs/VALIDATION.md`, "Final production pass",
`qa/perf_live.py`). The frame is composed of **cached GSK textures** (static layers,
live regions) plus **one per-frame cairo node** (the organism), so per-frame Python
work is: simulation (~0.8 ms), snapshot staging (~1.0–1.8 ms), and an
**unattributed ~1.1–1.7 ms/frame** block (GSK composite + presentation + GLib
dispatch + interpreter overhead) that the app's own probe does not cover. The
realistic Rust upside at the canonical tile is therefore ~1.5–2.5 ms of CPU per
frame (interpreter dispatch, NumPy temporaries/fusion, region-rebuild Python tax),
not an order of magnitude; the largest remaining absolute cost (Cairo raster +
surface upload at large windows) is intrinsic C and moves only via GPU, which is a
renderer decision, not a language one.

## Evidence integrity — read this first

- **Executed in this session: nothing.** This lane's toolset has file read/write
  and web tools but **no shell/execution tool**. I could not run
  `qa/perf_live.py`, `perf`, or `py-spy`, and could not even check whether they
  are installed. Per the honesty contract, **no number below is presented as a
  measurement taken by this session**.
- **FACTS** below are static code reading, cited `file:line` (≈L marks
  approximate line numbers from my reads).
- **MEASUREMENTS (recorded)** are numbers previously committed to the repo in
  `docs/VALIDATION.md` with stated provenance (machine: Ryzen 7 5800H, Hyprland
  0.56, 2560×1600 @ 1.6 scale, 120 Hz). They are evidence of what the harness
  produced on this machine, **not re-run here**; freshness is good (same
  revision tree) but they are second-hand for this lane.
- **INFERENCES** are my arithmetic/interpretation and are labelled.
- A ready-to-run protocol (commands + a drop-in stage-timer patch) is embedded in
  "Missing evidence" so the integrator can produce the first-party numbers.

## Findings

### F1 — What the app's own probe instruments (the three stage timers)

**Claim:** The app times exactly three things: telemetry sample, simulation tick,
and snapshot staging. Everything GTK does around them is uninstrumented.
**Sources:** `abyssal/app.py` — `TELEMETRY_HZ = 5.0` (≈L56); `_on_telemetry`
wraps `telemetry_src.sample()` + `history.push` in `perf_counter` → `_tel_ms`
(≈L316–330); `_on_tick` paces cadence and wraps `phys_model.update` +
`org.update` → `_sim_ms` (≈L425–447); `_on_snapshot` wraps layout resolve →
region list → `_draw_ms` (≈L457–520); probe JSONL every 2000 ms
(`GLib.timeout_add(2000, self._on_probe_sample)`, ≈L197).
**Support:** direct evidence. **Confidence:** high.

Timer boundaries, precisely:
- `_sim_ms` = PhysiologyModel EMAs + one full NumPy re-solve of the specimen
  (`SourceBody.update`, `abyssal/organism/mathforms.py` ≈L284, "re-solve.
  Allocation-free" into preallocated arrays).
- `_draw_ms` = `resolve()` layout + `console.layer_under/layer_over` (cache
  hits) + **the organism raster** (`append_cairo` → `draw_organism`, the only
  per-frame cairo node) + `console.regions()` (cache lookups) + `put()` texture
  hand-offs (uploads only when a surface's pixels changed,
  `_texture()` ≈L443–452, bounded LRU of 8).
- `_tel_ms` = /proc/stat + /proc/meminfo + /proc/diskstats + /proc/net/dev +
  hwmon temp parse (`abyssal/telemetry/source.py`, `TelemetrySource`).
- **Not covered by any timer:** GSK render/composite of the frame, texture
  uploads that do happen, presentation, frame-clock dispatch, probe I/O,
  interpreter overhead inside the callbacks is *inside* the timers only for
  Python-side work — C-side GSK work after `do_snapshot` returns is outside.

### F2 — Frame composition: static textures + one live cairo node + change-driven regions

**Claim:** Per frame, the only rasterised pixels are the organism; everything
else is a cached texture handed to GSK, re-rendered only when its own inputs
change (rack at 5 Hz telemetry, clock 1 Hz, keys on input).
**Sources:** `abyssal/app.py` `MonitorView.do_snapshot` docstring (≈L98–107) and
`_on_snapshot`; `abyssal/ui/console.py` composition comment (≈L1738–1754),
`layer_under`/`layer_over` (≈L1776–1798), `_region`/`regions` (≈L1806–1900),
`_layer_key` (console.py:1710); region cache keyed on content version —
`Ring.version` bumps per push (`abyssal/telemetry/history.py` ≈L21) and
`_rack_trace` re-renders only on `ring.version` change (console.py ≈L1357–1362).
**Support:** direct evidence. **Confidence:** high.

### F3 — Recorded live envelope (prior run, `qa/perf_live.py`, 8 s dwell per state)

**Claim:** CPU % of one core (utime+stime over the dwell), FPS, and the app's
windowed-mean snapshot time, per layout/state (`qa/perf_live.py:41–60` computes
CPU from `/proc/<pid>/stat`; probe medians over ~4 samples per dwell):

| layout | state | CPU % | FPS | draw ms | sim ms | tel ms |
|---|---|---|---|---|---|---|
| compact 600×480 | focused | 17.5 | 61.2 | 0.95 | 0.76 | 0.36 |
| compact | unfocused | 10.0 | 30.4 | 1.14 | 0.80 | 0.40 |
| instrument 900×700 | focused | **20.7** | 61.0 | 1.30 | 0.81 | 0.41 |
| instrument | unfocused | **12.2** | 30.3 | 1.64 | 0.84 | 0.42 |
| instrument | hidden | **0.2** | 0 | – | – | – |
| archive 1440×900 | focused | **25.5** | 60.2 | 1.75 | 0.78 | 0.38 |
| archive | unfocused | 15.7 | 29.6 | 2.59 | 0.83 | 0.38 |
| archive | hidden | 0.1 | 0 | – | – | – |

**Sources:** `docs/VALIDATION.md`, "Final production pass → Performance
re-measurement (`qa/perf_live.py`, 8 s dwell)". An earlier recorded run (10 s
dwell) agrees within noise: 20.1 / 11.7 / 0.2 (instrument) and 26.8 / 16.5 /
0.9 (archive). **Support:** measurement, recorded — provenance
`docs/VALIDATION.md`; **not re-run this session**. **Confidence:** high for the
envelope; **medium** for the probe medians (each dwell yields only ~4 probe
rows; `perf_live.py` takes the median of `draw_avg_ms`/`sim_ms`/`tel_ms` over
those rows).

### F4 — Recorded headless stage split (`qa/gates.py` GATE 3, warm caches, n=120)

| size | sim | organism | console | total |
|---|---|---|---|---|
| 900×700 | 0.26 ms | 0.66 ms | 0.37 ms | **1.29 ms** |
| 1400×860 | 0.26 ms | 0.78 ms | 0.74 ms | 1.79 ms |
| 1920×1080 | 0.27 ms | 1.15 ms | 1.49 ms | 2.91 ms |
| 2560×1600 | 0.28 ms | 2.54 ms | 3.39 ms | 6.20 ms |

**Sources:** `docs/VALIDATION.md` ("Headless frame cost, steady state");
method at `qa/gates.py` `gate3_performance` (≈L126–180): separate tight loops
for `org.update`, `draw_organism`, and warm-cache `draw_under`+`draw_over`.
Earlier recorded passes show the same measurement before the caching work:
900×700 total 4.20 ms, 2560×1600 15.84 ms — i.e. the console side improved
~4–8× via the static-layer/region/text caches. **Support:** measurement,
recorded. **Confidence:** high (large sample, warm loops; methodology read
directly from `qa/gates.py`).

### F5 — CPU % → ms/frame reconciliation, and the unattributed block

**Claim (INFERENCE):** Converting recorded CPU % to per-frame CPU time and
subtracting the probe's own timers leaves a consistent **~1.1–1.7 ms/frame**
unattributed block (GTK/GSK render of the frame, texture uploads on change,
GLib/frame-clock dispatch, PyGObject marshalling, interpreter overhead outside
the timed regions):

- instrument focused: 20.7 % × 1000 / 61.0 fps = **3.39 ms CPU/frame**;
  probe sum = 1.30 (draw) + 0.81 (sim) + 0.41×(5/61) (tel amortised) ≈
  **2.14 ms (63 %)** → unattributed ≈ **1.25 ms**.
- archive focused: 25.5 % / 60.2 fps = **4.24 ms/frame**; probe sum ≈ 1.75 +
  0.78 + 0.03 ≈ **2.56 ms (60 %)** → unattributed ≈ **1.68 ms**, of which
  ~0.5 ms/frame is the amortised rack-region rebuild (recorded ~6 ms per
  rebuild at 1440×900 @1.6×, 5 Hz → 30 ms CPU/s ÷ 60 fps).
- compact focused: 17.5 % / 61.2 = 2.86 ms/frame; probe sum ≈ 1.74 →
  unattributed ≈ **1.12 ms**.

**Support:** researcher inference from recorded numbers (F3) + recorded rack
rebuild cost (`docs/VALIDATION.md`: "A rack region rebuild … costs ~6 ms at
1440×900 @1.6×, five times a second"). **Confidence:** medium — arithmetic is
exact, but the split *inside* the unattributed block (GSK vs upload vs dispatch
vs interpreter) is not measured anywhere in the repo.

### F6 — Telemetry is negligible; the hidden-state floor cross-checks it

**Claim:** Telemetry at 5 Hz costs ~0.2 % of a core and is effectively the whole
hidden-state CPU.
**Sources:** recorded `tel_ms` 0.36–0.42 ms/sample (F3); recorded GATE 4:
"cost: 78 µs/sample, pure stdlib" (`docs/VALIDATION.md`; note the 78 µs
predates the diskstats/netdev channels — the live 0.4 ms includes
`/proc/diskstats` + `/proc/net/dev` line loops,
`abyssal/telemetry/source.py` `_sample_io`, ≈L180–231).
**INFERIENCE (inference):** 5 Hz × 0.41 ms ≈ 2.05 ms CPU/s ≈ **0.21 % of a
core**, which matches the recorded hidden-state CPU of 0.1–0.2 % (F3) almost
exactly — consistent with `_on_tick` returning early at cadence 0 (app.py
`cadence()`/`_suspended()`, ≈L372–393) leaving only the 5 Hz GLib timeout and
the idle main loop. **Confidence:** high for magnitude; the 78 µs vs 410 µs
spread across revisions is recorded but unexplained in-repo (likely the added
I/O channels).

### F7 — Font/text: Pango runs at cache-miss rate, not frame rate

**Claim:** Steady-state per-frame text cost is ≈ 0. All text renders through a
rendered-glyph-run cache (`_show`, `abyssal/ui/console.py` ≈L75–121, LRU 700,
keyed on text/size/weight/tracking/colour/alpha/align/max_w/family/device
scale); static type is baked into the cached `over` layer; live numerics live
inside change-driven regions. Pango layout happens only on a miss; font
descriptions/cap heights are cached per (size, weight, family)
(`abyssal/ui/chrome.py` module docstring, ≈L8–19). Numerals use the procedural
seven-segment renderer (`abyssal/ui/segment.py`), not Pango.
**Sources:** direct evidence + recorded note that before this cache "shaping
and rasterising [~140 strings] every time was the single largest cost in the
draw" (console.py `_show` docstring) and VALIDATION's typography pass
("No font face is created per frame"). **Confidence:** high.

### F8 — Per-stage attribution and interpreter-vs-intrinsic classification

The table merges F1–F7. "Stage cost" ranges cite the recorded measurements;
the classification is inference from code reading (which library does the
looping) unless noted.

| stage | runs at | stage cost (recorded) | dominant implementation | Python-interpreter share | realistic Rust upside |
|---|---|---|---|---|---|
| Telemetry reads | 5 Hz | 0.36–0.42 ms/sample (live probe); 78 µs/sample (older GATE 4) | Python stdlib parse (interpreter) | **~all** (only `read()` is C) | 5–10× on the stage, but stage is 0.2 % of a core → **irrelevant** |
| Physiology smoother | 60 Hz | «1 µs-scale scalar EMAs (part of recorded sim 0.26–0.84 ms) | pure Python scalars | **~all**, but tiny absolute | none needed |
| Organism math (equation + pulse + perturb) | 60 Hz | 0.26–0.28 ms headless, size-independent; 0.76–0.84 ms live single-shot | NumPy vectorised float32 over 10k–40k pts (`sources.py` SOURCES ≈L277: s01=10000, s02=20000, s03=40000, s04=20000, s05=10000) | low–moderate: ~50–100 NumPy dispatches/frame, fresh temporaries per `sample()` | **2–4×** (fuse passes, zero alloc, explicit SIMD); absolute ≤ ~0.5 ms/frame |
| Point-field accumulate | 60 Hz | inside "organism" 0.66→2.54 ms (420→2560 logical) | `np.bincount` ×3 (density/excite/hue) over one index set (`pointfield.py` `accumulate_physio` ≈L106–145) | low (few dispatches; C loops) | **2–3×**: one fused scatter for all three channels instead of three bincounts; absolute ~0.2–0.8 ms |
| Colormap → ARGB32 → cairo paint | 60 Hz | inside "organism"; lit-pixels-only (`paint_physio` ≈L157–259), field capped at MAX_FIELD_PX=760 (`render.py` ≈L38) | NumPy ufuncs (exp/pow over lit px) + `cairo.paint` (C) + fresh zeroed backing per frame | low | **~2×** (fused pack, no zero-fill, no temporaries); absolute ~0.3–1 ms at large sizes |
| Cairo raster/paint of the frame | 60 Hz | the part of "organism"+"console" that scales with device pixels; recorded: ANTIALIAS_FAST −16 %; stroke batching made it *worse* (9.3→14.9 ms) | Cairo/pixman (intrinsic C) | ~0 | **≈ none in Rust** — same C library; only a GPU renderer moves it (recorded: presentation already halves fps >~1400×880 logical) |
| Console static layers | resize/species only | 0 blit cost per frame (texture hits) | Cairo once → GSK texture | n/a | **none** — already optimal |
| Region rebuilds (rack 5 Hz, clock 1 Hz, keys on input) | event-driven | ~6 ms per rack rebuild @1440×900 @1.6× (recorded); visible as unfocused draw_ms 1.64 vs focused 1.30 (more rebuilds per frame at 30 fps) | Python loops over ~140 `_show` calls, 26-segment bargraphs, sprite blits; cairo commands marshalled via PyGObject | **high** — this is the largest *pure-Python-tax* stage left | **3–10×** on the rebuild; ~0.3–0.5 ms/frame amortised at archive |
| Text/Pango | cache miss only | ≈0 steady state (F7) | Pango/PangoCairo (C) + pygobject call per miss | low | **none** steady-state |
| GTK/GSK bookkeeping, presentation, main loop | 60 Hz | the unattributed **1.1–1.7 ms/frame** (F5, inference); historically dominant: 87–89.5 % CPU before texture caching (recorded) | GTK4/GSK C + PyGObject marshalling per tick/snapshot | marshalling share unknown, **unmeasured** | small–moderate; py-spy/perf attribution is exactly what's missing here |

### F9 — The repo's own history already isolates the big wins (and they're done)

**Claim:** The three recorded optimisation jumps map one-to-one onto where the
CPU used to be, and none of them is language-bound:
1. Full-window cairo surface re-uploaded per frame → texture-cached layers:
   instrument focused **87.1 % → 20.7 %**, archive **89.5 % → 25.5 %**
   (`docs/VALIDATION.md`, "Before this pass, same harness").
2. Per-frame text shaping → glyph-run cache: console headless cost 3.25 →
   0.37 ms @900×700 across passes.
3. Cairo micro-optimisations measured and settled in-repo: `ANTIALIAS_FAST`
   adopted (−16 %), stroke batching *rejected* (slower), Python path-build
   overhead measured at 0.33 ms ("not the bottleneck").
**Sources:** `docs/VALIDATION.md` (Known limitations #1; performance sections);
`abyssal/ui/console.py` `_show` docstring. **Support:** measurements, recorded.
**Confidence:** high. **Implication (inference):** remaining interpreter-side
cost is concentrated in region rebuilds + per-frame Python staging + marshalling
— precisely the parts a Rust port removes.

### F10 — Frame-time p50/p95 does not exist anywhere in the harness

**Claim:** No p50/p95 frame-time instrumentation exists. The probe writes the
0.5 s windowed mean (`_draw_avg`) and the *last* frame's instantaneous
`_draw_ms`/`_sim_ms` every 2 s (app.py `_tick_fps` ≈L525–540,
`_log_probe` ≈L550+); `perf_live.py` medians ~4 such rows per dwell. p95 tails
(untracked dict misses on new numeric strings, region rebuild frames, GC) are
invisible to every existing gate.
**Support:** direct evidence (absence in `qa/perf_live.py`, `qa/gates.py`,
`app.py`). **Confidence:** high. **This is the top gap for parity benchmarking**
— the Rust port must be compared on p50/p95, not means.

## Contradictions

- **Sim cost: 0.26–0.28 ms headless vs 0.76–0.84 ms live.** Same species
  (default specimen 0, s01, 10k pts). Hypotheses (inference): live is
  single-shot per frame with two `perf_counter` calls and cold caches vs the
  gate's warm 120-iteration loop; live also includes `PhysiologyModel.update`.
  Unresolved — the embedded patch below times them separately. (Sources:
  `qa/gates.py` GATE 3 vs `app.py` `_on_tick`.)
- **78 µs/sample (GATE 4) vs 0.36–0.42 ms/sample (live tel_ms).** Recorded in
  different passes; the later code reads `/proc/diskstats` + `/proc/net/dev`
  per sample, but no in-repo note reconciles the 5× gap. Unresolved.
- **"CPU 81 % of one core at ~68 fps" (early GATE 3 section) vs 20.7 % @60 fps
  now.** Not a true contradiction — different revisions (pre- vs post-
  texture-cache) — but the stale number must not be quoted as current.
- None found in the static architecture: probe timers, cache keys, and gate
  methods agree with the composition described in `docs/ARCHITECTURE.md`-level
  comments (I cross-read app.py ↔ console.py ↔ pointfield.py directly).

## Missing evidence (unverified / not runnable from this lane)

1. **No first-party live numbers from this session** (no execution tool):
   perf_live focused/unfocused/hidden, `perf stat`, `perf record -g`, `py-spy`.
   Availability of `py-spy`/`perf` on this machine is **unknown** to me.
2. **p50/p95 frame times** — uninstrumented (F10).
3. **The internal split of the unattributed 1.1–1.7 ms/frame** (GSK vs upload
   vs dispatch vs interpreter).
4. **Rack-region rebuild cost at the canonical 900×700** (only the 1440×900
   @1.6× figure is recorded).
5. **Unfocused variance** — VALIDATION itself flags Hyprland follow-mouse focus
   as a perturber of unfocused dwells.

### Ready-to-run protocol (NOT executed here — for the integrator)

```bash
cd /home/seeno/abyssal-organism-monitor
# 1) Recorded-envelope reproduction:
python3 qa/perf_live.py --dwell 8 --out /tmp/abyssal-cpu-profile/perf_live.json
# 2) Sampler attribution (user-local tools only, no system installs):
command -v py-spy || pipx install py-spy        # or: uv tool install py-spy
py-spy record --pid $(pgrep -f 'abyssal.app' | head -1) -d 10 -o /tmp/abyssal-cpu-profile/pyspy.svg --gil
py-spy top --pid $(pgrep -f 'abyssal.app' | head -1) -d 10
command -v perf && perf stat -p <PID> -- sleep 8 && \
  perf record -F 199 -g -p <PID> -- sleep 8 && perf report --stdio
# 3) Stage timers: append the patch below, then re-run perf_live; the probe
#    gains org_acc_ms / org_paint_ms / regions_ms / textures_ms / stage_keys_ms.
```

Drop-in stage timers for `Monitor._on_snapshot` (pure instrumentation; wrap,
don't alter, the existing calls; add the fields to `_log_probe`'s dict):

```python
# in Monitor.__init__:  self._st = {k: 0.0 for k in
#   ("layers", "org_math", "org_paint", "regions", "textures")}
# in _on_snapshot, around the existing blocks:
#   t=time.perf_counter() ... self._st["layers"]  += ...   # layer_under/over + put()
#   around PF.accumulate_physio: self._st["org_math"]  +=
#   around PF.paint_physio + cr.paint(): self._st["org_paint"] +=
#   around console.regions(...): self._st["regions"] +=
#   inside _texture() around the MemoryTexture.new cold path: upload ms counter
# in _tick_fps (0.5 s flush): emit self._st sums + counts as extra probe fields,
#   then reset; offline percentiles: python3 - <<'EOF' over the JSONL (p50/p95).
```

Also worth 30 minutes: run each specimen (keys 1–5) under the patched probe —
s03 (40k pts + trail) and s02/s04 (20k pts) will straddle the organism-math and
accumulate rows of the table.

## Sources

**Kept**
- `docs/VALIDATION.md` — the repo's measured record (perf_live tables, GATE 3
  tables, 78 µs telemetry, 6 ms rack rebuild, 87 %→20.7 % history, machine
  provenance). The measurement backbone of this profile.
- `qa/perf_live.py` — defines exactly how CPU%/FPS/draw/sim/tel are obtained
  (`/proc/<pid>/stat` utime+stime; probe medians; dwell per state).
- `abyssal/app.py` — frame loop, cadence policy (60/30/0), probe timer
  boundaries, texture cache, `--fps-cap`.
- `abyssal/ui/console.py` — static-layer/region/glyph-run caches with cache-key
  proofs; the "140 strings a frame" history.
- `abyssal/organism/{render,pointfield,mathforms,sources}.py` — organism pipeline
  and point counts (10k/20k/40k/20k/10k), bincount rationale, lit-pixel colormap.
- `abyssal/telemetry/source.py`, `abyssal/telemetry/history.py` — 5 Hz sample
  content and version-keyed trace cache.
- `qa/gates.py` — headless GATE 3 methodology (warm loops, n=120, per-stage).

**Rejected/deprioritized**
- `docs/CREATURE_EQUATIONS.md`, `qa/console_gates.py`, `qa/production_gates.py`,
  skin modules — read-worthy but no CPU-cost content beyond what VALIDATION
  already records (P12's 60/30/0 cadence is cited via VALIDATION).
- Early VALIDATION sections ("CPU 81 %", "organism 9.7 ms") — stale revisions,
  kept only as the optimisation history in F9, never as current state.
- Web sources — not consulted; every claim here is local code or local recorded
  measurement; nothing external was needed.

## Next steps

1. Integrator runs the protocol above (perf_live + py-spy/perf + stage-timer
   patch); fill F5's unattributed block and F8's "unknown" cells with first-party
   numbers, and produce p50/p95 frame-time baselines per layout × state × specimen.
2. Add p50/p95 to `qa/perf_live.py`/probe permanently — it is the metric the Rust
   parity benchmark will actually be judged on.
3. Before porting, decide the renderer question (CPU cairo parity vs GPU) —
   F8 shows the largest absolute cost at big windows is presentation-bound and
   language-independent.

## Supervisor coordination

No decision was needed: the task's own fallback ("if unavailable, say so and
fall back to timing instrumentation") covers this lane's missing execution
tooling, and the fallback artifacts (protocol + patch) are embedded above and
handed back via structured output. No routine handoff sent.
