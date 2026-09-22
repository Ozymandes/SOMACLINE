//! The five specimens: source equations, seated in the world, under telemetry.
//! Port of `organism/mathforms.py`.
//!
//! One `SourceDef` from `sources.rs` — a published generative sketch, ported
//! verbatim — plus a clock that runs in real seconds, a seat in the logical
//! world, and a physiology.
//!
//! THE PERTURBATION RULE: the equation is never edited. Telemetry acts on the
//! solved point cloud, and only in ways the creature could plausibly do to
//! itself. Every specimen reduces to its published form when physiology is at
//! rest (except colour, which pulses subtly even at rest).
//!
//! THE PROPAGATING PULSE: each sample carries a stable body coordinate `u`
//! in 0..1 derived once from the equation's own organisation, plus a periphery
//! (`lat`) and a part (`grp`). A wave phase is integrated in real seconds and
//! each point reads a leading edge, a trailing glow and a small afterpulse.
//! `f` drives excitation; hue is read off the system condition and warms along
//! the trail under stress.
//!
//! NUMERICS: point math runs in f32 exactly like the NumPy original (f64
//! scalars cast at the point of use, numpy weak-scalar semantics); the
//! integrators (time, wave, aux, jit) stay f64 exactly as in Python.

use crate::signals::Physiology;
use crate::sources::{by_key, c01, c02, c03, c04, c05, SourceDef};
use crate::world::WORLD_RADIUS;

/// The originals run at 60 frames a second.
pub const SOURCE_FPS: f64 = 60.0;

/// Smallest fraction of its own point set a specimen will express.
pub const MIN_EXPRESSION: f64 = 0.42;

/// Hard ceiling. Nothing may reach the bezel whatever the telemetry does.
pub const R_SAFE: f64 = WORLD_RADIUS - 6.0;

/// Pulse shape, as a share of one cycle.
pub const PULSE_LEAD: f64 = 0.035;
pub const PULSE_TRAIL: f64 = 0.10;
pub const AFTER_AT: f64 = 0.26;
pub const AFTER_W: f64 = 0.045;
pub const AFTER_K: f64 = 0.30;

/// System condition -> pulse hue (0 electric blue .. 1 orange).
const HUE_ACT: [f64; 6] = [0.00, 0.22, 0.45, 0.65, 0.82, 1.00];
const HUE_VAL: [f64; 6] = [0.05, 0.30, 0.50, 0.64, 0.80, 0.97];

/// The pulse hue the system condition asks for, 0..1. Piecewise-linear.
pub fn state_hue(activity: f64) -> f64 {
    let a = activity.clamp(0.0, 1.0);
    for j in 1..HUE_ACT.len() {
        if a <= HUE_ACT[j] {
            let t = (a - HUE_ACT[j - 1]) / (HUE_ACT[j] - HUE_ACT[j - 1]);
            return HUE_VAL[j - 1] + (HUE_VAL[j] - HUE_VAL[j - 1]) * t;
        }
    }
    HUE_VAL[HUE_VAL.len() - 1]
}

