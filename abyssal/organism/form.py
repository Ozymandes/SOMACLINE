"""The generalised Abyssal body plan.

One engine, five specimens. `AbyssalForm(morph)` is the original Plumiradia
solver with every hand-tuned constant lifted into `Morphology`, so a new
species is a parameter block rather than a new renderer.

The invariants from the original are preserved verbatim and are load-bearing:

* The creature is authored in POLAR coordinates. Only the ANGLE carries
  per-lobe phase; the radial profile is bit-identical across lobes. So
  |point| matches per lobe at every instant (the balance invariant), and
  bounding the creature stays a 1-D problem: max(radius), clipped to r_safe.
* Per-lobe temporal phases are a permutation of the N-th roots of unity, so no
  lobe is statistically favoured; averaged over time all lobes see the same
  distribution of motion.
* Every array is allocated once in __init__. update() writes in place with
  out= only, and never sees a pixel.

This engine with QUADRILOBATA reproduces the original hand-tuned `Plumiradia`
to within ~4e-13 world units (8.7e-14 % of the organism radius). It is not
bit-identical: lifting constants into parameters reassociates a few floating
point sums, e.g. `1 + a + b` becomes `1 + (a + b) * gain`. The residual is ~12
orders of magnitude below a device pixel, and `qa/gates.py` asserts the bound.
"""

from __future__ import annotations

import math

import numpy as np

from ..core.signals import Physiology
from ..core.world import WORLD_RADIUS
from .morphology import QUADRILOBATA, Morphology

N_RACHIS_SEG = 2
N_NODE_RACHIS = 6
SEG_SPLIT = 0.57
SEG_OVERLAP = 0.02

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


def _env_raw(s, knee: float, pw: float, soft: float):
    """Feather vane envelope.

    Rises fast off the core, holds a broad plateau over the middle of the
    rachis, then tapers to a point. A narrow bump would leave a bare whip of
    rachis past the vane, which reads as an antenna rather than a plume.
    """
    return (s / (s + knee)) * np.power(np.maximum(1.0 - np.power(s, pw), 0.0), soft)


def _env_norm(knee: float, pw: float, soft: float) -> float:
    return 1.0 / float(np.max(_env_raw(np.linspace(0.0, 1.0, 2001), knee, pw, soft)))


