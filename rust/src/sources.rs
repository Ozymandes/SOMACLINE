//! The five source equations, ported verbatim from `organism/sources.py`.
//!
//! Each specimen is a published generative sketch — a "one tweet" p5.js
//! program that draws a creature by evaluating one closed-form expression
//! tens of thousands of times per frame. The ports keep the original's
//! variable names (k, e, d, o, q, c), its operator order, and its loop
//! bounds; the only changes are the ones the vectorised form forces:
//!
//! ```text
//!     mag(a, b)        -> hypot(a, b)
//!     the for(i=N;i--;) -> one index array
//!     ternaries        -> select
//!     point(x, y)      -> one sample in the returned arrays
//!     circle(x,y,d)    -> one sample carrying a weight (c01 only)
//! ```
//!
//! NUMERICS: the Python ports run the point math in float32 (f64 scalars are
//! cast to f32 per operation, numpy weak-scalar semantics). The Rust kernels
//! do exactly the same: f32 buffers, scalars pre-computed in f64 and cast at
//! the point of use, left-associative operator order preserved.

/// Every source authors into this square.
pub const SRC: f64 = 400.0;
pub const SRC_MID: f64 = SRC / 2.0;

/// Half-extent, in world units, that a creature's own bounding box is fitted
/// to. Deliberately inside the 460 design radius: a specimen that reaches the
/// scope's outermost ring touches the glass and reads as CLIPPED rather than
/// as contained.
pub const WORLD_FIT: f64 = 372.0;

/// A source sketch: its equation, its clock, and how it was composited.
#[derive(Clone, Copy, Debug)]
pub struct SourceDef {
    pub key: &'static str,
    pub title: &'static str,
    /// samples per frame, from the original loop
    pub n: usize,
    /// t increment per frame, from the original
    pub dt: f64,
    /// stroke()/fill() alpha, 0..1
    pub ink: f64,
    /// 0.0 clears each frame; else frame retention
    pub persist: f64,
    /// Where this creature actually sits in its own canvas: centre and
    /// half-extent of its robust bounding box. Translation and one uniform
    /// scale — nothing else.
    pub frame_cx: f64,
    pub frame_cy: f64,
    pub frame_half: f64,
    /// the source, verbatim
    pub original: &'static str,
}

impl SourceDef {
    /// Source canvas -> Abyssal world units. A similarity transform only.
    ///
    /// p5's y axis points down and so does Cairo's, so the sign is carried
    /// through unchanged; flipping it here would mirror every creature.
    pub fn to_world_k(&self) -> f64 {
        WORLD_FIT / self.frame_half
    }
}

pub const PI: f64 = std::f64::consts::PI;

// =========================================================================
// 01 - the sigmoid plume
// =========================================================================
pub const SRC_01: &str = "\
a=(x,y,d=mag(k=4*cos(x/21),e=y/8-20))=>
circle(
  (q=3*sin(k*2)+.3/k+sin(y/19)*k*(9+2*sin(e*14-d*3+t*2)))+50*cos(c=d-t)+200,
  q*sin(c)+d*39-475,
  k*k>15?2:1
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).noStroke().fill(w,116);
for(t+=PI/240,i=1e4;i--;)a(i,i/235)}";

/// 10,000 samples, dt pi/240, ink 116/255. The heavy endpoints carry 4x the
/// area of the light ones (circle diameter 2 vs 1).
pub fn c01(t: f64, i: &[f32], x: &mut [f32], y: &mut [f32], w: &mut [f32]) {
    let t2 = (t * 2.0) as f32;
    for j in 0..i.len() {
        let ix = i[j];
        let iy = ix / 235.0f32;
        let k = 4.0f32 * (ix / 21.0f32).cos();
        let e = iy / 8.0f32 - 20.0f32;
        let d = k.hypot(e);
        // 0.3/k is a genuine feature: as cos(x/21) approaches zero the term
        // spikes, throwing the bright filament tips. x is integral so k is
        // never exactly zero; a non-finite sample is zeroed by the caller.
        let q = 3.0f32 * (k * 2.0f32).sin() + 0.3f32 / k
            + (iy / 19.0f32).sin() * k * (9.0f32 + 2.0f32 * (e * 14.0f32 - d * 3.0f32 + t2).sin());
        let c = d - t as f32;
        x[j] = q + 50.0f32 * c.cos() + 200.0f32;
        y[j] = q * c.sin() + d * 39.0f32 - 475.0f32;
        // circle(..., k*k>15 ? 2 : 1) - diameter, so the heavy points carry
        // 4x the area of the light ones.
        w[j] = if k * k > 15.0f32 { 4.0f32 } else { 1.0f32 };
    }
}

