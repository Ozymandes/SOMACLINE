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
from collections import deque
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
from ..skin.surface import draw_nine, draw_sprite, draw_sprite_fit
from . import segment as SEG
from . import selector as SEL
from .chrome import _cap, _show, _text_w, _W_MEDIUM, _W_NORMAL

TAU = math.tau


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
    disabled: frozenset[int] = frozenset()
    traces: dict[str, Trace] = field(default_factory=dict)
    switches: int = 0

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
_MODE_GAP = 0.055        # of bank width


def control_geometry(L: Layout) -> tuple[SEL.BankGeometry, Rect]:
    """(selector bank, mode key rect). Both may be invalid/empty."""
    if not L.show_controls or not L.controls.valid:
        return (SEL.layout(Rect(0, 0, 0, 0)), Rect(0, 0, 0, 0))

    box = L.controls
    mode_w = box.h * _MODE_ASPECT
    gap = box.h * _MODE_GAP
    # Reserve the mode key first, then let the bank fill what is left. The bank
    # centres itself inside that, so the pair always reads as one cluster.
    bank_box = Rect(box.x, box.y, max(0.0, box.w - mode_w - gap), box.h)
    geo = SEL.layout(bank_box, n=len(CATALOGUE))
    mode = Rect(geo.x + geo.w + gap, box.y + (box.h - box.h) * 0.5,
                mode_w, box.h)
    # Keep the cluster centred as a whole.
    group_w = geo.w + gap + mode_w
    shift = (box.w - group_w) * 0.5 - (geo.x - box.x)
    if abs(shift) > 0.5:
        geo = SEL.BankGeometry(geo.x + shift, geo.y, geo.w, geo.h,
                               geo.cap_l, geo.cap_r, geo.cell_w, geo.n)
        mode = Rect(mode.x + shift, mode.y, mode.w, mode.h)
    return (geo, mode)


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


def _polar_grid(cr, r: Rect, alpha: float = 1.0) -> None:
    """Observation graticule. Drawn in code, never baked into the glass."""
    if r.w < 60.0 or r.h < 60.0:
        return
    cx, cy = r.cx, r.cy
    rad = min(r.w, r.h) * 0.46
    cr.save()
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.clip()
    cr.set_line_width(1.0)
    for k in (0.25, 0.5, 0.75, 1.0):
        cr.set_source_rgba(*rgba(RULE, 0.55 * alpha))
        cr.save()
        cr.set_dash([2.0, 5.0])
        cr.arc(cx, cy, rad * k, 0.0, TAU)
        cr.stroke()
        cr.restore()
    cr.set_source_rgba(*rgba(RULE, 0.75 * alpha))
    cr.move_to(r.x + r.w * 0.06, cy)
    cr.line_to(r.right - r.w * 0.06, cy)
    cr.move_to(cx, r.y + r.h * 0.06)
    cr.line_to(cx, r.bottom - r.h * 0.06)
    cr.stroke()
    # cardinal ticks
    t = max(3.0, rad * 0.03)
    for ang in (0.0, TAU * 0.25, TAU * 0.5, TAU * 0.75):
        px, py = cx + math.cos(ang) * rad, cy + math.sin(ang) * rad
        cr.move_to(px - t, py)
        cr.line_to(px + t, py)
        cr.move_to(px, py - t)
        cr.line_to(px, py + t)
    cr.stroke()
    cr.restore()


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
    # body
    g = cairo.LinearGradient(r.x, r.y, r.x, r.bottom)
    g.add_color_stop_rgba(0.0, 0.085, 0.095, 0.105, 1.0)
    g.add_color_stop_rgba(0.55, 0.055, 0.062, 0.070, 1.0)
    g.add_color_stop_rgba(1.0, 0.075, 0.083, 0.092, 1.0)
    cr.set_source(g)
    cr.rectangle(r.x, r.y, r.w, r.h)
    cr.fill()
    # top shadow / bottom light-catch: the two cues that read as "cut in"
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
    r = L.header
    if not r.valid:
        return
    # Below the plaque's own bevel depth it leaves almost no interior, so a
    # short header uses the procedural inset strip instead of a miniature
    # plaque with no room for a name.
    if r.h >= _PLAQUE_MIN_H:
        draw_nine(cr, C.PLATE, r.x, r.y, r.w, r.h)
        cx, cy, cw, ch = C.PLATE.content(r.x, r.y, r.w, r.h)
        inner = Rect(cx, cy, cw, ch)
    else:
        inner = _rail(cr, r)
        ch = inner.h
    t = L.type
    sp = m.species

    pad = max(6.0, inner.h * 0.10)
    left = Rect(inner.x + pad, inner.y, inner.w * 0.46, inner.h)

    # Two stacked lines, sized to fit the interior rather than assuming it.
    title_sz = min(t.title, inner.h * 0.30)
    name_sz = min(t.specimen, inner.h * 0.48)
    stack = _cap(title_sz) + _cap(name_sz) * 1.32
    top = left.y + max(0.0, (inner.h - stack) * 0.5)
    _show(cr, "ABYSSAL ORGANISM MONITOR", title_sz, _W_NORMAL, t.tracking,
          left.x, top + _cap(title_sz), INK, 0.88, "l", left.w)
    _show(cr, sp.name, name_sz, _W_MEDIUM, t.tracking * 0.7,
          left.x, top + _cap(title_sz) + _cap(name_sz) * 1.28,
          INK_BRIGHT, 1.0, "l", left.w)

    if L.show_subtitle and inner.w > 420.0:
        mid = Rect(inner.x + inner.w * 0.50, inner.y, inner.w * 0.26, inner.h)
        _show(cr, sp.epithet, t.subtitle, _W_NORMAL, t.tracking,
              mid.cx, mid.cy + _cap(t.subtitle) * 0.5, INK, 0.85, "c", mid.w)

    # LIVE annunciator + clock, right end
    if inner.w > 280.0:
        lamp_h = min(inner.h * 0.42, 22.0)
        lx = inner.right - pad - inner.w * 0.20
        ly = inner.y + inner.h * 0.34
        draw_sprite_fit(cr, C.lamp("small", "nominal"), lx, ly, lamp_h)
        light.add(lx, ly, lamp_h * 1.9, L_CHART, 0.16)
        _show(cr, "LIVE", t.label, _W_MEDIUM, t.tracking,
              lx + lamp_h * 0.85, ly + _cap(t.label) * 0.5, LIME, 0.95, "l")
        clock = time.strftime("%H:%M:%S")
        ch_h = min(inner.h * 0.40, 20.0)
        SEG.draw_right(cr, clock, inner.right - pad,
                       inner.y + inner.h * 0.55, ch_h, SEG.CYAN)


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


