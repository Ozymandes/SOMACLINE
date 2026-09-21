"""The five specimens: source equations, seated in the world, under telemetry.

WHAT A SPECIMEN IS NOW
----------------------
One `Source` from `sources.py` - a published generative sketch, ported
verbatim - plus three things the console needs and the sketch has no notion
of: a clock that runs in real seconds, a seat in the logical world, and a
physiology.

    equation        sources.py, unmodified
    clock           the source's own dt, advanced per real second
    seat            translate + one uniform scale (sources.Source.to_world)
    physiology      a SMALL, per-species perturbation, applied AFTER the
                    equation has been solved

THE PERTURBATION RULE
---------------------
The equation is never edited. Telemetry acts on the solved point cloud, and
only in ways the creature could plausibly do to itself: a ripple along its
own body, a breath about its own centre, a band of excitation travelling
through it, a fraction of itself expressed or withheld.

That is a deliberate constraint. It is very easy to reach into an expression
like `sin(d*d - e/6 - t + m)` and multiply something by CPU load; it is also
how you end up with five creatures that no longer resemble their sources.
Every specimen here still reduces to its published form when physiology is
at rest, and `qa/console_gates.py` asserts exactly that.

Each species responds differently, and not by speed:

    01  a transverse ripple down the plume; a bright band runs its length
    02  the two bodies counter-rotate and separate
    03  a metabolic breath, and above 0.88 pulse the mirror plane SHEARS
    04  the four plumes desynchronise, and one flares at a time
    05  a whip whose amplitude grows toward the tip; the base flares

CONTRACT (unchanged, and load-bearing)
--------------------------------------
  * World units only. Nothing here imports pixels, Cairo, GTK or telemetry.
  * State is a function of accumulated TIME and Physiology, never of
    geometry, so a resize cannot perturb a specimen.
  * Index sets and scratch arrays are allocated once, in __init__.
"""

from __future__ import annotations

import math

import numpy as np

from ..core.signals import Physiology
from ..core.world import WORLD_RADIUS
from . import sources as S

TAU = math.tau

#: Hard ceiling. Nothing may reach the bezel whatever the telemetry does.
R_SAFE = WORLD_RADIUS - 6.0

#: The originals run at 60 frames a second. Advancing each source's own dt at
#: that rate is what makes the creature move at the speed it was authored to,
#: on a machine whose frame rate is neither fixed nor 60.
SOURCE_FPS = 60.0

#: Smallest fraction of its own point set a specimen will express. Memory
#: pressure thins the cloud; it never empties it.
MIN_EXPRESSION = 0.42


