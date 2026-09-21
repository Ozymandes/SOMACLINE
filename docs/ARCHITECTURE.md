# Architecture

## The one rule

**The simulation never sees a pixel.**

```
 telemetry/source.py   ->  Telemetry   (raw OS numbers, normalised 0..1)
 core/physiology.py    ->  Physiology  (smoothed organism drives)
 organism/plumiradia.py -> world-space geometry, in a fixed 1000x1000 world
 core/viewport.py      ->  THE ONLY place world units become pixels
 organism/render.py    ->  cairo strokes
 ui/chrome.py          ->  instrument text and readouts
```

Nothing left of `core/viewport.py` in that chain knows the window exists.

## Why this stack

GTK4 + Cairo, immediate mode, one `GtkDrawingArea`.

The previous prototype was Qt6/QML. Its crashes came from the scene graph:
`ShaderEffectSource` feedback buffers freed while the render thread still held
them, textures reallocated on size-class change, degenerate 1px allocations.
Every one of those bugs requires *persistent GPU rendering state that outlives
a frame*.

This project has none. There is no GL context, no texture, no framebuffer, no
scene graph, no render thread, and exactly one widget. A resize therefore
cannot churn rendering state, because there is no rendering state to churn.
That is a structural guarantee, not a bug fix.

The cost is CPU rasterisation, which is why the organism's point budget is
capped and measured (see `qa/gates.py`, GATE 3).

## The resize contract

A resize does exactly this, in `app.Monitor._on_draw`:

```python
layout = resolve(width, height)                   # pure function -> value
vp = Viewport.for_stage(*layout.stage)            # pure function -> value
```

Both are frozen dataclasses built from a few floats. They are constructed
fresh every frame, so there is no cache to invalidate. Nothing is rebuilt,
reset, reparented or reallocated. `qa/gates.py::gate_resize_invariance`
asserts this empirically: two organisms fed identical timesteps, one rendered
at a random size every frame, stay **bit-identical**.

`Viewport.scale` is a single float applied to both axes, so a non-uniform
scale is not expressible in this codebase. Circles stay circles by
construction.

## Responsive states

`core/layout.py` returns a different *layout*, not a scaled one. Font sizes
are fixed per state (`TypeScale`), not interpolated with window size.

| state | width | shape |
|---|---|---|
| COMPACT | < 700 | organism dominates, telemetry collapses to a strip |
| INSTRUMENT | 700–1100 | **the canonical Hyprland tile** |
| ARCHIVE | > 1100 | side instrument column with metadata |

Height demotes the state: a very short window falls back rather than crushing
the organism to nothing.

## Frame loop

`GdkFrameClock` tick -> advance simulation -> `queue_draw`. Simulation runs in
the tick, rendering in the draw handler; the draw handler is a pure function of
(simulation state, width, height) and has no side effects. `dt` is clamped so
an unmapped or occluded window cannot teleport the organism on resume.

## Diagnostics

`F1` overlay and `F2` calibration geometry are permanent product features.
`--probe FILE` appends a JSONL geometry event per resize/state change, which is
what `qa/torture.py` asserts against.

---

# The hardware skin

Everything above still holds. The skin is a rendering layer bolted on top of it;
nothing in `skin/`, `ui/console.py` or `ui/segment.py` can reach the simulation.

## Pipeline

```
 assets/hardware_v2/    2K generated masters (out of git, ~171MB)
   -> tools/build_sprites.py      ONE build step: slice, normalise, downsample
 assets/sprites/        41 runtime PNGs, 12MB, committed
   -> skin/surface.py             load once, 9-slice, cache by exact size
   -> skin/catalog.py             which sprite, and how it may scale
   -> ui/console.py               assemble panels, draw live content on top
```

The build step exists because the masters are 2688px wide and the generator
would not hold a constant footprint across a family of state sprites. Slicing
at build time makes alignment a property of the pipeline instead of a property
of a prompt: `build_sprites.py` re-centres every lamp state onto one canvas and
composites every selector key onto one canonical fascia.

