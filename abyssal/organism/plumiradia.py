"""PLUMIRADIA QUADRILOBATA -- "the quartered plume".

A deterministic procedural specimen: four feathered plumes radiating from a
luminous core at 45 / 135 / 225 / 315 degrees.

DESIGN NOTES (these are load-bearing, not decoration)
-----------------------------------------------------
* The whole creature is authored in POLAR coordinates. Every point is a
  (radius, angle) pair and only the ANGLE carries per-lobe phase variation --
  the radial profile is bit-for-bit identical across all four lobes. Two
  consequences fall out for free:
      1. |point| is exactly equal per lobe at every instant, so the quadrant
         balance invariant holds to floating point precision no matter how the
         lobes are swaying.
      2. Bounding the creature is a one dimensional problem: max extent is
         simply max(radius), which is clipped to R_SAFE < WORLD_RADIUS.
* Per-lobe temporal phases are always a permutation of {0, pi/2, pi, 3pi/2}, so
  no lobe is statistically favoured over any other; averaged over time all four
  see the identical distribution of motion.
* Every array is allocated exactly once in __init__. update() writes into those
  buffers with out= / in-place ops only. It never sees a pixel.
"""

from __future__ import annotations

import math

import numpy as np

from ..core.signals import Physiology
from ..core.world import WORLD_RADIUS

# --- geometry constants ---------------------------------------------------
N_LOBE = 4
N_PAIR = 20                      # barb pairs per lobe
N_BARB = N_PAIR * 2              # 40 barbs per lobe
N_RACHIS_SEG = 2                 # rachis is split so it can taper base -> tip
FIL_PER_LOBE = N_RACHIS_SEG + N_BARB       # 42
N_FIL = N_LOBE * FIL_PER_LOBE              # 168 filaments
N_PTS = 26                                 # 4368 points total
N_NODE_RACHIS = 6
N_RING = 8
N_NODE = N_LOBE * N_NODE_RACHIS + N_RING   # 32

R_START = 34.0                   # where a rachis leaves the core
LR_BASE = 322.0                  # nominal rachis length
BL_MAX = 104.0                   # nominal longest barb
BEND = 0.22                      # static rachis sweep, radians at the tip
R_REF = 390.0                    # lever normalisation for sway
R_SAFE = 452.0                   # hard radial ceiling (< WORLD_RADIUS = 460)
CORE_R = 26.0
RING_R_MULT = 1.78

# The two rachis segments overlap slightly so the join is seamless.
SEG_SPLIT = 0.57
SEG_OVERLAP = 0.02

# Barb tilt away from the radial direction: wide at the base, swept at the tip.
BETA_BASE = 0.88
BETA_TIP = 0.40

# Attachment span along the rachis.
SK_LO = 0.04
SK_HI = 0.97

# --- temporal constants (deliberately incommensurate) ---------------------
TAU = math.tau
W_BREATH1 = TAU / 11.37
W_BREATH2 = TAU / 7.13
W_BREATH3 = TAU / 17.91
W_BREATH4 = TAU / 5.87
W_SWAY_A = TAU / 6.71
W_SWAY_B = TAU / 4.29
W_CURL = TAU / 9.13
W_ROT_A = TAU / 23.70
W_ROT_B = TAU / 13.10

SWAY_FLOOR = 0.028               # radians, present even at zero agitation
SWAY_GAIN = 0.070
CURL_BASE = 0.30
CURL_SWING = 0.12
TREMOR_GAIN = 0.026


def _env(s):
    """Feather vane envelope.

    Rises fast off the core, holds a broad full-width plateau over the middle
    half of the rachis, then tapers to a point. A narrow bump would leave a
    bare whip of rachis sticking out past the vane, which reads as an antenna
    rather than a plume.
    """
    return _ENV_NORM * (s / (s + 0.18)) * np.power(
        np.maximum(1.0 - np.power(s, 3.0), 0.0), 0.55)


_ENV_NORM = 1.0
_ENV_NORM = 1.0 / float(np.max(_env(np.linspace(0.0, 1.0, 2001))))


