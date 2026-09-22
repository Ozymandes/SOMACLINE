"""Cairo rendering of the specimen. The only module that turns world -> pixels.

NO cairo transform is ever set. Every coordinate is pushed through
Viewport.px() / Viewport.length(), which are uniform by construction, so a
world circle is a screen circle at any window size or aspect ratio. Nothing
here mutates the organism.

WHAT IT DRAWS
-------------
An accumulating point field, not paths. The specimens are point clouds of ten
to forty thousand samples composited additively at a low alpha - the density
IS the creature - so they are counted into a NumPy buffer and blitted as one
surface. `organism/pointfield.py` carries that machinery and the reasoning.

COLOUR
------
Two ramps, both from the console's own palette:

    density   deep-field blue where the cloud is thin, core cyan where the
              equation crowds - so the creature's internal structure comes
              from the maths rather than from a shading trick
    pulse     the propagating body pulse (mathforms.py) carries its own
              colour, per point, through PULSE_PALETTE: abyssal blue at rest,
              bio-green in healthy operation, amber -> orange under stress.
              The field counts excitation and hue alongside density, so each
              PIXEL takes the colour of the pulse passing through it and the
              rest of the body stays cyan. The creature is never tinted as a
              whole; only a faint thermal cast remains of the old global ramp.
"""

from __future__ import annotations

import cairo
import numpy as np

from ..core.theme import AMBER, CYAN, CYAN_DEEP, LIME
from ..core.viewport import Viewport
from ..core.world import WORLD_RADIUS
from . import pointfield as PF
from .mathforms import SourceBody, max_extent

#: The deep-field wash behind the specimen, in world units.
_WASH_R = 430.0

#: Radius of the field buffer, in world units: the design radius plus a
#: margin for the few soft pixels a point spreads into.
R_FIELD = WORLD_RADIUS + 4.0

#: Largest side the point field is counted at, in pixels. Above it the field
#: is scaled up on the blit - see draw_organism.
MAX_FIELD_PX = 760.0
_WASH_STEP = 6.0
_WASH_LIMIT = 6
_WASH: dict = {}
_WASH_ORDER: list = []


def _hex(h: str) -> tuple[float, float, float]:
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0)


#: The pulse palette, as (hue position, colour) stops. Blue -> green ->
#: amber -> orange, deliberately without purple or red: a bioluminescent
#: specimen, not a spectrum. mathforms.state_hue maps the system condition
#: onto this axis.
PULSE_PALETTE = (
    (0.00, "4d9eff"),   # quiescent: electric abyssal blue
    (0.16, "8ce0f8"),   # base: cold pale cyan
    (0.34, "4cf0c8"),   # rousing: aqua
    (0.50, "5cff8a"),   # healthy excitation: bioluminescent green
    (0.64, "b4f25a"),   # high load: lime -> warm green
    (0.80, "ffc445"),   # thermal stress: amber
    (1.00, "ff7a1e"),   # high stress: orange
)


def _build_lut(n: int = 256) -> np.ndarray:
    pos = np.array([p for p, _ in PULSE_PALETTE], dtype=np.float64)
    rgb = np.array([_hex(c) for _, c in PULSE_PALETTE], dtype=np.float64)
    q = np.linspace(0.0, 1.0, n)
    return np.stack([np.interp(q, pos, rgb[:, c]) for c in range(3)],
                    axis=1).astype(np.float32)


PULSE_LUT = _build_lut()

_CD_R, _CD_G, _CD_B = CYAN_DEEP
_C_R, _C_G, _C_B = CYAN
_A_R, _A_G, _A_B = AMBER


def _mix(a, b, t: float):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def _wash_disc(radius: float):
    """The deep-field wash, rendered once per quantised radius.

    Filling a 900px radial gradient was the most expensive single operation
    in the frame, and its radius changes only on resize.
    """
    q = max(1, int(round(radius / _WASH_STEP)))
    hit = _WASH.get(q)
    if hit is not None:
        return hit
    r = q * _WASH_STEP
    side = int(r * 2.0) + 2
    if side < 2 or side > 8192:
        return None
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, side, side)
    c = cairo.Context(surf)
    mid = side * 0.5
    g = cairo.RadialGradient(mid, mid, 0.0, mid, mid, max(r, 1.0))
    g.add_color_stop_rgba(0.00, _CD_R, _CD_G, _CD_B, 0.070)
    g.add_color_stop_rgba(0.18, _CD_R, _CD_G, _CD_B, 0.048)
    g.add_color_stop_rgba(0.45, _CD_R, _CD_G, _CD_B, 0.024)
    g.add_color_stop_rgba(0.70, _CD_R, _CD_G, _CD_B, 0.010)
    g.add_color_stop_rgba(0.87, _CD_R, _CD_G, _CD_B, 0.003)
    g.add_color_stop_rgba(1.00, _CD_R, _CD_G, _CD_B, 0.000)
    c.set_source(g)
    c.arc(mid, mid, r, 0.0, 6.283185307179586)
    c.fill()
    surf.flush()
    ent = (surf, side)
    _WASH[q] = ent
    _WASH_ORDER.append(q)
    while len(_WASH_ORDER) > _WASH_LIMIT:
        _WASH.pop(_WASH_ORDER.pop(0), None)
    return ent


