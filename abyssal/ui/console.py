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
from ..skin.surface import (draw_nine, draw_sprite, draw_sprite_fit,
                            draw_sprite_rot90, sprite_size)
from . import segment as SEG
from . import selector as SEL
from .chrome import _cap, _show as _show_raw, _text_w, _W_MEDIUM, _W_NORMAL

TAU = math.tau

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

    def trace(self, key: str) -> Trace:
        t = self.traces.get(key)
        if t is None:
            t = Trace()
            self.traces[key] = t
        return t


# --------------------------------------------------------------------------
# pure geometry - shared by draw, hit test and QA
# --------------------------------------------------------------------------
_MODE_ASPECT = 160.0 / 226.0
_CYCLE_ASPECT = 192.0 / 113.0
_MODE_GAP = 0.045        # of bank height


def control_geometry(L: Layout) -> tuple[SEL.BankGeometry, Rect]:
    """(selector bank, mode key rect). Both may be invalid/empty."""
    if not L.show_controls or not L.controls.valid:
        return (SEL.layout(Rect(0, 0, 0, 0)), Rect(0, 0, 0, 0))

    box = L.controls
    mode_w = box.h * _MODE_ASPECT
    gap = box.h * _MODE_GAP
    # Reserve the mode key first, then let the bank fill what is left. The bank
    # centres itself inside that, so the group always reads as one cluster.
    bank_box = Rect(box.x, box.y, max(0.0, box.w - mode_w - gap * 2.0), box.h)
    geo = SEL.layout(bank_box, n=len(CATALOGUE))
    mode = Rect(geo.x + geo.w + gap, box.y, mode_w, box.h)
    cyc_w = (geo.h * 0.46) * _CYCLE_ASPECT + gap
    group_w = cyc_w + geo.w + gap + mode_w
    shift = (box.w - group_w) * 0.5 + cyc_w - (geo.x - box.x)
    if abs(shift) > 0.5:
        geo = SEL.BankGeometry(geo.x + shift, geo.y, geo.w, geo.h,
                               geo.cap_l, geo.cap_r, geo.cell_w, geo.n)
        mode = Rect(mode.x + shift, mode.y, mode.w, mode.h)
    return (geo, mode)


def cycle_rect(geo, mode: Rect) -> Rect:
    """The two-way cycle rocker, seated to the LEFT of the bank.

    It belongs to the switching cluster, so it sits with it rather than being
    parked somewhere else on the fascia.
    """
    if not geo.valid or not mode.valid:
        return Rect(0.0, 0.0, 0.0, 0.0)
    h = geo.h * 0.46
    w = h * _CYCLE_ASPECT
    gap = geo.h * _MODE_GAP
    return Rect(geo.x - gap - w, geo.y + (geo.h - h) * 0.5, w, h)


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
    """('key', i) | ('mode', 0) | None -- what the pointer is over."""
    geo, mode = control_geometry(L)
    i = SEL.hit(geo, px, py)
    if i is not None:
        return ("key", i)
    if mode.valid and mode.x <= px <= mode.right and mode.y <= py <= mode.bottom:
        return ("mode", 0)
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


