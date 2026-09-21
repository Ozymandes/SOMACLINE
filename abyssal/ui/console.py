"""The physical console: six generated hardware modules, live content in them.

WHAT THIS MODULE IS
-------------------
The machine is built from exactly six generated modules (skin/modules.py):
shell, header, observation chamber, telemetry rack, selector, status rail.
They supply the METAL - frames, bevels, recesses, apertures. Everything that
changes is drawn here in code, into the recess manufactured for it: nothing
reads a baked-in number, because nothing was baked in.

COMPOSITION
-----------
    layer_under   background, 01 shell, glass, graticule          (static)
    (host)        the organism, clipped to the glass              (per frame)
    layer_over    03 bezel, 02 header, 04 rack, 05 selector plate,
                  06 rail and all static type                     (static)
    regions()     header clock, rack readouts, selector keys      (on change)

Static layers and regions are cached on their complete inputs, at the display's
device scale. The live widget hands them to GSK as textures; the headless path
(draw_under / draw_over) composes the very same surfaces, so a QA frame IS a
live frame.

GEOMETRY IS SHARED
------------------
`control_geometry` and `stage_content` are pure functions. Drawing, hit testing
and the QA gates all call them, so the keys you see and the keys you can click
are the same keys by construction.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass

import cairo

from ..core.layout import Layout, LayoutState, Rect
from ..core.lighting import AMBER as L_AMBER
from ..core.lighting import CHARTREUSE as L_CHART
from ..core.lighting import CYAN as L_CYAN
from ..core.lighting import LightField
from ..core.signals import Telemetry
from ..core.theme import INK, INK_BRIGHT, INK_DIM, INK_TECH, LIME, RULE, rgba
from ..organism.species import CATALOGUE, Species
from ..skin import catalog as C
from ..skin import hidpi
from ..skin import modules as MOD
from ..skin.surface import draw_nine, draw_sprite, draw_sprite_fit
from ..telemetry.history import History
from . import segment as SEG
from . import selector as SEL
from .chrome import (_cap, _show as _show_raw, _text_w, _W_MEDIUM,
                     _W_NORMAL, draw_background)

TAU = math.tau

#: Scope graticule ink, measured off the reference field: a cool blue-grey
#: that reads clearly against the glass without competing with the specimen.
SCOPE = (0.36, 0.52, 0.62)

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
           round(rgb[2], 3), round(alpha, 3), hidpi.scale())
    ent = _TXT.get(key)
    if ent is None:
        w = _text_w(text, size, weight, tracking)
        if max_w > 0.0:
            w = min(w, max_w)
        pad = max(3.0, size * 0.8)
        base_in = size * 1.7
        sw = max(1, int(w + pad * 2.0))
        sh = max(1, int(base_in + size * 1.1 + pad))
        surf = hidpi.surface(sw, sh)
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
    switches: int = 0
    #: 60 s of telemetry at the telemetry cadence, owned by the host. The
    #: graphs read it; nothing in the draw path writes to it.
    history: History | None = None

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
    geo = SEL.from_module(MOD.SELECTOR.place(L.controls), n=len(CATALOGUE))
    return (geo, geo.aux_r)


def cycle_rect(geo, mode: Rect) -> Rect:
    """The two-way cycle rocker, seated in the trough's left end."""
    return geo.aux_l if geo.valid else Rect(0.0, 0.0, 0.0, 0.0)


def stage_content(L: Layout) -> Rect:
    """The glass inside the stage frame: where the organism may draw.

    The viewport is built from THIS, not from the raw stage, so the specimen
    sits behind the frame's inner lip instead of under its metal.
    """
    s = L.stage
    if not s.valid:
        return s
    return MOD.OBSERVATION.place(s).bay("aperture")


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
    key = (int(round(rad)), round(alpha, 2), hidpi.scale())
    surf = _RING_CACHE.get(key)
    side = int(rad * 2.0) + 4
    if surf is None:
        surf = hidpi.surface(side, side)
        c2 = cairo.Context(surf)
        c2.set_line_width(1.0)
        mid = side * 0.5
        for k in (0.22, 0.42, 0.62, 0.81, 1.0):
            c2.save()
            c2.set_source_rgba(*rgba(SCOPE, (0.55 if k == 1.0 else 0.36) * alpha))
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


def _polar_grid(cr, sc: "Scope", r: Rect, alpha: float = 1.0, t=None,
                x_min: float = -1e9) -> None:
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
    cr.set_source_rgba(*rgba(SCOPE, 0.62 * alpha))
    # The X axis starts after the radius ruler's gutter: it must never run
    # through the ruler's own labels.
    cr.move_to(max(r.x, sc.axis_x0, x_min), round(cy) + 0.5)
    cr.line_to(min(r.right, sc.axis_x1), round(cy) + 0.5)
    cr.move_to(round(cx) + 0.5, max(r.y, sc.axis_y0))
    cr.line_to(round(cx) + 0.5, min(r.bottom, sc.axis_y1))
    cr.stroke()

    # regular ticks along both axes, pitched off the design radius
    cr.set_source_rgba(*rgba(SCOPE, 0.80 * alpha))
    step = rad * 0.2
    n = int(max(sc.axis_x1 - cx, sc.axis_y1 - cy) / step)
    for i in range(1, n + 1):
        d = i * step
        tk = 3.0 if i % 5 else 5.5
        for sx in (-1, 1):
            px = cx + sx * d
            if (r.x < px < r.right and sc.axis_x0 <= px <= sc.axis_x1
                    and px > x_min):
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
        _show(cr, lab, sz, _W_NORMAL, t.tracking, ax, base, INK,
              0.92 * alpha, al)


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


#: Shell frame scale relative to the chassis: the reference's perimeter is
#: ~15-25 px on a 1368 px enclosure, and the shell sprite's frame is 97 src px
#: wide, so its fixed parts are drawn at 0.17 of the chassis scale.
_SHELL_K = 0.17


