"""The five source equations, and their NumPy ports.

WHAT THIS FILE IS
-----------------
Each specimen in this console is a published generative sketch - a "one
tweet" p5.js program that draws a creature by evaluating one closed-form
expression tens of thousands of times per frame. This module holds the
ORIGINAL SOURCE verbatim next to its port, so the two can be read against
each other. Nothing here is an approximation of a screenshot; the shape on
screen is the shape the equation produces.

WHY THE PORTS LOOK LIKE THE ORIGINALS
-------------------------------------
Deliberately. The temptation with code this dense is to "clean it up" -
name the subexpressions, hoist the constants, factor the branches. Every one
of those edits is a chance to change the creature. So each port keeps the
original's variable names (k, e, d, o, q, c), its operator order, and its
loop bounds; the only changes are the ones NumPy forces:

    mag(a, b)        -> np.hypot(a, b)
    the for(i=N;i--;) -> a single vectorised i = arange(N)
    ternaries        -> np.where
    point(x, y)      -> one sample in the returned array
    circle(x,y,d)    -> one sample carrying a weight, see `Sample.weight`

THE GLOBAL `i`
--------------
Sources 02 and 04 read the render loop's counter `i` from inside the
function - 02 for its `d` default argument, 04 for `i%4*8`. That is not an
accident of golfing, it is load-bearing: in 04 it is what separates the four
plumes. The ports keep it by evaluating over the same index array.

Descending (`i--`) versus ascending (`arange`) changes only the ORDER points
are emitted in, not the set. The originals composite every point with the
same alpha, so order cannot affect the image.

COORDINATES
-----------
Every source draws into its own 400x400 canvas. `to_world()` maps that square
onto the Abyssal logical world and nothing else - no reframing, no
recentring, no aspect correction. The morphology that arrives on the glass is
the morphology the equation drew.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

#: Every source authors into this square.
SRC = 400.0
SRC_MID = SRC / 2.0

#: Half-extent, in world units, that a creature's own bounding box is fitted
#: to. Deliberately inside the 460 design radius: a specimen that reaches the
#: scope's outermost ring touches the glass and reads as CLIPPED rather than
#: as contained, and the field's graticule then has nothing to measure it
#: against. This leaves roughly a fifth of the radius as observation margin.
WORLD_FIT = 372.0


@dataclass(frozen=True, slots=True)
class Sample:
    """One frame's worth of points, in SOURCE coordinates."""

    x: np.ndarray
    y: np.ndarray
    #: Per-point intensity multiplier. 1.0 for `point()`; sources that use
    #: `circle(x, y, d)` carry the disc's area here, which is what makes the
    #: original's heavier endpoints heavier.
    weight: np.ndarray | float = 1.0


@dataclass(frozen=True, slots=True)
class Source:
    """A source sketch: its equation, its clock, and how it was composited."""

    key: str
    title: str
    n: int                      # samples per frame, from the original loop
    dt: float                   # t increment per frame, from the original
    ink: float                  # stroke()/fill() alpha, 0..1
    persist: float              # 0.0 clears each frame; else frame retention
    fn: Callable[[float, np.ndarray], Sample]
    original: str               # the source, verbatim
    #: Where this creature actually sits in its own canvas, and how big it
    #: is: the centre and half-extent of its robust bounding box, measured
    #: over one full period of its own clock by `tools/measure_sources.py`.
    #: The originals frame themselves loosely and none of them is centred;
    #: this is what lets each one be SEATED in the scope without touching
    #: the equation. Translation and one uniform scale - nothing else.
    frame_cx: float = SRC_MID
    frame_cy: float = SRC_MID
    frame_half: float = SRC_MID

    def indices(self) -> np.ndarray:
        """The original loop's full index set.

        float32 throughout. These are display coordinates for a 400px canvas
        that is then rasterised into a pixel grid, so the last twenty bits of
        a double are thrown away before anything is drawn - and carrying them
        costs roughly half the simulation budget in memory bandwidth alone.
        """
        return np.arange(self.n, dtype=np.float32)

    def sample(self, t: float, i: np.ndarray | None = None) -> Sample:
        """Evaluate the equation. `i` defaults to the original's full loop."""
        return self.fn(t, self.indices() if i is None else i)

    def to_world(self, x: np.ndarray, y: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray]:
        """Source canvas -> Abyssal world units. A similarity transform only.

        p5's y axis points down and so does Cairo's, so the sign is carried
        through unchanged; flipping it here would mirror every creature.
        """
        k = WORLD_FIT / self.frame_half
        return ((x - self.frame_cx) * k, (y - self.frame_cy) * k)


