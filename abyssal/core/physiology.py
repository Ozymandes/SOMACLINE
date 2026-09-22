"""Telemetry -> Physiology, with heavy smoothing.

Effects are intentionally SUBTLE and bounded. The organism must stay
aesthetically coherent at 0% load and at 100% load alike; telemetry modulates
an already-alive creature, it does not drive it from zero.

THE SYSTEM CONDITION
--------------------
Besides the five original drives, the model derives one continuous system
condition, `activity` in 0..1, which is what the propagating body pulse is
coloured from. It is NOT an average of raw values. Each channel is first
normalised against the range where it actually means something on a laptop:

    cpu        0 .. 85 %         concave (x ** 0.75): the first tens of
                                 percent are the visible change
    thermal    stress onset 77 C, full at 93 C (k10temp Tctl idles at
                                 64-74 C here, so an idle machine is NOT hot)
    memory     only the last 20 % matters (0.80 .. 0.97 used)
    io         the existing smoothed flux
    render     frame-rate shortfall against the current cadence

    base     = 0.70 cpu + 0.10 memory + 0.10 io + 0.10 render
    activity = base + (1 - base) * 0.92 * thermal_stress

so a busy cool machine reaches the green-lime band and only heat - or load
AND heat - carries the creature into amber and orange.

No hard thresholds anywhere: the target passes a small hysteresis deadband
(sensor jitter cannot move it), then an asymmetric EMA (the organism rouses in
~1.4 s and calms over ~3.2 s, so recovery reads as recovery), then a rate
limit (no telemetry spike can snap the colour). All of it runs at render
cadence on the latest 5 Hz sample, which is what interpolates between samples.
"""

from __future__ import annotations

import math

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

#: The system condition: rouse faster than it calms.
TAU_ACTIVITY_UP = 1.4
TAU_ACTIVITY_DOWN = 3.2
#: Hard ceiling on how fast the condition may move, per second.
ACTIVITY_RATE = 0.30
#: Target deadband. A new target is adopted only when it differs from the
#: held one by more than this - the hysteresis that stops a reading hovering
#: on a boundary from dithering the colour.
ACTIVITY_DEADBAND = 0.015
TAU_EXCITE = 0.9
TAU_TENSION = 2.0

#: Normalisation ranges (see module docstring).
CPU_FULL = 0.85
THERMAL_C = (55.0, 95.0)
THERMAL_STRESS = (0.55, 0.95)      # of the normalised thermal range
MEMORY_PRESSURE = (0.80, 0.97)
STRESS_BAND = (0.60, 0.97)         # of activity

# Output ranges. Note the floors: the organism is never inert.
AGITATION_RANGE = (0.12, 0.85)
PULSE_RANGE = (0.15, 0.80)
DENSITY_RANGE = (0.30, 0.95)
FLUX_RANGE = (0.0, 1.0)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def smoothstep(e0: float, e1: float, v: float) -> float:
    t = _clip01((v - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


def condition(t: Telemetry, flux: float = 0.0, render: float = 0.0
              ) -> tuple[float, float, float]:
    """(activity target, cpu excitation, thermal stress) for one sample. Pure."""
    cpu = _clip01(t.cpu_load / CPU_FULL) ** 0.75
    if t.temp_available and t.temp_c is not None:
        therm = _clip01((t.temp_c - THERMAL_C[0]) / (THERMAL_C[1] - THERMAL_C[0]))
    else:
        therm = 0.0
    hot = smoothstep(*THERMAL_STRESS, therm)
    mem = smoothstep(*MEMORY_PRESSURE, t.memory_pressure)
    base = 0.70 * cpu + 0.10 * mem + 0.10 * _clip01(flux) + 0.10 * _clip01(render)
    return base + (1.0 - base) * 0.92 * hot, cpu, hot


class PhysiologyModel:
    """Stateful smoother. Cheap; one instance lives for the app's lifetime."""

    def __init__(self) -> None:
        self._p = Physiology()
        self._held = 0.0

    @property
    def current(self) -> Physiology:
        return self._p

    def update(self, dt: float, t: Telemetry, render: float = 0.0) -> Physiology:
        """Advance every drive by `dt` seconds toward sample `t`.

        `render` is the host's frame-rate shortfall, 0..1 (0 = on cadence).
        """
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

        act_t, cpu, _hot = condition(t, flux, render)
        if abs(act_t - self._held) > ACTIVITY_DEADBAND:
            self._held = act_t
        tau_a = TAU_ACTIVITY_UP if self._held > p.activity else TAU_ACTIVITY_DOWN
        act = _ema(p.activity, self._held, dt, tau_a)
        step = ACTIVITY_RATE * dt
        act = min(p.activity + step, max(p.activity - step, act))

        self._p = Physiology(
            agitation=_ema(p.agitation, target_ag, dt, TAU_AGITATION),
            pulse=_ema(p.pulse, target_pu, dt, TAU_PULSE),
            density=_ema(p.density, target_de, dt, TAU_DENSITY),
            flux=flux,
            surge=surge,
            vitality=1.0,
            activity=act,
            stress=smoothstep(*STRESS_BAND, act),
            excite=_ema(p.excite, cpu, dt, TAU_EXCITE),
            tension=_ema(p.tension, _clip01(render), dt, TAU_TENSION),
        )
        return self._p


def _ema(cur: float, target: float, dt: float, tau: float) -> float:
    k = 1.0 - math.exp(-dt / tau)
    return cur + (target - cur) * k