def shell_k(L: Layout) -> float:
    c = L.chassis
    return _SHELL_K * min(c.w / 1368.0, c.h / 1028.0) * (1600.0 / 1368.0) \
        if L.state is not LayoutState.COMPACT else \
        max(0.10, min(c.w, c.h) * 0.030 / 97.0)


def _draw_chassis(cr, L: Layout) -> None:
    """MODULE 01: the master shell. The bottom layer of the machine.

    It carries no screws of its own - every fastener on this machine belongs
    to the child module it holds down - and nothing passes through it.
    """
    c = L.chassis
    if not L.show_chassis or not c.valid:
        return
    if MOD.SHELL.available:
        MOD.SHELL.draw(cr, c, k=shell_k(L))
    else:
        draw_nine(cr, C.PLATE, c.x, c.y, c.w, c.h)


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


def fit_first(cands, size: float, weight: int, tracking: float,
              max_w: float) -> str:
    """The longest of `cands` (longest first) that fits `max_w` whole.

    Labels on this machine are shown complete or in a deliberately shorter
    form - never ellipsised mid-word. Empty string if none fits.
    """
    for c in cands:
        if c and _text_w(c, size, weight, tracking) <= max_w:
            return c
    return ""


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
             value_rgb=INK_BRIGHT, key_rgb=INK_TECH, alpha: float = 1.0,
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
               0.98 * alpha, "l", r.w * 0.62)
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

    if (not MOD.HEADER.available or L.state is LayoutState.COMPACT
            or r.w < 420.0 or r.h < 34.0):
        _draw_header_compact(cr, L, r, m, light, static, live)
        return

    P = MOD.HEADER.place(r) if not static else MOD.HEADER.draw(cr, r)

    # --- bay 1: instrument name over specimen name ------------------------
    b = bay_inner(P.bay("title")) if static else Rect(0, 0, 0, 0)
    if b.valid:
        two = b.h >= 26.0
        name_sz = min(t.specimen, b.h * (0.46 if two else 0.86))
        title_sz = min(t.title, b.h * 0.30)
        if two:
            stack = _cap(title_sz) * 1.20 + _cap(name_sz) * 1.36
            top = b.y + max(0.0, (b.h - stack) * 0.5)
            bay_line(cr, b, "ABYSSAL ORGANISM MONITOR", title_sz, _W_NORMAL,
                     t.tracking, INK_TECH, 0.96, "l", top + _cap(title_sz))
            base2 = top + _cap(title_sz) * 1.20 + _cap(name_sz) * 1.30
            lead = _show(cr, "SPECIMEN", title_sz, _W_NORMAL, t.tracking,
                         b.x, base2, INK_TECH, 0.80, "l")
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
            ew = bay_line(cr, b, sp.epithet, sub_sz, _W_NORMAL,
                          t.tracking * 1.5, INK_BRIGHT, 0.94, "c",
                          top + _cap(sub_sz))
            # Rule marks either side of the name, as the reference sets it.
            # Sized FROM the drawn text, so they can never cross it.
            ry = top + _cap(sub_sz) * 0.5
            rl = min(sub_sz * 1.6, (b.w - ew) * 0.5 - sub_sz * 0.8)
            if rl > sub_sz * 0.6:
                for sgn in (-1.0, 1.0):
                    x0 = b.cx + sgn * (ew * 0.5 + sub_sz * 0.55)
                    _hairline(cr, min(x0, x0 + sgn * rl), max(x0, x0 + sgn * rl),
                              ry, INK_BRIGHT, 0.70)
            cap_txt = fit_first(("BIOCOMPUTATIONAL OBSERVATION TERMINAL",
                                 "OBSERVATION TERMINAL"), micro_sz, _W_NORMAL,
                                t.tracking, b.w)
            if cap_txt:
                _show(cr, cap_txt, micro_sz, _W_NORMAL, t.tracking, b.cx,
                      top + _cap(sub_sz) * 1.28 + _cap(micro_sz) * 1.40,
                      INK_TECH, 0.82, "c")

    # --- bay 3: the LIVE annunciator, in the boss the asset provides ------
    lamp = P.bay("live_lamp") if static else Rect(0, 0, 0, 0)
    win = P.bay("live_window") if static else Rect(0, 0, 0, 0)
    if lamp.valid:
        # The boss is cast into the fascia, so the lamp is sized to the boss
        # and centred in it - never to the bay, which is wider than the boss
        # and was seating the lamp a few pixels left of its own housing.
        d = min(lamp.h, lamp.w) * 0.94
        draw_sprite_fit(cr, C.lamp("small", "nominal"), lamp.cx, lamp.cy, d)
        light.add(lamp.cx, lamp.cy, d * 2.2, L_CHART, 0.18)
    if win.valid:
        wi = bay_inner(win, sx=0.8, sy=0.5)
        bay_line(cr, wi, "LIVE", min(t.label, wi.h * 0.86), _W_MEDIUM,
                 t.tracking, LIME, 0.97, "c")

    # --- bay 4: date over the running clock -------------------------------
    # The clock is the one genuinely per-frame readout in this fascia, so it
    # is the one thing here that is NOT cached.
    b = bay_inner(P.bay("clock")) if live else Rect(0, 0, 0, 0)
    if b.valid:
        # The clock alone, as the reference fascia has it: large, centred in
        # its bay. The date lives in the tooltip-free archive record instead.
        clk = time.strftime("%H:%M:%S")
        dh = min(b.h * 0.86, b.w / max(SEG.measure(clk, 1.0, SEG.CYAN), 1e-6))
        cw = SEG.measure(clk, dh, SEG.CYAN)
        SEG.draw(cr, clk, b.cx - cw * 0.5, b.cy - dh * 0.5, dh, SEG.CYAN)
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
        sz = max(MIN_TEXT, min(t.micro * 1.14, b.h * 0.86))
        base = b.cy + _cap(sz) * 0.5

        def need(pr) -> float:
            k, v, _ = pr
            return (_text_w(k, sz, _W_NORMAL, t.tracking) + sz * 0.85
                    + _text_w(v, sz, _W_MEDIUM, t.tracking * 0.5) + sz * 1.2)

        # Whole pairs only: the second pair is dropped before anything is
        # ellipsised. Pairs sit on even stations when they fit them, and
        # otherwise pack from the left at their measured widths.
        shown = list(pairs)
        while shown and sum(need(pr) for pr in shown) > b.w:
            shown.pop()
        if not shown:
            continue
        station = b.w / len(shown)
        x = b.x
        for j, pr in enumerate(shown):
            k, v, col = pr
            x = max(x, b.x + j * station)
            cell = Rect(x, b.y, b.right - x, b.h)
            bay_pair(cr, cell, k, v, sz, t, value_rgb=col, baseline=base)
            x += need(pr)


