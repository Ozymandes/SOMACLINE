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

THE PROPAGATING PULSE
---------------------
On top of those perturbations every specimen carries a continual pulse that
TRAVELS THROUGH ITS ANATOMY, and the pulse is what carries colour:

    published equation -> canonical point field -> physiology -> render
                                                   (pulse, hue, swell)

Each sample gets a stable body coordinate `u` in 0..1, derived ONCE from the
equation's own organisation - the static `d` along the plume, the grid radius
from the mirrored core, the shaft coordinate of the feather - plus, where
the anatomy has them, a periphery (`lat`) and a part (`grp`: coupled body,
mirror side, plume). A wave phase is integrated in real seconds (never
`speed * t`, so a change of speed cannot jump the front) and each point reads

    s = frac(wave - lambda * u - species offsets)      time since the front
    f = lead(s) * (exp(-s / 0.10) + 0.30 * afterpulse(s))

a bright leading edge, a softer trailing glow and a small afterpulse, never
a symmetric sine stripe. `f` drives the point's EXCITATION (luminance and how
far its colour leaves the resting cyan) and its HUE is read off the system
condition, warming along the trail under stress. The renderer accumulates
both, so a creature can hold dormant cyan, a green wavefront and an amber
trailing region at once. Every step is vectorised over preallocated float32
arrays; the anatomy is never recomputed.

Geometry responds too - a swell riding the wavefront, desync, asymmetry,
curl - but ONLY through drives that are exactly zero at rest, so the resting
creature is still the published equation to the last bit. Colour, which the
rest-identity gate does not constrain, pulses even at rest: subtly.

    01  root-to-tail wave; filament tips lag the spine; under stress the
        spine warms first and the fronts turn irregular
    02  the two bodies fire in alternation - an exchange whose rate is CPU;
        stress desynchronises them into a bounded argument, warming one first
    03  the pulse leaves the core down both sides in mirror; heat makes one
        side LAG and warm first, then the mirror recovers as heat falls
    04  a four-stage chase round the plumes; I/O fires the opposite plume;
        stress pulls all four toward one synchronised flare
    05  a peristaltic wave base -> tip, ribs just after the shaft; heat curls
        the feather toward its tip

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


#: Pulse shape, as a share of one cycle: leading-edge rise, trailing decay,
#: and the afterpulse's position, width and strength.
PULSE_LEAD = 0.035
PULSE_TRAIL = 0.10
AFTER_AT, AFTER_W, AFTER_K = 0.26, 0.045, 0.30

#: System condition -> pulse hue (0 electric blue .. 1 orange); see render.py
#: for the palette itself. Piecewise-linear, so it has no thresholds to snap.
_HUE_ACT = (0.00, 0.22, 0.45, 0.65, 0.82, 1.00)
_HUE_VAL = (0.05, 0.30, 0.50, 0.64, 0.80, 0.97)


def state_hue(activity: float) -> float:
    """The pulse hue the system condition asks for, 0..1."""
    a = min(1.0, max(0.0, activity))
    for j in range(1, len(_HUE_ACT)):
        if a <= _HUE_ACT[j]:
            t = (a - _HUE_ACT[j - 1]) / (_HUE_ACT[j] - _HUE_ACT[j - 1])
            return _HUE_VAL[j - 1] + (_HUE_VAL[j] - _HUE_VAL[j - 1]) * t
    return _HUE_VAL[-1]


def _norm(v: np.ndarray) -> np.ndarray:
    lo, hi = float(v.min()), float(v.max())
    return ((v - lo) / max(hi - lo, 1e-9)).astype(np.float32)


