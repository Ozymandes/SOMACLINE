"""Diagnostic overlay. Kept permanently in the product, toggled with F1.

Hiding diagnostics is how scaling bugs survive. This shows the numbers that
would expose them: physical size, logical size, scale factor, viewport bounds,
render scale, and the simulation clock (which must never jump or reset).
"""

from __future__ import annotations

import cairo

from ..core.layout import Layout
from ..core.theme import LIME, RULE, rgba
from ..core.viewport import Viewport, isotropy_error


def draw_debug(cr: cairo.Context, layout: Layout, vp: Viewport, info: dict) -> None:
    lines = [
        f"WIDGET   {layout.width:.0f} x {layout.height:.0f} logical",
        f"SURFACE  {info['surf_w']} x {info['surf_h']} device   scale {info['gdk_scale']:.2f}",
        f"STATE    {layout.state.value}",
        f"STAGE    {vp.stage_w:.0f} x {vp.stage_h:.0f} @ {vp.stage_x:.0f},{vp.stage_y:.0f}",
        f"RSCALE   {vp.scale:.5f} px/world   organism r={vp.organism_px_radius:.1f}px",
        f"ISOTROPY {isotropy_error(vp):.2e} px   clip={vp.clips}",
        f"FPS      {info['fps']:.1f}   frame {info['frame_ms']:.2f}ms"
        f"  (sim {info['sim_ms']:.2f} draw {info['draw_ms']:.2f})",
        f"SIM      t={info['sim_time']:.2f}s  frames={info['frames']}"
        f"  resizes={info['resizes']}  rebuilds={info['rebuilds']}",
        f"PHYS     ag={info['ag']:.2f} pu={info['pu']:.2f} de={info['de']:.2f}",
        f"TELEM    {info['notes']}",
    ]

    cr.save()
    cr.select_font_face("JetBrainsMono Nerd Font",
                        cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    size = 11.0
    cr.set_font_size(size)
    line_h = size * 1.45

    widths = [cr.text_extents(s).width for s in lines]
    box_w = max(widths) + 20
    box_h = line_h * len(lines) + 16
    x = 10.0
    y = max(10.0, layout.height - box_h - 10.0)

    cr.set_source_rgba(0.01, 0.02, 0.03, 0.88)
    cr.rectangle(x, y, box_w, box_h)
    cr.fill()
    cr.set_source_rgba(*rgba(RULE, 0.9))
    cr.set_line_width(1.0)
    cr.rectangle(x + 0.5, y + 0.5, box_w - 1, box_h - 1)
    cr.stroke()

    cr.set_source_rgba(*rgba(LIME, 0.78))
    ty = y + 10 + size
    for s in lines:
        cr.move_to(x + 10, ty)
        cr.show_text(s)
        ty += line_h
    cr.restore()