def _draw_header_compact(cr, L: Layout, r: Rect, m: ConsoleModel,
                         light: LightField, static: bool = True,
                         live: bool = True) -> None:
    """Narrow states: one machined strip, two bays, same typography rules.

    Below the fascia's usable width its four bays would each be a few pixels
    wide, so the panel is replaced by a shallow recess carrying the two lines
    that still matter. This is a different arrangement, not a scaled one.
    """
    target = cr if static else _NULL_CR
    inner = _rail(target, r, depth=0.9)
    if not inner.valid:
        return
    t = L.type
    sp = m.species
    pad = max(5.0, inner.h * 0.10)
    left = Rect(inner.x + pad, inner.y, inner.w * 0.62 - pad, inner.h)
    if not static:
        cr_live, cr = cr, _NULL_CR
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
    if not static:
        cr = cr_live
    bay = _rail(cr if static else _NULL_CR, Rect(rx, inner.y + inner.h * 0.16,
                         inner.right - rx - pad, inner.h * 0.68), depth=0.8)
    if not bay.valid or bay.w < 54.0:
        return
    d = min(bay.h * 1.0, 13.0)
    lx = bay.x + d * 1.25
    if static:
        draw_sprite_fit(cr, C.lamp("small", "nominal"), bay.x + d * 0.6,
                        bay.cy, d)
    else:
        light.add(bay.x + d * 0.6, bay.cy, d * 2.2, L_CHART, 0.16)
    lw = bay_line(cr if static else _NULL_CR,
                  Rect(lx, bay.y, bay.right - lx, bay.h), "LIVE",
                  min(t.label, bay.h * 0.68), _W_MEDIUM, t.tracking, LIME,
                  0.96, "l")
    cx0 = lx + lw + d * 0.5
    if live and not static and bay.right - cx0 > 54.0:
        dh = min(bay.h * 0.80, 16.0)
        SEG.draw_right(cr, time.strftime("%H:%M:%S"), bay.right - 2.0,
                       bay.cy - dh * 0.5, dh, SEG.CYAN)


# --------------------------------------------------------------------------
# observation bezel
# --------------------------------------------------------------------------
def _draw_stage(cr, L: Layout, m: ConsoleModel, light: LightField) -> None:
    """The bezel overlay. Called AFTER the organism so the metal occludes it."""
    s = L.stage
    if not s.valid:
        return
    MOD.OBSERVATION.draw(cr, s)
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
    cr.set_source_rgba(*rgba(INK, alpha))
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
    cr.set_source_rgba(*rgba(SCOPE, 0.85 * alpha))
    cr.move_to(round(x_tick) + 0.5, sc.cy - span)
    cr.line_to(round(x_tick) + 0.5, sc.cy + span)
    cr.stroke()
    cr.restore()
    for i, v in enumerate(steps):
        yy = sc.cy - span + (2.0 * span) * (i / (len(steps) - 1.0))
        cr.save()
        cr.set_line_width(1.0)
        cr.set_source_rgba(*rgba(SCOPE, 0.95 * alpha))
        cr.move_to(x_tick - sz * (0.45 if i % 5 else 0.8), round(yy) + 0.5)
        cr.line_to(x_tick, round(yy) + 0.5)
        cr.stroke()
        cr.restore()
        _show(cr, f"{v:.2f}", sz, _W_NORMAL, t.tracking, x_tick - sz * 0.95,
              yy + _cap(sz) * 0.5, INK, 0.92 * alpha, "r")
    # The caption heads its own scale, and the gutter it reports back is wide
    # enough for the caption as well as the tick labels - so the SPECIMEN
    # FIELD zone begins clear of it rather than on top of it.
    # "R (mm)", not "RADIUS (mm)": the caption sets the width of the gutter
    # the SPECIMEN FIELD and VECTOR FIELD zones must start after, and every
    # pixel of that gutter is a pixel those zones cannot use without entering
    # the specimen's disc. R is the standard notation on a radial scale.
    cap = "R (mm)"
    _show(cr, cap, sz, _W_NORMAL, t.tracking, r.x + indent,
          sc.cy - span - _cap(sz) * 1.6, INK_TECH, 0.95 * alpha, "l")
    cap_w = _text_w(cap, sz, _W_NORMAL, t.tracking)
    return max(x_tick - r.x + sz * 1.2, indent + cap_w + sz * 0.8)


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
    _polar_grid(cr, sc, glass, t=t,
                x_min=(safe.x + gutter) if gutter > 0.0 else -1e9)
    _cardinals(cr, sc, safe, t, gutter)


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
    short: tuple[str, ...] = ()        # shorter forms of `sub`, longest first

    @property
    def subs(self) -> tuple[str, ...]:
        return (self.sub,) + self.short


