"""Accumulating point-field rasteriser.

WHY THE ORGANISMS ARE NOT DRAWN AS PATHS
----------------------------------------
The source sketches are point clouds: between ten and forty thousand
`point()` calls a frame, composited additively at a low alpha, so that where
the equation crowds the plane the image brightens. That accumulation IS the
creature - it is what gives these forms their volume and their translucency.
Asking Cairo for forty thousand arcs a frame would cost roughly a second;
asking it for a polyline through them would draw a different animal.

So points are accumulated in NumPy and the result is blitted as one surface:

    equation  ->  (x, y, weight) in source coordinates
    transform ->  world units, then stage pixels
    splat     ->  np.bincount over a flat pixel index
    map       ->  intensity to premultiplied ARGB32
    blit      ->  one cairo surface

`np.bincount` is what makes this cheap: it is a single C pass over the index
array, where `np.add.at` on the same data is two orders of magnitude slower.

PERSISTENCE
-----------
One source composites with `background(6, 96)` - a translucent wash rather
than a clear - so each frame keeps ~62% of the last. That is reproduced by
retaining the accumulator between frames rather than by fading pixels, which
keeps the trail in the same space the points are counted in.

STATE
-----
Everything here is derived pixels, keyed by the exact size asked for. It
holds no simulation state and cannot influence one; dropping every buffer
between two frames would change performance and nothing else.

THE OUTPUT IMAGE IS NEVER REUSED
--------------------------------
The accumulator and the colour-map scratch are cached, because only NumPy
ever touches them. The cairo surface the result is handed over in is NOT: it
is allocated fresh every frame.

That is not waste, it is a correctness rule. Once a surface has been painted
into a GTK frame, GSK may hold a SNAPSHOT of it for as long as it likes, and
Cairo forbids marking a snapshotted surface dirty - it aborts the process
(`cairo_surface_mark_dirty_rectangle: !_cairo_surface_has_snapshots`).
Rewriting one cached surface every frame passed every headless gate, because
an offscreen ImageSurface never snapshots its sources, and crashed the real
window within a second. A new surface per frame means no pixel a snapshot
can see is ever written again. The backing store comes from `np.empty`, so
there is no memset: every byte of the image area is overwritten anyway.
"""

from __future__ import annotations

from collections import OrderedDict

import cairo
import numpy as np

#: Bounded cache of accumulators and output images, keyed by (w, h, species).
_BUFFERS: "OrderedDict[tuple, dict]" = OrderedDict()
_BUFFER_LIMIT = 3


def _buffers(key: tuple, w: int, h: int) -> dict:
    ent = _BUFFERS.get(key)
    if ent is not None and ent["w"] == w and ent["h"] == h:
        _BUFFERS.move_to_end(key)
        return ent
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    ent = {
        "w": w, "h": h, "stride": stride,
        # The accumulator persists between frames for trailing species.
        "acc": np.zeros(w * h, dtype=np.float32),
        # Physiology channels, counted in the same space: excitation-weighted
        # density (E) and hue-weighted excitation (H). Their ratios per pixel
        # are the local pulse strength and colour.
        "acc_e": np.zeros(w * h, dtype=np.float32),
        "acc_h": np.zeros(w * h, dtype=np.float32),
        # Scratch for the colour map, so a frame allocates nothing per pixel.
        "a": np.zeros((h, w), dtype=np.float32),
        "m": np.zeros((h, w), dtype=np.float32),
        "tmp": np.zeros((h, w), dtype=np.float32),
    }
    _BUFFERS[key] = ent
    while len(_BUFFERS) > _BUFFER_LIMIT:
        _BUFFERS.popitem(last=False)
    return ent


def clear_cache() -> None:
    """Drop every buffer. Purely a memory operation; changes no output."""
    _BUFFERS.clear()


def accumulate(ent: dict, px: np.ndarray, py: np.ndarray,
               weight, persist: float) -> np.ndarray:
    """Count points into the accumulator and return it.

    Points outside the buffer are dropped rather than clamped: clamping would
    pile every off-canvas sample onto the border, which is exactly the bright
    edge artefact the originals do not have.
    """
    w, h = ent["w"], ent["h"]
    acc = ent["acc"]
    if persist > 0.0:
        acc *= persist
    else:
        acc[:] = 0.0

    ix = px.astype(np.int32, copy=False)
    iy = py.astype(np.int32, copy=False)
    ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    if not ok.any():
        return acc
    idx = iy[ok].astype(np.int64) * w + ix[ok]
    if np.isscalar(weight):
        acc += (np.bincount(idx, minlength=w * h) * float(weight)).astype(
            np.float32)
    else:
        acc += np.bincount(idx, weights=weight[ok], minlength=w * h).astype(
            np.float32)
    return acc