// =========================================================================
// 02 - the coupled bodies
// =========================================================================
pub const SRC_02: &str = "\
a=(m,d=mag(k=9*cos(i/81),e=i/765-13)/4)=>
point(
  (q=79-2*sin(k*3)+sin(k*k<19?t*3+d*4:d/2+4)/2*k*(9+5*sin(d*d-e/6-t+m)))
    *sin(c=d*d/9-t/16+m)+200,
  (q+50)*cos(c)+200
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).stroke(w,96);
for(t+=PI/45,i=2e4;i--;)a(i%2*9)}";

/// 20,000 samples, dt pi/45, ink 96/255. `m = i%2*9`: two bodies 9 radians
/// apart. The global loop counter `i` inside `d`'s default is load-bearing.
pub fn c02(t: f64, i: &[f32], x: &mut [f32], y: &mut [f32]) {
    let t3 = (t * 3.0) as f32;
    let t_ = t as f32;
    let t16 = (t / 16.0) as f32;
    for j in 0..i.len() {
        let iv = i[j];
        let m = (iv % 2.0f32) * 9.0f32;
        let k = 9.0f32 * (iv / 81.0f32).cos();
        let e = iv / 765.0f32 - 13.0f32;
        let d = k.hypot(e) / 4.0f32;
        // sin(k*k<19 ? t*3+d*4 : d/2+4) - the branch is on k, so the inner
        // and outer halves of each body are driven by different clocks.
        let inner = if k * k < 19.0f32 { t3 + d * 4.0f32 } else { d / 2.0f32 + 4.0f32 };
        let q = 79.0f32 - 2.0f32 * (k * 3.0f32).sin()
            + inner.sin() / 2.0f32 * k
                * (9.0f32 + 5.0f32 * (d * d - e / 6.0f32 - t_ + m).sin());
        let c = d * d / 9.0f32 - t16 + m;
        x[j] = q * c.sin() + 200.0f32;
        y[j] = (q + 50.0f32) * c.cos() + 200.0f32;
    }
}

// =========================================================================
// 03 - the mirrored alien
// =========================================================================
pub const SRC_03: &str = "\
a=(x,y,d=5*cos(o=mag(k=x/8-12.5,e=y/8-12.5)/12*cos(sin(k/2)*cos(e/2))))=>
point(
  (x+d*k*(sin(d*2+t)+sin(y*o*o))/9)/1.5+133,
  (y/3-d*40+19*cos(d+t))*1.5+300
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(6,96).stroke(w,46);
for(t+=PI/90,i=4e4;i--;)a(i%200,i/200)}";

/// 40,000 samples, dt pi/90, ink 46/255, retention background(6,96).
pub fn c03(t: f64, i: &[f32], x: &mut [f32], y: &mut [f32]) {
    let t_ = t as f32;
    for j in 0..i.len() {
        let iv = i[j];
        let ix = iv % 200.0f32;
        let iy = iv / 200.0f32;
        let k = ix / 8.0f32 - 12.5f32;
        let e = iy / 8.0f32 - 12.5f32;
        let o = k.hypot(e) / 12.0f32 * ((k / 2.0f32).sin() * (e / 2.0f32).cos()).cos();
        let d = 5.0f32 * o.cos();
        let a = d * 2.0f32 + t_;
        x[j] = (ix + d * k * (a.sin() + (iy * o * o).sin()) / 9.0f32) / 1.5f32 + 133.0f32;
        let b = d + t_;
        y[j] = (iy / 3.0f32 - d * 40.0f32 + 19.0f32 * b.cos()) * 1.5f32 + 300.0f32;
    }
}

// =========================================================================
// 04 - the four-part plume
// =========================================================================
pub const SRC_04: &str = "\
a=(y,o=mag(k=cos(y*9)*(y<5?sin(t/8+y)*35:11),e=y/8-13)/6)=>
point(
  (q=k*y/19+49+k*sin(y)*sin(o*2-e/5-t))*sin(c=o/3-e/5-t/8+i%4*8)
    -79*cos(c/3)+200,
  200+(q+70)*cos(c)
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(6).stroke(w,96);
for(t+=PI/30,i=2e4;i--;)a(i/500)}";

