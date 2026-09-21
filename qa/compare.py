#!/usr/bin/env python3
"""Deterministic comparison against references/Abbysal_Final.png.

    python3 qa/compare.py OUTDIR

Renders the live application headless at the reference's own size
(1448x1086), then writes:

    compare_side.png     reference | application, same scale
    compare_overlay.png  50% registration overlay
    compare_modules.png  application with the reference module rectangles
                         (core.layout REF_*) outlined, to check seating

and prints the per-module rectangle deltas between the reference geometry and
the layout the application actually resolved.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

from abyssal.core import layout as LY  # noqa: E402
from qa.offscreen import frame  # noqa: E402

REF = os.path.join(ROOT, "references", "Abbysal_Final.png")
W, H = 1448, 1086


def app_image(specimen: int = 0) -> Image.Image:
    surf = frame(W, H, specimen)
    buf = bytes(surf.get_data())
    im = Image.frombuffer("RGBA", (W, H), buf, "raw", "BGRA", 0, 1)
    return im.convert("RGB")


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "qa-out"
    os.makedirs(out, exist_ok=True)
    ref = Image.open(REF).convert("RGB").resize((W, H))
    app = app_image()
    side = Image.new("RGB", (W * 2 + 16, H), (20, 20, 20))
    side.paste(ref, (0, 0))
    side.paste(app, (W + 16, 0))
    side.save(os.path.join(out, "compare_side.png"))
    Image.blend(ref, app, 0.5).save(os.path.join(out, "compare_overlay.png"))

    L = LY.resolve(W, H)
    got = {"header": L.header, "stage": L.stage, "selector": L.controls,
           "rack": L.readout, "footer": L.footer}
    want = {"header": LY.REF_HEADER, "stage": LY.REF_STAGE,
            "selector": LY.REF_SELECTOR, "rack": LY.REF_RACK,
            "footer": LY.REF_FOOTER}
    marked = app.copy()
    d = ImageDraw.Draw(marked)
    print(f"{'module':10s} {'reference':>24s} {'application':>24s}  max|d|")
    worst = 0.0
    for k, (x, y, w, h) in want.items():
        r = got[k]
        d.rectangle([x, y, x + w, y + h], outline=(255, 60, 200), width=2)
        d.rectangle([r.x, r.y, r.right, r.bottom], outline=(60, 255, 120), width=1)
        delta = max(abs(r.x - x), abs(r.y - y), abs(r.right - (x + w)),
                    abs(r.bottom - (y + h)))
        worst = max(worst, delta)
        print(f"{k:10s} {x:5.0f},{y:5.0f} {w:5.0f}x{h:<5.0f}  "
              f"{r.x:5.0f},{r.y:5.0f} {r.w:5.0f}x{r.h:<5.0f}  {delta:5.1f}")
    marked.save(os.path.join(out, "compare_modules.png"))
    rack_margin = L.chassis.right - L.readout.right
    ref_margin = (LY.REF_CHASSIS[0] + LY.REF_CHASSIS[2]) - (LY.REF_RACK[0] + LY.REF_RACK[2])
    print(f"rack right margin: app {rack_margin:.1f}px, reference {ref_margin:.1f}px")
    print(f"worst module-edge delta: {worst:.1f}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