## 9-slice, and what is not 9-sliced

A panel is split by four insets. Corners never scale, edges scale on one axis,
the centre scales freely. That keeps screws circular and bevel crests sharp at
any window size.

Only true frames are 9-sliced: the observation bezel, the segment housings, the
graph well, the meter trough, the aux frame and the generic plate.

The composite panels of the reference machine - the chassis, the header, the
rack, a telemetry module, the selector bank - are **arrangements, not frames**.
The chassis has a viewport hole on the left and solid rack on the right, so it
has no symmetric border to stretch; a module has three fixed recesses that would
smear. Those are **assembled** at draw time from the primitives. That is why
this pass needed no extra corner or edge art.

Two rules were learned the hard way and are enforced in `skin/surface.py`:

* **Borders and content pads shrink together.** The borders say where the metal
  is painted; the pads say where the recess begins. Scaling one without the
  other makes the app believe the glass starts where metal is still being drawn,
  which is how a shrunken bezel covers its own viewport.
* **A content pad is a SOURCE-space measurement, mapped through the slice.** A
  pad inside the fixed border only shrinks; a pad past it rides the stretch.
  Treating pads as drawn-space offsets puts text on the bevel crest.

A raster bevel cannot shrink below its own thickness, so panels thinner than
their bevel do not use one: the archive rail and the condensed telemetry strip
are drawn procedurally by `console._rail`.

## The cache, and why it does not violate the resize contract

`skin/surface.py` keeps a bounded LRU of rendered panels keyed by
`(panel, exact integer size)`. The contract above is about **geometry**, and it
is untouched: nothing in this module can produce a Layout, a Viewport or a
simulation value. What is cached is derived pixels, and the key is the complete
set of inputs, so dropping the whole cache between any two frames would change
performance and nothing else. `gate_resize_invariance` still reports
bit-identical organisms.

## Seven-segment

`ui/segment.py` draws numerals as Cairo paths, not a font. No seven-segment face
is installed, and a font could not give unlit ghost segments, thickness
independent of glyph height, chamfered LED segment ends, or a bloom that tracks
the lit colour. Geometry is authored in a unit cell and scaled, so it stays
crisp at 11px and at 90px.

## Lighting

`core/lighting.py` collects emitters during a frame and paints them additively,
with per-emitter strength, a radius ceiling and no intermediate group. The group
was removed after measurement: it is sized to the current clip, and because the
emitters are spread across the console their union covers most of the window,
which cost 9.3ms at 1920x1080. See the module docstring for what that trades.

## Specimens

`organism/form.py` is the original Plumiradia solver with every hand-tuned
constant lifted into `organism/morphology.py`. A species is a parameter block,
not a new renderer, which is what makes the five read as one taxonomic family.

The refactor matches the original to ~4e-13 world units - not bit-identical,
because lifting constants reassociates a few floating point sums. That is ~12
orders of magnitude below a device pixel.

The original 4-quadrant balance check was replaced by ownership binning
(`form.lobe_mass`): species with a wide barb splay push barbs across an angular
sector boundary, so the old histogram measured the binning rather than the
organism.

## Selector

`ui/selector.py::layout` is a pure function from a rect to key rectangles, and
drawing, hit testing and QA all call it. The bank you can see and the bank you
can click are the same bank by construction; GATE 6 asserts it at every layout
size.

---

# Visual fidelity pass

The skin pass made the UI *use* the hardware library; this pass made it look
like the machine in `monitor_ref.png`. Four structural things were missing, and
they are what the reference's authority actually rests on.

**The enclosure.** Panels floated on black. The reference puts everything inside
one frame with corner screws and side handle rails, so `core/layout` now carves
a `chassis` rect first and everything else is laid out inside its inner face.
`console._draw_chassis` bolts it together from the plate plus real parts.