_CHANNELS = (
    _Channel("cpu", "PROCESSOR", "BIOCOMPUTATIONAL CORE", "%",
             "CPU LOAD", ("100", "50", "0"),
             ("CLK", "ALG", "NET", "I/O"),
             short=("BIOCOMP. CORE", "BIOCOMPUT.", "CORE")),
    _Channel("thermal", "THERMAL", "ENVIRONMENT & METABOLIC", "\u00b0C",
             "CORE TEMP", ("120", "80", "40"),
             ("SEN", "REG", "FAN", "SYS"),
             short=("ENVIRONMENT", "ENVIRON.", "ENV.")),
    _Channel("memory", "MEMORY", "FIELD DATA & STATE", "Gb",
             "RESIDENT SET", ("13.5", "6.8", "0"),
             ("MEM", "BUF", "CACHE", "I/O"), bars=True,
             short=("FIELD DATA", "DATA")),
    _Channel("frame", "FRAME / RENDER", "VISUALISATION PIPELINE", "FPS",
             "FRAME TIME", ("33", "16", "0"),
             ("REN", "IMG", "DSP", "SYNC"),
             short=("PIPELINE", "PIPE.")),
)


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


# --------------------------------------------------------------------------
# MODULE 04: the telemetry rack
# --------------------------------------------------------------------------
# The rack is ONE generated body with four manufactured rows. Each row has an
# ID plate, a title bay, four lamp sockets, a graph well, a numeric well, a
# meter trough and a state well - and every piece of data below is drawn into
# one of those recesses by name.
#
# CADENCE
#   static  (cached per size)   rack metal, plate IDs, titles, graph grids,
#                               scale values and captions
#   5 Hz    (cached per sample) each graph's 60-second trace
#   frame                       numerics, meters, state words, lamps
@dataclass(frozen=True, slots=True)
class _Scale:
    lo: float
    hi: float
    ticks: tuple[str, str, str]      # top, middle, bottom
    caption: str
    ref: float | None = None         # a reference line (60 FPS budget)


def _graph_scale(ch: str, tel: Telemetry) -> _Scale:
    """Fixed, meaningful scales. The graph shows history; it never autoscales."""
    if ch == "cpu":
        return _Scale(0.0, 100.0, ("100", "50", "0"), "CPU LOAD  %")
    if ch == "thermal":
        return _Scale(20.0, 100.0, ("100", "60", "20"), "CORE TEMP  °C")
    if ch == "memory":
        tot = max(1.0, tel.mem_total_gb)
        return _Scale(0.0, tot, (f"{tot:.0f}", f"{tot * 0.5:.0f}", "0"),
                      "RESIDENT  GB")
    return _Scale(0.0, 50.0, ("50", "25", "0"), "FRAME TIME  ms", ref=1000.0 / 60.0)


def _rack_zones(well: Rect, t) -> tuple[Rect, Rect, float]:
    """(plot, caption band, type size) inside one graph well. Pure."""
    g = well.inset(max(2.0, well.w * 0.035), max(2.0, well.h * 0.05))
    sz = max(MIN_TEXT, min(t.micro * 0.84, g.h * 0.16))
    cap_h = _cap(sz) * 2.0 if g.h > 40.0 else 0.0
    plot = Rect(g.x, g.y, g.w, g.h - cap_h)
    return plot, Rect(g.x, plot.bottom, g.w, cap_h), sz


def _rack_graph_static(cr, well: Rect, sc: _Scale, t) -> None:
    """Grid, scale values, 60 s divisions and caption: a real instrument."""
    plot, cap, sz = _rack_zones(well, t)
    if plot.w < 20.0 or plot.h < 12.0:
        return
    cr.save()
    cr.set_line_width(1.0)
    # four horizontal divisions
    cr.set_source_rgba(0.30, 0.52, 0.62, 0.30)
    for i in range(5):
        yy = round(plot.y + plot.h * i / 4.0) + 0.5
        cr.move_to(plot.x, yy)
        cr.line_to(plot.right, yy)
    cr.stroke()
    # six 10-second time divisions, dashed and quieter
    cr.set_source_rgba(0.30, 0.52, 0.62, 0.20)
    cr.set_dash([1.5, 3.0])
    for i in range(1, 6):
        xx = round(plot.x + plot.w * i / 6.0) + 0.5
        cr.move_to(xx, plot.y)
        cr.line_to(xx, plot.bottom)
    cr.stroke()
    cr.set_dash([])
    if sc.ref is not None and sc.lo < sc.ref < sc.hi:
        yy = round(plot.bottom - plot.h * (sc.ref - sc.lo) / (sc.hi - sc.lo)) + 0.5
        cr.set_source_rgba(0.72, 0.88, 0.29, 0.30)
        cr.set_dash([4.0, 2.5])
        cr.move_to(plot.x, yy)
        cr.line_to(plot.right, yy)
        cr.stroke()
        cr.set_dash([])
    cr.restore()
    # scale values, inside the plot at its left (oldest) edge
    vs = max(MIN_TEXT, sz * 0.92)
    if plot.h > vs * 3.2 and plot.w > 60.0:
        tx = plot.x + 2.0
        _show(cr, sc.ticks[0], vs, _W_NORMAL, t.tracking * 0.4, tx,
              plot.y + _cap(vs) + 2.0, INK_TECH, 0.62, "l")
        if plot.h > vs * 6.0:
            _show(cr, sc.ticks[1], vs, _W_NORMAL, t.tracking * 0.4, tx,
                  plot.cy + _cap(vs) * 0.5, INK_TECH, 0.48, "l")
        _show(cr, sc.ticks[2], vs, _W_NORMAL, t.tracking * 0.4, tx,
              plot.bottom - 2.0, INK_TECH, 0.62, "l")
    if cap.h > 0.0:
        base = cap.cy + _cap(sz) * 0.5
        span_w = _show(cr, "60 s", sz, _W_NORMAL, t.tracking * 0.5, cap.right,
                       base, INK_TECH, 0.62, "r")
        bay_line(cr, Rect(cap.x, cap.y, cap.w - span_w - sz, cap.h),
                 sc.caption, sz, _W_NORMAL, t.tracking * 0.6, INK_TECH, 0.86,
                 "l", base)


_TRACE_CACHE: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()
_TRACE_LIMIT = 16