/// 20,000 samples, dt pi/30, ink 96/255. `i%4*8` - FOUR separate plumes, not
/// a fourfold radial symmetry.
pub fn c04(t: f64, i: &[f32], x: &mut [f32], y: &mut [f32]) {
    let t8 = (t / 8.0) as f32;
    let t_ = t as f32;
    for j in 0..i.len() {
        let iv = i[j];
        let iy = iv / 500.0f32;
        // y<5 ? sin(t/8+y)*35 : 11 - the inner fifth of the body pulses on
        // its own slow clock while the rest holds a fixed amplitude.
        let inner = if iy < 5.0f32 { (t8 + iy).sin() * 35.0f32 } else { 11.0f32 };
        let k = (iy * 9.0f32).cos() * inner;
        let e = iy / 8.0f32 - 13.0f32;
        let o = k.hypot(e) / 6.0f32;
        let q = k * iy / 19.0f32 + 49.0f32
            + k * iy.sin() * (o * 2.0f32 - e / 5.0f32 - t_).sin();
        // i%4*8 - each index class gets its own angular offset.
        let c = o / 3.0f32 - e / 5.0f32 - t8 + (iv % 4.0f32) * 8.0f32;
        x[j] = q * c.sin() - 79.0f32 * (c / 3.0f32).cos() + 200.0f32;
        y[j] = 200.0f32 + (q + 70.0f32) * c.cos();
    }
}

// =========================================================================
// 05 - the single feather
// =========================================================================
pub const SRC_05: &str = "\
a=(x,y,d=mag(k=5*cos(x/14)*cos(y/30),e=y/8-13)**2/59+4)=>
point(
  (q=60-3*sin(atan2(k,e)*e)+k*(3+4/d*sin(d*d-t*2)))*sin(c=d/2+e/99-t/18)+200,
  (q+d*9)*cos(c)+200
)
t=0,draw=$=>{t||createCanvas(w=400,w);background(9).stroke(w,66);
for(t+=PI/20,i=1e4;i--;)a(i%200,i/43)}";

/// 10,000 samples, dt pi/20, ink 66/255. `mag(k,e)**2/59+4` - `**` binds
/// tighter than `/`, so this is (k*k+e*e)/59+4.
pub fn c05(t: f64, i: &[f32], x: &mut [f32], y: &mut [f32]) {
    let t2 = (t * 2.0) as f32;
    let t18 = (t / 18.0) as f32;
    for j in 0..i.len() {
        let iv = i[j];
        let ix = iv % 200.0f32;
        let iy = iv / 43.0f32;
        let k = 5.0f32 * (ix / 14.0f32).cos() * (iy / 30.0f32).cos();
        let e = iy / 8.0f32 - 13.0f32;
        let d = k.hypot(e) * k.hypot(e) / 59.0f32 + 4.0f32;
        let q = 60.0f32 - 3.0f32 * (k.atan2(e) * e).sin()
            + k * (3.0f32 + 4.0f32 / d * (d * d - t2).sin());
        let c = d / 2.0f32 + e / 99.0f32 - t18;
        x[j] = q * c.sin() + 200.0f32;
        y[j] = (q + d * 9.0f32) * c.cos() + 200.0f32;
    }
}

/// The five sources, in catalogue order.
pub static SOURCES: [SourceDef; 5] = [
    SourceDef {
        key: "s01",
        title: "sigmoid plume",
        n: 10000,
        dt: PI / 240.0,
        ink: 116.0 / 255.0,
        persist: 0.0,
        frame_cx: 196.38,
        frame_cy: 203.49,
        frame_half: 116.95,
        original: SRC_01,
    },
    SourceDef {
        key: "s02",
        title: "coupled bodies",
        n: 20000,
        dt: PI / 45.0,
        ink: 96.0 / 255.0,
        persist: 0.0,
        frame_cx: 213.12,
        frame_cy: 200.94,
        frame_half: 181.72,
        original: SRC_02,
    },
    SourceDef {
        key: "s03",
        title: "mirrored alien",
        n: 40000,
        dt: PI / 90.0,
        ink: 46.0 / 255.0,
        persist: 0.6235,
        frame_cx: 199.38,
        frame_cy: 182.16,
        frame_half: 164.02,
        original: SRC_03,
    },
    SourceDef {
        key: "s04",
        title: "four-part plume",
        n: 20000,
        dt: PI / 30.0,
        ink: 96.0 / 255.0,
        persist: 0.0,
        frame_cx: 199.68,
        frame_cy: 199.70,
        frame_half: 140.80,
        original: SRC_04,
    },
    SourceDef {
        key: "s05",
        title: "single feather",
        n: 10000,
        dt: PI / 20.0,
        ink: 66.0 / 255.0,
        persist: 0.0,
        frame_cx: 214.04,
        frame_cy: 187.18,
        frame_half: 124.43,
        original: SRC_05,
    },
];

pub fn by_key(key: &str) -> Option<&'static SourceDef> {
    SOURCES.iter().find(|s| s.key == key)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sources_table_matches_python() {
        assert_eq!(SOURCES.len(), 5);
        assert_eq!(by_key("s03").unwrap().n, 40000);
        assert_eq!(by_key("s03").unwrap().persist, 0.6235);
        assert_eq!(by_key("s01").unwrap().ink * 255.0, 116.0);
        assert_eq!(by_key("s05").unwrap().dt, PI / 20.0);
        assert!(by_key("nope").is_none());
    }
}
