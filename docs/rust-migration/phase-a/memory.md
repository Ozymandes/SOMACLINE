# Research: Python app memory baseline — Abyssal Organism Monitor (phase A, memory-audit lane)

## Summary
The app's resident set is dominated by raster state and runtime, not data: the code-derived
**data model floor is ≈ 5.4 MiB** (five cached organisms = 5.34 MiB + ≤6 KiB telemetry/physiology/history),
against a **recorded 278–359 MB RSS** on this machine (compact/instrument/archive, `qa/perf_live.py`,
`docs/VALIDATION.md`). Every pixel cache is count-bounded, not byte-bounded; the largest avoidable
duplication is the `GLib.Bytes` full-surface copy taken per cached texture in `app.py::_texture`,
and the largest churn is the rack region rebuild (≈6 ms, 5 Hz, recorded) plus per-frame `np.bincount`
float64 temporaries in the point-field rasteriser.

**Measurement honesty (read first):** this lane has **no shell/execution tools** (file read/write + web
only). Task step 1 (launch + 3× RSS/PSS sampling) **could not be executed by this lane**. All live numbers
below are prior recorded measurements from `docs/VALIDATION.md` (produced on this same machine by earlier
passes with `qa/perf_live.py`), cited as facts with their source but **not reproduced here**. Exact
reproduction commands, including the missing PSS sampling, are given in "Reproduce".

## Findings

### A. Baseline (task 1) — recorded, not re-measured by this lane

1. **Claim:** Steady-state RSS at the three harness layouts: compact 600×480 **278 MB**, instrument 900×700
   **311 MB** (unfocused 313, hidden 311), archive 1440×900 **358 MB** (unfocused 359). Method: `qa/perf_live.py`,
   10 s dwell per state, RSS from `/proc/<pid>/status` (`VmRSS`).
   **Sources:** [docs/VALIDATION.md — "Real application resource use (qa/perf_live.py, 10 s dwell per state)"](../../../../../../abyssal-organism-monitor/docs/VALIDATION.md), harness: `qa/perf_live.py::rss_mb` (reads `VmRSS:` from `/proc/<pid>/status`) and `qa/perf_live.py::measure`.
   **Support:** direct evidence (recorded run on this machine), **not reproduced by this lane**. **Confidence:** high for the numbers as recorded; medium for current-code applicability (code has changed since some passes).

2. **Claim:** At instrument, ~96 MB of RSS is file-backed shared libraries (NVIDIA GL 34 MB, LLVM 19 MB,
   GTK 8 MB per `smaps`), and the process's own anonymous memory is **~130 MB** ("Python, NumPy, decoded
   module sprites, device-resolution caches").
   **Sources:** docs/VALIDATION.md, same section ("**RSS** at instrument is ~240–310 MB; `smaps` shows…").
   **Support:** direct evidence (recorded). **Confidence:** high.

3. **Claim:** Earlier revision measured **RSS 156 MB** at the same canonical tile with growth +0.43 MB/15 s
   ("allocator noise, not a leak — tracemalloc ~0 bytes net over 500 steady-state update() calls"), CPU 81%
   of one core at ~68 fps.
   **Sources:** docs/VALIDATION.md — GATE 3 section, POC pass.
   **Support:** direct evidence (recorded); superseded by the production-pass numbers in (1) after the
   module-skin/static-layer/texture passes. **Confidence:** high (as recorded).

4. **Claim:** PSS (`/proc/<pid>/smaps_rollup`) has **never been sampled** by any recorded pass; only RSS and
   a qualitative `smaps` library attribution exist. Three-sample RSS/PSS at ~60 s steady state is the gap.
   **Sources:** docs/VALIDATION.md (no PSS figures anywhere); `qa/perf_live.py` (no `smaps_rollup` read).
   **Support:** direct evidence of absence. **Confidence:** high.

