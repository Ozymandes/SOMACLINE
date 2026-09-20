"""Hardware console assembly: the raster skin with live content drawn into it.

WHAT THIS MODULE IS
-------------------
`chrome.py` draws the flat instrument. This draws the PHYSICAL one: the
generated hardware panels, composited scale-safely, with every piece of
changing information rendered on top in code. Nothing here reads a baked-in
number, because nothing was baked in.

ASSEMBLY, NOT STRETCHING
------------------------
Only true frames are 9-sliced (see skin/catalog.py). The composite panels of
the reference machine - the header, the rack, a telemetry module, the selector
bank - are ARRANGEMENTS. They are rebuilt here from primitives at whatever size
the layout asks for, which is why they stay sharp at every window size and why
the asset pass needed no extra corner art.

GEOMETRY IS SHARED
------------------
`control_geometry` and `stage_content` are pure functions. Drawing, hit testing
and the QA harness all call them, so the bank you can see and the bank you can
click are the same bank by construction.

DEGRADATION
-----------
Every sprite draw returns a bool. If `assets/sprites` has not been built the
panels simply do not appear and the text still renders, so the app remains
runnable and testable on a bare checkout.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

import cairo

from ..core.layout import Layout, LayoutState, Rect
from ..core.lighting import AMBER as L_AMBER
from ..core.lighting import CHARTREUSE as L_CHART
from ..core.lighting import CYAN as L_CYAN
from ..core.lighting import LightField
from ..core.signals import Telemetry
from ..core.theme import AMBER, CYAN, INK, INK_BRIGHT, INK_DIM, LIME, RULE, rgba
from ..organism.species import CATALOGUE, Species
from ..skin import catalog as C
from ..skin import fascia as F
from ..skin.surface import (draw_nine, draw_sprite, draw_sprite_fit,
                            draw_sprite_rot90, sprite_size)
from . import segment as SEG
from . import selector as SEL
from .chrome import (_cap, _show as _show_raw, _text_w, _W_MEDIUM,
                     _W_NORMAL, draw_background)

TAU = math.tau

#: A throwaway context for geometry helpers that both measure and draw. Asking
#: them to measure against this costs one 1x1 surface for the life of the
#: process and keeps a single implementation of each layout rule, instead of a
#: measuring copy that can silently drift from the drawing one.
_NULL_SURFACE = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
_NULL_CR = cairo.Context(_NULL_SURFACE)

#: chrome refuses to draw below this; ask for it or do not draw at all.
MIN_TEXT = 7.0

# --------------------------------------------------------------------------
# cached text
# --------------------------------------------------------------------------
_TXT: "OrderedDict[tuple, tuple]" = OrderedDict()
_TXT_LIMIT = 700


def _show(cr, text: str, size: float, weight: int, tracking: float,
          x: float, baseline: float, rgb, alpha: float = 1.0,
          align: str = "l", max_w: float = -1.0) -> float:
    """Draw one line of instrument text, via a cache of rendered glyph runs.

    The console draws on the order of 140 strings a frame and almost all of
    them - TARGET, PROCESSOR, MODE:, the axis ticks, the footer keys - are
    byte-identical from frame to frame. Shaping and rasterising them every time
    was the single largest cost in the draw.

    Each distinct (text, size, weight, tracking, colour, alpha, align, max_w)
    is rasterised once into a small surface and then blitted. The key is the
    complete set of inputs, so this cannot change what is drawn; a miss costs
    one ordinary render. Positions are rounded to whole pixels, which also
    stops microcopy shimmering as values change around it.
    """
    if not text or size < 1.0:
        return 0.0
    key = (text, round(size, 2), weight, round(tracking, 3), align,
           round(max_w, 1), round(rgb[0], 3), round(rgb[1], 3),
           round(rgb[2], 3), round(alpha, 3))
    ent = _TXT.get(key)
    if ent is None:
        w = _text_w(text, size, weight, tracking)
        if max_w > 0.0:
            w = min(w, max_w)
        pad = max(3.0, size * 0.8)
        base_in = size * 1.7
        sw = max(1, int(w + pad * 2.0))
        sh = max(1, int(base_in + size * 1.1 + pad))
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
        c2 = cairo.Context(surf)
        _show_raw(c2, text, size, weight, tracking, pad, base_in,
                  rgb, alpha, "l", max_w)
        surf.flush()
        ent = (surf, w, pad, base_in)
        _TXT[key] = ent
        while len(_TXT) > _TXT_LIMIT:
            _TXT.popitem(last=False)
    else:
        _TXT.move_to_end(key)
    surf, w, pad, base_in = ent
    if align == "r":
        ox = x - w
    elif align == "c":
        ox = x - w * 0.5
    else:
        ox = x
    cr.save()
    cr.set_source_surface(surf, round(ox - pad), round(baseline - base_in))
    cr.paint()
    cr.restore()
    return w


# --------------------------------------------------------------------------
# live history
# --------------------------------------------------------------------------
class Trace:
    """Fixed-length ring of recent values, for the sparkline wells.

    Bounded and preallocated: a long session cannot grow it, and sampling is
    O(1). Values are normalised 0..1 by the caller.
    """

    __slots__ = ("_v", "n")

    def __init__(self, n: int = 96) -> None:
        self.n = n
        self._v: deque[float] = deque([0.0] * n, maxlen=n)

    def push(self, v: float) -> None:
        self._v.append(0.0 if v < 0.0 else (1.0 if v > 1.0 else float(v)))

    @property
    def values(self) -> list[float]:
        return list(self._v)


@dataclass
class ConsoleModel:
    """Everything the console needs that is not telemetry or layout."""

    species: Species
    active: int = 0
    pressed: int | None = None
    focus: int | None = None
    mode_state: str = "armed"
    cycle_state: str = "neutral"
    disabled: frozenset[int] = frozenset()
    traces: dict[str, Trace] = field(default_factory=dict)
    switches: int = 0

    # Live observation-field values. Set by the host each frame; every one of
    # these is rendered in code, never baked into the glass.
    phase: float = 0.0            # radians
    rotation: float = 0.0         # rpm
    coords: tuple[float, float, float] = (0.0, 0.0, 0.0)
    behavior: str = "STABLE"
    magnification: float = 4.0
    field_mm: float = 2.50
    aperture: str = "f/1.8"
    flux: float = 0.0
    surge: float = 0.0

    def trace(self, key: str) -> Trace:
        t = self.traces.get(key)
        if t is None:
            t = Trace()
            self.traces[key] = t
        return t


# --------------------------------------------------------------------------
# pure geometry - shared by draw, hit test and QA
# --------------------------------------------------------------------------
def control_geometry(L: Layout) -> tuple[SEL.BankGeometry, Rect]:
    """(specimen bank, mode key rect). Both may be invalid/empty.

    One pure function, three consumers: the draw, the hit test and the QA
    gate. They cannot disagree about where a key is, because there is only one
    answer to ask for.
    """
    if not L.show_controls or not L.controls.valid:
        return (SEL.layout(Rect(0, 0, 0, 0)), Rect(0, 0, 0, 0))
    geo = SEL.layout(L.controls, n=len(CATALOGUE))
    return (geo, geo.aux_r)


def cycle_rect(geo, mode: Rect) -> Rect:
    """The two-way cycle rocker, seated in the trough's left end."""
    return geo.aux_l if geo.valid else Rect(0.0, 0.0, 0.0, 0.0)


def stage_frame(L: Layout):
    """The frame surrounding the specimen field.

    Always the observation bezel: `skin.surface` guarantees a minimum stretched
    middle, so the bezel stays usable at small stages instead of closing over
    its own viewport. A plate cannot substitute here - its centre is opaque
    metal and would hide the organism entirely.
    """
    return C.OBSERVATION_BEZEL


def stage_content(L: Layout) -> Rect:
    """The glass inside the stage frame: where the organism may draw.

    The viewport is built from THIS, not from the raw stage, so the specimen
    sits behind the frame's inner lip instead of under its metal.
    """
    s = L.stage
    if not s.valid:
        return s
    x, y, w, h = stage_frame(L).content(s.x, s.y, s.w, s.h)
    return Rect(x, y, w, h)


def hit_controls(L: Layout, px: float, py: float):
    """('key', i) | ('mode', 0) | ('cycle', d) | None -- what the pointer is over.

    Tests the SAME rectangles that were drawn: `control_geometry` is the only
    source of key positions in the program.
    """
    geo, mode = control_geometry(L)
    i = SEL.hit(geo, px, py)
    if i is not None:
        return ("key", i)
    if mode.valid and mode.x <= px <= mode.right and mode.y <= py <= mode.bottom:
        return ("mode", 0)
    cyc = cycle_rect(geo, mode)
    if cyc.valid and cyc.x <= px <= cyc.right and cyc.y <= py <= cyc.bottom:
        # Left half steps back, right half steps forward.
        return ("cycle", -1 if px < cyc.cx else +1)
    return None


# --------------------------------------------------------------------------
# small drawing primitives
# --------------------------------------------------------------------------
def _sparkline(cr, r: Rect, vals: list[float], rgb, alpha: float = 0.92) -> None:
    """Non-baked trace: every point comes from live telemetry."""
    if r.w < 6.0 or r.h < 4.0 or len(vals) < 2:
        return
    cr.save()
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.clip()
    n = len(vals)
    dx = r.w / (n - 1)
    cr.set_line_width(max(1.0, r.h * 0.035))
    cr.set_line_join(1)
    cr.set_source_rgba(*rgba(rgb, alpha))
    for i, v in enumerate(vals):
        px = r.x + i * dx
        py = r.bottom - v * r.h * 0.92 - r.h * 0.04
        if i == 0:
            cr.move_to(px, py)
        else:
            cr.line_to(px, py)
    cr.stroke()
    cr.restore()


def _bargraph(cr, r: Rect, level: float, rgb, segments: int = 28) -> None:
    """Segmented level fill for the meter trough, as the reference uses."""
    if r.w < 4.0 or r.h < 2.0:
        return
    lv = 0.0 if level < 0.0 else (1.0 if level > 1.0 else level)
    gap = max(0.6, r.w / segments * 0.30)
    sw = (r.w - gap * (segments - 1)) / segments
    if sw <= 0.2:
        cr.set_source_rgba(*rgba(rgb, 0.85))
        cr.rectangle(r.x, r.y, r.w * lv, r.h)
        cr.fill()
        return
    lit = lv * segments
    for i in range(segments):
        frac = min(1.0, max(0.0, lit - i))
        if frac <= 0.02:
            a = 0.10
        else:
            a = 0.30 + 0.62 * frac
        cr.set_source_rgba(*rgba(rgb, a))
        cr.rectangle(r.x + i * (sw + gap), r.y, sw, r.h)
        cr.fill()


_RING_CACHE: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()
_RING_LIMIT = 8


def _rings(cr, cx: float, cy: float, rad: float, alpha: float) -> None:
    """Dotted radial rings, cached.

    A dashed arc is one of the more expensive things Cairo can be asked for and
    these are redrawn every frame at a size that changes only on resize, so the
    ring set is rasterised once per field size and then blitted.
    """
    if rad < 8.0:
        return
    key = (int(round(rad)), round(alpha, 2))
    surf = _RING_CACHE.get(key)
    side = int(rad * 2.0) + 4
    if surf is None:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, side, side)
        c2 = cairo.Context(surf)
        c2.set_line_width(1.0)
        mid = side * 0.5
        for k in (0.22, 0.42, 0.62, 0.81, 1.0):
            c2.save()
            c2.set_source_rgba(*rgba(RULE, (0.72 if k == 1.0 else 0.50) * alpha))
            c2.set_dash([1.6, 5.0])
            c2.arc(mid, mid, rad * k, 0.0, TAU)
            c2.stroke()
            c2.restore()
        surf.flush()
        _RING_CACHE[key] = surf
        while len(_RING_CACHE) > _RING_LIMIT:
            _RING_CACHE.popitem(last=False)
    else:
        _RING_CACHE.move_to_end(key)
    cr.save()
    cr.set_source_surface(surf, round(cx - side * 0.5), round(cy - side * 0.5))
    cr.paint()
    cr.restore()