class Plumiradia:
    """Deterministic plume organism. World units only; never sees pixels."""

    __slots__ = (
        "time", "core_r",
        "fil_x", "fil_y", "fil_alpha", "fil_width", "fil_tint",
        "node_x", "node_y", "node_r", "node_a",
        "_r", "_a", "_r4", "_a4", "_a4L", "_r4L",
        "_r1", "_a1", "_lever", "_curl1", "_tmp",
        "_q", "_q2", "_qsq2", "_rach_rc", "_rach_ang",
        "_sk", "_side", "_env", "_cosb", "_sinb_side", "_side_arr",
        "_satt093", "_aatt", "_aatt2", "_ratt", "_ratt2",
        "_bl", "_blc", "_blc2", "_bls", "_bls2", "_blsd", "_blsd2",
        "_bphase", "_cmrow", "_cmrow2",
        "_base_alpha", "_base_width", "_base_tint", "_thr", "_gate",
        "_theta", "_node_take", "_ring_ang", "_ring_cos", "_ring_sin",
        "_node_r_base", "_node_a_base", "_pulse_phase", "_trem_phase",
    )

    def __init__(self, seed: int = 20260920) -> None:
        rng = np.random.default_rng(seed)

        self.time = 0.0
        self.core_r = CORE_R
        self._pulse_phase = 0.0
        self._trem_phase = 0.0

        # ---- public buffers ------------------------------------------------
        self.fil_x = np.zeros((N_FIL, N_PTS), dtype=np.float64)
        self.fil_y = np.zeros((N_FIL, N_PTS), dtype=np.float64)
        self.fil_alpha = np.zeros(N_FIL, dtype=np.float64)
        self.fil_width = np.zeros(N_FIL, dtype=np.float64)
        self.fil_tint = np.zeros(N_FIL, dtype=np.float64)
        self.node_x = np.zeros(N_NODE, dtype=np.float64)
        self.node_y = np.zeros(N_NODE, dtype=np.float64)
        self.node_r = np.zeros(N_NODE, dtype=np.float64)
        self.node_a = np.zeros(N_NODE, dtype=np.float64)

        # ---- polar work buffers -------------------------------------------
        self._r = np.zeros((N_FIL, N_PTS), dtype=np.float64)
        self._a = np.zeros((N_FIL, N_PTS), dtype=np.float64)
        self._r4 = self._r.reshape(N_LOBE, FIL_PER_LOBE, N_PTS)
        self._a4 = self._a.reshape(N_LOBE, FIL_PER_LOBE, N_PTS)
        self._a4L = [self._a4[i] for i in range(N_LOBE)]
        self._r4L = [self._r4[i] for i in range(N_LOBE)]

        # Single canonical lobe; broadcast to the other three every frame.
        self._r1 = np.zeros((FIL_PER_LOBE, N_PTS), dtype=np.float64)
        self._a1 = np.zeros((FIL_PER_LOBE, N_PTS), dtype=np.float64)
        self._lever = np.zeros((FIL_PER_LOBE, N_PTS), dtype=np.float64)
        self._curl1 = np.zeros((FIL_PER_LOBE, N_PTS), dtype=np.float64)
        self._tmp = np.zeros((FIL_PER_LOBE, N_PTS), dtype=np.float64)

        # ---- static per-point terms ---------------------------------------
        q = np.linspace(0.0, 1.0, N_PTS)
        self._q = q
        self._q2 = q.reshape(1, N_PTS)
        self._qsq2 = (q * q).reshape(1, N_PTS)
        s_in = np.linspace(0.0, SEG_SPLIT + SEG_OVERLAP, N_PTS)
        s_out = np.linspace(SEG_SPLIT - SEG_OVERLAP, 1.0, N_PTS)
        s_seg = np.stack([s_in, s_out])
        self._rach_rc = np.power(s_seg, 0.93)          # (2, N_PTS)
        self._rach_ang = BEND * np.power(s_seg, 1.7)   # (2, N_PTS)

        # ---- static per-filament terms (canonical lobe) --------------------
        sk = np.zeros(FIL_PER_LOBE)
        side = np.zeros(FIL_PER_LOBE)
        p = np.arange(N_PAIR) / (N_PAIR - 1.0)
        base = SK_LO + (SK_HI - SK_LO) * np.power(p, 0.95)
        stagger = 0.5 * (SK_HI - SK_LO) / (N_PAIR - 1.0)
        # Barbs alternate left / right with a half-step offset, as real vanes do.
        b0 = N_RACHIS_SEG
        sk[b0::2] = base
        side[b0::2] = 1.0
        sk[b0 + 1::2] = np.minimum(base + stagger, SK_HI)
        side[b0 + 1::2] = -1.0
        sk[:b0] = 0.0
        side[:b0] = 0.0
        self._sk = sk
        self._side_arr = side

        env = _env(np.clip(sk, 1e-6, 1.0))
        env[:N_RACHIS_SEG] = 0.0
        # A whisper of per-barb variety, mirrored identically into all 4 lobes
        # so the 4-fold symmetry is untouched.
        env *= 1.0 + 0.07 * rng.standard_normal(FIL_PER_LOBE)
        self._env = np.clip(env, 0.0, 1.35)

        # A barb leaves the rachis at BETA to the LOCAL RACHIS TANGENT, not to
        # the radial direction. The rachis bends, so its tangent leans off
        # radial by gamma(s); without this correction one half of every vane
        # collapses onto the rachis while the other half fans out.
        skc = np.clip(sk, 1e-6, 1.0)
        dads = BEND * 1.7 * np.power(skc, 0.7)          # d(angle)/ds
        drds = LR_BASE * 0.93 * np.power(skc, -0.07)    # d(radius)/ds
        rr = R_START + LR_BASE * np.power(skc, 0.93)
        gamma = np.arctan2(rr * dads, drds)
        beta = BETA_BASE + (BETA_TIP - BETA_BASE) * sk
        d_ang = gamma + side * beta
        self._cosb = np.cos(d_ang)
        self._sinb_side = np.sin(d_ang)

        self._satt093 = np.power(sk, 0.93)
        self._aatt = BEND * np.power(sk, 1.7)
        self._aatt2 = self._aatt.reshape(FIL_PER_LOBE, 1)

        self._ratt = np.zeros(FIL_PER_LOBE)
        self._ratt2 = self._ratt.reshape(FIL_PER_LOBE, 1)
        self._bl = np.zeros(FIL_PER_LOBE)
        self._blc = np.zeros(FIL_PER_LOBE)
        self._blc2 = self._blc.reshape(FIL_PER_LOBE, 1)
        self._bls = np.zeros(FIL_PER_LOBE)
        self._bls2 = self._bls.reshape(FIL_PER_LOBE, 1)
        self._blsd = np.zeros(FIL_PER_LOBE)
        self._blsd2 = self._blsd.reshape(FIL_PER_LOBE, 1)
        self._cmrow = np.zeros(FIL_PER_LOBE)
        self._cmrow2 = self._cmrow.reshape(FIL_PER_LOBE, 1)

        self._bphase = rng.uniform(0.0, TAU, FIL_PER_LOBE)
        self._bphase[:N_RACHIS_SEG] = 0.0

        # ---- static appearance, tiled identically over the four lobes ------
        a1 = np.empty(FIL_PER_LOBE)
        w1 = np.empty(FIL_PER_LOBE)
        t1 = np.empty(FIL_PER_LOBE)
        a1[0], w1[0], t1[0] = 0.86, 3.6, 0.96    # rachis, base half
        a1[1], w1[1], t1[1] = 0.74, 2.05, 0.86   # rachis, tip half
        skb = sk[N_RACHIS_SEG:]
        a1[N_RACHIS_SEG:] = 0.66 * (1.0 - 0.42 * skb)
        w1[N_RACHIS_SEG:] = 2.0 * (1.0 - 0.50 * skb)
        t1[N_RACHIS_SEG:] = 0.18 + 0.58 * (1.0 - skb)
        self._base_alpha = np.tile(a1, N_LOBE)
        self._base_width = np.tile(w1, N_LOBE)
        self._base_tint = np.tile(t1, N_LOBE)

        # Golden-ratio thresholds: barbs thin out evenly rather than in clumps.
        thr = np.zeros(FIL_PER_LOBE)
        idx = np.arange(1, FIL_PER_LOBE - N_RACHIS_SEG + 1)
        thr[N_RACHIS_SEG:] = np.mod(idx * 0.6180339887498949, 1.0) * 0.95
        thr[:N_RACHIS_SEG] = -1.0          # the rachis is never faded out
        self._thr = np.tile(thr, N_LOBE)
        self._gate = np.zeros(N_FIL)

        # ---- lobe base angles ---------------------------------------------
        self._theta = np.array([math.pi * 0.25 + i * math.pi * 0.5
                                for i in range(N_LOBE)])

        # ---- node wiring ---------------------------------------------------
        # (rachis segment, point index) for each punctuating node
        node_pts = ((0, 0), (0, 9), (0, 18), (1, 4), (1, 15), (1, N_PTS - 1))
        take = []
        for L in range(N_LOBE):
            row0 = L * FIL_PER_LOBE         # rachis segment 0 of lobe L
            for seg, pj in node_pts:
                take.append((row0 + seg) * N_PTS + pj)
        self._node_take = np.array(take, dtype=np.intp)

        nr = np.array([3.2, 2.4, 2.2, 2.05, 1.9, 2.9])
        na = np.array([0.62, 0.44, 0.41, 0.38, 0.34, 0.68])
        self._node_r_base = np.concatenate(
            [np.tile(nr, N_LOBE), np.full(N_RING, 1.70)])
        self._node_a_base = np.concatenate(
            [np.tile(na, N_LOBE), np.full(N_RING, 0.44)])

        self._ring_ang = np.arange(N_RING) * (TAU / N_RING)
        self._ring_cos = np.zeros(N_RING)
        self._ring_sin = np.zeros(N_RING)

        # Settle into a valid, drawable state before the first frame.
        self.update(0.0, Physiology())

    # ------------------------------------------------------------------
    def update(self, dt: float, phys: Physiology) -> None:
        dt = 0.0 if dt < 0.0 else (0.25 if dt > 0.25 else float(dt))
        self.time += dt
        t = self.time

        ag = 0.0 if phys.agitation < 0.0 else (1.0 if phys.agitation > 1.0 else phys.agitation)
        pu = 0.0 if phys.pulse < 0.0 else (1.0 if phys.pulse > 1.0 else phys.pulse)
        de = 0.0 if phys.density < 0.0 else (1.0 if phys.density > 1.0 else phys.density)
        vit = 0.0 if phys.vitality < 0.0 else (1.0 if phys.vitality > 1.0 else phys.vitality)

        # -- scalar rhythms ------------------------------------------------
        self._pulse_phase += dt * TAU * (0.20 + 0.34 * pu)
        self._trem_phase += dt * TAU * (0.85 + 1.90 * ag)
        cp = math.sin(self._pulse_phase)
        b1 = math.sin(t * W_BREATH1)
        b2 = math.sin(t * W_BREATH2 + 1.10)
        b3 = math.sin(t * W_BREATH3 + 2.37)
        b4 = math.sin(t * W_BREATH4 + 0.51)

        lr = LR_BASE * (1.0 + 0.030 * cp + 0.018 * b3) * (1.0 + 0.055 * pu)
        self.core_r = CORE_R * (1.0 + 0.11 * cp + 0.045 * b2) * (1.0 + 0.10 * pu)
        bl_scale = (0.70 + 0.30 * de) * (1.0 + 0.035 * b1)
        grot = 0.040 * math.sin(t * W_ROT_A) + 0.022 * math.sin(t * W_ROT_B + 0.70)

        # -- canonical lobe: radii (identical for all four lobes) -----------
        r1 = self._r1
        np.multiply(self._satt093, lr, out=self._ratt)
        np.add(self._ratt, R_START, out=self._ratt)
        np.multiply(self._env, BL_MAX * bl_scale, out=self._bl)
        np.multiply(self._bl, self._cosb, out=self._blc)
        np.multiply(self._bl, self._sinb_side, out=self._bls)
        np.multiply(self._bl, self._side_arr, out=self._blsd)

        np.multiply(self._blc2, self._q2, out=r1)
        np.add(r1, self._ratt2, out=r1)
        np.multiply(self._rach_rc, lr, out=r1[:N_RACHIS_SEG])
        np.add(r1[:N_RACHIS_SEG], R_START, out=r1[:N_RACHIS_SEG])
        np.clip(r1, 1.0, R_SAFE, out=r1)

        # -- canonical lobe: angles ----------------------------------------
        a1 = self._a1
        np.multiply(self._bls2, self._q2, out=a1)
        np.divide(a1, r1, out=a1)
        np.add(a1, self._aatt2, out=a1)
        np.copyto(a1[:N_RACHIS_SEG], self._rach_ang)

        # Barb curl profile (angular), scaled per lobe further down.
        np.multiply(self._blsd2, self._qsq2, out=self._curl1)
        np.divide(self._curl1, r1, out=self._curl1)
        self._curl1[:N_RACHIS_SEG] = 0.0

        # Sway lever: the tips move, the base does not.
        np.divide(r1, R_REF, out=self._lever)
        np.power(self._lever, 1.35, out=self._lever)

        # -- broadcast to four lobes ---------------------------------------
        self._r4[:] = r1
        self._a4[:] = a1

        sway_amp = (SWAY_FLOOR + SWAY_GAIN * ag) * (0.55 + 0.45 * vit)
        curl_amp = CURL_SWING * (0.5 + 0.5 * vit)
        trem_amp = TREMOR_GAIN * ag
        tmp = self._tmp
        cm = self._cmrow

        for L in range(N_LOBE):
            ph = L * (math.pi * 0.5)
            sway = sway_amp * (0.62 * math.sin(t * W_SWAY_A + ph)
                               + 0.38 * math.sin(t * W_SWAY_B + ph + 0.90))
            curl_l = CURL_BASE + curl_amp * math.sin(t * W_CURL + ph + 1.7)
            aL = self._a4L[L]

            # per-barb tremor, phase-permuted per lobe -> statistically balanced
            np.add(self._bphase, self._trem_phase + ph, out=cm)
            np.sin(cm, out=cm)
            np.multiply(cm, trem_amp, out=cm)
            np.add(cm, curl_l, out=cm)
            np.multiply(self._curl1, self._cmrow2, out=tmp)
            np.add(aL, tmp, out=aL)

            np.multiply(self._lever, sway, out=tmp)
            np.add(aL, tmp, out=aL)
            np.add(aL, self._theta[L] + grot, out=aL)

        # -- polar -> cartesian --------------------------------------------
        np.cos(self._a, out=self.fil_x)
        np.multiply(self.fil_x, self._r, out=self.fil_x)
        np.sin(self._a, out=self.fil_y)
        np.multiply(self.fil_y, self._r, out=self.fil_y)

        # -- appearance -----------------------------------------------------
        dv = 0.35 + 0.65 * de
        np.subtract(dv, self._thr, out=self._gate)
        np.multiply(self._gate, 5.0, out=self._gate)
        np.clip(self._gate, 0.0, 1.0, out=self._gate)
        np.multiply(self._gate, 0.82, out=self._gate)
        np.add(self._gate, 0.18, out=self._gate)
        np.multiply(self._base_alpha, self._gate, out=self.fil_alpha)
        np.multiply(self.fil_alpha, (0.86 + 0.14 * vit) * (1.0 + 0.05 * b4),
                    out=self.fil_alpha)
        np.clip(self.fil_alpha, 0.0, 1.0, out=self.fil_alpha)

        np.multiply(self._base_width, 1.0 + 0.05 * cp, out=self.fil_width)

        np.multiply(self._gate, 0.22, out=self.fil_tint)
        np.add(self.fil_tint, self._base_tint, out=self.fil_tint)
        np.add(self.fil_tint, 0.09 * b4 - 0.16, out=self.fil_tint)
        np.clip(self.fil_tint, 0.0, 1.0, out=self.fil_tint)

        # -- nodes -----------------------------------------------------------
        nr = N_LOBE * N_NODE_RACHIS
        np.take(self.fil_x.reshape(-1), self._node_take, out=self.node_x[:nr])
        np.take(self.fil_y.reshape(-1), self._node_take, out=self.node_y[:nr])

        ring_r = self.core_r * RING_R_MULT
        np.add(self._ring_ang, grot * 2.0 + 0.10 * b3, out=self._ring_cos)
        np.copyto(self._ring_sin, self._ring_cos)
        np.cos(self._ring_cos, out=self._ring_cos)
        np.sin(self._ring_sin, out=self._ring_sin)
        np.multiply(self._ring_cos, ring_r, out=self.node_x[nr:])
        np.multiply(self._ring_sin, ring_r, out=self.node_y[nr:])

        np.multiply(self._node_r_base, 1.0 + 0.09 * cp, out=self.node_r)
        np.multiply(self._node_a_base,
                    (0.80 + 0.20 * vit) * (1.0 + 0.16 * cp), out=self.node_a)
        np.clip(self.node_a, 0.0, 1.0, out=self.node_a)


