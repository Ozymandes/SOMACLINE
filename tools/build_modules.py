"""Build the six runtime hardware modules from their accepted 2K masters.

    python3 tools/build_modules.py            # build assets/sprites/module/*.png
    python3 tools/build_modules.py --measure  # also print recess components

ONE deterministic step per module, nothing painted by hand:

  1. alpha contrast stretch   The generator emits dithered alpha (background
                              0-1, body 253-254). <=8 -> 0 and >=248 -> 255;
                              real antialiased edges keep their ramp.
  2. crop to the opaque body  so the sprite rect IS the module's outer edge.
  3. aperture trueing         (selector only) the generator cannot hold an
                              exact pitch. Every key well is re-cut to one
                              identical rectangle on a least-squares uniform
                              pitch fitted to the wells it drew. The approved
                              keys are drawn on exactly this pitch and cover
                              aperture + lip, so the trueing is invisible and
                              the wells are provably identical.
  4. downsample (Lanczos)     to a runtime size that covers ARCHIVE at 2560px
                              without shipping 2K masters to every frame.

The masters live in assets/modules/masters/ and are the source of truth.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.module_qa import _components  # noqa: E402

MASTERS = ROOT / "assets" / "modules" / "masters"
OUT = ROOT / "assets" / "sprites" / "module"

#: accepted master -> (runtime name, runtime width)
ACCEPTED = {
    "m01_shell_g1.png": ("shell", 1600),
    "m02_header_g1.png": ("header", 1800),
    "m03_observation_g1.png": ("observation", 1300),
    "m04_rack_g1.png": ("rack", 900),
    "m05_selector_g4.png": ("selector", 1400),
    "m06_footer_g1.png": ("footer", 1800),
}


def stretch_alpha(a: np.ndarray) -> np.ndarray:
    a = a.astype(np.float32)
    out = np.clip((a - 8.0) * (255.0 / 240.0), 0.0, 255.0)
    return out.round().astype(np.uint8)


def crop_body(im: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    ys, xs = np.nonzero(im[..., 3] > 127)
    x0, x1 = max(0, xs.min() - 2), min(im.shape[1], xs.max() + 3)
    y0, y1 = max(0, ys.min() - 2), min(im.shape[0], ys.max() + 3)
    return im[y0:y1, x0:x1].copy(), (x0, y0)


def _holes(a: np.ndarray, min_area: int) -> list[tuple[int, int, int, int, int]]:
    h, w = a.shape
    comps = _components(a < 16, min_area)
    return [c for c in comps
            if c[0] > 0 and c[1] > 0 and c[0] + c[2] < w and c[1] + c[3] < h]


def true_selector(im: np.ndarray) -> dict:
    """Re-cut the five key wells to one size on one exact pitch."""
    a = im[..., 3]
    holes = _holes(a, 2000)
    wells = sorted([c for c in holes if 0.8 < c[2] / c[3] < 1.3],
                   key=lambda c: c[0])
    if len(wells) != 5:
        raise SystemExit(f"selector: expected 5 key wells, found {len(wells)}")
    cx = np.array([c[0] + c[2] / 2.0 for c in wells])
    cy = np.array([c[1] + c[3] / 2.0 for c in wells])
    i = np.arange(5, dtype=np.float64)
    pitch, x0 = np.polyfit(i, cx, 1)
    ww = max(c[2] for c in wells)
    wh = max(c[3] for c in wells)
    ycen = float(np.median(cy))
    resid = cx - (x0 + pitch * i)
    for k in range(5):
        c = x0 + pitch * k
        xa, xb = int(round(c - ww / 2.0)), int(round(c + ww / 2.0))
        ya, yb = int(round(ycen - wh / 2.0)), int(round(ycen + wh / 2.0))
        im[ya:yb, xa:xb, 3] = 0
        im[ya:yb, xa:xb, :3] = 0
    others = sorted([c for c in holes if not (0.8 < c[2] / c[3] < 1.3)],
                    key=lambda c: c[0])
    return {"pitch": float(pitch), "x0": float(x0), "cy": ycen,
            "well_w": int(ww), "well_h": int(wh),
            "max_residual": float(np.abs(resid).max()),
            "aux": [list(map(int, c[:4])) for c in others]}


def build(measure: bool) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    for src, (name, width) in ACCEPTED.items():
        im = np.asarray(Image.open(MASTERS / src).convert("RGBA")).copy()
        im[..., 3] = stretch_alpha(im[..., 3])
        # A fully clear pixel carries no colour: zero it, so no downsampling
        # filter can pull stray RGB out of the background into an edge.
        im[im[..., 3] == 0, :3] = 0
        im, _ = crop_body(im)
        meta: dict = {}
        if name == "selector":
            meta = true_selector(im)
        h0, w0 = im.shape[:2]
        k = width / float(w0)
        out = Image.fromarray(im, "RGBA")
        # premultiply for the resample, so edges do not darken or fringe
        arr = np.asarray(out).astype(np.float32)
        arr[..., :3] *= arr[..., 3:4] / 255.0
        pm = Image.fromarray(arr.round().astype(np.uint8), "RGBA")
        size = (width, max(1, int(round(h0 * k))))
        pm = pm.resize(size, Image.LANCZOS)
        arr = np.asarray(pm).astype(np.float32)
        al = arr[..., 3:4]
        rgb = np.where(al > 0, arr[..., :3] * 255.0 / np.maximum(al, 1.0), 0.0)
        arr = np.concatenate([np.clip(rgb, 0, 255), al], axis=2)
        res = arr.round().astype(np.uint8)
        # Lanczos rings: restore exact 0 / 255 where the master was exact.
        a = res[..., 3]
        a[a <= 3] = 0
        a[a >= 252] = 255
        res[a == 0, :3] = 0
        Image.fromarray(res, "RGBA").save(OUT / f"{name}.png", optimize=True)
        entry = {"master": src, "size": list(size), "scale": k,
                 "master_crop": [w0, h0]}
        if meta:
            entry["trueing"] = {kk: (v * k if isinstance(v, float) and kk != "max_residual"
                                     else v) for kk, v in meta.items()}
            entry["trueing"]["max_residual_px"] = meta["max_residual"] * k
        report[name] = entry
        print(f"{name:12s} {src:26s} -> {size[0]}x{size[1]}")
        if measure:
            holes = _holes(res[..., 3], 150)
            lum = (0.2126 * res[..., 0] + 0.7152 * res[..., 1]
                   + 0.0722 * res[..., 2])
            dark = _components((lum < 40) & (res[..., 3] > 240), 120)
            print("   apertures:", [c[:4] for c in holes][:12])
            print("   recesses :", [c[:4] for c in dark][:40])
    (OUT / "build.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true")
    build(ap.parse_args().measure)