class AbyssalForm:
    """Deterministic radial organism. World units only; never sees pixels."""

    __slots__ = (
        "m", "time", "core_r",
        "fil_x", "fil_y", "fil_alpha", "fil_width", "fil_tint",
        "node_x", "node_y", "node_r", "node_a",
        "_r", "_a", "_rL", "_aL", "_a4L",
        "_r1", "_a1", "_lever", "_curl1", "_tmp",
        "_q2", "_qsq2", "_rach_rc", "_rach_ang",
        "_sk", "_env", "_cosb", "_sinb_side", "_side_arr",
        "_satt093", "_aatt2", "_ratt", "_ratt2",
        "_bl", "_blc", "_blc2", "_bls", "_bls2", "_blsd", "_blsd2",
        "_bphase", "_cmrow", "_cmrow2",
        "_base_alpha", "_base_width", "_base_tint", "_thr", "_gate",
        "_theta", "_node_take", "_ring_ang", "_ring_cos", "_ring_sin",
        "_node_r_base", "_node_a_base", "_pulse_phase", "_trem_phase",
        "_nfil", "_npts", "_nlobe", "_fpl", "_nnode_r",
    )

    def __init__(self, morph: Morphology = QUADRILOBATA, seed: int = 20260920) -> None:
        m = self.m = morph
        rng = np.random.default_rng(seed)

        NL = self._nlobe = m.n_lobe
        FPL = self._fpl = m.fil_per_lobe
        NF = self._nfil = m.n_fil
        NP = self._npts = m.n_pts
        NRING = m.n_ring
        NNR = self._nnode_r = NL * N_NODE_RACHIS

        self.time = 0.0
        self.core_r = m.core_r
        self._pulse_phase = 0.0
        self._trem_phase = 0.0

        # ---- public buffers ------------------------------------------------
        self.fil_x = np.zeros((NF, NP), dtype=np.float64)
        self.fil_y = np.zeros((NF, NP), dtype=np.float64)
        self.fil_alpha = np.zeros(NF, dtype=np.float64)
        self.fil_width = np.zeros(NF, dtype=np.float64)
        self.fil_tint = np.zeros(NF, dtype=np.float64)
        self.node_x = np.zeros(m.n_node, dtype=np.float64)
        self.node_y = np.zeros(m.n_node, dtype=np.float64)
        self.node_r = np.zeros(m.n_node, dtype=np.float64)
        self.node_a = np.zeros(m.n_node, dtype=np.float64)

        # ---- polar work buffers -------------------------------------------
        self._r = np.zeros((NF, NP), dtype=np.float64)
        self._a = np.zeros((NF, NP), dtype=np.float64)
        r_view = self._r.reshape(NL, FPL, NP)
        a_view = self._a.reshape(NL, FPL, NP)
        self._rL = r_view
        self._aL = a_view
        self._a4L = [a_view[i] for i in range(NL)]

        # Single canonical lobe; broadcast to the rest every frame.
        self._r1 = np.zeros((FPL, NP), dtype=np.float64)
        self._a1 = np.zeros((FPL, NP), dtype=np.float64)
        self._lever = np.zeros((FPL, NP), dtype=np.float64)
        self._curl1 = np.zeros((FPL, NP), dtype=np.float64)
        self._tmp = np.zeros((FPL, NP), dtype=np.float64)

        # ---- static per-point terms ---------------------------------------
        q = np.linspace(0.0, 1.0, NP)
        self._q2 = q.reshape(1, NP)
        self._qsq2 = (q * q).reshape(1, NP)
        s_in = np.linspace(0.0, SEG_SPLIT + SEG_OVERLAP, NP)
        s_out = np.linspace(SEG_SPLIT - SEG_OVERLAP, 1.0, NP)
        s_seg = np.stack([s_in, s_out])
        self._rach_rc = np.power(s_seg, m.rachis_pow)
        self._rach_ang = m.bend * np.power(s_seg, m.bend_pow)

        # ---- static per-filament terms (canonical lobe) --------------------
        sk = np.zeros(FPL)
        side = np.zeros(FPL)
        p = np.arange(m.n_pair) / (m.n_pair - 1.0)
        base = m.sk_lo + (m.sk_hi - m.sk_lo) * np.power(p, 0.95)
        stagger = 0.5 * (m.sk_hi - m.sk_lo) / (m.n_pair - 1.0)
        # Barbs alternate left / right with a half-step offset, as real vanes do.
        b0 = N_RACHIS_SEG
        sk[b0::2] = base
        side[b0::2] = 1.0
        sk[b0 + 1::2] = np.minimum(base + stagger, m.sk_hi)
        side[b0 + 1::2] = -1.0
        sk[:b0] = 0.0
        side[:b0] = 0.0
        self._sk = sk
        self._side_arr = side

        env = _env_norm(m.env_knee, m.env_pow, m.env_soft) * _env_raw(
            np.clip(sk, 1e-6, 1.0), m.env_knee, m.env_pow, m.env_soft)
        env[:N_RACHIS_SEG] = 0.0
        # A whisper of per-barb variety, mirrored identically into every lobe
        # so the N-fold symmetry is untouched.
        env *= 1.0 + 0.07 * rng.standard_normal(FPL)
        self._env = np.clip(env, 0.0, 1.35)

        # A barb leaves the rachis at BETA to the LOCAL RACHIS TANGENT, not to
        # the radial direction. The rachis bends, so its tangent leans off
        # radial by gamma(s); without this correction one half of every vane
        # collapses onto the rachis while the other half fans out.
        skc = np.clip(sk, 1e-6, 1.0)
        dads = m.bend * m.bend_pow * np.power(skc, m.bend_pow - 1.0)
        drds = m.lr_base * m.rachis_pow * np.power(skc, m.rachis_pow - 1.0)
        rr = m.r_start + m.lr_base * np.power(skc, m.rachis_pow)
        gamma = np.arctan2(rr * dads, drds)
        beta = m.beta_base + (m.beta_tip - m.beta_base) * sk
        d_ang = gamma + side * beta
        self._cosb = np.cos(d_ang)
        self._sinb_side = np.sin(d_ang)

        self._satt093 = np.power(sk, m.rachis_pow)
        aatt = m.bend * np.power(sk, m.bend_pow)
        self._aatt2 = aatt.reshape(FPL, 1)

        self._ratt = np.zeros(FPL)
        self._ratt2 = self._ratt.reshape(FPL, 1)
        self._bl = np.zeros(FPL)
        self._blc = np.zeros(FPL)
        self._blc2 = self._blc.reshape(FPL, 1)
        self._bls = np.zeros(FPL)
        self._bls2 = self._bls.reshape(FPL, 1)
        self._blsd = np.zeros(FPL)
        self._blsd2 = self._blsd.reshape(FPL, 1)
        self._cmrow = np.zeros(FPL)
        self._cmrow2 = self._cmrow.reshape(FPL, 1)

        self._bphase = rng.uniform(0.0, TAU, FPL)
        self._bphase[:N_RACHIS_SEG] = 0.0

        # ---- static appearance, tiled identically over the lobes -----------
        a1 = np.empty(FPL)
        w1 = np.empty(FPL)
        t1 = np.empty(FPL)
        a1[0], w1[0], t1[0] = m.rachis_alpha, m.rachis_width, 0.96
        a1[1], w1[1], t1[1] = m.rachis_alpha_tip, m.rachis_width_tip, 0.86
        skb = sk[N_RACHIS_SEG:]
        a1[N_RACHIS_SEG:] = m.barb_alpha * (1.0 - 0.42 * skb)
        w1[N_RACHIS_SEG:] = m.barb_width * (1.0 - 0.50 * skb)
        t1[N_RACHIS_SEG:] = 0.18 + 0.58 * (1.0 - skb)
        self._base_alpha = np.tile(a1, NL)
        self._base_width = np.tile(w1, NL)
        self._base_tint = np.clip(np.tile(t1, NL) + m.tint_bias, 0.0, 1.0)

        # Golden-ratio thresholds: barbs thin out evenly rather than in clumps.
        thr = np.zeros(FPL)
        idx = np.arange(1, FPL - N_RACHIS_SEG + 1)
        thr[N_RACHIS_SEG:] = np.mod(idx * 0.6180339887498949, 1.0) * 0.95
        thr[:N_RACHIS_SEG] = -1.0          # the rachis is never faded out
        self._thr = np.tile(thr, NL)
        self._gate = np.zeros(NF)

        # ---- lobe base angles ---------------------------------------------
        step = m.lobe_step
        self._theta = np.array([m.theta0 + i * step for i in range(NL)])

        # ---- node wiring ---------------------------------------------------
        hi = NP - 1
        node_pts = ((0, 0), (0, min(9, hi)), (0, min(18, hi)),
                    (1, min(4, hi)), (1, min(15, hi)), (1, hi))
        take = []
        for L in range(NL):
            row0 = L * FPL
            for seg, pj in node_pts:
                take.append((row0 + seg) * NP + pj)
        self._node_take = np.array(take, dtype=np.intp)

        nr = np.array([3.2, 2.4, 2.2, 2.05, 1.9, 2.9])
        na = np.array([0.62, 0.44, 0.41, 0.38, 0.34, 0.68])
        self._node_r_base = np.concatenate(
            [np.tile(nr, NL), np.full(NRING, 1.70)])
        self._node_a_base = np.concatenate(
            [np.tile(na, NL), np.full(NRING, 0.44)])

        self._ring_ang = np.arange(NRING) * (TAU / NRING)
        self._ring_cos = np.zeros(NRING)
        self._ring_sin = np.zeros(NRING)

        # Settle into a valid, drawable state before the first frame.
        self.update(0.0, Physiology())

    # ------------------------------------------------------------------
    def update(self, dt: float, phys: Physiology) -> None:
        m = self.m
        NL, FPL, NP = self._nlobe, self._fpl, self._npts
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

        bg = m.breath_gain
        lr = m.lr_base * (1.0 + (0.030 * cp + 0.018 * b3) * bg) * (1.0 + 0.055 * pu)
        self.core_r = m.core_r * (1.0 + (0.11 * cp + 0.045 * b2) * bg) * (1.0 + 0.10 * pu)
        bl_scale = (0.70 + 0.30 * de) * (1.0 + 0.035 * b1)
        grot = (0.040 * math.sin(t * W_ROT_A)
                + 0.022 * math.sin(t * W_ROT_B + 0.70)) * m.rot_gain

        # -- canonical lobe: radii (identical for every lobe) ---------------
        r1 = self._r1
        np.multiply(self._satt093, lr, out=self._ratt)
        np.add(self._ratt, m.r_start, out=self._ratt)
        np.multiply(self._env, m.bl_max * bl_scale, out=self._bl)
        np.multiply(self._bl, self._cosb, out=self._blc)
        np.multiply(self._bl, self._sinb_side, out=self._bls)
        np.multiply(self._bl, self._side_arr, out=self._blsd)

        np.multiply(self._blc2, self._q2, out=r1)
        np.add(r1, self._ratt2, out=r1)
        np.multiply(self._rach_rc, lr, out=r1[:N_RACHIS_SEG])
        np.add(r1[:N_RACHIS_SEG], m.r_start, out=r1[:N_RACHIS_SEG])
        np.clip(r1, 1.0, m.r_safe, out=r1)

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
        np.divide(r1, m.r_ref, out=self._lever)
        np.power(self._lever, 1.35, out=self._lever)

        # -- broadcast to every lobe ---------------------------------------
        self._rL[:] = r1
        self._aL[:] = a1

        sway_amp = (m.sway_floor + m.sway_gain * ag) * (0.55 + 0.45 * vit)
        curl_amp = m.curl_swing * (0.5 + 0.5 * vit)
        trem_amp = m.tremor_gain * ag
        tmp = self._tmp
        cm = self._cmrow
        step = m.lobe_step

        for L in range(NL):
            ph = L * step
            sway = sway_amp * (0.62 * math.sin(t * W_SWAY_A + ph)
                               + 0.38 * math.sin(t * W_SWAY_B + ph + 0.90))
            curl_l = m.curl_base + curl_amp * math.sin(t * W_CURL + ph + 1.7)
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
        nr = self._nnode_r
        np.take(self.fil_x.reshape(-1), self._node_take, out=self.node_x[:nr])
        np.take(self.fil_y.reshape(-1), self._node_take, out=self.node_y[:nr])

        ring_r = self.core_r * m.ring_r_mult
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
def max_extent(org: AbyssalForm) -> float:
    """Current outermost reach in world units (filament points)."""
    return float(np.max(np.hypot(org.fil_x, org.fil_y)))


def lobe_mass(org: AbyssalForm) -> list[float]:
    """Sum of |p| per lobe, bucketed by FILAMENT OWNERSHIP.

    The original checked angular quadrants, which works only while a lobe's
    barbs stay inside its own 90 degree sector. Species with a wide barb splay
    (HEXASTOMA fans +/-66 degrees into 60 degree sectors) push barbs across the
    sector boundary, so an angular histogram measures the binning, not the
    organism.

    Ownership binning tests the property the design actually claims: that no
    lobe is favoured, because every lobe is driven from one canonical radial
    profile with only its phase changed.
    """
    fpl = org.m.fil_per_lobe
    r = np.hypot(org.fil_x, org.fil_y)
    return [float(r[k * fpl:(k + 1) * fpl].sum()) for k in range(org.m.n_lobe)]


def balance_error(org: AbyssalForm) -> float:
    q = lobe_mass(org)
    hi = max(q)
    return 0.0 if hi <= 0.0 else (hi - min(q)) / hi
