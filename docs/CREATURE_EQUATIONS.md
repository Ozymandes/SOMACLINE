# Creature equations

The five specimens are published generative sketches — compact p5.js
programs that draw a creature by evaluating one closed-form expression tens
of thousands of times a frame. This file records each source **verbatim**,
the rules its NumPy port follows, and what was verified.

- **Source of truth:** `abyssal/organism/sources.py` (the verbatim JS lives
  in each `Source.original`; this document is generated from it).
- **Standalone validation:** `python3 qa/creature_validate.py` renders every
  equation in its OWN 400×400 canvas — original sample count, original time
  step, original compositing, no world transform, no telemetry. Output in
  `docs/creature_validation/`; `comparison.png` sets each render beside its
  engraved selector key.

## Porting rules

| p5.js | port |
|---|---|
| `mag(a,b)` | `np.hypot(a,b)` |
| `sin` `cos` `atan2` | `np.sin` `np.cos` `np.arctan2` |
| `PI` | `np.pi` |
| `for(i=N;i--;)` | one vectorised index array (see note on order) |
| `a ? b : c` | `np.where(a, b, c)` |
| `point(x,y)` | one sample in the returned array |
| `circle(x,y,d)` | one sample carrying weight `d²` (disc area) |
| `stroke(w,A)` / `fill(w,A)` | `Source.ink = A/255` |
| `background(g)` | accumulator cleared each frame |
| `background(g,A)` | accumulator retained at `1 − A/255` per frame |

**Variable names, operator order, constants and loop bounds are kept
exactly.** The ports are not tidied: naming a subexpression or hoisting a
constant is an opportunity to change the creature.

**Loop order.** The originals iterate `i` downward; the ports evaluate an
ascending array. Every point is composited with the same alpha, so emission
order cannot change the image — only the *set* of indices matters, and it is
identical.

**The global `i`.** Sources 02 and 04 read the render loop's counter from
inside the function (02 in its `d` default argument, 04 as `i%4*8`). The
ports keep that dependency by evaluating over the same index array. In 04 it
is load-bearing: it is what separates the four plumes.

## Transcription check

Each transcription was checked operator by operator against JavaScript
precedence. Two points needed care; neither required a correction:

- **Source 05, `mag(k,e)**2/59+4`.** In JS `**` binds tighter than `/`, so
  this is `(k²+e²)/59 + 4`, not `k²+e²` over `(59+4)`. The port writes
  `np.hypot(k, e) ** 2 / 59.0 + 4.0`, which NumPy evaluates identically.
- **Source 02, `sin(...)/2*k*(...)`.** Left-associative: `((sin(...)/2)·k)·(...)`.

**No transcription corrections were required.** The one behaviour worth
naming is source 01's `.3/k`: as `cos(x/21)` nears zero the term spikes and
throws a handful of samples hundreds of units off-canvas. `x` is integral so
`k` is never exactly zero; p5 draws those samples off-screen where nobody
sees them, and the console does the same by giving them zero weight.

**Orientation.** No axis is flipped. p5 and Cairo both put +y down, so every
creature appears the way its equation draws it. Source 05 therefore carries
its bulb at the *lower* end of the rachis; the engraved key draws it at the
top. The equation is authoritative, so the port keeps the equation's
orientation rather than matching the key.

## Seating in the world

The originals frame themselves loosely and none is centred. `to_world()`
applies **one translation and one uniform scale** — the centre and
half-extent of each creature's robust bounding box over a full period of its
own clock (`tools/measure_sources.py`) — so the creature sits on the scope
without any change to its shape. The robust bound (0.15/99.85 percentile)
ignores source 01's off-canvas spike samples.

## Telemetry

The equation is never edited. Physiology acts on the SOLVED cloud, and every
term is proportional to its own channel, so each specimen reduces **exactly**
to its published equation when physiology is at rest. `qa/console_gates.py`
GATE 4 asserts that for all five.

| specimen | CPU | thermal | memory | I/O |
|---|---|---|---|---|
| s01 SIGMA | transverse ripple, growing toward the tail | plume breathes about its centre | point density | a bright band runs head to tail |
| s02 DIPLO | the two bodies counter-rotate | the pair breathes apart | point density | a packet crosses from one body to the other |
| s03 ROSTRA | the trailing limbs agitate | metabolic breath; above 0.88 the mirror plane shears | density and trail persistence | a band travels down the body |
| s04 QUADRI | the four plumes fall out of step | all four breathe together | point density | the flare walks round the four |
| s05 PENNA | a whip, strongest at the tip | the arc opens | point density | the base flares |