#[inline]
fn smoothstep(e0: f64, e1: f64, v: f64) -> f64 {
    let t = ((v - e0) / (e1 - e0).max(1e-9)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

#[inline]
fn norm(v: &[f64]) -> Vec<f32> {
    let lo = v.iter().cloned().fold(f64::INFINITY, f64::min);
    let hi = v.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let span = (hi - lo).max(1e-9);
    v.iter().map(|&x| ((x - lo) / span) as f32).collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SpeciesKind {
    SigmoidPlume,
    CoupledBodies,
    MirroredAlien,
    QuadPlume,
    SingleFeather,
}

impl SpeciesKind {
    /// Pulse cadence at rest, in body traversals per second.
    fn pulse_hz(self) -> f64 {
        match self {
            SpeciesKind::SigmoidPlume => 0.20,
            SpeciesKind::CoupledBodies => 0.30,
            SpeciesKind::MirroredAlien => 0.24,
            SpeciesKind::QuadPlume => 0.34,
            SpeciesKind::SingleFeather => 0.30,
        }
    }

    /// Wavefronts along the body at once.
    fn waves(self) -> f64 {
        match self {
            SpeciesKind::QuadPlume => 1.3,
            _ => 1.0,
        }
    }
}

/// One specimen. Solves its equation each frame into world coordinates.
pub struct SourceBody {
    kind: SpeciesKind,
    src: &'static SourceDef,
    pub time: f64,
    seed: u64,
    core_r: f64,

    perm: Vec<f32>,
    mod2: Vec<f32>,
    mod4: Vec<f32>,

    // THE ANATOMY: stable per-sample body coordinates, permuted order.
    u: Vec<f32>,
    lat: Vec<f32>,
    grp: Vec<f32>,

    // pulse scratch, reused every frame
    s: Vec<f32>,
    f: Vec<f32>,
    e: Vec<f32>,
    h: Vec<f32>,
    t1: Vec<f32>,

    wave: f64, // integrated pulse phase, in cycles
    aux: f64,  // integrated species phase (exchange, chase, cilia)
    jit: f64,  // integrated irregularity phase
    hue: f64,
    amp: f64,

    x: Vec<f32>,
    y: Vec<f32>,
    w: Vec<f32>,
    live: usize,
    warm: f64,
    expr: f64,
    dt: f64,
}

const PULSE_GAIN: f64 = 1.8;

/// The f32 permutation of the original loop's indices, dumped once from the
/// Python oracle so a fraction of the body means the SAME uniform thinning
/// in both implementations. mod2/mod4 derive from it exactly as in Python.
fn load_perm(key: &str) -> Vec<f32> {
    let bytes: &[u8] = match key {
        "s01" => include_bytes!("../tests/golden/s01_perm.f32"),
        "s02" => include_bytes!("../tests/golden/s02_perm.f32"),
        "s03" => include_bytes!("../tests/golden/s03_perm.f32"),
        "s04" => include_bytes!("../tests/golden/s04_perm.f32"),
        "s05" => include_bytes!("../tests/golden/s05_perm.f32"),
        _ => panic!("unknown species key: {key}"),
    };
    bytes
        .chunks_exact(4)
        .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .collect()
}

/// (u, lat, grp) over the ORIGINAL loop index, in f64, then cast f32.
fn anatomy(kind: SpeciesKind, n: usize) -> (Vec<f32>, Vec<f32>, Vec<f32>) {
    let i: Vec<f64> = (0..n).map(|k| k as f64).collect();
    let zeros = || vec![0.0f32; n];
    match kind {
        SpeciesKind::SigmoidPlume => {
            // d = mag(k, e) is static per index and runs root -> tail.
            let k: Vec<f64> = i.iter().map(|&x| 4.0 * (x / 21.0).cos()).collect();
            let e: Vec<f64> = i.iter().map(|&x| (x / 235.0) / 8.0 - 20.0).collect();
            let d: Vec<f64> = k.iter().zip(&e).map(|(&a, &b)| a.hypot(b)).collect();
            let u = norm(&d);
            let lat: Vec<f32> = k.iter().map(|&v| (v.abs() / 4.0) as f32).collect();
            (u, lat, zeros())
        }
        SpeciesKind::CoupledBodies => {
            // m = i%2*9 is the body; d = mag(k, e)/4 runs along each spiral.
            let k: Vec<f64> = i.iter().map(|&x| 9.0 * (x / 81.0).cos()).collect();
            let e: Vec<f64> = i.iter().map(|&x| x / 765.0 - 13.0).collect();
            let d: Vec<f64> = k.iter().zip(&e).map(|(&a, &b)| a.hypot(b) / 4.0).collect();
            let u = norm(&d);
            let lat: Vec<f32> = k.iter().map(|&v| (v.abs() / 9.0) as f32).collect();
            let grp: Vec<f32> = i.iter().map(|&x| (x % 2.0) as f32).collect();
            (u, lat, grp)
        }
        SpeciesKind::MirroredAlien => {
            // 200x200 grid; the mirror plane is k = 0.
            let k: Vec<f64> = i.iter().map(|&x| (x % 200.0) / 8.0 - 12.5).collect();
            let e: Vec<f64> = i.iter().map(|&x| (x / 200.0) / 8.0 - 12.5).collect();
            let d: Vec<f64> = k.iter().zip(&e).map(|(&a, &b)| a.hypot(b)).collect();
            let u = norm(&d);
            let grp: Vec<f32> = k.iter().map(|&v| if v > 0.0 { 1.0f32 } else { 0.0f32 }).collect();
            (u, zeros(), grp)
        }
        SpeciesKind::QuadPlume => {
            // y = i/500 runs along each plume; i%4 is the plume itself.
            let y: Vec<f64> = i.iter().map(|&x| x / 500.0).collect();
            let u = norm(&y);
            let grp: Vec<f32> = i.iter().map(|&x| (x % 4.0) as f32).collect();
            (u, zeros(), grp)
        }
        SpeciesKind::SingleFeather => {
            // d = mag(k,e)**2/59 + 4 grows from the base to the extremity.
            let k: Vec<f64> = i
                .iter()
                .map(|&x| 5.0 * ((x % 200.0) / 14.0).cos() * ((x / 43.0) / 30.0).cos())
                .collect();
            let e: Vec<f64> = i.iter().map(|&x| (x / 43.0) / 8.0 - 13.0).collect();
            let d: Vec<f64> = k
                .iter()
                .zip(&e)
                .map(|(&a, &b)| a.hypot(b) * a.hypot(b) / 59.0 + 4.0)
                .collect();
            let u = norm(&d);
            let lat: Vec<f32> = i
                .iter()
                .map(|&x| (((x % 200.0) / 14.0).cos().abs()) as f32)
                .collect();
            (u, lat, zeros())
        }
    }
}

/// A one-shot envelope over one chase cycle: sharp on, slow off.
fn burst(z: f64) -> f64 {
    let z = z.rem_euclid(1.0);
    (-z / 0.30).exp() * (1.0 - (-(1.0 - z) / 0.05).exp())
}

impl SourceBody {
    pub fn build(key: &str, _seed: u64) -> SourceBody {
        let src = by_key(key).unwrap_or_else(|| panic!("unknown species key: {key}"));
        let kind = match key {
            "s01" => SpeciesKind::SigmoidPlume,
            "s02" => SpeciesKind::CoupledBodies,
            "s03" => SpeciesKind::MirroredAlien,
            "s04" => SpeciesKind::QuadPlume,
            "s05" => SpeciesKind::SingleFeather,
            _ => panic!("unknown species key: {key}"),
        };
        let n = src.n;
        let perm = load_perm(key);
        let mod2: Vec<f32> = perm.iter().map(|&v| v % 2.0f32).collect();
        let mod4: Vec<f32> = perm.iter().map(|&v| v % 4.0f32).collect();
        // anatomy over the original indices, then reordered by the permutation
        let (u0, lat0, grp0) = anatomy(kind, n);
        let mut u = vec![0.0f32; n];
        let mut lat = vec![0.0f32; n];
        let mut grp = vec![0.0f32; n];
        for (k, &p) in perm.iter().enumerate() {
            let o = p as usize;
            u[k] = u0[o];
            lat[k] = lat0[o];
            grp[k] = grp0[o];
        }
        let mut body = SourceBody {
            kind,
            src,
            time: 0.0,
            seed: _seed,
            core_r: 0.0,
            perm,
            mod2,
            mod4,
            u,
            lat,
            grp,
            s: vec![0.0; n],
            f: vec![0.0; n],
            e: vec![0.0; n],
            h: vec![0.0; n],
            t1: vec![0.0; n],
            wave: 0.0,
            aux: 0.0,
            jit: 0.0,
            hue: state_hue(0.0),
            amp: 0.0,
            x: vec![0.0; n],
            y: vec![0.0; n],
            w: vec![1.0; n],
            live: n,
            warm: 0.0,
            expr: 1.0,
            dt: 1.0 / SOURCE_FPS,
        };
        body.update(0.0, &Physiology::default());
        body
    }

    // -- contract ---------------------------------------------------------
    #[inline]
    pub fn n_points(&self) -> usize {
        self.live
    }

    /// Thermal stress, 0..1. The renderer tints with this.
    #[inline]
    pub fn warmth(&self) -> f64 {
        self.warm
    }

    /// Retention for THIS frame. Memory pressure lengthens the trail; the
    /// retention is raised to the frame's own duration so the trail's length
    /// stays constant in SECONDS.
    pub fn persist(&self) -> f64 {
        if self.src.persist <= 0.0 {
            return 0.0;
        }
        let base = (self.src.persist + 0.16 * self.expr).min(0.88);
        base.powf((self.dt * SOURCE_FPS).clamp(0.25, 4.0))
    }

    /// (world x, world y, weight) for this frame.
    #[inline]
    pub fn points(&self) -> (&[f32], &[f32], &[f32]) {
        (
            &self.x[..self.live],
            &self.y[..self.live],
            &self.w[..self.live],
        )
    }

    /// (excitation 0..1, hue 0..1) per point, aligned with points().
    #[inline]
    pub fn excitation(&self) -> (&[f32], &[f32]) {
        (&self.e[..self.live], &self.h[..self.live])
    }

    #[inline]
    pub fn time(&self) -> f64 {
        self.time
    }

    #[inline]
    pub fn src(&self) -> &'static SourceDef {
        self.src
    }

    /// Farthest VISIBLE point from the world origin, in world units.
    /// Weighted: a zero-intensity sample is not on screen and cannot clip.
    pub fn max_extent(&self) -> f64 {
        let n = self.live;
        let mut m = 0.0f32;
        for k in 0..n {
            if self.w[k] > 0.0 {
                let d = self.x[k].hypot(self.y[k]);
                if d > m {
                    m = d;
                }
            }
        }
        m as f64
    }

    /// Advance the source clock and re-solve. Allocation-free.
    pub fn update(&mut self, dt: f64, p: &Physiology) {
        // CPU raises the phase rate, but only within a band: this is the one
        // place a global speed-up is legitimate.
        let rate = 1.0 + 0.5 * p.agitation;
        self.time += dt * SOURCE_FPS * self.src.dt * rate;
        if dt > 0.0 {
            self.dt = dt;
        }
        // The pulse clock. Integrated, so a change of cadence bends the
        // front's speed instead of teleporting it.
        self.jit += dt * 2.3;
        let wobble = 0.30 * f64::max(p.tension, 0.6 * p.stress) * self.jit.sin();
        self.wave += dt * self.kind.pulse_hz() * (1.0 + PULSE_GAIN * p.excite) * (1.0 + wobble);
        self.advance_aux(dt, p);

        self.warm = smoothstep(0.58, 0.95, p.pulse);
        self.expr = (MIN_EXPRESSION + (1.0 - MIN_EXPRESSION) * (0.30 + 0.70 * p.density)).min(1.0);
        let nl = 64usize.max((self.src.n as f64 * self.expr) as usize);
        self.live = nl;

        let kind = self.kind;
        let time = self.time;

        // 1. solve the equation into (x, y, w) — permuted index order.
        {
            let SourceBody { perm, x, y, w, .. } = self;
            let idx = &perm[..nl];
            let xb = &mut x[..nl];
            let yb = &mut y[..nl];
            let wb = &mut w[..nl];
            match kind {
                SpeciesKind::SigmoidPlume => c01(time, idx, xb, yb, wb),
                SpeciesKind::CoupledBodies => {
                    c02(time, idx, xb, yb);
                    wb.fill(1.0);
                }
                SpeciesKind::MirroredAlien => {
                    c03(time, idx, xb, yb);
                    wb.fill(1.0);
                }
                SpeciesKind::QuadPlume => {
                    c04(time, idx, xb, yb);
                    wb.fill(1.0);
                }
                SpeciesKind::SingleFeather => {
                    c05(time, idx, xb, yb);
                    wb.fill(1.0);
                }
            }
        }

        // 2. the propagating pulse: excitation + hue per point.
        let (hue, amp) = {
            let SourceBody { kind, u, lat, grp, s, f, e, h, t1, .. } = self;
            run_pulse(
                kind, nl, p, self.wave, self.aux, u, lat, grp, s, f, e, h, t1,
            )
        };
        self.hue = hue;
        self.amp = amp;

        // 3. small, species-specific perturbation (source coordinates).
        {
            let SourceBody { kind, lat, grp, u, mod2, mod4, t1, x, y, w, .. } = self;
            run_perturb(
                kind, nl, p, time, self.aux, x, y, w, lat, grp, u, mod2, mod4, t1,
            );
        }

        // 4. seat: translate to the creature's own frame centre, scale once.
        let seat_k = (crate::sources::WORLD_FIT / self.src.frame_half) as f32;
        let cx = self.src.frame_cx as f32;
        let cy = self.src.frame_cy as f32;
        let r_safe = R_SAFE as f32;
        let xb = &mut self.x[..nl];
        let yb = &mut self.y[..nl];
        let wb = &mut self.w[..nl];
        for k in 0..nl {
            xb[k] = (xb[k] - cx) * seat_k;
            yb[k] = (yb[k] - cy) * seat_k;
        }
        // 5. A sample outside the design radius is made INVISIBLE, not
        // clamped onto it. Non-finite samples get zero weight, never dropped.
        for k in 0..nl {
            let (mut xv, mut yv, mut wv) = (xb[k], yb[k], wb[k]);
            let mut r = xv.hypot(yv);
            r = r.max(1e-9f32);
            if !(r <= r_safe) {
                wv = 0.0;
            }
            r = (r_safe / r).min(1.0);
            xv *= r;
            yv *= r;
            if !(xv.is_finite() && yv.is_finite()) {
                xv = 0.0;
                yv = 0.0;
                wv = 0.0;
            }
            xb[k] = xv;
            yb[k] = yv;
            wb[k] = wv;
        }
    }

    /// Advance the species' own secondary phase.
    fn advance_aux(&mut self, dt: f64, p: &Physiology) {
        let sdt = dt * SOURCE_FPS * self.src.dt;
        self.aux += match self.kind {
            // ciliary ripple, at the rate it was authored for (3.1 / unit t)
            SpeciesKind::SigmoidPlume => sdt * 3.1 * (1.0 + 0.9 * p.excite),
            // the argument's own slow clock
            SpeciesKind::CoupledBodies => dt * 0.9,
            SpeciesKind::MirroredAlien => dt * 0.7,
            // one full round of the four plumes; CPU raises the chase rate
            SpeciesKind::QuadPlume => dt * 0.22 * (1.0 + 2.0 * p.excite),
            // rib-wave phase at the whip's authored rate (2.7 / unit t)
            SpeciesKind::SingleFeather => sdt * 2.7 * (1.0 + 0.6 * p.excite),
        };
    }
}

/// The wave coordinate of every point: wave - lambda*u - species offsets.
#[inline]
fn phase_base(kind: SpeciesKind, n: usize, wave: f64, u: &[f32], s: &mut [f32]) {
    let neg_waves = (-kind.waves()) as f32;
    let w32 = wave as f32;
    for k in 0..n {
        s[k] = u[k] * neg_waves + w32;
    }
}

/// Fill excitation and hue for the first `n` points.
#[allow(clippy::too_many_arguments)]
fn run_pulse(
    kind: &SpeciesKind,
    n: usize,
    p: &Physiology,
    wave: f64,
    aux: f64,
    u: &[f32],
    lat: &[f32],
    grp: &[f32],
    s: &mut [f32],
    f: &mut [f32],
    e: &mut [f32],
    h: &mut [f32],
    t1: &mut [f32],
) -> (f64, f64) {
    let sb = &mut s[..n];
    phase_base(*kind, n, wave, &u[..n], sb);
    match kind {
        SpeciesKind::SigmoidPlume => {
            // filament tips respond just after the spine
            for k in 0..n {
                sb[k] -= 0.07f32 * lat[k];
            }
            if p.stress > 0.01 {
                // under stress the front turns ragged along the body
                let k05 = (0.05 * p.stress) as f32;
                let w23 = (wave * 2.3) as f32;
                for k in 0..n {
                    sb[k] -= k05 * (u[k] * 17.0f32 + w23).sin();
                }
            }
        }
        SpeciesKind::CoupledBodies => {
            // The second body fires half a cycle after the first; stress
            // pushes the pair out of step - a bounded argument.
            let lag = 0.5 + 0.22 * p.stress * aux.sin();
            let lag32 = lag as f32;
            for k in 0..n {
                sb[k] -= lag32 * grp[k];
            }
        }
        SpeciesKind::MirroredAlien => {
            if p.stress > 0.0 {
                // one side falls behind the other; symmetric again at rest
                let lag = 0.12 * p.stress * (0.6 + 0.4 * aux.sin());
                let lag32 = lag as f32;
                for k in 0..n {
                    sb[k] -= lag32 * grp[k];
                }
            }
        }
        SpeciesKind::QuadPlume => {}
        SpeciesKind::SingleFeather => {
            // the ribs light just after the shaft beside them
            for k in 0..n {
                sb[k] -= 0.09f32 * lat[k];
            }
        }
    }

    // floor modulo into 0..1
    for k in 0..n {
        sb[k] = sb[k].rem_euclid(1.0f32);
    }

    let lead_k = (-1.0 / PULSE_LEAD) as f32;
    let trail_k = (-1.0 / PULSE_TRAIL) as f32;
    let after_k = (1.0 / AFTER_W) as f32;
    let after_amp = AFTER_K as f32;
    let (fb, t1b) = (&mut f[..n], &mut t1[..n]);
    for k in 0..n {
        let sv = sb[k];
        // leading edge: 0 just ahead of the front, 1 on and behind it
        let mut tv = 1.0f32 - sv;
        tv = (tv * lead_k).exp();
        tv = 1.0f32 - tv;
        // trailing glow
        let mut fv = (sv * trail_k).exp() * tv;
        // afterpulse
        let mut av = sv - AFTER_AT as f32;
        av = av * after_k;
        av = -av * av;
        av = av.exp();
        fv += after_amp * av;
        fb[k] = fv;
        t1b[k] = av;
    }

    // species modulation of the envelope (quad plume chase)
    if *kind == SpeciesKind::QuadPlume {
        let sync = 0.85 * smoothstep(0.35, 1.0, p.stress);
        let mut env = [0.0f32; 4];
        for (l, env_l) in env.iter_mut().enumerate() {
            let v = 0.22
                + 0.78 * burst(aux - l as f64 * 0.25 * (1.0 - sync))
                + 0.8 * p.surge * burst(aux - (((l + 2) % 4) as f64) * 0.25);
            *env_l = v as f32;
        }
        for k in 0..n {
            let gain = env[grp[k] as usize];
            fb[k] *= gain;
            t1b[k] = gain; // keep the per-point gain for the swell
        }
    }

    for v in fb[..n].iter_mut() {
        *v = v.clamp(0.0, 1.0);
    }

    // Excitation: subtle at rest, full under load.
    let amp = 0.42 + 0.58 * (p.activity + 0.25 * p.surge).min(1.0);
    let amp32 = amp as f32;
    for k in 0..n {
        e[k] = fb[k] * amp32;
    }
    // Hue: the condition's hue at the leading edge, warming along the trail.
    let hue = state_hue(p.activity);
    let hue32 = hue as f32;
    let stress_hue = (0.16 * p.stress) as f32;
    for k in 0..n {
        h[k] = sb[k] * stress_hue + hue32;
    }

    // species hue bias
    match kind {
        SpeciesKind::SigmoidPlume => {
            if p.stress > 0.0 {
                // heat reaches the central spine before the filaments
                let a = (0.10 * p.stress) as f32;
                let b = (0.16 * p.stress) as f32;
                for k in 0..n {
                    h[k] += a - b * lat[k];
                }
            }
        }
        SpeciesKind::CoupledBodies => {
            if p.stress > 0.0 {
                // orange starts in one body and reaches the other late
                let a = (0.08 * p.stress) as f32;
                let b = (0.13 * p.stress) as f32;
                for k in 0..n {
                    h[k] += a - b * grp[k];
                }
            }
        }
        SpeciesKind::MirroredAlien => {
            if p.stress > 0.0 {
                // the leading side warms first
                let a = (0.09 * p.stress) as f32;
                let b = a;
                for k in 0..n {
                    h[k] += a - b * grp[k];
                }
            }
        }
        SpeciesKind::QuadPlume => {
            if p.stress > 0.0 {
                let a = (0.07 * p.stress) as f32;
                for k in 0..n {
                    h[k] += a * (t1b[k] - 0.6f32);
                }
            }
        }
        SpeciesKind::SingleFeather => {
            if p.stress > 0.0 {
                // the base warms first; the signal carries it out to the tip
                let a = (0.10 * p.stress) as f32;
                let b = a;
                for k in 0..n {
                    h[k] += a - b * u[k];
                }
            }
        }
    }
    for v in h[..n].iter_mut() {
        *v = v.clamp(0.0, 1.0);
    }
    (hue, amp)
}

/// The wavefront swell: a local radial bulge riding the pulse, in SOURCE
/// coordinates. Zero at rest by construction except for the small rest
/// agitation share, exactly as Python.
#[allow(clippy::too_many_arguments)]
fn swell(
    n: usize,
    p: &Physiology,
    f: &[f32],
    x: &mut [f32],
    y: &mut [f32],
    cx: f32,
    cy: f32,
    gain: Option<&[f32]>,
) {
    let k = 0.030 * p.agitation + 0.035 * p.stress;
    if k <= 0.0 {
        return;
    }
    let k32 = k as f32;
    for idx in 0..n {
        let mut g = f[idx] * k32;
        if let Some(gn) = gain {
            g *= gn[idx];
        }
        x[idx] = (x[idx] - cx) * (1.0f32 + g) + cx;
        y[idx] = (y[idx] - cy) * (1.0f32 + g) + cy;
    }
}

#[allow(clippy::too_many_arguments)]
fn run_perturb(
    kind: &SpeciesKind,
    n: usize,
    p: &Physiology,
    t: f64,
    aux: f64,
    x: &mut [f32],
    y: &mut [f32],
    w: &mut [f32],
    lat: &[f32],
    grp: &[f32],
    u: &[f32],
    mod2: &[f32],
    mod4: &[f32],
    t1: &[f32],
) {
    let (xb, yb, wb) = (&mut x[..n], &mut y[..n], &mut w[..n]);
    match kind {
        SpeciesKind::SigmoidPlume => perturb_s01(n, p, t, aux, xb, yb, wb, lat),
        SpeciesKind::CoupledBodies => perturb_s02(n, p, t, xb, yb, wb, mod2),
        SpeciesKind::MirroredAlien => perturb_s03(n, p, t, xb, yb, wb),
        SpeciesKind::QuadPlume => perturb_s04(n, p, t, xb, yb, wb, mod4, t1),
        SpeciesKind::SingleFeather => perturb_s05(n, p, t, aux, xb, yb, wb, u),
    }
}

// =========================================================================
// 01 - the sigmoid plume
// =========================================================================
fn perturb_s01(
    n: usize,
    p: &Physiology,
    t: f64,
    aux: f64,
    x: &mut [f32],
    y: &mut [f32],
    w: &mut [f32],
    lat: &[f32],
) {
    let t32 = t as f32;
    let aux32 = aux as f32;
    // The body runs head to tail with the index, so `y` is arc position.
    let mut s_clip = vec![0.0f32; n];
    for k in 0..n {
        s_clip[k] = (y[k] / 320.0f32).clamp(0.0, 1.0);
    }
    if p.agitation > 0.0 {
        let amp = (7.0 * p.agitation) as f32;
        for k in 0..n {
            x[k] += amp * s_clip[k] * (y[k] * 0.22f32 - aux32).sin();
        }
    }
    // Stress: the filaments agitate, the tips most.
    if p.stress > 0.0 {
        let amp = (4.0 * p.stress) as f32;
        let aux17 = (aux * 1.7) as f32;
        for k in 0..n {
            x[k] += amp * lat[k] * (y[k] * 0.5f32 + aux17).sin();
        }
    }
    // Thermal: the plume opens out about its own frame centre.
    if p.pulse > 0.0 {
        let g = (1.0 + 0.09 * p.pulse * (t * 0.52).sin()) as f32;
        for k in 0..n {
            x[k] = x[k] * g;
            y[k] = y[k] * g;
        }
    }
    swell(n, p, &s_clip, x, y, 0.0, 0.0, None);
    // I/O: a narrow band of excitation travels head to tail.
    if p.surge > 0.01 {
        let centre = (t * 0.19).rem_euclid(1.2);
        let gain = (1.0 + 3.4 * p.surge) as f32;
        for k in 0..n {
            let band = (-(s_clip[k] - centre as f32 - 0.1f32).powi(2) / 0.09f32).exp();
            w[k] *= gain * band;
        }
    }
}

// =========================================================================
// 02 - the coupled bodies
// =========================================================================
fn perturb_s02(
    n: usize,
    p: &Physiology,
    t: f64,
    x: &mut [f32],
    y: &mut [f32],
    w: &mut [f32],
    mod2: &[f32],
) {
    let t32 = t as f32;
    let (cx, cy) = (0.0f32, 0.0f32);
    // m = i%2*9 assigns every sample to one body or the other.
    let side: Vec<f32> = (0..n)
        .map(|k| if mod2[k] < 0.5f32 { -1.0f32 } else { 1.0f32 })
        .collect();
    let ang_rot = (0.22 * p.agitation) * (t * 0.7).sin();
    let (ca, sa) = (ang_rot.cos() as f32, ang_rot.sin() as f32);
    let dx: Vec<f32> = (0..n).map(|k| x[k] - cx).collect();
    let dy: Vec<f32> = (0..n).map(|k| y[k] - cy).collect();
    for k in 0..n {
        x[k] = cx + dx[k] * ca - dy[k] * sa;
        y[k] = cy + dx[k] * sa + dy[k] * ca;
    }
    // Thermal: the pair breathes apart and back together.
    let breathe = (6.0 * p.pulse * (t * 0.41).sin()) as f32;
    for k in 0..n {
        x[k] += side[k] * breathe;
    }
    swell(n, p, &[], x, y, cx, cy, None);
    // I/O: a packet crosses from one body to the other.
    if p.surge > 0.01 {
        let phase = (t * 0.33).rem_euclid(2.0);
        let want = if phase < 1.0 { -1.0f32 } else { 1.0f32 };
        let half = 181.72f32.max(1.0); // max(self.src.frame_half, 1.0)
        let gain = (1.0 + 2.6 * p.surge) as f32;
        for k in 0..n {
            let r = dx[k].hypot(dy[k]) / half;
            let band = (-(r - (phase % 1.0) as f32).powi(2) / 0.16f32).exp();
            let sel = if side[k] == want { 1.0f32 } else { 0.0f32 };
            w[k] *= gain * band * sel;
        }
    }
}

// =========================================================================
// 03 - the mirrored alien
// =========================================================================
fn perturb_s03(n: usize, p: &Physiology, t: f64, x: &mut [f32], y: &mut [f32], w: &mut [f32]) {
    let t32 = t as f32;
    let (cx, cy) = (0.0f32, 0.0f32);
    let mut dx: Vec<f32> = (0..n).map(|k| x[k] - cx).collect();
    let mut dy: Vec<f32> = (0..n).map(|k| y[k] - cy).collect();
    // Metabolic pulse: a slow breath whose depth rises with temperature.
    let g = (1.0 + 0.065 * p.pulse * (t * 0.62).sin()) as f32;
    for k in 0..n {
        dx[k] *= g;
        dy[k] *= g;
    }
    // CPU: the trailing limbs agitate; the body does not.
    let half11 = (164.02f64 * 1.1) as f32; // src.frame_half * 1.1
    let s_clip: Vec<f32> = (0..n).map(|k| (dy[k] / half11).clamp(0.0, 1.0)).collect();
    if p.agitation > 0.0 {
        let amp = (7.0 * p.agitation) as f32;
        let t23 = (t * 2.3) as f32;
        for k in 0..n {
            dx[k] += amp * s_clip[k] * (dy[k] * 0.16f32 - t23).sin();
        }
    }
    // THERMAL EXTREME: the creature loses its own mirror plane.
    let skew = smoothstep(0.88, 1.0, p.pulse);
    if skew > 0.0 {
        let scale = (1.0 + 0.26 * skew) as f32;
        let shear = (14.0 * skew) as f32;
        for k in 0..n {
            if dx[k] < 0.0 {
                dx[k] *= scale;
                dy[k] += shear * (dx[k] * 0.05f32 + t32).sin();
            }
        }
    }
    for k in 0..n {
        x[k] = cx + dx[k];
        y[k] = cy + dy[k];
    }
    swell(n, p, &s_clip, x, y, cx, cy, None);
    if p.surge > 0.01 {
        let centre = (t * 0.22).rem_euclid(1.1);
        let gain = (1.0 + 1.5 * p.surge) as f32;
        for k in 0..n {
            let band = (-(s_clip[k] - centre as f32).powi(2) / 0.12f32).exp();
            w[k] *= gain * band;
        }
    }
}

// =========================================================================
// 04 - the four-part plume
// =========================================================================
fn perturb_s04(
    n: usize,
    p: &Physiology,
    t: f64,
    x: &mut [f32],
    y: &mut [f32],
    w: &mut [f32],
    mod4: &[f32],
    t1: &[f32],
) {
    let (cx, cy) = (0.0f32, 0.0f32);
    let dx: Vec<f32> = (0..n).map(|k| x[k] - cx).collect();
    let dy: Vec<f32> = (0..n).map(|k| y[k] - cy).collect();
    // i%4 is what separates the four plumes in the source; give each its own
    // small angular lead so load reads as them falling out of step.
    let ang_rot = (0.11 * p.agitation) as f32;
    let (ca, sa) = {
        let mut ca = [0.0f32; 4];
        let mut sa = [0.0f32; 4];
        // per-lobe angle: (0.11*agitation) * sin(t*0.9 + lobe*1.9)
        for (l, (cv, sv)) in ca.iter_mut().zip(sa.iter_mut()).enumerate() {
            let a = ang_rot * ((t * 0.9) as f32 + (l as f32) * 1.9f32).sin();
            *cv = a.cos();
            *sv = a.sin();
        }
        (ca, sa)
    };
    for k in 0..n {
        let lobe = mod4[k] as usize;
        x[k] = cx + dx[k] * ca[lobe] - dy[k] * sa[lobe];
        y[k] = cy + dx[k] * sa[lobe] + dy[k] * ca[lobe];
    }
    // Thermal: all four breathe radially, together.
    if p.pulse > 0.0 {
        let g = (1.0 + 0.058 * p.pulse * (t * 0.55).sin()) as f32;
        for k in 0..n {
            x[k] = cx + (x[k] - cx) * g;
            y[k] = cy + (y[k] - cy) * g;
        }
    }
    let t1b = &t1[..n];
    swell(n, p, &[], x, y, cx, cy, Some(t1b));
    // I/O: the flare walks round the four.
    if p.surge > 0.01 {
        let hot = (t * 0.6) as i64 % 4;
        let gain = (1.0 + 2.8 * p.surge) as f32;
        let hot32 = hot as f32;
        for k in 0..n {
            let sel = if mod4[k] == hot32 { 1.0f32 } else { 0.0f32 };
            w[k] *= gain * sel;
        }
    }
}

// =========================================================================
// 05 - the single feather
// =========================================================================
fn perturb_s05(
    n: usize,
    p: &Physiology,
    t: f64,
    aux: f64,
    x: &mut [f32],
    y: &mut [f32],
    w: &mut [f32],
    u: &[f32],
) {
    let (cx, cy) = (0.0f32, 0.0f32);
    let dx: Vec<f32> = (0..n).map(|k| x[k] - cx).collect();
    let dy: Vec<f32> = (0..n).map(|k| y[k] - cy).collect();
    let half = 124.43f32.max(1.0); // max(self.src.frame_half, 1.0)
    let r: Vec<f32> = (0..n).map(|k| dx[k].hypot(dy[k]) / half).collect();
    // The whip: displacement perpendicular to the radius, growing with it.
    if p.agitation > 0.0 {
        let amp_c = (9.0 * p.agitation) as f32;
        let aux32 = aux as f32;
        for k in 0..n {
            let amp = amp_c * r[k].clamp(0.0, 1.2).powf(1.6);
            let ph = (r[k] * 5.0f32 - aux32).sin();
            let inv = 1.0 / dx[k].hypot(dy[k]).max(1e-6f32);
            x[k] = cx + dx[k] + amp * ph * (-dy[k] * inv);
            y[k] = cy + dy[k] + amp * ph * (dx[k] * inv);
        }
    }
    // Thermal: the arc opens.
    if p.pulse > 0.0 {
        let g = (1.0 + 0.065 * p.pulse * (t * 0.48).sin()) as f32;
        for k in 0..n {
            x[k] = cx + (x[k] - cx) * g;
            y[k] = cy + (y[k] - cy) * g;
        }
    }
    // Stress: the feather curls, most at its tip - tension, bounded.
    if p.stress > 0.01 {
        let ang_c = (0.14 * p.stress) as f32;
        let mut ddx = [0.0f32; 0];
        let _ = &mut ddx;
        for k in 0..n {
            let ang = ang_c * u[k] * u[k];
            let (ca, sa) = (ang.cos(), ang.sin());
            let ddx = x[k] - cx;
            let ddy = y[k] - cy;
            x[k] = cx + ddx * ca - ddy * sa;
            y[k] = cy + ddx * sa + ddy * ca;
        }
    }
    swell(n, p, &[], x, y, cx, cy, None);
    // I/O: the base of the feather flares as a burst enters it.
    if p.surge > 0.01 {
        let gain = (1.0 + 3.0 * p.surge) as f32;
        for k in 0..n {
            let band = (-(r[k] / 0.30f32).powi(2)).exp();
            w[k] *= gain * band;
        }
    }
}