**Reproduce (for the integrator to run; not run by this lane):**
```
./run.sh --help                                   # flags: --width --height --quit-after --probe --fps-cap
python3 qa/perf_live.py --dwell 8 --out /tmp/claude-1000/abyssal-qa/perf.jsonl
# PSS supplement (3 samples at ≥60 s steady state):
for i in 1 2 3; do sleep 20; grep -H VmRSS /proc/$PID/status; grep -H \
  -e Pss: -e Private_Clean: -e Private_Dirty: -e Swap: /proc/$PID/smaps_rollup; done
# headless fallback (no compositor needed):
python3 qa/offscreen.py /tmp/frame.png --width 900 --height 700 --specimen 0
```

### B. Byte attribution from code (task 2)

All values are computed from source-declared shapes × dtype × counts (researcher computation; the
underlying shapes are direct evidence). "ds" = `hidpi.scale()` — device cache scale, quantised to quarter
steps (`hidpi.set_scale`); on this 1.6× desktop it is **1.5** (`round(1.6*4)/4`).

5. **Claim:** Decoded module PNGs (`skin/surface.sprite()` cache `_base`, decode-once, never evicted), from
   the registry dims in `skin/modules.py` (ARGB32 = w·h·4 B):
   SHELL 1600×1172 → 7.15 MiB · HEADER 1800×185 → 1.27 MiB · OBSERVATION 1300×948 → 4.70 MiB ·
   RACK 900×1213 → 4.17 MiB · SELECTOR 1400×264 → 1.41 MiB · FOOTER 1800×93 → 0.64 MiB.
   **Σ six modules = 20,277,200 B ≈ 19.3 MiB** (all six resident after ARCHIVE; SHELL/HEADER/OBSERVATION/
   SELECTOR/FOOTER ≈ 15.2 MiB on the INSTRUMENT path — RACK is drawn only when `six_module()`).
   Plus key plates 256×267 ×4 B ≈ 0.26 MiB each (10 = 2.6 MiB after touring all five specimens), lamps/caps/
   mode/cycle sprites small (≈0.3 MiB). `frame/observation_bezel` (authored 1280×900 → 4.4 MiB) and
   `frame/plate_recessed` (640×186 → 0.45 MiB) are NOT on the steady-state module path (module sprites
   replace the bezel; PLATE only as a missing-sprite fallback).
   **Sources:** `abyssal/skin/modules.py` (SHELL…FOOTER registry dims), `abyssal/skin/surface.py::sprite()` (`create_from_png`, `_base` dict), `abyssal/ui/console.py::six_module/_draw_stage/_draw_chassis`, `abyssal/ui/selector.py::KEY_ASPECT` (256/267), `docs/VALIDATION.md` ("51 of 65 sprites referenced").
   **Support:** shapes direct; byte math interpretation. **Confidence:** high for the six modules; medium for the small-sprite tail (on-disk dims not stat-able from this lane; registry notes rebuilt sprites may differ by ±1 px).

6. **Claim:** Point-field buffers (`organism/pointfield.py::_buffers`, LRU `_BUFFER_LIMIT = 3`, key
   (species,w,h)): six arrays per entry (acc, acc_e, acc_h flat f32; a, m, tmp f32) = **24 B/px**. Field side
   = `int(2·R_FIELD·vp.scale·res)+2` with `R_FIELD = 464`, `res = min(1, 760/side_px)` (`MAX_FIELD_PX = 760`):
   canonical INSTRUMENT (glass ≈470×390, scale≈0.39) side ≈ 364 → **3.0 MiB/entry**; 2560×1600 fullscreen
   side ≈ 762 (cap) → **13.3 MiB/entry**. Steady state holds 1 entry (3.0–13.3 MiB); after species
   switching up to 3 (9–40 MiB).
   **Sources:** `abyssal/organism/pointfield.py::_buffers/_BUFFER_LIMIT`, `abyssal/organism/render.py::draw_organism` (side/res math, `MAX_FIELD_PX`), `abyssal/core/world.py::WORLD_RADIUS`.
   **Support:** shapes direct; sides at given window sizes are researcher computation (glass rect depends on layout). **Confidence:** high.

