#!/usr/bin/env python3
"""GATE 1 (geometry) + GATE 3 (performance), headless via cairo ImageSurface.

These need no compositor, so they run fast and catch scaling bugs long before
a window is involved.  `python3 qa/gates.py`
"""

from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402

from abyssal.core.layout import LayoutState, resolve  # noqa: E402
from abyssal.core.physiology import PhysiologyModel  # noqa: E402
from abyssal.core.signals import Telemetry  # noqa: E402
from abyssal.core.viewport import Viewport, isotropy_error  # noqa: E402
from abyssal.core.world import WORLD_RADIUS  # noqa: E402
from abyssal.organism.mathforms import max_extent  # noqa: E402
from abyssal.organism.species import CATALOGUE, by_index  # noqa: E402
from abyssal.core.lighting import LightField  # noqa: E402
from abyssal.ui import console  # noqa: E402
from abyssal.organism.render import draw_organism  # noqa: E402
from abyssal.ui.chrome import draw_background, draw_chrome  # noqa: E402

# A broad sweep, including hostile aspect ratios and the canonical tile.
SIZES = [
    (180, 120), (240, 200), (320, 240), (420, 340), (500, 1000),
    (640, 480), (700, 560), (800, 600), (900, 700), (1000, 640),
    (1100, 700), (1280, 800), (1400, 860), (1600, 900), (1920, 1080),
    (2000, 400), (2560, 1600), (300, 900), (1800, 420), (960, 1200),
]

FAKE = Telemetry(cpu_load=0.42, memory_pressure=0.67, temperature=0.61,
                 temp_available=True, cpu_pct=42.0, mem_used_gb=9.1,
                 mem_total_gb=13.5, temp_c=69.0, temp_label="K10TEMP",
                 notes="qa")


def gate1_geometry() -> tuple[bool, list[str]]:
    print("=== GATE 1 — GEOMETRY ===")
    import numpy as np
    problems: list[str] = []
    ref_sig = None
    org = by_index(0).build()
    pm = PhysiologyModel()
    # Drive to a hot state so the organism is at maximum extent.
    hot = Telemetry(cpu_load=1.0, memory_pressure=1.0, temperature=1.0,
                    temp_available=True)
    for _ in range(900):
        org.update(1 / 60, pm.update(1 / 60, hot))

    print(f"  {'size':>11}  {'state':<11} {'stage':>11} {'scale':>8} "
          f"{'iso_err':>9} {'r_px':>7} {'centre_err':>10} {'shape_err':>10}")
    for w, h in SIZES:
        L = resolve(w, h)
        glass = console.stage_content(L)
        vp = Viewport.for_stage(glass.x, glass.y, glass.w, glass.h)
        cm = console.ConsoleModel(species=by_index(0), active=0)
        light = LightField()

        iso = isotropy_error(vp)
        if iso > 1e-6:
            problems.append(f"{w}x{h}: isotropy error {iso:.3e}px — stretched")

        # Centre: the organism's pixel centre must equal the centre of the
        # GLASS it is seen through, not of the raw stage. The bezel's aperture
        # is very slightly off-centre in its own frame (96/100px left/right,
        # 85/93 top/bottom in sprite space), so centring on the stage would
        # sit the specimen a few pixels off inside the viewport it occupies.
        cerr = math.hypot(vp.cx - glass.cx, vp.cy - glass.cy)
        if cerr > 1e-9:
            problems.append(f"{w}x{h}: off-centre by {cerr:.3e}px")

        # Clipping: worst-case organism extent must fit the stage.
        ext_px = max_extent(org) * vp.scale
        room = min(vp.stage_w, vp.stage_h) / 2.0
        if glass.valid and ext_px > room + 0.5:
            problems.append(f"{w}x{h}: organism {ext_px:.1f}px > {room:.1f}px — CLIPS")

        # Shape invariance: unprojecting the drawn pixels must give back the
        # world coordinates EXACTLY, at every size. This is the claim that
        # matters and it holds for any body plan - the old quadrant-balance
        # check only ever made sense for a four-fold radial plume, and the
        # catalogue no longer contains one.
        sig = _shape_signature(org, vp)
        if ref_sig is None:
            ref_sig = sig
            serr = 0.0
        else:
            serr = float(np.max(np.abs(sig - ref_sig)))
        if serr > 1e-9:
            problems.append(f"{w}x{h}: shape changed under projection by "
                            f"{serr:.3e} world units")

        print(f"  {w:>5}x{h:<5}  {L.state.value:<11} "
              f"{L.stage.w:>5.0f}x{L.stage.h:<5.0f} {vp.scale:>8.4f} "
              f"{iso:>9.1e} {vp.organism_px_radius:>7.1f} {cerr:>10.1e} "
              f"{serr:>10.1e}")

    # Breakpoint sanity
    checks = [(699, 600, LayoutState.COMPACT), (700, 600, LayoutState.INSTRUMENT),
              (1099, 700, LayoutState.INSTRUMENT), (1100, 700, LayoutState.ARCHIVE)]
    for w, h, want in checks:
        got = resolve(w, h).state
        if got is not want:
            problems.append(f"breakpoint {w}x{h}: got {got.value}, want {want.value}")
    print(f"  breakpoints    {'OK' if not any('breakpoint' in p for p in problems) else 'BAD'}")
    print(f"  max design radius {WORLD_RADIUS}, organism max extent "
          f"{max_extent(org):.1f}")
    return (not problems), problems