def _polar_grid(cr, r: Rect, alpha: float = 1.0, t=None) -> None:
    """Observation graticule: dotted radial rings, axes, ticks, cardinals.

    Drawn in code, never baked into the glass, so it tracks the field rather
    than being a picture of a grid.
    """
    if r.w < 60.0 or r.h < 60.0:
        return
    cx, cy = r.cx, r.cy
    rad = min(r.w, r.h) * 0.455
    cr.save()
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.clip()
    cr.set_line_width(1.0)

    _rings(cr, cx, cy, rad, alpha)

    # axes reach the field edges, as an instrument's crosshair does
    cr.set_source_rgba(*rgba(RULE, 0.85 * alpha))
    cr.move_to(r.x + r.w * 0.035, round(cy) + 0.5)
    cr.line_to(r.right - r.w * 0.035, round(cy) + 0.5)
    cr.move_to(round(cx) + 0.5, r.y + r.h * 0.045)
    cr.line_to(round(cx) + 0.5, r.bottom - r.h * 0.045)
    cr.stroke()

    # regular ticks along both axes
    cr.set_source_rgba(*rgba(RULE, 0.95 * alpha))
    step = rad * 0.2
    n = int((max(r.w, r.h) * 0.47) / step)
    for i in range(1, n + 1):
        d = i * step
        tk = 3.0 if i % 5 else 5.5
        for sx in (-1, 1):
            px = cx + sx * d
            if r.x < px < r.right:
                cr.move_to(round(px) + 0.5, cy - tk)
                cr.line_to(round(px) + 0.5, cy + tk)
            py = cy + sx * d
            if r.y < py < r.bottom:
                cr.move_to(cx - tk, round(py) + 0.5)
                cr.line_to(cx + tk, round(py) + 0.5)
    cr.stroke()

    # cardinal crosses on the outer ring
    for ang in (0.0, TAU * 0.25, TAU * 0.5, TAU * 0.75):
        px, py = cx + math.cos(ang) * rad, cy + math.sin(ang) * rad
        k = max(3.0, rad * 0.028)
        cr.move_to(px - k, py)
        cr.line_to(px + k, py)
        cr.move_to(px, py - k)
        cr.line_to(px, py + k)
    cr.stroke()
    cr.restore()

    if t is not None and r.w > 300.0:
        sz = max(MIN_TEXT, min(t.micro, 10.0))
        for lab, ax, ay, al in (
                ("Y+", cx, r.y + r.h * 0.045 + _cap(sz) * 1.2, "c"),
                ("Y-", cx, r.bottom - r.h * 0.045 - _cap(sz) * 0.2, "c"),
                ("X-", r.x + r.w * 0.035 + _cap(sz) * 0.3, cy - _cap(sz) * 0.5, "l"),
                ("X+", r.right - r.w * 0.035 - _cap(sz) * 0.3, cy - _cap(sz) * 0.5, "r")):
            _show(cr, lab, sz, _W_NORMAL, t.tracking, ax, ay, INK_DIM,
                  0.72 * alpha, al)


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
# header
# --------------------------------------------------------------------------
def _draw_header(cr, L: Layout, m: ConsoleModel, light: LightField) -> None:
    """Title block, centre identity block, status block - then a status rail.

    The reference header is three compartments divided by machined grooves,
    with a shorter four-bay rail beneath. Reproducing that compartment logic is
    most of what separates "a title bar" from "the top of an instrument".
    """
    r = L.header
    if not r.valid:
        return
    # The reference header is a dark inset field, not an applied plaque: at the
    # heights a header actually gets, a plaque bevel would eat the interior
    # before either line of type could fit.
    inner = _rail(cr, r, depth=0.9)
    t = L.type
    sp = m.species
    pad = max(6.0, inner.h * 0.12)

    wide = inner.w > 640.0
    # Compartment boundaries, as fractions of the header interior.
    x_title = inner.x + pad
    x_mid = inner.x + inner.w * (0.44 if wide else 0.52)
    x_status = inner.x + inner.w * 0.70

    # --- left: instrument name over specimen name -------------------------
    # Two stacked lines need real room. Below it the instrument name is dropped
    # rather than crushed: the specimen name is the line that matters, and two
    # crammed lines read as a rendering fault, not as density.
    two_line = inner.h >= 36.0
    title_sz = min(t.title, inner.h * 0.30)
    name_sz = min(t.specimen, inner.h * (0.46 if two_line else 0.72))
    tw = (x_mid - x_title) - pad
    if two_line:
        stack = _cap(title_sz) * 1.05 + _cap(name_sz) * 1.34
        top = inner.y + max(0.0, (inner.h - stack) * 0.5)
        _show(cr, "ABYSSAL ORGANISM MONITOR", title_sz, _W_NORMAL, t.tracking,
              x_title, top + _cap(title_sz), INK, 0.80, "l", tw)
        lead = _text_w("SPECIMEN ", title_sz, _W_NORMAL,
                       t.tracking) if wide else 0.0
        if wide:
            _show(cr, "SPECIMEN", title_sz, _W_NORMAL, t.tracking, x_title,
                  top + _cap(title_sz) + _cap(name_sz) * 1.30,
                  INK_DIM, 0.75, "l")
        _show(cr, sp.name, name_sz, _W_MEDIUM, t.tracking * 0.7,
              x_title + lead, top + _cap(title_sz) + _cap(name_sz) * 1.30,
              INK_BRIGHT, 1.0, "l", max(40.0, tw - lead))
    else:
        _show(cr, sp.name, name_sz, _W_MEDIUM, t.tracking * 0.7, x_title,
              inner.cy + _cap(name_sz) * 0.5, INK_BRIGHT, 1.0, "l", tw)

    if not wide:
        # Only the right end, or the LIVE plaque lands on top of the name.
        sx = inner.x + inner.w * 0.58
        _header_status(cr, L, m,
                       Rect(sx, inner.y, inner.right - sx - pad, inner.h),
                       light, compact=True)
        return

    _divider(cr, x_mid - pad * 0.7, inner.y + inner.h * 0.10,
             inner.bottom - inner.h * 0.10)
    _divider(cr, x_status - pad * 0.7, inner.y + inner.h * 0.10,
             inner.bottom - inner.h * 0.10)

    # --- centre: vernacular name between rules ----------------------------
    mid_w = x_status - x_mid - pad * 2.0
    sub_sz = min(t.subtitle * 1.25, inner.h * 0.30)
    cy = inner.y + inner.h * 0.42
    _show(cr, sp.epithet, sub_sz, _W_NORMAL, t.tracking * 1.5,
          x_mid + mid_w * 0.5 + pad, cy, INK_BRIGHT, 0.92, "c", mid_w)
    ew = _text_w(sp.epithet, sub_sz, _W_NORMAL, t.tracking * 1.5)
    rule_y = cy - _cap(sub_sz) * 0.35
    gapw = ew * 0.5 + mid_w * 0.06
    _hairline(cr, x_mid + pad, x_mid + mid_w * 0.5 + pad - gapw, rule_y,
              RULE, 0.85)
    _hairline(cr, x_mid + mid_w * 0.5 + pad + gapw, x_mid + mid_w + pad,
              rule_y, RULE, 0.85)
    _show(cr, "BIOCOMPUTATIONAL OBSERVATION TERMINAL",
          min(t.micro, inner.h * 0.19), _W_NORMAL, t.tracking,
          x_mid + mid_w * 0.5 + pad, inner.bottom - inner.h * 0.16,
          INK_DIM, 0.72, "c", mid_w)

    # --- right: SYSTEM STATUS + LIVE plaque, and the clock ----------------
    _header_status(cr, L, m, Rect(x_status, inner.y,
                                  inner.right - x_status - pad, inner.h),
                   light, compact=False)


