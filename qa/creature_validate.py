#!/usr/bin/env python3
"""Render each source equation in ITS OWN 400x400 canvas, unmodified.

This is the check that has to pass before any of this reaches the console.
The equations are run in their original coordinate system, at their original
sample count, with their original time step and compositing - no world
transform, no telemetry, no chrome. If a creature is wrong here, it is the
port that is wrong, and no amount of work downstream will fix it.

    python3 qa/creature_validate.py                 # all five + a sheet
    python3 qa/creature_validate.py --key s03       # one
    python3 qa/creature_validate.py --frames 240    # let it settle further
"""
from __future__ import annotations

import argparse
import os
import sys

import cairo
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from abyssal.organism import pointfield as PF        # noqa: E402
from abyssal.organism import sources as S            # noqa: E402

OUT = os.path.join(ROOT, "docs", "creature_validation")

#: The originals draw white on near-black. Validation renders them that way
#: so the comparison is against the source, not against our palette.
LO = (0.72, 0.78, 0.82)
HI = (1.00, 1.00, 1.00)


def render(src: S.Source, frames: int, size: int = 400) -> cairo.ImageSurface:
    ent = PF.field_buffers("validate", size, size)
    ent["acc"][:] = 0.0
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    # background(9) / background(6) - the originals' near-black ground.
    cr.set_source_rgb(9 / 255, 9 / 255, 9 / 255)
    cr.paint()

    t = 0.0
    k = size / S.SRC
    for _ in range(frames):
        t += src.dt
        sm = src.sample(t)
        px = sm.x * k
        py = sm.y * k
        good = np.isfinite(px) & np.isfinite(py)
        acc = PF.accumulate(ent, px[good], py[good],
                            sm.weight if np.isscalar(sm.weight)
                            else sm.weight[good],
                            src.persist)
    PF.paint(cr, ent, acc, 0, 0, LO, HI, src.ink)
    surf.flush()
    return surf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None)
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--size", type=int, default=400)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    picks = [s for s in S.SOURCES if a.key in (None, s.key)]
    tiles = []
    for src in picks:
        surf = render(src, a.frames, a.size)
        path = os.path.join(OUT, f"{src.key}.png")
        surf.write_to_png(path)
        print(f"  {src.key}  {src.title:<18} n={src.n:>6} "
              f"dt=PI/{np.pi / src.dt:<5.0f} ink={src.ink:.3f} "
              f"persist={src.persist:.3f}  -> {path}")
        tiles.append(surf)

    if len(tiles) > 1:
        sheet = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                   a.size * len(tiles), a.size)
        c = cairo.Context(sheet)
        for i, s in enumerate(tiles):
            c.set_source_surface(s, i * a.size, 0)
            c.paint()
        path = os.path.join(OUT, "sheet.png")
        sheet.write_to_png(path)
        print(f"  sheet -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