# ==========================================================================
# 01 - the sigmoid plume
# ==========================================================================
_SRC_01 = """\
a=(x,y,d=mag(k=4*cos(x/21),e=y/8-20))=>
circle(
  (q=3*sin(k*2)+.3/k+sin(y/19)*k*(9+2*sin(e*14-d*3+t*2)))+50*cos(c=d-t)+200,
  q*sin(c)+d*39-475,
  k*k>15?2:1
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).noStroke().fill(w,116);
for(t+=PI/240,i=1e4;i--;)a(i,i/235)}"""


def _c01(t: float, i: np.ndarray) -> Sample:
    x, y = i, i / 235.0
    k = 4.0 * np.cos(x / 21.0)
    e = y / 8.0 - 20.0
    d = np.hypot(k, e)
    # 0.3/k is a genuine feature: as cos(x/21) approaches zero the term
    # spikes, throwing the bright filament tips the reference is full of.
    # x is integral so k is never exactly zero, but the guard keeps a
    # pathological sample from poisoning the frame.
    with np.errstate(divide="ignore", invalid="ignore"):
        q = (3.0 * np.sin(k * 2.0) + 0.3 / k
             + np.sin(y / 19.0) * k
             * (9.0 + 2.0 * np.sin(e * 14.0 - d * 3.0 + t * 2.0)))
    c = d - t
    X = q + 50.0 * np.cos(c) + 200.0
    Y = q * np.sin(c) + d * 39.0 - 475.0
    # circle(...,  k*k>15 ? 2 : 1) - diameter, so the heavy points carry 4x
    # the area of the light ones.
    return Sample(X, Y, np.where(k * k > 15.0, 4.0, 1.0))


# ==========================================================================
# 02 - the coupled bodies
# ==========================================================================
_SRC_02 = """\
a=(m,d=mag(k=9*cos(i/81),e=i/765-13)/4)=>
point(
  (q=79-2*sin(k*3)+sin(k*k<19?t*3+d*4:d/2+4)/2*k*(9+5*sin(d*d-e/6-t+m)))
    *sin(c=d*d/9-t/16+m)+200,
  (q+50)*cos(c)+200
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).stroke(w,96);
for(t+=PI/45,i=2e4;i--;)a(i%2*9)}"""


def _c02(t: float, i: np.ndarray) -> Sample:
    m = (i % 2.0) * 9.0            # a(i%2*9): two bodies, 9 radians apart
    k = 9.0 * np.cos(i / 81.0)
    e = i / 765.0 - 13.0
    d = np.hypot(k, e) / 4.0
    # sin(k*k<19 ? t*3+d*4 : d/2+4) - the branch is on k, so the inner and
    # outer halves of each body are driven by different clocks.
    inner = np.where(k * k < 19.0, t * 3.0 + d * 4.0, d / 2.0 + 4.0)
    q = (79.0 - 2.0 * np.sin(k * 3.0)
         + np.sin(inner) / 2.0 * k
         * (9.0 + 5.0 * np.sin(d * d - e / 6.0 - t + m)))
    c = d * d / 9.0 - t / 16.0 + m
    return Sample(q * np.sin(c) + 200.0, (q + 50.0) * np.cos(c) + 200.0)


# ==========================================================================
# 03 - the mirrored alien
# ==========================================================================
_SRC_03 = """\
a=(x,y,d=5*cos(o=mag(k=x/8-12.5,e=y/8-12.5)/12*cos(sin(k/2)*cos(e/2))))=>
point(
  (x+d*k*(sin(d*2+t)+sin(y*o*o))/9)/1.5+133,
  (y/3-d*40+19*cos(d+t))*1.5+300
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(6,96).stroke(w,46);
for(t+=PI/90,i=4e4;i--;)a(i%200,i/200)}"""


