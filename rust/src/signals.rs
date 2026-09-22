//! Shared value types crossing workstream boundaries.
//!
//! Raw OS telemetry and organism physiology are deliberately SEPARATE types.
//! `telemetry` produces `Telemetry`. `physiology` maps `Telemetry -> Physiology`.
//! The organism modules consume only `Physiology` and have no idea the OS exists.

/// One sample of real machine state. 0..1 fields are normalised.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Telemetry {
    pub cpu_load: f64,          // 0..1
    pub memory_pressure: f64,   // 0..1
    pub temperature: f64,       // 0..1 (0 when unavailable)
    pub io_rate: f64,           // 0..1  block + network throughput

    pub temp_available: bool,
    pub io_available: bool,

    // Raw, for display only.
    pub cpu_pct: f64,
    pub mem_used_gb: f64,
    pub mem_total_gb: f64,
    pub temp_c: Option<f64>,
    pub temp_label: String,
    pub io_mb_s: f64,
    pub net_mb_s: f64,
    pub notes: String,
}

impl Telemetry {
    pub fn new() -> Self {
        Self {
            temp_label: "--".to_string(),
            ..Default::default()
        }
    }
}

/// Organism drive parameters. Smoothed, bounded, unitless.
///
/// Defaults are the RESTING physiology: the five original drives at an idle
/// machine, and every system-condition channel at exactly ZERO so a Physiology
/// built from defaults alone - including the rest state the QA gates use -
/// carries no condition at all.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Physiology {
    pub agitation: f64,   // 0..1  plume motion energy      <- cpu
    pub pulse: f64,       // 0..1  breathing rate / expansion <- temperature
    pub density: f64,     // 0..1  filament fullness          <- memory
    pub flux: f64,        // 0..1  peripheral event energy    <- io / net

    /// Rises sharply on an I/O or network burst and decays over seconds, so a
    /// transient shows on the creature as a propagating event rather than as a
    /// step change. Distinct from `flux`, which is the smoothed level.
    pub surge: f64,       // 0..1

    /// Always-present idle life, so the creature breathes with no load at all.
    pub vitality: f64,

    /// Continuous system condition, 0..1. The ONE value the pulse colour is
    /// read from: ~0 quiescent (abyssal blue), ~0.25-0.65 healthy (bio-green),
    /// ~0.65-1 stressed (amber -> orange). Smoothed, rate-limited, hysteretic.
    pub activity: f64,
    /// The stressed share of `activity`, 0..1 (smoothstep over its top third).
    /// Species read it for their stress behaviours: desync, asymmetry, curl.
    pub stress: f64,
    /// CPU excitation, 0..1, faster than `activity`: the pulse CADENCE.
    pub excite: f64,
    /// Render pressure (frame-rate shortfall), 0..1: rhythmic irregularity.
    pub tension: f64,
}

impl Default for Physiology {
    fn default() -> Self {
        Physiology {
            agitation: 0.15,
            pulse: 0.20,
            density: 0.35,
            flux: 0.0,
            surge: 0.0,
            vitality: 1.0,
            activity: 0.0,
            stress: 0.0,
            excite: 0.0,
            tension: 0.0,
        }
    }
}
