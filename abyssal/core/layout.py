"""Responsive layout STATES (not a single scaled poster).

CONTRACT
--------
`resolve(w, h)` is a pure function of the widget's logical size. It returns an
immutable Layout: a set of pixel rects plus a fixed type scale for that state.
It allocates nothing, touches no global state, and is safe to call every frame.

Font sizes are chosen PER STATE, not scaled proportionally with the window.
Within a state they are clamped only enough to survive extreme aspect ratios.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LayoutState(Enum):
    COMPACT = "COMPACT"
    INSTRUMENT = "INSTRUMENT"
    ARCHIVE = "ARCHIVE"


#: Aspect range in which the six-module machine is used at all.
_SIX_MAX_ASPECT = 2.2
_SIX_MIN_ASPECT = 0.72

# Breakpoints on logical widget width, in px.
BP_INSTRUMENT = 700
BP_ARCHIVE = 1100


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    def inset(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy,
                    max(0.0, self.w - 2 * dx), max(0.0, self.h - 2 * dy))

    @property
    def valid(self) -> bool:
        return self.w > 1.0 and self.h > 1.0


@dataclass(frozen=True, slots=True)
class TypeScale:
    """Fixed sizes for one layout state, in logical px."""
    title: float
    specimen: float
    subtitle: float
    label: float
    value: float
    micro: float
    tracking: float          # extra letter-spacing for labels, px


@dataclass(frozen=True, slots=True)
class Layout:
    state: LayoutState
    width: float
    height: float
    pad: float
    header: Rect             # title block
    stage: Rect              # organism lives here, and ONLY here
    readout: Rect            # telemetry row / strip
    meta: Rect               # extra metadata column (ARCHIVE only; may be invalid)
    controls: Rect           # specimen selector bank + mode key (may be invalid)
    footer: Rect             # archive / status rail (may be invalid)
    chassis: Rect            # outer machine enclosure (may be invalid)
    status: Rect             # header's secondary status rail (may be invalid)
    type: TypeScale
    show_subtitle: bool
    show_meta: bool
    show_controls: bool
    show_footer: bool
    show_chassis: bool
    show_status: bool
    readout_vertical: bool   # readouts stacked in a column (ARCHIVE side panel)


_T_COMPACT = TypeScale(title=9.5, specimen=13.0, subtitle=8.0, label=8.0,
                       value=15.0, micro=7.5, tracking=1.1)
_T_INSTRUMENT = TypeScale(title=10.5, specimen=17.0, subtitle=9.5, label=9.0,
                          value=21.0, micro=8.5, tracking=1.5)
_T_ARCHIVE = TypeScale(title=12.0, specimen=22.0, subtitle=11.0, label=10.0,
                       value=26.0, micro=9.5, tracking=2.0)


def classify(width: float, height: float) -> LayoutState:
    """Pick a state from the *effective* width.

    Height participates: a very short window is demoted, because an INSTRUMENT
    layout with no vertical room would push the organism to nothing.
    """
    effective = width
    # The six-module machine is a ~4:3 object. Far outside its proportions
    # it could only be letterboxed or smeared, so those windows get the
    # portable configuration instead.
    aspect = width / max(height, 1.0)
    if aspect > _SIX_MAX_ASPECT or aspect < _SIX_MIN_ASPECT:
        return LayoutState.COMPACT
    if height < 420:
        effective = min(effective, BP_INSTRUMENT - 1)
    if height < 560:
        effective = min(effective, BP_ARCHIVE - 1)
    if effective < BP_INSTRUMENT:
        return LayoutState.COMPACT
    if effective < BP_ARCHIVE:
        return LayoutState.INSTRUMENT
    return LayoutState.ARCHIVE


# --- the six-module machine -------------------------------------------------
# Module rectangles read off references/Abbysal_Final.png (1448x1086), in that
# image's pixels. The six-module arrangement is laid out FROM these numbers:
# vertical positions follow the chassis height, horizontal ones the chassis
# width, and the telemetry rack keeps its manufactured proportions (it is
# sized from the height scale) so its bays never smear. The observation
# column absorbs whatever width is left - that is the one region of the
# machine that is a frame rather than an arrangement.
REF_CHASSIS = (40.0, 30.0, 1368.0, 1028.0)
REF_HEADER = (55.0, 55.0, 1340.0, 145.0)
REF_STAGE = (52.0, 222.0, 770.0, 568.0)
REF_SELECTOR = (52.0, 800.0, 770.0, 152.0)
REF_RACK = (838.0, 222.0, 534.0, 730.0)
REF_FOOTER = (48.0, 962.0, 1350.0, 78.0)

#: Natural width/height of the selector module (1400x264). Layout may not
#: import skin/, so the aspect is a constant; qa/console_gates.py GATE 6
#: asserts it equals skin.modules.SELECTOR.aspect.
_BANK_ASPECT = 1400.0 / 264.0
#: The widest the observation window may become before the rack takes the
#: extra width instead (ARCHIVE on a 16:9 panel).
_STAGE_MAX_ASPECT = 1.62
#: The rack may never take more than this share of the chassis width.
_RACK_MAX_SHARE = 0.44
_RACK_MIN_SHARE = 0.30

# --- outer chassis ---------------------------------------------------------
# The enclosure's margin inside the window matches the reference's: ~2.8% of
# each dimension. The frame is what makes the UI read as ONE machine, so it is
# only dropped when the window is too small to spend the pixels.
_CHASSIS_MIN_W = 420.0
_CHASSIS_MIN_H = 330.0
_CHASSIS_MX = 40.0 / 1448.0
_CHASSIS_MY = 30.0 / 1086.0

#: COMPACT arrangement: header rail and telemetry strip heights, in px.
_COMPACT_HEAD = 38.0
_COMPACT_STRIP = 52.0


def bank_height(state: "LayoutState", stage_w: float, avail_h: float) -> float:
    """Pure: how tall the selector module is for a given column width."""
    if state is LayoutState.COMPACT:
        return 0.0
    return max(0.0, min(stage_w / _BANK_ASPECT, avail_h))


def resolve(width: float, height: float) -> Layout:
    w = max(float(width), 1.0)
    h = max(float(height), 1.0)
    state = classify(w, h)
    t = {LayoutState.COMPACT: _T_COMPACT,
         LayoutState.INSTRUMENT: _T_INSTRUMENT,
         LayoutState.ARCHIVE: _T_ARCHIVE}[state]
    if state is LayoutState.COMPACT:
        return _compact(w, h, t)
    return _six_module(w, h, state, t)


_NONE = Rect(0.0, 0.0, 0.0, 0.0)


#: Type measured off references/Abbysal_Final.png at its own scale (s = 1),
#: in px: header title, specimen name, epithet base, rack/section label,
#: microcopy, tracking. The six-module states scale these with the machine,
#: because the machine's bays scale with it; every call site still clamps to
#: its own bay, so type can never outgrow the recess it sits in.
_REF_TYPE = dict(title=18.0, specimen=26.0, subtitle=16.0, label=14.5,
                 value=30.0, micro=12.5, tracking=2.6)


def _machine_type(s: float, base: TypeScale) -> TypeScale:
    k = max(0.5, min(1.9, s))
    r = _REF_TYPE
    return TypeScale(title=r["title"] * k, specimen=r["specimen"] * k,
                     subtitle=r["subtitle"] * k, label=r["label"] * k,
                     value=r["value"] * k, micro=max(7.0, r["micro"] * k),
                     tracking=max(1.0, r["tracking"] * k))


def _six_module(w: float, h: float, state: LayoutState, t: TypeScale) -> Layout:
    """INSTRUMENT / ARCHIVE: the full six-module machine."""
    C = Rect(w * _CHASSIS_MX, h * _CHASSIS_MY,
             w * (1.0 - 2.0 * _CHASSIS_MX), h * (1.0 - 2.0 * _CHASSIS_MY))
    rx, ry, rw, rh = REF_CHASSIS
    sx, sy = C.w / rw, C.h / rh
    s = min(sx, sy)
    t = _machine_type(s, t)

    def X(v: float) -> float:
        return C.x + (v - rx) * sx

    def Y(v: float) -> float:
        return C.y + (v - ry) * sy

    hx, hy, hw, hh = REF_HEADER
    header = Rect(X(hx), Y(hy), hw * sx, hh * sy)
    fx, fy, fw, fh = REF_FOOTER
    footer = Rect(X(fx), Y(fy), fw * sx, fh * sy)

    body_top = Y(REF_STAGE[1])
    body_bot = Y(REF_RACK[1] + REF_RACK[3])
    body_h = body_bot - body_top

    # Rack: manufactured proportions from the HEIGHT scale, then the right
    # margin exactly as the reference seats it (36 px of 1368).
    margin_r = (rx + rw - (REF_RACK[0] + REF_RACK[2])) * s
    left = X(REF_STAGE[0])
    gap = (REF_RACK[0] - (REF_STAGE[0] + REF_STAGE[2])) * s
    rack_w = REF_RACK[2] * sy
    col_w = C.right - margin_r - rack_w - gap - left
    # the observation window may not become a letterbox: past this aspect the
    # rack takes the width instead
    stage_h_est = body_h * (REF_STAGE[3] / (REF_RACK[3]))
    if col_w > stage_h_est * _STAGE_MAX_ASPECT:
        col_w = stage_h_est * _STAGE_MAX_ASPECT
    rack_w = C.right - margin_r - gap - left - col_w
    rack_w = max(C.w * _RACK_MIN_SHARE, min(C.w * _RACK_MAX_SHARE, rack_w))
    col_w = C.right - margin_r - rack_w - gap - left
    readout = Rect(C.right - margin_r - rack_w, body_top, rack_w, body_h)

    # Selector: its own aspect, never stretched vertically. The keys grow
    # with the column (up to a limit) rather than the plate smearing.
    sel_gap = (REF_SELECTOR[1] - (REF_STAGE[1] + REF_STAGE[3])) * sy
    sel_h = bank_height(state, col_w, REF_SELECTOR[3] * sy * 1.30)
    controls = Rect(left, body_bot - sel_h, col_w, sel_h)
    stage = Rect(left, body_top, col_w,
                 max(0.0, controls.y - sel_gap - body_top))

    return Layout(
        state=state, width=w, height=h, pad=0.0,
        header=header, stage=stage, readout=readout, meta=_NONE,
        controls=controls, footer=footer, chassis=C, status=_NONE,
        type=t, show_subtitle=True, show_meta=False,
        show_controls=sel_h > 20.0, show_footer=True, show_chassis=True,
        show_status=False, readout_vertical=True)


def _compact(w: float, h: float, t: TypeScale) -> Layout:
    """COMPACT: a portable configuration, not a crushed machine.

    Minimal header, the observation chamber, a clean telemetry strip. There is
    no selector bank here - keys 1-5 and the arrows still switch specimens.
    """
    show_chassis = w >= _CHASSIS_MIN_W and h >= _CHASSIS_MIN_H
    if show_chassis:
        C = Rect(w * _CHASSIS_MX, h * _CHASSIS_MY,
                 w * (1.0 - 2.0 * _CHASSIS_MX), h * (1.0 - 2.0 * _CHASSIS_MY))
        wall = max(8.0, min(C.w, C.h) * 0.030)
        content = C.inset(wall, wall)
    else:
        C = _NONE
        content = Rect(8.0, 8.0, max(0.0, w - 16.0), max(0.0, h - 16.0))
    gap = 6.0
    head_h = min(_COMPACT_HEAD, content.h * 0.16)
    header = Rect(content.x, content.y, content.w, head_h)
    strip_h = min(_COMPACT_STRIP, content.h * 0.20)
    readout = Rect(content.x, content.bottom - strip_h, content.w, strip_h)
    stage = Rect(content.x, header.bottom + gap, content.w,
                 max(0.0, readout.y - gap - header.bottom - gap))
    return Layout(
        state=LayoutState.COMPACT, width=w, height=h, pad=8.0,
        header=header, stage=stage, readout=readout, meta=_NONE,
        controls=_NONE, footer=_NONE, chassis=C, status=_NONE,
        type=t, show_subtitle=False, show_meta=False, show_controls=False,
        show_footer=False, show_chassis=show_chassis, show_status=False,
        readout_vertical=False)