def _plaque_recess(cr, r: Rect) -> Rect:
    """A metal plate with a dark inset field cut into it.

    The plate alone is not enough: instrument ink needs a dark ground.
    """
    draw_nine(cr, C.PLATE, r.x, r.y, r.w, r.h)
    x, y, w, h = C.PLATE.content(r.x, r.y, r.w, r.h)
    return _rail(cr, Rect(x, y, w, h), depth=0.9)


def _header_status(cr, L: Layout, m: ConsoleModel, box: Rect,
                   light: LightField, compact: bool) -> None:
    """The LIVE annunciator in its own seated plaque, plus the clock."""
    t = L.type
    if box.w < 90.0:
        return
    pad = max(4.0, box.h * 0.12)
    lab_sz = min(t.micro, box.h * 0.22)

    if not compact:
        _show(cr, "SYSTEM STATUS", lab_sz, _W_NORMAL, t.tracking,
              box.x + pad, box.y + box.h * 0.34, INK_DIM, 0.80, "l", box.w)

    # LIVE plaque
    pw = min(box.w * 0.46, 104.0)
    ph = min(box.h * 0.40, 26.0)
    px = box.x + pad
    py = box.y + box.h * (0.44 if not compact else 0.28)
    live = _rail(cr, Rect(px, py, pw, ph), depth=0.85)
    lamp_h = min(live.h * 1.05, 15.0)
    draw_sprite_fit(cr, C.lamp("small", "nominal"),
                    live.x + lamp_h * 0.62, live.cy, lamp_h)
    light.add(live.x + lamp_h * 0.62, live.cy, lamp_h * 2.2, L_CHART, 0.18)
    _show(cr, "LIVE", min(t.label, live.h * 0.62), _W_MEDIUM, t.tracking,
          live.x + lamp_h * 1.30, live.cy + _cap(min(t.label, live.h * 0.62)) * 0.5,
          LIME, 0.96, "l", live.w)

    # clock plaque
    cw = box.w - pw - pad * 2.4
    if cw > 70.0:
        cx0 = px + pw + pad * 1.2
        clk = _rail(cr, Rect(cx0, py, cw, ph), depth=0.85)
        dh = min(clk.h * 0.74, 20.0)
        SEG.draw_right(cr, time.strftime("%H:%M:%S"), clk.right - clk.w * 0.05,
                       clk.cy - dh * 0.5, dh, SEG.CYAN)


def _draw_status_rail(cr, L: Layout, m: ConsoleModel) -> None:
    """SYS BUS / ARCHIVE / INSTR / FIELD - the reference's second header row."""
    r = L.status
    if not L.show_status or not r.valid:
        return
    inner = _rail(cr, r)
    if inner.w < 200.0:
        return
    t = L.type
    sp = m.species
    bays = (("SYS BUS", "ONLINE", LIME), ("ARCHIVE", "READY", INK_BRIGHT),
            ("INSTR", "NOMINAL", LIME), ("FIELD", sp.symmetry, INK_BRIGHT))
    cw = inner.w / len(bays)
    sz = max(5.5, min(t.micro, inner.h * 0.52))
    for i, (k, v, col) in enumerate(bays):
        bx = inner.x + i * cw
        base = inner.cy + _cap(sz) * 0.5
        kw = _show(cr, k + ":", sz, _W_NORMAL, t.tracking, bx + cw * 0.05,
                   base, INK_DIM, 0.85, "l", cw * 0.5)
        _show(cr, v, sz, _W_MEDIUM, t.tracking * 0.6,
              bx + cw * 0.05 + kw + sz * 0.8, base, col, 0.95, "l", cw * 0.42)
        if i:
            _divider(cr, bx - cw * 0.02, inner.y, inner.bottom, 0.8)


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


#: chrome refuses to draw below this; ask for it or do not draw at all.
MIN_TEXT = 7.0

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


def _radius_ruler(cr, r: Rect, vp_scale: float, t, alpha: float = 1.0) -> float:
    """The vertical RADIUS (mm) axis down the left of the field.

    Ticks are derived from the viewport scale, so the labels describe the
    organism actually on screen rather than being decorative numbers.
    """
    if r.h < 180.0 or r.w < 220.0:
        return 0.0
    sz = max(MIN_TEXT, min(t.micro * 0.92, r.h * 0.030))
    lab_w = _text_w("1.25", sz, _W_NORMAL, t.tracking)
    x0 = r.x
    x_tick = x0 + lab_w + sz * 1.5
    steps = (1.25, 1.00, 0.75, 0.50, 0.25, 0.0, 0.25, 0.50, 0.75, 1.00, 1.25)
    span = r.h * 0.74
    top = r.cy - span * 0.5
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba(RULE, 0.9 * alpha))
    cr.move_to(round(x_tick) + 0.5, top)
    cr.line_to(round(x_tick) + 0.5, top + span)
    cr.stroke()
    cr.restore()
    for i, v in enumerate(steps):
        yy = top + span * (i / (len(steps) - 1.0))
        cr.save()
        cr.set_line_width(1.0)
        cr.set_source_rgba(*rgba(RULE, 0.95 * alpha))
        cr.move_to(x_tick - sz * 0.45, round(yy) + 0.5)
        cr.line_to(x_tick, round(yy) + 0.5)
        cr.stroke()
        cr.restore()
        _show(cr, f"{v:.2f}", sz, _W_NORMAL, t.tracking,
              x_tick - sz * 0.85, yy + _cap(sz) * 0.5, INK_DIM, 0.82 * alpha, "r")
    _show(cr, "RADIUS (mm)", sz, _W_NORMAL, t.tracking, x0,
          top - sz * 1.9, INK_DIM, 0.80 * alpha, "l")
    # The gutter must clear the CAPTION too, not just the tick labels, or the
    # first annotation block lands on top of it.
    cap_w = _text_w("RADIUS (mm)", sz, _W_NORMAL, t.tracking)
    return max(x_tick - r.x + sz, cap_w + sz * 0.8)


