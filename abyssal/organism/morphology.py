"""Morphology: the genome of one Abyssal specimen.

Every field here is a SHAPE parameter in world units or radians. None of them
know about pixels, telemetry or time-of-day. A species is exactly one frozen
Morphology plus the archive metadata in `species.py`.

The five specimens are one taxonomic family by construction: they all share the
same radial body plan (a core, N identical lobes, a rachis per lobe, paired
barbs along it) and differ only in the numbers below. That is what stops them
reading as five unrelated effects.

QUADRILOBATA is bit-for-bit the original hand-tuned Plumiradia. The generalised
engine is verified against it in `form.self_test`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Morphology:
    # --- body plan ---------------------------------------------------------
    n_lobe: int = 4               # radial symmetry order
    n_pair: int = 20              # barb pairs per lobe
    n_pts: int = 26               # samples along a filament
    n_ring: int = 8               # core ring nodes

    # --- radial geometry (world units) ------------------------------------
    r_start: float = 34.0         # where a rachis leaves the core
    lr_base: float = 322.0        # nominal rachis length
    bl_max: float = 104.0         # nominal longest barb
    core_r: float = 26.0
    r_safe: float = 452.0         # hard ceiling, < WORLD_RADIUS
    r_ref: float = 390.0          # sway lever normalisation
    ring_r_mult: float = 1.78

    # --- vane shape --------------------------------------------------------
    bend: float = 0.22            # static rachis sweep at the tip, radians
    beta_base: float = 0.88       # barb tilt off the rachis tangent, at base
    beta_tip: float = 0.40        # ... and at the tip
    env_knee: float = 0.18        # vane envelope rise
    env_pow: float = 3.0          # vane envelope taper sharpness
    env_soft: float = 0.55        # vane envelope shoulder
    rachis_pow: float = 0.93      # radial profile exponent
    bend_pow: float = 1.7
    sk_lo: float = 0.04           # barb attachment span along the rachis
    sk_hi: float = 0.97
    theta0: float = math.pi * 0.25  # angle of lobe 0

    # --- motion ------------------------------------------------------------
    sway_floor: float = 0.028     # idle sway, radians
    sway_gain: float = 0.070
    curl_base: float = 0.30
    curl_swing: float = 0.12
    tremor_gain: float = 0.026
    breath_gain: float = 1.0      # scales all radial breathing
    rot_gain: float = 1.0         # scales slow global rotation

    # --- appearance --------------------------------------------------------
    rachis_alpha: float = 0.86        # rachis base segment
    rachis_alpha_tip: float = 0.74    # rachis tip segment
    barb_alpha: float = 0.66
    rachis_width: float = 3.6
    rachis_width_tip: float = 2.05
    barb_width: float = 2.0
    tint_bias: float = 0.0        # + shifts filaments toward the warm accent

    @property
    def n_barb(self) -> int:
        return self.n_pair * 2

    @property
    def fil_per_lobe(self) -> int:
        return 2 + self.n_barb    # 2 rachis segments + barbs

    @property
    def n_fil(self) -> int:
        return self.n_lobe * self.fil_per_lobe

    @property
    def n_node(self) -> int:
        return self.n_lobe * 6 + self.n_ring

    @property
    def lobe_step(self) -> float:
        return math.tau / self.n_lobe


# --------------------------------------------------------------------------
# The five specimens. Read these as: same body plan, different numbers.
# --------------------------------------------------------------------------

#: 4-fold, broad feathered plumes. The original, unchanged.
QUADRILOBATA = Morphology()

#: 3-fold, long and strongly curled - a spiral rather than a plume.
TRISPIRA = Morphology(
    n_lobe=3, n_pair=24, r_start=30.0, lr_base=352.0, bl_max=88.0,
    core_r=24.0, bend=0.58, bend_pow=1.45,
    beta_base=1.02, beta_tip=0.26, env_knee=0.15, env_pow=2.4, env_soft=0.62,
    theta0=math.pi * 0.5,
    sway_floor=0.022, sway_gain=0.058, curl_base=0.62, curl_swing=0.20,
    tremor_gain=0.020, rot_gain=1.6,
    rachis_alpha_tip=0.79, rachis_width=3.9, rachis_width_tip=2.20,
    barb_width=1.85, tint_bias=-0.06,
)

#: 5-fold, many very fine cilia on short rachides - dense and hazy.
PENTAFIDA = Morphology(
    n_lobe=5, n_pair=30, n_pts=22, r_start=38.0, lr_base=286.0, bl_max=74.0,
    core_r=28.0, bend=0.14, beta_base=0.72, beta_tip=0.52,
    env_knee=0.10, env_pow=2.0, env_soft=0.78,
    theta0=math.pi * 0.5,
    sway_floor=0.034, sway_gain=0.088, curl_base=0.18, curl_swing=0.09,
    tremor_gain=0.040, rot_gain=0.7,
    rachis_alpha=0.74, rachis_alpha_tip=0.62, barb_alpha=0.52,
    rachis_width=2.5, rachis_width_tip=1.55, barb_width=1.35, tint_bias=0.05,
)

#: 6-fold, short broad umbels - a squat, architectural specimen.
HEXASTOMA = Morphology(
    n_lobe=6, n_pair=16, r_start=42.0, lr_base=252.0, bl_max=122.0,
    core_r=32.0, ring_r_mult=1.55,
    bend=0.09, bend_pow=1.9, beta_base=1.16, beta_tip=0.78,
    env_knee=0.26, env_pow=4.2, env_soft=0.44,
    theta0=0.0,
    sway_floor=0.018, sway_gain=0.044, curl_base=0.14, curl_swing=0.07,
    tremor_gain=0.015, breath_gain=1.35, rot_gain=0.45,
    rachis_alpha=0.90, rachis_alpha_tip=0.80, barb_alpha=0.70,
    rachis_width=4.2, rachis_width_tip=2.60, barb_width=2.5, tint_bias=0.10,
)

#: 2-fold, two long bare whips - the sparsest of the family.
BIFIDA = Morphology(
    n_lobe=2, n_pair=26, r_start=26.0, lr_base=404.0, bl_max=62.0,
    core_r=20.0, n_ring=6, ring_r_mult=2.10,
    bend=0.34, bend_pow=2.1, beta_base=0.62, beta_tip=0.18,
    env_knee=0.32, env_pow=2.2, env_soft=0.90,
    theta0=math.pi * 0.5,
    sway_floor=0.046, sway_gain=0.120, curl_base=0.40, curl_swing=0.26,
    tremor_gain=0.052, breath_gain=0.8, rot_gain=2.2,
    rachis_alpha=0.92, rachis_alpha_tip=0.84, barb_alpha=0.58,
    rachis_width=4.6, rachis_width_tip=2.90, barb_width=1.6, tint_bias=-0.10,
)
