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


# Per-state arrangement metrics: padding, header height, readout row height,
# readout column width bounds, gap, and the minimum stage edge worth keeping.
_METRICS = {
    LayoutState.COMPACT:    dict(pad=12.0, head=34.0, row=46.0,
                                 col=(112.0, 190.0), gap=8.0, min_stage=90.0),
    LayoutState.INSTRUMENT: dict(pad=20.0, head=56.0, row=62.0,
                                 col=(158.0, 260.0), gap=18.0, min_stage=150.0),
    LayoutState.ARCHIVE:    dict(pad=30.0, head=72.0, row=74.0,
                                 col=(200.0, 300.0), gap=26.0, min_stage=200.0),
}

# Above this body aspect the readouts move to a side column, so the organism
# keeps a stage close to square instead of a wide letterboxed strip. This is
# what makes a short wide Hyprland tile look deliberate rather than starved.
SIDE_COLUMN_ASPECT = 1.45


def resolve(width: float, height: float) -> Layout:
    w = max(float(width), 1.0)
    h = max(float(height), 1.0)
    state = classify(w, h)
    m = _METRICS[state]
    t = {LayoutState.COMPACT: _T_COMPACT,
         LayoutState.INSTRUMENT: _T_INSTRUMENT,
         LayoutState.ARCHIVE: _T_ARCHIVE}[state]

    pad = m["pad"]
    content = Rect(pad, pad, max(0.0, w - 2 * pad), max(0.0, h - 2 * pad))

    head_h = min(m["head"], content.h * 0.22)
    header = Rect(content.x, content.y, content.w, head_h)
    gap = m["gap"]
    body = Rect(content.x, header.bottom + gap * 0.55,
                content.w, max(0.0, content.h - head_h - gap * 0.55))

    aspect = body.w / max(body.h, 1.0)
    col_lo, col_hi = m["col"]
    use_column = False
    col_w = 0.0
    if aspect >= SIDE_COLUMN_ASPECT and body.w > col_lo + m["min_stage"] + gap:
        col_w = min(col_hi, max(col_lo, body.w * 0.30))
        if body.w - col_w - gap >= m["min_stage"]:
            use_column = True

    if use_column:
        readout = Rect(body.right - col_w, body.y, col_w, body.h)
        stage = Rect(body.x, body.y, body.w - col_w - gap, body.h)
    else:
        row_h = min(m["row"], body.h * 0.26)
        readout = Rect(body.x, body.bottom - row_h, body.w, row_h)
        stage = Rect(body.x, body.y, body.w, max(0.0, body.h - row_h - gap * 0.6))

    # ARCHIVE keeps a quiet metadata block at the top of its side column.
    show_meta = state is LayoutState.ARCHIVE and use_column and readout.h > 300.0
    if show_meta:
        meta_h = min(150.0, readout.h * 0.34)
        meta = Rect(readout.x, readout.y, readout.w, meta_h)
        readout = Rect(readout.x, meta.bottom + gap, readout.w,
                       max(0.0, readout.h - meta_h - gap))
    else:
        meta = Rect(0.0, 0.0, 0.0, 0.0)

    return Layout(
        state=state, width=w, height=h, pad=pad,
        header=header, stage=stage, readout=readout, meta=meta, type=t,
        show_subtitle=(state is not LayoutState.COMPACT and content.w > 360.0),
        show_meta=show_meta,
        readout_vertical=use_column,
    )
