# Phase A — Rust Port Parity Specification (the acceptance contract)

This document is the **acceptance contract** for translating the Abyssal
Organism Monitor (Python 3 + GTK4 + Cairo + NumPy) to native Rust (gtk4-rs).
Every invariant the Rust build must reproduce is stated here with its source
location, followed by the executable gate list (**G-R1 … G-R45**) the Rust
port must pass.

Citation convention: `file::symbol` refers to the Python codebase at
`/home/seeno/abyssal-organism-monitor` (FACTS). Items marked **[proposal]**
are thresholds chosen by this spec (not pre-existing project measurements)
and are calibrated at first cross-render; items citing `docs/VALIDATION.md`
are existing MEASUREMENTS.

---

## §A. Equation invariants

### A.1 The five sources — canonical data (FACT: `abyssal/organism/sources.py::SOURCES`, `docs/CREATURE_EQUATIONS.md`)

| key | n/frame | dt (per source frame) | ink | persist | frame_cx | frame_cy | frame_half |
|---|---|---|---|---|---|---|---|
| s01 | 10,000 | π/240 | 116/255 (0.454902) | 0.0 | 196.38 | 203.49 | 116.95 |
| s02 | 20,000 | π/45  | 96/255 (0.376471)  | 0.0 | 213.12 | 200.94 | 181.72 |
| s03 | 40,000 | π/90  | 46/255 (0.180392)  | 0.6235 | 199.38 | 182.16 | 164.02 |
| s04 | 20,000 | π/30  | 96/255 (0.376471)  | 0.0 | 199.68 | 199.70 | 140.80 |
| s05 | 10,000 | π/20  | 66/255 (0.258824)  | 0.0 | 214.04 | 187.18 | 124.43 |

- All sources draw into a 400×400 canvas: `SRC = 400.0`, `SRC_MID = 200.0`
  (`sources.py::SRC`).
- Verbatim JS source for each equation lives in `Source.original` and in
  `docs/CREATURE_EQUATIONS.md`; the Rust port must be transcribed from the
  **same ported forms** as `_c01.._c05` (below), which are themselves
  operator-by-operator checked against JS precedence.
- Porting rules (FACT `docs/CREATURE_EQUATIONS.md` "Porting rules"):
  `mag(a,b)`→`hypot(a,b)`; `sin/cos/atan2` map 1:1; `PI`→`π`;
  `for(i=N;i--;)`→one index array; ternary→`where`; `point(x,y)`→one sample;
  `circle(x,y,d)`→one sample with weight `d²` (s01 only: `k*k>15 ? 2 : 1`
  diameter → weight `4.0` or `1.0`); `stroke/fill` alpha `A/255`→`ink`;
  `background(g)`→accumulator cleared; `background(g,A)`→retention `1−A/255`
  (s03: `background(6,96)` → persist `0.6235`).
- **Operator-order traps already resolved in the Python port — keep them:**
  - s05: `mag(k,e)**2/59+4` = `(k²+e²)/59 + 4` (`**` binds tighter than `/`).
  - s02: `sin(...)/2*k*(...)` is left-associative: `((sin/2)·k)·(...)`.
  - s01: `.3/k` spikes as `cos(x/21)→0` — a genuine feature; `x` is integral
    so `k≠0`. Non-finite samples get **zero weight, never dropped**
    (see A.5).
- **Loop order**: originals count down, the ports count up; identical point
  *set*, and all points composite at one alpha, so order cannot change the
  image (`sources.py` "Loop order").
