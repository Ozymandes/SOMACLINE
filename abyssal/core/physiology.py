"""Telemetry -> Physiology, with heavy smoothing.

Effects are intentionally SUBTLE and bounded. The organism must stay
aesthetically coherent at 0% load and at 100% load alike; telemetry modulates
an already-alive creature, it does not drive it from zero.
"""

from __future__ import annotations

from .signals import Physiology, Telemetry

# Time constants in seconds. Long, so readouts can jitter without the organism
# twitching, and so a telemetry stall never shows up as a visual glitch.
TAU_AGITATION = 1.8
TAU_PULSE = 6.0
TAU_DENSITY = 3.5
TAU_FLUX = 2.2
#: The surge envelope: fast attack so a burst is visible, slow release so it
#: propagates through the body instead of flickering.
TAU_SURGE_UP = 0.22
TAU_SURGE_DOWN = 2.6

# Output ranges. Note the floors: the organism is never inert.
AGITATION_RANGE = (0.12, 0.85)
PULSE_RANGE = (0.15, 0.80)
DENSITY_RANGE = (0.30, 0.95)
FLUX_RANGE = (0.0, 1.0)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class PhysiologyModel:
    """Stateful smoother. Cheap; one instance lives for the app's lifetime."""

    def __init__(self) -> None:
        self._p = Physiology()

    @property
    def current(self) -> Physiology:
        return self._p

    def update(self, dt: float, t: Telemetry) -> Physiology:
        dt = max(0.0, min(dt, 0.25))

        target_ag = _lerp(*AGITATION_RANGE, t.cpu_load ** 0.85)
        target_pu = _lerp(*PULSE_RANGE, t.temperature if t.temp_available else 0.35)
        target_de = _lerp(*DENSITY_RANGE, t.memory_pressure)
        target_fx = _lerp(*FLUX_RANGE, t.io_rate if t.io_available else 0.0)

        p = self._p
        flux = _ema(p.flux, target_fx, dt, TAU_FLUX)
        # Asymmetric envelope: attack on the raw rate, release on its own clock.
        tau_s = TAU_SURGE_UP if target_fx > p.surge else TAU_SURGE_DOWN
        surge = _ema(p.surge, target_fx, dt, tau_s)
        self._p = Physiology(
            agitation=_ema(p.agitation, target_ag, dt, TAU_AGITATION),
            pulse=_ema(p.pulse, target_pu, dt, TAU_PULSE),
            density=_ema(p.density, target_de, dt, TAU_DENSITY),
            flux=flux,
            surge=surge,
            vitality=1.0,
        )
        return self._p


def _ema(cur: float, target: float, dt: float, tau: float) -> float:
    import math
    k = 1.0 - math.exp(-dt / tau)
    return cur + (target - cur) * k
