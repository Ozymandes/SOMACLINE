#!/usr/bin/env python3
"""Headless frame capture: render the console straight to a PNG.

The whole draw is a pure function of (sim state, width, height), so a frame can
be produced without a window, a compositor or a frame clock. That makes the
visual feedback loop seconds instead of minutes, and it is deterministic:
the same seed, sim time and size always give the same image.

    python3 qa/offscreen.py out.png --width 1400 --height 880 --specimen 0
"""
from __future__ import annotations

import argparse
import os
import sys

import cairo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from abyssal.core.layout import resolve                     # noqa: E402
from abyssal.core.lighting import LightField                # noqa: E402
from abyssal.core.physiology import PhysiologyModel         # noqa: E402
from abyssal.core.signals import Telemetry                  # noqa: E402
from abyssal.core.viewport import Viewport                  # noqa: E402
from abyssal.organism.render import draw_organism           # noqa: E402
from abyssal.organism.species import by_index               # noqa: E402
from abyssal.ui import console                              # noqa: E402
from abyssal.ui.chrome import draw_background               # noqa: E402


def frame(width: int, height: int, specimen: int = 0, seconds: float = 6.0,
          tel: Telemetry | None = None, fps: float = 60.0) -> cairo.ImageSurface:
    sp = by_index(specimen)
    org = sp.build(seed=20260920 + specimen)
    phys = PhysiologyModel()
    # Normalised channels are derived from the raw ones exactly as
    # telemetry.source derives them, so the harness and the app agree about
    # which state a reading is in (83 C is ELEVATED, and reads amber).
    tel = tel or Telemetry(
        cpu_load=0.14, memory_pressure=8.2 / 13.5,
        temperature=(83.0 - 30.0) / (95.0 - 30.0), temp_available=True,
        io_rate=0.22, io_available=True,
        cpu_pct=14.0, mem_used_gb=8.2, mem_total_gb=13.5,
        temp_c=83.0, temp_label="CPU")
    dt = 1.0 / 60.0
    for _ in range(int(seconds / dt)):
        org.update(dt, phys.update(dt, tel))

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surf)
    L = resolve(width, height)
    glass = console.stage_content(L)
    vp = Viewport.for_stage(glass.x, glass.y, glass.w, glass.h)
    m = console.ConsoleModel(species=sp, active=specimen)
    m.mode_state = "active"
    light = LightField()

    # Prime the traces so the graphs show a real 60 s history, not one point.
    import math
    for i in range(96):
        t2 = Telemetry(
            cpu_load=max(0.0, min(1.0, tel.cpu_load + 0.10 * math.sin(i * 0.31)
                                  + 0.05 * math.sin(i * 1.7))),
            memory_pressure=max(0.0, min(1.0, tel.memory_pressure + 0.03 * math.sin(i * 0.2))),
            temperature=max(0.0, min(1.0, tel.temperature + 0.06 * math.sin(i * 0.17)
                                     + 0.03 * math.sin(i * 0.9))),
            temp_available=True, cpu_pct=tel.cpu_pct, mem_used_gb=tel.mem_used_gb,
            mem_total_gb=tel.mem_total_gb, temp_c=tel.temp_c)
        for ch in ("cpu", "thermal", "memory", "frame"):
            pass
        m.trace("cpu").push(t2.cpu_load)
        m.trace("thermal").push(t2.temperature)
        m.trace("memory").push(t2.memory_pressure)
        m.trace("frame").push(min(1.0, (58 + 3 * math.sin(i * 0.5)) / 120.0))

    console.draw_under(cr, L, m, vp.scale)
    if L.stage.valid:
        cr.save()
        cr.rectangle(glass.x, glass.y, glass.w, glass.h)
        cr.clip()
        draw_organism(cr, vp, org)
        cr.restore()
    console.draw_over(cr, L, tel, fps, 1000.0 / max(fps, 1.0), m, light)
    surf.flush()
    return surf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--height", type=int, default=880)
    ap.add_argument("--specimen", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=6.0)
    a = ap.parse_args()
    s = frame(a.width, a.height, a.specimen, a.seconds)
    s.write_to_png(a.out)
    print(f"{a.out}  {a.width}x{a.height} specimen {a.specimen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