def _polar_grid(cr, sc: "Scope", r: Rect, alpha: float = 1.0, t=None) -> None:
    """Observation graticule, drawn CONCENTRIC WITH THE SPECIMEN.

    The rings, axes, ticks and cardinal marks are built from the scope - the
    same centre and design radius the viewport gives the organism - not from
    whatever rectangle happened to be left over after the annotation gutters
    were taken. That is what makes X+ actually sit on the +x axis of the
    creature instead of on the middle of a widget.
    """
    if r.w < 60.0 or r.h < 60.0:
        return
    cx, cy, rad = sc.cx, sc.cy, sc.rad
    cr.save()
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.clip()
    cr.set_line_width(1.0)

    _rings(cr, cx, cy, rad, alpha)

    # axes reach the field edges, as an instrument's crosshair does
    cr.set_source_rgba(*rgba(RULE, 0.85 * alpha))
    cr.move_to(max(r.x, sc.axis_x0), round(cy) + 0.5)
    cr.line_to(min(r.right, sc.axis_x1), round(cy) + 0.5)
    cr.move_to(round(cx) + 0.5, max(r.y, sc.axis_y0))
    cr.line_to(round(cx) + 0.5, min(r.bottom, sc.axis_y1))
    cr.stroke()

    # regular ticks along both axes, pitched off the design radius
    cr.set_source_rgba(*rgba(RULE, 0.95 * alpha))
    step = rad * 0.2
    n = int(max(sc.axis_x1 - cx, sc.axis_y1 - cy) / step)
    for i in range(1, n + 1):
        d = i * step
        tk = 3.0 if i % 5 else 5.5
        for sx in (-1, 1):
            px = cx + sx * d
            if r.x < px < r.right and sc.axis_x0 <= px <= sc.axis_x1:
                cr.move_to(round(px) + 0.5, cy - tk)
                cr.line_to(round(px) + 0.5, cy + tk)
            py = cy + sx * d
            if r.y < py < r.bottom and sc.axis_y0 <= py <= sc.axis_y1:
                cr.move_to(cx - tk, round(py) + 0.5)
                cr.line_to(cx + tk, round(py) + 0.5)
    cr.stroke()

    # cardinal crosses on the design radius
    for ang in (0.0, TAU * 0.25, TAU * 0.5, TAU * 0.75):
        px, py = cx + math.cos(ang) * rad, cy + math.sin(ang) * rad
        k = max(3.0, rad * 0.028)
        cr.move_to(px - k, py)
        cr.line_to(px + k, py)
        cr.move_to(px, py - k)
        cr.line_to(px, py + k)
    cr.stroke()
    cr.restore()


def _cardinals(cr, sc: "Scope", r: Rect, t, gutter: float = 0.0,
               alpha: float = 1.0) -> None:
    """X+ / X- / Y+ / Y-, pinned to the SCOPE's own axes at the field edge."""
    if r.w < 260.0 or r.h < 200.0:
        return
    sz = max(MIN_TEXT, min(t.micro, 10.0))
    pad = sz * 0.9
    x_lo = max(r.x + gutter, sc.axis_x0)
    for lab, ax, ay, al, base in (
            ("Y+", sc.cx, max(r.y, sc.axis_y0), "c",
             max(r.y, sc.axis_y0) + _cap(sz) * 1.15),
            ("Y-", sc.cx, min(r.bottom, sc.axis_y1), "c",
             min(r.bottom, sc.axis_y1) - _cap(sz) * 0.35),
            ("X-", x_lo + pad, sc.cy, "l", sc.cy - _cap(sz) * 0.55),
            ("X+", min(r.right, sc.axis_x1) - pad, sc.cy, "r",
             sc.cy - _cap(sz) * 0.55)):
        _show(cr, lab, sz, _W_NORMAL, t.tracking, ax, base, INK_DIM,
              0.74 * alpha, al)


#: Under this height the recessed plaque is all bevel; use `_rail` instead.
_PLAQUE_MIN_H = 56.0


def _rail(cr, r: Rect, depth: float = 0.55) -> Rect:
    """A thin machined inset strip, drawn procedurally. Returns its interior.

    The reference machine's archive rail and status bays are shallow recesses
    cut into the fascia, not applied plaques. Drawing them in code rather than
    9-slicing a plaque means they stay correct at any height: a raster bevel
    cannot shrink below its own thickness, and these rails are routinely 36px
    tall against a plaque bevel of 18.
    """
    if r.w < 8.0 or r.h < 6.0:
        return r
    cr.save()
    g = cairo.LinearGradient(r.x, r.y, r.x, r.bottom)
    g.add_color_stop_rgba(0.0, 0.085, 0.095, 0.105, 1.0)
    g.add_color_stop_rgba(0.55, 0.055, 0.062, 0.070, 1.0)
    g.add_color_stop_rgba(1.0, 0.075, 0.083, 0.092, 1.0)
    cr.set_source(g)
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.fill()
    cr.set_line_width(1.0)
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.55 * depth)
    cr.move_to(r.x, r.y + 0.5)
    cr.line_to(r.right, r.y + 0.5)
    cr.stroke()
    cr.set_source_rgba(0.62, 0.66, 0.68, 0.30 * depth)
    cr.move_to(r.x, r.bottom - 0.5)
    cr.line_to(r.right, r.bottom - 0.5)
    cr.stroke()
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.35 * depth)
    cr.move_to(r.x + 0.5, r.y)
    cr.line_to(r.x + 0.5, r.bottom)
    cr.move_to(r.right - 0.5, r.y)
    cr.line_to(r.right - 0.5, r.bottom)
    cr.stroke()
    cr.restore()
    pad = max(2.0, min(6.0, r.h * 0.10))
    return Rect(r.x + pad * 1.6, r.y + pad, r.w - pad * 3.2, r.h - pad * 2.0)


def _divider(cr, x: float, y0: float, y1: float, alpha: float = 1.0) -> None:
    """A machined separator between compartments: dark groove, bright lip."""
    if y1 - y0 < 3.0:
        return
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.55 * alpha)
    cr.move_to(round(x) + 0.5, y0)
    cr.line_to(round(x) + 0.5, y1)
    cr.stroke()
    cr.set_source_rgba(0.55, 0.60, 0.62, 0.22 * alpha)
    cr.move_to(round(x) + 1.5, y0)
    cr.line_to(round(x) + 1.5, y1)
    cr.stroke()
    cr.restore()


def _hairline(cr, x0: float, x1: float, y: float, rgb=RULE,
              alpha: float = 0.9) -> None:
    if x1 - x0 < 2.0:
        return
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba(rgb, alpha))
    cr.move_to(x0, round(y) + 0.5)
    cr.line_to(x1, round(y) + 0.5)
    cr.stroke()
    cr.restore()


def _draw_chassis(cr, L: Layout) -> None:
    """The outer enclosure: the thing that makes this read as ONE machine.

    Assembled, not stretched: a plate for the body, a real slotted screw sunk
    at each corner, and a handle rail run down each side member - exactly the
    parts the reference console is built from.
    """
    c = L.chassis
    if not L.show_chassis or not c.valid:
        return
    draw_nine(cr, C.PLATE, c.x, c.y, c.w, c.h)

    wall = max(10.0, min(c.w, c.h) * 0.020)
    inset = wall * 0.78
    scr = min(wall * 1.55, 30.0)
    for sx, sy in ((c.x + inset, c.y + inset),
                   (c.right - inset, c.y + inset),
                   (c.x + inset, c.bottom - inset),
                   (c.right - inset, c.bottom - inset)):
        draw_sprite_fit(cr, "part/screw_large", sx, sy, scr)

    rail_h = c.h * 0.42
    rail_w = min(wall * 0.62, 15.0)
    if rail_h > 60.0 and rail_w > 4.0:
        for rx in (c.x + inset - rail_w * 0.5, c.right - inset - rail_w * 0.5):
            draw_sprite_rot90(cr, "part/handle", rx, c.cy - rail_h * 0.5,
                              rail_w, rail_h)


def _engrave(cr, text: str, r: Rect, size: float, tracking: float,
             rgb=INK, align: str = "c", alpha: float = 1.0) -> None:
    """Label text sitting in a machined recess.

    Drawn twice: a dark offset copy first, so the glyphs read as cut into the
    metal rather than printed on it.
    """
    if not text or r.w < 4.0 or size < 5.0:
        return
    x = r.cx if align == "c" else (r.x if align == "l" else r.right)
    base = r.cy + _cap(size) * 0.5
    _show(cr, text, size, _W_NORMAL, tracking, x, base + 1.0,
          (0.02, 0.03, 0.04), 0.75 * alpha, align, r.w)
    _show(cr, text, size, _W_NORMAL, tracking, x, base, rgb, alpha, align, r.w)


# --------------------------------------------------------------------------
# typography inside a hardware bay
# --------------------------------------------------------------------------
# Every text region on this machine obeys the same five rules, and they are
# implemented here once rather than re-invented per call site:
#
#   1. a physical bay          - a recess measured off the generated asset
#   2. internal padding        - proportional, with a pixel floor
#   3. a defined baseline      - from the bay's own box, never the panel's
#   4. a defined alignment     - left / centre / right, declared per line
#   5. a responsive elision    - shrink to the floor, then ellipsise
#
# Nothing draws type outside a bay. That is what stops a label landing on a
# bevel crest or drifting across a compartment when the window is resized.

#: Bay padding as a share of the bay box, with pixel floors.
_BAY_PAD_X = 0.050
_BAY_PAD_Y = 0.115
_BAY_PAD_X_MIN = 3.0
_BAY_PAD_Y_MIN = 2.0


def bay_inner(r: Rect, sx: float = 1.0, sy: float = 1.0) -> Rect:
    """The writable interior of a bay: the recess minus its internal padding."""
    if not r.valid:
        return r
    px = max(_BAY_PAD_X_MIN, r.w * _BAY_PAD_X) * sx
    py = max(_BAY_PAD_Y_MIN, r.h * _BAY_PAD_Y) * sy
    px = min(px, r.w * 0.30)
    py = min(py, r.h * 0.30)
    return Rect(r.x + px, r.y + py, r.w - px * 2.0, r.h - py * 2.0)


def _elide(text: str, size: float, weight: int, tracking: float,
           max_w: float) -> str:
    """Shorten `text` until it fits `max_w`, ending in a single ellipsis.

    Measured, not estimated: a monospace face still has per-glyph tracking and
    the caller's letter-spacing on top, so guessing character counts clips.
    """
    if max_w <= 0.0 or not text:
        return ""
    if _text_w(text, size, weight, tracking) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_w(text[:mid] + "\u2026", size, weight, tracking) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + "\u2026") if lo else ""


def bay_line(cr, r: Rect, text: str, size: float, weight: int, tracking: float,
             rgb, alpha: float = 1.0, align: str = "l",
             baseline: float | None = None, x: float | None = None,
             min_size: float = MIN_TEXT) -> float:
    """One line of type inside a bay. Returns the width actually drawn.

    `baseline` is an absolute y; when omitted the line is centred on the bay's
    own vertical middle by cap height, which is what keeps a row of bays on one
    optical baseline even when their boxes differ by a pixel or two.
    """
    if not text or not r.valid:
        return 0.0
    sz = size
    if sz < min_size:
        sz = min_size
    avail = r.w if x is None else (
        (r.right - x) if align == "l" else (x - r.x) if align == "r" else r.w)
    if avail <= 1.0:
        return 0.0
    # Shrink before ellipsising: a slightly smaller full label beats a clipped
    # one, but only down to the legibility floor.
    while sz > min_size and _text_w(text, sz, weight, tracking) > avail:
        sz = max(min_size, sz - 0.5)
    txt = _elide(text, sz, weight, tracking, avail)
    if not txt:
        return 0.0
    base = baseline if baseline is not None else (r.cy + _cap(sz) * 0.5)
    if x is not None:
        ax = x
    else:
        ax = r.x if align == "l" else (r.right if align == "r" else r.cx)
    return _show(cr, txt, sz, weight, tracking, ax, base, rgb, alpha, align,
                 avail)


def bay_pair(cr, r: Rect, key: str, value: str, size: float, t,
             value_rgb=INK_BRIGHT, key_rgb=INK_DIM, alpha: float = 1.0,
             baseline: float | None = None) -> None:
    """A KEY / VALUE pair sharing one bay line: key left, value right of it.

    The key column is measured from the key actually present, so a long key
    never collides with its value and a short one never strands it.
    """
    if not r.valid:
        return
    sz = max(MIN_TEXT, size)
    base = baseline if baseline is not None else (r.cy + _cap(sz) * 0.5)
    kw = _show(cr, key, sz, _W_NORMAL, t.tracking, r.x, base, key_rgb,
               0.86 * alpha, "l", r.w * 0.62)
    vx = r.x + kw + sz * 0.85
    if r.right - vx > sz:
        bay_line(cr, Rect(vx, r.y, r.right - vx, r.h), value, sz, _W_MEDIUM,
                 t.tracking * 0.5, value_rgb, 0.96 * alpha, "l", base)


