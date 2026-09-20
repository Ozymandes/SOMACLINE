"""Generated fascia panels, and the bays that were manufactured into them.

WHY THIS EXISTS
---------------
The hardware library was generated as REAL PANELS: a header fascia with four
recessed information bays over a three-bay rail, a telemetry module shell with
a plaque, a title bay, four annunciator wells and three display recesses, an
archive rail with a badge boss and eight bays. Those recesses are the whole
point of the assets. Drawing the panel and then placing text wherever the
layout felt like throws away exactly the thing that was paid for, and it is
what makes a UI read as an overlay: the type has no physical home.

A `Fascia` is therefore a sprite PLUS the measured source-pixel rectangle of
every recess cut into it. `place()` scales the panel to a target rect and
returns the same bays in widget pixels. Text is then drawn INSIDE a bay, with
the bay's own padding and baseline - so a label cannot land on a bevel crest,
cannot drift between compartments, and cannot spill onto bare metal.

HOW THE SCALING WORKS
---------------------
Straight stretching would ovalise the corner screws, so a fascia is drawn as a
three-band slice on each axis: the end caps (screws, frame edge) scale
uniformly by `k`, and only the middle stretches. `place()` maps the bays
through exactly that piecewise transform, so the bays a caller is handed are
where the metal actually is, at every size, by construction.

Bay rectangles were measured off the runtime sprites by connected-component
analysis of their recesses (luminance < 48 under opaque alpha), not by eye.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.layout import Rect
from .surface import MIN_BORDER_SCALE, NineSlice, draw_nine, sprite_size


@dataclass(frozen=True, slots=True)
class Placed:
    """One fascia positioned on the panel, with its bays in widget pixels."""

    fascia: "Fascia"
    x: float
    y: float
    w: float
    h: float
    kx: float
    ky: float

    def bay(self, key: str) -> Rect:
        """The recess named `key`, in widget pixels. Invalid Rect if absent."""
        src = self.fascia.bays.get(key)
        if src is None:
            return Rect(0.0, 0.0, 0.0, 0.0)
        sx, sy, sw, sh = src
        x0 = self._mx(sx)
        x1 = self._mx(sx + sw)
        y0 = self._my(sy)
        y1 = self._my(sy + sh)
        return Rect(x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))

    def point(self, sx: float, sy: float) -> tuple[float, float]:
        return (self._mx(sx), self._my(sy))

    def scale_y(self, src_len: float) -> float:
        """A source length that must stay round (a lamp diameter), in pixels."""
        return src_len * self.ky

    # -- the piecewise axis maps -------------------------------------------
    def _mx(self, v: float) -> float:
        f = self.fascia
        return self.x + _axis(v, f.sw, f.cap_l, f.cap_r, self.w, self.kx)

    def _my(self, v: float) -> float:
        f = self.fascia
        return self.y + _axis(v, f.sh, f.cap_t, f.cap_b, self.h, self.ky)


def _axis(v: float, size: int, cap0: int, cap1: int,
          dst: float, k: float) -> float:
    """Map a source coordinate through one axis of the three-band slice."""
    d0 = cap0 * k
    d1 = cap1 * k
    if v <= cap0:
        return v * k
    if v >= size - cap1:
        return dst - (size - v) * k
    mid_s = size - cap0 - cap1
    if mid_s <= 0.0:
        return d0
    return d0 + (v - cap0) * ((dst - d0 - d1) / mid_s)


@dataclass(frozen=True, slots=True)
class Fascia:
    """A generated panel and the recesses manufactured into it."""

    name: str
    sw: int
    sh: int
    cap_l: int
    cap_t: int
    cap_r: int
    cap_b: int
    #: key -> (x, y, w, h) in SOURCE pixels.
    bays: dict[str, tuple[float, float, float, float]]

    @property
    def aspect(self) -> float:
        return self.sw / float(self.sh)

    def nine(self) -> NineSlice:
        return NineSlice(self.name, self.cap_l, self.cap_t,
                         self.cap_r, self.cap_b)

    def place(self, r: Rect) -> Placed:
        """Position (do not draw) the fascia in `r`, returning its bay map."""
        w = max(1.0, r.w)
        h = max(1.0, r.h)
        # One factor per axis, clamped exactly as surface._shrink clamps the
        # drawn slice, so the map and the metal agree at every size.
        k = min(w / float(self.sw), h / float(self.sh))
        k = max(MIN_BORDER_SCALE, min(1.0, k))
        kx = ky = k
        lr = (self.cap_l + self.cap_r) * kx
        tb = (self.cap_t + self.cap_b) * ky
        if lr > w * 0.66 and lr > 0.0:
            kx *= max(0.0, (w * 0.66) / lr)
        if tb > h * 0.66 and tb > 0.0:
            ky *= max(0.0, (h * 0.66) / tb)
        return Placed(self, r.x, r.y, w, h, kx, ky)

    def draw(self, cr, r: Rect, alpha: float = 1.0) -> Placed:
        """Draw the panel into `r` and hand back its bays. One call, one panel."""
        p = self.place(r)
        if sprite_size(self.name)[0]:
            draw_nine(cr, self.nine(), r.x, r.y, r.w, r.h, alpha)
        return p


# --------------------------------------------------------------------------
# The measured library
# --------------------------------------------------------------------------
#: Top command fascia. Four information bays over a three-bay status rail.
#: Bay 'live' carries a lamp boss and a small window cast into the metal.
HEADER = Fascia(
    name="frame/header_fascia", sw=1600, sh=214,
    cap_l=66, cap_t=22, cap_r=64, cap_b=24,
    bays={
        "title":   (78.0, 29.0, 545.0, 95.0),
        "epithet": (635.0, 29.0, 401.0, 95.0),
        "live":    (1048.0, 29.0, 239.0, 95.0),
        "clock":   (1299.0, 28.0, 225.0, 96.0),
        "rail_0":  (78.0, 140.0, 480.0, 49.0),
        "rail_1":  (570.0, 140.0, 459.0, 49.0),
        "rail_2":  (1042.0, 140.0, 482.0, 49.0),
        # sub-features inside 'live', cast into the asset itself
        "live_lamp":   (1086.0, 46.0, 60.0, 60.0),
        "live_window": (1145.0, 53.0, 114.0, 46.0),
    },
)

#: Bottom archive rail: a machined badge boss, eight key/value bays, and a
#: three-line block at the right end for the standing motto.
FOOTER = Fascia(
    name="frame/footer_rail", sw=1600, sh=241,
    cap_l=52, cap_t=40, cap_r=50, cap_b=44,
    bays={
        "badge":  (63.0, 52.0, 138.0, 137.0),
        "bay_0":  (212.0, 52.0, 127.0, 137.0),
        "bay_1":  (350.0, 52.0, 126.0, 137.0),
        "bay_2":  (487.0, 52.0, 126.0, 137.0),
        "bay_3":  (624.0, 52.0, 127.0, 137.0),
        "bay_4":  (761.0, 52.0, 127.0, 137.0),
        "bay_5":  (899.0, 52.0, 127.0, 137.0),
        "bay_6":  (1037.0, 52.0, 127.0, 137.0),
        "bay_7":  (1175.0, 52.0, 127.0, 137.0),
        "motto":  (1316.0, 52.0, 222.0, 137.0),
    },
)

#: One telemetry module. This asset is the whole of CRITICAL PROBLEM 04's
#: answer: it was generated with a large graph recess, an equally large
#: numeric recess, a compact status recess, an identity plaque, a title bay
#: and four annunciator wells - the exact instrument hierarchy the reference
#: console uses. Everything is placed into those bays, nothing beside them.
MODULE = Fascia(
    name="frame/module_shell", sw=1152, sh=371,
    cap_l=76, cap_t=20, cap_r=30, cap_b=30,
    bays={
        "plaque":  (83.0, 19.0, 199.0, 59.0),
        "title":   (287.0, 21.0, 589.0, 52.0),
        "graph":   (82.0, 111.0, 379.0, 226.0),
        "numeric": (501.0, 111.0, 395.0, 226.0),
        "status":  (936.0, 110.0, 188.0, 227.0),
        "rail":    (15.0, 62.0, 27.0, 298.0),
        "lamp_0":  (896.0, 27.0, 42.0, 42.0),
        "lamp_1":  (957.0, 27.0, 42.0, 42.0),
        "lamp_2":  (1017.0, 27.0, 42.0, 42.0),
        "lamp_3":  (1077.0, 27.0, 42.0, 42.0),
        # The strip of raised metal between the annunciator wells and the
        # display row. The legends are engraved here, as on the reference
        # console; it is the only place they fit without touching a bevel.
        "legend":  (880.0, 69.0, 260.0, 11.0),
    },
)

#: Four-bay mounting rack. Generated, and deliberately NOT used at runtime:
#: it duplicates the module shell's own rail/screw mounting structure, and
#: nesting the two costs ~20% of the graph width for a second frame the
#: approved reference does not have. Kept here so the audit is explicit.
RACK = Fascia(
    name="frame/telemetry_rack", sw=896, sh=1120,
    cap_l=52, cap_t=25, cap_r=53, cap_b=63,
    bays={
        "bay_0": (115.0, 64.0, 711.0, 227.0),
        "bay_1": (115.0, 318.0, 711.0, 220.0),
        "bay_2": (115.0, 565.0, 711.0, 218.0),
        "bay_3": (115.0, 811.0, 711.0, 222.0),
    },
)

#: The five-position selector fascia: engraved label ledges above each well,
#: the key wells themselves, and a lamp boss under each.
SELECTOR_BANK = Fascia(
    name="selector/bank_empty", sw=1280, sh=351,
    cap_l=40, cap_t=14, cap_r=40, cap_b=14,
    bays={},
)
