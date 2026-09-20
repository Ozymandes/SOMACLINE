"""The five mathematical organisms.

WHAT THESE ARE
--------------
Each specimen is a compact set of point equations evaluated every frame into
polyline buffers - the same idiom as the generative Processing/p5 works the
catalogue is drawn from, ported to NumPy and Cairo. Nothing is rasterised,
traced or baked: every dot on screen is a solved coordinate.

They are FIVE DIFFERENT ORGANISMS, not five parameter sets of one. The
previous catalogue was a single radial-plume solver with the symmetry order
changed, which is why switching specimens only ever changed how many arms it
had. These differ in body plan:

    01  CILIARIBBON   a sigmoid spine with club-tipped cilia on both flanks
    02  FUNNELIS      a colony of dotted conical bells trailing smooth tendrils
    03  SYMMETRA      a vertically mirrored rostrum, vanes, eyes and tentacles
    04  DYAD          two bodies: a dense comb and a wandering dotted orbit
    05  FROND         one long curved rachis, club-tipped barbs, a hood at the head

THE CONTRACT THEY ALL KEEP
--------------------------
Identical to the original engine, and load-bearing:

  * World units only. Nothing here imports pixels, Cairo, GTK or telemetry.
  * Every array is allocated ONCE in __init__. `update()` writes in place.
  * State is a function of accumulated TIME and Physiology, never of geometry,
    so a resize cannot perturb a specimen and a specimen cannot perturb layout.
  * Everything stays inside WORLD_RADIUS; `max_extent` is asserted by QA.

BUFFERS THE RENDERER READS
--------------------------
    fil_x, fil_y    (NF, NP)  polyline vertices
    fil_alpha       (NF,)     0 culls the filament entirely
    fil_width       (NF,)     world-unit stroke width
    fil_tint        (NF,)     0 = deep field colour, 1 = bright core colour
    fil_dot         (NF,)     0 = solid stroke, >0 = dot pitch in world units
    node_x/y/r/a    (NN,)     bright discrete points: club tips, papillae, eyes
    core_r                    luminous core radius; 0 for species that have none

`fil_dot` is what carries the point-cloud character of the references: the
engravings are drawn as sequences of dots, not as continuous ink, and a dash
pattern reproduces that at a fraction of the cost of one filament per dot.

HOW TELEMETRY REACHES THE BODY
------------------------------
Never as "animation speed". Each species maps the four physiological channels
onto its OWN morphology, so the machine's state is legible as a change of
shape rather than of tempo:

    agitation <- cpu     beat frequency, wave amplitude, filament agitation
    pulse     <- temp    metabolic contraction/expansion, warm stress tint,
                         and, at the extremes, loss of symmetry
    density   <- memory  how much body is expressed: cilia, ribs, tendrils
    flux      <- io/net  peripheral excitation
    surge     <- io/net  a transient that PROPAGATES through the body
"""

from __future__ import annotations

import math

import numpy as np

from ..core.signals import Physiology
from ..core.world import WORLD_RADIUS

TAU = math.tau

#: Hard ceiling. Every species is clamped to this, so nothing can ever reach
#: the bezel no matter what the telemetry does.
R_SAFE = WORLD_RADIUS - 8.0


def _clamp_radius(x: np.ndarray, y: np.ndarray, limit: float = R_SAFE) -> None:
    """Scale any point beyond `limit` back onto it, in place.

    Radial rather than per-axis, so a clamped body keeps its shape instead of
    being squashed against a box.
    """
    r = np.hypot(x, y)
    np.maximum(r, 1e-9, out=r)
    k = np.minimum(1.0, limit / r)
    np.multiply(x, k, out=x)
    np.multiply(y, k, out=y)


def _rot(x: np.ndarray, y: np.ndarray, ang: float) -> tuple[np.ndarray, np.ndarray]:
    c, s = math.cos(ang), math.sin(ang)
    return (x * c - y * s, x * s + y * c)


