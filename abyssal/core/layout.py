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
    type: TypeScale
    show_subtitle: bool
    show_meta: bool
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
    if height < 420:
        effective = min(effective, BP_INSTRUMENT - 1)
    if height < 560:
        effective = min(effective, BP_ARCHIVE - 1)
    if effective < BP_INSTRUMENT:
        return LayoutState.COMPACT
    if effective < BP_ARCHIVE:
        return LayoutState.INSTRUMENT
    return LayoutState.ARCHIVE


def resolve(width: float, height: float) -> Layout:
    w = max(float(width), 1.0)
    h = max(float(height), 1.0)
    state = classify(w, h)

    if state is LayoutState.COMPACT:
        pad = 12.0
        t = _T_COMPACT
        head_h = min(34.0, h * 0.14)
        read_h = min(46.0, h * 0.20)
        header = Rect(pad, pad, w - 2 * pad, head_h)
        readout = Rect(pad, h - pad - read_h, w - 2 * pad, read_h)
        stage = Rect(pad, header.bottom + 4, w - 2 * pad,
                     max(0.0, readout.y - header.bottom - 8))
        meta = Rect(0, 0, 0, 0)
        return Layout(state, w, h, pad, header, stage, readout, meta, t,
                      show_subtitle=False, show_meta=False,
                      readout_vertical=False)

    if state is LayoutState.INSTRUMENT:
        pad = 20.0
        t = _T_INSTRUMENT
        head_h = min(56.0, h * 0.16)
        read_h = min(62.0, h * 0.20)
        header = Rect(pad, pad, w - 2 * pad, head_h)
        readout = Rect(pad, h - pad - read_h, w - 2 * pad, read_h)
        stage = Rect(pad, header.bottom + 10, w - 2 * pad,
                     max(0.0, readout.y - header.bottom - 22))
        meta = Rect(0, 0, 0, 0)
        return Layout(state, w, h, pad, header, stage, readout, meta, t,
                      show_subtitle=True, show_meta=False,
                      readout_vertical=False)

    # ARCHIVE: side instrument column on the right, organism keeps the left.
    pad = 30.0
    t = _T_ARCHIVE
    col_w = min(300.0, max(220.0, w * 0.24))
    head_h = min(72.0, h * 0.14)
    header = Rect(pad, pad, w - 2 * pad - col_w - 28, head_h)
    meta = Rect(w - pad - col_w, pad, col_w, h - 2 * pad)
    readout = Rect(meta.x, meta.y + 108, col_w, meta.h - 108)
    stage = Rect(pad, header.bottom + 14, w - 2 * pad - col_w - 28,
                 max(0.0, h - pad - header.bottom - 14))
    return Layout(state, w, h, pad, header, stage, readout, meta, t,
                  show_subtitle=True, show_meta=True, readout_vertical=True)