def _smoothstep(e0: float, e1: float, v: float) -> float:
    t = min(1.0, max(0.0, (v - e0) / max(1e-9, e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


class SourceBody:
    """One specimen. Solves its equation each frame into world coordinates."""

    __slots__ = ("src", "time", "seed", "core_r", "_perm", "_mod2", "_mod4",
                 "_n", "_x", "_y", "_w", "_live", "_warm", "_expr")

    def __init__(self, src: S.Source, seed: int = 20260920) -> None:
        self.src = src
        self.time = 0.0
        self.seed = seed
        self.core_r = 0.0
        rng = np.random.default_rng(seed)
        # A fixed permutation of the original loop's indices. Expressing a
        # fraction of the body then means taking a PREFIX of this - a uniform
        # thinning of the whole creature. Taking a prefix of the raw index
        # range instead would amputate it, because every one of these sources
        # maps its index onto a body coordinate.
        self._perm = rng.permutation(src.n).astype(np.float32)
        # The index classes the sources use to separate their parts - i%2 for
        # the coupled bodies, i%4 for the four plumes - precomputed, because
        # the perturbations need them every frame and a modulo over 40k
        # floats is not free.
        self._mod2 = (self._perm % 2.0).astype(np.float32)
        self._mod4 = (self._perm % 4.0).astype(np.float32)
        self._n = src.n
        self._x = np.zeros(src.n, dtype=np.float32)
        self._y = np.zeros(src.n, dtype=np.float32)
        self._w = np.ones(src.n, dtype=np.float32)
        self._live = src.n
        self._warm = 0.0
        self._expr = 1.0
        self.update(0.0, Physiology())

    # -- contract ---------------------------------------------------------
    @property
    def n_points(self) -> int:
        return self._live

    @property
    def warmth(self) -> float:
        """Thermal stress, 0..1. The renderer tints with this."""
        return self._warm

    @property
    def persist(self) -> float:
        """Frame retention. Memory pressure lengthens what trails species keep."""
        if self.src.persist <= 0.0:
            return 0.0
        return min(0.88, self.src.persist + 0.16 * self._expr)

    def points(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(world x, world y, weight) for this frame."""
        n = self._live
        return (self._x[:n], self._y[:n], self._w[:n])

    def update(self, dt: float, p: Physiology) -> None:
        """Advance the source clock and re-solve. Allocation-free."""
        # CPU raises the phase rate, but only within a band: this is the one
        # place a global speed-up is legitimate, because the sources are
        # themselves phase animations. At rest the clock runs at exactly the
        # rate the sketch was authored for, and it is bounded above so speed
        # can never become the whole of a creature's response to load.
        rate = 1.0 + 0.5 * p.agitation
        self.time += dt * SOURCE_FPS * self.src.dt * rate

        self._warm = _smoothstep(0.58, 0.95, p.pulse)
        self._expr = min(1.0, MIN_EXPRESSION
                         + (1.0 - MIN_EXPRESSION) * (0.30 + 0.70 * p.density))
        n = max(64, int(self.src.n * self._expr))
        self._live = n

        # The equation's outputs are already fresh arrays, so they are
        # perturbed and transformed IN PLACE rather than copied first.
        sm = self.src.sample(self.time, self._perm[:n])
        x, y = sm.x, sm.y
        if np.isscalar(sm.weight):
            w = np.full(n, float(sm.weight), dtype=np.float32)
        else:
            w = sm.weight

        self._perturb(x, y, w, p)

        # Seat: translate to the creature's own frame centre, scale once.
        k = S.WORLD_FIT / self.src.frame_half
        np.subtract(x, self.src.frame_cx, out=x)
        np.multiply(x, k, out=x)
        np.subtract(y, self.src.frame_cy, out=y)
        np.multiply(y, k, out=y)

        # A sample outside the design radius is made INVISIBLE, not clamped
        # onto it. Source 01 throws a handful of points hundreds of units off
        # its own canvas every frame (the 0.3/k spike); p5 simply draws those
        # off-screen where nobody sees them, and clamping instead would pile
        # them into a bright arc on the outermost ring that the equation
        # never drew. The coordinate is still pulled in so the body's
        # reported extent stays bounded.
        r = np.hypot(x, y)
        np.maximum(r, 1e-9, out=r)
        np.multiply(w, r <= R_SAFE, out=w)
        np.divide(R_SAFE, r, out=r)
        np.minimum(r, 1.0, out=r)
        np.multiply(x, r, out=x)
        np.multiply(y, r, out=y)
        # A non-finite sample (source 01's 0.3/k spike) is given zero weight
        # rather than dropped, so the point count stays a fixed shape.
        bad = ~(np.isfinite(x) & np.isfinite(y))
        if bad.any():
            x[bad] = 0.0
            y[bad] = 0.0
            w[bad] = 0.0
        self._x[:n] = x
        self._y[:n] = y
        self._w[:n] = w

    # -- per-species physiology -------------------------------------------
    def _perturb(self, x, y, w, p: Physiology) -> None:
        """Small, species-specific. Overridden below; the base is inert."""
        return


def max_extent(org: "SourceBody") -> float:
    """Farthest VISIBLE point from the world origin, in world units.

    Weighted, because a sample carrying zero intensity is not on screen and
    so cannot clip: counting it would report a radius the creature does not
    actually occupy.
    """
    wx, wy, w = org.points()
    live = w > 0.0
    if not live.any():
        return 0.0
    return float(np.max(np.hypot(wx[live], wy[live])))


# ==========================================================================
# 01 - the sigmoid plume
# ==========================================================================
class SigmoidPlume(SourceBody):
    """A transverse ripple down the body; a bright band runs its length."""

    __slots__ = ()

    def _perturb(self, x, y, w, p: Physiology) -> None:
        t = self.time
        # The body runs head to tail with the index, so `y` is arc position.
        # Displace ACROSS it, with the amplitude growing toward the tail.
        s = np.clip(y / 320.0, 0.0, 1.0)
        if p.agitation > 0.0:
            x += (7.0 * p.agitation) * s * np.sin(y * 0.22 - t * 3.1)
        # Thermal: the plume opens out about its own frame centre.
        if p.pulse > 0.0:
            g = 1.0 + 0.09 * p.pulse * math.sin(t * 0.52)
            cx, cy = self.src.frame_cx, self.src.frame_cy
            x[:] = cx + (x - cx) * g
            y[:] = cy + (y - cy) * g
        # I/O: a narrow band of excitation travels head to tail.
        if p.surge > 0.01:
            band = np.exp(-np.square((s - (t * 0.19) % 1.2 - 0.1) / 0.09))
            w *= 1.0 + 3.4 * p.surge * band


# ==========================================================================
# 02 - the coupled bodies
# ==========================================================================
class CoupledBodies(SourceBody):
    """The two bodies counter-rotate and draw apart."""

    __slots__ = ()

    def _perturb(self, x, y, w, p: Physiology) -> None:
        t = self.time
        # m = i%2*9 assigns every sample to one body or the other; recover
        # that split and act on the two halves oppositely.
        side = np.where(self._mod2[:x.size] < 0.5, np.float32(-1.0),
                        np.float32(1.0))
        cx, cy = self.src.frame_cx, self.src.frame_cy
        dx, dy = x - cx, y - cy
        ang = side * (0.22 * p.agitation) * math.sin(t * 0.7)
        ca, sa = np.cos(ang), np.sin(ang)
        x[:] = cx + dx * ca - dy * sa
        y[:] = cy + dx * sa + dy * ca
        # Thermal: the pair breathes apart and back together.
        x += side * (6.0 * p.pulse * math.sin(t * 0.41))
        # I/O: a packet crosses from one body to the other.
        if p.surge > 0.01:
            phase = (t * 0.33) % 2.0
            want = -1.0 if phase < 1.0 else 1.0
            r = np.hypot(dx, dy) / max(self.src.frame_half, 1.0)
            band = np.exp(-np.square((r - abs(phase % 1.0)) / 0.16))
            w *= 1.0 + 2.6 * p.surge * band * (side == want)


# ==========================================================================
# 03 - the mirrored alien
# ==========================================================================
class MirroredAlien(SourceBody):
    """A metabolic breath - and above 0.88 pulse, the mirror plane shears."""

    __slots__ = ()

    def _perturb(self, x, y, w, p: Physiology) -> None:
        t = self.time
        cx, cy = self.src.frame_cx, self.src.frame_cy
        dx, dy = x - cx, y - cy
        # Metabolic pulse: a slow breath whose depth rises with temperature.
        g = 1.0 + (0.065 * p.pulse) * math.sin(t * 0.62)
        dx *= g
        dy *= g
        # CPU: the trailing limbs agitate; the body does not.
        s = np.clip(dy / (self.src.frame_half * 1.1), 0.0, 1.0)
        if p.agitation > 0.0:
            dx += (7.0 * p.agitation) * s * np.sin(dy * 0.16 - t * 2.3)
        # THERMAL EXTREME: the creature loses its own mirror plane. This is
        # the only specimen with an exact one, so it is the only one where
        # the failure is unmistakable.
        skew = _smoothstep(0.88, 1.0, p.pulse)
        if skew > 0.0:
            left = dx < 0.0
            dx[left] *= 1.0 + 0.26 * skew
            dy[left] += 14.0 * skew * np.sin(dx[left] * 0.05 + t)
        x[:] = cx + dx
        y[:] = cy + dy
        if p.surge > 0.01:
            w *= 1.0 + 1.5 * p.surge * np.exp(-np.square((s - (t * 0.22) % 1.1) / 0.12))


# ==========================================================================
# 04 - the four-part plume
# ==========================================================================
class QuadPlume(SourceBody):
    """The four plumes desynchronise under load; one flares at a time."""

    __slots__ = ()

    def _perturb(self, x, y, w, p: Physiology) -> None:
        t = self.time
        # i%4 is what separates the four plumes in the source; give each its
        # own small angular lead so load reads as them falling out of step.
        lobe = self._mod4[:x.size]
        cx, cy = self.src.frame_cx, self.src.frame_cy
        dx, dy = x - cx, y - cy
        ang = (0.11 * p.agitation) * np.sin(t * 0.9 + lobe * 1.9)
        ca, sa = np.cos(ang), np.sin(ang)
        x[:] = cx + dx * ca - dy * sa
        y[:] = cy + dx * sa + dy * ca
        # Thermal: all four breathe radially, together.
        if p.pulse > 0.0:
            g = 1.0 + (0.058 * p.pulse) * math.sin(t * 0.55)
            x[:] = cx + (x - cx) * g
            y[:] = cy + (y - cy) * g
        # I/O: the flare walks round the four.
        if p.surge > 0.01:
            hot = int(t * 0.6) % 4
            w *= 1.0 + 2.8 * p.surge * (lobe == hot)


# ==========================================================================
# 05 - the single feather
# ==========================================================================
class SingleFeather(SourceBody):
    """A whip whose amplitude grows toward the tip; the base flares."""

    __slots__ = ()

    def _perturb(self, x, y, w, p: Physiology) -> None:
        t = self.time
        cx, cy = self.src.frame_cx, self.src.frame_cy
        dx, dy = x - cx, y - cy
        r = np.hypot(dx, dy) / max(self.src.frame_half, 1.0)
        # The whip: displacement perpendicular to the radius, growing with it.
        if p.agitation > 0.0:
            amp = (9.0 * p.agitation) * np.clip(r, 0.0, 1.2) ** 1.6
            ph = np.sin(r * 5.0 - t * 2.7)
            inv = 1.0 / np.maximum(np.hypot(dx, dy), 1e-6)
            x[:] = cx + dx + amp * ph * (-dy * inv)
            y[:] = cy + dy + amp * ph * (dx * inv)
        # Thermal: the arc opens.
        if p.pulse > 0.0:
            g = 1.0 + (0.065 * p.pulse) * math.sin(t * 0.48)
            x[:] = cx + (x - cx) * g
            y[:] = cy + (y - cy) * g
        # I/O: the base of the feather flares as a burst enters it.
        if p.surge > 0.01:
            w *= 1.0 + 3.0 * p.surge * np.exp(-np.square(r / 0.30))


#: key -> class. `species.py` binds these to the archive metadata.
BODIES = {
    "s01": SigmoidPlume,
    "s02": CoupledBodies,
    "s03": MirroredAlien,
    "s04": QuadPlume,
    "s05": SingleFeather,
}


def build(key: str, seed: int = 20260920) -> SourceBody:
    return BODIES[key](S.BY_KEY[key], seed=seed)