def _draw_stage_graticule(cr, L: Layout, m: ConsoleModel) -> None:
    """Grid, ticks and field annotations. Drawn UNDER the organism."""
    glass = stage_content(L)
    if not glass.valid:
        return
    _polar_grid(cr, glass)
    if glass.w < 430.0 or glass.h < 230.0:
        return
    t = L.type
    sp = m.species
    pad = max(8.0, glass.w * 0.022)
    rows = (("MODE", "LIVE OBSERVATION"), ("SYMMETRY", sp.symmetry),
            ("LOBES", str(sp.lobes)), ("ARCHIVE", sp.archive))
    y = glass.y + pad + _cap(t.micro)
    for k, v in rows:
        _show(cr, k, t.micro, _W_NORMAL, t.tracking, glass.x + pad, y,
              INK_DIM, 0.75, "l", glass.w * 0.18)
        _show(cr, v, t.micro, _W_NORMAL, t.tracking * 0.6,
              glass.x + pad + glass.w * 0.20, y, INK, 0.85, "l",
              glass.w * 0.34)
        y += _cap(t.micro) * 2.0
    # cardinal letters
    for lab, ax, ay in (("Y+", glass.cx, glass.y + pad + _cap(t.micro)),
                        ("Y-", glass.cx, glass.bottom - pad),
                        ("X-", glass.x + pad, glass.cy),
                        ("X+", glass.right - pad, glass.cy)):
        _show(cr, lab, t.micro, _W_NORMAL, t.tracking, ax, ay, INK_DIM, 0.6, "c")


# --------------------------------------------------------------------------
# telemetry modules
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Channel:
    key: str
    title: str
    sub: str
    unit: str