7. **Claim:** Static console layers (`ui/console.py::_UNDER_CACHE/_OVER_CACHE`, `_LAYER_LIMIT = 2` each):
   full-window `hidpi.surface(w,h)` ARGB32 at ds: canonical 900×700 → 1350×1050 → **5.4 MiB each (10.8 both)**;
   fullscreen 1600×1000 logical → 2400×1500 → **13.7 MiB each (27.5 both)**. Each cache may hold TWO sizes
   after a resize (stale entry until a third size arrives) → worst 2× those figures.
   **Sources:** `abyssal/ui/console.py::_cached_layer/_layer_key/layer_under/layer_over`, `abyssal/skin/hidpi.py::set_scale` (quarter-step quantise).
   **Support:** shapes direct; window→ds arithmetic interpretation. **Confidence:** high.

8. **Claim:** Region cache (`_REGION_CACHE`, `_REGION_LIMIT = 8`): each region re-composites the full
   under+over static layers into its own surface (pad 6 logical px, device-snapped). Rack region
   ≈ readout rect: canonical INSTRUMENT condensed strip ≈ 860×100 logical → **0.74 MiB/entry**; ARCHIVE
   rack column ≈ 534×729 logical → **3.5 MiB/entry** (fullscreen ≈ 6.7 MiB). Keys: rack regions are rebuilt
   **every 5 Hz telemetry sample** (key includes `tel_key` + per-channel `ring.version`), the clock every 1 s,
   keys on input — so the 8 LRU slots hold ~1.6 s of dead rack regions: **≈6 MiB resident at canonical,
   ≈25–50 MiB at ARCHIVE/fullscreen**, all destined for eviction.
   **Sources:** `abyssal/ui/console.py::_region/regions/_snap`, docs/VALIDATION.md ("A rack region rebuild … costs ~6 ms at 1440×900 @1.6×, five times a second").
   **Support:** direct code evidence for keys/lifecycle; sizes computed. **Confidence:** high (mechanism), medium (exact MiB — rect sizes vary per layout).

9. **Claim:** Smaller raster caches: `_TRACE_CACHE` (≤16; 4 graph traces, new entry per 200 ms sample;
   ≈0.12 MiB each at ARCHIVE → ≤1.9 MiB, ~75% dead entries); `_TXT` glyph-run cache (≤700 rendered-string
   surfaces, `console.py::_TXT_LIMIT = 700`; **estimated 14–24 MiB at steady state** — estimate, not
   measured); `_RING_CACHE` (≤8, dotted graticule rings, ≈1.1 MiB/entry at canonical, keyed incl. alpha);
   `_WASH` deep-field discs (`render.py`, ≤6, ≈0.43 MiB each at canonical, raw pixel-size ARGB32);
   `_FALLOFF` A8 light masks (`core/lighting.py`, ≤24, radius ≤240 → ≤0.22 MiB each, 8 px quantisation);
   chrome Pango plumbing: `_TW_CACHE` (≤4096 measured widths), `_FD_CACHE/_ATTR_CACHE/_CAP_CACHE/_BG_CACHE`
   (small); `console._NULL_SURFACE` 1×1 A8. Telemetry history: 4 channels × 300 × f32 = **4,800 B**
   (production gate P10 verifies 4,800 B before/after 20,000 pushes) + per-Ring idx caches (≤8 small i64
   arrays). Segment displays are procedural paths — no cache.
   **Sources:** `abyssal/ui/console.py` (_TXT ~line 66, _RING_CACHE ~line 222, _TRACE_CACHE/_TRACE_LIMIT ~line 1395), `abyssal/organism/render.py::_wash_disc/_WASH_LIMIT`, `abyssal/core/lighting.py::_FALLOFF/_FALLOFF_LIMIT/MAX_RADIUS`, `abyssal/ui/chrome.py::_TW_CACHE etc.`, `abyssal/telemetry/history.py::History.nbytes`, docs/VALIDATION.md gate P10, `abyssal/ui/segment.py` module docstring.
   **Support:** mixed (shapes direct; _TXT residency is researcher estimate). **Confidence:** high except _TXT (low-medium, flagged).