def bay_stack(cr, r: Rect, key: str, value: str, k_size: float, v_size: float,
              t, value_rgb=INK_BRIGHT, alpha: float = 1.0) -> None:
    """A KEY above its VALUE, both inside one bay, on fixed baselines.

    This is the archive-rail arrangement: the key is a fixed micro caption
    pinned to the top of the recess, the value sits on the lower baseline.
    """
    if not r.valid:
        return
    ks = max(MIN_TEXT, k_size)
    vs = max(MIN_TEXT, v_size)
    stack = _cap(ks) * 1.15 + _cap(vs) * 1.45
    if stack > r.h:
        # Not enough recess for two lines. The VALUE is what the bay exists
        # to show, so the caption goes rather than both being clipped.
        bay_line(cr, r, value, min(vs, r.h * 0.86), _W_MEDIUM,
                 t.tracking * 0.5, value_rgb, 0.96 * alpha, "l")
        return
    top = r.y + max(0.0, (r.h - stack) * 0.5)
    bay_line(cr, r, key, ks, _W_NORMAL, t.tracking, INK_DIM, 0.82 * alpha, "l",
             top + _cap(ks))
    bay_line(cr, r, value, vs, _W_MEDIUM, t.tracking * 0.5, value_rgb,
             0.96 * alpha, "l", top + _cap(ks) * 1.15 + _cap(vs) * 1.30)


def header_panel(L: Layout) -> Rect:
    """Header and its status rail are ONE physical fascia, so they are one rect.

    The generated header asset is a single panel carrying both rows of bays.
    Drawing two separate strips is exactly the "programmatic overlay" read the
    asset exists to remove.
    """
    h = L.header
    if not h.valid:
        return h
    if L.show_status and L.status.valid:
        return Rect(h.x, h.y, h.w, L.status.bottom - h.y)
    return h


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------
def _draw_header(cr, L: Layout, m: ConsoleModel, light: LightField,
                 static: bool = True, live: bool = True) -> None:
    """The command fascia, with every line of type seated in a real recess.

    The generated header asset is a manufactured panel: four information bays
    over a three-bay status rail, a lamp boss and a small window cast into the
    third bay. Nothing here invents a black rectangle - it asks the asset where
    its recesses are and writes inside them.
    """
    r = header_panel(L)
    if not r.valid:
        return
    t = L.type
    sp = m.species

    if sprite_size(F.HEADER.name)[0] == 0 or r.w < 420.0 or r.h < 34.0:
        _draw_header_compact(cr, L, r, m, light)
        return

    P = F.HEADER.place(r) if not static else F.HEADER.draw(cr, r)

    # --- bay 1: instrument name over specimen name ------------------------
    b = bay_inner(P.bay("title")) if static else Rect(0, 0, 0, 0)
    if b.valid:
        two = b.h >= 26.0
        name_sz = min(t.specimen, b.h * (0.50 if two else 0.86))
        title_sz = min(t.title, b.h * 0.27)
        if two:
            stack = _cap(title_sz) * 1.20 + _cap(name_sz) * 1.36
            top = b.y + max(0.0, (b.h - stack) * 0.5)
            bay_line(cr, b, "ABYSSAL ORGANISM MONITOR", title_sz, _W_NORMAL,
                     t.tracking, INK, 0.80, "l", top + _cap(title_sz))
            base2 = top + _cap(title_sz) * 1.20 + _cap(name_sz) * 1.30
            lead = _show(cr, "SPECIMEN", title_sz, _W_NORMAL, t.tracking,
                         b.x, base2, INK_DIM, 0.78, "l")
            nx = b.x + lead + title_sz * 1.1
            bay_line(cr, Rect(nx, b.y, b.right - nx, b.h), sp.name, name_sz,
                     _W_MEDIUM, t.tracking * 0.7, INK_BRIGHT, 1.0, "l", base2)
        else:
            bay_line(cr, b, sp.name, name_sz, _W_MEDIUM, t.tracking * 0.7,
                     INK_BRIGHT, 1.0, "l")

    # --- bay 2: vernacular name over the terminal designation -------------
    b = bay_inner(P.bay("epithet")) if static else Rect(0, 0, 0, 0)
    if b.valid:
        sub_sz = min(t.subtitle * 1.30, b.h * 0.46)
        micro_sz = max(MIN_TEXT, min(t.micro * 0.92, b.h * 0.30))
        stack = _cap(sub_sz) * 1.28 + _cap(micro_sz) * 1.50
        if stack > b.h:
            # One line or none: the terminal designation is the line to lose.
            bay_line(cr, b, sp.epithet, min(sub_sz, b.h * 0.88), _W_NORMAL,
                     t.tracking * 1.5, INK_BRIGHT, 0.94, "c")
        else:
            top = b.y + max(0.0, (b.h - stack) * 0.5)
            bay_line(cr, b, sp.epithet, sub_sz, _W_NORMAL, t.tracking * 1.5,
                     INK_BRIGHT, 0.94, "c", top + _cap(sub_sz))
            bay_line(cr, b, "BIOCOMPUTATIONAL OBSERVATION TERMINAL", micro_sz,
                     _W_NORMAL, t.tracking, INK_DIM, 0.74, "c",
                     top + _cap(sub_sz) * 1.28 + _cap(micro_sz) * 1.40)

    # --- bay 3: the LIVE annunciator, in the boss the asset provides ------
    lamp = P.bay("live_lamp") if static else Rect(0, 0, 0, 0)
    win = P.bay("live_window") if static else Rect(0, 0, 0, 0)
    if lamp.valid:
        d = min(lamp.h, lamp.w) * 1.06
        draw_sprite_fit(cr, C.lamp("small", "nominal"), lamp.cx, lamp.cy, d)
        light.add(lamp.cx, lamp.cy, d * 2.4, L_CHART, 0.20)
    if win.valid:
        wi = bay_inner(win, sy=0.6)
        bay_line(cr, wi, "LIVE", min(t.label, wi.h * 0.92), _W_MEDIUM,
                 t.tracking, LIME, 0.97, "c")

    # --- bay 4: date over the running clock -------------------------------
    # The clock is the one genuinely per-frame readout in this fascia, so it
    # is the one thing here that is NOT cached.
    b = bay_inner(P.bay("clock")) if live else Rect(0, 0, 0, 0)
    if b.valid:
        dsz = max(MIN_TEXT, min(t.micro * 0.90, b.h * 0.26))
        dh = min(b.h * 0.56, 22.0)
        top = b.y + max(0.0, (b.h - (_cap(dsz) * 1.55 + dh)) * 0.5)
        bay_line(cr, b, time.strftime("%Y-%m-%d"), dsz, _W_NORMAL, t.tracking,
                 INK_DIM, 0.80, "r", top + _cap(dsz))
        cw = SEG.measure(time.strftime("%H:%M:%S"), dh, SEG.CYAN)
        if cw > b.w:
            dh *= b.w / cw
        SEG.draw_right(cr, time.strftime("%H:%M:%S"), b.right,
                       top + _cap(dsz) * 1.55, dh, SEG.CYAN)
        light.glow(b, L_CYAN, 0.055, spread=0.55)

    if static:
        _draw_status_bays(cr, P, L, m)


def _status_bay_rows(m: ConsoleModel) -> tuple[tuple[tuple[str, str, object], ...], ...]:
    """Two key/value pairs per rail bay. Live values, never placeholders."""
    sp = m.species
    return (
        (("SYS BUS", "ONLINE", LIME), ("ARCHIVE", "READY", INK_BRIGHT)),
        (("INSTR", "NOMINAL", LIME), ("FIELD", sp.plan, INK_BRIGHT)),
        (("SPECIMEN", sp.archive, INK_BRIGHT),
         ("CHANNEL", f"{m.active + 1}/{len(CATALOGUE)}", INK_BRIGHT)),
    )


def _draw_status_bays(cr, P, L: Layout, m: ConsoleModel) -> None:
    """The fascia's lower rail: three bays, two readouts each."""
    t = L.type
    for i, pairs in enumerate(_status_bay_rows(m)):
        b = bay_inner(P.bay(f"rail_{i}"), sy=0.5)
        # A bay too short for its own type is left as blank machined metal.
        # Type crammed into a 6px recess is not density, it is a fault.
        if not b.valid or b.w < 150.0 or b.h < MIN_TEXT + 1.0:
            continue
        sz = max(MIN_TEXT, min(t.micro, b.h * 0.86))
        base = b.cy + _cap(sz) * 0.5
        half = b.w / len(pairs)
        for j, (k, v, col) in enumerate(pairs):
            cell = Rect(b.x + j * half, b.y, half - sz * 0.6, b.h)
            bay_pair(cr, cell, k, v, sz, t, value_rgb=col, baseline=base)


def _draw_header_compact(cr, L: Layout, r: Rect, m: ConsoleModel,
                         light: LightField) -> None:
    """Narrow states: one machined strip, two bays, same typography rules.

    Below the fascia's usable width its four bays would each be a few pixels
    wide, so the panel is replaced by a shallow recess carrying the two lines
    that still matter. This is a different arrangement, not a scaled one.
    """
    inner = _rail(cr, r, depth=0.9)
    if not inner.valid:
        return
    t = L.type
    sp = m.species
    pad = max(5.0, inner.h * 0.10)
    left = Rect(inner.x + pad, inner.y, inner.w * 0.62 - pad, inner.h)
    two = inner.h >= 32.0
    name_sz = min(t.specimen, inner.h * (0.46 if two else 0.70))
    if two:
        tsz = min(t.title, inner.h * 0.26)
        stack = _cap(tsz) * 1.20 + _cap(name_sz) * 1.34
        top = inner.y + max(0.0, (inner.h - stack) * 0.5)
        bay_line(cr, left, "ABYSSAL ORGANISM MONITOR", tsz, _W_NORMAL,
                 t.tracking, INK, 0.78, "l", top + _cap(tsz))
        bay_line(cr, left, sp.name, name_sz, _W_MEDIUM, t.tracking * 0.7,
                 INK_BRIGHT, 1.0, "l",
                 top + _cap(tsz) * 1.20 + _cap(name_sz) * 1.28)
    else:
        bay_line(cr, left, sp.name, name_sz, _W_MEDIUM, t.tracking * 0.7,
                 INK_BRIGHT, 1.0, "l")

    rx = inner.x + inner.w * 0.64
    bay = _rail(cr, Rect(rx, inner.y + inner.h * 0.16,
                         inner.right - rx - pad, inner.h * 0.68), depth=0.8)
    if not bay.valid or bay.w < 54.0:
        return
    d = min(bay.h * 1.0, 13.0)
    draw_sprite_fit(cr, C.lamp("small", "nominal"), bay.x + d * 0.6, bay.cy, d)
    light.add(bay.x + d * 0.6, bay.cy, d * 2.2, L_CHART, 0.16)
    lx = bay.x + d * 1.25
    lw = bay_line(cr, Rect(lx, bay.y, bay.right - lx, bay.h), "LIVE",
                  min(t.label, bay.h * 0.68), _W_MEDIUM, t.tracking, LIME,
                  0.96, "l")
    cx0 = lx + lw + d * 0.5
    if bay.right - cx0 > 54.0:
        dh = min(bay.h * 0.80, 16.0)
        SEG.draw_right(cr, time.strftime("%H:%M:%S"), bay.right - 2.0,
                       bay.cy - dh * 0.5, dh, SEG.CYAN)


def _draw_status_rail(cr, L: Layout, m: ConsoleModel) -> None:
    """Kept as a no-op hook: the rail is now part of the header fascia."""
    return


# --------------------------------------------------------------------------
# observation bezel
# --------------------------------------------------------------------------
def _draw_stage(cr, L: Layout, m: ConsoleModel, light: LightField) -> None:
    """The bezel overlay. Called AFTER the organism so the metal occludes it."""
    s = L.stage
    if not s.valid:
        return
    draw_nine(cr, stage_frame(L), s.x, s.y, s.w, s.h)
    glass = stage_content(L)
    if glass.valid:
        light.glow(glass, L_CYAN, 0.07, spread=0.40)


#: The bezel's inner bevel overlaps the glass rect, so annotations placed flush
#: to the glass edge are cut by metal. Everything drawn on the field is inset
#: by this much first.
_FIELD_INSET_X = 0.055
_FIELD_INSET_Y = 0.075