def accumulate_physio(ent: dict, px: np.ndarray, py: np.ndarray, weight,
                      excite: np.ndarray, hue: np.ndarray,
                      persist: float) -> None:
    """Count density, excitation and hue into the three accumulators.

    One index computation serves all three counts. A trailing species decays
    all three together, so the colour of its trail stays the colour of the
    pulse that laid it down.
    """
    w, h = ent["w"], ent["h"]
    acc, acc_e, acc_h = ent["acc"], ent["acc_e"], ent["acc_h"]
    if persist > 0.0:
        acc *= persist
        acc_e *= persist
        acc_h *= persist
    ix = px.astype(np.int32, copy=False)
    iy = py.astype(np.int32, copy=False)
    ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    if not ok.any():
        if persist <= 0.0:
            acc[:] = 0.0
            acc_e[:] = 0.0
            acc_h[:] = 0.0
        return
    idx = iy[ok] * np.int32(w) + ix[ok]
    wt = (np.full(idx.size, float(weight), dtype=np.float32)
          if np.isscalar(weight) else weight[ok].astype(np.float32, copy=False))
    we = wt * excite[ok]
    weh = we * hue[ok]
    n = w * h
    if persist > 0.0:
        acc += np.bincount(idx, weights=wt, minlength=n)
        acc_e += np.bincount(idx, weights=we, minlength=n)
        acc_h += np.bincount(idx, weights=weh, minlength=n)
    else:
        # a clearing species: the count IS the frame, no zero-then-add
        acc[:] = np.bincount(idx, weights=wt, minlength=n)
        acc_e[:] = np.bincount(idx, weights=we, minlength=n)
        acc_h[:] = np.bincount(idx, weights=weh, minlength=n)


#: Excitation -> extra luminance, and -> how far a pixel's colour leaves the
#: resting density ramp for the pulse palette.
EXCITE_GLOW = 1.15
EXCITE_MIX = 1.6


