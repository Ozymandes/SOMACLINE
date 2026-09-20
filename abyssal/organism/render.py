"""Cairo rendering of the specimen. The only module that turns world -> pixels.

NO cairo transform is ever set. Every coordinate is pushed through
Viewport.px() / Viewport.length(), which are uniform by construction, so a
world circle is a screen circle and a stroke width is the same in x and y at
any window size or aspect ratio. Nothing here mutates the organism.
"""

from __future__ import annotations

from collections import OrderedDict

import cairo
import numpy as np

from ..core.theme import CYAN, CYAN_DEEP, LIME, SILVER
from ..core.viewport import Viewport
from ..core.world import WORLD_RADIUS
from .mathforms import Body, max_extent

# Pixel floors / ceilings so the specimen survives both a postage stamp and a
# 4K stage without turning into either invisible hairlines or fat blobs.
MIN_STROKE_PX = 0.45
MAX_STROKE_PX = 4.0
MIN_NODE_PX = 0.55
MAX_NODE_PX = 3.4
ALPHA_CULL = 0.015

#: Below this drawn pitch a dotted filament is stroked solid instead: the dots
#: would be closer together than the line is wide, so the dash costs work and
#: changes nothing visible.
MIN_DOT_PITCH_PX = 2.6
MIN_DOT_GAP_PX = 0.85

_WASH_R = 430.0

# Reusable scratch, keyed by array shape. Renderer-side only; the organism
# never learns that pixels exist.
_SCRATCH: dict = {}

#: The deep-field wash, cached as a rendered disc per quantised radius.
#: Filling a 900px radial gradient was 6.5ms - by a wide margin the most
#: expensive single operation in the frame - and its radius changes only on
#: resize. Rasterising it once and blitting the result is the same pixels for
#: a twentieth of the cost. Bounded, and derived pixels only.
_WASH_STEP = 6.0
_WASH_LIMIT = 6
_WASH: "OrderedDict[int, cairo.ImageSurface]" = OrderedDict()

_CD_R, _CD_G, _CD_B = CYAN_DEEP
_C_R, _C_G, _C_B = CYAN
_DR, _DG, _DB = _C_R - _CD_R, _C_G - _CD_G, _C_B - _CD_B


def _scratch(shape):
    buf = _SCRATCH.get(shape)
    if buf is None:
        buf = (np.empty(shape, dtype=np.float64),
               np.empty(shape, dtype=np.float64))
        _SCRATCH[shape] = buf
    return buf


def _wash_disc(radius: float) -> tuple[cairo.ImageSurface, int] | None:
    """The deep-field wash rendered to a disc, cached per quantised radius."""
    q = max(1, int(round(radius / _WASH_STEP)))
    hit = _WASH.get(q)
    if hit is not None:
        _WASH.move_to_end(q)
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
    while len(_WASH) > _WASH_LIMIT:
        _WASH.popitem(last=False)
    return ent