def field_area(L: Layout) -> Rect:
    """The glass, inset clear of the bezel's inner lip."""
    g = stage_content(L)
    if not g.valid:
        return g
    return g.inset(g.w * _FIELD_INSET_X, g.h * _FIELD_INSET_Y)


@dataclass(frozen=True, slots=True)
class Scope:
    """The specimen's own coordinate frame, in widget pixels.

    THE observation field's single source of truth. Its centre and design
    radius are derived exactly as `core.viewport.Viewport` derives them from
    the same glass rect, so everything drawn against a Scope - rings, axes,
    ticks, cardinal labels, the radius ruler, the scale bar - is registered
    to the creature rather than to the widget it happens to sit in.
    """

    cx: float
    cy: float
    rad: float          # WORLD_RADIUS in pixels
    axis_x0: float      # how far the drawn crosshair reaches
    axis_x1: float
    axis_y0: float
    axis_y1: float

    @property
    def left(self) -> float:
        return self.cx - self.rad

    @property
    def right(self) -> float:
        return self.cx + self.rad

    @property
    def top(self) -> float:
        return self.cy - self.rad

    @property
    def bottom(self) -> float:
        return self.cy + self.rad


#: WORLD_RADIUS / WORLD_SIZE. Kept local so ui/ does not import the world.
_SCOPE_R = 0.46


def scope_of(L: Layout) -> Scope:
    """Build the Scope for this layout. Pure; safe to call every frame."""
    g = stage_content(L)
    if not g.valid:
        return Scope(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    rad = min(g.w, g.h) * _SCOPE_R
    f = field_area(L)
    return Scope(cx=g.cx, cy=g.cy, rad=rad,
                 axis_x0=max(f.x, g.cx - g.w * 0.47),
                 axis_x1=min(f.right, g.cx + g.w * 0.47),
                 axis_y0=max(f.y, g.cy - g.h * 0.47),
                 axis_y1=min(f.bottom, g.cy + g.h * 0.47))


def _corner_brackets(cr, r: Rect, size: float, alpha: float = 0.7) -> None:
    """Registration brackets at the field corners, as an optical instrument has."""
    if r.w < size * 4 or r.h < size * 4:
        return
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba(INK_DIM, alpha))
    for cx, cy, dx, dy in ((r.x, r.y, 1, 1), (r.right, r.y, -1, 1),
                           (r.x, r.bottom, 1, -1), (r.right, r.bottom, -1, -1)):
        cr.move_to(cx + dx * 0.5, cy + dy * size)
        cr.line_to(cx + dx * 0.5, cy + dy * 0.5)
        cr.line_to(cx + dx * size, cy + dy * 0.5)
    cr.stroke()
    cr.restore()


def _radius_ruler(cr, sc: Scope, r: Rect, t, alpha: float = 1.0,
                  indent: float = 0.0) -> float:
    """The RADIUS (mm) axis down the left edge, graduated off the SCOPE.

    Tick spacing comes from the scope's design radius, so 1.25 mm is exactly
    the outermost ring the creature can reach. It describes the specimen on
    screen rather than being a decorative column of numbers.
    """
    if r.h < 170.0 or r.w < 210.0 or sc.rad < 40.0:
        return 0.0
    sz = max(MIN_TEXT, min(t.micro * 0.92, r.h * 0.030))
    lab_w = _text_w("1.25", sz, _W_NORMAL, t.tracking)
    x_tick = r.x + lab_w + sz * 1.5
    steps = (1.25, 1.00, 0.75, 0.50, 0.25, 0.0, 0.25, 0.50, 0.75, 1.00, 1.25)
    # Half-span is the scope radius itself, clamped into the field.
    span = min(sc.rad, (r.h * 0.5) - _cap(sz) * 2.2)
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba(RULE, 0.9 * alpha))
    cr.move_to(round(x_tick) + 0.5, sc.cy - span)
    cr.line_to(round(x_tick) + 0.5, sc.cy + span)
    cr.stroke()
    cr.restore()
    for i, v in enumerate(steps):
        yy = sc.cy - span + (2.0 * span) * (i / (len(steps) - 1.0))
        cr.save()
        cr.set_line_width(1.0)
        cr.set_source_rgba(*rgba(RULE, 0.95 * alpha))
        cr.move_to(x_tick - sz * (0.45 if i % 5 else 0.8), round(yy) + 0.5)
        cr.line_to(x_tick, round(yy) + 0.5)
        cr.stroke()
        cr.restore()
        _show(cr, f"{v:.2f}", sz, _W_NORMAL, t.tracking, x_tick - sz * 0.95,
              yy + _cap(sz) * 0.5, INK_DIM, 0.82 * alpha, "r")
    # The caption heads its own scale, and the gutter it reports back is wide
    # enough for the caption as well as the tick labels - so the SPECIMEN
    # FIELD zone begins clear of it rather than on top of it.
    _show(cr, "RADIUS (mm)", sz, _W_NORMAL, t.tracking, r.x + indent,
          sc.cy - span - _cap(sz) * 1.6, INK_DIM, 0.80 * alpha, "l")
    cap_w = _text_w("RADIUS (mm)", sz, _W_NORMAL, t.tracking)
    return max(x_tick - r.x + sz * 1.2, indent + cap_w + sz * 1.0)


def _scale_bar(cr, sc: Scope, r: Rect, t, alpha: float = 1.0) -> float:
    """The SCALE (mm) reference, bottom right. Returns the height it used.

    Graduated from the scope, so the bar is a true magnification reference:
    its full length is exactly 1.5 mm of field at the current viewport.
    """
    if r.w < 300.0 or r.h < 190.0 or sc.rad < 40.0:
        return 0.0
    sz = max(MIN_TEXT, min(t.micro * 0.92, r.h * 0.028))
    bw = min(r.w * 0.30, sc.rad * 0.78, 220.0)
    bx = r.right - bw
    by = r.bottom - _cap(sz) * 2.0
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba(INK_DIM, 0.85 * alpha))
    cr.move_to(bx, round(by) + 0.5)
    cr.line_to(bx + bw, round(by) + 0.5)
    for i in range(13):
        tx = bx + bw * (i / 12.0)
        hh = sz * (0.78 if i % 4 == 0 else 0.42)
        cr.move_to(round(tx) + 0.5, by)
        cr.line_to(round(tx) + 0.5, by - hh)
    cr.stroke()
    cr.restore()
    for i, v in enumerate(("0", "0.5", "1.0", "1.5")):
        _show(cr, v, sz, _W_NORMAL, t.tracking, bx + bw * (i / 3.0),
              by - sz * 1.25, INK_DIM, 0.82 * alpha, "c")
    _show(cr, "SCALE (mm)", sz, _W_NORMAL, t.tracking, bx + bw * 0.5,
          by + sz * 1.45, INK_DIM, 0.78 * alpha, "c")
    return _cap(sz) * 4.6


def _field_block(cr, x: float, y: float, w: float, rows, t,
                 heading: str | None = None, alpha: float = 1.0,
                 align_r: bool = False, measure_only: bool = False) -> float:
    """One annotation zone inside the observation field. Returns its height.

    `measure_only` lays the block out without drawing, so a bottom-anchored
    zone can be positioned from its true height instead of a guess.
    """
    # Deliberately the densest type on the machine. At ARCHIVE this lands
    # near 8px, which is where the reference console's field annotations sit
    # relative to its own width - the readings must be legible without ever
    # competing with the specimen.
    sz = max(MIN_TEXT, min(t.micro * 0.86, 9.2))
    line = _cap(sz) * 1.98
    yy = y
    if heading:
        hz = sz * 1.12
        if not measure_only:
            _show(cr, heading, hz, _W_MEDIUM, t.tracking * 1.2,
                  (x + w) if align_r else x, yy + _cap(hz), INK_BRIGHT,
                  0.95 * alpha, "r" if align_r else "l", w)
            hw = _text_w(heading, hz, _W_MEDIUM, t.tracking * 1.2)
            _hairline(cr, (x + w - hw) if align_r else x,
                      (x + w) if align_r else (x + hw), yy + _cap(hz) * 1.45,
                      RULE, 0.9 * alpha)
        yy += line * 1.18
    keyw = min(w * 0.66,
               max(_text_w(k + ":", sz, _W_NORMAL, t.tracking)
                   for k, _ in rows) + sz * 0.9)
    for k, v in rows:
        base = yy + _cap(sz)
        # A value wider than its column drops to a line of its own rather
        # than being ellipsised. A truncated measurement is worse than a
        # taller block: the whole point of the field is that it reads true.
        wide = _text_w(v, sz, _W_MEDIUM, t.tracking * 0.6) > (w - keyw)
        if not measure_only:
            if align_r:
                _show(cr, k + ":", sz, _W_NORMAL, t.tracking,
                      x + w - (0.0 if wide else keyw + sz * 0.6), base,
                      INK_DIM, 0.80 * alpha, "r", w)
                _show(cr, v, sz, _W_MEDIUM, t.tracking * 0.6, x + w,
                      base + (line if wide else 0.0), INK, 0.92 * alpha, "r",
                      w if wide else (w - keyw))
            else:
                _show(cr, k + ":", sz, _W_NORMAL, t.tracking, x, base,
                      INK_DIM, 0.80 * alpha, "l", w if wide else keyw)
                _show(cr, v, sz, _W_MEDIUM, t.tracking * 0.6,
                      x + (0.0 if wide else keyw),
                      base + (line if wide else 0.0), INK, 0.92 * alpha, "l",
                      w if wide else (w - keyw))
        yy += line * (2.0 if wide else 1.0)
    return yy - y


def _field_zones(L: Layout, m: ConsoleModel):
    """Geometry of the observation field's six zones. Pure.

    Both passes - the cached measuring furniture and the live readings - take
    their positions from this one call, so a cached graticule and the text
    drawn over it cannot disagree about where the gutter ends.
    """
    glass = stage_content(L)
    sc = scope_of(L)
    safe = field_area(L)
    bracket = min(glass.w, glass.h) * 0.035 if glass.valid else 0.0
    med = glass.w >= 360.0 and glass.h >= 250.0
    big = glass.w >= 560.0 and glass.h >= 330.0
    return (glass, sc, safe, bracket, med, big)


def _draw_field_static(cr, L: Layout, m: ConsoleModel) -> None:
    """The field's measuring furniture: glass, brackets, ruler, grid, scale.

    Every mark here is a function of the field geometry alone. It changes on
    resize and at no other time, which is exactly what makes it cacheable.
    """
    glass, sc, safe, bracket, med, big = _field_zones(L, m)
    if not glass.valid:
        return
    # The chassis plate sits behind everything, so the bezel's transparent
    # aperture would show METAL. Fill the glass first: this is the specimen
    # chamber, and it has to be the darkest thing on the machine.
    cr.save()
    g = cairo.LinearGradient(glass.x, glass.y, glass.x, glass.bottom)
    g.add_color_stop_rgb(0.0, 0.024, 0.040, 0.050)
    g.add_color_stop_rgb(0.55, 0.014, 0.026, 0.034)
    g.add_color_stop_rgb(1.0, 0.020, 0.034, 0.043)
    cr.set_source(g)
    cr.rectangle(glass.x, glass.y, glass.w, glass.h)
    cr.fill()
    cr.restore()

    t = L.type
    _corner_brackets(cr, safe, bracket)
    gutter = (_radius_ruler(cr, sc, safe, t, indent=bracket * 0.85)
              if med else 0.0)
    _polar_grid(cr, sc, glass, t=t)
    _cardinals(cr, sc, safe, t, gutter)
    if big:
        _scale_bar(cr, sc, safe, t)


def _field_gutter(L: Layout, m: ConsoleModel) -> float:
    """Width the radius ruler reserved, without drawing it."""
    glass, sc, safe, bracket, med, _ = _field_zones(L, m)
    if not med or not glass.valid:
        return 0.0
    return _radius_ruler(_NULL_CR, sc, safe, L.type, indent=bracket * 0.85)