def paint_physio(cr, ent: dict, x0: float, y0: float,
                 rgb_lo: tuple[float, float, float],
                 rgb_hi: tuple[float, float, float],
                 lut: np.ndarray, ink: float, knee: float = 2.2,
                 scale_up: float = 1.0) -> None:
    """Map the three accumulators to colour, on LIT PIXELS ONLY, and blit.

    A creature covers a small fraction of its field, so the colour map runs
    over the pixels the equation actually reached - np.flatnonzero of the
    density - and scatters the packed result into a zeroed image. Per pixel:

        alpha   soft-knee of density plus the pulse's extra glow
        base    the resting ramp: deep-field blue -> core cyan by density
        pulse   the palette at the pixel's own mean hue
        colour  base -> pulse by the pixel's excitation fraction

    so the wavefront is coloured where it is, and nowhere else.
    """
    w, h = ent["w"], ent["h"]
    acc, acc_e, acc_h = ent["acc"], ent["acc_e"], ent["acc_h"]
    # Below this density a pixel rounds to zero alpha. Using it as the lit
    # threshold also keeps a trailing species' decayed tail - which would
    # otherwise shrink toward denormals forever - out of the colour pass.
    thr = np.float32(0.4 / 255.0 / max(ink, 1e-6))
    nz = np.flatnonzero(acc > thr)
    stride = ent["stride"]
    backing = np.zeros(stride * h, dtype=np.uint8)
    if nz.size:
        v = acc[nz]
        e = acc_e[nz]
        hs = acc_h[nz]
        a = v + e * np.float32(EXCITE_GLOW)
        a *= np.float32(-ink)
        np.exp(a, out=a)
        np.subtract(np.float32(1.0), a, out=a)
        if knee != 1.0:
            np.power(a, np.float32(1.0 / knee), out=a)
        np.clip(a, 0.0, 1.0, out=a)
        m = v * np.float32(ink * 0.55)
        np.clip(m, 0.0, 1.0, out=m)
        x = e / v
        x *= np.float32(EXCITE_MIX)
        np.clip(x, 0.0, 1.0, out=x)
        np.maximum(e, np.float32(1e-9), out=e)
        hs /= e
        hs *= np.float32(lut.shape[0] - 1)
        li = hs.astype(np.intp)
        np.clip(li, 0, lut.shape[0] - 1, out=li)
        pix = np.zeros(nz.size, dtype=np.uint32)
        a255 = a * np.float32(255.0)
        for ch, shift in ((2, 0), (1, 8), (0, 16)):
            lo, hi = rgb_lo[ch], rgb_hi[ch]
            c = m * np.float32(hi - lo)
            c += np.float32(lo)
            c += (lut[li, ch] - c) * x
            c *= a255
            pix |= c.astype(np.uint32) << np.uint32(shift)
        pix |= a255.astype(np.uint32) << np.uint32(24)
        if stride == w * 4:
            backing.view(np.uint32)[nz] = pix
        else:  # pragma: no cover - ARGB32 stride is always w*4
            rows = backing.reshape(h, stride)[:, :w * 4].reshape(h, w, 4)
            rows.view(np.uint32).reshape(h * w)[nz] = pix
    surf = cairo.ImageSurface.create_for_data(
        memoryview(backing), cairo.FORMAT_ARGB32, w, h, stride)
    cr.save()
    if scale_up != 1.0:
        cr.translate(x0, y0)
        cr.scale(scale_up, scale_up)
        cr.set_source_surface(surf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
    else:
        cr.set_source_surface(surf, round(x0), round(y0))
    cr.paint()
    cr.restore()


def paint(cr, ent: dict, acc: np.ndarray, x0: float, y0: float,
          rgb_lo: tuple[float, float, float],
          rgb_hi: tuple[float, float, float],
          ink: float, knee: float = 2.2, scale_up: float = 1.0) -> None:
    """Map accumulated counts to colour and blit the field.

    Two colours, not one. A single tint makes a point field look like fog;
    ramping from the deep-field blue at one hit toward the core colour where
    the equation crowds gives the creature the internal structure the
    references have, using only the density the maths already produced.
    """
    w, h = ent["w"], ent["h"]
    v = acc.reshape(h, w)
    a, m = ent["a"], ent["m"]
    # Soft-knee response: linear while sparse, compressing as it saturates,
    # which is what an additive alpha composite does in the original.
    np.multiply(v, np.float32(-ink), out=a)
    np.exp(a, out=a)
    np.subtract(np.float32(1.0), a, out=a)
    if knee != 1.0:
        np.power(a, np.float32(1.0 / knee), out=a)
    np.clip(a, 0.0, 1.0, out=a)
    # Mix toward the core colour with density.
    np.multiply(v, np.float32(ink * 0.55), out=m)
    np.clip(m, 0.0, 1.0, out=m)
    # A fresh image per frame - see "THE OUTPUT IMAGE IS NEVER REUSED".
    stride = ent["stride"]
    backing = np.empty(stride * h, dtype=np.uint8)
    out = backing.reshape(h, stride // 4, 4)[:, :w, :]
    tmp = ent["tmp"]
    for ch, lo, hi in ((0, rgb_lo[2], rgb_hi[2]),
                       (1, rgb_lo[1], rgb_hi[1]),
                       (2, rgb_lo[0], rgb_hi[0])):
        # Premultiplied, as cairo ARGB32 requires.
        np.multiply(m, np.float32((hi - lo) * 255.0), out=tmp)
        np.add(tmp, np.float32(lo * 255.0), out=tmp)
        np.multiply(tmp, a, out=tmp)
        out[:, :, ch] = tmp
    np.multiply(a, np.float32(255.0), out=tmp)
    out[:, :, 3] = tmp
    # The surface keeps `backing` alive for as long as anything references
    # it, including a GSK snapshot, so the memory stays valid after return.
    surf = cairo.ImageSurface.create_for_data(
        memoryview(backing), cairo.FORMAT_ARGB32, w, h, stride)
    cr.save()
    if scale_up != 1.0:
        cr.translate(x0, y0)
        cr.scale(scale_up, scale_up)
        cr.set_source_surface(surf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
    else:
        cr.set_source_surface(surf, round(x0), round(y0))
    cr.paint()
    cr.restore()


def field_buffers(key: str, w: int, h: int) -> dict:
    return _buffers((key, w, h), w, h)
