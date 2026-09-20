#!/usr/bin/env python3
"""Render the specimen catalogue to one contact sheet, headless.

The creature engine is the slowest thing in this project to iterate on by eye,
so it gets its own loop: no window, no console chrome, one organism per tile
at a chosen sim time and physiology.

    python3 qa/specimen_sheet.py out.png [--seconds 8] [--cpu .4 --temp .5 ...]
"""
from __future__ import annotations

import argparse
import os
import sys

import cairo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from abyssal.core.signals import Physiology            # noqa: E402
from abyssal.core.viewport import Viewport             # noqa: E402
from abyssal.organism.render import draw_organism      # noqa: E402
from abyssal.organism.species import CATALOGUE         # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--tile", type=int, default=440)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--cpu", type=float, default=0.30)
    ap.add_argument("--temp", type=float, default=0.45)
    ap.add_argument("--mem", type=float, default=0.70)
    ap.add_argument("--io", type=float, default=0.25)
    a = ap.parse_args()

    n = len(CATALOGUE)
    T = a.tile
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, T * n, T + 26)
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.016, 0.028, 0.036)
    cr.paint()

    phys = Physiology(agitation=a.cpu, pulse=a.temp, density=a.mem,
                      flux=a.io, surge=a.io)
    dt = 1.0 / 60.0
    for i, sp in enumerate(CATALOGUE):
        org = sp.build(seed=20260920 + i)
        for _ in range(int(a.seconds / dt)):
            org.update(dt, phys)
        vp = Viewport.for_stage(i * T, 0, T, T)
        cr.save()
        cr.rectangle(i * T, 0, T, T)
        cr.clip()
        draw_organism(cr, vp, org)
        cr.restore()
        cr.select_font_face("monospace")
        cr.set_font_size(11)
        cr.set_source_rgb(0.55, 0.66, 0.74)
        cr.move_to(i * T + 10, T + 17)
        cr.show_text(f"{i + 1:02d} {sp.name}")
        cr.set_source_rgba(0.1, 0.16, 0.2, 1.0)
        cr.rectangle(i * T + T - 0.5, 0, 1, T)
        cr.fill()
    surf.flush()
    surf.write_to_png(a.out)
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