def _draw_field_live(cr, L: Layout, m: ConsoleModel) -> None:
    """The readings: four annotation zones, every value from live state.

    ZONES
    -----
        top left      SPECIMEN FIELD  (mode, magnification, field, optics)
        top right     MORPHOLOGY      (phase, rotation, symmetry, behaviour)
        bottom left   VECTOR FIELD    (axis lock, tracking, coordinates)

    Each is width-clamped so it cannot reach the vertical axis, which is what
    guarantees the specimen stays the dominant object however long a value
    string becomes. Zones drop out in a fixed order as the field shrinks.
    """
    glass, sc, safe, bracket, med, big = _field_zones(L, m)
    if not glass.valid or not med:
        return
    t = L.type
    sp = m.species
    gutter = _field_gutter(L, m)

    left_x = safe.x + gutter + glass.w * 0.012
    max_w = min(glass.w * 0.345, (sc.cx - left_x) - glass.w * 0.045, 320.0)
    bw = max(90.0, max_w if big else min(glass.w * 0.44,
                                         sc.cx - left_x - 8.0))

    # A narrower field drops rows rather than ellipsising them: an observation
    # annotation that cannot be read in full is worse than one not shown,
    # because the reader cannot tell which it is.
    rows = [("MODE", "LIVE OBSERVATION"),
            ("MAGNIFICATION", f"{m.magnification:.1f}x"),
            ("FIELD WIDTH", f"{m.field_mm:.2f} mm"),
            ("FOCUS", "AUTO"),
            ("APERTURE", m.aperture)]
    if not big:
        rows = [("MODE", "LIVE"),
                ("MAG", f"{m.magnification:.1f}x"),
                ("FIELD", f"{m.field_mm:.2f} mm")]
    _field_block(cr, left_x, safe.y, bw, tuple(rows), t,
                 heading="SPECIMEN FIELD")
    if not big:
        return

    right_edge = safe.right - glass.w * 0.012
    right_w = min(max_w, right_edge - (sc.cx + glass.w * 0.045))
    if right_w > 90.0:
        _field_block(cr, right_edge - right_w, safe.y, right_w, (
            ("PHASE", f"{m.phase:.3f} \u03c0"),
            ("ROTATION", f"{m.rotation:.2f} RPM"),
            ("SYMMETRY", sp.symmetry_short),
            ("BEHAVIOR", m.behavior),
        ), t, heading=sp.morphology, align_r=True)

    cx, cy, cz = m.coords
    vrows = (("AXIS LOCK", "STABLE"),
             ("TRACKING", "CENTROID"),
             ("COORDINATES", f"{cx:+.3f}, {cy:+.3f}, {cz:+.3f} mm"))
    h = _field_block(cr, left_x, 0.0, bw, vrows, t, heading="VECTOR FIELD",
                     measure_only=True)
    _field_block(cr, left_x, safe.bottom - h, bw, vrows, t,
                 heading="VECTOR FIELD")


# --------------------------------------------------------------------------
# telemetry modules
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Channel:
    key: str
    title: str
    sub: str
    unit: str                          # drawn IN the segment display
    caption: str                       # inside the graph well, lower ledge
    axis: tuple[str, str, str]         # graph y-axis ticks, high -> low
    lamps: tuple[str, str, str, str]   # annunciator micro-labels
    span: str = "60 s"                 # time span indicator
    bars: bool = False                 # graph shows a bargraph, not a trace


_CHANNELS = (
    _Channel("cpu", "PROCESSOR", "BIOCOMPUTATIONAL CORE", "%",
             "CPU LOAD", ("100", "50", "0"),
             ("CLK", "ALG", "NET", "I/O")),
    _Channel("thermal", "THERMAL", "ENVIRONMENT & METABOLIC", "\u00b0C",
             "CORE TEMP", ("120", "80", "40"),
             ("SEN", "REG", "FAN", "SYS")),
    _Channel("memory", "MEMORY", "FIELD DATA & STATE", "G",
             "RESIDENT SET", ("13.5", "6.8", "0"),
             ("MEM", "BUF", "CACHE", "I/O"), bars=True),
    _Channel("frame", "FRAME / RENDER", "VISUALISATION PIPELINE", "FPS",
             "FRAME TIME", ("33", "16", "0"),
             ("REN", "IMG", "DSP", "SYNC")),
)


def _status_rows(ch: str, tel: Telemetry, fps: float, frame_ms: float,
                 m: "ConsoleModel"):
    """The four-row TARGET / PEAK / RANGE / delta / STATE column.

    Every value is live; the reference shows the same shape of information for
    each subsystem, which is a large part of why its modules read as dense.
    """
    tr = m.trace(ch).values
    peak = max(tr) if tr else 0.0
    if ch == "cpu":
        d = (tr[-1] - tr[-13]) * 100.0 if len(tr) > 13 else 0.0
        return (("TARGET", "100%"), ("PEAK", f"{peak * 100:.0f}%"),
                ("\u0394", f"{d:+.0f}%"))
    if ch == "thermal":
        d = (tr[-1] - tr[-13]) * 120.0 if len(tr) > 13 else 0.0
        return (("TARGET", "100\u00b0C"), ("RANGE", "0-120"),
                ("\u0394", f"{d:+.1f}\u00b0C"))
    if ch == "memory":
        d = (tr[-1] - tr[-13]) * max(tel.mem_total_gb, 1.0) if len(tr) > 13 else 0.0
        return (("TARGET", f"{tel.mem_total_gb:.1f}G"),
                ("PEAK", f"{peak * max(tel.mem_total_gb, 1.0):.1f}G"),
                ("\u0394", f"{d:+.1f}G"))
    d = (tr[-1] - tr[-13]) * 120.0 if len(tr) > 13 else 0.0
    return (("TARGET", "60+"), ("RANGE", "0-33"), ("\u0394", f"{d:+.1f}"))


def _channel_values(ch: str, tel: Telemetry, fps: float, frame_ms: float):
    """(display text, level 0..1, segment style, state word, lamp state)."""
    if ch == "cpu":
        lv = tel.cpu_load
        warn = lv > 0.85
        return (f"{tel.cpu_pct:.0f}", lv,
                SEG.AMBER if warn else SEG.CYAN,
                "ELEVATED" if warn else "NOMINAL",
                "warning" if warn else "nominal")
    if ch == "thermal":
        if not tel.temp_available or tel.temp_c is None:
            return ("--", 0.0, SEG.CYAN, "NO SENSOR", "off")
        lv = tel.temperature
        hot = lv > 0.80
        return (f"{tel.temp_c:.0f}", lv,
                SEG.RED if lv > 0.94 else (SEG.AMBER if hot else SEG.CYAN),
                "AT LIMIT" if lv > 0.94 else ("ELEVATED" if hot else "NOMINAL"),
                "critical" if lv > 0.94 else ("warning" if hot else "nominal"))
    if ch == "memory":
        lv = tel.memory_pressure
        warn = lv > 0.88
        return (f"{tel.mem_used_gb:.1f}/{tel.mem_total_gb:.1f}", lv,
                SEG.AMBER if warn else SEG.CYAN,
                "HIGH" if warn else "NOMINAL",
                "warning" if warn else "nominal")
    low = fps < 50.0
    return (f"{fps:.0f}", min(1.0, fps / 120.0),
            SEG.AMBER if low else SEG.PALE,
            "REDUCED" if low else "SMOOTH",
            "warning" if low else "nominal")


def _graticule(cr, r: Rect, rows: int = 4, cols: int = 6,
               alpha: float = 1.0) -> None:
    """A subtle measuring grid behind a trace. Without it a sparkline is
    decoration; with it the same line is a reading."""
    if r.w < 24.0 or r.h < 16.0:
        return
    cr.save()
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.clip()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba(RULE, 0.55 * alpha))
    for i in range(1, rows):
        yy = round(r.y + r.h * i / rows) + 0.5
        cr.move_to(r.x, yy)
        cr.line_to(r.right, yy)
    cr.stroke()
    cr.set_source_rgba(*rgba(RULE, 0.38 * alpha))
    cr.set_dash([1.5, 3.5])
    for i in range(1, cols):
        xx = round(r.x + r.w * i / cols) + 0.5
        cr.move_to(xx, r.y)
        cr.line_to(xx, r.bottom)
    cr.stroke()
    cr.restore()


def _trace(cr, r: Rect, vals: list[float], rgb, alpha: float = 0.95,
           fill: bool = True) -> None:
    """The live trace: a filled skirt under a crisp line, plus a head marker.

    The skirt is what makes a 60-second history legible at a glance - the eye
    reads area faster than it reads a hairline - and the head marker says which
    end is now.
    """
    if r.w < 8.0 or r.h < 6.0 or len(vals) < 2:
        return
    n = len(vals)
    dx = r.w / (n - 1)
    top = r.y + r.h * 0.06
    span = r.h * 0.88

    def _pt(i: int, v: float) -> tuple[float, float]:
        return (r.x + i * dx, top + (1.0 - max(0.0, min(1.0, v))) * span)

    cr.save()
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.clip()
    if fill:
        cr.new_path()
        cr.move_to(r.x, r.bottom)
        for i, v in enumerate(vals):
            cr.line_to(*_pt(i, v))
        cr.line_to(r.right, r.bottom)
        cr.close_path()
        g = cairo.LinearGradient(r.x, top, r.x, r.bottom)
        g.add_color_stop_rgba(0.0, rgb[0], rgb[1], rgb[2], 0.30 * alpha)
        g.add_color_stop_rgba(1.0, rgb[0], rgb[1], rgb[2], 0.02 * alpha)
        cr.set_source(g)
        cr.fill()
    cr.set_line_join(1)
    cr.set_line_cap(1)
    cr.set_line_width(max(1.0, r.h * 0.045))
    cr.set_source_rgba(*rgba(rgb, alpha))
    cr.new_path()
    for i, v in enumerate(vals):
        px, py = _pt(i, v)
        cr.line_to(px, py) if i else cr.move_to(px, py)
    cr.stroke()
    hx, hy = _pt(n - 1, vals[-1])
    cr.set_source_rgba(*rgba(rgb, min(1.0, alpha * 1.1)))
    cr.new_path()
    cr.arc(hx - 1.0, hy, max(1.2, r.h * 0.035), 0.0, TAU)
    cr.fill()
    cr.restore()


def _graph_zones(bay: Rect, ch: _Channel, t) -> tuple[Rect, Rect, float, float]:
    """(well interior, plot area, label size, caption ledge height).

    Pure. Both the static pass (axis, graticule, caption) and the live pass
    (the trace) derive their geometry from this one call, so a cached frame
    and the trace drawn on top of it cannot disagree about where the plot is.
    """
    r = bay_inner(bay, sx=0.55, sy=0.85)
    if not r.valid or r.w < 40.0 or r.h < 22.0:
        return (r, r, 0.0, 0.0)
    sz = max(MIN_TEXT, min(t.micro * 0.84, r.h * 0.185))
    ledge = _cap(sz) * 1.9 if r.h > 44.0 else 0.0
    gutter = 0.0
    if r.w > 96.0 and not ch.bars:
        gutter = max(_text_w(x, sz, _W_NORMAL, t.tracking)
                     for x in ch.axis) + sz * 0.7
    return (r, Rect(r.x + gutter, r.y, r.w - gutter, r.h - ledge), sz, ledge)


def _draw_graph_frame(cr, bay: Rect, ch: _Channel, t,
                      alpha: float = 1.0) -> None:
    """The measured part of a graph well: axis values, graticule, captions.

    None of it changes between frames, which is why it lives in the cached
    static layer. A sparkline without this is decoration; with it the same
    line is a reading.
    """
    r, plot, sz, ledge = _graph_zones(bay, ch, t)
    if sz <= 0.0:
        return
    if plot.x > r.x:
        for i, lab in enumerate(ch.axis):
            yy = plot.y + (plot.h - _cap(sz)) * (i / (len(ch.axis) - 1.0)) \
                + _cap(sz)
            _show(cr, lab, sz, _W_NORMAL, t.tracking, plot.x - sz * 0.7, yy,
                  INK_DIM, 0.76 * alpha, "r")
    _graticule(cr, plot, alpha=alpha)
    if ledge > 0.0:
        base = r.bottom - ledge * 0.14
        _show(cr, ch.caption, sz, _W_NORMAL, t.tracking, r.x, base,
              INK_DIM, 0.76 * alpha, "l", r.w * 0.62)
        _show(cr, ch.span, sz, _W_NORMAL, t.tracking, r.right, base,
              INK_DIM, 0.62 * alpha, "r", r.w * 0.34)


def _draw_graph_live(cr, bay: Rect, ch: _Channel, t, vals: list[float],
                     level: float, rgb, alpha: float = 1.0) -> Rect:
    """The live trace, drawn into the plot area the frame reserved."""
    r, plot, sz, _ = _graph_zones(bay, ch, t)
    if not plot.valid:
        return plot
    if ch.bars:
        _bargraph(cr, plot.inset(0.0, plot.h * 0.10), level, rgb, segments=30)
    else:
        _trace(cr, plot, vals, rgb, 0.95 * alpha)
    return plot