def _c03(t: float, i: np.ndarray) -> Sample:
    x, y = i % 200.0, i / 200.0
    k = x / 8.0 - 12.5
    e = y / 8.0 - 12.5
    o = np.hypot(k, e) / 12.0 * np.cos(np.sin(k / 2.0) * np.cos(e / 2.0))
    d = 5.0 * np.cos(o)
    X = (x + d * k * (np.sin(d * 2.0 + t) + np.sin(y * o * o)) / 9.0) / 1.5 + 133.0
    Y = (y / 3.0 - d * 40.0 + 19.0 * np.cos(d + t)) * 1.5 + 300.0
    return Sample(X, Y)


# ==========================================================================
# 04 - the four-part plume
# ==========================================================================
_SRC_04 = """\
a=(y,o=mag(k=cos(y*9)*(y<5?sin(t/8+y)*35:11),e=y/8-13)/6)=>
point(
  (q=k*y/19+49+k*sin(y)*sin(o*2-e/5-t))*sin(c=o/3-e/5-t/8+i%4*8)
    -79*cos(c/3)+200,
  200+(q+70)*cos(c)
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(6).stroke(w,96);
for(t+=PI/30,i=2e4;i--;)a(i/500)}"""


def _c04(t: float, i: np.ndarray) -> Sample:
    y = i / 500.0
    # y<5 ? sin(t/8+y)*35 : 11 - the inner fifth of the body pulses on its
    # own slow clock while the rest holds a fixed amplitude.
    k = np.cos(y * 9.0) * np.where(y < 5.0, np.sin(t / 8.0 + y) * 35.0, 11.0)
    e = y / 8.0 - 13.0
    o = np.hypot(k, e) / 6.0
    q = k * y / 19.0 + 49.0 + k * np.sin(y) * np.sin(o * 2.0 - e / 5.0 - t)
    # i%4*8 - FOUR separate plumes, not a fourfold radial symmetry. Each
    # index class gets its own angular offset and carries its own anatomy.
    c = o / 3.0 - e / 5.0 - t / 8.0 + (i % 4.0) * 8.0
    X = q * np.sin(c) - 79.0 * np.cos(c / 3.0) + 200.0
    Y = 200.0 + (q + 70.0) * np.cos(c)
    return Sample(X, Y)


# ==========================================================================
# 05 - the single feather
# ==========================================================================
_SRC_05 = """\
a=(x,y,d=mag(k=5*cos(x/14)*cos(y/30),e=y/8-13)**2/59+4)=>
point(
  (q=60-3*sin(atan2(k,e)*e)+k*(3+4/d*sin(d*d-t*2)))*sin(c=d/2+e/99-t/18)+200,
  (q+d*9)*cos(c)+200
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).stroke(w,66);
for(t+=PI/20,i=1e4;i--;)a(i%200,i/43)}"""


def _c05(t: float, i: np.ndarray) -> Sample:
    x, y = i % 200.0, i / 43.0
    k = 5.0 * np.cos(x / 14.0) * np.cos(y / 30.0)
    e = y / 8.0 - 13.0
    # mag(k,e)**2/59+4 - ** binds tighter than /, so this is (k*k+e*e)/59+4.
    d = np.hypot(k, e) ** 2 / 59.0 + 4.0
    q = (60.0 - 3.0 * np.sin(np.arctan2(k, e) * e)
         + k * (3.0 + 4.0 / d * np.sin(d * d - t * 2.0)))
    c = d / 2.0 + e / 99.0 - t / 18.0
    return Sample(q * np.sin(c) + 200.0, (q + d * 9.0) * np.cos(c) + 200.0)


PI = np.pi

SOURCES: tuple[Source, ...] = (
    Source("s01", "sigmoid plume", 10000, PI / 240, 116 / 255, 0.0,
           _c01, _SRC_01, 196.38, 203.49, 116.95),
    Source("s02", "coupled bodies", 20000, PI / 45, 96 / 255, 0.0,
           _c02, _SRC_02, 213.12, 200.94, 181.72),
    Source("s03", "mirrored alien", 40000, PI / 90, 46 / 255, 0.6235,
           _c03, _SRC_03, 199.38, 182.16, 164.02),
    Source("s04", "four-part plume", 20000, PI / 30, 96 / 255, 0.0,
           _c04, _SRC_04, 199.68, 199.70, 140.80),
    Source("s05", "single feather", 10000, PI / 20, 66 / 255, 0.0,
           _c05, _SRC_05, 214.04, 187.18, 124.43),
)

BY_KEY = {s.key: s for s in SOURCES}