class SourceBody:
    """One specimen. Solves its equation each frame into world coordinates."""

    __slots__ = ("src", "time", "seed", "core_r", "_perm", "_mod2", "_mod4",
                 "_n", "_x", "_y", "_w", "_live", "_warm", "_expr", "_dt",
                 "_u", "_lat", "_grp", "_s", "_f", "_e", "_h", "_t1",
                 "wave", "aux", "_jit", "hue", "_amp")

    #: Pulse cadence at rest, in body traversals per second. CPU excitation
    #: multiplies it by up to 1 + PULSE_GAIN.
    PULSE_HZ = 0.24
    PULSE_GAIN = 1.8
    #: Wavefronts along the body at once.
    WAVES = 1.0

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
        # THE ANATOMY: stable per-sample body coordinates, from the source's
        # own index structure, in the same permuted order as the samples.
        order = self._perm.astype(np.int64)
        u, lat, grp = self._anatomy(np.arange(src.n, dtype=np.float64))
        self._u = u[order]
        self._lat = lat[order]
        self._grp = grp[order]
        # pulse scratch, reused every frame
        self._s = np.zeros(src.n, dtype=np.float32)
        self._f = np.zeros(src.n, dtype=np.float32)
        self._e = np.zeros(src.n, dtype=np.float32)
        self._h = np.zeros(src.n, dtype=np.float32)
        self._t1 = np.zeros(src.n, dtype=np.float32)
        self.wave = 0.0     # integrated pulse phase, in cycles
        self.aux = 0.0      # integrated species phase (exchange, chase, cilia)
        self._jit = 0.0     # integrated irregularity phase
        self.hue = state_hue(0.0)
        self._amp = 0.0
        self._x = np.zeros(src.n, dtype=np.float32)
        self._y = np.zeros(src.n, dtype=np.float32)
        self._w = np.ones(src.n, dtype=np.float32)
        self._live = src.n
        self._warm = 0.0
        self._expr = 1.0
        self._dt = 1.0 / SOURCE_FPS
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
        """Retention for THIS frame. Memory pressure lengthens the trail.

        The source's retention is per frame at 60 frames a second. Applied per
        frame on a machine presenting at 30, the trail would smear across twice
        as much motion and read as a saturated mass. Raising it to the frame's
        own duration keeps the trail's length constant in SECONDS, which is
        the thing the eye actually sees.
        """
        if self.src.persist <= 0.0:
            return 0.0
        base = min(0.88, self.src.persist + 0.16 * self._expr)
        return base ** max(0.25, min(4.0, self._dt * SOURCE_FPS))

    def points(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(world x, world y, weight) for this frame."""
        n = self._live
        return (self._x[:n], self._y[:n], self._w[:n])

    def excitation(self) -> tuple[np.ndarray, np.ndarray]:
        """(excitation 0..1, hue 0..1) per point, aligned with points()."""
        n = self._live
        return (self._e[:n], self._h[:n])

    def update(self, dt: float, p: Physiology) -> None:
        """Advance the source clock and re-solve. Allocation-free."""
        # CPU raises the phase rate, but only within a band: this is the one
        # place a global speed-up is legitimate, because the sources are
        # themselves phase animations. At rest the clock runs at exactly the
        # rate the sketch was authored for, and it is bounded above so speed
        # can never become the whole of a creature's response to load.
        rate = 1.0 + 0.5 * p.agitation
        self.time += dt * SOURCE_FPS * self.src.dt * rate
        if dt > 0.0:
            self._dt = dt
        # The pulse clock. Integrated, so a change of cadence bends the
        # front's speed instead of teleporting it. Render pressure and stress
        # add a bounded wobble to the rhythm - irregular, never random.
        self._jit += dt * 2.3
        wobble = 0.30 * max(p.tension, 0.6 * p.stress) * math.sin(self._jit)
        self.wave += dt * self.PULSE_HZ * (1.0 + self.PULSE_GAIN * p.excite) \
            * (1.0 + wobble)
        self._advance_aux(dt, p)

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

        self._pulse(n, p)
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

    # -- the propagating pulse ---------------------------------------------
    def _anatomy(self, i: np.ndarray):
        """(u, lat, grp) over the ORIGINAL loop index. Overridden per species."""
        n = i.size
        return (_norm(i), np.zeros(n, np.float32), np.zeros(n, np.float32))

    def _advance_aux(self, dt: float, p: Physiology) -> None:
        """Advance the species' own secondary phase. Base: none."""
        return

    def _phase(self, n: int, p: Physiology) -> np.ndarray:
        """The wave coordinate of every point: wave - lambda*u - offsets.

        Written into self._s[:n]. Species add their own offsets on top.
        """
        s = self._s[:n]
        np.multiply(self._u[:n], np.float32(-self.WAVES), out=s)
        s += np.float32(self.wave)
        return s

    def _pulse(self, n: int, p: Physiology) -> None:
        """Fill excitation and hue for the first `n` points. Allocation-light."""
        s = self._phase(n, p)
        np.mod(s, np.float32(1.0), out=s)
        f, t1 = self._f[:n], self._t1[:n]
        # leading edge: 0 just ahead of the front, 1 on and behind it
        np.subtract(np.float32(1.0), s, out=t1)
        t1 *= np.float32(-1.0 / PULSE_LEAD)
        np.exp(t1, out=t1)
        np.subtract(np.float32(1.0), t1, out=t1)
        # trailing glow
        np.multiply(s, np.float32(-1.0 / PULSE_TRAIL), out=f)
        np.exp(f, out=f)
        f *= t1
        # afterpulse
        np.subtract(s, np.float32(AFTER_AT), out=t1)
        t1 *= np.float32(1.0 / AFTER_W)
        np.square(t1, out=t1)
        np.negative(t1, out=t1)
        np.exp(t1, out=t1)
        f += np.float32(AFTER_K) * t1
        self._shape(n, p, f)
        np.clip(f, 0.0, 1.0, out=f)

        # Excitation: subtle at rest, full under load.
        self._amp = 0.42 + 0.58 * min(1.0, p.activity + 0.25 * p.surge)
        np.multiply(f, np.float32(self._amp), out=self._e[:n])
        # Hue: the condition's hue at the leading edge, warming along the
        # trail as stress rises - so a stressed pulse drags an amber wake.
        self.hue = state_hue(p.activity)
        h = self._h[:n]
        np.multiply(s, np.float32(0.16 * p.stress), out=h)
        h += np.float32(self.hue)
        self._tint(n, p, h)
        np.clip(h, 0.0, 1.0, out=h)

    def _shape(self, n: int, p: Physiology, f: np.ndarray) -> None:
        """Species modulation of the pulse envelope (in place). Base: none."""
        return

    def _tint(self, n: int, p: Physiology, h: np.ndarray) -> None:
        """Species hue bias (in place). Base: none."""
        return

    def _swell(self, x, y, p: Physiology, gain=None) -> None:
        """The wavefront swell: a local radial bulge riding the pulse.

        Zero at rest by construction (agitation and stress both 0), so it can
        never move the resting equation.
        """
        k = 0.030 * p.agitation + 0.035 * p.stress
        if k <= 0.0:
            return
        g = self._f[:x.size] * np.float32(k)
        if gain is not None:
            g *= gain
        cx, cy = self.src.frame_cx, self.src.frame_cy
        x -= cx
        x *= 1.0 + g
        x += cx
        y -= cy
        y *= 1.0 + g
        y += cy

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
    """Root-to-tail pulse; tips lag the spine; a ripple down the body."""

    __slots__ = ()
    PULSE_HZ = 0.20

    def _anatomy(self, i):
        # The equation's own d = mag(k, e) is static per index and runs
        # monotonically root -> tail (Y = ... + d*39), so it IS the spine
        # coordinate. |k| = |4 cos(x/21)| is how far a sample sits out on
        # its filament: the plume's periphery.
        k = 4.0 * np.cos(i / 21.0)
        e = (i / 235.0) / 8.0 - 20.0
        return (_norm(np.hypot(k, e)), (np.abs(k) / 4.0).astype(np.float32),
                np.zeros(i.size, np.float32))

    def _advance_aux(self, dt, p):
        # ciliary ripple phase, at the rate the ripple was authored for (3.1
        # per unit of source time); CPU excitation raises its frequency
        self.aux += dt * SOURCE_FPS * self.src.dt * 3.1 * (1.0 + 0.9 * p.excite)

    def _phase(self, n, p):
        s = super()._phase(n, p)
        # filament tips respond just after the spine
        s -= np.float32(0.07) * self._lat[:n]
        if p.stress > 0.01:
            # under stress the front turns ragged along the body
            s -= np.float32(0.05 * p.stress) * np.sin(
                self._u[:n] * np.float32(17.0) + np.float32(self.wave * 2.3))
        return s

    def _tint(self, n, p, h):
        if p.stress > 0.0:
            # heat reaches the central spine before the peripheral filaments
            h += np.float32(0.10 * p.stress) - np.float32(0.16 * p.stress) \
                * self._lat[:n]

    def _perturb(self, x, y, w, p: Physiology) -> None:
        t = self.time
        # The body runs head to tail with the index, so `y` is arc position.
        # Displace ACROSS it, with the amplitude growing toward the tail.
        s = np.clip(y / 320.0, 0.0, 1.0)
        if p.agitation > 0.0:
            x += (7.0 * p.agitation) * s * np.sin(y * 0.22 - self.aux)
        # Stress: the filaments agitate, the tips most.
        if p.stress > 0.0:
            x += (4.0 * p.stress) * self._lat[:x.size] * np.sin(
                y * 0.5 + self.aux * 1.7)
        # Thermal: the plume opens out about its own frame centre.
        if p.pulse > 0.0:
            g = 1.0 + 0.09 * p.pulse * math.sin(t * 0.52)
            cx, cy = self.src.frame_cx, self.src.frame_cy
            x[:] = cx + (x - cx) * g
            y[:] = cy + (y - cy) * g
        self._swell(x, y, p)
        # I/O: a narrow band of excitation travels head to tail.
        if p.surge > 0.01:
            band = np.exp(-np.square((s - (t * 0.19) % 1.2 - 0.1) / 0.09))
            w *= 1.0 + 3.4 * p.surge * band


# ==========================================================================
# 02 - the coupled bodies
# ==========================================================================
class CoupledBodies(SourceBody):
    """Two bodies firing in alternation; stress makes them argue."""

    __slots__ = ()
    PULSE_HZ = 0.30

    def _anatomy(self, i):
        # m = i%2*9 is the body; d = mag(k, e)/4 runs from each body's core
        # out along its spiral; |k| = |9 cos(i/81)| is the lateral fringe.
        k = 9.0 * np.cos(i / 81.0)
        e = i / 765.0 - 13.0
        return (_norm(np.hypot(k, e) / 4.0),
                (np.abs(k) / 9.0).astype(np.float32),
                (i % 2.0).astype(np.float32))

    def _advance_aux(self, dt, p):
        # the argument's own slow clock
        self.aux += dt * 0.9

    def _phase(self, n, p):
        s = super()._phase(n, p)
        # The second body fires half a cycle after the first: excitation
        # handed across. Stress pushes the pair out of step - a bounded
        # argument that settles back as the stress does.
        lag = 0.5 + 0.22 * p.stress * math.sin(self.aux)
        s -= np.float32(lag) * self._grp[:n]
        return s

    def _tint(self, n, p, h):
        if p.stress > 0.0:
            # orange starts in one body and reaches the other late
            h += np.float32(0.08 * p.stress) \
                - np.float32(0.13 * p.stress) * self._grp[:n]

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
        self._swell(x, y, p)
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
    """A mirrored pulse from the core; heat makes one side lag."""

    __slots__ = ()
    PULSE_HZ = 0.24

    def _anatomy(self, i):
        # The equation samples a 200x200 grid; its (k, e) are centred on the
        # grid, and o = mag(k, e)/12... is the creature's radial anatomy -
        # the core at k = e = 0 draws the upper body. The mirror plane is
        # k = 0, so sign(k) is the side.
        x, y = i % 200.0, i / 200.0
        k = x / 8.0 - 12.5
        e = y / 8.0 - 12.5
        return (_norm(np.hypot(k, e)), np.zeros(i.size, np.float32),
                (k > 0.0).astype(np.float32))

    def _advance_aux(self, dt, p):
        self.aux += dt * 0.7

    def _phase(self, n, p):
        s = super()._phase(n, p)
        if p.stress > 0.0:
            # one side falls behind the other; symmetric again at rest
            lag = 0.12 * p.stress * (0.6 + 0.4 * math.sin(self.aux))
            s -= np.float32(lag) * self._grp[:n]
        return s

    def _tint(self, n, p, h):
        if p.stress > 0.0:
            # the leading side warms first
            h += np.float32(0.09 * p.stress) \
                - np.float32(0.09 * p.stress) * self._grp[:n]

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
        self._swell(x, y, p)
        if p.surge > 0.01:
            w *= 1.0 + 1.5 * p.surge * np.exp(-np.square((s - (t * 0.22) % 1.1) / 0.12))


# ==========================================================================
# 04 - the four-part plume
# ==========================================================================
def _burst(z: float) -> float:
    """A one-shot envelope over one chase cycle: sharp on, slow off."""
    z %= 1.0
    return math.exp(-z / 0.30) * (1.0 - math.exp(-(1.0 - z) / 0.05))


class QuadPlume(SourceBody):
    """A four-stage chase round the plumes; stress pulls them into step."""

    __slots__ = ()
    PULSE_HZ = 0.34
    WAVES = 1.3

    def _anatomy(self, i):
        # y = i/500 runs along each plume; i%4 is the plume itself.
        y = i / 500.0
        return (_norm(y), np.zeros(i.size, np.float32),
                (i % 4.0).astype(np.float32))

    def _advance_aux(self, dt, p):
        # one full round of the four plumes; CPU raises the chase rate
        self.aux += dt * 0.22 * (1.0 + 2.0 * p.excite)

    def _shape(self, n, p, f):
        # Each plume fires in turn. Stress pulls the four stations together
        # until, near the top of the band, all four flare at once.
        sync = 0.85 * _smoothstep(0.35, 1.0, p.stress)
        env = np.array([0.22 + 0.78 * _burst(self.aux - L * 0.25 * (1.0 - sync))
                        + 0.8 * p.surge * _burst(self.aux - ((L + 2) % 4) * 0.25)
                        for L in range(4)], dtype=np.float32)
        gain = env[self._grp[:n].astype(np.intp)]
        f *= gain
        # keep the per-point gain for the swell
        self._t1[:n] = gain

    def _tint(self, n, p, h):
        if p.stress > 0.0:
            h += np.float32(0.07 * p.stress) * (self._t1[:n] - np.float32(0.6))

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
        self._swell(x, y, p)
        # I/O: the flare walks round the four.
        if p.surge > 0.01:
            hot = int(t * 0.6) % 4
            w *= 1.0 + 2.8 * p.surge * (lobe == hot)


# ==========================================================================
# 05 - the single feather
# ==========================================================================
class SingleFeather(SourceBody):
    """A peristaltic wave base -> tip; ribs after the shaft; heat curls it."""

    __slots__ = ()
    PULSE_HZ = 0.30

    def _anatomy(self, i):
        # d = mag(k, e)**2/59 + 4 is static and grows from the feather's base
        # out to its extremity (the radius is q + d*9): the shaft coordinate.
        # |cos(x/14)| is how far out along its rib a sample sits.
        x, y = i % 200.0, i / 43.0
        k = 5.0 * np.cos(x / 14.0) * np.cos(y / 30.0)
        e = y / 8.0 - 13.0
        return (_norm(np.hypot(k, e) ** 2 / 59.0 + 4.0),
                np.abs(np.cos(x / 14.0)).astype(np.float32),
                np.zeros(i.size, np.float32))

    def _advance_aux(self, dt, p):
        # rib-wave phase, at the whip's authored rate (2.7 per unit of source
        # time); faster with CPU
        self.aux += dt * SOURCE_FPS * self.src.dt * 2.7 * (1.0 + 0.6 * p.excite)

    def _phase(self, n, p):
        s = super()._phase(n, p)
        # the ribs light just after the shaft beside them
        s -= np.float32(0.09) * self._lat[:n]
        return s

    def _tint(self, n, p, h):
        if p.stress > 0.0:
            # the base warms first; the signal carries it out to the tip
            h += np.float32(0.10 * p.stress) \
                - np.float32(0.10 * p.stress) * self._u[:n]

    def _perturb(self, x, y, w, p: Physiology) -> None:
        t = self.time
        cx, cy = self.src.frame_cx, self.src.frame_cy
        dx, dy = x - cx, y - cy
        r = np.hypot(dx, dy) / max(self.src.frame_half, 1.0)
        # The whip: displacement perpendicular to the radius, growing with it.
        if p.agitation > 0.0:
            amp = (9.0 * p.agitation) * np.clip(r, 0.0, 1.2) ** 1.6
            ph = np.sin(r * 5.0 - self.aux)
            inv = 1.0 / np.maximum(np.hypot(dx, dy), 1e-6)
            x[:] = cx + dx + amp * ph * (-dy * inv)
            y[:] = cy + dy + amp * ph * (dx * inv)
        # Thermal: the arc opens.
        if p.pulse > 0.0:
            g = 1.0 + (0.065 * p.pulse) * math.sin(t * 0.48)
            x[:] = cx + (x - cx) * g
            y[:] = cy + (y - cy) * g
        # Stress: the feather curls, most at its tip - tension, bounded.
        if p.stress > 0.01:
            u = self._u[:x.size]
            ang = (0.14 * p.stress) * u * u
            ca, sa = np.cos(ang), np.sin(ang)
            ddx, ddy = x - cx, y - cy
            x[:] = cx + ddx * ca - ddy * sa
            y[:] = cy + ddx * sa + ddy * ca
        self._swell(x, y, p)
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