def _draw_numeric(cr, bay: Rect, ch: _Channel, t, text: str, level: float,
                  style, light: LightField, alpha: float = 1.0,
                  meter: bool = True) -> None:
    """Fill one module's numeric recess: the value, its unit, and its meter.

    The unit is drawn by the segment engine at a fixed fraction of the digit
    height and bottom-aligned to the digits, so "83" and "\u00b0C" belong to one
    display. Beneath them the bargraph trough - the generated meter asset, at
    very close to its authored aspect - carries the same value as a level.
    """
    r = bay_inner(bay, sx=0.5, sy=0.5)
    if not r.valid or r.w < 26.0 or r.h < 14.0:
        return
    trough_h = 0.0
    if meter and r.h > 46.0 and r.w > 70.0:
        trough_h = min(r.h * 0.26, max(10.0, r.w / 8.2))
    disp = Rect(r.x, r.y, r.w, r.h - trough_h)

    unit = ch.unit
    # Reserve the unit first, then let the digits take everything that is
    # left. Sizing them the other way round is what makes a unit look like an
    # afterthought bolted to the right of a number.
    u_unit = SEG.measure_unit(unit, 1.0, style)
    u_text = SEG.measure(text, 1.0, style)
    gap_u = 0.16
    total = u_text + (gap_u + u_unit if unit else 0.0)
    dh = min(disp.h * 0.90, disp.w / max(total, 1e-6))
    if dh > 4.0:
        tw = u_text * dh
        uw = (u_unit * dh) if unit else 0.0
        gw = (gap_u * dh) if unit else 0.0
        x0 = disp.x + max(0.0, (disp.w - tw - gw - uw) * 0.5)
        y0 = disp.y + (disp.h - dh) * 0.5
        SEG.draw(cr, text, x0, y0, dh, style)
        if unit:
            SEG.draw_unit(cr, unit, x0 + tw + gw, y0, dh, style)
        light.glow(disp, style.lit, 0.085 * alpha, spread=0.5)

    if trough_h > 8.0:
        mt = Rect(r.x, r.bottom - trough_h, r.w, trough_h)
        draw_nine(cr, C.METER_TROUGH, mt.x, mt.y, mt.w, mt.h)
        bx, by, bw, bh = C.METER_TROUGH.content(mt.x, mt.y, mt.w, mt.h)
        _bargraph(cr, Rect(bx, by, bw, bh), level, style.lit, segments=28)


def _draw_status_column(cr, bay: Rect, ch: _Channel, t, rows, style,
                        alpha: float = 1.0) -> None:
    """The compact diagnostic block: measured rows, then the state word.

    The state is given its own full-width block rather than a fourth
    key/value row. It is the one value here that is a WORD, it is the widest
    thing in the column, and it is what the eye goes to - crushing it into a
    right-aligned half-column is how it ended up ellipsised.
    """
    r = bay_inner(bay, sx=1.0, sy=0.45)
    if not r.valid or r.w < 30.0:
        return
    pairs = list(rows[:-1])
    state_k, state_v = rows[-1]
    # Below this the measured rows cannot show both a key and a value without
    # ellipsising one of them, so the recess carries the state word alone.
    if r.w < 74.0:
        vsz = max(MIN_TEXT, min(t.micro * 1.05, r.h * 0.20))
        ksz = max(MIN_TEXT, min(t.micro * 0.84, r.h * 0.16))
        _show(cr, state_k, ksz, _W_NORMAL, t.tracking, r.cx,
              r.cy - _cap(vsz) * 0.55, INK_DIM, 0.80 * alpha, "c", r.w)
        bay_line(cr, r, state_v, vsz, _W_MEDIUM, t.tracking * 0.4, style.lit,
                 0.98 * alpha, "c", r.cy + _cap(vsz) * 0.95)
        return
    n = len(pairs)
    state_h = min(r.h * 0.34, max(14.0, r.h * 0.28))
    top = Rect(r.x, r.y, r.w, r.h - state_h)
    line = top.h / max(1, n)
    sz = max(MIN_TEXT, min(t.micro * 0.86, line * 0.60))
    for j, (k, v) in enumerate(pairs):
        base = top.y + line * j + (line + _cap(sz)) * 0.5
        kw = _show(cr, k, sz, _W_NORMAL, t.tracking, top.x, base, INK_DIM,
                   0.82 * alpha, "l", top.w * 0.60)
        vx = top.x + kw + sz * 0.55
        if top.right - vx > sz:
            bay_line(cr, Rect(vx, top.y, top.right - vx, line), v, sz,
                     _W_MEDIUM, t.tracking * 0.4, INK, 0.92 * alpha, "r",
                     base, x=top.right)

    sb = Rect(r.x, r.bottom - state_h, r.w, state_h)
    ksz = max(MIN_TEXT, min(t.micro * 0.80, sb.h * 0.42))
    vsz = max(MIN_TEXT, min(t.micro * 0.98, sb.h * 0.52))
    _hairline(cr, sb.x, sb.right, sb.y + 0.5, RULE, 0.55 * alpha)
    _show(cr, state_k, ksz, _W_NORMAL, t.tracking, sb.x,
          sb.y + _cap(ksz) * 1.55, INK_DIM, 0.80 * alpha, "l", sb.w)
    bay_line(cr, Rect(sb.x, sb.y, sb.w, sb.h), state_v, vsz, _W_MEDIUM,
             t.tracking * 0.4, style.lit, 0.98 * alpha, "r",
             sb.bottom - _cap(vsz) * 0.30, x=sb.right)


#: Cached static module panels. Bounded, and keyed by every input the static
#: pass reads, so a hit can only ever be pixel-identical to a miss.
_MODULE_STATIC: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()
_MODULE_STATIC_LIMIT = 12


def _module_static(w: int, h: int, ch: _Channel, idx: int,
                   L: Layout) -> cairo.ImageSurface | None:
    """The unchanging half of a telemetry module, rendered once.

    WHAT IS STATIC, AND WHY IT IS WORTH CACHING
    -------------------------------------------
    A module's shell, its identity plaque, its title and subsystem caption,
    its graph graticule, axis values, caption and time-span are byte-identical
    from frame to frame - they change only on resize. They are also the
    expensive half: a 9-sliced panel blit plus a dozen shaped text runs.

    Rendering them into one surface per module size and blitting that leaves
    the per-frame work as exactly the things that actually move: the lamps,
    the trace, the numerals, the meter and the status values.

    SAFETY
    ------
    The key carries the drawn size, the channel and the layout state - the
    complete set of inputs the static pass reads. Nothing live can reach this
    surface, because the live pass is a different function. Dropping the whole
    cache between any two frames would change performance and nothing else.
    """
    key = (w, h, ch.key, idx, L.state.value, round(L.type.label, 2),
           round(L.type.micro, 2), round(L.type.tracking, 2))
    hit = _MODULE_STATIC.get(key)
    if hit is not None:
        _MODULE_STATIC.move_to_end(key)
        return hit
    if w < 1 or h < 1:
        return None
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    c2 = cairo.Context(surf)
    local = Rect(0.0, 0.0, float(w), float(h))
    t = L.type
    P = F.MODULE.draw(c2, local)

    pl = P.bay("plaque")
    if pl.valid and pl.h > 9.0:
        _engrave(c2, f"0{idx + 1}", pl, min(t.label * 1.1, pl.h * 0.62),
                 t.tracking * 0.4, INK_BRIGHT, "c")

    tb = bay_inner(P.bay("title"), sy=0.4)
    if tb.valid:
        tsz = min(t.label * 1.20, tb.h * 0.82)
        base = tb.cy + _cap(tsz) * 0.5
        tw = bay_line(c2, tb, ch.title, tsz, _W_MEDIUM, t.tracking,
                      INK_BRIGHT, 0.97, "l", base)
        sx = tb.x + tw + tsz * 1.1
        if tb.right - sx > tsz * 3.0:
            ssz = max(MIN_TEXT, min(t.micro * 0.86, tb.h * 0.62))
            bay_line(c2, Rect(sx, tb.y, tb.right - sx, tb.h), ch.sub, ssz,
                     _W_NORMAL, t.tracking, INK_DIM, 0.72, "r", base,
                     x=tb.right)

    _draw_graph_frame(c2, P.bay("graph"), ch, t)
    surf.flush()
    _MODULE_STATIC[key] = surf
    while len(_MODULE_STATIC) > _MODULE_STATIC_LIMIT:
        _MODULE_STATIC.popitem(last=False)
    return surf


def _draw_module(cr, r: Rect, ch: _Channel, idx: int, L: Layout,
                 tel: Telemetry, fps: float, frame_ms: float,
                 m: ConsoleModel, light: LightField) -> None:
    """One subsystem bay, drawn into the generated module shell.

    The shell is a manufactured panel: identity plaque, title bay, four
    annunciator wells, and three display recesses proportioned as GRAPH |
    NUMERIC | STATUS. Every piece of content below is placed into one of those
    recesses by name. Nothing is positioned by guesswork against the outer
    rectangle, which is what previously left the graphs reading as thumbnails
    floating on a plate.
    """
    if r.w < 60.0 or r.h < 28.0:
        return
    t = L.type
    text, level, style, state, lamp_state = _channel_values(
        ch.key, tel, fps, frame_ms)
    m.trace(ch.key).push(level)
    vals = m.trace(ch.key).values

    if sprite_size(F.MODULE.name)[0] == 0 or r.w < 210.0 or r.h < 76.0:
        _draw_module_plain(cr, r, ch, idx, L, text, level, style, state,
                           lamp_state, vals, tel, m, light)
        return

    # The shell, the plaque, the titles and the graph's measured frame do not
    # change between frames: blit them, then draw only what moves.
    iw, ih = int(round(r.w)), int(round(r.h))
    static = _module_static(iw, ih, ch, idx, L)
    if static is not None:
        cr.save()
        cr.set_source_surface(static, round(r.x), round(r.y))
        cr.paint()
        cr.restore()
        P = F.MODULE.place(Rect(round(r.x), round(r.y), float(iw), float(ih)))
    else:
        P = F.MODULE.draw(cr, r)

    # --- annunciator wells, with engraved legends beneath ------------------
    l0 = P.bay("lamp_0")
    if l0.valid and l0.h > 5.0:
        d = min(l0.h, l0.w) * 1.06
        band = P.bay("legend")
        lab_sz = max(5.6, min(t.micro * 0.80, band.h * 0.94))
        show_legend = (band.valid and band.h >= 7.0
                       and band.w > lab_sz * 11.0)
        for k in range(4):
            lb = P.bay(f"lamp_{k}")
            st = lamp_state if k == 3 else ("nominal" if k else "standby")
            draw_sprite_fit(cr, C.lamp("small", st), lb.cx, lb.cy, d)
            if st == "warning":
                light.add(lb.cx, lb.cy, d * 2.6, L_AMBER, 0.15)
            elif st == "critical":
                light.add(lb.cx, lb.cy, d * 2.8, L_AMBER, 0.18)
            elif st == "nominal":
                light.add(lb.cx, lb.cy, d * 2.0, L_CHART, 0.10)
            if show_legend:
                _engrave(cr, ch.lamps[k],
                         Rect(lb.cx - band.w * 0.16, band.y,
                              band.w * 0.32, band.h),
                         lab_sz, t.tracking * 0.35, INK_DIM, "c", 0.92)

    plot = _draw_graph_live(cr, P.bay("graph"), ch, t, vals, level, style.lit)
    light.glow(plot if plot.valid else P.bay("graph"), style.lit, 0.05,
               spread=0.45)
    _draw_numeric(cr, P.bay("numeric"), ch, t, text, level, style, light)

    rows = list(_status_rows(ch.key, tel, fps, frame_ms, m))
    rows.append(("STATE", state))
    _draw_status_column(cr, P.bay("status"), ch, t, rows, style)