10. **Claim:** Persistent organism state (`organism/mathforms.py::SourceBody`, `__slots__`): 14 f32 arrays of
    length n — `_perm,_mod2,_mod4,_u,_lat,_grp,_s,_f,_e,_h,_t1,_x,_y,_w` = 56n B per organism; n from
    `organism/sources.py::SOURCES` (s01 10 000, s02 20 000, s03 40 000, s04 20 000, s05 10 000). All five
    organisms are built lazily and **kept for the life of the process** (`app.py::_organisms`):
    s01 0.53 MiB · s02 1.07 MiB · s03 2.14 MiB · s04 1.07 MiB · s05 0.53 MiB → **Σ ≈ 5.34 MiB** once every
    specimen has been visited (0.5–2.1 MiB before that). Pulse LUT 256×3 f32 = 3 KiB.
    **Sources:** `abyssal/organism/mathforms.py` (slots + `__init__` allocations), `abyssal/organism/sources.py::SOURCES`, `abyssal/app.py::_organism` ("built lazily and then kept for the life of the process").
    **Support:** direct evidence. **Confidence:** high.

### C. Duplication & churn (task 3)

11. **Claim:** No duplicate PNG decode exists: `cairo.ImageSurface.create_from_png` is called at exactly one
    site (`skin/surface.py::sprite`), memoised in `_base`; module drawing scales/clips the base surface per
    tile without intermediate surfaces (`skin/modules.py::Module.draw`). **Duplicate-decode risk: none found.**
    **Sources:** `abyssal/skin/surface.py`, `abyssal/skin/modules.py::draw`. **Support:** direct. **Confidence:** high.

12. **Claim:** The biggest avoidable *resident* duplication is `app.py::_texture`: every cached surface is
    handed to GSK as `Gdk.MemoryTexture.new(..., GLib.Bytes.new(bytes(surf.get_data())), ...)` — a **full
    second copy in RAM of every layer/region** (static layers double: +10.8 MiB canonical / +27.5 MiB
    fullscreen; plus one copy per region), with `_textures` holding ≤8 (surf, tex) pairs alive.
    **Sources:** `abyssal/app.py::_texture/_on_snapshot/put`. **Support:** direct evidence. **Confidence:** high.

13. **Claim:** Stale-size retention is systemic: every pixel cache is bounded by COUNT, never by bytes, and
    nothing evicts after a resize settles — `_UNDER/_OVER` keep the previous full-window layer until a third
    size; `_scaled` (nine-slice/sprite scales, ≤64 entries, `skin/surface.py`) keeps panels at every drawn
    size of a resize drag; `_BUFFERS`(3)/`_WASH`(6)/`_RING_CACHE`(8)/`_FALLOFF`(24) keep previous
    radii/species; `_REGION_CACHE`/`_TRACE_CACHE` keys include volatile inputs (`tel_key`, `ring.version`,
    clock string) so ~75–90% of resident entries are dead-but-resident between rebuilds. During torture-style
    resize storms the caches legally pin roughly: 2 sizes × 10.8 MiB layers + 64 scaled panels + 8 regions +
    16 traces + 3 field buffers ≈ tens of MiB at canonical, more at fullscreen.
    **Sources:** `abyssal/ui/console.py` (all four console caches), `abyssal/skin/surface.py::_MAX_SCALED/nine_surface/_scaled_sprite`, `abyssal/organism/pointfield.py::_BUFFER_LIMIT`, `abyssal/organism/render.py::_WASH_LIMIT`, `abyssal/core/lighting.py::_FALLOFF_LIMIT`.
    **Support:** direct code evidence; magnitude is researcher inference. **Confidence:** high (mechanism), medium (magnitude).

14. **Claim:** Per-frame NumPy garbage (transient, not resident): (a) `accumulate_physio` calls
    `np.bincount` three times per frame — bincount returns **float64 at w·h** → 3.2 MB/frame at canonical,
    13.9 MB/frame at fullscreen cap; (b) `paint_physio`/`paint` allocate a fresh output `backing`
    (stride·h uint8: 0.5 MB canonical / 2.3 MB fullscreen per frame) — documented as a correctness rule (GSK
    snapshot safety), not waste; (c) `SourceBody.update` + source equations allocate ~30–40 temporaries of
    4n B (s03: ≈6 MB per update); (d) at 5 Hz each rack region rebuild allocates surface + Bytes copy
    (≈1.5–13 MB/event). The POC's tracemalloc finding (≈0 net) shows this is churn, not growth.
    **Sources:** `abyssal/organism/pointfield.py::accumulate/accumulate_physio/paint/paint_physio`, `abyssal/organism/mathforms.py::update`, `abyssal/organism/sources.py::_c01…_c05`, `abyssal/app.py::_texture`, docs/VALIDATION.md (tracemalloc note; rack 6 ms × 5 Hz).
    **Support:** direct (allocation sites); per-frame MB computed. **Confidence:** high.

