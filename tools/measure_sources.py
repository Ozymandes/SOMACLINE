#!/usr/bin/env python3
"""Measure where each source equation frames its creature in its own canvas.

The originals are loosely framed and none is centred - which is fine for a
400x400 sketch and wrong for a scope with a graduated radius axis. This
sweeps one full period of each source's clock and reports the centre and
half-extent of its robust bounding box, which `sources.py` carries as the
frame_* constants.

Robust, not absolute: source 01's `0.3/k` term throws a handful of samples
hundreds of units off-canvas every frame. Fitting to the absolute extrema
would shrink the visible animal to a smudge to accommodate points nobody can
see, so the 0.15/99.85 percentiles are used instead.

    python3 tools/measure_sources.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from abyssal.organism import sources as S  # noqa: E402

PCT = (0.15, 99.85)
PERIOD_FRAMES = 240


def main() -> int:
    print(f"{'key':5s} {'frame_cx':>9} {'frame_cy':>9} {'frame_half':>11}")
    for src in S.SOURCES:
        xs, ys = [], []
        for f in range(PERIOD_FRAMES):
            sm = src.sample(f * src.dt)
            g = np.isfinite(sm.x) & np.isfinite(sm.y)
            xs.append(sm.x[g])
            ys.append(sm.y[g])
        X, Y = np.concatenate(xs), np.concatenate(ys)
        x0, x1 = np.percentile(X, PCT)
        y0, y1 = np.percentile(Y, PCT)
        print(f"{src.key:5s} {(x0 + x1) / 2:9.2f} {(y0 + y1) / 2:9.2f} "
              f"{max(x1 - x0, y1 - y0) / 2:11.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
