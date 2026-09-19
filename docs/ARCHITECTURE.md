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
