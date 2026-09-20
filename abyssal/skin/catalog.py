"""The hardware registry: which sprite, and how it is allowed to scale.

SAFE INSETS
-----------
Each NineSlice below carries the four borders that must NOT stretch. They were
measured off the 2K masters (metal thickness around the recess) and then padded
so a rounded inner corner always falls inside a fixed corner tile rather than
being smeared by an edge band.

WHAT IS AND IS NOT 9-SLICEABLE
------------------------------
9-sliceable -- a single frame around a single recess:
    observation bezel, segment housings, graph well, meter trough, aux frame,
    generic plate.

NOT 9-sliceable -- these are ARRANGEMENTS, not frames, and stretching them
would smear their internal structure:
    chassis      (viewport hole left, solid rack right: no symmetric border)
    header rail  (a row of compartments; stretching multiplies nothing)
    footer rail  (ditto)
    telemetry rack (four discrete bays)
    module shell (three fixed recesses)
    selector bank (five discrete key wells)

Those are ASSEMBLED at draw time from the primitives above plus the state
sprites, which is precisely what the component library was generated for. That
is why this pass needed no extra corner/edge art.
"""

from __future__ import annotations

from .surface import NineSlice

# --- 9-sliceable frames ---------------------------------------------------
OBSERVATION_BEZEL = NineSlice("frame/observation_bezel", 150, 140, 146, 144,
                              96, 85, 100, 93)
SEGMENT_HOUSING = NineSlice("frame/segment_housing", 120, 70, 120, 95,
                            87, 48, 87, 69)
SEGMENT_HOUSING_WIDE = NineSlice("frame/segment_housing_wide", 74, 47, 74, 43,
                                 55, 35, 55, 32)
# pad_b is the BEVEL only, not the sprite's caption ledge: the caption is drawn
# below the well, so reserving the ledge internally collapsed the plot area to
# a few pixels once the border scale kicked in.
GRAPH_WELL = NineSlice("frame/graph_well", 72, 44, 72, 110,
                       51, 30, 52, 44)
METER_TROUGH = NineSlice("frame/meter_trough", 53, 31, 53, 42,
                         39, 23, 39, 31)
AUX_FRAME = NineSlice("frame/aux_frame", 74, 50, 74, 111,
                      55, 37, 55, 82)

# Generic console metal. The recessed plaque has no end screws and rounded
# corners, so it tiles to any panel size without repeating a fastener.
# Insets deliberately small: this panel is used for rails as short as 26px,
# and a border wider than half the panel leaves no interior at all.
# This plate is the generic rail: it is asked for panels from a 26px status
# strip up to a full-width header, far from its own 640x186. Its insets are
# therefore kept small, so a short rail keeps a usable interior instead of
# being all bevel.
# Content pads are the RECESSED FIELD, not the first bevel step: the plate has
# an outer bevel, a flat land and an inner step before the field begins.
#
# NOTE this plate is only used where there is room for its ~18px bevel - the
# header and the module bodies. Thin rails (footer, condensed telemetry) draw a
# procedural inset strip instead; see ui.console._rail. A bevel that cannot
# scale is the wrong tool for a 36px bar.
PLATE = NineSlice("frame/plate_recessed", 24, 18, 24, 18,
                  32, 30, 26, 28)

# --- state sprite families -------------------------------------------------
LAMP_STATES = ("off", "standby", "active", "nominal", "warning", "critical")


def lamp(size: str, state: str) -> str:
    """size: 'small' | 'primary'; state: one of LAMP_STATES."""
    if state not in LAMP_STATES:
        state = "off"
    return f"lamp/{size}_{state}"


SELECTOR_STATES = ("idle", "focus", "pressed", "latched", "disabled")


def selector_cell(state: str) -> str:
    if state not in SELECTOR_STATES:
        state = "idle"
    return f"selector/cell_{state}"


SELECTOR_CAP_L = "selector/cap_left"
SELECTOR_CAP_R = "selector/cap_right"

MODE_STATES = ("inactive", "armed", "active", "error")


def mode_key(state: str) -> str:
    if state not in MODE_STATES:
        state = "inactive"
    return f"mode/{state}"


CYCLE_STATES = ("neutral", "prev", "next")


def cycle_key(state: str) -> str:
    if state not in CYCLE_STATES:
        state = "neutral"
    return f"cycle/{state}"


# Natural aspect of one selector cell, used by layout to size the bank without
# loading pixels. Kept in sync with tools/build_sprites.py (224 x 303).
SELECTOR_CELL_ASPECT = 224.0 / 303.0
SELECTOR_CAP_ASPECT = 48.0 / 303.0