def _scale_bar(cr, r: Rect, t, alpha: float = 1.0) -> None:
    """The horizontal SCALE (mm) bar in the lower right of the field."""
    if r.w < 300.0 or r.h < 200.0:
        return
    sz = max(MIN_TEXT, min(t.micro * 0.92, r.h * 0.028))
    bw = min(r.w * 0.30, 210.0)
    bx = r.right - r.w * 0.045 - bw
    by = r.bottom - r.h * 0.085
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgba(INK_DIM, 0.85 * alpha))
    cr.move_to(bx, round(by) + 0.5)
    cr.line_to(bx + bw, round(by) + 0.5)
    for i in range(13):
        tx = bx + bw * (i / 12.0)
        h = sz * (0.78 if i % 4 == 0 else 0.42)
        cr.move_to(round(tx) + 0.5, by)
        cr.line_to(round(tx) + 0.5, by - h)
    cr.stroke()
    cr.restore()
    for i, v in enumerate(("0", "0.5", "1.0", "1.5")):
        _show(cr, v, sz, _W_NORMAL, t.tracking, bx + bw * (i / 3.0),
              by - sz * 1.25, INK_DIM, 0.82 * alpha, "c")
    _show(cr, "SCALE (mm)", sz, _W_NORMAL, t.tracking, bx + bw * 0.5,
          by + sz * 1.55, INK_DIM, 0.78 * alpha, "c")


def _field_block(cr, x: float, y: float, w: float, rows, t,
                 heading: str | None = None, alpha: float = 1.0,
                 align_r: bool = False) -> float:
    """One annotation block inside the observation field. Returns its height."""
    sz = max(MIN_TEXT, min(t.micro, 10.5))
    line = _cap(sz) * 1.95
    yy = y
    if heading:
        hz = sz * 1.12
        _show(cr, heading, hz, _W_MEDIUM, t.tracking * 1.2,
              (x + w) if align_r else x, yy + _cap(hz), INK_BRIGHT,
              0.95 * alpha, "r" if align_r else "l", w)
        hw = _text_w(heading, hz, _W_MEDIUM, t.tracking * 1.2)
        _hairline(cr, (x + w - hw) if align_r else x,
                  (x + w) if align_r else (x + hw), yy + _cap(hz) * 1.45,
                  RULE, 0.9 * alpha)
        yy += line * 1.18
    # Size the key column from the widest key actually present, so a long
    # label like MAGNIFICATION never collides with its value.
    keyw = min(w * 0.66,
               max(_text_w(k + ":", sz, _W_NORMAL, t.tracking)
                   for k, _ in rows) + sz * 0.9)
    for k, v in rows:
        base = yy + _cap(sz)
        if align_r:
            _show(cr, v, sz, _W_MEDIUM, t.tracking * 0.6, x + w, base,
                  INK, 0.92 * alpha, "r", w - keyw)
            _show(cr, k + ":", sz, _W_NORMAL, t.tracking, x + w - keyw - sz * 0.6,
                  base, INK_DIM, 0.80 * alpha, "r", keyw)
        else:
            _show(cr, k + ":", sz, _W_NORMAL, t.tracking, x, base,
                  INK_DIM, 0.80 * alpha, "l", keyw)
            _show(cr, v, sz, _W_MEDIUM, t.tracking * 0.6, x + keyw, base,
                  INK, 0.92 * alpha, "l", w - keyw)
        yy += line
    return yy - y