### D. Rust floor for the data model (task 4)

15. **Claim:** The DATA MODEL alone (telemetry + physiology + creature + history; no GTK/Pango/Cairo, no
    raster) fits in **≈ 5.4 MiB**: organisms 5.34 MiB (all five resident, matching the Python app's actual
    policy) or 0.53–2.14 MiB with only the active specimen; telemetry/physiology/model scalars < 10 KiB;
    history rings 4,800 B + ≤2 KiB index tables; LUT 3 KiB. Layout/Viewport are value types (~1 KiB,
    per-frame). Including *creature raster only* (point-field accumulators + one reusable output image,
    single active species): +4 MiB canonical / +15.5 MiB fullscreen-cap → **≈ 9–21 MiB total**. Including
    *console raster at parity* (static layers + regions + decoded sprites + text/ring/falloff caches,
    canonical): ≈ 55–75 MiB → ≈ 65–95 MiB. Since the recorded RSS is 311 MB at the same state, ≈ 98% of
    resident memory is runtime (CPython+NumPy+GTK bindings ≈ 60–110 MB anonymous + 96 MB file-backed libs +
    GSK/compositor buffers) and raster caching, not data.
    **Sources:** all attribution findings above; docs/VALIDATION.md (278–359 MB, ~96 MB libs, ~130 MB anonymous, P10 4,800 B).
    **Support:** researcher computation from direct-evidence shapes; the 311 MB anchor is a recorded measurement. **Confidence:** medium-high (data-model terms high; the ≈98% split is inference).

    Rust-port implications (inference): the floor says the win is (a) dropping CPython/NumPy/pygobject
    overhead, (b) preallocated f32 scatter/bincount (no float64 temporaries), (c) reusable output/staging
    buffers where the GSK snapshot rule does not apply, (d) optional: upload decoded module PNGs once as
    `gdk::Texture` and free the CPU-side ARGB32 (−19.3 MiB), (e) byte-budgeted LRU eviction on resize
    settle. Behavioral/visual parity does NOT require the Bytes-copy duplication or float64 bincount.

## Contradictions
- **"Organism arrays total under 1 MB" (VALIDATION.md, POC pass) vs current code:** one s03 organism is now
  2.14 MiB and all five cached total 5.34 MiB. The POC statement predates the propagating-pulse arrays
  (`_perm/_mod2/_mod4/_u/_lat/_grp/_s/_f/_e/_h/_t1`) and the five-organism cache. Recorded number is stale
  relative to HEAD; treat 5.34 MiB as current.
- **RSS 156 MB (POC) vs 311 MB (final) at the same 900×700 tile.** Both recorded; the +155 MB came with the
  module-skin/static-layer/device-scale/texture passes. Not a leak (+0.43 MB/15 s, tracemalloc ≈0 net), but
  the "RSS is dominated by the runtime" framing only holds against the current caches with the ~130 MB
  anonymous figure.
- **Scale labelling:** VALIDATION.md describes caches "at 1.6×"; `hidpi.set_scale` quantises to quarter
  steps, so the actual cache scale on this 1.6× desktop is **1.5**. Byte figures here use 1.5; P9's
  "byte-identical at 1.0× and 1.6×" presumably exercised `set_scale(1.6)` → 1.5. Minor, but changes pixel
  math by (1.6/1.5)² ≈ 1.14× if read literally.

## Missing evidence
- **No live RSS/PSS samples by this lane** (no execution tool). The required 3× RSS+PSS at ~60 s steady
  state is unrun; the commands in "Reproduce" close this gap in ~4 minutes.