def _smoothstep(e0: float, e1: float, v: float) -> float:
    t = min(1.0, max(0.0, (v - e0) / max(1e-9, e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


class Body:
    """Base class: buffer allocation, the clock, and the shared contract.

    Subclasses implement `_build()` (once, to lay out constant structure) and
    `_shape(p)` (every frame, to solve the current pose into the buffers).
    """

    __slots__ = ("time", "core_r", "rng", "seed",
                 "fil_x", "fil_y", "fil_alpha", "fil_width", "fil_tint",
                 "fil_dot", "node_x", "node_y", "node_r", "node_a",
                 "_nf", "_np", "_nn", "_rate", "_ox", "_oy")

    #: Species-level identity, mirrored into `species.py`.
    KEY = "body"

    def __init__(self, seed: int = 20260920) -> None:
        n_fil, n_pts, n_node = self._sizes()
        self.time = 0.0
        self.core_r = 0.0
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self._rate = 1.0
        self._nf, self._np, self._nn = n_fil, n_pts, n_node
        self.fil_x = np.zeros((n_fil, n_pts), dtype=np.float64)
        self.fil_y = np.zeros((n_fil, n_pts), dtype=np.float64)
        self.fil_alpha = np.zeros(n_fil, dtype=np.float64)
        self.fil_width = np.zeros(n_fil, dtype=np.float64)
        self.fil_tint = np.zeros(n_fil, dtype=np.float64)
        self.fil_dot = np.zeros(n_fil, dtype=np.float64)
        self.node_x = np.zeros(n_node, dtype=np.float64)
        self.node_y = np.zeros(n_node, dtype=np.float64)
        self.node_r = np.zeros(n_node, dtype=np.float64)
        self.node_a = np.zeros(n_node, dtype=np.float64)
        self._ox = 0.0
        self._oy = 0.0
        self._build()
        self._shape(Physiology())
        self._centre()

    # -- contract ---------------------------------------------------------
    def _centre(self) -> None:
        """Measure the offset that seats this body on the world origin.

        A specimen authored in its own natural frame does not generally have
        its visual mass at (0, 0) - a single arcuate frond certainly does not -
        and the viewport centres the WORLD origin, not the creature. Left
        alone, half the catalogue sits off to one side of its own scope.

        The offset is measured ONCE, from the rest pose, and then held fixed.
        Re-measuring per frame would make the creature crawl around the field
        as it moved, which is worse than being off-centre.
        """
        m = self.fil_alpha > 0.02
        if not m.any():
            return
        w = self.fil_alpha[m][:, None]
        self._ox = -float((self.fil_x[m] * w).sum() / (w.sum() * self._np))
        self._oy = -float((self.fil_y[m] * w).sum() / (w.sum() * self._np))

    def update(self, dt: float, p: Physiology) -> None:
        """Advance the clock and re-solve the pose. Allocation-free."""
        self.time += dt * self._rate
        self._shape(p)
        if self._ox or self._oy:
            np.add(self.fil_x, self._ox, out=self.fil_x)
            np.add(self.fil_y, self._oy, out=self.fil_y)
            np.add(self.node_x, self._ox, out=self.node_x)
            np.add(self.node_y, self._oy, out=self.node_y)
        _clamp_radius(self.fil_x, self.fil_y)
        _clamp_radius(self.node_x, self.node_y)

    def _sizes(self) -> tuple[int, int, int]:   # pragma: no cover - overridden
        """(filaments, vertices per filament, discrete nodes)."""
        raise NotImplementedError

    def _build(self) -> None:          # pragma: no cover - overridden
        """Lay out the constant structure. Runs once."""
        raise NotImplementedError

    def _shape(self, p: Physiology) -> None:   # pragma: no cover - overridden
        raise NotImplementedError

    # -- shared helpers ---------------------------------------------------
    @property
    def n_points(self) -> int:
        return self._nf * self._np

    def _warm(self, p: Physiology) -> float:
        """Thermal stress, 0..1. Above ~0.72 pulse the body tints amber."""
        return _smoothstep(0.58, 0.95, p.pulse)

    def _express(self, p: Physiology, n: int) -> np.ndarray:
        """Per-filament expression gate from memory pressure.

        Density decides HOW MUCH BODY IS THERE, not how bright it is: a
        filament above the gate fades out entirely rather than dimming, so
        memory reads as structure appearing and disappearing.
        """
        frac = 0.34 + 0.66 * p.density
        idx = np.linspace(0.0, 1.0, n)
        return np.clip((frac - idx) * (n * 0.30), 0.0, 1.0)


def max_extent(org: Body) -> float:
    """Farthest point of the organism from the world origin, in world units."""
    r = float(np.max(np.hypot(org.fil_x, org.fil_y))) if org._nf else 0.0
    if org._nn:
        r = max(r, float(np.max(np.hypot(org.node_x, org.node_y))))
    return r


# ==========================================================================
# shared point-equation helpers
# ==========================================================================
def _arcs(bx, by, ang0, curl, length, u, out_x, out_y) -> None:
    """A family of constant-curvature appendages, solved at once.

    Each appendage leaves its base at `ang0` and turns through `curl` over its
    length, which is the cheapest equation that produces the gently recurved
    filament the engravings are built from. Everything is (N, 1) against a
    (1, P) parameter, so one call solves a whole flank.

    bx, by, ang0, curl, length : (N, 1)      u : (1, P)
    """
    a = ang0 + curl * u
    r = length * u
    np.multiply(r, np.cos(a), out=out_x)
    np.add(out_x, bx, out=out_x)
    np.multiply(r, np.sin(a), out=out_y)
    np.add(out_y, by, out=out_y)


def _tangent(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit tangent of a sampled curve, by central difference."""
    dx = np.gradient(x)
    dy = np.gradient(y)
    n = np.hypot(dx, dy)
    np.maximum(n, 1e-9, out=n)
    return (dx / n, dy / n)




# ==========================================================================
# 01 - CILIARIBBON
# ==========================================================================
class Ciliaribbon(Body):
    """A sigmoid spine carrying club-tipped cilia along both flanks.

    THE EQUATIONS
    -------------
    The spine is one sine in the body's own frame,

        x(v) = A sin(2pi k v + phi) + B(v) sin(2pi m v - w t)
        y(v) = L (v - 1/2)

    where the second term is a travelling wave whose envelope grows toward the
    tail, so the body swims rather than wags. Cilia are constant-curvature
    arcs raised off the spine normal, and their beat is METACHRONAL - each
    station lags the one before it - which is what makes a ciliated flank read
    as alive rather than as a row of twitching hairs.

    TELEMETRY
    ---------
    cpu  beat amplitude and frequency, and the travelling wave
    temp body length (contraction/expansion) and a warm tint on the flanks
    mem  how far along the body cilia are expressed at all
    io   a bright excitation packet running head to tail, lengthening the
         cilia it passes - a propagating signal, not a global brightening
    """

    KEY = "ciliaribbon"

    _M = 74                  # cilia stations on the main spine
    _MB = 14                 # stations on the secondary branch
    _P = 24
    _LEN = 566.0
    _AMP = 122.0
    _CILIA = 116.0
    _POSE = -0.36            # the specimen sits on a diagonal, as engraved

    def _sizes(self) -> tuple[int, int, int]:
        return (3 + self._M * 2 + 2 + self._MB * 2, self._P,
                self._M * 2 + self._MB * 2 + 2)

    def _build(self) -> None:
        M, MB, P = self._M, self._MB, self._P
        self.core_r = 0.0
        self._rate = 1.0
        self.v = np.linspace(0.035, 0.995, M)[:, None]
        self.vb = np.linspace(0.10, 1.0, MB)[:, None]
        self.u = np.linspace(0.0, 1.0, P)[None, :]
        self.vs = np.linspace(0.0, 1.0, P)
        # Per-cilium jitter, drawn once: a real flank is not a comb.
        self.jit = self.rng.uniform(-0.085, 0.085, (M * 2, 1))
        self.jitb = self.rng.uniform(-0.09, 0.09, (MB * 2, 1))
        self.len_jit = self.rng.uniform(0.86, 1.14, (M * 2, 1))

    # -- the spine --------------------------------------------------------
    def _spine(self, v: np.ndarray, t: float, ag: float, stretch: float):
        L = self._LEN * stretch
        amp = self._AMP * (0.90 + 0.10 * stretch)
        wave = (5.0 + 30.0 * ag) * np.power(v, 1.5)
        # 0.86 of a period across the body: enough that the spine crosses its
        # own axis twice and reads as the engraved S rather than as an arc.
        x = amp * np.sin(TAU * 0.86 * v - 1.55) + \
            wave * np.sin(TAU * 1.35 * v - t * (1.5 + 2.6 * ag))
        y = L * (v - 0.5)
        return (x, y)

    def _shape(self, p: Physiology) -> None:
        t = self.time
        M, MB, P = self._M, self._MB, self._P
        ag, warm = p.agitation, self._warm(p)
        stretch = 0.94 + 0.13 * p.pulse

        # --- spine, and the frame every cilium is raised from -------------
        sx, sy = self._spine(self.v, t, ag, stretch)
        sx = sx[:, 0]
        sy = sy[:, 0]
        tx, ty = _tangent(sx, sy)
        nx, ny = -ty, tx

        # three dotted rails: the spine of the engraving is a bundle
        dense = np.linspace(0.02, 0.999, P)[:, None]
        rx, ry = self._spine(dense, t, ag, stretch)
        rx = rx[:, 0]
        ry = ry[:, 0]
        rtx, rty = _tangent(rx, ry)
        for i, off in enumerate((-5.2, 0.0, 5.2)):
            self.fil_x[i] = rx - rty * off
            self.fil_y[i] = ry + rtx * off
            self.fil_alpha[i] = 0.95 if off == 0.0 else 0.70
            self.fil_width[i] = 4.6 if off == 0.0 else 2.4
            self.fil_tint[i] = 0.96 if off == 0.0 else 0.72
            self.fil_dot[i] = 0.0 if off == 0.0 else 7.0

        # --- cilia --------------------------------------------------------
        # Envelope: longest just past a third of the way down, tapering to
        # nothing at both ends, exactly as the engraved flank does.
        env = np.power(np.sin(np.pi * np.clip(self.v[:, 0], 0, 1)), 0.62) * \
            (1.0 - 0.34 * self.v[:, 0])
        # The excitation packet: a narrow Gaussian running head to tail.
        vp = (t * 0.42) % 1.25 - 0.12
        pulse_env = np.exp(-np.square((self.v[:, 0] - vp) / 0.075)) * p.surge

        base_ang = np.arctan2(ny, nx)
        beat = 0.085 + 0.33 * ag
        freq = 1.15 + 1.9 * ag
        gate = self._express(p, M)

        for side in (0, 1):
            sgn = 1.0 - 2.0 * side
            i0 = 3 + side * M
            sl = slice(i0, i0 + M)
            jit = self.jit[side * M:(side + 1) * M]
            ln_j = self.len_jit[side * M:(side + 1) * M]

            ang = (base_ang * sgn if sgn > 0 else base_ang + np.pi)[:, None]
            # Sweep the cilium back toward the tail and beat it. The phase
            # lag along v is the metachronal wave.
            phase = TAU * 1.7 * self.v - t * freq * TAU * 0.5
            ang = ang - sgn * (0.40 + 0.34 * self.v) \
                + sgn * beat * np.sin(phase) + jit
            curl = sgn * (0.55 + 0.75 * self.v) * (1.0 + 0.5 * ag)
            ln = (self._CILIA * env[:, None] * ln_j
                  * (0.72 + 0.28 * p.density)
                  * (1.0 + 0.55 * pulse_env[:, None]))
            bx = (sx + nx * sgn * 5.0)[:, None]
            by = (sy + ny * sgn * 5.0)[:, None]
            _arcs(bx, by, ang, curl, ln, self.u,
                  self.fil_x[sl], self.fil_y[sl])
            a = gate * (0.60 + 0.36 * env) * (1.0 + 0.7 * pulse_env)
            self.fil_alpha[sl] = np.clip(a, 0.0, 1.0)
            self.fil_width[sl] = 1.9
            self.fil_tint[sl] = np.clip(0.30 + 0.45 * env + warm * 0.30
                                        + 0.5 * pulse_env, 0.0, 1.0)
            self.fil_dot[sl] = 5.4

            # club tips
            k0 = side * M
            self.node_x[k0:k0 + M] = self.fil_x[sl][:, -1]
            self.node_y[k0:k0 + M] = self.fil_y[sl][:, -1]
            self.node_r[k0:k0 + M] = 2.6 + 1.5 * env + 2.0 * pulse_env
            self.node_a[k0:k0 + M] = np.clip(a * 1.05, 0.0, 1.0)

        # --- the secondary branch, near the head --------------------------
        j = 3 + M * 2
        root = 0.70
        bxr = float(np.interp(root, self.v[:, 0], sx))
        byr = float(np.interp(root, self.v[:, 0], sy))
        ba = math.atan2(float(np.interp(root, self.v[:, 0], ny)),
                        float(np.interp(root, self.v[:, 0], nx))) - 0.55
        bl = 168.0 * (0.80 + 0.20 * p.density)
        ba_arr = np.array([[ba]])
        _arcs(np.array([[bxr]]), np.array([[byr]]), ba_arr,
              np.array([[0.62]]), np.array([[bl]]), self.u,
              self.fil_x[j:j + 1], self.fil_y[j:j + 1])
        self.fil_alpha[j] = 0.80
        self.fil_width[j] = 3.0
        self.fil_tint[j] = 0.85
        self.fil_dot[j] = 0.0

        bsx = self.fil_x[j]
        bsy = self.fil_y[j]
        btx, bty = _tangent(bsx, bsy)
        idx = np.clip((self.vb[:, 0] * (P - 1)).astype(int), 0, P - 1)
        benv = np.power(np.sin(np.pi * self.vb[:, 0]), 0.55)
        for side in (0, 1):
            sgn = 1.0 - 2.0 * side
            i0 = j + 1 + side * MB
            sl = slice(i0, i0 + MB)
            jb = self.jitb[side * MB:(side + 1) * MB]
            # The branch's own normal, taken per station from its tangent.
            bnx, bny = -bty[idx], btx[idx]
            bang = np.arctan2(bny * sgn, bnx * sgn)
            ang = bang[:, None] + sgn * 0.30 + jb \
                + sgn * beat * 0.7 * np.sin(TAU * 1.5 * self.vb - t * freq)
            _arcs(bsx[idx][:, None], bsy[idx][:, None], ang,
                  np.full((MB, 1), sgn * 0.7),
                  (62.0 * benv[:, None]) * (0.7 + 0.3 * p.density), self.u,
                  self.fil_x[sl], self.fil_y[sl])
            self.fil_alpha[sl] = 0.56 * (0.6 + 0.4 * benv)
            self.fil_width[sl] = 1.7
            self.fil_tint[sl] = 0.40 + 0.35 * benv + warm * 0.25
            self.fil_dot[sl] = 5.0
            k0 = M * 2 + side * MB
            self.node_x[k0:k0 + MB] = self.fil_x[sl][:, -1]
            self.node_y[k0:k0 + MB] = self.fil_y[sl][:, -1]
            self.node_r[k0:k0 + MB] = 2.1 + 1.0 * benv
            self.node_a[k0:k0 + MB] = 0.62 * (0.6 + 0.4 * benv)

        # head and tail marks
        self.node_x[-2:] = (sx[0], sx[-1])
        self.node_y[-2:] = (sy[0], sy[-1])
        self.node_r[-2:] = (4.2, 3.0)
        self.node_a[-2:] = (0.95, 0.80)

        # --- pose ---------------------------------------------------------
        drift = self._POSE + 0.05 * math.sin(t * 0.11)
        self.fil_x[:], self.fil_y[:] = _rot(self.fil_x, self.fil_y, drift)
        self.node_x[:], self.node_y[:] = _rot(self.node_x, self.node_y, drift)


# ==========================================================================
# 02 - FUNNELIS
# ==========================================================================
class Funnelis(Body):
    """A colony of dotted conical bells trailing smooth tendrils.

    THE EQUATIONS
    -------------
    Each bell is a cone solved in its own frame: an apex, an axis, and a mouth
    ellipse foreshortened by `e` so the cone reads as a solid seen at an angle.
    The surface is drawn as `n_rib` generatrices

        P(u) = (1-u) * apex + u * mouth(phi_i) + bulge sin(pi u) * outward

    plus concentric hoops at fixed u. That is the entire structure: a dotted
    ruled surface. The tendrils are logarithmic curls,

        theta(u) = theta_0 + c u^1.6 + a sin(2pi f u - w t)

    drawn solid, so the colony reads as rigid architecture with soft trailing
    parts - which is exactly what the engraving does.

    TELEMETRY
    ---------
    cpu  tendril undulation, and the colony's differential rotation
    temp bell aperture: the mouths open and close as a metabolic pulse
    mem  how many tendrils and hoops are expressed
    io   a bright ripple running out along one tendril per burst
    """

    KEY = "funnelis"

    _BELLS = 3
    _RIB = 28
    _HOOP = 8
    _TEND = 16
    _P = 30

    #: (apex x, apex y, axis angle, length, mouth radius, foreshorten, spin)
    # Axis angles are deliberately spread over the circle: three bells all
    # opening the same way read as one object seen three times.
    # The three axes are spread 120 degrees apart and each apex is pulled
    # back along its own axis, so the mouths form a triangle and every bell
    # stays legible instead of the colony collapsing into one silhouette.
    _PLAN = (
        (80.0, 30.0, 1.920, 236.0, 168.0, 0.52, 0.050),
        (-58.0, 48.0, -2.270, 208.0, 142.0, 0.46, -0.040),
        (-12.0, -68.0, -0.175, 190.0, 126.0, 0.50, 0.064),
    )

    #: These are FUNNELS, not cones: the generatrices start on a narrow throat
    #: ring rather than at a single point. A true apex collapses every rib into
    #: one pixel and reads as a paper hat; a throat keeps the surface a surface
    #: all the way down, which is what the engraving shows.
    _THROAT = 0.14

    def _sizes(self) -> tuple[int, int, int]:
        per = self._RIB + self._HOOP
        return (self._BELLS * per + self._TEND, self._P,
                self._BELLS * self._RIB + self._TEND)

    def _build(self) -> None:
        self.core_r = 0.0
        self._rate = 1.0
        P = self._P
        self.u = np.linspace(0.0, 1.0, P)[None, :]
        self.phi = np.linspace(0.0, TAU, self._RIB, endpoint=False)[:, None]
        self.ring = np.linspace(0.0, TAU, P)[None, :]
        self.tphase = self.rng.uniform(0.0, TAU, (self._TEND, 1))
        self.tcurl = self.rng.uniform(1.1, 2.8, (self._TEND, 1))
        self.tlen = self.rng.uniform(104.0, 198.0, (self._TEND, 1))
        self.tang = self.rng.uniform(0.0, TAU, (self._TEND, 1))
        self.tbell = self.rng.integers(0, self._BELLS, self._TEND)

    def _shape(self, p: Physiology) -> None:
        t = self.time
        RIB, HOOP, P = self._RIB, self._HOOP, self._P
        ag, warm = p.agitation, self._warm(p)
        # Aperture is the metabolic pulse: the bells breathe.
        aperture = 0.80 + 0.34 * p.pulse + 0.05 * math.sin(t * 0.63)
        gate_r = self._express(p, RIB)
        per = RIB + HOOP

        mouths = []
        for b, (ax, ay, th0, L, R, e, spin) in enumerate(self._PLAN):
            th = th0 + spin * t * (0.45 + 0.9 * ag)
            Rb = R * aperture
            Lb = L * (0.92 + 0.12 * p.pulse)
            # mouth ring in the bell's own frame, then rotated into the world
            mx_l = Rb * np.cos(self.phi)
            my_l = Lb + Rb * e * np.sin(self.phi)
            mx, my = _rot(mx_l, my_l, th)
            mx += ax
            my += ay
            mouths.append((mx, my, th, Lb, Rb, e, ax, ay))

            # --- generatrices ---------------------------------------------
            sl = slice(b * per, b * per + RIB)
            th_r = self._THROAT
            uu = th_r + (1.0 - th_r) * self.u
            bulge = (0.10 + 0.16 * ag) * Rb * np.sin(np.pi * self.u)
            lx = (mx - ax) * uu + bulge * np.cos(self.phi)
            ly = (my - ay) * uu + bulge * e * np.sin(self.phi)
            self.fil_x[sl] = lx + ax
            self.fil_y[sl] = ly + ay
            self.fil_alpha[sl] = np.clip(gate_r * 0.78, 0.0, 1.0)
            self.fil_width[sl] = 1.7
            self.fil_tint[sl] = 0.42 + 0.30 * warm
            self.fil_dot[sl] = 4.4

            # --- hoops ------------------------------------------------------
            for k in range(HOOP):
                f = self._THROAT + (1.0 - self._THROAT) * (k / (HOOP - 1.0))
                i = b * per + RIB + k
                hx_l = Rb * f * np.cos(self.ring)
                hy_l = Lb * f + Rb * f * e * np.sin(self.ring)
                hx, hy = _rot(hx_l, hy_l, th)
                self.fil_x[i] = hx[0] + ax
                self.fil_y[i] = hy[0] + ay
                last = k == HOOP - 1
                self.fil_alpha[i] = 0.82 if last else 0.44
                self.fil_width[i] = 2.6 if last else 1.5
                self.fil_tint[i] = 0.72 + 0.22 * warm
                self.fil_dot[i] = 0.0 if last else 4.2

            k0 = b * RIB
            self.node_x[k0:k0 + RIB] = mx[:, 0]
            self.node_y[k0:k0 + RIB] = my[:, 0]
            self.node_r[k0:k0 + RIB] = 2.0
            self.node_a[k0:k0 + RIB] = np.clip(gate_r * 0.80, 0.0, 1.0)

        # --- tendrils ---------------------------------------------------
        i0 = self._BELLS * per
        gate_t = self._express(p, self._TEND)
        # One tendril at a time carries the burst, chosen by the clock so the
        # event visibly travels around the colony rather than flashing at once.
        hot = int((t * 0.7) % self._TEND)
        for j in range(self._TEND):
            b = int(self.tbell[j])
            mx, my, th, Lb, Rb, e, ax, ay = mouths[b]
            k = int((j * 7) % RIB)
            bx, by = float(mx[k, 0]), float(my[k, 0])
            excite = p.surge if j == hot else 0.0
            ang = (self.tang[j] + th
                   + (0.5 + 1.4 * ag) * 0.18 * math.sin(t * 0.8 + j))
            a = (ang + self.tcurl[j] * np.power(self.u, 1.6)
                 + (0.10 + 0.40 * ag) * np.sin(TAU * 1.7 * self.u
                                               - t * (1.1 + 2.0 * ag)
                                               + self.tphase[j]))
            # Taper: a tendril thins away rather than ending at full stride.
            r = (self.tlen[j] * (0.70 + 0.30 * p.density)
                 * np.power(self.u, 0.88) * (1.0 + 0.10 * excite))
            i = i0 + j
            self.fil_x[i] = bx + (r * np.cos(a))[0]
            self.fil_y[i] = by + (r * np.sin(a))[0]
            self.fil_alpha[i] = min(1.0, gate_t[j] * (0.62 + 0.30 * excite))
            self.fil_width[i] = 2.2
            self.fil_tint[i] = min(1.0, 0.58 + 0.30 * warm + 0.40 * excite)
            self.fil_dot[i] = 0.0
            kn = self._BELLS * RIB + j
            self.node_x[kn] = self.fil_x[i][-1]
            self.node_y[kn] = self.fil_y[i][-1]
            self.node_r[kn] = 2.2 + 2.4 * excite
            self.node_a[kn] = min(1.0, gate_t[j] * (0.7 + 0.3 * excite))

        drift = 0.10 * math.sin(t * 0.08)
        self.fil_x[:], self.fil_y[:] = _rot(self.fil_x, self.fil_y, drift)
        self.node_x[:], self.node_y[:] = _rot(self.node_x, self.node_y, drift)


# ==========================================================================
# 03 - SYMMETRA
# ==========================================================================
class Symmetra(Body):
    """A vertically mirrored rostrum with spiral eyes, vanes and tentacles.

    THE EQUATIONS
    -------------
    This is the only specimen with an exact mirror plane. The right half is
    solved and the left half is written as its reflection, so the symmetry is
    a property of the code rather than of a coincidence between two sets of
    parameters - and breaking it is then a deliberate, measurable act.

        rostrum   ribs at height v, half-width  w(v) = W sin(pi v^0.72)^0.8
        eye       r(theta) = a e^(b theta),  theta in [0, 3.6 pi]
        vane      a fan of constant-curvature arcs off one root
        tentacle  theta(u) = theta_0 + c u^1.25 + A(u) sin(2pi f u - w t)
                  with A(u) growing toward the tip, so the wave travels out

    TELEMETRY
    ---------
    cpu  tentacle wave frequency and amplitude
    temp mantle contraction and warm tint - and, ABOVE 0.88, a deliberate
         SYMMETRY INSTABILITY: the left half acquires a small divergence, so
         thermal distress is visible as the body losing its own mirror plane
    mem  vane density and rostrum rib count
    io   papillae along the tentacles brighten in a travelling band
    """

    KEY = "symmetra"

    _RIB = 26
    _VANE = 30
    _TENT = 4                 # per side
    _P = 28
    _W = 92.0

    def _sizes(self) -> tuple[int, int, int]:
        half = self._RIB + self._VANE + self._TENT + 1     # +1 eye spiral
        return (half * 2, self._P, (self._VANE + self._TENT) * 2 + 4)

    def _build(self) -> None:
        self.core_r = 0.0
        self._rate = 1.0
        P = self._P
        self.u = np.linspace(0.0, 1.0, P)[None, :]
        self.v = np.linspace(0.02, 1.0, self._RIB)[:, None]
        self.vv = np.linspace(0.0, 1.0, self._VANE)[:, None]
        self.spiral = np.linspace(0.0, 3.6 * np.pi, P)
        self.half = self._RIB + self._VANE + self._TENT + 1
        # Tentacle plan: root offset, base angle, curl, length, thickness.
        # Arms splay DOWN AND OUT and never cross the mid-line: base angles
        # increase with the root offset, and every curl turns outward.
        self.t_root = np.array([[16.0], [40.0], [66.0], [92.0]])
        self.t_ang = np.array([[1.44], [1.22], [1.00], [0.80]])
        self.t_curl = np.array([[-0.34], [-0.50], [-0.66], [-0.82]])
        self.t_len = np.array([[268.0], [312.0], [330.0], [280.0]])

    def _shape(self, p: Physiology) -> None:
        t = self.time
        RIB, VANE, TENT, P = self._RIB, self._VANE, self._TENT, self._P
        ag, warm = p.agitation, self._warm(p)
        # Metabolic pulse: the mantle contracts and expands as a whole.
        breathe = 1.0 + 0.085 * math.sin(t * (0.55 + 0.9 * p.pulse)) \
            * (0.5 + 0.5 * p.pulse)
        W = self._W * breathe
        H = 240.0 * breathe
        gate_v = self._express(p, VANE)
        gate_r = self._express(p, RIB)

        H0 = self.half
        # ---- rostrum ribs ------------------------------------------------
        v = self.v
        w = W * np.power(np.sin(np.pi * np.power(v, 0.72)), 0.80)
        y = -H * 0.86 + (H * 1.30) * v
        # each rib is a shallow bowed arc from the mid-line outward
        rx = w * self.u
        ry = y + (w * 0.16) * np.sin(np.pi * self.u)
        self.fil_x[0:RIB] = rx
        self.fil_y[0:RIB] = ry
        self.fil_alpha[0:RIB] = np.clip(gate_r * 0.66, 0.0, 1.0)
        self.fil_width[0:RIB] = 1.7
        self.fil_tint[0:RIB] = 0.46 + 0.34 * warm
        self.fil_dot[0:RIB] = 4.0

        # ---- eye spiral ---------------------------------------------------
        i_eye = RIB
        sp_t = self.spiral + t * 0.22
        er = 3.4 * np.exp(0.185 * self.spiral)
        self.fil_x[i_eye] = 40.0 + er * np.cos(sp_t)
        self.fil_y[i_eye] = -H * 0.30 + er * np.sin(sp_t)
        self.fil_alpha[i_eye] = 0.90
        self.fil_width[i_eye] = 2.1
        self.fil_tint[i_eye] = 0.95
        self.fil_dot[i_eye] = 0.0

        # ---- lateral vane -------------------------------------------------
        i_v = RIB + 1
        sl = slice(i_v, i_v + VANE)
        # Two interleaved ranks off slightly different roots: one fan of
        # rays reads as a comb, two overlapping fans read as a membrane.
        rank = (np.arange(VANE) % 2)[:, None]
        vang = -0.56 + 1.80 * self.vv + rank * 0.055
        vlen = ((210.0 + 92.0 * np.sin(np.pi * self.vv))
                * (0.74 + 0.26 * p.density) * (1.0 - 0.14 * rank))
        flut = (0.04 + 0.16 * ag) * np.sin(TAU * 1.2 * self.vv - t * 0.9)
        _arcs(38.0 + rank * 14.0, -H * 0.44 + rank * 12.0,
              vang + flut, 0.46 + rank * 0.10, vlen, self.u,
              self.fil_x[sl], self.fil_y[sl])
        self.fil_alpha[sl] = np.clip(gate_v * 0.62, 0.0, 1.0)
        self.fil_width[sl] = 1.6
        self.fil_tint[sl] = 0.38 + 0.34 * warm
        self.fil_dot[sl] = 4.6

        # ---- tentacles ----------------------------------------------------
        i_t = RIB + 1 + VANE
        slt = slice(i_t, i_t + TENT)
        band = (t * 0.55) % 1.3 - 0.15
        amp = (0.05 + 0.30 * ag) * np.power(self.u, 1.4)
        ang = (self.t_ang + self.t_curl * np.power(self.u, 1.25)
               + amp * np.sin(TAU * (1.1 + 0.9 * ag) * self.u
                              - t * (1.3 + 2.2 * ag)
                              + np.arange(TENT)[:, None] * 0.7))
        r = self.t_len * (0.90 + 0.14 * p.pulse) * self.u
        self.fil_x[slt] = self.t_root + r * np.cos(ang)
        self.fil_y[slt] = -H * 0.05 + r * np.sin(ang)
        self.fil_alpha[slt] = 0.86
        self.fil_width[slt] = 3.4
        self.fil_tint[slt] = 0.62 + 0.28 * warm + 0.20 * p.surge
        self.fil_dot[slt] = 3.4

        # ---- mirror to the left half --------------------------------------
        # Thermal distress breaks the mirror plane. It is a small, bounded
        # divergence applied ONLY to the reflected half, so the instability is
        # legible as the body failing to match itself.
        skew = 0.16 * _smoothstep(0.88, 1.0, p.pulse)
        self.fil_x[H0:] = -self.fil_x[:H0] * (1.0 - skew)
        self.fil_y[H0:] = self.fil_y[:H0] * (1.0 + skew * 0.55)
        self.fil_alpha[H0:] = self.fil_alpha[:H0]
        self.fil_width[H0:] = self.fil_width[:H0]
        self.fil_tint[H0:] = self.fil_tint[:H0]
        self.fil_dot[H0:] = self.fil_dot[:H0]

        # ---- discrete marks ------------------------------------------------
        n_tip = (VANE + TENT)
        tips_x = np.concatenate([self.fil_x[sl][:, -1], self.fil_x[slt][:, -1]])
        tips_y = np.concatenate([self.fil_y[sl][:, -1], self.fil_y[slt][:, -1]])
        tip_a = np.concatenate([np.clip(gate_v * 0.7, 0, 1),
                                np.full(TENT, 0.92)])
        self.node_x[:n_tip] = tips_x
        self.node_y[:n_tip] = tips_y
        self.node_x[n_tip:n_tip * 2] = -tips_x * (1.0 - skew)
        self.node_y[n_tip:n_tip * 2] = tips_y * (1.0 + skew * 0.55)
        self.node_r[:n_tip * 2] = 2.6
        self.node_a[:n_tip] = tip_a
        self.node_a[n_tip:n_tip * 2] = tip_a
        # eye centres and the rostrum apex
        ex, ey = 40.0, -H * 0.30
        self.node_x[-4:] = (ex, -ex, 0.0, 0.0)
        self.node_y[-4:] = (ey, ey, -H * 0.86, H * 0.44)
        self.node_r[-4:] = (4.4, 4.4, 3.0, 3.6)
        gl = 0.85 + 0.15 * p.flux
        self.node_a[-4:] = (gl, gl, 0.70, 0.80)


# ==========================================================================
# 04 - DYAD
# ==========================================================================
class Dyad(Body):
    """Two bodies: a dense dotted comb, and a wandering dotted orbit.

    THE EQUATIONS
    -------------
    The specimen is genuinely double. The left body is a curved rachis whose
    outer flank carries a dense comb of club-tipped rays; the right body is a
    single long Lissajous path,

        x(s) = R ( sin(a s + p1) + 1/2 sin(b s) )
        y(s) = R ( cos(c s)      + 1/2 sin(d s + p2) )

    drawn as one dotted trail. Neither is a copy of the other, and a few
    connective filaments run between them so the pair reads as ONE organism
    with two organs rather than as two creatures sharing a field.

    TELEMETRY
    ---------
    cpu  the orbit's frequency ratio drifts, so its path visibly reorganises
    temp the two bodies separate and close - a paired respiration
    mem  how much of the comb is expressed
    io   a bright PACKET travels along the orbit and into the comb: the two
         bodies are wired together, and a burst is the signal crossing
    """

    KEY = "dyad"

    _RAY = 46
    _INNER = 12
    _ORBIT = 6               # chained segments of the trail
    _LINK = 5
    _P = 34

    def _sizes(self) -> tuple[int, int, int]:
        return (2 + self._RAY + self._INNER + self._ORBIT + self._LINK,
                self._P, self._RAY + self._INNER + 8)

    def _build(self) -> None:
        self.core_r = 0.0
        self._rate = 1.0
        P = self._P
        self.u = np.linspace(0.0, 1.0, P)[None, :]
        self.v = np.linspace(0.03, 0.99, self._RAY)[:, None]
        self.vi = np.linspace(0.12, 0.92, self._INNER)[:, None]
        self.spine_u = np.linspace(0.0, 1.0, P)
        self.jit = self.rng.uniform(-0.06, 0.06, (self._RAY, 1))
        self.ljit = self.rng.uniform(0.88, 1.12, (self._RAY, 1))
        # The orbit is sampled as one continuous parameter, then cut into
        # chained filaments so it can be drawn with a shared vertex count.
        self.s = np.linspace(0.0, 1.0, self._ORBIT * (P - 1) + 1)

    def _comb_spine(self, t: float, sep: float, ag: float):
        """The left body's rachis: an arc that nods with the clock."""
        a = -0.68 + 2.34 * self.spine_u + 0.05 * math.sin(t * 0.5)
        r = 60.0 + 232.0 * self.spine_u
        return (-sep + r * np.cos(a) * 1.05, r * np.sin(a) * 0.98)

    def _shape(self, p: Physiology) -> None:
        t = self.time
        RAY, INNER, ORB, LINK, P = (self._RAY, self._INNER, self._ORBIT,
                                    self._LINK, self._P)
        ag, warm = p.agitation, self._warm(p)
        # Paired respiration: the bodies breathe apart and together.
        sep = 168.0 + 26.0 * p.pulse + 14.0 * math.sin(t * 0.37)

        # ---- body A: the comb --------------------------------------------
        sx, sy = self._comb_spine(t, sep, ag)
        tx, ty = _tangent(sx, sy)
        # The rachis curves away from the pair, so its OUTER flank is the
        # right normal. Taking the left one buried every ray inside the arc.
        nx, ny = ty, -tx
        idx = np.clip((self.v[:, 0] * (P - 1)).astype(int), 0, P - 1)

        for i, off in enumerate((0.0, 5.0)):
            self.fil_x[i] = sx + nx * off
            self.fil_y[i] = sy + ny * off
            self.fil_alpha[i] = 0.95 if off == 0.0 else 0.58
            self.fil_width[i] = 4.4 if off == 0.0 else 2.0
            self.fil_tint[i] = 0.98 if off == 0.0 else 0.66
            self.fil_dot[i] = 0.0 if off == 0.0 else 6.0

        env = (np.power(np.sin(np.pi * np.power(self.v[:, 0], 0.85)), 0.48)
               * (0.62 + 0.38 * self.v[:, 0]))
        gate = self._express(p, RAY)
        beat = (0.05 + 0.22 * ag) * np.sin(TAU * 2.1 * self.v - t * (1.4 + 2.4 * ag))
        # The packet arriving from the orbit lights the comb's root first.
        arrive = math.exp(-((((t * 0.45) % 1.6) - 1.15) / 0.13) ** 2) * p.surge

        sl = slice(2, 2 + RAY)
        ang = np.arctan2(ny[idx], nx[idx])[:, None] + beat + self.jit
        ln = (186.0 * env[:, None] * self.ljit * (0.70 + 0.30 * p.density)
              * (1.0 + 0.22 * arrive))
        _arcs(sx[idx][:, None], sy[idx][:, None], ang,
              np.full((RAY, 1), 0.52 + 0.5 * ag), ln, self.u,
              self.fil_x[sl], self.fil_y[sl])
        a_ray = np.clip(gate * (0.48 + 0.40 * env) * (1.0 + 0.5 * arrive), 0, 1)
        self.fil_alpha[sl] = a_ray
        self.fil_width[sl] = 1.9
        self.fil_tint[sl] = np.clip(0.34 + 0.42 * env + 0.30 * warm
                                    + 0.4 * arrive, 0, 1)
        self.fil_dot[sl] = 5.0
        self.node_x[:RAY] = self.fil_x[sl][:, -1]
        self.node_y[:RAY] = self.fil_y[sl][:, -1]
        self.node_r[:RAY] = 2.5 + 1.4 * env + 1.6 * arrive
        self.node_a[:RAY] = a_ray

        # short inner rays, on the concave flank
        sli = slice(2 + RAY, 2 + RAY + INNER)
        idi = np.clip((self.vi[:, 0] * (P - 1)).astype(int), 0, P - 1)
        angi = np.arctan2(-ny[idi], -nx[idi])[:, None]
        _arcs(sx[idi][:, None], sy[idi][:, None], angi,
              np.full((INNER, 1), -0.45), np.full((INNER, 1), 46.0), self.u,
              self.fil_x[sli], self.fil_y[sli])
        self.fil_alpha[sli] = 0.52
        self.fil_width[sli] = 1.5
        self.fil_tint[sli] = 0.30 + 0.25 * warm
        self.fil_dot[sli] = 4.2
        self.node_x[RAY:RAY + INNER] = self.fil_x[sli][:, -1]
        self.node_y[RAY:RAY + INNER] = self.fil_y[sli][:, -1]
        self.node_r[RAY:RAY + INNER] = 1.8
        self.node_a[RAY:RAY + INNER] = 0.50

        # ---- body B: the orbit ---------------------------------------------
        # The frequency ratio drifts with load, so the trail reorganises into
        # a visibly different figure instead of merely moving faster.
        a_f = 2.0 + 0.8 * ag
        b_f = 3.0
        c_f = 3.0 + 0.5 * ag
        d_f = 5.0
        R = 112.0 * (0.86 + 0.18 * p.density)
        s = self.s * TAU
        ox = sep + 20.0 + R * (np.sin(a_f * s + t * 0.18)
                               + 0.50 * np.sin(b_f * s - t * 0.11))
        oy = -12.0 + R * (np.cos(c_f * s - t * 0.14)
                          + 0.50 * np.sin(d_f * s + t * 0.09))
        i0 = 2 + RAY + INNER
        # Packet position along the trail, as a fraction of its length.
        pk = (t * 0.45) % 1.6
        for k in range(ORB):
            a, b = k * (P - 1), k * (P - 1) + P
            i = i0 + k
            self.fil_x[i] = ox[a:b]
            self.fil_y[i] = oy[a:b]
            seg = (k + 0.5) / ORB
            hot = math.exp(-((seg - (1.0 - min(pk, 1.0))) / 0.18) ** 2) * p.surge
            # The pair is a pair: an orbit that reads as a stray hairline
            # next to the comb makes the specimen look like one body and a
            # rendering artefact.
            self.fil_alpha[i] = min(1.0, 0.74 + 0.26 * hot)
            self.fil_width[i] = 2.8 + 1.6 * hot
            self.fil_tint[i] = min(1.0, 0.58 + 0.24 * warm + 0.40 * hot)
            self.fil_dot[i] = 6.0

        # ---- connective filaments -------------------------------------------
        il = i0 + ORB
        for k in range(LINK):
            f = (k + 1) / (LINK + 1.0)
            ax = float(sx[int(f * (P - 1))])
            ay = float(sy[int(f * (P - 1))])
            bx = float(ox[int(f * (len(ox) - 1))])
            by = float(oy[int(f * (len(oy) - 1))])
            bow = (24.0 + 28.0 * math.sin(t * 0.4 + k)) * (0.4 + 0.6 * ag)
            i = il + k
            self.fil_x[i] = ax + (bx - ax) * self.spine_u
            self.fil_y[i] = (ay + (by - ay) * self.spine_u
                             + bow * np.sin(np.pi * self.spine_u))
            self.fil_alpha[i] = 0.34 + 0.34 * p.flux
            self.fil_width[i] = 1.5
            self.fil_tint[i] = 0.30 + 0.45 * p.surge
            self.fil_dot[i] = 8.0

        # orbit marks
        n0 = RAY + INNER
        pick = np.linspace(0, len(ox) - 1, 8).astype(int)
        self.node_x[n0:n0 + 8] = ox[pick]
        self.node_y[n0:n0 + 8] = oy[pick]
        self.node_r[n0:n0 + 8] = 2.8
        self.node_a[n0:n0 + 8] = 0.78


# ==========================================================================
# 05 - FROND
# ==========================================================================
class Frond(Body):
    """One long curved rachis of club-tipped barbs, under a small hood.

    THE EQUATIONS
    -------------
    A single appendage in polar form,

        r(u) = L u ,  theta(u) = theta_0 + K u^1.35 + A(u) sin(2pi f u - w t)

    with the sway envelope A(u) growing toward the tip, so the whip starts at
    the head and runs out - the frond lashes rather than pivots. Barbs are
    constant-curvature arcs raised off the rachis normal, swept toward the
    tip, under an envelope that peaks near a third of the length. The hood is
    a half-ellipse of radial ribs on a short stalk.

    TELEMETRY
    ---------
    cpu  whip amplitude and frequency, strongest at the tip
    temp rachis arc opening, hood pulse, and the warm stress tint
    mem  barb expression: the frond fills in from the base outward
    io   barbs flare near the base as a burst passes into the body
    """

    KEY = "frond"

    _M = 72                  # barb stations
    _HOOD = 13
    _P = 22
    _LEN = 412.0
    _POSE = 1.02             # brings the hood to the head, as engraved

    def _sizes(self) -> tuple[int, int, int]:
        return (2 + self._M * 2 + self._HOOD + 2, self._P,
                self._M * 2 + self._HOOD + 1)

    def _build(self) -> None:
        self.core_r = 0.0
        self._rate = 1.0
        P = self._P
        self.u = np.linspace(0.0, 1.0, P)[None, :]
        self.v = np.linspace(0.055, 0.995, self._M)[:, None]
        self.dense = np.linspace(0.0, 1.0, P)
        self.hood_phi = np.linspace(-0.12, np.pi + 0.12, self._HOOD)[:, None]
        self.jit = self.rng.uniform(-0.055, 0.055, (self._M * 2, 1))
        self.ljit = self.rng.uniform(0.88, 1.12, (self._M * 2, 1))

    def _rachis(self, v, t: float, ag: float, arc: float):
        """Polar rachis. Returns world (x, y)."""
        sway = (0.018 + 0.085 * ag) * np.power(v, 1.7)
        th = (-1.42 + arc * np.power(v, 1.35)
              + sway * np.sin(TAU * 1.25 * v - t * (1.1 + 2.1 * ag)))
        r = self._LEN * v
        return (r * np.cos(th), r * np.sin(th))

    def _shape(self, p: Physiology) -> None:
        t = self.time
        M, HOOD, P = self._M, self._HOOD, self._P
        ag, warm = p.agitation, self._warm(p)
        arc = 1.90 + 0.28 * p.pulse

        dv = self.dense[:, None]
        rx, ry = self._rachis(dv, t, ag, arc)
        rx = rx[:, 0]
        ry = ry[:, 0]
        # Seat the body so the whole arc is centred in the world square.
        ox = -float(rx.mean()) * 0.85
        oy = -float(ry.mean()) * 0.85
        rx += ox
        ry += oy
        rtx, rty = _tangent(rx, ry)

        for i, off in enumerate((0.0, 4.4)):
            self.fil_x[i] = rx - rty * off
            self.fil_y[i] = ry + rtx * off
            self.fil_alpha[i] = 0.95 if off == 0.0 else 0.55
            self.fil_width[i] = 4.2 if off == 0.0 else 1.9
            self.fil_tint[i] = 0.97 if off == 0.0 else 0.64
            self.fil_dot[i] = 0.0 if off == 0.0 else 6.2

        sxv, syv = self._rachis(self.v, t, ag, arc)
        sxv = sxv[:, 0] + ox
        syv = syv[:, 0] + oy
        tx, ty = _tangent(sxv, syv)
        nx, ny = -ty, tx

        # Peaks near a third of the way out, then tapers to nothing: the tail
        # of a frond comes to a point, it does not stop.
        env = (np.power(np.sin(np.pi * np.power(self.v[:, 0], 0.72)), 0.66)
               * np.power(1.0 - self.v[:, 0], 0.42))
        gate = self._express(p, M)
        # A burst enters at the base and flares the barbs it reaches.
        flare = np.exp(-np.square(self.v[:, 0] / 0.30)) * p.surge

        for side in (0, 1):
            sgn = 1.0 - 2.0 * side
            i0 = 2 + side * M
            sl = slice(i0, i0 + M)
            jit = self.jit[side * M:(side + 1) * M]
            lj = self.ljit[side * M:(side + 1) * M]
            base = np.arctan2(ny * sgn, nx * sgn)[:, None]
            # Barbs sweep toward the TIP, and the sweep tightens along the body.
            ang = (base - sgn * (0.52 + 0.30 * self.v) + jit
                   + sgn * (0.04 + 0.16 * ag)
                   * np.sin(TAU * 2.4 * self.v - t * (1.6 + 2.2 * ag)))
            ln = (118.0 * env[:, None] * lj * (0.68 + 0.32 * p.density)
                  * (1.0 + 0.45 * flare[:, None]))
            _arcs((sxv + nx * sgn * 3.6)[:, None],
                  (syv + ny * sgn * 3.6)[:, None], ang,
                  sgn * (0.42 + 0.52 * self.v), ln, self.u,
                  self.fil_x[sl], self.fil_y[sl])
            a = np.clip(gate * (0.50 + 0.38 * env) * (1.0 + 0.6 * flare), 0, 1)
            self.fil_alpha[sl] = a
            self.fil_width[sl] = 1.8
            self.fil_tint[sl] = np.clip(0.32 + 0.44 * env + 0.30 * warm
                                        + 0.4 * flare, 0, 1)
            self.fil_dot[sl] = 5.0
            k0 = side * M
            self.node_x[k0:k0 + M] = self.fil_x[sl][:, -1]
            self.node_y[k0:k0 + M] = self.fil_y[sl][:, -1]
            self.node_r[k0:k0 + M] = 2.4 + 1.4 * env + 1.8 * flare
            self.node_a[k0:k0 + M] = a

        # ---- hood, on its stalk at the head -------------------------------
        hx0, hy0 = float(rx[0]), float(ry[0])
        hang = math.atan2(-rty[0], -rtx[0]) * 0.0 + math.atan2(rty[0], rtx[0])
        # The hood opens across the rachis, and pulses with the metabolic rate.
        open_k = 1.0 + 0.13 * math.sin(t * (0.62 + 0.8 * p.pulse))
        HR = 74.0 * open_k * (0.82 + 0.18 * p.density)
        i0 = 2 + M * 2
        sl = slice(i0, i0 + HOOD)
        phi = self.hood_phi
        cx = hx0 - rtx[0] * 26.0
        cy = hy0 - rty[0] * 26.0
        lx = HR * np.cos(phi) * self.u
        ly = -HR * 0.52 * np.sin(phi) * self.u
        wx, wy = _rot(lx, ly, hang + np.pi * 0.5)
        self.fil_x[sl] = cx + wx
        self.fil_y[sl] = cy + wy
        self.fil_alpha[sl] = 0.72
        self.fil_width[sl] = 1.8
        self.fil_tint[sl] = 0.70 + 0.25 * warm
        self.fil_dot[sl] = 3.8

        # hood rim and stalk
        i_rim = i0 + HOOD
        ring = np.linspace(-0.12, np.pi + 0.12, P)[None, :]
        lx = HR * np.cos(ring)
        ly = -HR * 0.52 * np.sin(ring)
        wx, wy = _rot(lx, ly, hang + np.pi * 0.5)
        self.fil_x[i_rim] = cx + wx[0]
        self.fil_y[i_rim] = cy + wy[0]
        self.fil_alpha[i_rim] = 0.92
        self.fil_width[i_rim] = 2.6
        self.fil_tint[i_rim] = 0.92
        self.fil_dot[i_rim] = 0.0

        i_st = i_rim + 1
        self.fil_x[i_st] = hx0 + (cx - hx0) * self.dense
        self.fil_y[i_st] = hy0 + (cy - hy0) * self.dense
        self.fil_alpha[i_st] = 0.85
        self.fil_width[i_st] = 3.2
        self.fil_tint[i_st] = 0.88
        self.fil_dot[i_st] = 0.0

        k0 = M * 2
        self.node_x[k0:k0 + HOOD] = self.fil_x[sl][:, -1]
        self.node_y[k0:k0 + HOOD] = self.fil_y[sl][:, -1]
        self.node_r[k0:k0 + HOOD] = 2.0
        self.node_a[k0:k0 + HOOD] = 0.70
        self.node_x[-1] = cx
        self.node_y[-1] = cy
        self.node_r[-1] = 3.6
        self.node_a[-1] = 0.90

        # ---- pose ----------------------------------------------------------
        drift = self._POSE + 0.045 * math.sin(t * 0.13)
        self.fil_x[:], self.fil_y[:] = _rot(self.fil_x, self.fil_y, drift)
        self.node_x[:], self.node_y[:] = _rot(self.node_x, self.node_y, drift)
