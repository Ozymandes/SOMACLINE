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