def draw_organism(cr, vp: Viewport, org: SourceBody) -> None:
    """Draw one frame of the specimen. Caller has already painted the field."""
    # ---- deep-field wash ---------------------------------------------------
    wash = _wash_disc(vp.length(_WASH_R))
    if wash is not None:
        disc, side = wash
        cr.save()
        cr.set_source_surface(disc, round(vp.cx - side * 0.5),
                              round(vp.cy - side * 0.5))
        cr.paint()
        cr.restore()

    # ---- the point field ---------------------------------------------------
    # The field buffer covers the creature's own disc - the design radius -
    # and not the whole stage. The colour map is a per-PIXEL pass, so sizing
    # it to a wide stage paid for empty glass on either side of the animal;
    # at 2560x1600 that was most of a 74 ms frame.
    #
    # Beyond MAX_FIELD_PX the field is counted at that resolution and scaled
    # up once on the blit. These are soft one- and two-pixel points, so a
    # modest upscale is invisible, and it bounds the cost at any window size.
    side_px = 2.0 * R_FIELD * vp.scale
    if side_px < 8.0:
        return
    res = min(1.0, MAX_FIELD_PX / side_px)
    side = int(side_px * res) + 2
    wx, wy, weight = org.points()
    if wx.size == 0:
        return

    ent = PF.field_buffers(org.src.key, side, side)
    k = np.float32(vp.scale * res)
    half = np.float32(side * 0.5)
    px = wx * k + half
    py = wy * k + half
    exc, hue = org.excitation()
    PF.accumulate_physio(ent, px, py, weight, exc, hue, org.persist)

    # Ink is scaled by how many BUFFER pixels one world unit covers: the same
    # cloud spread over four times the area must be counted four times as
    # strongly, or the creature fades out as the window grows.
    eff = vp.scale * res
    # Exposure: matched to references/Abbysal_Final.png, where the specimen is
    # the brightest object on the machine. A presentation gain on the counted
    # density only - the equations and every point position are untouched.
    ink = min(org.src.ink * 1.20 / max(eff * eff * 3.4, 0.02), 5.0)
    # A trailing species keeps a fraction `r` of every frame, so its
    # accumulator converges to 1/(1-r) times a single frame's count. Divide
    # that back out, or the trail - the species' whole character - saturates
    # into a solid silhouette and the structure inside it is lost.
    # Square root rather than the full 1-r: the eye reads a soft-knee
    # composite, not a linear count, so dividing the whole gain out left the
    # trail a faint ghost. This keeps it as bright as a clearing species
    # while leaving the structure inside it legible.
    r = org.persist
    if r > 0.0:
        ink *= (1.0 - r) ** 0.5 * 1.55

    # Only a faint thermal cast on the resting body: the stress colour is
    # carried locally by the pulse, never by tinting the whole creature.
    warm = org.warmth * 0.22
    lo = _mix(CYAN_DEEP, (0.34, 0.20, 0.06), warm)
    hi = _mix(CYAN, AMBER, warm)
    up = 1.0 / res
    PF.paint_physio(cr, ent, vp.cx - side * 0.5 * up,
                    vp.cy - side * 0.5 * up, lo, hi, PULSE_LUT, ink,
                    scale_up=up)


def draw_calibration(cr, vp: Viewport, org: SourceBody) -> None:
    """DEBUG overlay -- GATE 1 circularity check.

    World circles at WORLD_RADIUS and r=250, a crosshair through the world
    origin, and the specimen's current extent. If the stage is ever stretched
    these circles show up as ellipses on the very first frame.
    """
    import math

    cx, cy = vp.cx, vp.cy
    l_r, l_g, l_b = LIME
    TAU = 6.283185307179586

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(1.0)

    for r_world, a in ((WORLD_RADIUS, 0.26), (250.0, 0.17)):
        cr.set_source_rgba(l_r, l_g, l_b, a)
        cr.new_path()
        cr.arc(cx, cy, vp.length(r_world), 0.0, TAU)
        cr.stroke()

    cr.set_source_rgba(l_r, l_g, l_b, 0.13)
    ext = vp.length(WORLD_RADIUS + 26.0)
    cr.new_path()
    cr.move_to(cx - ext, cy)
    cr.line_to(cx + ext, cy)
    cr.move_to(cx, cy - ext)
    cr.line_to(cx, cy + ext)
    cr.stroke()

    cr.set_source_rgba(l_r, l_g, l_b, 0.19)
    cr.new_path()
    for k in range(4):
        ang = math.pi * 0.25 + k * math.pi * 0.5
        c, s = math.cos(ang), math.sin(ang)
        x0, y0 = vp.px(c * 40.0, s * 40.0)
        x1, y1 = vp.px(c * WORLD_RADIUS, s * WORLD_RADIUS)
        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
    cr.stroke()

    try:
        e = max_extent(org)
    except Exception:
        e = 0.0
    if e > 0.0:
        cr.set_source_rgba(l_r, l_g, l_b, 0.38)
        cr.set_line_width(0.8)
        cr.new_path()
        cr.arc(cx, cy, vp.length(e), 0.0, TAU)
        cr.stroke()
    cr.restore()