- `_TXT` cache residency (14–24 MiB) is an estimate from entry-count × average surface size; no
  instrumentation exists. A one-off probe (`len(console._TXT)` + Σ w·h·4 over entries) would settle it.
- On-disk PNG dimensions for the ~65 runtime sprites were not stat-able (no shell); the six module dims and
  key-plate aspect come from code, with a documented ±1 px rebuild caveat. The small-sprite tail (≈0.5 MiB)
  is bounded but unverified.
- GSK-side memory (GPU texture copies of the 8 cached `Gdk.MemoryTexture`s, compositor buffers) is not
  code-attributable; it is inside the recorded RSS/anonymous figures but not separated.
- PSS was never recorded in any pass, so shared-vs-private split over time (swap, page sharing) is unknown.

## Sources
- Kept: `docs/VALIDATION.md` — the only recorded live measurements (RSS per layout/state, ~96 MB libs,
  ~130 MB anonymous, P10 history bytes, rack-region cost); directly anchors every modelled number.
- Kept: `qa/perf_live.py` — the exact harness/method for RSS+CPU sampling (and where PSS sampling belongs).
- Kept: `abyssal/organism/pointfield.py`, `abyssal/organism/render.py`, `abyssal/organism/mathforms.py`,
  `abyssal/organism/sources.py` — point-field buffer shapes, field-size cap, per-frame temporaries, n per
  species, persist values.
- Kept: `abyssal/ui/console.py` — `_UNDER/_OVER/_REGION/_TRACE/_TXT/_RING` caches with limits and keys;
  region lifecycle (5 Hz rack, 1 Hz clock).
- Kept: `abyssal/app.py` — `_textures` (≤8) and the `GLib.Bytes` duplication in `_texture`.
- Kept: `abyssal/skin/surface.py`, `abyssal/skin/modules.py`, `abyssal/skin/hidpi.py`,
  `abyssal/skin/catalog.py` — decode-once sprite cache, ≤64 scaled-panel cache, module registry dims,
  quarter-step device-scale quantisation.
- Kept: `abyssal/telemetry/history.py`, `abyssal/telemetry/source.py`, `abyssal/core/signals.py`,
  `abyssal/core/physiology.py`, `abyssal/core/world.py`, `abyssal/core/viewport.py`, `abyssal/core/layout.py`,
  `abyssal/core/lighting.py`, `abyssal/core/theme.py`, `abyssal/ui/chrome.py`, `abyssal/ui/fonts.py`,
  `abyssal/ui/segment.py`, `abyssal/ui/selector.py` — small/verified-negligible state; falloff masks; Pango
  plumbing caches; procedural (cache-free) segment renderer; pure-geometry selector.
- Kept: `assets/hardware_v2/INVENTORY.md` — 2K master dims and the "large assets, load once" guidance
  (masters, not runtime sprites).
- Rejected/deprioritized: `qa/gates.py`, `qa/console_gates.py`, `qa/production_gates.py`,
  `qa/physiology_gates.py`, `qa/torture.py`, `qa/shots.py`, `qa/compare.py` — correctness/perf gates, not
  memory evidence (only their recorded P10 result was needed); `docs/ARCHITECTURE.md`,
  `docs/CREATURE_EQUATIONS.md` — design docs superseded by reading the code directly for byte attribution;
  web search — unnecessary, this is a local code-audit task.

## Next steps
1. Integrator runs the 3× RSS + PSS sampling at ~60 s steady state (commands above), ideally adding a
   `--pss` option to `qa/perf_live.py::measure` so the baseline is reproducible for the Rust comparison.
2. One-off instrumentation pass: report `len()` and byte totals of `_TXT`, `_scaled`, `_REGION_CACHE`,
   `_TRACE_CACHE`, `_BUFFERS`, `_WASH`, `_RING_CACHE`, `_FALLOFF` at steady state (10-line probe), to
   replace the two estimates in this audit.
3. After the Rust skeleton exists: re-measure with the same harness; acceptance target for the data model
   is ≤6 MiB private, and for churn zero float64 bincount temporaries.