- **The global `i`**: s02 reads `i` in its `d` default (`9*cos(i/81)`,
  `i/765`), s04 reads `i%4*8`. Load-bearing (s04's four plumes). Rust must
  evaluate over the same index array semantics.
- **Orientation**: no y-flip anywhere. p5 and Cairo both put +y down; s05's
  bulb stays at the *lower* end of the rachis even though the engraved key
  draws it on top (`docs/CREATURE_EQUATIONS.md` "Orientation").

### A.2 Ported equation forms (FACT `abyssal/organism/sources.py::_c01.._c05`)

Rust must evaluate these exact expressions (names kept: `k, e, d, o, q, c`):

- **s01** `x=i, y=i/235`: `k=4·cos(x/21)`, `e=y/8−20`, `d=hypot(k,e)`;
  `q=3·sin(2k) + 0.3/k + sin(y/19)·k·(9+2·sin(14e−3d+2t))`;
  `X=q+50·cos(c=d−t)+200`; `Y=q·sin(c)+39d−475`;
  weight `where(k²>15, 4.0, 1.0)`.
- **s02** `m=(i%2)·9`: `k=9·cos(i/81)`, `e=i/765−13`, `d=hypot(k,e)/4`;
  `inner=where(k²<19, 3t+4d, d/2+4)`;
  `q=79−2·sin(3k) + sin(inner)/2 · k · (9+5·sin(d²−e/6−t+m))`;
  `c=d²/9−t/16+m`; `X=q·sin(c)+200`; `Y=(q+50)·cos(c)+200`.
- **s03** `x=i%200, y=i/200`: `k=x/8−12.5`, `e=y/8−12.5`,
  `o=hypot(k,e)/12 · cos(sin(k/2)·cos(e/2))`, `d=5·cos(o)`;
  `X=(x + d·k·(sin(2d+t)+sin(y·o²))/9)/1.5 + 133`;
  `Y=(y/3 − 40d + 19·cos(d+t))·1.5 + 300`.
- **s04** `y=i/500`: `k=cos(9y)·where(y<5, sin(t/8+y)·35, 11)`, `e=y/8−13`,
  `o=hypot(k,e)/6`;
  `q=k·y/19 + 49 + k·sin(y)·sin(2o−e/5−t)`;
  `c=o/3 − e/5 − t/8 + (i%4)·8`; `X=q·sin(c) − 79·cos(c/3) + 200`;
  `Y=200+(q+70)·cos(c)`.
- **s05** `x=i%200, y=i/43`: `k=5·cos(x/14)·cos(y/30)`, `e=y/8−13`,
  `d=hypot(k,e)²/59 + 4`;
  `q=60 − 3·sin(atan2(k,e)·e) + k·(3 + (4/d)·sin(d²−2t))`;
  `c=d/2 + e/99 − t/18`; `X=q·sin(c)+200`; `Y=(q+9d)·cos(c)+200`.

### A.3 Numeric-precision contract (FACT — dtype boundaries; see also Appendix 1)

- **All equation arithmetic is float32.** `Source.indices()` returns
  `arange(n, dtype=float32)` (`sources.py::Source.indices`); every array op
  in `_c01.._c05` and every perturbation/scatter op in `mathforms.py`
  operates on float32 arrays. Python-f64 scalars (including `t`) enter
  ufunc calls as *weak* scalars and are rounded into the f32 loop per
  element (NumPy 1.x value-based and 2.x NEP-50 promotion both give f32
  here). Rust: use `f32` arrays/ops throughout the organism solver;
  constants as `f32` literals.
- **Clocks and physiology are f64**: `Source.time`, `wave`, `aux`, `_jit`,
  everything in `core/physiology.py`, and all of `core/viewport.py` /
  `core/layout.py`.
- **Two explicit f64→f32 round points on the render path** (Rust must
  reproduce both):
  1. `render.py::draw_organism`: `k = np.float32(vp.scale * res)` — the
     world→field-pixel factor is computed in f64 then rounded to f32 before
     multiplying the f32 point coordinates; `half = np.float32(side*0.5)`;
     `px = wx*k + half` (f32).
  2. Pixel indexing truncates toward zero: `px.astype(np.int32)` (C cast).
- **`np.bincount` accumulates in f64** regardless of the weight dtype; the
  sum is then added into a float32 accumulator with f32 rounding
  (`pointfield.py::accumulate/accumulate_physio`). Within one pixel,
  summation order = input (permuted) point order. Rust must accumulate f64
  per pixel then round to f32 on store to match bit-for-bit.
- `History` rings are float32; `Ring.window()` column means computed with
  float32 `add.reduceat` over `nan_to_num`-cleaned f32 (`history.py`).
- `np.exp/np.sin/np.cos/np.arctan2/np.hypot` on f32 arrays use NumPy's SIMD
  paths, which are not guaranteed correctly rounded; Rust `f32`
  transcendentals (libm) may differ by ULPs. The gates therefore demand
  *tolerance* on coordinates (G-R1) but *bit-exactness* on logic the
  implementation controls itself (G-R9, G-R15, G-R20).

### A.4 Seating transform (FACT `sources.py::Source.to_world`, `mathforms.py::update`)

- `WORLD_FIT = 372.0` world half-extent; world square 1000×1000, design
  radius `WORLD_RADIUS = 460.0` (`core/world.py`).
- Seating = translate to `(frame_cx, frame_cy)` then uniform scale
  `k = WORLD_FIT / frame_half` — performed **in place on f32 arrays**
  (`mathforms.SourceBody.update`): `x = (x − cx)·k`, `y = (y − cy)·k`.
  No rotation, no flip, no aspect correction.
- Robust bounding boxes (0.15/99.85 percentiles over one full source-clock
  period, from `tools/measure_sources.py`) are the `frame_*` constants in
  §A.1; Rust uses the same constants (or re-derives identical ones).

### A.5 Clipping / finiteness rule (FACT `mathforms.py::update`)

After seating, in this exact order:

1. `r = hypot(x,y)` (f32), floored at `1e-9`.
2. `w *= (r <= R_SAFE)` where `R_SAFE = WORLD_RADIUS − 6 = 454.0`: points
   beyond the safe radius become **invisible (zero weight), not clamped**,
   though their coordinate is still pulled onto the circle
   (`r = min(R_SAFE/r, 1)`; `x*=r; y*=r`) so the reported extent stays
   bounded.
3. Non-finite samples: coordinate set to `0.0` and weight to `0.0` — the
   point count is a fixed shape; samples are never dropped.

### A.6 Species construction, permutation, anatomy (FACT `mathforms.py::SourceBody.__init__`, per-species classes)

- Default seed `20260920`; the app builds specimen `i` with
  `seed = opts.seed + i` (`app.py::Monitor._organism`); offscreen/QA harness
  may pass other seeds. `build(key, seed)` in `mathforms.py`.
- `self._perm = np.random.default_rng(seed).permutation(n).astype(f32)` —
  **NumPy `default_rng` = SeedSequence→PCG64.** Rust must either implement
  NumPy's exact SeedSequence+PCG64 stream or (preferred) consume a fixture
  array exported from Python (G-R3). The permutation defines the *prefix*
  thinning under memory pressure, so it is parity-critical.
- `_mod2 = perm % 2`, `_mod4 = perm % 4` (f32), precomputed.
- Anatomy `(u, lat, grp)` is computed **once, in original index order over
  `arange(n, f64)`, then reordered by `perm`**, stored f32:
  - s01 SigmoidPlume: `k=4cos(i/21)`, `e=(i/235)/8−20`;
    `u=norm(hypot(k,e))`, `lat=|k|/4`, `grp=0`.
  - s02 CoupledBodies: `k=9cos(i/81)`, `e=i/765−13`;
    `u=norm(hypot(k,e)/4)`, `lat=|k|/9`, `grp=i%2`.
  - s03 MirroredAlien: `x=i%200,y=i/200`; `k=x/8−12.5`, `e=y/8−12.5`;
    `u=norm(hypot(k,e))`, `lat=0`, `grp=(k>0)`.
  - s04 QuadPlume: `y=i/500`; `u=norm(y)`, `lat=0`, `grp=i%4`.
  - s05 SingleFeather: `x=i%200,y=i/43`; `k=5cos(x/14)cos(y/30)`, `e=y/8−13`;
    `u=norm(hypot(k,e)²/59+4)`, `lat=|cos(x/14)|`, `grp=0`.
  - `_norm(v) = (v−min)/(max−min)` with a `1e-9` floor on the denominator,
    in f32 (`mathforms._norm`).
- Species→class binding: `BODIES = {s01:SigmoidPlume, s02:CoupledBodies,
  s03:MirroredAlien, s04:QuadPlume, s05:SingleFeather}`.

### A.7 Specimen metadata (FACT `organism/species.py::CATALOGUE`)

| idx | key | name | epithet | archive | short | plan | notes | response |
|---|---|---|---|---|---|---|---|---|
| 0 | sigmata | PLUMARIA SIGMATA | THE SIGMOID PLUME | AQS-0042 | SIGMA | AXIAL | BIFURCATED FILAMENT SHEET | LOAD READS AS A RIPPLE DOWN THE BODY |
| 1 | coniugata | DIPLOSOMA CONIUGATA | THE COUPLED PAIR | AQS-0117 | DIPLO | PAIRED | TWO BODIES, ONE EQUATION | I/O CROSSES BETWEEN THE BODIES |
| 2 | rostrata | SYMMETRA ROSTRATA | THE MIRRORED ROSTRUM | AQS-0233 | ROSTRA | MIRROR | EXACT REFLECTION PLANE | HEAT BREAKS THE MIRROR PLANE |
| 3 | quadriplex | QUADRIPLUMA ARTICULATA | THE QUARTERED PLUME | AQS-0308 | QUADRI | QUARTERED | FOUR PLUMES, ONE INDEX CLASS | LOAD DESYNCHRONISES THE FOUR |
| 4 | solitaria | PENNARIA SOLITARIA | THE SOLITARY FEATHER | AQS-0451 | PENNA | ARCUATE | ONE RACHIS, RIBBED VANE | THE WHIP GROWS TOWARD THE TIP |

All are `cls="MATHEMATICAL FORM"`, `origin="SYNTHETIC"`; symmetry strings:
AXIAL / CURVILINEAR · PAIRED / ACENTRIC · SAGITTAL MIRROR · FOUR-PART /
ARTICULATED · AXIAL / ARCUATE; morphology: SIGMOID PLUME · DIPLOSOMATIC ·
ROSTRATE · QUADRIPLUMATE · PENNATE. These strings appear in the footer rail,
header and hit the selector label logic — byte-identical in Rust.

---

## §B. Physiology invariants

### B.1 Smoother core (FACT `core/physiology.py`)

All f64. `update(dt, telemetry, render)`:

1. `dt = clamp(dt, 0, 0.25)`.
2. Targets: `ag = lerp(0.12, 0.85, cpu_load**0.85)`;
   `pu = lerp(0.15, 0.80, temperature if temp_available else 0.35)`;
   `de = lerp(0.30, 0.95, memory_pressure)`;
   `fx = lerp(0.0, 1.0, io_rate if io_available else 0.0)`.
3. `flux = ema(flux, fx, dt, τ=2.2)`; surge asymmetric:
   `τ = 0.22 if fx > surge else 2.6`; `surge = ema(surge, fx, dt, τ)`.
4. `act_t = condition(t, flux, render)` (below). Hysteresis: adopt a new
   held target only when `|act_t − held| > 0.015`. Asymmetric EMA:
   `τ = 1.4 if held > activity else 3.2`. Rate limit:
   `act = clamp(ema, activity ± 0.30·dt)`.
5. Outputs: `agitation=ema(τ=1.8)`, `pulse=ema(τ=6.0)`, `density=ema(τ=3.5)`,
   `flux`, `surge`, `vitality=1.0`, `activity`, `stress=smoothstep(0.60,
   0.97, activity)`, `excite=ema(cpu_excite, τ=0.9)`,
   `tension=ema(clip(render), τ=2.0)`.
6. `_ema(cur,target,dt,τ) = cur + (target−cur)·(1−exp(−dt/τ))`;
   `smoothstep(e0,e1,v)` = clip → `t²(3−2t)`.

`condition()` (pure):
`cpu = clip(cpu_load/0.85)**0.75`;
`therm = clip((temp_c−55)/40)` (0.0 if no sensor);
`hot = smoothstep(0.55, 0.95, therm)`;
`mem = smoothstep(0.80, 0.97, memory_pressure)`;
`base = 0.70·cpu + 0.10·mem + 0.10·clip(flux) + 0.10·clip(render)`;
`activity_target = base + (1−base)·0.92·hot`; also returns
`(cpu_excite, hot)`.

`Physiology` field defaults and meaning: `core/signals.py::Physiology`
(agitation 0.15, pulse 0.20, density 0.35, flux 0, surge 0, vitality 1.0,
activity 0, stress 0, excite 0, tension 0).

### B.2 Source-clock and pulse propagation (FACT `mathforms.py::SourceBody.update`)

Per frame, in order:

- `rate = 1 + 0.5·agitation` (bounded 1.0×–1.5×: the only legitimate global
  speed-up); `time += dt·60·src.dt·rate` (`SOURCE_FPS = 60`).
- `dt` bookkeeping: if `dt>0`, `self._dt = dt` (drives persist exponent).
- `_jit += dt·2.3`; `wobble = 0.30·max(tension, 0.6·stress)·sin(_jit)`;
  `wave += dt·PULSE_HZ·(1 + 1.8·excite)·(1 + wobble)`
  (`PULSE_GAIN = 1.8`). Integrated phase — cadence changes bend the front,
  never teleport it.
- Species `_advance_aux(dt, p)` (below).
- `warmth = smoothstep(0.58, 0.95, pulse)`.
- Expression: `expr = min(1, 0.42 + 0.58·(0.30 + 0.70·density))`
  (`MIN_EXPRESSION = 0.42`); `live = max(64, int(n·expr))` — prefix of
  `_perm`, uniform thinning.
- Sample → perturb → seat → clip (§A.2/§A.5 order).

Pulse envelope (all f32 array math, `mathforms._pulse`):
`s = frac(phase − λ·u − species offsets)` with `λ = WAVES`;
`t1 = 1 − exp(−(1−s)/0.035)`; `f = exp(−s/0.10)·t1`;
afterpulse `t1 = exp(−((s−0.26)/0.045)²)`; `f += 0.30·t1`;
species `_shape` may multiply `f`; `f = clip(f,0,1)`.
`amp = 0.42 + 0.58·min(1, activity + 0.25·surge)`; `e = f·amp` (clip 0..1).
Hue: `h = s·(0.16·stress) + state_hue(activity)`; species `_tint` then
clip 0..1. Constants: `PULSE_LEAD=0.035, PULSE_TRAIL=0.10, AFTER_AT=0.26,
AFTER_W=0.045, AFTER_K=0.30`.

`state_hue(activity)` — piecewise-linear breakpoints
(`mathforms._HUE_ACT/_HUE_VAL`): activity (0, .22, .45, .65, .82, 1) →
hue (.05, .30, .50, .64, .80, .97).

Generic swell (`_swell`), zero at rest by construction:
`k = 0.030·agitation + 0.035·stress` (× optional gain), radial bulge
`x ← cx + (x−cx)(1+f·k)` about the **source frame centre**, same for y.

Persistence (`persist` property) — s03 only:
`base = min(0.88, 0.6235 + 0.16·expr)`; return
`base ** clamp(dt·60, 0.25, 4.0)` — retention is per **frame-duration**, so
the trail's length in seconds is frame-rate invariant.

### B.3 Per-species constants and responses (FACT `mathforms.py` per class)

| species | PULSE_HZ | WAVES | aux clock |
|---|---|---|---|
| s01 | 0.20 | 1.0 | `aux += dt·60·src.dt·3.1·(1+0.9·excite)` |
| s02 | 0.30 | 1.0 | `aux += dt·0.9` |
| s03 | 0.24 | 1.0 | `aux += dt·0.7` |
| s04 | 0.34 | 1.3 | `aux += dt·0.22·(1+2.0·excite)` |
| s05 | 0.30 | 1.0 | `aux += dt·60·src.dt·2.7·(1+0.6·excite)` |

- **s01** `_phase`: `s −= 0.07·lat`; if `stress>0.01`:
  `s −= 0.05·stress·sin(17u + 2.3·wave)`. `_tint` (if stress>0):
  `h += 0.10·stress − 0.16·stress·lat`. `_perturb(t=time)`:
  `s=clip(y/320,0,1)`; if agitation>0: `x += 7·agitation·s·sin(0.22y−aux)`;
  if stress>0: `x += 4·stress·lat·sin(0.5y + 1.7·aux)`;
  if pulse>0: scale about frame centre `g=1+0.09·pulse·sin(0.52t)`;
  swell; if surge>0.01:
  `w *= 1 + 3.4·surge·exp(−((s − (0.19t mod 1.2) − 0.1)/0.09)²)`.
- **s02** `_phase`: `lag = 0.5 + 0.22·stress·sin(aux)`; `s −= lag·grp`.
  `_tint` (if stress>0): `h += 0.08·stress − 0.13·stress·grp`. `_perturb`:
  `side = ±1` from `_mod2`; rotate about frame centre by
  `ang = side·0.22·agitation·sin(0.7t)`;
  `x += side·6·pulse·sin(0.41t)`; swell; if surge>0.01:
  `phase=(0.33t) mod 2`, `want = phase<1 ? −1 : +1`,
  `r=hypot(dx,dy)/frame_half`,
  `w *= 1 + 2.6·surge·exp(−((r−|phase mod 1|)/0.16)²)·(side==want)`.
- **s03** `_phase` (if stress>0): `lag = 0.12·stress·(0.6+0.4·sin(aux))`;
  `s −= lag·grp`. `_tint`: `h += 0.09·stress − 0.09·stress·grp`. `_perturb`:
  breath `g = 1+0.065·pulse·sin(0.62t)` on (dx,dy);
  `s = clip(dy/(1.1·frame_half), 0, 1)`; if agitation>0:
  `dx += 7·agitation·s·sin(0.16dy − 2.3t)`;
  **thermal shear**: `skew = smoothstep(0.88, 1.0, pulse)`; if >0, for the
  `dx<0` half: `dx *= 1+0.26·skew`, `dy += 14·skew·sin(0.05dx + t)`;
  swell; if surge>0.01:
  `w *= 1 + 1.5·surge·exp(−((s − (0.22t mod 1.1))/0.12)²)`.
- **s04** `_shape`: `sync = 0.85·smoothstep(0.35, 1.0, stress)`;
  per plume L∈{0..3}:
  `env[L] = 0.22 + 0.78·burst(aux − L·0.25·(1−sync))
   + 0.8·surge·burst(aux − ((L+2)%4)·0.25)` where
  `burst(z) = exp(−(z mod 1)/0.30)·(1 − exp(−(1−(z mod 1))/0.05))`;
  `f *= env[grp]`; the gain is retained in `_t1` for `_tint`:
  `h += 0.07·stress·(_t1 − 0.6)`. `_perturb`:
  `ang = 0.11·agitation·sin(0.9t + 1.9·mod4)` rotate about centre;
  if pulse>0: `g = 1+0.058·pulse·sin(0.55t)`; swell; if surge>0.01:
  `hot = int(0.6t) mod 4`; `w *= 1 + 2.8·surge·(mod4==hot)`.
- **s05** `_phase`: `s −= 0.09·lat`. `_tint`: `h += 0.10·stress −
  0.10·stress·u`. `_perturb`:
  `r = hypot(dx,dy)/frame_half`; if agitation>0:
  `amp = 9·agitation·clip(r,0,1.2)^1.6`, `ph = sin(5r − aux)`,
  perpendicular displacement `amp·ph·(−dy,dx)/max(hypot,1e-6)`;
  if pulse>0: `g = 1+0.065·pulse·sin(0.48t)`; if stress>0.01:
  rotate about centre by `ang = 0.14·stress·u²`; swell; if surge>0.01:
  `w *= 1 + 3.0·surge·exp(−(r/0.30)²)`.

**Rest-identity contract**: with `Physiology(agitation=0, pulse=0,
density=1.0, flux=0, surge=0)` and condition fields 0, every perturbation
term is exactly zero and the solved cloud equals the published equation to
within float32 noise (GATE 4 / PH5 enforce ≤1e-3 world units vs raw source).
Note: *colour* still pulses at rest; *geometry* does not.

`max_extent(org)` = farthest point with `weight > 0` from world origin.

---

## §C. Layout / visual invariants

### C.1 Resize contract (FACT `core/viewport.py`, `app.py::_on_snapshot`, `docs/ARCHITECTURE.md`)

- A resize produces exactly: `layout = resolve(w,h)` and
  `vp = Viewport.for_stage(*stage_content(layout))` — both fresh immutable
  values per frame. No rebuild, reset, reparent, allocation, buffer or
  texture churn. `rebuilds` counter must stay 0 forever; sim clock strictly
  monotonic under any resize sequence (probe JSONL asserts this).
- `Viewport.for_stage`: `scale = min(w,h)/1000` (**uniform by
  construction**), `cx,cy` = stage rect centre. `px(x,y) = (cx+x·scale,
  cy+y·scale)`; `length(l) = l·scale`. No cairo transform is ever set on the
  organism path.
- **The viewport is built from the observation glass (bezel aperture bay),
  not the raw stage** — the specimen is centred in the aperture (GATE 1
  asserts centre error 0 vs the glass, not the stage).
- The organism is clipped to the glass rect (`app.py::_on_snapshot`).

### C.2 Responsive states (FACT `core/layout.py`)

`classify(w,h)`: aspect = w/max(h,1); if aspect > 2.2 or < 0.72 → COMPACT.
Else effective = w; if h < 420 → effective = min(effective, 699); if
h < 560 → effective = min(effective, 1099); then `<700` COMPACT, `<1100`
INSTRUMENT, else ARCHIVE. Breakpoints exact: 699→COMPACT / 700→INSTRUMENT,
1099→INSTRUMENT / 1100→ARCHIVE.

Base type scales (`TypeScale`):

| state | title | specimen | subtitle | label | value | micro | tracking |
|---|---|---|---|---|---|---|---|
| COMPACT | 9.5 | 13.0 | 8.0 | 8.0 | 15.0 | 7.5 | 1.1 |
| INSTRUMENT | 10.5 | 17.0 | 9.5 | 9.0 | 21.0 | 8.5 | 1.5 |
| ARCHIVE | 12.0 | 22.0 | 11.0 | 10.0 | 26.0 | 9.5 | 2.0 |

Six-module states replace the scale with `_machine_type(s)`: `k =
clamp(min(sx,sy), 0.5, 1.9)` times `_REF_TYPE = {title 18, specimen 26,
subtitle 16, label 14.5, value 30, micro 12.5 (floor 7.0), tracking 2.6
(floor 1.0)}`.

COMPACT: chassis shown iff `w ≥ 420 and h ≥ 330` (margins 40/1448 · w,
30/1086 · h); header ≤ 38px (≤16% content h); strip ≤ 52px (≤20%); no
selector bank, no footer; keys 1–5 / arrows still switch.

### C.3 Six-module composition (FACT `core/layout.py::_six_module`, REF_* constants)

Reference rectangles (px, from `references/Abbysal_Final.png` 1448×1086):
`REF_CHASSIS (40,30,1368,1028)`, `REF_HEADER (55,55,1340,145)`,
`REF_STAGE (52,222,770,568)`, `REF_SELECTOR (52,800,770,152)`,
`REF_RACK (838,222,534,730)`, `REF_FOOTER (48,962,1350,78)`.
`sx = C.w/1368`, `sy = C.h/1028`, `s = min(sx,sy)`. Header/footer from REF_*
mapped through X()/Y(). Rack width manufactured from the **height** scale
(`REF_RACK[2]·sy`), right margin = 36·s, gap from REF spacing; observation
column absorbs the rest but its aspect is capped at 1.62 (`_STAGE_MAX_ASPECT`),
after which the rack takes the width; rack share clamped to [0.30, 0.44] of
chassis width. Selector height =
`min(col_w/ (1400/264), REF_SELECTOR[3]·sy·1.30)` (`_BANK_ASPECT =
1400/264` must equal `skin.modules.SELECTOR.aspect`). Stage bottom =
controls.y − sel_gap. Measured agreement with the reference is ≤ ~4.8 px on
the worst module edge (`docs/VALIDATION.md` "Reference agreement").

Layer composition (`ui/console.py`, `app.py`):
- **layer_under** (static texture): background, module 01 shell, glass fill,
  graticule/furniture.
- **organism** (the ONLY per-frame raster): clipped to glass.
- **layer_over** (static texture): module 03 bezel, 02 header, 04 rack,
  05 selector plate, 06 rail, all static type, constant light emitters.
- **regions** (re-rendered only on their own inputs): clock (once/second),
  rack/condensed readouts (per telemetry sample or displayed-FPS change),
  selector keys (on input). Region = static composite of its snapped rect +
  live pass + light, cache key includes its own inputs.
- Texture handoff: `Gdk.MemoryTexture` `B8G8R8A8_PREMULTIPLIED`, bounded
  texture cache (≤8), a texture is never mutated; **the point-field output
  surface must be a fresh allocation every frame** (GSK snapshot rule —
  rewriting a snapshotted surface aborts Cairo; this is the crash GATE 0
  caught).

### C.4 The six modules and multi-band slicing (FACT `skin/modules.py`)

Registry (sw×sh, stretch bands, bays, screws): SHELL 1600×1172 (bands_x
(180,1420); bands_y (190,360),(470,725),(835,1000); bay "face"); HEADER
1800×185; OBSERVATION 1300×948 — bays `aperture (128,128,1044,697)` and
`glass (120,121,1059,709)`; RACK 900×1213 with rows at `_ROW_Y =
0,294,589,884` and per-row bays `r{i}_plate/title/graph/numeric/meter/state/
lamp0..3/row`; SELECTOR 1400×264 with `SEL_PITCH = 200.6596`, `SEL_X0 =
294.0374`, wells `(cx−66.5, 65.5, 133, 138)`, keys `(cx−80.539, 36,
161.079, 168)`, ledges `(cx−82, 207.5, 164, 26)`, rocker/mode bays; FOOTER
1800×93 with seven bays + terminal + badge. Screws per module as declared;
the shell declares none; no two modules may place a screw within 1.6
summed-radii (P7).

Scaling: **multi-band slicing** — fixed parts scale by one uniform factor
`k = min(w/sw, h/sh)` (default), declared stretch bands share the remaining
length proportionally to source length; fallback to a single uniform factor
if band slack < 15%·k. Bays map through the same piecewise `AxisMap` as the
pixels (`Placed.bay/map_rect/point`); tiles are clipped, translated, scaled
with `FILTER_GOOD` + `EXTEND_PAD`, edges snapped outward by 0.02px to avoid
seams. Shell drawn at `k = 0.17 · min(C.w/1368, C.h/1028) · 1600/1368`
(`console._SHELL_K`; COMPACT alternative `max(0.10, min(C.w,C.h)·0.030/97)`).

### C.5 9-slice rules and what is NOT 9-sliced (FACT `skin/surface.py`, `skin/catalog.py`)

9-sliceable frames (insets L,T,R,B and content pads in source px):
`observation_bezel (150,140,146,144 | 96,85,100,93)`,
`segment_housing (120,70,120,95 | 87,48,87,69)`,
`segment_housing_wide (74,47,74,43 | 55,35,55,32)`,
`graph_well (72,44,72,110 | 51,30,52,44)` (pad_b is bevel only, not the
caption ledge), `meter_trough (53,31,53,42 | 39,23,39,31)`,
`aux_frame (74,50,74,111 | 55,37,55,82)`, `plate_recessed (24,18,24,18 |
32,30,26,28)`.

Rules: corners 1:1; edges stretch one axis; centre stretches freely.
`_shrink`: one factor both axes, `k = clamp(min(w/sw,h/sh), 0.45, 1.0)`
(`MIN_BORDER_SCALE`), further limited so borders never exceed
`1 − 0.34` of the panel (`MIN_MIDDLE_SHARE`). Content pads are
**source-space** values mapped through the slice: pad ≤ inset shrinks with
the border; pad > inset rides the stretch (`_map_pad`). Borders and pads
share one factor — never scale one without the other.

**NOT 9-sliced** (arrangements, assembled at draw time): chassis (shell
module), header rail, footer rail, telemetry rack, module shell, selector
bank — plus: thin rails (footer/condensed) are procedural
(`console._rail`), and `skin/fascia.py` panels are compact fallbacks only.

Caches (derived pixels only; keys are the complete input set): scaled
panels ≤64, keyed `(name, insets, w, h, device_scale)`; sprites stretched
to exact w×h with aspect preserved where required (`draw_sprite_fit`);
`draw_sprite_rot90` for handle rails.

### C.6 Observation chamber recess geometry & field furniture (FACT `ui/console.py`)

- Glass = `OBSERVATION.place(stage).bay("aperture")`; live content clipped
  to it; bezel drawn **after** the organism (metal occludes).
- Glass fill gradient (linear, y): stops 0 `(0.024,0.040,0.050)`, 0.55
  `(0.014,0.026,0.034)`, 1 `(0.020,0.034,0.043)`.
- `field_area` = glass inset `5.5%` x / `7.5%` y. `Scope`: `cx,cy` = glass
  centre; `rad = min(g.w,g.h)·0.46`; axes clamped to ±0.47 of glass.
- Graticule (`SCOPE = (0.36,0.52,0.62)`): dotted rings at k ∈ {0.22, 0.42,
  0.62, 0.81, 1.0}·rad, dash `[1.6, 5.0]`, alpha 0.36 (0.55 outermost),
  1px, cached per radius (quantised to 1px, ≤8 entries); crosshair axes
  (alpha 0.62) starting after the ruler gutter; ticks every 0.2·rad (len 3,
  every 5th 5.5, alpha 0.80); cardinal crosses at the design radius
  (k = max(3, rad·0.028)); cardinal labels X+/X−/Y+/Y−; corner brackets
  (size = 3.5% of glass min side, alpha 0.7); RADIUS ruler: steps
  `(1.25,1.00,0.75,0.50,0.25,0.00,0.25,0.50,0.75,1.00,1.25)` mm, caption
  `"R (mm)"`, drawn only when glass ≥ 360×250 (med); cardinals need
  ≥260×200.
- Zones graduate by glass size: `med = w≥360 and h≥250`, `big = w≥560 and
  h≥330`.

### C.7 Lighting (FACT `core/lighting.py`)

Emitters collected per frame, painted once, `OPERATOR_ADD`, **no
intermediate group**. Caps: per-emitter strength ≤ `MAX_STRENGTH=0.30`,
radius ≤ `MAX_RADIUS=240` px, skip if `radius ≤ 1` or `strength ≤ 0.002`;
alpha pre-scaled by `MAX_TOTAL=0.42`. Falloff = cached A8 mask per
quantised radius (step 8px, ≤24 entries): radial stops (0→1.0, 0.45→0.38,
1→0). Emitter colours: CYAN (0.42,0.78,0.92), CHARTREUSE (0.72,0.88,0.28),
AMBER (0.96,0.62,0.18), RED (0.90,0.26,0.20); `state_rgb`:
nominal→CHARTREUSE, active/standby→CYAN (default), warning→AMBER,
critical→RED. `glow(rect, rgb, strength, spread=0.62)`:
radius = max(rect.w,rect.h)·spread. Actual emitters: glass glow 0.07/0.40;
clock 0.055/0.55; numeric displays 0.085/0.5; LIVE lamp 0.18; rack lamps
only when warning/critical (0.14); active key lamp 0.10; mode key 0.10
active / 0.10 error / 0.06 armed.

### C.8 Seven-segment rendering (FACT `ui/segment.py`)

- Procedural Cairo paths, no font. Unit cell geometry with chamfered
  segments (`_seg_poly`, `_cell_segments`); shear about the cell's vertical
  centre.
- `SegmentStyle` defaults: thickness 0.118·h, slant 0.045, gap 0.030,
  aspect 0.560 (w/h), tracking 0.190, ghost 0.070, bloom 0.30, alpha 1.0.
- Moods: `CYAN (0.60,0.86,0.95)`, `AMBER (0.98,0.64,0.16)`, `RED
  (0.92,0.26,0.22)`, `PALE (0.84,0.90,0.95)`.
- Draw order per glyph: ghost pass (unlit segments at `ghost·alpha`) →
  bloom pass (lit segments stroked, width `0.9·t`, alpha `0.16·bloom·a`,
  ROUND join) → lit fill. Narrow glyphs (`. : / ' ° % space`) have widths
  {0.34, 0.34, 0.58, 0.30, 0.46, 0.66, 0.34}·h and bespoke geometry.
- `GLYPHS` segment map (canonical order a,b,c,d,e,f,g) for digits 0–9,
  `-`, `_`, and letters A C E F G H J L P S U Y b c d h n o r t u.
  Unsegmentable = `KMQRVWXZ` (unit layer only). Unsupported chars are
  skipped, never raise; `height < 2.0` draws nothing.
- Units: `draw_unit` at `scale=0.54` of digit height, bottom-aligned,
  thickness ×1.10, tracking ×0.9, ghost ×0.55, bloom ×0.8, alpha ×0.92.
- `measure` returns advance width including tracking; `fit_height` =
  largest height fitting a box; `draw_right` right-aligns (readouts pinned
  while digits change).
- Clock text `%H:%M:%S` centered in the header clock bay; the `:` is a
  narrow two-dot glyph.

### C.9 Selector behaviour (FACT `ui/selector.py`, `ui/console.py::control_geometry`)

- Key plate family: `specimen/key_{01..5}_{inactive|active}`; authored
  aspect `KEY_ASPECT = 256/267`; plates never tinted/recoloured — the
  active plate is swapped.
- Geometry sources: six-module states use `from_module(SELECTOR.place(...))`
  — key bays from the trued module wells, one exact pitch
  (`SEL_PITCH`), keys keep authored aspect by construction, `SEAT_DROP =
  3.0/168` of key height applied to `key_y` (hit test, draw, labels and
  lamp points all read `key_rect`). COMPACT has no bank.
- Fallback `layout(box)`: wall 5.5%, ledge 14.5% (dropped below 8px), gap
  17% of key width, span ≤ 78% of box width, uniform shrink only, centred;
  aux seats (cycle rocker aspect 192/113 at 33% key height; mode key aspect
  160/226 at 55%) fitted only where spare metal allows (thresholds 1.20× /
  1.60×).
- State priority: `disabled > pressed > latched(active) > focus > idle`;
  `plate(state) = "active"` iff pressed or latched. Pressed draws mechanical
  travel `PRESS_TRAVEL = 0.022` of key height (offset, not repaint);
  disabled draws at alpha 0.42. Momentary press feedback lasts
  `0.13 s` on a wall clock (`app.PRESS_FEEDBACK_S`).
- Hit testing (`SEL.hit`): key plates only, not the trough; pitch includes
  the gap — clicks between keys select nothing. `hit_controls`:
  keys → `("key", i)`; mode key rect; cycle rocker split at its centre
  (`("cycle", −1|+1)`). Drawn geometry == clickable geometry by
  construction (one pure function).
- Focus (hover) tracked in `ConsoleModel.focus`; ledge identifiers
  (`"01  AQS-0042"` style, eliding to archive code then number) are
  **service view only** — drawn when `diag` (F1) is on; production shows
  clean metal.
- Mode key states cycle `("inactive","armed","active","error")` on click or
  `m/M`; default `"armed"`. Cycle rocker states: neutral/prev/next.

### C.10 Graduated states (FACT `docs/ARCHITECTURE.md` "Graduated states")

| | field annotation | telemetry bay | header |
|---|---|---|---|
| ARCHIVE | ruler + 4 blocks + scale bar | full bay incl. status column | 3 compartments + status rail |
| INSTRUMENT | ruler + specimen block | bay with graph well | 3 compartments + status rail |
| COMPACT | brackets + graticule | condensed 4-up rail | one line + LIVE + clock |

Component degradation is by **dropping layers**, never proportional
shrinking; type floors: `MIN_TEXT = 7.0` px (chrome `MIN_PT`), below that
the element is dropped/elided (see §D).

### C.11 Point-field raster contract (FACT `organism/render.py`, `organism/pointfield.py`)

- Deep-field wash first: disc radius 430 world units, CYAN_DEEP radial
  stops (0→α0.070, 0.18→0.048, 0.45→0.024, 0.70→0.010, 0.87→0.003,
  1→0.000), cached per quantised radius (step 6px, ≤6).
- Field buffer: `R_FIELD = 464` (WORLD_RADIUS+4); `side_px = 2·R_FIELD·
  vp.scale`; `res = min(1, 760/side_px)` (`MAX_FIELD_PX = 760`);
  `side = int(side_px·res)+2`; skip if `side_px < 8` or no live points.
- Three f32 accumulators per (species,w,h): density `acc`,
  excitation-weighted `acc_e`, hue-weighted `acc_h` (≤3 entries, LRU).
  Points outside dropped, never clamped. `persist` decay applied to all
  three together.
- Ink: `eff = vp.scale·res`;
  `ink = min(src.ink·1.20 / max(eff²·3.4, 0.02), 5.0)`;
  if `persist > 0`: `ink *= (1−persist)**0.5 · 1.55` (trail steady-state
  normalisation). Lit threshold `thr = 0.4/255/max(ink,1e-6)`.
- Colour map on lit pixels only (`paint_physio`):
  `a = v + e·1.15`; `a = 1−exp(−ink·a)`; `a = a**(1/2.2)` (knee);
  `m = clip(v·ink·0.55)`; `x = clip((e/max(v,1e-9))·1.6)`;
  `hue_idx = clip(round((acc_h/e)·255))`;
  `c = lerp(lo, hi, m)` then `c = lerp(c, LUT[hue_idx], x)`, premultiplied
  by `a·255`, packed BGRA (cairo ARGB32). New output surface every frame.
- Palette `PULSE_PALETTE` (position, hex): (0.00 "4d9eff") (0.16 "8ce0f8")
  (0.34 "4cf0c8") (0.50 "5cff8a") (0.64 "b4f25a") (0.80 "ffc445")
  (1.00 "ff7a1e"); LUT = 256 entries, linear interp per channel, f32.
  No purple, no red (PH7).
- Thermal cast on the resting body only: `warm = warmth·0.22`;
  `lo = mix(CYAN_DEEP, (0.34,0.20,0.06), warm)`, `hi = mix(CYAN, AMBER,
  warm)` — the stress colour itself travels with the pulse, never a
  whole-body tint.
- Blit at `(vp.cx − side/2·(1/res), vp.cy − side/2·(1/res))`,
  `FILTER_GOOD` when upscaled.

### C.12 Background & chrome primitives (FACT `ui/chrome.py`)

`draw_background`: flat ABYSS `#05070b`, then a vertical vignette toward
FRAME `#0d131c` — 6 steps, quadratic alpha (top 0.50·(1−f)² over 40% span,
bottom 0.38·(1−f)² over 32%), cached per height (≤12). Hairlines 1px on
half-pixels; meters 2px (RULE track, CYAN fill / AMBER warn, α0.88);
nominal dot LIME α0.9. Warn level 0.85 (FPS never warns); FPS meter
full-scale 75.

### C.13 Diagnostics & calibration (FACT `ui/debug.py`, `organism/render.py::draw_calibration`)

F1 overlay: JetBrainsMono 11px, LIME 0.78 on 0.88 dark box, bottom-left;
reports WIDGET/SURFACE/STATE/STAGE/RSCALE/ISOTROPY/FPS/SIM (incl.
`frames`, `resizes`, `rebuilds`)/PHYS/TELEM. F2 calibration: lime circles
at WORLD_RADIUS and 250 (α0.26/0.17), crosshair (α0.13), four spokes at
45° (α0.19), current extent circle (α0.38). Both are permanent product
features.

### C.14 App-level facts the port must keep (FACT `app.py`)

- Window: title "Abyssal Organism Monitor", default 900×700, APP_ID
  `dev.abyssal.OrganismMonitor`, `NON_UNIQUE`, one widget total.
- CLI: `--width --height --seed(20260920) --specimen(0) --debug
  --calibration --probe FILE --quit-after S --qa-temp C --fps-cap`.
- Keyboard: `1–5` select; `Right/Down/n/N` next; `Left/Up/p/P` previous;
  `m/M` mode cycle; `F1` debug; `F2` calibration; `f/F/F11` fullscreen;
  `q/Q/Escape` quit.
- Frame pacing: phase accumulator on the frame clock; interval 1e6/fps µs;
  tolerance `min(interval/2, 4000µs)`; first tick `dt = 1/60`; otherwise
  `dt = clamp(Δt, 0, 0.1)`. Cadence: 60 focused / 30 unfocused / 0
  suspended (`--fps-cap` overrides; cadence 0 = no simulation, no draw).
- Simulation in the tick, composition in snapshot; per-frame model refresh:
  `phase = (org.time·0.31) mod 2`; `rotation = 0.08 + 0.42·agitation`;
  behavior = AGITATED (>0.66) / ACTIVE (>0.33) / STABLE;
  `magnification = max(0.1, vp.scale·10)`; `field_mm = min(stage_w,
  stage_h)/vp.scale/400`.
- FPS accounting: windowed mean over ≥0.5s windows; `frame_ms` fed to
  history; probe JSONL fields exactly as `app._log_probe` (event, wall, w,
  h, state, stage, scale, iso_err, clips, sim_time, frames, resizes,
  rebuilds, fps, draw_ms, sim_ms, tel_ms, draw_avg_ms, cadence, active).

---

## §D. Typography invariants

### D.1 Faces and where they appear (FACT `core/theme.py`, `ui/chrome.py`, `ui/console.py`, `ui/fonts.py`, `docs/VALIDATION.md`)

| role | family | weight | where |
|---|---|---|---|
| major display titles | **Astro** (`assets/fonts/astro.ttf`) | NORMAL | "ABYSSAL ORGANISM MONITOR", specimen scientific name, hero epithet |
| technical / support | **Microgramma** (`assets/fonts/microgrammanormal.ttf`) | NORMAL | header status rail, footer archive rail, rack channel titles/captions, state wells, selector service labels, field microcopy |
| numerics | procedural seven-segment (§C.8) | — | clock, telemetry values, units |
| micro labels, axis ticks, body | JetBrainsMono Nerd Font (fallback Noto Sans Mono) | NORMAL / MEDIUM | graph scales, rulers, axis values, readout labels/values |

Family strings (exact): `_FAMILY = "JetBrainsMono Nerd Font,Noto Sans
Mono,monospace"`; `_FAMILY_HERO = "Astro,JetBrainsMono Nerd Font,monospace"`;
`_FAMILY_LABEL = "Microgramma,JetBrainsMono Nerd Font,monospace"`.

### D.2 Measurement and fitting rules (FACT `ui/chrome.py`)

- Sizes are **absolute** (`set_absolute_size(size·Pango.SCALE)`), i.e.
  device-independent px.
- `MIN_PT = 7.0`: never render smaller; below it elements are dropped.
  `CAP_RATIO = 0.74` fallback cap-height; real caps measured from "H" ink
  extents, cached per (size, weight, family).
- Widths: visual advance = layout width **minus one trailing tracking**;
  memoised (`_TW_CACHE` ≤4096, cleared at limit).
- Fitting: `_fit` shrinks in 0.5px steps to the floor; `_elide` binary
  searches the longest prefix + single `…`; `fit_first` picks the longest
  whole candidate (labels shown whole or in a deliberate short form —
  never ellipsised mid-word; the motto "OBSERVE · UNDERSTAND · EXTEND" is
  shown whole or not at all).
- Bay typography rules (`ui/console.py`): every text region = physical bay
  → proportional padding with pixel floors (`bay_inner`: 5%/11.5%, floors
  3/2px, caps 30%) → defined baseline from the bay's box → declared
  alignment → responsive elision (shrink to floor, then ellipsize; for
  observation readings, drop the row rather than truncate).
- Baselines: `bay_line` centres on the bay by cap height; `bay_pair`
  key-left/value-right on one line; `bay_stack` key above value on fixed
  baselines (value wins if the recess is too short).
- Positions round to whole pixels (stops microcopy shimmer).

### D.3 Static text caching rules (FACT `ui/console.py::_show`, layer caches)

- Rendered glyph-run cache `_TXT` (≤700, LRU): key =
  (text, size(rounded 2dp), weight, tracking(3dp), align, max_w(1dp),
  rgb(3dp×3), alpha(3dp), **device scale**, family). A miss rasterises once
  into a padded hidpi surface; hits blit. Key is the complete input set —
  a hit is always identical to a fresh render (GATE 8 asserts warm==cold
  byte-identical).
- Font descriptions, letter-spacing attr lists, cap heights and background
  gradients are cached per inputs; no font face is created per frame.
- Static layers `_UNDER_CACHE`/`_OVER_CACHE` (≤2 each), keyed by
  `_layer_key` = (w, h, state, species.key, active, behavior, show_chassis,
  show_footer, show_controls, show_status, mem_total(1dp), hidpi, diag);
  `layer_over` adds `tel.mem_total_gb` (memory graph scale is the installed
  RAM). Region cache ≤8; trace cache ≤16 keyed (ring id, w, h, ring
  .version, scale lo/hi, rgb, hidpi); text-width cache ≤4096; ring cache ≤8.
- Rust may implement caches differently **provided** output is identical
  (G-R21) and budgets hold (G-R43); the *semantics* (which text is static
  vs live, region invalidation triggers) must match: clock → 1 Hz; rack →
  telemetry sample or displayed-FPS change (key includes rounded telemetry
  values + per-channel ring versions); keys → input only.

### D.4 Font installation (FACT `ui/fonts.py`)

`ensure_user_fonts()` copies both TTFs to
`~/.local/share/fonts/abyssal/` (only when SHA-256 differs) and runs
`fc-cache -f <dir>` (30s timeout, best-effort, fail-soft to mono fallback),
**before the first Pango font map is created**, exactly once per process.
Rust must preserve: bundled assets, same destination, same copy-if-changed
semantics, same ordering constraint (before the first font map), same
graceful fallback.

---

## §E. Telemetry semantics

### E.1 Cadence and history (FACT `app.py`, `telemetry/history.py`)

- `TELEMETRY_HZ = 5.0` (`TELEMETRY_INTERVAL_MS = 200`); `/proc`+`/sys` read
  **only** in this timer, never at render rate; the timer runs regardless
  of draw cadence (including suspended windows).
- History: `SPAN_S = 60.0`; 4 channels `("cpu","thermal","memory","frame")`;
  `cap = round(60·5) = 300` float32 samples each; `push(cpu_pct, temp_c or
  NaN, mem_used_gb, frame_ms)`; O(1), allocation-free; `version` bumps per
  push (renderers cache traces on it).
- `window(cols)`: full span reduced to ≥2 column means (NaN-aware; missing
  history renders as NaN so traces start where data starts), cached index
  table per col count (≤8).
- Graph scales are **fixed, never autoscaled**: CPU 0–100 %, thermal
  20–100 °C, memory 0–installed-GB, frame 0–50 ms with the 16.7 ms
  (1000/60) budget dashed line (`console._graph_scale`).
- Graph furniture: 4 horizontal divisions (α0.30), 6 dashed 10 s divisions
  (α0.20, dash [1.5,3.0]), scale values inside the plot's left edge,
  caption + "60 s" on the caption ledge. Trace: filled gradient (α0.26→
  0.03 top-to-bottom) + 1–1.8px stroke α0.95 + head dot
  (r = max(1.3, 3.5%·h)); NaN columns break the run.

### E.2 Exact fields read (FACT `telemetry/source.py`)

- **CPU** `/proc/stat`, first line only, must start `"cpu"`: fields
  `user nice system idle iowait irq softirq steal guest guest_nice`;
  `idle = f[3] + (f[4] if len>4)`; `total = sum(fields)`; first call
  returns 0.0; `load = clip01((Δtotal − Δidle)/Δtotal)`; `cpu_pct =
  load·100`; `Δtotal ≤ 0` → last-known-good.
- **Memory** `/proc/meminfo`: `MemTotal:` and `MemAvailable:` (kB values);
  `pressure = clip01(1 − avail/total)`; `used = total − avail`; GB =
  `kb / (1024·1024)`. If `MemAvailable` is absent, it defaults to
  `MemTotal` (pressure 0, used 0); if `MemTotal` is absent or zero →
  last-known-good.
- **Temperature** `/sys/class/hwmon/hwmon*/` (glob, sorted) → `name`;
  `temp*_input` (sorted) with optional `temp*_label`. Priority list
  `[("k10temp",["tctl","tdie"]), ("coretemp",["package id 0"]),
  ("k10temp",[]), ("acpitz",[]), ("amdgpu",[])]` — first chip match with a
  preferred label substring (case-insensitive) wins; chips with empty
  label prefs accept their first input; then a global fallback to any
  `temp*_input`. `temp_c = raw/1000`; normalised `(c−30)/65` clipped
  0..1. Display tag = chip name uppercased; note =
  `temp=<chip>:<label or temp1>` (+" (fallback)" on the global fallback).
  Sensor gone: keep last value + label, `temp_available=True` only if a
  last value exists; never raise.
- **I/O** `/proc/diskstats`: split fields; name = p[2]; skip names starting
  `loop/ram/zram/dm-`; skip partitions (`name[-1].isdigit()` unless the
  name starts with `nvme`; `nvme*n p*` skipped if it contains `"p"`); bytes
  = `(int(p[5]) + int(p[9]))·512` (sectors read + written).
  `/proc/net/dev`: skip 2 header lines and `lo`; bytes =
  `int(p[0]) + int(p[8])` (rx+tx). Rate = `min(1, (Δblk+Δnet)/Δt /
  40e6)` (`_IO_FULL_SCALE`); Δt = monotonic; first sample →
  `(0.0, unavailable)`; `Δt ≤ 0` → last-known-good; `io_mb_s`,
  `net_mb_s` = per-second deltas / 1e6.
- **Failure contract**: `sample()` never raises; every sub-sampler
  degrades to last-known-good (or zero/unavailable); notes string =
  `cpu=/proc/stat mem=/proc/meminfo <sensor note>`; worst-case returns a
  default `Telemetry(notes="telemetry construction failed")`.
- Display derivations elsewhere must agree: e.g. rack thermal thresholds —
  `lv > 0.80` → ELEVATED/AMBER, `lv > 0.94` → AT LIMIT/RED; cpu warn
  `> 0.85` → AMBER; memory warn `> 0.88`; frame `< 50` FPS → REDUCED/AMBER
  (`console._channel_values`).

### E.3 Unfocused / hidden behaviour (FACT `app.py::cadence`, P12, perf_live)

Focused (window active): 60 FPS. Visible but unfocused: 30 FPS. Suspended
(`Gdk.ToplevelState.SUSPENDED` — other workspace, minimised, occluded):
0 — no simulation, no draw; resume uses clamped `dt` so nothing teleports.
Telemetry timer continues. Measured envelope (docs/VALIDATION.md, 8s
dwell): instrument focused 20.7% CPU / 61.0 FPS; unfocused 12.2% / 30.3;
hidden 0.1–0.2% / 0. The FRAME module reports the *presented* rate
honestly.

### E.4 `--qa-temp` (FACT `app.py::_on_telemetry`)

QA substitution of the temperature reading only:
`temp_c = opts.qa_temp`, `temp_available = True`,
`temperature = clip01((qa_temp − 30)/65)`; every other channel stays live.
Required for deterministic thermal-state screenshots.

---

## §F. Gate list for the Rust port

Legend — **Maps to**: existing Python gate to reproduce. **New**: parity
gate introduced by this spec. Every gate is executable and must exit
non-zero on failure. Rust gate harness lives in the Rust workspace
(e.g. `xtask`/integration tests); fixture export tooling (§G) is Python
side. Unless stated, tolerances are **[proposal]** calibrated at first
cross-render; the *invariants* are FACTS cited above.

### Organism core

- [ ] **G-R1 — Equation vector fixtures** *(New)*. For each source s01–s05,
  at t ∈ {0, 10·dt, 100·dt, 1000·dt} (plus one t ≥ 300 rad), evaluate the
  equation over the canonical index array (both the full set and the first
  1000 indices) and compare Rust f32 output against Python golden vectors:
  max |Δ| ≤ 1e-3 source units (400-unit canvas) on finite samples;
  identical NaN/Inf mask; identical weight values. Also golden vectors for
  the *seated+clipped* world coordinates at rest (§A.5 applied) with
  `Physiology()` at rest and `density=1.0`: max |Δ| ≤ 1e-3 world units.
- [ ] **G-R2 — Creature standalone validation renders** *(Maps
  `qa/creature_validate.py`)*. Render each equation in its own 400×400
  canvas, 90 frames, original dt/ink/persist, white-on-9/255; compare to
  `docs/creature_validation/*.png` golden: mean |Δ| ≤ 2/255, p99.9 ≤
  10/255, and s03's trail (persist) present after 90 frames.
- [ ] **G-R3 — Permutation & anatomy fixtures** *(New)*. For seeds
  {20260920, 20260920+i for i∈0..4, 1234+i}: assert Rust `_perm`, `_mod2`,
  `_mod4`, `_u`, `_lat`, `_grp` match the exported NumPy arrays exactly
  (integer index arrays; f32 for u/lat/grp/mods). Prefer consuming the
  fixture at runtime over reimplementing PCG64.
- [ ] **G-R4 — Physiology model unit fixtures** *(New)*. Golden traces:
  drive `PhysiologyModel` for 600 frames at dt=1/60 through
  QUIESCENT/NORMAL/STRESSED samples (`qa/physiology_gates.py::TEL_*`) and a
  step sequence; assert per-frame f64 outputs (all 10 Physiology fields)
  match to ≤ 1e-12 relative (1e-15 target). `condition()` outputs exact to
  1e-15 on a 21-point telemetry grid.

### Physiology behaviour

- [ ] **G-R5 — Condition model semantics** *(Maps PH1)*: `state_hue`
  monotone, hue(0) ≤ 0.12, hue(1) ≥ 0.90; cool 60% load → thermal stress <
  0.05; 92 °C idle → stress > 0.80; busy-cool activity ∈ (0.30, 0.80);
  sub-deadband (0.015) jitter never adopts a new held target; per-frame
  activity movement ≤ 0.30·dt; rouse faster than calm.
- [ ] **G-R6 — Five species × three states** *(Maps PH2)*: finite fields;
  extents ≤ 460; excitation and hue within 0..1; mean hue and activity
  strictly ordered QUIESCENT < NORMAL < STRESSED for every species; stress
  band ordering (stressed > 0.9 > normal).
- [ ] **G-R7 — Pulse kinematics** *(Maps PH3)*: idle wave speed =
  `PULSE_HZ·(1+1.8·excite)` within 12%+0.02; CPU raises cadence > 1.5×;
  excitation-profile correlation travel within
  `[0.6·v·0.3−0.02, v·0.3+0.06]`; identical drives → bitwise identical
  state arrays and `wave`.
- [ ] **G-R8 — Species personalities** *(Maps PH4)*: s01 tips lag spine
  0.02–0.12 and stressed front ragged (≥0.02) with zero modulation at
  rest; s02 alternation lag 0.35–0.65, stress desync + one-sided warm;
  s03 mirror symmetric at rest (≤0.01), heat side-lag + warm split; s04
  chase visits 0→1→2→3 (mod-4 sequential), stress pulls flare spread to <
  0.6×; s05 ribs lag shaft 0.03–0.16, stress curl ≥ 0.01 rad.
- [ ] **G-R9 — Rest identity** *(Maps PH5 + GATE 4 tail)*: at rest
  (density=1.0, everything else 0) geometry is bitwise independent of
  pulse phase (wave/aux/jit perturbed); resting geometry equals the raw
  seated source equation ≤ 1e-3 world units; resting colour subtle (0 <
  mean excitation < 0.25) at hue = state_hue(0); zero telemetry → zero
  (activity, stress, excite, tension) with idle floors intact
  (agitation→0.12, pulse→lerp(0.15,0.80,0.35)).
- [ ] **G-R10 — Render/resize safety** *(Maps PH6)*: rendering at 4
  viewports never mutates organism/physiology state (bitwise); same size +
  same state → identical raster (trailing species rendered to steady state
  first).
- [ ] **G-R11 — Pulse palette** *(Maps PH7)*: 256-entry LUT from the 7
  stops; no purple (r>0.45 ∧ b>0.45 ∧ g<0.35) and no red (r>0.80 ∧
  g<0.25) entries; starts blue (b>0.5), ends warm (b<0.5, r≈1), has a
  green band (g>0.7 in the middle).

### Geometry, layout, resize

- [ ] **G-R12 — Viewport geometry** *(Maps GATE 1)* over the 20-size sweep
  incl. hostile aspects: isotropy error ≤ 1e-6 px; centre error vs the
  **glass** = 0; shape invariance (project→unproject identity) ≤ 1e-9
  world units; no clipping at hot-state max extent; breakpoints exact at
  699/700 and 1099/1100.
- [ ] **G-R13 — Responsive classification + type scales** *(Maps GATE 2
  heritage + P11)*: `classify` matches Python on a grid incl. aspect
  extremes (aspect <0.72 or >2.2 → COMPACT; h<420/h<560 demotions);
  COMPACT shows no selector bank and no key hit areas; TypeScale values per
  state equal §C.2 (incl. `_machine_type` clamp 0.5–1.9 × `_REF_TYPE`).
- [ ] **G-R14 — Six-module reference seating** *(Maps `qa/compare.py`)*:
  module rects (header/stage/selector/rack/footer) at 1448×1086 within 5px
  of `REF_*`; rack right margin within 2px of 36·s; at 5 canonical sizes:
  rack top == stage top, rack bottom == selector bottom, rack inside the
  shell, rack never overlapping the observation column.
- [ ] **G-R15 — Resize invariance** *(Maps `gate_resize_invariance`)*: 400
  frames, one organism rendered at a random size every frame (seed 7), the
  other never rendered: `|dx| = |dy| = |Δtime| = 0` exactly.
- [ ] **G-R16 — Headless performance budget** *(Maps GATE 3)*: full frame
  (sim + organism + console, warm caches) at 420×340, 900×700, 1400×860,
  1920×1080, 2560×1600: hard fail if 900×700 total > 16.6 ms; report
  Python baselines (docs/VALIDATION.md: 1.29–6.20 ms) — flag if Rust
  exceeds 1.5× the Python measured value at any size **[proposal]**.

### Species, skin, selector, caches

- [ ] **G-R17 — Species bounds** *(Maps GATE 4)*: 400 mixed-drive frames
  per species (incl. periodic all-1/all-0 spikes): extent ≤ 460; all
  arrays finite; pairwise morphological signature distance ≥ 0.35 (joint
  radius×|angle| histogram, 8×6 bins, weighted); rostrata mirror asymmetry
  ≤ 0.25 at rest and > 2× at thermal limit.
- [ ] **G-R18 — Skin / 9-slice** *(Maps GATE 5)*: 6 panels × 6 sizes:
  output exactly w×h; content rect inside the panel and non-degenerate;
  glass inside stage and > 25% of it at 10 sizes; `MAX_RADIUS ≤ 400`.
- [ ] **G-R19 — Selector** *(Maps GATE 6 + P4)*: 10 key plates one
  footprint (256×267) within 0.002 aspect; `_BANK_ASPECT == 1400/264`;
  keys drawn at authored aspect within 0.004 at every size; key centres
  hit-test to their own index; gap and left-of-bank clicks reject; even
  pitch, no overlap; labels clear of keys; state-priority table (§C.9)
  exact; wells trued to one pitch (`SEL_PITCH`).
- [ ] **G-R20 — Switching** *(Maps GATE 7)*: 120 switches, clocks never
  rewind, inactive specimens' clocks untouched, all specimens drawable at
  all sizes without exception; switching allocates nothing per switch
  (organisms built lazily, kept for process lifetime).
- [ ] **G-R21 — Static layer determinism** *(Maps GATE 8 + P9)*: warm vs
  cold render byte-identical at 3 sizes (clock frozen); cold renders
  byte-identical at device scales 1.0 and 1.6; warm hit returns the same
  surface/texture object.

### Production parity (P1–P12)

- [ ] **G-R22 (P1)** six module assets exist (runtime PNGs + masters) and
  the registry declares exactly 6 modules.
- [ ] **G-R23 (P2)** module alpha valid: rounded corners alpha 0; master
  canvas borders ≤ 8; ≥30% fully opaque; ≤6% partial alpha; registry size
  == sprite size.
- [ ] **G-R24 (P3)** apertures exactly transparent (α=0): observation
  aperture, 5 selector wells, rocker + mode openings.
- [ ] **G-R25 (P5)** no active-key matte: active key plate corners clear;
  socket green lift outside the lit key ≤ 10/255.
- [ ] **G-R26 (P6)** no baked runtime values: every data bay floor
  luminance ≤ 60.
- [ ] **G-R27 (P7)** no duplicate fasteners: shell declares none; nearest
  cross-module screws ≥ 1.6 summed-radii apart at 5 sizes.
- [ ] **G-R28 (P8)** rack seating per G-R14 reference deltas.
- [ ] **G-R29 (P9)** deterministic cache — folded into G-R21.
- [ ] **G-R30 (P10)** bounded history: 4 rings × 300 f32 samples; nbytes
  unchanged over 20,000 pushes; `window(173)` returns 173 columns.
- [ ] **G-R31 (P11)** compact layout without selector bank — folded into
  G-R13.
- [ ] **G-R32 (P12)** cadence stub: focused == 60 (≤60), unfocused ∈
  (0, 30], hidden == 0, unfocused < focused; `--fps-cap` overrides.

### Raster & typography parity

- [ ] **G-R33 — Pointfield accumulator/colour-map fixtures** *(New)*.
  Golden vectors: (a) `accumulate_physio` on a synthetic 200-point cloud at
  fixed persist values — per-pixel `acc/acc_e/acc_h` f32 exact (bincount
  f64 accumulation then f32 rounding, point order preserved);
  (b) `paint_physio` colour map on a golden accumulator at two (ink,
  persist) settings — packed ARGB output within ±1/255 per channel on ≥
  99.9% of lit pixels, ±2/255 max; (c) ink formula check points.
- [ ] **G-R34 — Segment glyph geometry fixtures** *(New)*. For the full
  `GLYPHS` + `NARROW` set at heights {11, 24, 48, 90}: measure/advance
  widths match Python ≤ 0.01px; per-glyph lit/ghost/bloom path bounding
  boxes match ≤ 0.5px; unit layer metrics match.
- [ ] **G-R35 — Font installation & face availability** *(New)*. After
  Rust startup on a clean font cache: fc-list shows Astro and Microgramma
  from `~/.local/share/fonts/abyssal/`; Pango resolves both families;
  no-copy when digests match; app runs (mono fallback) if the directory is
  unwritable.
- [ ] **G-R36 — Text metrics parity** *(New)*. Golden table of `_text_w`
  and cap heights for ~40 representative strings × the three families ×
  weights {NORMAL, MEDIUM}: match Python ≤ 0.25px (same Pango/fontconfig
  stack, so tolerance covers only shaping-version drift **[proposal]**).
- [ ] **G-R37 — Static text cache parity** *(New)*. With identical inputs,
  cached glyph-run blits are byte-identical to fresh renders (warm==cold),
  and cache invalidation triggers exactly on the semantic changes of §D.3
  (clock 1 Hz; rack on telemetry/FPS change incl. ring versions; keys on
  input only).

### Telemetry parity

- [ ] **G-R38 — `/proc`+`/sys` parser fixtures** *(New)*. Feed fixture file
  contents (normal, short, malformed, missing) through the Rust sampler and
  compare against Python golden outputs bit-for-bit: cpu load/pct
  (two-sample deltas), memory pressure/GB, sensor selection order over a
  synthetic hwmon tree (all priority branches + fallback + stale sensor),
  io rate/mb (incl. first-sample-unavailable, Δt≤0), notes strings, and
  the never-raise contract (every failure mode returns last-known-good).
- [ ] **G-R39 — History ring & window fixtures** *(New)*. Golden: push
  10,000 samples → `values()`, `last()`, `peak()`, `window(n)` for n ∈
  {2, 50, 173, 300} match Python f32 exactly (NaN semantics included);
  version monotonicity.
- [ ] **G-R40 — Live telemetry cadence** *(Maps perf_live telemetry rows +
  README)*: sample interval 200 ms ± scheduler jitter; `tel_ms` reported;
  telemetry continues while the window is suspended; `--qa-temp`
  substitution exact per §E.4.

### Cross-implementation & live

- [ ] **G-R41 — Cross-implementation screenshot deltas** *(New)*. Render
  both implementations headless at frozen inputs (specimen ∈ 0..4, seed
  20260920+i, fixed sim time via N fixed dt=1/60 steps, `--qa-temp`
  telemetry fixture, layout sizes {600×480, 900×700, 1448×1086, 1400×860},
  hidpi 1.0, clock frozen) and compare:
  (a) module geometry overlay delta ≤ 2px (all five modules);
  (b) full frame: mean |Δ| ≤ 3/255, p99.9 ≤ 12/255, max ≤ 40/255;
  (c) glass-only organism region: mean ≤ 4/255, p99.9 ≤ 16/255;
  (d) static layers byte-identical between warm and cold **within** each
  implementation. Thresholds are initial **[proposal]** — calibrate on the
  first baseline pair, then freeze them into the gate.
- [ ] **G-R42 — Live resource envelope** *(Maps `qa/perf_live.py`)*, 8s
  dwell, focused/unfocused/hidden × {compact, instrument, archive}:
  focused ≤ 25% CPU at instrument (Python measured 20.7), unfocused ≤ 15%
  (12.2), hidden ≤ 1% (0.1–0.2); FPS 60/30/0 ± 2; draw/sim/tel ms
  reported and ≤ 1.5× Python measured (1.30/0.81/0.41 ms instrument)
  **[proposal]**.
- [ ] **G-R43 — smaps accounting** *(New)*. From `/proc/<pid>/smaps_rollup`
  at instrument size, steady state: report Rss, shared file-backed, and
  anonymous; assert (a) anonymous RSS ≤ 100 MB **[proposal]** (Python
  baseline ≈ 130 MB anon; Rust should be lower — hard ceiling 160 MB);
  (b) RSS growth over 120 s steady animation ≤ 8 MB (no leak; Python
  measured +0.43 MB/15 s allocator noise); (c) every bounded cache respects
  its limit (textures 8, scaled panels 64, glyph runs 700, text widths
  4096, regions 8, traces 16, rings 8, wash 6, falloff 24, field buffers 3)
  — verified by an internal counters API exposed to the gate.
- [ ] **G-R44 — Probe JSONL contract** *(New)*. The Rust app emits the
  same JSONL fields as `app._log_probe` on the same events (resize, state,
  sample @2 s) so `qa/torture.py::analyse` runs unmodified against a Rust
  log.
- [ ] **G-R45 — Live Hyprland torture** *(Maps GATE 0, `qa/torture.py`)*:
  ≥3 cycles of tile → tiled resizes → fullscreen → maximize → float → 8
  floating resizes → retile, plus the 6 s rapid-resize storm: app alive
  throughout; probe analysis (via G-R44) passes — clock monotonic,
  `rebuilds == 0`, worst isotropy ≤ 1e-6 px, zero clipped events, all
  three states exercised.

---

## §G. Fixture exports required from Python (built by the integrator; this spec only defines them)

1. `eq_vectors.npz` — per source: index arrays, t values, `x/y/weight`
   outputs (f32) + finite masks; seated+rest world coords.
2. `perm_anatomy.npz` — per (species, seed): `perm` (i64), `mod2/mod4`,
   `u/lat/grp` (f32).
3. `physio_traces.json` — PhysiologyModel golden traces (f64, full
   precision, 10 fields × frames).
4. `telemetry_fixtures/` — synthetic `/proc`+`/sys` trees with golden
   `Telemetry` results (incl. failure modes).
5. `history_windows.npz` — ring pushes + `window(n)` outputs (f32).
6. `pointfield_golden.npz` — accumulator triples + packed ARGB frames at
   fixed (ink, persist).
7. `segment_metrics.json` — advance widths / bboxes per glyph × height.
8. `text_metrics.json` — widths/caps for the §G-R36 string set.
9. `offscreen_baseline/*.png` — the frozen-input frames of G-R41 (clock
   frozen, `--qa-temp` fixed), generated by a variant of `qa/offscreen.py`.

Export tool should live at `qa/export_rust_fixtures.py` and be
deterministic (fixed seeds, frozen clocks).

---

## Appendix 1 — f32/f64 boundary table (summary)

| quantity | width | note |
|---|---|---|
| equation arrays (x,y,weight,i,perm,mod2/4,u,lat,grp,s,f,e,h) | f32 | weak-scalar promotion keeps f32 |
| `Source.time`, `wave`, `aux`, `_jit` | f64 | integrated clocks |
| physiology (all fields, all taus) | f64 | pure f64 |
| viewport scale/layout rects | f64 | `np.float32(vp.scale*res)` rounds before point multiply |
| point→pixel cast | f32→i32 | C truncation toward zero |
| bincount accumulation | f64 | rounded to f32 on store into `acc*` |
| `acc/acc_e/acc_h`, colour map, LUT | f32 | `np.exp/power` in f32 |
| history rings + `window()` | f32 | reduceat in f32 |
| telemetry numeric parsing | i64→f64 | counters are integers |
| ink, persist exponent, state_hue, smoothstep | f64 | scalar math |

## Appendix 2 — Known deliberate deviations to preserve

- s05's bulb sits at the *lower* end (equation authoritative, key engraving
  is not flipped to match).
- s01's `.3/k` off-canvas spike points are made invisible, not clamped.
- Resting **colour** pulses subtly; resting **geometry** does not.
- The FRAME module reports presented FPS honestly (may show half-rate at
  large windows).
- Omarchy's 98.5% window opacity faintly shows the wallpaper — not an app
  bug; per-window opt-out rule documented in README.
- Selector ledge identifiers are F1/service-view only.
