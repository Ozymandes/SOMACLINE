#!/usr/bin/env python3
"""imgdiff.py A.png B.png [--map out.png] — mean/p99/within-12 + worst regions."""
import sys
import numpy as np
from PIL import Image

a = np.asarray(Image.open(sys.argv[1]).convert("RGB")).astype(np.int16)
b = np.asarray(Image.open(sys.argv[2]).convert("RGB")).astype(np.int16)
if a.shape != b.shape:
    print(f"SIZE MISMATCH {a.shape} vs {b.shape}"); sys.exit(2)
d = np.abs(a - b).max(axis=2)
mean, p99, mx = d.mean(), np.percentile(d, 99), d.max()
within = (d <= 12).mean() * 100
print(f"mean abs {mean:.4f}/255   p99 {p99:.0f}   max {mx}   within 12: {within:.3f}%   "
      f"exact: {(d==0).mean()*100:.3f}%")
# worst 32x32 tiles
H, W = d.shape
tiles = []
for y in range(0, H - 31, 32):
    for x in range(0, W - 31, 32):
        t = d[y:y+32, x:x+32]
        if t.max() > 12:
            tiles.append((t.mean(), x, y, int(t.max())))
tiles.sort(reverse=True)
if tiles:
    print(f"{len(tiles)} tiles (32x32) exceed 12; worst:")
    for m, x, y, mxt in tiles[:8]:
        print(f"   at {x:5d},{y:5d}  mean {m:6.2f}  max {mxt}")
else:
    print("no 32x32 tile exceeds 12")
if "--map" in sys.argv:
    out = sys.argv[sys.argv.index("--map") + 1]
    Image.fromarray(np.clip(d * 8, 0, 255).astype(np.uint8)).save(out)
    print("map:", out)