def _rack_trace(cr, well: Rect, ring, sc: _Scale, rgb, t) -> None:
    """The 60-second history, re-rendered only when a sample arrives."""
    plot, _, _ = _rack_zones(well, t)
    if plot.w < 20.0 or plot.h < 12.0 or ring is None:
        return
    pw, ph = int(round(plot.w)), int(round(plot.h))
    key = (id(ring), pw, ph, ring.version, sc.lo, sc.hi, rgb, hidpi.scale())
    surf = _TRACE_CACHE.get(key)
    if surf is None:
        surf = hidpi.surface(pw, ph)
        c2 = cairo.Context(surf)
        cols = max(2, min(pw, ring.cap))
        v = ring.window(cols)
        span = max(1e-6, sc.hi - sc.lo)
        n = len(v)
        pts = []
        run: list[tuple[float, float]] = []
        for i in range(n):
            val = v[i]
            if val != val:          # NaN: no data yet for this column
                if run:
                    pts.append(run)
                    run = []
                continue
            f = (float(val) - sc.lo) / span
            f = 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)
            run.append((pw * i / (n - 1), 1.5 + (ph - 3.0) * (1.0 - f)))
        if run:
            pts.append(run)
        for seg in pts:
            if len(seg) < 2:
                continue
            c2.move_to(seg[0][0], ph)
            for x, y in seg:
                c2.line_to(x, y)
            c2.line_to(seg[-1][0], ph)
            c2.close_path()
            g = cairo.LinearGradient(0, 0, 0, ph)
            g.add_color_stop_rgba(0.0, rgb[0], rgb[1], rgb[2], 0.26)
            g.add_color_stop_rgba(1.0, rgb[0], rgb[1], rgb[2], 0.03)
            c2.set_source(g)
            c2.fill()
            c2.set_line_join(cairo.LINE_JOIN_ROUND)
            c2.set_line_width(max(1.0, min(1.8, ph * 0.03)))
            c2.set_source_rgba(rgb[0], rgb[1], rgb[2], 0.95)
            c2.move_to(*seg[0])
            for x, y in seg[1:]:
                c2.line_to(x, y)
            c2.stroke()
        if pts and pts[-1]:
            hx, hy = pts[-1][-1]
            c2.set_source_rgba(rgb[0], rgb[1], rgb[2], 1.0)
            c2.arc(min(hx, pw - 2.0), hy, max(1.3, ph * 0.035), 0.0, TAU)
            c2.fill()
        surf.flush()
        _TRACE_CACHE[key] = surf
        while len(_TRACE_CACHE) > _TRACE_LIMIT:
            _TRACE_CACHE.popitem(last=False)
    else:
        _TRACE_CACHE.move_to_end(key)
    cr.save()
    cr.set_source_surface(surf, round(plot.x), round(plot.y))
    cr.paint()
    cr.restore()


#: Deliberate short forms of the state words, for narrow state wells.
_STATE_SHORT = {"NOMINAL": ("NOM.",), "ELEVATED": ("ELEV.",),
                "REDUCED": ("RED.",), "SMOOTH": ("OK",), "HIGH": ("HI",),
                "AT LIMIT": ("LIMIT", "LIM."), "NO SENSOR": ("N/A",)}


def _rack_state(cr, well: Rect, word: str, rgb, t) -> None:
    """STATE caption over the state word, both centred in the narrow well."""
    r = well.inset(max(1.5, well.w * 0.06), max(2.0, well.h * 0.10))
    if not r.valid or r.w < 16.0:
        return
    ksz = max(MIN_TEXT, min(t.micro * 0.84, r.h * 0.16))
    vsz = max(MIN_TEXT, min(t.label * 1.10, r.h * 0.26, r.w * 0.22))
    gap = _cap(ksz) * 1.1
    stack = _cap(ksz) + gap + _cap(vsz)
    top = r.cy - stack * 0.5
    # The full word, shrunk by at most a quarter; then the short form.
    full = word
    word = ""
    for sz in (vsz, vsz * 0.9, vsz * 0.8, vsz * 0.75):
        sz = max(MIN_TEXT, sz)
        if _text_w(full, sz, _W_MEDIUM, t.tracking * 0.3) <= r.w:
            word, vsz = full, sz
            break
    if not word:
        word = fit_first(_STATE_SHORT.get(full, ()), vsz, _W_MEDIUM,
                         t.tracking * 0.3, r.w)
    if not word:
        return
    if _text_w("STATE", ksz, _W_NORMAL, t.tracking * 0.8) <= r.w:
        _show(cr, "STATE", ksz, _W_NORMAL, t.tracking * 0.8, r.cx,
              top + _cap(ksz), INK_TECH, 0.72, "c")
    _show(cr, word, vsz, _W_MEDIUM, t.tracking * 0.3, r.cx, top + stack, rgb,
          0.98, "c")


def _rack_placed(L: Layout):
    return MOD.RACK.place(L.readout)


def _draw_rack_static(cr, L: Layout, tel: Telemetry) -> None:
    """Rack metal plus everything in it that only changes on resize."""
    r = L.readout
    if not r.valid:
        return
    t = L.type
    P = MOD.RACK.draw(cr, r)
    for i, ch in enumerate(_CHANNELS):
        pl = P.bay(f"r{i}_plate")
        if pl.h > 7.0:
            sz = max(MIN_TEXT, min(t.label * 1.05, pl.h * 0.62))
            # engraved into the bright plate: dark cut, light lower lip
            base = pl.cy + _cap(sz) * 0.5
            _show(cr, f"0{i + 1}", sz, _W_MEDIUM, t.tracking * 0.6, pl.cx,
                  base + 1.0, (0.86, 0.88, 0.88), 0.50, "c")
            _show(cr, f"0{i + 1}", sz, _W_MEDIUM, t.tracking * 0.6, pl.cx,
                  base, (0.03, 0.04, 0.05), 1.0, "c")
        tb = P.bay(f"r{i}_title")
        tb = tb.inset(max(3.0, tb.h * 0.30), 0.0)
        if tb.valid:
            tsz = max(MIN_TEXT, min(t.label * 1.15, tb.h * 0.70))
            base = tb.cy + _cap(tsz) * 0.5
            tw = bay_line(cr, tb, ch.title, tsz, _W_MEDIUM, t.tracking,
                          INK_BRIGHT, 0.97, "l", base)
            sx = tb.x + tw + tsz * 1.2
            ssz = max(MIN_TEXT, min(t.micro * 0.90, tb.h * 0.50))
            sub = fit_first(ch.subs, ssz, _W_NORMAL, t.tracking,
                            tb.right - sx)
            if sub:
                _show(cr, sub, ssz, _W_NORMAL, t.tracking, tb.right, base,
                      INK_TECH, 0.80, "r")
        _rack_graph_static(cr, P.bay(f"r{i}_graph"),
                           _graph_scale(ch.key, tel), t)