**Text belongs in recesses.** Once the chassis plate sat behind everything, half
the microcopy vanished: the instrument ink is a near-black palette and is
invisible on lit metal. The reference never puts type on bare metal - every text
zone is a dark inset. Module interiors, the header and the rails are now cut
with `console._rail`, and the type reads again. This is a rule, not a fix: if a
new label is unreadable, it is on metal and wants a recess.

**Borders cannot stay 1:1 forever.** A 9-sliced border is authored at the
sprite's own size. The observation bezel carries 178px of metal vertically at
1280x900; at a 489px stage that was 36% of the field, and the specimen could not
command it. `skin.surface` now scales borders by how far below its authored size
a panel is drawn, using ONE factor for both axes so corners stay square.

**Density is structure, not noise.** Each telemetry bay carries what the
reference's does - index plaque, title, subtitle, four labelled annunciators, a
graph well with its own axis and caption, the numeric housing, and a
TARGET/PEAK/delta/STATE column. The observation field carries a measured RADIUS
axis, a SCALE bar, registration brackets and four annotation blocks. All of it
is live: nothing in either is a fixed string standing in for a number.

## Why the console got faster while getting denser

The enriched draw cost 14.2ms at 1400x860. Three caches brought it to 8.0ms,
below the *pre-enrichment* 8.5ms:

* `chrome._text_w` is memoised. Layout decisions (widest key in a block, the
  axis gutter, the motto column) re-measure the same strings every frame, and
  each miss is a full Pango shaping pass.
* `console._show` caches rendered glyph runs. The console draws ~140 strings a
  frame and nearly all are byte-identical between frames. Each distinct
  (text, size, weight, tracking, colour, alpha, align, max_w) is rasterised once
  and then blitted. Positions round to whole pixels, which also stops microcopy
  shimmering as the values beside it change.
* `console._rings` rasterises the dotted polar rings once per field size. A
  dashed arc is among the more expensive things Cairo can be asked for.

All three are keyed by their complete inputs, so none can change what is drawn.

## Graduated states

The three layout states are the same machine, not a poster and two apologies.
Thresholds are on the space a component actually receives, so a state degrades
by dropping a layer rather than by shrinking everything:

| | field annotation | telemetry bay | header |
|---|---|---|---|
| ARCHIVE | ruler + 4 blocks + scale bar | full bay incl. status column | 3 compartments + status rail |
| INSTRUMENT | ruler + specimen block | bay with graph well | 3 compartments + status rail |
| COMPACT | brackets + graticule | condensed 4-up rail | one line + LIVE + clock |

---

# Visual-integration pass

Four things changed shape in this pass. Each solved a problem that could not
be solved by tuning numbers.

## 1. Fascia bays: text has a physical home

`abyssal/skin/fascia.py`.

The generated hardware was never flat metal. The header asset is a
manufactured panel with four recessed information bays over a three-bay
status rail; the module shell has an identity plaque, a title bay, four
annunciator wells and three display recesses. Those recesses are the point of
the artwork. Drawing the panel and then placing type wherever the layout felt
like throws away exactly the thing that was paid for — and it is precisely
what makes a UI read as an overlay: the type has no physical home.

A `Fascia` is therefore a sprite **plus the measured source-pixel rectangle of
every recess cut into it**. Bays were located by connected-component analysis
of the sprite's own dark regions (luminance < 48 under opaque alpha), not by
eye. `place()` scales the panel and returns the same bays in widget pixels.

Scaling is a three-band slice per axis: the end caps (screws, frame edge)
scale uniformly, and only the middle stretches. `place()` maps the bays
through that identical piecewise transform, so the bays a caller is handed are
where the metal actually is, at every size, by construction.

On top of that sit five rules, implemented once in `ui/console.py` rather than
re-invented per call site:

1. a physical bay — a recess measured off the asset
2. internal padding — proportional, with a pixel floor
3. a defined baseline — from the bay's own box, never the panel's
4. a defined alignment, declared per line
5. a responsive rule: shrink to the legibility floor, then ellipsise, and for
   an observation reading, drop the row rather than truncate it