def _draw_module_plain(cr, r: Rect, ch: _Channel, idx: int, L: Layout,
                       text: str, level: float, style, state: str,
                       lamp_state: str, vals, tel, m: ConsoleModel,
                       light: LightField) -> None:
    """A module too small for the shell: the same instruments, assembled.

    Here the discrete generated housings earn their keep - a graph well, a
    segment housing and a meter trough seated in a machined recess. It is a
    different arrangement of the same hardware, not a shrunken module.
    """
    t = L.type
    inner = _rail(cr, r, depth=0.9)
    if not inner.valid or inner.h < 20.0:
        return
    pad = max(3.0, inner.w * 0.012)
    head_h = min(inner.h * 0.30, _cap(t.label) * 2.3)
    tsz = min(t.label * 1.05, max(MIN_TEXT, head_h * 0.68))
    bay_line(cr, Rect(inner.x + pad, inner.y, inner.w - pad * 2, head_h),
             ch.title, tsz, _W_MEDIUM, t.tracking, INK_BRIGHT, 0.96, "l")
    lamp_h = min(head_h * 0.52, 9.0)
    if inner.w > 170.0 and lamp_h > 3.0:
        draw_sprite_fit(cr, C.lamp("small", lamp_state),
                        inner.right - lamp_h * 0.9, inner.y + head_h * 0.5,
                        lamp_h)

    body = Rect(inner.x, inner.y + head_h, inner.w,
                max(0.0, inner.h - head_h))
    if body.h < 14.0:
        return
    gap = max(2.0, body.w * 0.016)
    mt_h = min(body.h * 0.22, max(7.0, body.w / 9.0)) if body.h > 34.0 else 0.0
    row = Rect(body.x, body.y, body.w, body.h - mt_h)
    well_w = body.w * (0.44 if body.w > 200.0 else 0.40)

    well = Rect(row.x, row.y, well_w, row.h)
    draw_nine(cr, C.GRAPH_WELL, well.x, well.y, well.w, well.h)
    gx, gy, gw, gh = C.GRAPH_WELL.content(well.x, well.y, well.w, well.h)
    _draw_graph_frame(cr, Rect(gx, gy, gw, gh), ch, t)
    _draw_graph_live(cr, Rect(gx, gy, gw, gh), ch, t, vals, level, style.lit)

    hx = row.x + well_w + gap
    hr = Rect(hx, row.y, row.right - hx, row.h)
    if hr.w > 34.0:
        draw_nine(cr, C.SEGMENT_HOUSING, hr.x, hr.y, hr.w, hr.h)
        sx, sy, sw, sh = C.SEGMENT_HOUSING.content(hr.x, hr.y, hr.w, hr.h)
        _draw_numeric(cr, Rect(sx, sy, sw, sh), ch, t, text, level, style,
                      light, meter=False)

    if mt_h > 6.0:
        mt = Rect(body.x, body.bottom - mt_h, body.w, mt_h * 0.94)
        draw_nine(cr, C.METER_TROUGH, mt.x, mt.y, mt.w, mt.h)
        bx, by, bw, bh = C.METER_TROUGH.content(mt.x, mt.y, mt.w, mt.h)
        _bargraph(cr, Rect(bx, by, bw, bh), level, style.lit, segments=32)


def _draw_condensed(cr, r: Rect, L: Layout, tel: Telemetry, fps: float,
                    frame_ms: float, m: ConsoleModel,
                    light: LightField) -> None:
    """One rail carrying four readouts, for layouts with no room for modules.

    This is a different arrangement of the same hardware, not a scaled-down
    copy of the rack: at this size four separate module shells would be all
    bevel and no information.
    """
    inner = _rail(cr, r)
    ix, iy, iw, ih = inner.x, inner.y, inner.w, inner.h
    if iw < 40.0 or ih < 10.0:
        return
    n = len(_CHANNELS)
    cw = iw / n
    t = L.type
    for i, ch in enumerate(_CHANNELS):
        text, level, style, state, lamp_state = _channel_values(
            ch.key, tel, fps, frame_ms)
        m.trace(ch.key).push(level)
        cx0 = ix + i * cw
        cell = Rect(cx0, iy, cw, ih)

        # Three rows in a shallow rail: caption, readout, meter. Sized from
        # the interior so the caption clears the bevel and the meter clears
        # the bottom edge.
        lab = ch.title.split(" /")[0][:4]
        lab_sz = max(5.0, min(t.micro, ih * 0.24))
        _show(cr, lab, lab_sz, _W_NORMAL, t.tracking, cell.x + cw * 0.06,
              cell.y + _cap(lab_sz) * 1.35, INK_DIM, 0.90, "l", cw * 0.9)

        # The unit belongs to the display here too. A bare "83" on a console
        # that elsewhere reads 83 degrees C is the readout contradicting itself.
        u_unit = SEG.measure_unit(ch.unit, 1.0, style)
        u_text = SEG.measure(text, 1.0, style)
        total = u_text + 0.16 + u_unit
        dh = min(ih * 0.44, (cw * 0.88) / max(total, 1e-6))
        if dh > 5.0:
            x0 = cell.x + cw * 0.06
            y0 = cell.y + ih * 0.40
            w = SEG.draw(cr, text, x0, y0, dh, style)
            SEG.draw_unit(cr, ch.unit, x0 + w + dh * 0.16, y0, dh, style)
        bar = Rect(cell.x + cw * 0.06, cell.bottom - ih * 0.12,
                   cw * 0.86, max(2.0, ih * 0.09))
        _bargraph(cr, bar, level, style.lit, segments=14)
        if i:
            cr.set_source_rgba(*rgba(RULE, 0.45))
            cr.rectangle(cx0, iy + ih * 0.12, 1.0, ih * 0.76)
            cr.fill()


def _draw_modules(cr, L: Layout, tel: Telemetry, fps: float, frame_ms: float,
                  m: ConsoleModel, light: LightField) -> None:
    r = L.readout
    if not r.valid:
        return
    n = len(_CHANNELS)
    # A module shell needs real estate in BOTH axes to be worth drawing. Test
    # the cell a module would actually get, not just the axis it stacks along:
    # a tall narrow column and a short wide row fail for different reasons.
    if L.readout_vertical:
        cell_w, cell_h = r.w, r.h / n
    else:
        cell_w, cell_h = r.w / n, r.h
    if cell_w < 150.0 or cell_h < 54.0:
        _draw_condensed(cr, r, L, tel, fps, frame_ms, m, light)
        return
    gap = max(3.0, (r.h if L.readout_vertical else r.w) * 0.012)
    if L.readout_vertical:
        cell_h = (r.h - gap * (n - 1)) / n
        for i, ch in enumerate(_CHANNELS):
            _draw_module(cr, Rect(r.x, r.y + i * (cell_h + gap), r.w, cell_h),
                         ch, i, L, tel, fps, frame_ms, m, light)
    else:
        cell_w = (r.w - gap * (n - 1)) / n
        for i, ch in enumerate(_CHANNELS):
            _draw_module(cr, Rect(r.x + i * (cell_w + gap), r.y, cell_w, r.h),
                         ch, i, L, tel, fps, frame_ms, m, light)


# --------------------------------------------------------------------------
# controls
# --------------------------------------------------------------------------
def _key_seat(cr, r: Rect, depth: float = 1.0, lit: bool = False,
              focus: bool = False) -> None:
    """The recess a specimen key sits in: a soft socket, not a drawn button.

    Its entire job is to make the authored plate read as INSTALLED. It is
    therefore darker than the trough, slightly larger than the key on every
    side, and carries a bright lower lip - the one cue that says the metal
    continues underneath. It never draws an outline the key would have to
    align with, because a raster key with a rounded profile can never sit
    exactly inside a drawn rectangle.
    """
    if r.w < 6.0 or r.h < 6.0:
        return
    pad = max(1.0, r.h * 0.030)
    s = Rect(r.x - pad, r.y - pad, r.w + pad * 2.0, r.h + pad * 2.0)
    cr.save()
    g = cairo.LinearGradient(s.x, s.y, s.x, s.bottom)
    g.add_color_stop_rgba(0.0, 0.026, 0.029, 0.032, 0.78 * depth)
    g.add_color_stop_rgba(0.72, 0.050, 0.055, 0.060, 0.60 * depth)
    g.add_color_stop_rgba(1.0, 0.092, 0.098, 0.104, 0.52 * depth)
    cr.set_source(g)
    cr.rectangle(s.x, s.y, s.w, s.h)
    cr.fill()
    cr.set_line_width(1.0)
    cr.set_source_rgba(0.60, 0.64, 0.66, (0.30 if focus else 0.16) * depth)
    cr.move_to(s.x, s.bottom - 0.5)
    cr.line_to(s.right, s.bottom - 0.5)
    cr.stroke()
    if lit:
        # The authored active plate carries its own illumination; this is only
        # the catch it throws on the socket immediately around it.
        cr.set_source_rgba(0.42, 0.95, 0.46, 0.055)
        cr.rectangle(s.x, s.y, s.w, s.h)
        cr.fill()
    cr.restore()


def _draw_controls(cr, L: Layout, m: ConsoleModel, light: LightField,
                   static: bool = True, live: bool = True) -> None:
    """The specimen selector: five engraved creature keys, mechanically seated.

    ASSEMBLY ORDER, and why
    -----------------------
      1. the trough        one dark machined recess spanning the bezel width
      2. the backing rail  a thin gunmetal strip the keys are bolted through
      3. divider ribs      hairline grooves between key stations
      4. the sockets       a shadowed seat per key
      5. the key plates    the authored artwork, uniform scale, never tinted
      6. the identifiers   engraved channel + archive code on the ledge
      7. the light         a faint green catch from whichever key is live

    Everything before step 5 is deliberately quieter than step 5. The keys are
    the hero of this console; the mounting exists only to stop them reading as
    five PNGs on a black field.
    """
    geo, mode = control_geometry(L)
    if not geo.valid:
        return
    t = L.type
    trough = L.controls

    if not static:
        rail = trough.inset(max(2.0, trough.h * 0.055) * 0.9,
                            max(2.0, trough.h * 0.055) * 0.75)
        _draw_controls_live(cr, L, m, light, geo, mode)
        return

    # --- 1 + 2. the trough, floored in gunmetal ----------------------------
    # One channel, full bezel width, with a MACHINED METAL FLOOR rather than a
    # black field. A dark rectangle behind the keys was exactly the "PNGs on a
    # background" read this mounting exists to remove, and it left the ends of
    # the row looking like an unfinished cut-out.
    rail = trough
    if trough.valid:
        wall = max(2.0, trough.h * 0.055)
        rail = trough.inset(wall * 0.9, wall * 0.75)
        cr.save()
        g = cairo.LinearGradient(trough.x, trough.y, trough.x, trough.bottom)
        g.add_color_stop_rgba(0.0, 0.052, 0.056, 0.060, 1.0)
        g.add_color_stop_rgba(1.0, 0.088, 0.094, 0.100, 1.0)
        cr.set_source(g)
        cr.rectangle(trough.x, trough.y, trough.w, trough.h)
        cr.fill()
        # floor
        g2 = cairo.LinearGradient(rail.x, rail.y, rail.x, rail.bottom)
        g2.add_color_stop_rgba(0.0, 0.158, 0.165, 0.170, 1.0)
        g2.add_color_stop_rgba(0.55, 0.120, 0.127, 0.133, 1.0)
        g2.add_color_stop_rgba(1.0, 0.086, 0.092, 0.098, 1.0)
        cr.set_source(g2)
        cr.rectangle(rail.x, rail.y, rail.w, rail.h)
        cr.fill()
        cr.set_line_width(1.0)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.60)
        cr.move_to(trough.x, trough.y + 0.5)
        cr.line_to(trough.right, trough.y + 0.5)
        cr.stroke()
        cr.set_source_rgba(0.66, 0.70, 0.72, 0.26)
        cr.move_to(rail.x, rail.y + 0.5)
        cr.line_to(rail.right, rail.y + 0.5)
        cr.move_to(trough.x, trough.bottom - 0.5)
        cr.line_to(trough.right, trough.bottom - 0.5)
        cr.stroke()
        cr.restore()
        scr = min(trough.h * 0.15, 12.0)
        if scr > 5.0 and trough.w > 280.0:
            for sx in (trough.x + scr * 1.0, trough.right - scr * 1.0):
                draw_sprite_fit(cr, "part/screw_small", sx, trough.cy, scr)

    # --- 3. divider ribs between key stations ------------------------------
    for i in range(geo.n - 1):
        x = geo.key_rect(i).right + (geo.pitch - geo.key_w) * 0.5
        _divider(cr, x, rail.y + rail.h * 0.12, rail.bottom - rail.h * 0.12,
                 0.60)

    # --- 6. engraved channel identifiers -----------------------------------
    if geo.ledge >= 7.0:
        sz = max(MIN_TEXT, min(t.micro * 0.84, geo.ledge * 0.78))
        for i, sp in enumerate(CATALOGUE):
            lr = geo.label_rect(i)
            dim = i in m.disabled
            live = i == m.active
            txt = f"{i + 1:02d}  {sp.archive}"
            if _text_w(txt, sz, _W_NORMAL, t.tracking * 0.35) > lr.w:
                # Simplify the LABEL before shrinking the key: the engraved
                # organism is the identity, the code is the footnote.
                txt = sp.archive
                if _text_w(txt, sz, _W_NORMAL, t.tracking * 0.35) > lr.w:
                    txt = f"{i + 1:02d}"
            _engrave(cr, txt, lr, sz, t.tracking * 0.35,
                     INK_BRIGHT if live else INK_DIM,
                     "c", 0.35 if dim else (0.95 if live else 0.78))

    if live:
        _draw_controls_live(cr, L, m, light, geo, mode)