def _draw_rack_live(cr, L: Layout, tel: Telemetry, fps: float,
                    frame_ms: float, m: ConsoleModel, light: LightField) -> None:
    r = L.readout
    if not r.valid:
        return
    t = L.type
    P = _rack_placed(L)
    hist = m.history
    for i, ch in enumerate(_CHANNELS):
        text, level, style, state, lamp_state = _channel_values(
            ch.key, tel, fps, frame_ms)
        # lamps
        d = P.bay(f"r{i}_lamp0").h * 1.05
        for k in range(4):
            lb = P.bay(f"r{i}_lamp{k}")
            st = lamp_state if k == 3 else ("nominal" if k else "standby")
            draw_sprite_fit(cr, C.lamp("small", st), lb.cx, lb.cy, d)
            # Only a lamp that is SAYING something throws light: a warning
            # spill is information, sixteen green halos are noise (and were a
            # third of a rack rebuild).
            if st in ("warning", "critical"):
                light.add(lb.cx, lb.cy, d * 2.4, L_AMBER, 0.14)
        # graph: the 60 s history
        well = P.bay(f"r{i}_graph")
        _rack_trace(cr, well, hist[ch.key] if hist else None,
                    _graph_scale(ch.key, tel), style.lit, t)
        # numeric: the value NOW, and its level in the meter trough
        _draw_numeric(cr, P.bay(f"r{i}_numeric"), ch, t, text, level, style,
                      light, meter=False)
        mt = P.bay(f"r{i}_meter")
        mi = mt.inset(max(2.0, mt.h * 0.22), max(1.5, mt.h * 0.28))
        _bargraph(cr, mi, level, style.lit, segments=26)
        _rack_state(cr, P.bay(f"r{i}_state"), state, style.lit, t)


# --------------------------------------------------------------------------
# controls
# --------------------------------------------------------------------------
def _draw_controls(cr, L: Layout, m: ConsoleModel, light: LightField,
                   static: bool = True, live: bool = True) -> None:
    """MODULE 05: the selector mounting plate, and the approved keys in it.

    STATIC   the generated plate (five trued transparent wells, two control
             openings, label ledges) and the engraved channel identifiers.
    LIVE     the approved key plates, rocker and mode key, and their light.

    The plate's wells are genuinely open: whatever sits beneath a key is the
    shell's recessed bed, never a black rectangle painted to hide a gap.
    """
    geo, mode = control_geometry(L)
    if not geo.valid:
        return
    if static:
        MOD.SELECTOR.draw(cr, L.controls)
        t = L.type
        if geo.ledge >= 5.0:
            sz = max(MIN_TEXT, min(t.micro * 0.84, geo.ledge * 0.66))
            for i, sp in enumerate(CATALOGUE):
                lr = geo.label_rect(i)
                txt = f"{i + 1:02d}  {sp.archive}"
                if _text_w(txt, sz, _W_NORMAL, t.tracking * 0.35) > lr.w:
                    txt = sp.archive
                    if _text_w(txt, sz, _W_NORMAL, t.tracking * 0.35) > lr.w:
                        txt = f"{i + 1:02d}"
                live_i = i == m.active
                # baseline on the trough floor, clear of the key above
                _show(cr, txt, sz, _W_NORMAL, t.tracking * 0.35, lr.cx,
                      lr.bottom - lr.h * 0.16,
                      INK_BRIGHT if live_i else INK_TECH, 1.0 if live_i else 0.86,
                      "c", lr.w)
    if live:
        _draw_controls_live(cr, L, m, light, geo, mode)


def _draw_controls_live(cr, L: Layout, m: ConsoleModel, light: LightField,
                        geo, mode: Rect) -> None:
    """The approved key plates, the aux controls and their light.

    NO BACKING RECTANGLE. An earlier build filled each key's socket and
    washed the lit one green, which read as a green square matte around the
    active key. The authored active plate carries its own illumination; the
    only addition here is a small bloom from its lamp strip, confined to the
    key's own silhouette by the light pass radius.
    """
    SEL.draw_keys(cr, geo, m.active, m.pressed, m.focus, m.disabled)
    if 0 <= m.active < geo.n and m.active not in m.disabled:
        lx, ly, lrad = geo.lamp_point(m.active)
        light.add(lx, ly, max(6.0, lrad * 1.2), L_CHART, 0.10)

    cyc = cycle_rect(geo, mode)
    if cyc.valid and cyc.w > 10.0:
        draw_sprite(cr, C.cycle_key(m.cycle_state), cyc.x, cyc.y, cyc.w, cyc.h)
    if mode.valid and mode.w > 8.0:
        draw_sprite(cr, C.mode_key(m.mode_state), mode.x, mode.y,
                    mode.w, mode.h)
        if m.mode_state == "active":
            light.add(mode.cx, mode.cy, mode.h * 0.5, L_CHART, 0.10)
        elif m.mode_state == "error":
            light.add(mode.cx, mode.cy, mode.h * 0.5, L_AMBER, 0.10)
        elif m.mode_state == "armed":
            light.add(mode.cx, mode.cy, mode.h * 0.45, L_CYAN, 0.06)


# --------------------------------------------------------------------------
# footer
# --------------------------------------------------------------------------