## 2. Five organisms, not one with a parameter

`abyssal/organism/mathforms.py`.

The previous catalogue was a single radial-plume solver with the symmetry
order changed, which is why switching specimens only ever changed how many
arms the creature had. It has been replaced by five separate body plans —
a ciliated sigmoid ribbon, a colony of ruled conic funnels, an exactly
mirrored rostrum, a coupled comb-and-orbit pair, and an arcuate frond — each
a compact set of point equations solved into the buffers every frame.

The engine contract is unchanged and still load-bearing: world units only,
every array allocated once, `update()` writes in place, state is a function of
accumulated time and physiology and never of geometry. What changed is that
`Body` is now a base class with a `_sizes()` / `_build()` / `_shape()`
protocol instead of one solver with a parameter block.

Two additions to the buffers the renderer reads:

- `fil_dot` — a per-filament dot pitch. The reference engravings are drawn as
  sequences of dots, not continuous ink; a dash pattern reproduces that for
  the cost of one call, where one filament per dot would multiply the path
  count by twenty.
- `core_r` may be zero. A body whose structure *is* its centre would be
  falsified by a glowing bead at the world origin.

Telemetry reaches each body as morphology, never as animation speed. The
clearest case is `Symmetra`: its mirror plane is exact at rest (GATE 4
asserts a residual of 0.0 world units) and **breaks** above 0.88 thermal
pulse, so heat is legible as the organism failing to match itself.

`Physiology` gained `flux` and `surge` from a new I/O + network channel in
`telemetry/source.py`. `surge` uses an asymmetric envelope — fast attack, slow
release — so a burst propagates through a body as an event rather than
flickering.

## 3. The specimen keys are the hero control

`abyssal/ui/selector.py`.

Five bespoke console keys, each engraved with one organism's morphology, in
two authored states. The artwork is the specimen's identity, so it is never
tinted or recoloured at runtime; selecting a specimen swaps to that key's own
illuminated plate, and a press is mechanical travel drawn on top, not a
repainted PNG.

The mounting is deliberately quieter than the keys: one dark trough with a
machined gunmetal floor, a seat shadow per key, hairline divider ribs, and an
engraved channel identifier beneath. The previous bank's flat black field
behind the cluster was not a sprite matte — it was a procedural recess — and
it was what made the control read as five images pasted on a background.

`layout()` remains a pure function from a rect to key rectangles, serving
drawing, hit testing and QA from one answer. Keys are drawn at one uniform
scale and their authored aspect, always; a row wider than they need is
resolved by spacing and centring, never by stretching.

## 4. The static hardware layer

`ui/console.py`, `core/lighting.py`, `organism/render.py`.

Most of this console does not change between frames. The chassis, the bezels,
the header fascia and its engraved names, the archive rail, the selector's
mounting, the field's graticule and rulers are all a pure function of the
widget size and which specimen is selected.

Three caches were added, all of them derived pixels only:

- **Two layer surfaces** — one behind the organism, one in front — keyed by
  every discrete input the static passes read.
- **The lighting falloff**, as an A8 mask per quantised radius. Rasterising
  ~20 radial gradients a frame was 3.5ms; stamping a cached mask with a flat
  colour is 0.16ms for the same pixels.
- **The deep-field wash**, as a rendered disc per quantised radius. At 6.5ms
  it was by a wide margin the most expensive single operation in the frame,
  and its radius changes only on resize.

The safety property that makes this acceptable is the same one that made the
sprite cache acceptable: the key is the complete set of inputs, so a hit can
only ever be identical to a miss. `qa/console_gates.py` GATE 8 renders each
frame warm and cold at three sizes and asserts the two are byte-identical.

Measured effect, full frame including simulation:

| size | before | after |
|---|---|---|
| 900x700 | 10.97 ms | 5.43 ms |
| 1400x860 | 16.14 ms | 6.85 ms |
| 1920x1080 | 21.38 ms | 9.11 ms |
| 2560x1600 | 32.93 ms | 12.89 ms |

## What GATE 1 asserts now

The old geometry gate asserted *quadrant balance* — equal filament mass per
diagonal quadrant. That is a property of a four-fold radial plume and of
nothing else in the current catalogue, so it was replaced by the claim that
actually matters and holds for any body plan: projecting the body through the
viewport and unprojecting it must be the identity. Any anisotropy, rounding or
centre drift shows up as a non-zero residual. Measured residual across twenty
sizes: `< 9e-13` world units.

---

# Final convergence pass

## The organisms are their source equations

`organism/sources.py` · `organism/mathforms.py` · `organism/pointfield.py`

The previous pass reconstructed five body plans from the engraved selector
keys. They are replaced by the published p5.js sketches the keys depict,
ported verbatim — same variable names, operator order, constants, loop bounds,
and the render loop's global `i` where the source reads it. The full
transcriptions and the checks made against them are in
`docs/CREATURE_EQUATIONS.md`.

Three layers, each with one job:

    sources.py     the equation, untouched, in its own 400x400 canvas
    mathforms.py   a clock in real seconds, a seat in the world (one
                   translation, one uniform scale), and physiology
    pointfield.py  counting ten to forty thousand points into pixels

**Why a point field.** The sources composite every sample additively at a low
alpha; where the equation crowds the plane the image brightens, and that
density is the creature's volume. Forty thousand Cairo arcs a frame would
cost about a second. `np.bincount` over a flat pixel index is one C pass.

**Bounded cost.** The field buffer covers only the creature's own disc, not
the stage, and above 760px it is counted at that size and scaled once on the
blit. At 2560×1600 this took the organism from 74ms to 7ms.

**Persistence in seconds.** One source composites with `background(6,96)` —
a translucent wash, so each frame keeps 62% of the last. That rate is per
frame at 60fps; applied per frame at 30fps the trail smears across twice the
motion. Retention is raised to the frame's own duration so the trail stays
~75ms at any frame rate, and the ink gain divides out its steady-state
brightness so a trailing species is not brighter than a clearing one.

**Physiology never edits an equation.** It acts on the solved point cloud,
and every term is proportional to its own channel, so at rest each specimen
is exactly its source. GATE 4 compares the specimen at rest against the raw
equation solved with no body at all.

## A crash only the live gate could see

The point field first reused one cairo surface, rewriting its pixels each
frame. GSK keeps a snapshot of whatever it last painted, and Cairo aborts the
process if a snapshotted surface is marked dirty. An offscreen ImageSurface
never snapshots its sources, so every headless gate passed and the real
window died within a second. GATE 0 — the Hyprland torture harness — caught
it. The output image is now allocated per frame from `np.empty` (no memset);
the accumulator and scratch, which only NumPy touches, stay cached.

## The observation zones clear the specimen, not just the axis

Each corner zone is clamped so its inner corner stays outside the specimen's
disc — the circle solved at the zone's own edge, so a short block at the top
may run further toward the axis than a tall one. Zones also carry a height
budget that keeps them off the horizontal axis. A reading that cannot be
shown whole is dropped rather than ellipsised; a key that would crowd the
table goes key-over-value rather than being cut.

## Hardware finish

- **No programmatic chassis corner screws.** The header fascia, archive rail,
  observation bezel and every module shell carry their own fasteners; a third
  screw at each chassis corner sat within ~40px of two real ones.
- **INK_TECH**, a fourth ink rank in the clock-blue family, for secondary
  technical microtype that previously sat on INK_DIM and could not be read.
- **The selector is milled into a lower board** of chassis metal, seamed to
  the bezel at 3px, with a shadowed upper wall, a floor, and screws where it
  terminates against the side members. No new assets were generated.