def _draw_stage_graticule(cr, L: Layout, m: ConsoleModel,
                          vp_scale: float = 1.0) -> None:
    """Everything inside the glass EXCEPT the organism, drawn underneath it.

    The reference field is a working optical instrument: a measured radius
    axis, a scale bar, registration brackets, a polar graticule and four
    annotation blocks. All of it is drawn in code from live state.
    """
    glass = stage_content(L)
    if not glass.valid:
        return
    # The chassis plate sits behind everything now, so the bezel's transparent
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
    sp = m.species
    med = glass.w >= 360.0 and glass.h >= 250.0
    big = glass.w >= 560.0 and glass.h >= 330.0

    safe = field_area(L)
    _corner_brackets(cr, safe, min(glass.w, glass.h) * 0.035)

    gutter = _radius_ruler(cr, safe, vp_scale, t) if med else 0.0
    field = Rect(safe.x + gutter, glass.y, glass.w - gutter - (safe.x - glass.x),
                 glass.h)
    _polar_grid(cr, field, t=t)

    if not med:
        return

    pad_x = glass.w * 0.012
    pad_y = 0.0
    # With only the specimen block shown there is no right-hand block to clear,
    # so it may take a wider column instead of ellipsising its values.
    bw = min(glass.w * (0.345 if big else 0.46), 300.0)

    _field_block(cr, safe.x + gutter + pad_x, safe.y, bw, (
        ("MODE", "LIVE OBSERVATION"),
        ("MAGNIFICATION", f"{m.magnification:.1f}x"),
        ("FIELD WIDTH", f"{m.field_mm:.2f} mm"),
        ("FOCUS", "AUTO"),
        ("APERTURE", m.aperture),
    ), t, heading="SPECIMEN FIELD")

    if not big:
        return

    _field_block(cr, safe.right - bw, safe.y, bw, (
        ("MORPHOLOGY", sp.morphology),
        ("PHASE", f"{m.phase:.3f} \u03c0"),
        ("ROTATION", f"{m.rotation:.2f} RPM"),
        ("SYMMETRY", f"{sp.lobes}-FOLD"),
        ("BEHAVIOR", m.behavior),
    ), t, align_r=True)

    cx, cy, cz = m.coords
    h = _field_block(cr, safe.x + gutter + pad_x, 0.0, bw * 1.18, (
        ("VECTOR FIELD", "ACTIVE"),
        ("AXIS LOCK", "STABLE"),
        ("TRACKING", "CENTROID"),
        ("COORDINATES", f"{cx:+.3f}, {cy:+.3f}, {cz:+.3f} (mm)"),
    ), t, alpha=0.0)
    _field_block(cr, safe.x + gutter + pad_x, safe.bottom - h, bw * 1.18, (
        ("VECTOR FIELD", "ACTIVE"),
        ("AXIS LOCK", "STABLE"),
        ("TRACKING", "CENTROID"),
        ("COORDINATES", f"{cx:+.3f}, {cy:+.3f}, {cz:+.3f} (mm)"),
    ), t)

    _scale_bar(cr, Rect(field.x, safe.y, field.w, safe.h), t)


# --------------------------------------------------------------------------
# telemetry modules
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Channel:
    key: str
    title: str
    sub: str
    unit: str
    caption: str                       # under the graph well
    axis: tuple[str, str, str]         # graph y-axis ticks, high -> low
    lamps: tuple[str, str, str, str]   # annunciator micro-labels
    bars: bool = False                 # graph well shows a bargraph, not a trace