def _draw_footer(cr, L: Layout, m: ConsoleModel) -> None:
    """MODULE 06: the bottom status rail, with the archive record in its bays.

    Seven information bays carry the specimen's archive record; the terminal
    bay carries the system state. The badge boss at the left is cast metal
    (part of the generated rail), so nothing is drawn there.
    """
    r = L.footer
    if not L.show_footer or not r.valid:
        return
    t = L.type
    sp = m.species
    if not MOD.FOOTER.available or r.w < 420.0 or r.h < 18.0:
        _draw_footer_plain(cr, L, r, m)
        return
    P = MOD.FOOTER.draw(cr, r)
    # One line per bay, one type size for the whole rail, as the reference
    # rail reads. Values are the SHORT forms so none is ever ellipsised; the
    # full archive record is in the header and the field.
    vals = (("AOM-1",), (sp.archive,), (sp.cls.split()[0], "MATH."),
            (sp.origin, "SYNTH."), (sp.symmetry_short, sp.symmetry_short[:5]),
            ("LIVE",), (m.behavior, m.behavior[:4]))
    b0 = P.bay("bay_0")
    inner = [P.bay(f"bay_{i}").inset(max(3.0, b0.h * 0.24), 0.0)
             for i in range(len(vals))]
    vsz = max(MIN_TEXT, min(t.micro * 1.02, b0.h * 0.38))
    ksz = max(MIN_TEXT, min(t.micro * 0.80, b0.h * 0.26))
    # one size for the rail: shrink (never below the floor) until every
    # value's FULL form fits, then fall back to short forms bay by bay
    while vsz > MIN_TEXT and any(
            _text_w(v[0], vsz, _W_MEDIUM, t.tracking * 0.5) > bi.w
            for v, bi in zip(vals, inner)):
        vsz = max(MIN_TEXT, vsz - 0.25)
    for v, bi in zip(vals, inner):
        txt = fit_first(v, vsz, _W_MEDIUM, t.tracking * 0.5, bi.w)
        if bi.valid and txt:
            _show(cr, txt, vsz, _W_MEDIUM, t.tracking * 0.5, bi.x,
                  bi.cy + _cap(vsz) * 0.5, INK_BRIGHT, 0.92, "l")
    tb = P.bay("terminal").inset(max(4.0, b0.h * 0.30), max(1.5, b0.h * 0.10))
    if tb.valid:
        base = tb.cy + _cap(vsz) * 0.5
        w0 = _show(cr, "SYSTEM BUS", ksz, _W_NORMAL, t.tracking, tb.x,
                   base, INK_TECH, 0.80, "l")
        w1 = _show(cr, "ONLINE", vsz, _W_MEDIUM, t.tracking * 0.6,
                   tb.x + w0 + vsz * 0.7, base, LIME, 0.96, "l")
        mx = tb.x + w0 + w1 + vsz * 2.0
        motto = "OBSERVE · UNDERSTAND · EXTEND"
        # The motto is shown whole or not at all: an ellipsised motto is noise.
        if _text_w(motto, ksz, _W_NORMAL, t.tracking) <= tb.right - mx:
            _show(cr, motto, ksz, _W_NORMAL, t.tracking, tb.right, base,
                  INK_DIM, 0.85, "r")


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
_REGION_CACHE: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()
_LAYER_LIMIT = 2
_REGION_LIMIT = 8


def _layer_key(L: Layout, m: ConsoleModel, mem_total: float = 0.0) -> tuple:
    """Every discrete input the static passes read. Nothing live belongs here.

    `mem_total` is part of the key because the memory graph's fixed scale is
    the installed memory; it is constant for the life of the process.
    """
    return (int(round(L.width)), int(round(L.height)), L.state.value,
            m.species.key, m.active, m.behavior,
            L.show_chassis, L.show_footer, L.show_controls, L.show_status,
            round(mem_total, 1), hidpi.scale())


def six_module(L: Layout) -> bool:
    """True when the full six-module machine is drawn (INSTRUMENT/ARCHIVE)."""
    return L.state is not LayoutState.COMPACT and L.readout_vertical


def _cached_layer(cache, key, w: int, h: int, paint,
                  limit: int = _LAYER_LIMIT) -> cairo.ImageSurface | None:
    hit = cache.get(key)
    if hit is not None:
        cache.move_to_end(key)
        return hit
    if w < 1 or h < 1:
        return None
    surf = hidpi.surface(w, h)
    paint(cairo.Context(surf))
    surf.flush()
    cache[key] = surf
    while len(cache) > limit:
        cache.popitem(last=False)
    return surf


def _blit(cr, surf) -> None:
    cr.save()
    cr.set_source_surface(surf, 0, 0)
    cr.paint()
    cr.restore()


def clear_static_cache() -> None:
    """Drop derived surfaces. Purely a memory operation; changes no output."""
    _UNDER_CACHE.clear()
    _OVER_CACHE.clear()
    _REGION_CACHE.clear()
    _TRACE_CACHE.clear()


# --------------------------------------------------------------------------
# composition: static layers + live regions
# --------------------------------------------------------------------------
# The frame is composed from, bottom to top:
#
#   LAYER 0-1  under   background, 01 shell, glass, graticule     (static)
#   LAYER 2    organism, clipped to the glass                     (per frame)
#   LAYER 3    over    03 bezel, 02 header, 04 rack, 05 selector
#                      plate, 06 rail + all static type           (static)
#   LAYER 4-6  regions header clock, rack readouts, selector keys,
#                      each with its own light spill              (on change)
#
# A REGION is a small rectangle that is re-rendered only when its own inputs
# change - the clock once a second, the rack when a telemetry sample lands,
# the keys on input. It starts from the static composite of that rectangle,
# so placing it over the static layers is seamless, and it never overlaps the
# glass. Per frame, the only thing rasterised is the organism.
@dataclass(frozen=True, slots=True)
class Region:
    name: str
    rect: Rect              # logical, snapped to whole device pixels
    surface: cairo.ImageSurface
    key: tuple


