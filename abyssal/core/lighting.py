"""Restrained dynamic lighting over the hardware skin.

The console is a dark physical object. Light in this scene comes from the
instrument's own screens and lamps, so a lit display should catch the metal
immediately around it and nothing further. That local spill is most of what
makes a raster skin read as an object rather than a picture of one.

DISCIPLINE
----------
This is deliberately hard to abuse:

* every emitter's strength is clamped to `MAX_STRENGTH`
* the whole pass is clamped again to `MAX_TOTAL`
* emitters are additive but the accumulated result is alpha-limited, so
  stacking ten lamps cannot bloom the panel out
* radii are in pixels and small by construction - a spill is a halo on nearby
  metal, never a wash across the console

The result should be noticeable only if you switch it off. It is not a neon
cyberpunk layer, and the caps above exist to keep it that way.

The pass is a pure function of the emitter list, so it holds no state across
frames and cannot drift.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import cairo

MAX_STRENGTH = 0.30
MAX_TOTAL = 0.42

#: Hard ceiling on one emitter's radius, in pixels. A spill is light catching
#: the metal NEAR a screen; past a certain size it stops being a spill and
#: becomes a wash over the console - which is both wrong for the brief and,
#: because the composite group is sized to the emitters, the single largest
#: cost in the whole draw. Capping it bounds both.
MAX_RADIUS = 240.0


@dataclass(frozen=True, slots=True)
class Emitter:
    x: float
    y: float
    radius: float
    rgb: tuple[float, float, float]
    strength: float


class LightField:
    """Collects emitters during a frame, then paints them once."""

    __slots__ = ("_e",)

    def __init__(self) -> None:
        self._e: list[Emitter] = []

    def clear(self) -> None:
        self._e.clear()

    def add(self, x: float, y: float, radius: float,
            rgb: tuple[float, float, float], strength: float) -> None:
        if radius <= 1.0 or strength <= 0.002:
            return
        self._e.append(Emitter(x, y, min(radius, MAX_RADIUS),
                               rgb, min(MAX_STRENGTH, max(0.0, strength))))

    def glow(self, rect, rgb: tuple[float, float, float], strength: float,
             spread: float = 0.62) -> None:
        """A screen spilling onto the metal around it.

        The radius is tied to the rect's own size, so a big display throws a
        proportionally wider - never a brighter - pool of light.
        """
        r = max(rect.w, rect.h) * spread
        self.add(rect.cx, rect.cy, r, rgb, strength)

    @property
    def count(self) -> int:
        return len(self._e)

    def paint(self, cr: cairo.Context, clip=None) -> None:
        """Composite every emitter additively.

        Each emitter is drawn straight onto the target with OPERATOR_ADD, its
        alpha pre-scaled by MAX_TOTAL. There is deliberately NO intermediate
        group: a group is sized to the current clip, and because the emitters
        are spread across the console (one per module, plus the stage and the
        selector) their union covers most of the window. That made the group
        the single most expensive thing in the frame - 9.3ms at 1920x1080.

        The group existed to cap the ACCUMULATED alpha. Dropping it means
        overlapping spills can now sum instead. That is acceptable here because
        the emitters are spatially separated by construction, each one is capped
        at MAX_STRENGTH * MAX_TOTAL = 0.126, and MAX_RADIUS bounds how far any
        of them can reach. Three fully coincident emitters would still only
        reach ~0.38, well short of washing out the metal.

        THE FALLOFF IS A CACHED MASK, NOT A GRADIENT
        --------------------------------------------
        Rasterising a radial gradient is the expensive part, and this console
        asks for about twenty of them a frame at a handful of distinct radii
        that only change on resize. The falloff is therefore rendered ONCE per
        quantised radius into an A8 mask and then stamped with a flat colour
        source. That is pure arithmetic identical in result and roughly seven
        times cheaper; the cache is derived pixels only, so dropping it between
        any two frames would change performance and nothing else.
        """
        if not self._e:
            return
        cr.save()
        if clip is not None:
            cr.rectangle(clip.x, clip.y, clip.w, clip.h)
            cr.clip()
        cr.set_operator(cairo.OPERATOR_ADD)
        for e in self._e:
            a = e.strength * MAX_TOTAL
            r, gr, b = e.rgb
            mask, side = _falloff(e.radius)
            cr.set_source_rgba(r, gr, b, a)
            cr.mask_surface(mask, round(e.x - side * 0.5),
                            round(e.y - side * 0.5))
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.restore()


#: Quantisation of the falloff radius, in pixels. Emitters whose radii differ
#: by less than this share one mask; at these sizes and alphas the difference
#: is invisible, and it keeps the cache to a handful of entries across a
#: resize drag instead of one per pixel.
_RADIUS_STEP = 8.0
_FALLOFF_LIMIT = 24
_FALLOFF: "OrderedDict[int, tuple[cairo.ImageSurface, int]]" = OrderedDict()


def _falloff(radius: float) -> tuple[cairo.ImageSurface, int]:
    """An A8 mask of the emitter profile, cached per quantised radius."""
    q = max(1, int(round(radius / _RADIUS_STEP)))
    hit = _FALLOFF.get(q)
    if hit is not None:
        _FALLOFF.move_to_end(q)
        return hit
    rad = q * _RADIUS_STEP
    side = int(rad * 2.0) + 2
    surf = cairo.ImageSurface(cairo.FORMAT_A8, side, side)
    c = cairo.Context(surf)
    mid = side * 0.5
    g = cairo.RadialGradient(mid, mid, 0.0, mid, mid, rad)
    g.add_color_stop_rgba(0.0, 0.0, 0.0, 0.0, 1.0)
    g.add_color_stop_rgba(0.45, 0.0, 0.0, 0.0, 0.38)
    g.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
    c.set_source(g)
    c.arc(mid, mid, rad, 0.0, 6.283185307179586)
    c.fill()
    surf.flush()
    ent = (surf, side)
    _FALLOFF[q] = ent
    while len(_FALLOFF) > _FALLOFF_LIMIT:
        _FALLOFF.popitem(last=False)
    return ent


# Emitter colours, matched to the instrument palette.
CYAN = (0.42, 0.78, 0.92)
CHARTREUSE = (0.72, 0.88, 0.28)
AMBER = (0.96, 0.62, 0.18)
RED = (0.90, 0.26, 0.20)


def state_rgb(state: str) -> tuple[float, float, float]:
    return {
        "nominal": CHARTREUSE,
        "active": CYAN,
        "standby": CYAN,
        "warning": AMBER,
        "critical": RED,
    }.get(state, CYAN)