_CHANNELS = (
    _Channel("cpu", "PROCESSOR", "BIOCOMPUTATIONAL CORE", "%"),
    _Channel("thermal", "THERMAL", "ENVIRONMENT & METABOLIC", "C"),
    _Channel("memory", "MEMORY", "FIELD DATA & STATE", "G"),
    _Channel("frame", "FRAME / RENDER", "VISUALISATION PIPELINE", "FPS"),
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


def _draw_module(cr, r: Rect, ch: _Channel, idx: int, L: Layout,
                 tel: Telemetry, fps: float, frame_ms: float,
                 m: ConsoleModel, light: LightField) -> None:
    """One subsystem module, assembled from primitives at this exact size."""
    if r.w < 60.0 or r.h < 28.0:
        return
    draw_nine(cr, C.PLATE, r.x, r.y, r.w, r.h)
    ix, iy, iw, ih = C.PLATE.content(r.x, r.y, r.w, r.h)
    inner = Rect(ix, iy, iw, ih)
    t = L.type

    text, level, style, state, lamp_state = _channel_values(
        ch.key, tel, fps, frame_ms)
    m.trace(ch.key).push(level)

    rich = inner.h > 62.0 and inner.w > 190.0
    head_h = min(inner.h * (0.26 if rich else 0.40), _cap(t.label) * 2.4)

    # --- header rail: index plaque, title, annunciators -------------------
    hx = inner.x
    plaq_w = min(inner.w * 0.10, head_h * 1.5)
    if rich and plaq_w > 12.0:
        pr = Rect(hx, inner.y, plaq_w, head_h)
        draw_nine(cr, C.PLATE, pr.x, pr.y, pr.w, pr.h)
        _engrave(cr, f"0{idx + 1}", pr, min(t.label, pr.h * 0.62),
                 t.tracking * 0.4, INK_BRIGHT, "c")
        hx += plaq_w + inner.w * 0.02

    _show(cr, ch.title, t.label, _W_MEDIUM, t.tracking, hx,
          inner.y + head_h * 0.5 + _cap(t.label) * 0.5, INK_BRIGHT, 0.96, "l",
          inner.w * 0.5)

    if rich:
        lamp_h = min(head_h * 0.62, 14.0)
        lx = inner.right - lamp_h * 0.8
        for k in range(4):
            st = lamp_state if k == 0 else ("nominal" if k < 3 else "off")
            draw_sprite_fit(cr, C.lamp("small", st), lx, inner.y + head_h * 0.5,
                            lamp_h)
            lx -= lamp_h * 1.35

    body = Rect(inner.x, inner.y + head_h, inner.w,
                max(0.0, inner.h - head_h))
    if body.h < 14.0:
        return

    # --- body: graph well | segment readout | meter -----------------------
    gap = max(3.0, body.w * 0.015)
    if rich:
        well_w = body.w * 0.30
        val_w = body.w * 0.40
        well = Rect(body.x, body.y, well_w, body.h * 0.74)
        draw_nine(cr, C.GRAPH_WELL, well.x, well.y, well.w, well.h)
        gx, gy, gw, gh = C.GRAPH_WELL.content(well.x, well.y, well.w, well.h)
        grect = Rect(gx, gy, gw, gh)
        _sparkline(cr, grect, m.trace(ch.key).values, style.lit)
        light.glow(grect, style.lit, 0.05, spread=0.45)
        vx = body.x + well_w + gap
    else:
        val_w = body.w * 0.62
        vx = body.x

    # primary readout in its housing
    hr = Rect(vx, body.y, val_w, body.h * 0.74)
    if hr.w > 40.0 and hr.h > 16.0:
        draw_nine(cr, C.SEGMENT_HOUSING, hr.x, hr.y, hr.w, hr.h)
        sx, sy, sw, sh = C.SEGMENT_HOUSING.content(hr.x, hr.y, hr.w, hr.h)
        unit_sz = min(t.micro, sh * 0.30)
        uw = _text_w(ch.unit, unit_sz, _W_NORMAL, t.tracking) + sw * 0.04
        avail = max(8.0, sw - uw)
        dh = min(sh * 0.94, SEG.fit_height(text, avail, sh * 0.94, style))
        if dh > 5.0:
            tw = SEG.measure(text, dh, style)
            tx = sx + max(0.0, (avail - tw) * 0.5)
            SEG.draw(cr, text, tx, sy + (sh - dh) * 0.5, dh, style)
            _show(cr, ch.unit, unit_sz, _W_NORMAL, t.tracking,
                  sx + sw, sy + sh * 0.74, style.lit, 0.85, "r")
        light.glow(Rect(sx, sy, sw, sh), style.lit, 0.09, spread=0.5)

    # status column
    if rich:
        stx = body.x + well_w + gap + val_w + gap
        stw = max(0.0, body.right - stx)
        if stw > 40.0:
            _show(cr, state, t.micro, _W_MEDIUM, t.tracking, stx,
                  body.y + _cap(t.micro) * 1.4, style.lit, 0.95, "l", stw)
            _show(cr, ch.sub, t.micro, _W_NORMAL, t.tracking, stx,
                  body.y + _cap(t.micro) * 3.2, INK_DIM, 0.70, "l", stw)

    # meter trough along the bottom
    mt = Rect(body.x, body.bottom - body.h * 0.22, body.w, body.h * 0.20)
    if mt.w > 30.0 and mt.h > 5.0:
        draw_nine(cr, C.METER_TROUGH, mt.x, mt.y, mt.w, mt.h)
        bx, by, bw, bh = C.METER_TROUGH.content(mt.x, mt.y, mt.w, mt.h)
        _bargraph(cr, Rect(bx, by, bw, bh), level, style.lit)


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
    SEL.draw(cr, geo, m.active, m.pressed, m.focus, m.disabled)

    t = L.type
    label_size = max(5.5, min(t.micro, geo.h * 0.115))
    for i, sp in enumerate(CATALOGUE):
        lr = geo.label_rect(i)
        dim = i in m.disabled
        _engrave(cr, sp.short, lr, label_size, t.tracking * 0.35,
                 INK_BRIGHT if i == m.active else INK,
                 "c", 0.35 if dim else (1.0 if i == m.active else 0.80))

        fr = geo.face_rect(i)
        ar = Rect(fr.x, fr.y + fr.h * 0.10, fr.w, fr.h * 0.5)
        _engrave(cr, sp.archive, ar, max(5.0, label_size * 0.86),
                 t.tracking * 0.3, INK_DIM,
                 "c", 0.30 if dim else 0.70)

        # a latched key throws a little light onto the fascia around it
        if i == m.active and not dim:
            lx, ly, lrad = SEL.lamp_point(geo, i)
            light.add(lx, ly, max(8.0, lrad * 7.0), L_CHART, 0.20)

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
def _draw_footer(cr, L: Layout, m: ConsoleModel) -> None:
    r = L.footer
    if not L.show_footer or not r.valid:
        return
    inner = _rail(cr, r)
    sp = m.species
    t = L.type
    bays = (("INSTRUMENT", "AOM-1"), ("ARCHIVE", sp.archive),
            ("CLASS", sp.cls), ("ORIGIN", sp.origin),
            ("SYMMETRY", sp.symmetry), ("NOTES", sp.notes))
    n = len(bays)
    if inner.w < 240.0:
        bays = bays[:3]
        n = 3
    cw = inner.w / n
    size = max(6.0, min(t.micro, inner.h * 0.46))
    for i, (k, v) in enumerate(bays):
        bx = inner.x + i * cw
        _show(cr, k, size * 0.86, _W_NORMAL, t.tracking, bx + cw * 0.05,
              inner.y + _cap(size) * 1.12, INK_DIM, 0.88, "l", cw * 0.90)
        _show(cr, v, size, _W_MEDIUM, t.tracking * 0.5, bx + cw * 0.05,
              inner.y + inner.h - _cap(size) * 0.10, INK_BRIGHT, 0.95,
              "l", cw * 0.90)
        if i:
            cr.set_source_rgba(*rgba(RULE, 0.5))
            cr.rectangle(bx, inner.y + inner.h * 0.15, 1.0, inner.h * 0.70)
            cr.fill()


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------
def draw_under(cr, L: Layout, m: ConsoleModel) -> None:
    """Everything that belongs BEHIND the organism."""
    _draw_stage_graticule(cr, L, m)


def draw_over(cr, L: Layout, tel: Telemetry, fps: float, frame_ms: float,
              m: ConsoleModel, light: LightField) -> None:
    """Everything that belongs IN FRONT of the organism, then the light pass."""
    light.clear()
    _draw_stage(cr, L, m, light)
    _draw_header(cr, L, m, light)
    _draw_modules(cr, L, tel, fps, frame_ms, m, light)
    _draw_controls(cr, L, m, light)
    _draw_footer(cr, L, m)
    light.paint(cr)