def _snap(r: Rect, pad: float = 0.0) -> Rect:
    """Grow `r` to whole DEVICE pixels, so a region's copy of the static
    layers lands pixel-for-pixel on them and cannot leave a seam."""
    ds = hidpi.scale()
    x0 = math.floor((r.x - pad) * ds)
    y0 = math.floor((r.y - pad) * ds)
    x1 = math.ceil((r.right + pad) * ds)
    y1 = math.ceil((r.bottom + pad) * ds)
    return Rect(x0 / ds, y0 / ds, (x1 - x0) / ds, (y1 - y0) / ds)


def layer_under(L: Layout, m: ConsoleModel) -> cairo.ImageSurface | None:
    w, h = int(round(L.width)), int(round(L.height))

    def paint(c2):
        draw_background(c2, L.width, L.height)
        _draw_chassis(c2, L)
        _draw_field_static(c2, L, m)
    return _cached_layer(_UNDER_CACHE, _layer_key(L, m), w, h, paint)


def layer_over(L: Layout, m: ConsoleModel,
               tel: Telemetry) -> cairo.ImageSurface | None:
    w, h = int(round(L.width)), int(round(L.height))
    six = six_module(L)

    def paint(c2):
        light = LightField()
        _draw_stage(c2, L, m, light)
        _draw_header(c2, L, m, light, static=True, live=False)
        if six:
            _draw_rack_static(c2, L, tel)
        _draw_controls(c2, L, m, light, static=True, live=False)
        _draw_footer(c2, L, m)
        # constant emitters (the glass, the LIVE lamp) belong to the metal
        light.paint(c2)
    return _cached_layer(_OVER_CACHE, _layer_key(L, m, tel.mem_total_gb),
                         w, h, paint)


def _region(name: str, rect: Rect, key: tuple, under, over, paint_live,
            pad: float = 6.0) -> Region | None:
    r = _snap(rect, pad)
    if r.w < 2.0 or r.h < 2.0:
        return None
    full = (name, r.x, r.y, r.w, r.h, hidpi.scale(), id(under), id(over)) + key
    hit = _REGION_CACHE.get(full)
    if hit is None:
        hit = hidpi.surface(r.w, r.h)
        c = cairo.Context(hit)
        c.translate(-r.x, -r.y)
        for layer in (under, over):
            if layer is not None:
                c.set_source_surface(layer, 0, 0)
                c.paint()
        light = LightField()
        paint_live(c, light)
        light.paint(c, clip=r)
        hit.flush()
        _REGION_CACHE[full] = hit
        while len(_REGION_CACHE) > _REGION_LIMIT:
            _REGION_CACHE.popitem(last=False)
    else:
        _REGION_CACHE.move_to_end(full)
    return Region(name, r, hit, full)


def regions(L: Layout, m: ConsoleModel, tel: Telemetry, fps: float,
            frame_ms: float, under=None, over=None) -> list[Region]:
    """The live regions for this frame. Each is cached on its OWN inputs."""
    if under is None:
        under = layer_under(L, m)
    if over is None:
        over = layer_over(L, m, tel)
    out: list[Region] = []
    six = six_module(L)
    hist_v = tuple(m.history[k].version for k in History.CHANNELS) \
        if m.history is not None else ()
    fps_i = int(round(fps))
    tel_key = (round(tel.cpu_pct, 1), tel.temp_c, round(tel.mem_used_gb, 2),
               round(tel.mem_total_gb, 1), tel.temp_available,
               round(tel.cpu_load, 3), round(tel.temperature, 3),
               round(tel.memory_pressure, 3))

    # header clock: once a second
    clk = time.strftime("%H:%M:%S")
    if six:
        P = MOD.HEADER.place(header_panel(L))
        hb = P.bay("clock") if L.header.valid else Rect(0, 0, 0, 0)
    else:
        hb = L.header
    if hb.valid:
        reg = _region("clock", hb, (clk,), under, over,
                      lambda c, lf: _draw_header(c, L, m, lf, static=False,
                                                 live=True))
        if reg:
            out.append(reg)

    # telemetry: when a sample lands or the displayed FPS changes
    if L.readout.valid:
        if six:
            def rack(c, lf):
                _draw_rack_live(c, L, tel, fps_i, frame_ms, m, lf)
        else:
            def rack(c, lf):
                _draw_condensed(c, L.readout, L, tel, fps_i, frame_ms, m, lf)
        reg = _region("rack", L.readout, (tel_key, fps_i, hist_v), under,
                      over, rack)
        if reg:
            out.append(reg)

    # selector keys: on input only
    if L.show_controls and L.controls.valid:
        kkey = (m.active, m.pressed, m.focus, m.disabled, m.mode_state,
                m.cycle_state)
        reg = _region("keys", L.controls, kkey, under, over,
                      lambda c, lf: _draw_controls(c, L, m, lf, static=False,
                                                   live=True))
        if reg:
            out.append(reg)
    return out


def draw_under(cr, L: Layout, m: ConsoleModel, vp_scale: float = 1.0) -> None:
    """Everything BEHIND the organism (headless / cairo path)."""
    surf = layer_under(L, m)
    if surf is not None:
        _blit(cr, surf)


def draw_over(cr, L: Layout, tel: Telemetry, fps: float, frame_ms: float,
              m: ConsoleModel, light: LightField) -> None:
    """Everything IN FRONT of the organism (headless / cairo path).

    Composed from exactly the same static layers and regions the live widget
    uploads as textures, so a headless frame IS a live frame.
    """
    under = layer_under(L, m)
    over = layer_over(L, m, tel)
    if over is not None:
        _blit(cr, over)
    for reg in regions(L, m, tel, fps, frame_ms, under, over):
        cr.save()
        cr.set_source_surface(reg.surface, reg.rect.x, reg.rect.y)
        cr.rectangle(reg.rect.x, reg.rect.y, reg.rect.w, reg.rect.h)
        cr.fill()
        cr.restore()
