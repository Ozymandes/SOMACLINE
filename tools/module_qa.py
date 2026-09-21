"""Alpha and geometry QA for one generated hardware module.

    python3 tools/module_qa.py assets/modules/masters/m01_shell.png [--sheet out.png]

Reports, from the pixels rather than by eye:

    * alpha histogram: fully clear / fully opaque / partial
    * whether the canvas border is transparent (outside the body)
    * the opaque body's bounding box and aspect
    * interior transparent regions (apertures), largest first
    * dark opaque recesses (candidate bays), largest first

`--sheet` writes the module over magenta and over black side by side, so a
matte, a halo or a checkerboard painted into the RGB is visible at a glance.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
from PIL import Image


def _components(mask: np.ndarray, min_px: int) -> list[tuple[int, int, int, int, int]]:
    """Connected components of a boolean mask: (x, y, w, h, area), largest first."""
    try:
        from scipy import ndimage  # type: ignore
        lab, n = ndimage.label(mask)
        out = []
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            if sl is None:
                continue
            area = int((lab[sl] == i).sum())
            if area >= min_px:
                ys, xs = sl
                out.append((xs.start, ys.start, xs.stop - xs.start,
                            ys.stop - ys.start, area))
        return sorted(out, key=lambda c: -c[4])
    except ImportError:
        pass
    # Pure NumPy fallback: iterative flood fill over a downsampled mask.
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for y0 in range(0, h, 4):
        for x0 in range(0, w, 4):
            if not mask[y0, x0] or seen[y0, x0]:
                continue
            stack = [(y0, x0)]
            seen[y0, x0] = True
            xs0 = xs1 = x0
            ys0 = ys1 = y0
            area = 0
            while stack:
                y, x = stack.pop()
                area += 1
                xs0, xs1 = min(xs0, x), max(xs1, x)
                ys0, ys1 = min(ys0, y), max(ys1, y)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if area >= min_px:
                out.append((xs0, ys0, xs1 - xs0 + 1, ys1 - ys0 + 1, area))
    return sorted(out, key=lambda c: -c[4])


def analyse(path: str) -> dict:
    im = np.asarray(Image.open(path).convert("RGBA"))
    a = im[..., 3].astype(np.int32)
    h, w = a.shape
    rep: dict = {"path": path, "size": (w, h)}
    rep["clear"] = float((a == 0).mean())
    rep["opaque"] = float((a == 255).mean())
    rep["partial"] = float(((a > 0) & (a < 255)).mean())
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    rep["border_max_alpha"] = int(border.max())
    body = a > 127
    ys, xs = np.nonzero(body)
    if len(xs):
        rep["body_bbox"] = (int(xs.min()), int(ys.min()),
                            int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        bw, bh = rep["body_bbox"][2:]
        rep["body_aspect"] = bw / float(bh)
    # interior holes: clear pixels not connected to the canvas border
    clear = a < 16
    comps = _components(clear, max(64, (w * h) // 4000))
    holes = [c for c in comps
             if c[0] > 0 and c[1] > 0 and c[0] + c[2] < w and c[1] + c[3] < h]
    rep["apertures"] = holes[:12]
    lum = (0.2126 * im[..., 0] + 0.7152 * im[..., 1] + 0.0722 * im[..., 2])
    dark = (lum < 40) & (a > 240)
    rep["dark_recesses"] = _components(dark, max(64, (w * h) // 3000))[:24]
    return rep


def sheet(path: str, out: str) -> None:
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    k = min(1.0, 1400.0 / w)
    im = im.resize((int(w * k), int(h * k)), Image.LANCZOS)
    w, h = im.size
    s = Image.new("RGBA", (w * 2 + 20, h), (40, 40, 40, 255))
    for i, bg in enumerate(((255, 0, 255, 255), (0, 0, 0, 255))):
        tile = Image.new("RGBA", (w, h), bg)
        tile.alpha_composite(im)
        s.paste(tile, (i * (w + 20), 0))
    s.convert("RGB").save(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--sheet")
    o = ap.parse_args()
    r = analyse(o.png)
    print(f"{r['path']}  {r['size'][0]}x{r['size'][1]}")
    print(f"  alpha: clear {r['clear']:.3f}  opaque {r['opaque']:.3f}  "
          f"partial {r['partial']:.3f}  border max {r['border_max_alpha']}")
    if "body_bbox" in r:
        print(f"  body bbox {r['body_bbox']}  aspect {r['body_aspect']:.3f}")
    print(f"  apertures ({len(r['apertures'])}):")
    for c in r["apertures"]:
        print(f"    x{c[0]} y{c[1]} {c[2]}x{c[3]}  area {c[4]}")
    print(f"  dark recesses ({len(r['dark_recesses'])}):")
    for c in r["dark_recesses"]:
        print(f"    x{c[0]} y{c[1]} {c[2]}x{c[3]}  area {c[4]}")
    if o.sheet:
        sheet(o.png, o.sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