_CHANNELS = (
    _Channel("cpu", "PROCESSOR", "BIOCOMPUTATIONAL CORE", "%",
             "CPU LOAD (LAST 60 s)", ("100", "50", "0"),
             ("CLK", "ALG", "NET", "I/O")),
    _Channel("thermal", "THERMAL", "ENVIRONMENT & METABOLIC", "\u00b0C",
             "TEMPERATURE (\u00b0C)", ("120", "80", "40"),
             ("SEN", "REG", "FAN", "SYS")),
    _Channel("memory", "MEMORY", "FIELD DATA & STATE", "G",
             "USAGE (GIGABYTES)", ("13.5", "6.75", "0"),
             ("MEM", "BUF", "CACHE", "I/O"), bars=True),
    _Channel("frame", "FRAME / RENDER", "VISUALISATION PIPELINE", "FPS",
             "FRAME TIME (ms)", ("33", "16", "0"),
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


def _graph_well(cr, r: Rect, ch: _Channel, t, alpha: float = 1.0) -> Rect:
    """Label a graph well and return its plotting area.

    Reserves a gutter on the left for the y-axis ticks and a ledge along the
    bottom for the caption - both INSIDE the well, as the reference has them.
    An unlabelled well is what made the previous build's telemetry read as
    decorative rather than measured.
    """
    if r.w < 90.0 or r.h < 34.0:
        return r
    sz = max(MIN_TEXT, min(t.micro * 0.84, r.h * 0.24))
    pad = sz * 0.45
    cap_h = _cap(sz) * 1.9 if r.h > 46.0 else 0.0
    plot_h = r.h - cap_h

    wmax = max(_text_w(a, sz, _W_NORMAL, t.tracking) for a in ch.axis)
    for i, lab in enumerate(ch.axis):
        yy = r.y + (plot_h - sz) * (i / (len(ch.axis) - 1.0)) + _cap(sz)
        _show(cr, lab, sz, _W_NORMAL, t.tracking, r.x + pad + wmax, yy,
              INK_DIM, 0.75 * alpha, "r")
    if cap_h > 0.0:
        _show(cr, ch.caption, sz, _W_NORMAL, t.tracking, r.x + pad,
              r.bottom - cap_h * 0.18, INK_DIM, 0.72 * alpha, "l", r.w - pad * 2)
    gx = r.x + pad + wmax + sz * 0.5
    return Rect(gx, r.y, max(4.0, r.right - pad - gx), plot_h)


def _draw_module(cr, r: Rect, ch: _Channel, idx: int, L: Layout,
                 tel: Telemetry, fps: float, frame_ms: float,
                 m: ConsoleModel, light: LightField) -> None:
    """One subsystem bay, assembled to the reference's structure.

    left rail | index plaque | title + subtitle | labelled annunciators
              | graph well (axis + caption) | numeric housing | status column
    """
    if r.w < 60.0 or r.h < 28.0:
        return
    draw_nine(cr, C.PLATE, r.x, r.y, r.w, r.h)
    ix, iy, iw, ih = C.PLATE.content(r.x, r.y, r.w, r.h)
    # Every text zone on this machine sits in a DARK RECESS, never on bare
    # metal: the instrument ink is a near-black palette and simply disappears
    # against a lit plate. The reference module is a dark inset inside a metal
    # frame, and that is what makes its microcopy readable.
    inner = _rail(cr, Rect(ix, iy, iw, ih), depth=0.9)
    t = L.type

    text, level, style, state, lamp_state = _channel_values(
        ch.key, tel, fps, frame_ms)
    m.trace(ch.key).push(level)

    rich = inner.h > 50.0 and inner.w > 240.0
    full = inner.h > 68.0 and inner.w > 360.0

    # --- left mounting rail ------------------------------------------------
    rail_w = 0.0
    if rich:
        rail_w = min(inner.w * 0.048, 22.0)
        sc = min(rail_w * 0.86, 13.0)
        draw_sprite_fit(cr, "part/screw_small",
                        inner.x + rail_w * 0.5, inner.y + sc * 0.75, sc)
        bar_h = inner.h * 0.52
        if bar_h > 20.0:
            draw_sprite_rot90(cr, "part/handle",
                              inner.x + rail_w * 0.5 - rail_w * 0.18,
                              inner.bottom - bar_h - inner.h * 0.06,
                              rail_w * 0.36, bar_h)
        rail_w += inner.w * 0.012

    body_x = inner.x + rail_w
    body_w = inner.w - rail_w
    head_h = min(inner.h * (0.36 if full else 0.40), _cap(t.label) * 3.6)

    # --- header rail: plaque, title, subtitle, labelled lamps -------------
    hx = body_x
    if rich:
        pw = min(body_w * 0.092, head_h * 1.45)
        ph = head_h * 0.68
        pr = Rect(hx, inner.y + (head_h - ph) * 0.35, pw, ph)
        draw_nine(cr, C.PLATE, pr.x, pr.y, pr.w, pr.h)
        _engrave(cr, f"0{idx + 1}", pr, min(t.label, pr.h * 0.66),
                 t.tracking * 0.4, INK_BRIGHT, "c")
        hx += pw + body_w * 0.022

    title_sz = min(t.label * 1.18, head_h * 0.46)
    _show(cr, ch.title, title_sz, _W_MEDIUM, t.tracking, hx,
          inner.y + _cap(title_sz) * (1.05 if full else 1.25),
          INK_BRIGHT, 0.97, "l", body_w * 0.46)
    if full:
        sub_sz = max(MIN_TEXT, min(t.micro * 0.86, head_h * 0.30))
        _show(cr, ch.sub, sub_sz, _W_NORMAL, t.tracking, hx,
              inner.y + _cap(title_sz) * 1.05 + _cap(sub_sz) * 1.85,
              INK_DIM, 0.74, "l", body_w * 0.52)

    if rich:
        lamp_h = min(head_h * 0.30, 11.0)
        lab_sz = max(MIN_TEXT, lamp_h * 0.80)
        lx = inner.right - lamp_h * 1.1
        for k in range(4):
            st = lamp_state if k == 0 else ("nominal" if k < 3 else "off")
            ly = inner.y + head_h * 0.62
            draw_sprite_fit(cr, C.lamp("small", st), lx, ly, lamp_h)
            if full:
                _show(cr, ch.lamps[3 - k], lab_sz, _W_NORMAL, t.tracking * 0.5,
                      lx, ly - lamp_h * 1.15, INK_DIM, 0.72, "c")
            lx -= max(lamp_h * 2.35, lab_sz * 3.6)

    body = Rect(body_x, inner.y + head_h, body_w, max(0.0, inner.h - head_h))
    if body.h < 14.0:
        return
    if rich:
        _hairline(cr, body.x, body.right, body.y - body.h * 0.04, RULE, 0.55)

    # --- body: graph well | numeric housing | status column ---------------
    gap = max(3.0, body.w * 0.014)
    if full:
        well_w = body.w * 0.355
        stat_w = body.w * 0.275
        val_w = body.w - well_w - stat_w - gap * 2.0
    elif rich:
        well_w = body.w * 0.32
        stat_w = 0.0
        val_w = body.w - well_w - gap
    else:
        well_w = stat_w = 0.0
        val_w = body.w * 0.66

    vx = body.x
    if well_w > 0.0:
        well = Rect(body.x, body.y, well_w, body.h * (0.86 if full else 0.74))
        draw_nine(cr, C.GRAPH_WELL, well.x, well.y, well.w, well.h)
        gx, gy, gw, gh = C.GRAPH_WELL.content(well.x, well.y, well.w, well.h)
        plot = _graph_well(cr, Rect(gx, gy, gw, gh), ch, t) if full \
            else Rect(gx, gy, gw, gh)
        if ch.bars:
            _bargraph(cr, plot.inset(0.0, plot.h * 0.18), level, style.lit,
                      segments=26)
        else:
            _sparkline(cr, plot, m.trace(ch.key).values, style.lit)
        light.glow(plot, style.lit, 0.05, spread=0.45)
        vx = body.x + well_w + gap

    # primary readout
    hr = Rect(vx, body.y, val_w, body.h * (0.86 if full else 0.74))
    if hr.w > 40.0 and hr.h > 16.0:
        draw_nine(cr, C.SEGMENT_HOUSING, hr.x, hr.y, hr.w, hr.h)
        sx, sy, sw, sh = C.SEGMENT_HOUSING.content(hr.x, hr.y, hr.w, hr.h)
        unit_sz = min(t.label, sh * 0.34)
        uw = _text_w(ch.unit, unit_sz, _W_NORMAL, t.tracking) + sw * 0.06
        avail = max(8.0, sw - uw)
        dh = min(sh * 0.94, SEG.fit_height(text, avail, sh * 0.94, style))
        if dh > 5.0:
            tw = SEG.measure(text, dh, style)
            tx = sx + max(0.0, (avail - tw) * 0.5)
            SEG.draw(cr, text, tx, sy + (sh - dh) * 0.5, dh, style)
            _show(cr, ch.unit, unit_sz, _W_NORMAL, t.tracking,
                  sx + sw, sy + sh * 0.70, style.lit, 0.90, "r")
        light.glow(Rect(sx, sy, sw, sh), style.lit, 0.09, spread=0.5)

    # status column
    if stat_w > 40.0:
        stx = vx + val_w + gap
        rows = list(_status_rows(ch.key, tel, fps, frame_ms, m))
        rows.append(("STATE", state))
        sz = max(MIN_TEXT, min(t.micro * 0.94, body.h * 0.165))
        line = body.h * 0.215
        yy = body.y + body.h * 0.045
        for j, (k, v) in enumerate(rows):
            last = j == len(rows) - 1
            base = yy + _cap(sz)
            _show(cr, k, sz, _W_NORMAL, t.tracking, stx, base,
                  INK_DIM, 0.82, "l", stat_w * 0.43)
            _show(cr, v, sz, _W_MEDIUM, t.tracking * 0.5, stx + stat_w, base,
                  style.lit if last else INK, 0.96 if last else 0.90,
                  "r", stat_w * 0.55)
            yy += line
    elif rich:
        stx = vx + val_w + gap
        if body.right - stx > 40.0:
            _show(cr, state, t.micro, _W_MEDIUM, t.tracking, stx,
                  body.y + _cap(t.micro) * 1.4, style.lit, 0.95, "l",
                  body.right - stx)

    # meter trough along the bottom
    mt_h = body.h * (0.17 if full else 0.20)
    mt = Rect(body.x, body.bottom - mt_h, body.w, mt_h * 0.92)
    if mt.w > 30.0 and mt.h > 5.0 and not full:
        draw_nine(cr, C.METER_TROUGH, mt.x, mt.y, mt.w, mt.h)
        bx, by, bw, bh = C.METER_TROUGH.content(mt.x, mt.y, mt.w, mt.h)
        _bargraph(cr, Rect(bx, by, bw, bh), level, style.lit)
    elif full:
        mt = Rect(body.x, body.bottom - mt_h * 0.86, body.w, mt_h * 0.72)
        draw_nine(cr, C.METER_TROUGH, mt.x, mt.y, mt.w, mt.h)
        bx, by, bw, bh = C.METER_TROUGH.content(mt.x, mt.y, mt.w, mt.h)
        _bargraph(cr, Rect(bx, by, bw, bh), level, style.lit, segments=40)


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

        dh = min(ih * 0.44, SEG.fit_height(text, cw * 0.86, ih * 0.44, style))
        if dh > 5.0:
            SEG.draw(cr, text, cell.x + cw * 0.06,
                     cell.y + ih * 0.40, dh, style)
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
    if cell_w < 190.0 or cell_h < 72.0:
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
def _draw_controls(cr, L: Layout, m: ConsoleModel, light: LightField) -> None:
    geo, mode = control_geometry(L)
    if not geo.valid:
        return
    # Seat the whole cluster in a shallow recess so it reads as fitted INTO the
    # fascia rather than laid on top of it.
    if L.show_chassis and L.controls.valid:
        pad = geo.h * 0.11
        cyc0 = cycle_rect(geo, mode)
        left = (cyc0.x if cyc0.valid else geo.x) - pad * 1.4
        right = (mode.right if mode.valid else geo.x + geo.w) + pad * 1.4
        _rail(cr, Rect(left, geo.y - pad, right - left,
                       geo.h + pad * 2.0), depth=0.65)
    SEL.draw(cr, geo, m.active, m.pressed, m.focus, m.disabled)

    t = L.type
    label_size = max(5.5, min(t.micro, geo.h * 0.115))
    for i, sp in enumerate(CATALOGUE):
        lr = geo.label_rect(i)
        dim = i in m.disabled
        _engrave(cr, sp.short, lr, label_size, t.tracking * 0.35,
                 INK_BRIGHT if i == m.active else INK,
                 "c", 0.35 if dim else (1.0 if i == m.active else 0.96))

        fr = geo.face_rect(i)
        ar = Rect(fr.x, fr.y + fr.h * 0.10, fr.w, fr.h * 0.5)
        _engrave(cr, sp.archive, ar, max(MIN_TEXT, label_size * 0.78),
                 t.tracking * 0.3, INK if i == m.active else INK_DIM,
                 "c", 0.30 if dim else 0.88)

        # a latched key throws a little light onto the fascia around it
        if i == m.active and not dim:
            lx, ly, lrad = SEL.lamp_point(geo, i)
            light.add(lx, ly, max(8.0, lrad * 7.0), L_CHART, 0.20)

    cyc = cycle_rect(geo, mode)
    if cyc.valid and cyc.x > L.controls.x - 1.0:
        draw_sprite(cr, C.cycle_key(m.cycle_state), cyc.x, cyc.y, cyc.w, cyc.h)

    if mode.valid and mode.w > 8.0:
        draw_sprite(cr, C.mode_key(m.mode_state), mode.x, mode.y,
                    mode.w, mode.h)
        if m.mode_state == "active":
            light.add(mode.cx, mode.cy, mode.h * 0.8, L_CHART, 0.18)
        elif m.mode_state == "error":
            light.add(mode.cx, mode.cy, mode.h * 0.8, L_AMBER, 0.16)
        elif m.mode_state == "armed":
            light.add(mode.cx, mode.cy, mode.h * 0.7, L_CYAN, 0.10)


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
    """The archive rail: badge, information bays, and the standing motto."""
    r = L.footer
    if not L.show_footer or not r.valid:
        return
    inner = _rail(cr, r)
    sp = m.species
    t = L.type
    wide = inner.w > 820.0

    # machined badge
    x0 = inner.x
    if wide and inner.h > 20.0:
        rad = inner.h * 0.40
        _reticle(cr, inner.x + rad * 1.15, inner.cy, rad)
        x0 = inner.x + rad * 2.6
        _divider(cr, x0 - rad * 0.5, inner.y, inner.bottom, 0.8)

    # standing motto, right end
    x1 = inner.right
    if wide and inner.h > 26.0:
        mz = max(MIN_TEXT, min(t.micro * 0.84, inner.h * 0.30))
        bw = _text_w("UNDERSTAND", mz, _W_NORMAL, t.tracking * 1.2) + mz * 3.2
        x1 = inner.right - bw
        _divider(cr, x1 - mz, inner.y, inner.bottom, 0.8)
        for i, word in enumerate(("OBSERVE", "UNDERSTAND", "EXTEND")):
            _show(cr, word, mz, _W_NORMAL, t.tracking * 1.2, x1 + mz * 0.4,
                  inner.y + _cap(mz) * (1.15 + i * 1.62), INK_DIM, 0.78, "l", bw)

    bays = [("INSTRUMENT", "AOM-1"), ("ARCHIVE", sp.archive),
            ("CLASS", sp.cls), ("ORIGIN", sp.origin),
            ("SYMMETRY", sp.symmetry), ("MODE", "LIVE OBSERVATION"),
            ("FIELD", m.behavior), ("SYSTEM BUS", "ONLINE")]
    avail = x1 - x0
    if avail < 240.0:
        bays = bays[:3]
    elif avail < 520.0:
        bays = bays[:5]
    elif avail < 720.0:
        bays = bays[:6]
    n = len(bays)
    cw = avail / n
    size = max(5.5, min(t.micro, inner.h * 0.34))
    for i, (k, v) in enumerate(bays):
        bx = x0 + i * cw
        _show(cr, k, size * 0.88, _W_NORMAL, t.tracking, bx + cw * 0.06,
              inner.y + _cap(size) * 1.18, INK_DIM, 0.85, "l", cw * 0.88)
        _show(cr, v, size, _W_MEDIUM, t.tracking * 0.5, bx + cw * 0.06,
              inner.bottom - _cap(size) * 0.32,
              LIME if v == "ONLINE" else INK_BRIGHT, 0.95, "l", cw * 0.88)
        if i:
            _divider(cr, bx - cw * 0.02, inner.y, inner.bottom, 0.7)

    # engraved division mark on the chassis metal below the rail
    if L.show_chassis and L.chassis.valid and wide:
        ez = max(MIN_TEXT, min(t.micro * 0.80, inner.h * 0.28))
        ey = min(L.chassis.bottom - ez * 0.6, r.bottom + ez * 2.2)
        _show(cr, "OCEANOGRAPHIC RESEARCH DIVISION", ez, _W_NORMAL,
              t.tracking * 1.4, L.chassis.right - L.chassis.w * 0.030,
              ey, (0.20, 0.22, 0.23), 0.95, "r")


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------
def draw_under(cr, L: Layout, m: ConsoleModel, vp_scale: float = 1.0) -> None:
    """Everything BEHIND the organism: the enclosure and the field graticule."""
    _draw_chassis(cr, L)
    _draw_stage_graticule(cr, L, m, vp_scale)


def draw_over(cr, L: Layout, tel: Telemetry, fps: float, frame_ms: float,
              m: ConsoleModel, light: LightField) -> None:
    """Everything that belongs IN FRONT of the organism, then the light pass."""
    light.clear()
    _draw_stage(cr, L, m, light)
    _draw_header(cr, L, m, light)
    _draw_status_rail(cr, L, m)
    _draw_modules(cr, L, tel, fps, frame_ms, m, light)
    _draw_controls(cr, L, m, light)
    _draw_footer(cr, L, m)
    light.paint(cr)