All five also shift toward amber as thermal stress rises, and CPU raises the
phase rate within a bounded band (1.0× at rest, 1.5× at full load) — the one
place a global speed-up is legitimate, because the sources are themselves
phase animations.

---

## s01 — sigmoid plume

**Specimen:** PLUMARIA SIGMATA · *the sigmoid plume* · `AQS-0042` · selector key 01

| | |
|---|---|
| samples per frame | 10,000 |
| time step | `PI/240` |
| ink | `116/255` = 0.455 |
| compositing | clear each frame |
| world frame | centre (196.38, 203.49), half-extent 116.95 |

```js
a=(x,y,d=mag(k=4*cos(x/21),e=y/8-20))=>
circle(
  (q=3*sin(k*2)+.3/k+sin(y/19)*k*(9+2*sin(e*14-d*3+t*2)))+50*cos(c=d-t)+200,
  q*sin(c)+d*39-475,
  k*k>15?2:1
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).noStroke().fill(w,116);
for(t+=PI/240,i=1e4;i--;)a(i,i/235)}
```

## s02 — coupled bodies

**Specimen:** DIPLOSOMA CONIUGATA · *the coupled pair* · `AQS-0117` · selector key 02

| | |
|---|---|
| samples per frame | 20,000 |
| time step | `PI/45` |
| ink | `96/255` = 0.376 |
| compositing | clear each frame |
| world frame | centre (213.12, 200.94), half-extent 181.72 |

```js
a=(m,d=mag(k=9*cos(i/81),e=i/765-13)/4)=>
point(
  (q=79-2*sin(k*3)+sin(k*k<19?t*3+d*4:d/2+4)/2*k*(9+5*sin(d*d-e/6-t+m)))
    *sin(c=d*d/9-t/16+m)+200,
  (q+50)*cos(c)+200
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).stroke(w,96);
for(t+=PI/45,i=2e4;i--;)a(i%2*9)}
```

## s03 — mirrored alien

**Specimen:** SYMMETRA ROSTRATA · *the mirrored rostrum* · `AQS-0233` · selector key 03

| | |
|---|---|
| samples per frame | 40,000 |
| time step | `PI/90` |
| ink | `46/255` = 0.180 |
| compositing | retain 0.6235 per frame (`background(6,96)`) |
| world frame | centre (199.38, 182.16), half-extent 164.02 |

```js
a=(x,y,d=5*cos(o=mag(k=x/8-12.5,e=y/8-12.5)/12*cos(sin(k/2)*cos(e/2))))=>
point(
  (x+d*k*(sin(d*2+t)+sin(y*o*o))/9)/1.5+133,
  (y/3-d*40+19*cos(d+t))*1.5+300
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(6,96).stroke(w,46);
for(t+=PI/90,i=4e4;i--;)a(i%200,i/200)}
```

## s04 — four-part plume

**Specimen:** QUADRIPLUMA ARTICULATA · *the quartered plume* · `AQS-0308` · selector key 04

| | |
|---|---|
| samples per frame | 20,000 |
| time step | `PI/30` |
| ink | `96/255` = 0.376 |
| compositing | clear each frame |
| world frame | centre (199.68, 199.7), half-extent 140.8 |

```js
a=(y,o=mag(k=cos(y*9)*(y<5?sin(t/8+y)*35:11),e=y/8-13)/6)=>
point(
  (q=k*y/19+49+k*sin(y)*sin(o*2-e/5-t))*sin(c=o/3-e/5-t/8+i%4*8)
    -79*cos(c/3)+200,
  200+(q+70)*cos(c)
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(6).stroke(w,96);
for(t+=PI/30,i=2e4;i--;)a(i/500)}
```

## s05 — single feather

**Specimen:** PENNARIA SOLITARIA · *the solitary feather* · `AQS-0451` · selector key 05

| | |
|---|---|
| samples per frame | 10,000 |
| time step | `PI/20` |
| ink | `66/255` = 0.259 |
| compositing | clear each frame |
| world frame | centre (214.04, 187.18), half-extent 124.43 |

```js
a=(x,y,d=mag(k=5*cos(x/14)*cos(y/30),e=y/8-13)**2/59+4)=>
point(
  (q=60-3*sin(atan2(k,e)*e)+k*(3+4/d*sin(d*d-t*2)))*sin(c=d/2+e/99-t/18)+200,
  (q+d*9)*cos(c)+200
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).stroke(w,66);
for(t+=PI/20,i=1e4;i--;)a(i%200,i/43)}
```