# ----------------------------------------------------------------------
def max_extent(org: Plumiradia) -> float:
    """Current outermost reach in world units (filament points)."""
    return float(np.max(np.hypot(org.fil_x, org.fil_y)))


def quadrant_mass(org: Plumiradia) -> tuple[float, float, float, float]:
    """Sum of |p| over filament points, bucketed by quadrant."""
    x = org.fil_x.reshape(-1)
    y = org.fil_y.reshape(-1)
    r = np.hypot(x, y)
    out = []
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        m = ((x * sx) > 0.0) & ((y * sy) > 0.0)
        out.append(float(r[m].sum()))
    return tuple(out)


def self_test() -> None:
    from ..core.signals import Physiology as _P

    org = Plumiradia()
    worst = 0.0
    worst_imbalance = 0.0

    for i in range(600):
        f = i / 599.0
        phys = _P(
            agitation=0.5 + 0.5 * math.sin(i * 0.11),
            pulse=0.5 + 0.5 * math.sin(i * 0.037 + 1.0),
            density=0.5 + 0.5 * math.sin(i * 0.071 + 2.0),
            vitality=1.0 if i % 7 else 0.55,
        )
        if i % 97 == 0:                       # slam the extremes too
            phys = _P(agitation=1.0, pulse=1.0, density=1.0, vitality=1.0)
        elif i % 89 == 0:
            phys = _P(agitation=0.0, pulse=0.0, density=0.0, vitality=0.0)
        org.update(1.0 / 60.0, phys)

        assert np.isfinite(org.fil_x).all(), f"NaN in fil_x at step {i}"
        assert np.isfinite(org.fil_y).all(), f"NaN in fil_y at step {i}"
        assert np.isfinite(org.fil_alpha).all(), f"NaN in fil_alpha at step {i}"
        assert np.isfinite(org.node_x).all(), f"NaN in node_x at step {i}"
        assert np.isfinite(org.node_y).all(), f"NaN in node_y at step {i}"
        assert math.isfinite(org.core_r), f"NaN core_r at step {i}"

        e = max_extent(org)
        worst = max(worst, e)
        assert e <= WORLD_RADIUS, f"extent {e:.2f} > WORLD_RADIUS at step {i}"

        q = quadrant_mass(org)
        imbalance = (max(q) - min(q)) / max(q)
        worst_imbalance = max(worst_imbalance, imbalance)
        assert imbalance < 0.02, (
            f"quadrant imbalance {imbalance*100:.3f}% at step {i}: {q}")

        assert (org.fil_alpha >= 0.0).all() and (org.fil_alpha <= 1.0).all()
        assert (org.fil_tint >= 0.0).all() and (org.fil_tint <= 1.0).all()
        assert (org.fil_width > 0.0).all()
        _ = f

    # Allocation discipline: steady-state update() must not rebind buffers.
    ids = (id(org.fil_x), id(org.fil_y), id(org.fil_alpha), id(org.node_x))
    org.update(1 / 60.0, _P())
    assert ids == (id(org.fil_x), id(org.fil_y), id(org.fil_alpha),
                   id(org.node_x)), "update() reallocated a public buffer"

    print(f"plumiradia self_test OK")
    print(f"  filaments {N_FIL}  points/filament {N_PTS}  "
          f"total points {N_FIL * N_PTS}  nodes {N_NODE}")
    print(f"  worst extent      {worst:.2f} / {WORLD_RADIUS:.0f} "
          f"({worst / WORLD_RADIUS * 100:.1f}%)")
    print(f"  worst quad imbal. {worst_imbalance * 100:.6f}%  (limit 2%)")


if __name__ == "__main__":
    self_test()