def draw_organism(cr, vp: Viewport, org: Body) -> None:
    """Draw one frame of the specimen. Caller has already painted the field."""
    scale = vp.scale
    cx = vp.cx
    cy = vp.cy

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    # Filaments are hairline-thin; FAST antialiasing is visually indistinguishable
    # from DEFAULT here and is ~16% cheaper, which matters because Cairo is a CPU
    # rasteriser and this display runs at fractional scale 1.6. Restored before
    # returning so text and chrome keep full-quality antialiasing.
    prev_aa = cr.get_antialias()
    cr.set_antialias(cairo.ANTIALIAS_FAST)

    # ---- 1. deep-field wash ------------------------------------------------
    wash = _wash_disc(vp.length(_WASH_R))
    if wash is not None:
        disc, side = wash
        cr.set_source_surface(disc, round(cx - side * 0.5),
                              round(cy - side * 0.5))
        cr.paint()

    # ---- 2. filaments, back to front by alpha ------------------------------
    px, py = _scratch(org.fil_x.shape)
    np.multiply(org.fil_x, scale, out=px)
    np.add(px, cx, out=px)
    np.multiply(org.fil_y, scale, out=py)
    np.add(py, cy, out=py)

    xs = px.tolist()
    ys = py.tolist()
    alpha = org.fil_alpha
    order = alpha.argsort().tolist()
    a_l = alpha.tolist()
    w_l = org.fil_width.tolist()
    t_l = org.fil_tint.tolist()
    d_l = org.fil_dot.tolist()

    move_to = cr.move_to
    line_to = cr.line_to
    stroke = cr.stroke
    set_rgba = cr.set_source_rgba
    set_lw = cr.set_line_width
    cd_r, cd_g, cd_b = _CD_R, _CD_G, _CD_B
    dr, dg, db = _DR, _DG, _DB

    set_dash = cr.set_dash
    dashed = False
    for i in order:
        a = a_l[i]
        if a < ALPHA_CULL:
            continue
        t = t_l[i]
        set_rgba(cd_r + dr * t, cd_g + dg * t, cd_b + db * t, a)
        lw = w_l[i] * scale
        if lw < MIN_STROKE_PX:
            lw = MIN_STROKE_PX
        elif lw > MAX_STROKE_PX:
            lw = MAX_STROKE_PX
        set_lw(lw)
        # The references are drawn as sequences of DOTS, not continuous ink.
        # A dash pattern reproduces that for the cost of one call, where one
        # filament per dot would multiply the path count by twenty. Below the
        # pitch at which dots would merge into a line anyway, the filament is
        # simply stroked solid - which is also what keeps a tiny stage cheap.
        pitch = d_l[i] * scale
        if pitch >= MIN_DOT_PITCH_PX:
            gap = pitch - lw
            if gap < MIN_DOT_GAP_PX:
                gap = MIN_DOT_GAP_PX
            set_dash([lw * 0.95, gap], 0.0)
            dashed = True
        elif dashed:
            set_dash([], 0.0)
            dashed = False
        rx = xs[i]
        ry = ys[i]
        move_to(rx[0], ry[0])
        for j in range(1, len(rx)):
            line_to(rx[j], ry[j])
        stroke()
    if dashed:
        set_dash([], 0.0)

    # ---- 3. luminous construction nodes ------------------------------------
    nx = org.node_x
    ny = org.node_y
    nxs = (nx * scale + cx).tolist()
    nys = (ny * scale + cy).tolist()
    nrs = org.node_r.tolist()
    nas = org.node_a.tolist()
    arc = cr.arc
    fill = cr.fill
    new_path = cr.new_path
    s_r, s_g, s_b = SILVER
    TAU = 6.283185307179586

    set_lw(max(MIN_STROKE_PX, min(1.0, 0.9 * scale * 2.0)))
    for i in range(len(nrs)):
        a = nas[i]
        if a < ALPHA_CULL:
            continue
        r = nrs[i] * scale
        if r < MIN_NODE_PX:
            r = MIN_NODE_PX
        elif r > MAX_NODE_PX:
            r = MAX_NODE_PX
        x = nxs[i]
        y = nys[i]
        # dim ring, then crisp dot
        if r > 1.1:
            set_rgba(cd_r, cd_g, cd_b, a * 0.45)
            new_path()
            arc(x, y, r + 1.4, 0.0, TAU)
            stroke()
        set_rgba(s_r, s_g, s_b, a)
        new_path()
        arc(x, y, r, 0.0, TAU)
        fill()

    # ---- 4. core -----------------------------------------------------------
    # Not every specimen has one. A body whose structure IS its centre - the
    # comb, the frond, the mirrored rostrum - would be falsified by a glowing
    # bead at the world origin, so a zero radius draws nothing at all.
    if org.core_r <= 0.0:
        cr.set_antialias(prev_aa)
        return
    core = vp.length(org.core_r)
    if core < 1.5:
        core = 1.5

    # inner body
    set_rgba(cd_r, cd_g, cd_b, 0.15)
    new_path()
    cr.arc(cx, cy, core * 0.92, 0.0, TAU)
    fill()

    # 2-pass halo: wide + very low alpha under a crisp hairline. No bloom.
    set_rgba(_C_R, _C_G, _C_B, 0.07)
    set_lw(max(1.2, core * 0.42))
    new_path()
    cr.arc(cx, cy, core * 1.06, 0.0, TAU)
    stroke()

    set_rgba(_C_R, _C_G, _C_B, 0.80)
    set_lw(max(MIN_STROKE_PX, min(1.8, core * 0.075)))
    new_path()
    cr.arc(cx, cy, core, 0.0, TAU)
    stroke()

    # inner construction ring + centre mark
    set_rgba(_C_R, _C_G, _C_B, 0.26)
    set_lw(MIN_STROKE_PX)
    new_path()
    cr.arc(cx, cy, core * 0.46, 0.0, TAU)
    stroke()

    set_rgba(0.92, 0.98, 1.0, 0.85)
    new_path()
    cr.arc(cx, cy, max(0.7, core * 0.10), 0.0, TAU)
    fill()

    cr.set_antialias(prev_aa)

def draw_calibration(cr, vp: Viewport, org: Body) -> None:
    """DEBUG overlay -- GATE 1 circularity check.

    World circles at WORLD_RADIUS and r=250, a crosshair through the world
    origin, and the four 45-degree lobe spokes. If the stage is ever stretched
    these circles show up as ellipses on the very first frame.
    """
    import math

    cx = vp.cx
    cy = vp.cy
    l_r, l_g, l_b = LIME
    TAU = 6.283185307179586

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(max(MIN_STROKE_PX, 1.0))

    for r_world, a in ((WORLD_RADIUS, 0.26), (250.0, 0.17)):
        cr.set_source_rgba(l_r, l_g, l_b, a)
        cr.new_path()
        cr.arc(cx, cy, vp.length(r_world), 0.0, TAU)
        cr.stroke()

    # crosshair through the world origin
    cr.set_source_rgba(l_r, l_g, l_b, 0.13)
    ext = vp.length(WORLD_RADIUS + 26.0)
    cr.new_path()
    cr.move_to(cx - ext, cy)
    cr.line_to(cx + ext, cy)
    cr.move_to(cx, cy - ext)
    cr.line_to(cx, cy + ext)
    cr.stroke()

    # 45-degree quadrant spokes -- one per lobe axis
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

    # current actual extent, so drift is visible against the design radius
    try:
        e = max_extent(org)
    except Exception:
        e = 0.0
    if e > 0.0:
        cr.set_source_rgba(l_r, l_g, l_b, 0.38)
        cr.set_line_width(max(MIN_STROKE_PX, 0.8))
        cr.new_path()
        cr.arc(cx, cy, vp.length(e), 0.0, TAU)
        cr.stroke()

    cr.restore()
