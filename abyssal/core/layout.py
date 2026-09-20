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
    LayoutState.INSTRUMENT: dict(pad=20.0, head=88.0, row=62.0,
                                 col=(170.0, 330.0), gap=18.0, min_stage=150.0),
    LayoutState.ARCHIVE:    dict(pad=30.0, head=98.0, row=74.0,
                                 col=(230.0, 460.0), gap=26.0, min_stage=200.0),
}

# Above this body aspect the readouts move to a side column, so the organism
# keeps a stage close to square instead of a wide letterboxed strip. This is
# what makes a short wide Hyprland tile look deliberate rather than starved.
SIDE_COLUMN_ASPECT = 1.45

# The selector bank is sized from the STAGE width, not the window width, so it
# stays proportional to the specimen it switches. Clamped at both ends: too
# short and the engraved labels stop being legible, too tall and a hero control
# starts eating the organism.
_BANK_ASPECT = (48.0 + 5 * 224.0 + 46.0) / 303.0     # tools/build_sprites.py
_BANK_SHARE = 0.66          # of stage width
_BANK_MIN_H = 30.0
_BANK_MAX_H = {"COMPACT": 46.0, "INSTRUMENT": 72.0, "ARCHIVE": 88.0}
_FOOTER_H = {"COMPACT": 0.0, "INSTRUMENT": 36.0, "ARCHIVE": 52.0}

# --- outer chassis ---------------------------------------------------------
# Proportions read off monitor_ref.png (1672x941): the enclosure takes ~3.3% of
# the width per side and ~2% of the height, leaving a 1560x900 inner face. The
# frame is what makes the UI read as ONE machine rather than floating panels,
# so it is only dropped when the window is too small to spend the pixels.
_CHASSIS_MIN_W = 620.0
_CHASSIS_MIN_H = 460.0
_CHASSIS_MX = 0.026        # of width, per side
_CHASSIS_MY = 0.030        # of height, per side
_CHASSIS_WALL = 0.020      # frame thickness as a share of min(w, h)

# Reference body split: stage 60.6%, rack 39.4% of the inner width.
_STAGE_SHARE = 0.605
_RACK_MIN = 230.0
_RACK_MAX = 560.0


def bank_height(state: "LayoutState", stage_w: float, avail_h: float) -> float:
    """Pure: how tall the control strip should be. Shared by layout and QA."""
    want = (stage_w * _BANK_SHARE) / _BANK_ASPECT
    hi = min(_BANK_MAX_H[state.value], avail_h)
    if hi < _BANK_MIN_H:
        return 0.0
    return max(_BANK_MIN_H, min(want, hi))


def resolve(width: float, height: float) -> Layout:
    w = max(float(width), 1.0)
    h = max(float(height), 1.0)
    state = classify(w, h)
    m = _METRICS[state]
    t = {LayoutState.COMPACT: _T_COMPACT,
         LayoutState.INSTRUMENT: _T_INSTRUMENT,
         LayoutState.ARCHIVE: _T_ARCHIVE}[state]

    # --- outer enclosure ---------------------------------------------------
    show_chassis = w >= _CHASSIS_MIN_W and h >= _CHASSIS_MIN_H
    if show_chassis:
        mx, my = w * _CHASSIS_MX, h * _CHASSIS_MY
        chassis = Rect(mx, my, w - 2 * mx, h - 2 * my)
        wall = max(10.0, min(chassis.w, chassis.h) * _CHASSIS_WALL)
        content = chassis.inset(wall, wall)
        pad = 0.0
    else:
        chassis = Rect(0.0, 0.0, 0.0, 0.0)
        pad = m["pad"]
        content = Rect(pad, pad, max(0.0, w - 2 * pad), max(0.0, h - 2 * pad))

    gap = m["gap"]

    # --- header, with its secondary status rail ----------------------------
    head_h = min(m["head"], content.h * 0.22)
    header = Rect(content.x, content.y, content.w, head_h)
    status = Rect(0.0, 0.0, 0.0, 0.0)
    show_status = False
    if show_chassis and head_h >= 58.0 and content.w > 620.0:
        # The reference splits its header into a tall title block and a short
        # status rail beneath it. Both are compartmented; see ui.console.
        srow = min(head_h * 0.34, 26.0)
        header = Rect(header.x, header.y, header.w, head_h - srow)
        status = Rect(content.x, header.bottom + 2.0, content.w, srow - 2.0)
        show_status = True

    body_top = (status.bottom if show_status else header.bottom) + gap * 0.55
    body = Rect(content.x, body_top, content.w,
                max(0.0, content.bottom - body_top))

    # --- archive rail ------------------------------------------------------
    foot_h = _FOOTER_H[state.value]
    if foot_h > 0.0 and body.h > foot_h * 5.0:
        footer = Rect(body.x, body.bottom - foot_h, body.w, foot_h)
        body = Rect(body.x, body.y, body.w, body.h - foot_h - gap * 0.45)
        show_footer = True
    else:
        footer = Rect(0.0, 0.0, 0.0, 0.0)
        show_footer = False

    # --- stage / telemetry split -------------------------------------------
    aspect = body.w / max(body.h, 1.0)
    col_lo, col_hi = m["col"]
    use_column = False
    col_w = 0.0
    if aspect >= SIDE_COLUMN_ASPECT and body.w > col_lo + m["min_stage"] + gap:
        if show_chassis:
            # Follow the reference proportions rather than a fixed width, so
            # the rack grows with the machine instead of stranding the stage
            # in a letterbox.
            col_w = min(_RACK_MAX, max(_RACK_MIN,
                                       body.w * (1.0 - _STAGE_SHARE) - gap))
        else:
            col_w = min(col_hi, max(col_lo, body.w * 0.33))
        if body.w - col_w - gap >= m["min_stage"]:
            use_column = True

    if use_column:
        readout = Rect(body.right - col_w, body.y, col_w, body.h)
        stage = Rect(body.x, body.y, body.w - col_w - gap, body.h)
    else:
        row_h = min(m["row"], body.h * 0.26)
        readout = Rect(body.x, body.bottom - row_h, body.w, row_h)
        stage = Rect(body.x, body.y, body.w, max(0.0, body.h - row_h - gap * 0.6))

    # --- selector bank, carved from the stage column -----------------------
    bank_h = bank_height(state, stage.w, stage.h * 0.24)
    if bank_h > 0.0 and stage.h - bank_h - gap * 0.5 >= m["min_stage"]:
        controls = Rect(stage.x, stage.bottom - bank_h, stage.w, bank_h)
        stage = Rect(stage.x, stage.y, stage.w,
                     max(0.0, stage.h - bank_h - gap * 0.5))
        show_controls = True
    else:
        controls = Rect(0.0, 0.0, 0.0, 0.0)
        show_controls = False

    # ARCHIVE keeps a quiet metadata block at the top of its side column.
    show_meta = state is LayoutState.ARCHIVE and use_column and readout.h > 300.0
    meta = Rect(0.0, 0.0, 0.0, 0.0)

    return Layout(
        state=state, width=w, height=h, pad=pad,
        header=header, stage=stage, readout=readout, meta=meta,
        controls=controls, footer=footer, chassis=chassis, status=status,
        type=t,
        show_subtitle=(state is not LayoutState.COMPACT and content.w > 360.0),
        show_meta=False,
        show_controls=show_controls,
        show_footer=show_footer,
        show_chassis=show_chassis,
        show_status=show_status,
        readout_vertical=use_column,
    )