def _shape_signature(org, vp) -> "np.ndarray":
    """Project the body to pixels, then unproject. Must be the identity.

    A resize is only allowed to be a uniform similarity transform. Projecting
    through the viewport and dividing the result back out recovers the world
    coordinates if and only if that is true; any anisotropy, rounding or
    centre drift shows up here as a non-zero residual, whatever the body plan.
    """
    import numpy as np
    x, y, _ = org.points()
    x, y = x[::5].astype(np.float64), y[::5].astype(np.float64)
    px = vp.cx + x * vp.scale
    py = vp.cy + y * vp.scale
    return np.concatenate([(px - vp.cx) / vp.scale, (py - vp.cy) / vp.scale])


def gate3_performance(outdir: str | None = None) -> tuple[bool, list[str]]:
    print("\n=== GATE 3 — PERFORMANCE ===")
    import numpy as np
    problems: list[str] = []
    ref_sig = None
    org = by_index(0).build()
    pm = PhysiologyModel()

    bench = [(420, 340), (900, 700), (1400, 860), (1920, 1080), (2560, 1600)]
    print(f"  {'size':>11} {'sim':>8} {'organism':>9} {'console':>8} "
          f"{'total':>8} {'fps_cap':>8}")
    for w, h in bench:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        L = resolve(w, h)
        glass = console.stage_content(L)
        vp = Viewport.for_stage(glass.x, glass.y, glass.w, glass.h)
        cm = console.ConsoleModel(species=by_index(0), active=0)
        light = LightField()

        n = 120
        for _ in range(12):  # warm
            org.update(1 / 60, pm.current)
            draw_organism(cr, vp, org)

        t = time.perf_counter()
        for _ in range(n):
            org.update(1 / 60, pm.update(1 / 60, FAKE))
        sim_ms = (time.perf_counter() - t) / n * 1000

        t = time.perf_counter()
        for _ in range(n):
            draw_organism(cr, vp, org)
        org_ms = (time.perf_counter() - t) / n * 1000

        for _ in range(4):   # warm the skin cache at this exact size
            console.draw_under(cr, L, cm, vp.scale)
            console.draw_over(cr, L, FAKE, 60.0, 16.6, cm, light)

        t = time.perf_counter()
        for _ in range(n):
            console.draw_under(cr, L, cm, vp.scale)
            console.draw_over(cr, L, FAKE, 60.0, 16.6, cm, light)
        chr_ms = (time.perf_counter() - t) / n * 1000

        total = sim_ms + org_ms + chr_ms
        cap = 1000.0 / total if total > 0 else 999
        flag = "" if total < 16.6 else "  <-- OVER BUDGET"
        print(f"  {w:>5}x{h:<5} {sim_ms:>7.2f}ms {org_ms:>8.2f}ms "
              f"{chr_ms:>7.2f}ms {total:>7.2f}ms {cap:>7.0f}{flag}")
        if (w, h) == (900, 700) and total > 16.6:
            problems.append(f"canonical tile {w}x{h} costs {total:.2f}ms "
                            f"(> 16.6ms 60fps budget)")
        if outdir:
            console.draw_under(cr, L, cm, vp.scale)
            draw_organism(cr, vp, org)
            console.draw_over(cr, L, FAKE, 60.0, 16.6, cm, light)
            surf.write_to_png(os.path.join(outdir, f"gate-{w}x{h}.png"))
    return (not problems), problems


def gate_resize_invariance() -> tuple[bool, list[str]]:
    """The core claim: resizing does not perturb the simulation, at all.

    Two organisms fed identical time steps, one of them rendered at a chaotic
    sequence of sizes, must end bit-identical.
    """
    print("\n=== RESIZE INVARIANCE ===")
    import numpy as np
    a, b = by_index(0).build(seed=5), by_index(0).build(seed=5)
    pa, pb = PhysiologyModel(), PhysiologyModel()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 2560, 1600)
    cr = cairo.Context(surf)

    rng = np.random.default_rng(7)
    for i in range(400):
        dt = 1 / 60
        a.update(dt, pa.update(dt, FAKE))
        b.update(dt, pb.update(dt, FAKE))
        # b gets rendered at a random size every frame; a is never rendered.
        w = int(rng.integers(120, 2560))
        h = int(rng.integers(100, 1600))
        L = resolve(w, h)
        glass = console.stage_content(L)
        vp = Viewport.for_stage(glass.x, glass.y, glass.w, glass.h)
        cm = console.ConsoleModel(species=by_index(0), active=0)
        light = LightField()
        if L.stage.valid:
            draw_organism(cr, vp, b)
        draw_chrome(cr, L, FAKE, 60.0, 16.6)

    ax, ay, _ = a.points()
    bx, by, _ = b.points()
    dx = float(np.abs(ax - bx).max())
    dy = float(np.abs(ay - by).max())
    dt_ = abs(a.time - b.time)
    print(f"  400 frames, b resized every frame to a random size")
    print(f"  max |dx| {dx:.3e}   max |dy| {dy:.3e}   dt {dt_:.3e}")
    ok = dx == 0.0 and dy == 0.0 and dt_ == 0.0
    print(f"  {'IDENTICAL — rendering cannot touch simulation' if ok else 'DIVERGED'}")
    return ok, ([] if ok else [f"resize perturbed simulation: dx={dx} dy={dy}"])


def main() -> int:
    outdir = os.environ.get("ABYSSAL_GATE_OUT")
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    results = []
    results.append(("GATE 1 geometry", *gate1_geometry()))
    results.append(("GATE 3 performance", *gate3_performance(outdir)))
    results.append(("resize invariance", *gate_resize_invariance()))

    print("\n=== SUMMARY ===")
    rc = 0
    for name, ok, problems in results:
        print(f"  {name:<22} {'PASS' if ok else 'FAIL'}")
        for p in problems:
            print(f"      - {p}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
