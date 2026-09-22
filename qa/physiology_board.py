#!/usr/bin/env python3
"""The 5x3 physiology validation board: five specimens, three states.

    python3 qa/physiology_board.py

Rows are the five species in bank order; columns are QUIESCENT, NORMAL and
STRESSED, each driven by the REAL PhysiologyModel from the same telemetry
samples qa/physiology_gates.py uses, and rendered by the REAL draw pipeline
(draw_organism - the same code the window shows). 15 deterministic renders,
one contact sheet, saved under docs/physiology_validation/.

The states must visibly differ - the column hue walks blue -> green -> amber
-> orange - while each row remains unmistakably the same organism.
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cairo  # noqa: E402

from abyssal.core.layout import Rect  # noqa: E402
from abyssal.core.physiology import PhysiologyModel  # noqa: E402
from abyssal.core.signals import Telemetry  # noqa: E402
from abyssal.core.theme import INK_BRIGHT, INK_TECH, rgba  # noqa: E402
from abyssal.core.viewport import Viewport  # noqa: E402
from abyssal.organism.render import draw_organism  # noqa: E402
from abyssal.organism.species import CATALOGUE  # noqa: E402
from qa.physiology_gates import (  # noqa: E402
    STATES, TEL_NORMAL, TEL_QUIESCENT, TEL_STRESSED)

DT = 1.0 / 60.0
DRIVE_S = 8.0            # seconds of driven life before the shutter
CELL_W, CELL_H = 420, 330
PAD = 10
HEAD = 54
SIDE_W = 92

OUT = os.path.join(ROOT, "docs", "physiology_validation", "physiology_5x3.png")


def _label(cr, text: str, size: float, cx: float, cy: float,
           rgb, alpha: float = 1.0) -> None:
    """A small centred caption. Board text is fixed, so it is drawn direct."""
    cr.save()
    cr.set_source_rgba(*rgba(rgb, alpha))
    cr.select_font_face("Microgramma", cairo.FontSlant.NORMAL,
                        cairo.FontWeight.NORMAL)
    cr.set_font_size(size)
    ext = cr.text_extents(text)
    cr.move_to(cx - ext.x_advance / 2.0, cy + ext.height / 2.0 - ext.y_bearing / 2.0)
    cr.show_text(text)
    cr.restore()


def cell(specimen: int, tel: Telemetry, side_px: int) -> np.ndarray:
    """One driven, rendered cell: REAL model, REAL organism, REAL renderer."""
    sp = CATALOGUE[specimen]
    org = sp.build()
    pm = PhysiologyModel()
    for _ in range(int(DRIVE_S / DT)):
        org.update(DT, pm.update(DT, tel))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, side_px, side_px)
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.012, 0.020, 0.032)
    cr.paint()
    vp = Viewport.for_stage(0, 0, float(side_px), float(side_px))
    draw_organism(cr, vp, org)
    surf.flush()
    return np.ndarray(shape=(side_px, side_px, 4), dtype=np.uint8,
                      buffer=surf.get_data()).copy()


def main() -> int:
    w = PAD + SIDE_W + 3 * (CELL_W + PAD) + PAD
    h = HEAD + 5 * (CELL_H + PAD) + PAD
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.008, 0.012, 0.020)
    cr.paint()

    # column heads
    for c, (sname, _) in enumerate(STATES):
        x = PAD + SIDE_W + c * (CELL_W + PAD)
        _label(cr, sname, 17.0, x + CELL_W / 2.0, HEAD - 26.0, INK_TECH, 0.95)

    for r, sp in enumerate(CATALOGUE):
        y = HEAD + r * (CELL_H + PAD)
        _label(cr, f"0{r + 1}", 22.0, PAD + 16, y + CELL_H / 2.0 - 16.0,
               INK_BRIGHT, 0.95)
        _label(cr, sp.name.split()[0], 11.0, PAD + SIDE_W / 2.0,
               y + CELL_H / 2.0 + 8.0, INK_TECH, 0.85)
        _label(cr, sp.name.split()[1], 11.0, PAD + SIDE_W / 2.0,
               y + CELL_H / 2.0 + 26.0, INK_TECH, 0.85)
        for c, (_, tel) in enumerate(STATES):
            pix = cell(r, tel, 300)
            x = PAD + SIDE_W + c * (CELL_W + PAD)
            # the rendered 300px organism, centred in its cell, at 1:1
            dst = np.ndarray(shape=(h, w, 4), dtype=np.uint8,
                             buffer=surf.get_data())
            oy, ox = int(y + (CELL_H - 300) / 2.0), int(x + (CELL_W - 300) / 2.0)
            a = pix[..., 3:4].astype(np.float32) / 255.0
            blend = (pix[..., :3] * a
                     + dst[oy:oy + 300, ox:ox + 300, :3] * (1 - a)).astype(np.uint8)
            dst[oy:oy + 300, ox:ox + 300, :3] = blend
            dst[oy:oy + 300, ox:ox + 300, 3] = 255
            # hairline frame, as the instrument draws one
            cr.save()
            cr.set_source_rgba(*rgba(INK_TECH, 0.25))
            cr.set_line_width(1.0)
            cr.rectangle(x + 0.5, y + 0.5, CELL_W - 1.0, CELL_H - 1.0)
            cr.stroke()
            cr.restore()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    surf.write_to_png(OUT)
    print(f"board -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