def _draw_controls_live(cr, L: Layout, m: ConsoleModel, light: LightField,
                        geo, mode: Rect) -> None:
    """Sockets, the authored key plates, the aux controls and their light.

    Split out because these are the only parts of the selector that change:
    which key is latched, which is under the pointer, which is being pressed.
    The trough, rail, ribs and engraved identifiers around them do not.
    """
    for i in range(geo.n):
        st = SEL.key_state(i, m.active, m.pressed, m.focus, m.disabled)
        _key_seat(cr, geo.key_rect(i), lit=(SEL.plate(st) == "active"),
                  focus=(m.focus == i))
    SEL.draw_keys(cr, geo, m.active, m.pressed, m.focus, m.disabled)

    # The one lit key catches the metal around it.
    if 0 <= m.active < geo.n and m.active not in m.disabled:
        lx, ly, lrad = geo.lamp_point(m.active)
        light.add(lx, ly, max(10.0, lrad * 2.6), L_CHART, 0.13)
        kr = geo.key_rect(m.active)
        light.add(kr.cx, kr.bottom + kr.h * 0.10, kr.w * 0.85, L_CHART, 0.07)

    cyc = cycle_rect(geo, mode)
    if cyc.valid and cyc.w > 10.0:
        _key_seat(cr, cyc.inset(-cyc.w * 0.02, -cyc.h * 0.06), depth=0.45)
        draw_sprite(cr, C.cycle_key(m.cycle_state), cyc.x, cyc.y,
                    cyc.w, cyc.h)
    if mode.valid and mode.w > 8.0:
        _key_seat(cr, mode.inset(-mode.w * 0.03, -mode.h * 0.03), depth=0.45)
        draw_sprite(cr, C.mode_key(m.mode_state), mode.x, mode.y,
                    mode.w, mode.h)
        if m.mode_state == "active":
            light.add(mode.cx, mode.cy, mode.h * 0.7, L_CHART, 0.12)
        elif m.mode_state == "error":
            light.add(mode.cx, mode.cy, mode.h * 0.7, L_AMBER, 0.12)
        elif m.mode_state == "armed":
            light.add(mode.cx, mode.cy, mode.h * 0.6, L_CYAN, 0.08)


# --------------------------------------------------------------------------
# footer
# --------------------------------------------------------------------------
def _reticle(cr, cx: float, cy: float, rad: float, alpha: float = 1.0) -> None:
    """The machined crosshair badge at the left end of the archive rail."""
    if rad < 5.0:
        return
    cr.save()
    g = cairo.RadialGradient(cx - rad * 0.3, cy - rad * 0.4, rad * 0.1,
                             cx, cy, rad)
    g.add_color_stop_rgba(0.0, 0.42, 0.45, 0.45, 1.0)
    g.add_color_stop_rgba(1.0, 0.20, 0.22, 0.23, 1.0)
    cr.set_source(g)
    cr.arc(cx, cy, rad, 0.0, TAU)
    cr.fill()
    cr.set_line_width(1.2)
    cr.set_source_rgba(0.08, 0.09, 0.10, 0.9)
    cr.arc(cx, cy, rad, 0.0, TAU)
    cr.stroke()
    cr.set_line_width(max(1.0, rad * 0.10))
    cr.set_source_rgba(0.10, 0.11, 0.12, 0.85)
    cr.move_to(cx - rad * 0.82, cy)
    cr.line_to(cx + rad * 0.82, cy)
    cr.move_to(cx, cy - rad * 0.82)
    cr.line_to(cx, cy + rad * 0.82)
    cr.stroke()
    cr.set_source_rgba(0.60, 0.64, 0.66, 0.35)
    cr.arc(cx, cy, rad * 0.46, 0.0, TAU)
    cr.stroke()
    cr.restore()


def _draw_footer(cr, L: Layout, m: ConsoleModel) -> None:
    """The archive rail, drawn into the generated footer fascia.

    The asset is a manufactured panel: a machined badge boss with the
    instrument reticle already cast into it, eight key/value bays, and a
    three-line block at the right end for the standing motto. Every bay below
    is addressed by name. Nothing is centred by guesswork, and the reticle is
    not redrawn in code - it is part of the metal.
    """
    r = L.footer
    if not L.show_footer or not r.valid:
        return
    t = L.type
    sp = m.species

    if sprite_size(F.FOOTER.name)[0] == 0 or r.w < 420.0 or r.h < 22.0:
        _draw_footer_plain(cr, L, r, m)
        return

    P = F.FOOTER.draw(cr, r)
    bays = (("INSTRUMENT", "AOM-1", INK_BRIGHT),
            ("ARCHIVE", sp.archive, INK_BRIGHT),
            ("CLASS", sp.cls, INK_BRIGHT),
            ("ORIGIN", sp.origin, INK_BRIGHT),
            ("SYMMETRY", sp.symmetry, INK_BRIGHT),
            ("MODE", "LIVE OBSERVATION", INK_BRIGHT),
            ("FIELD", m.behavior, INK_BRIGHT),
            ("SYSTEM BUS", "ONLINE", LIME))
    b0 = P.bay("bay_0")
    ksz = max(MIN_TEXT, min(t.micro * 0.90, b0.h * 0.34))
    vsz = max(MIN_TEXT, min(t.micro * 1.10, b0.h * 0.44))
    for i, (k, v, col) in enumerate(bays):
        bi = bay_inner(P.bay(f"bay_{i}"), sx=0.7, sy=1.35)
        if bi.valid:
            bay_stack(cr, bi, k, v, ksz, vsz, t, value_rgb=col)

    mb = bay_inner(P.bay("motto"), sx=0.8, sy=0.5)
    if mb.valid and mb.h > 14.0:
        mz = max(MIN_TEXT, min(t.micro * 0.84, mb.h * 0.29))
        step = mb.h / 3.0
        for i, word in enumerate(("OBSERVE", "UNDERSTAND", "EXTEND")):
            bay_line(cr, mb, word, mz, _W_NORMAL, t.tracking * 1.2, INK_DIM,
                     0.80, "l", mb.y + step * i + (step + _cap(mz)) * 0.5)

    # engraved division mark on the chassis metal below the rail
    if L.show_chassis and L.chassis.valid and r.w > 820.0:
        ez = max(MIN_TEXT, min(t.micro * 0.80, r.h * 0.26))
        ey = min(L.chassis.bottom - ez * 0.6, r.bottom + ez * 2.0)
        _show(cr, "OCEANOGRAPHIC RESEARCH DIVISION", ez, _W_NORMAL,
              t.tracking * 1.4, L.chassis.right - L.chassis.w * 0.030,
              ey, (0.20, 0.22, 0.23), 0.95, "r")


def _draw_footer_plain(cr, L: Layout, r: Rect, m: ConsoleModel) -> None:
    """Narrow states: a shallow machined rail carrying as many bays as fit."""
    inner = _rail(cr, r)
    if not inner.valid:
        return
    sp = m.species
    t = L.type
    bays = [("INSTRUMENT", "AOM-1"), ("ARCHIVE", sp.archive),
            ("CLASS", sp.cls), ("ORIGIN", sp.origin),
            ("SYMMETRY", sp.symmetry), ("MODE", "LIVE OBSERVATION"),
            ("FIELD", m.behavior), ("SYSTEM BUS", "ONLINE")]
    avail = inner.w
    if avail < 240.0:
        bays = bays[:3]
    elif avail < 520.0:
        bays = bays[:5]
    elif avail < 720.0:
        bays = bays[:6]
    cw = avail / len(bays)
    size = max(MIN_TEXT, min(t.micro, inner.h * 0.34))
    for i, (k, v) in enumerate(bays):
        cell = Rect(inner.x + i * cw + cw * 0.06, inner.y, cw * 0.88, inner.h)
        bay_stack(cr, cell, k, v, size * 0.88, size, t,
                  value_rgb=LIME if v == "ONLINE" else INK_BRIGHT)
        if i:
            _divider(cr, inner.x + i * cw - cw * 0.02, inner.y, inner.bottom,
                     0.7)


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# the static hardware layer
# --------------------------------------------------------------------------
# WHY THIS EXISTS
# ---------------
# Most of this console does not change between frames. The chassis, the
# bezels, the header fascia and its engraved names, the archive rail, the
# selector's mounting trough, the field's graticule and rulers - all of it is
# a pure function of the widget size and which specimen is selected. Rebuilding
# it sixty times a second was the largest cost in the frame and bought nothing.
#
# It is therefore rendered ONCE into two surfaces - one for behind the
# organism, one for in front of it - and blitted. What remains per-frame is
# exactly the set of things that actually move.
#
# SAFETY
# ------
# The cache key carries every discrete input the static passes read. A live
# value cannot leak into a cached surface because the live pass is a different
# function; `qa/console_gates.py` GATE 8 renders each frame warm and cold and
# asserts the two are byte-identical. Dropping the cache between any two
# frames would change performance and nothing else.
_UNDER_CACHE: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()
_OVER_CACHE: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()
_LAYER_LIMIT = 3


def _layer_key(L: Layout, m: ConsoleModel) -> tuple:
    """Every discrete input the static passes read. Nothing live belongs here."""
    return (int(round(L.width)), int(round(L.height)), L.state.value,
            m.species.key, m.active, m.behavior,
            L.show_chassis, L.show_footer, L.show_controls, L.show_status)


def _cached_layer(cache, key, w: int, h: int, paint) -> cairo.ImageSurface | None:
    hit = cache.get(key)
    if hit is not None:
        cache.move_to_end(key)
        return hit
    if w < 1 or h < 1:
        return None
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    paint(cairo.Context(surf))
    surf.flush()
    cache[key] = surf
    while len(cache) > _LAYER_LIMIT:
        cache.popitem(last=False)
    return surf


def _blit(cr, surf) -> None:
    cr.save()
    cr.set_source_surface(surf, 0, 0)
    cr.paint()
    cr.restore()


def clear_static_cache() -> None:
    """Drop derived surfaces. Purely a memory operation; changes no output."""
    _MODULE_STATIC.clear()
    _UNDER_CACHE.clear()
    _OVER_CACHE.clear()


def draw_under(cr, L: Layout, m: ConsoleModel, vp_scale: float = 1.0) -> None:
    """Everything BEHIND the organism: enclosure, glass, measuring furniture.

    The static half is cached; the live half is the field's readings, which
    change every frame because the specimen does.
    """
    w, h = int(round(L.width)), int(round(L.height))
    key = _layer_key(L, m)

    def paint(c2):
        draw_background(c2, L.width, L.height)
        _draw_chassis(c2, L)
        _draw_field_static(c2, L, m)

    surf = _cached_layer(_UNDER_CACHE, key, w, h, paint)
    if surf is None:
        paint(cr)
    else:
        _blit(cr, surf)
    _draw_field_live(cr, L, m)


def draw_over(cr, L: Layout, tel: Telemetry, fps: float, frame_ms: float,
              m: ConsoleModel, light: LightField) -> None:
    """Everything IN FRONT of the organism, then the light pass."""
    light.clear()
    w, h = int(round(L.width)), int(round(L.height))
    key = _layer_key(L, m)
    dummy = LightField()

    def paint(c2):
        _draw_stage(c2, L, m, dummy)
        _draw_header(c2, L, m, dummy, static=True, live=False)
        _draw_controls(c2, L, m, dummy, static=True, live=False)
        _draw_footer(c2, L, m)

    surf = _cached_layer(_OVER_CACHE, key, w, h, paint)
    if surf is None:
        paint(cr)
    else:
        _blit(cr, surf)

    # The stage's own glow is an emitter, not pixels, so it is re-added each
    # frame rather than being baked into the cached plate.
    glass = stage_content(L)
    if glass.valid:
        light.glow(glass, L_CYAN, 0.07, spread=0.40)
    _draw_header(cr, L, m, light, static=False, live=True)
    _draw_modules(cr, L, tel, fps, frame_ms, m, light)
    _draw_controls(cr, L, m, light, static=False, live=True)
    light.paint(cr)
