"""Device scale for every derived-pixel cache.

On a scaled display (Omarchy at 1.6x) GTK hands the draw function a cairo
context whose target has a device scale. A cache surface allocated at LOGICAL
size is then upsampled on every blit - soft metal, soft type. Every cache in
this program therefore allocates through `surface()`, at device resolution,
and carries `scale()` in its key.

The host sets the scale once per frame from the real target. Headless QA
leaves it at 1.0 (or sets it explicitly to test HiDPI).
"""

from __future__ import annotations

import math

import cairo

_DS = 1.0


def set_scale(s: float) -> None:
    """Quantised to quarter steps so a fractional scale cannot churn caches."""
    global _DS
    _DS = max(1.0, min(4.0, round(float(s) * 4.0) / 4.0))


def scale() -> float:
    return _DS


def surface(w: float, h: float) -> cairo.ImageSurface:
    """An ARGB32 surface of LOGICAL size w x h at device resolution."""
    ds = _DS
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                           max(1, int(math.ceil(w * ds))),
                           max(1, int(math.ceil(h * ds))))
    s.set_device_scale(ds, ds)
    return s


def from_context(cr) -> float:
    """The device scale of a context's target (1.0 if it has none)."""
    try:
        return float(cr.get_target().get_device_scale()[0])
    except Exception:
        return 1.0
