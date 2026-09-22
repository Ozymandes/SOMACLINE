//! Telemetry -> Physiology, with heavy smoothing. Port of `core/physiology.py`.
//!
//! Effects are intentionally SUBTLE and bounded. The organism must stay
//! aesthetically coherent at 0% load and at 100% load alike; telemetry modulates
//! an already-alive creature, it does not drive it from zero.
//!
//! THE SYSTEM CONDITION: `activity` in 0..1 is what the propagating body pulse
//! is coloured from. Each channel is normalised against the range where it
//! means something on a laptop (cpu 0..85% concave x^0.75; thermal onset 77C
//! full at 93C; memory only the last 20%; io the smoothed flux; render the
//! frame-rate shortfall), then
//!
//! ```text
//!     base     = 0.70 cpu + 0.10 memory + 0.10 io + 0.10 render
//!     activity = base + (1 - base) * 0.92 * thermal_stress
//! ```
//!
//! No hard thresholds anywhere: the target passes a small hysteresis deadband,
//! then an asymmetric EMA (rouse ~1.4 s, calm ~3.2 s), then a rate limit.

use crate::signals::{Physiology, Telemetry};

// Time constants in seconds.
pub const TAU_AGITATION: f64 = 1.8;
pub const TAU_PULSE: f64 = 6.0;
pub const TAU_DENSITY: f64 = 3.5;
pub const TAU_FLUX: f64 = 2.2;
/// The surge envelope: fast attack, slow release.
pub const TAU_SURGE_UP: f64 = 0.22;
pub const TAU_SURGE_DOWN: f64 = 2.6;

/// The system condition: rouse faster than it calms.
pub const TAU_ACTIVITY_UP: f64 = 1.4;
pub const TAU_ACTIVITY_DOWN: f64 = 3.2;
/// Hard ceiling on how fast the condition may move, per second.
pub const ACTIVITY_RATE: f64 = 0.30;
/// Target deadband (hysteresis against sensor jitter).
pub const ACTIVITY_DEADBAND: f64 = 0.015;
pub const TAU_EXCITE: f64 = 0.9;
pub const TAU_TENSION: f64 = 2.0;

/// Normalisation ranges.
pub const CPU_FULL: f64 = 0.85;
pub const THERMAL_C: (f64, f64) = (55.0, 95.0);
pub const THERMAL_STRESS: (f64, f64) = (0.55, 0.95);
pub const MEMORY_PRESSURE: (f64, f64) = (0.80, 0.97);
pub const STRESS_BAND: (f64, f64) = (0.60, 0.97);

// Output ranges. Note the floors: the organism is never inert.
pub const AGITATION_RANGE: (f64, f64) = (0.12, 0.85);
pub const PULSE_RANGE: (f64, f64) = (0.15, 0.80);
pub const DENSITY_RANGE: (f64, f64) = (0.30, 0.95);
pub const FLUX_RANGE: (f64, f64) = (0.0, 1.0);

#[inline]
fn lerp(a: f64, b: f64, t: f64) -> f64 {
    a + (b - a) * t
}

#[inline]
fn clip01(v: f64) -> f64 {
    if v < 0.0 {
        0.0
    } else if v > 1.0 {
        1.0
    } else {
        v
    }
}

pub fn smoothstep(e0: f64, e1: f64, v: f64) -> f64 {
    let t = clip01((v - e0) / (e1 - e0));
    t * t * (3.0 - 2.0 * t)
}

/// `(activity target, cpu excitation, thermal stress)` for one sample. Pure.
pub fn condition(t: &Telemetry, flux: f64, render: f64) -> (f64, f64, f64) {
    let cpu = clip01(t.cpu_load / CPU_FULL).powf(0.75);
    let therm = if t.temp_available {
        if let Some(tc) = t.temp_c {
            clip01((tc - THERMAL_C.0) / (THERMAL_C.1 - THERMAL_C.0))
        } else {
            0.0
        }
    } else {
        0.0
    };
    let hot = smoothstep(THERMAL_STRESS.0, THERMAL_STRESS.1, therm);
    let mem = smoothstep(MEMORY_PRESSURE.0, MEMORY_PRESSURE.1, t.memory_pressure);
    let base = 0.70 * cpu + 0.10 * mem + 0.10 * clip01(flux) + 0.10 * clip01(render);
    (base + (1.0 - base) * 0.92 * hot, cpu, hot)
}

#[inline]
fn ema(cur: f64, target: f64, dt: f64, tau: f64) -> f64 {
    let k = 1.0 - (-dt / tau).exp();
    cur + (target - cur) * k
}

/// Stateful smoother. Cheap; one instance lives for the app's lifetime.
pub struct PhysiologyModel {
    p: Physiology,
    held: f64,
}

impl Default for PhysiologyModel {
    fn default() -> Self {
        Self::new()
    }
}

impl PhysiologyModel {
    pub fn new() -> Self {
        PhysiologyModel { p: Physiology::default(), held: 0.0 }
    }

    pub fn current(&self) -> &Physiology {
        &self.p
    }

    /// Advance every drive by `dt` seconds toward sample `t`.
    ///
    /// `render` is the host's frame-rate shortfall, 0..1 (0 = on cadence).
    pub fn update(&mut self, dt: f64, t: &Telemetry, render: f64) -> Physiology {
        let dt = dt.max(0.0).min(0.25);

        let target_ag = lerp(
            AGITATION_RANGE.0,
            AGITATION_RANGE.1,
            t.cpu_load.powf(0.85),
        );
        let target_pu = lerp(
            PULSE_RANGE.0,
            PULSE_RANGE.1,
            if t.temp_available { t.temperature } else { 0.35 },
        );
        let target_de = lerp(DENSITY_RANGE.0, DENSITY_RANGE.1, t.memory_pressure);
        let target_fx = lerp(
            FLUX_RANGE.0,
            FLUX_RANGE.1,
            if t.io_available { t.io_rate } else { 0.0 },
        );

        let p = self.p;
        let flux = ema(p.flux, target_fx, dt, TAU_FLUX);
        // Asymmetric envelope: attack on the raw rate, release on its own clock.
        let tau_s = if target_fx > p.surge { TAU_SURGE_UP } else { TAU_SURGE_DOWN };
        let surge = ema(p.surge, target_fx, dt, tau_s);

        let (act_t, cpu, _hot) = condition(t, flux, render);
        if (act_t - self.held).abs() > ACTIVITY_DEADBAND {
            self.held = act_t;
        }
        let tau_a = if self.held > p.activity { TAU_ACTIVITY_UP } else { TAU_ACTIVITY_DOWN };
        let act = ema(p.activity, self.held, dt, tau_a);
        let step = ACTIVITY_RATE * dt;
        let act = (p.activity + step).min((p.activity - step).max(act));

        self.p = Physiology {
            agitation: ema(p.agitation, target_ag, dt, TAU_AGITATION),
            pulse: ema(p.pulse, target_pu, dt, TAU_PULSE),
            density: ema(p.density, target_de, dt, TAU_DENSITY),
            flux,
            surge,
            vitality: 1.0,
            activity: act,
            stress: smoothstep(STRESS_BAND.0, STRESS_BAND.1, act),
            excite: ema(p.excite, cpu, dt, TAU_EXCITE),
            tension: ema(p.tension, clip01(render), dt, TAU_TENSION),
        };
        self.p
    }
}
