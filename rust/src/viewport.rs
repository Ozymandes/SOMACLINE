//! The ONE place where logical world units become device pixels.
//! Port of `core/viewport.py`.
//!
//! RESIZE CONTRACT: a resize produces a new Viewport value and nothing else.
//! Scale is UNIFORM on both axes by construction (a single float), so a circle
//! in world space is a circle on screen at every window size.

use crate::world::{WORLD_RADIUS, WORLD_SIZE};

/// Maps world units -> widget pixels for one rectangular stage.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Viewport {
    /// The stage this viewport fills, in widget pixels.
    pub stage_x: f64,
    pub stage_y: f64,
    pub stage_w: f64,
    pub stage_h: f64,

    /// Uniform world-units -> pixels factor, and the pixel centre of the stage.
    pub scale: f64,
    pub cx: f64,
    pub cy: f64,
}

impl Viewport {
    /// Fit the WORLD_SIZE square into the stage rect, letterboxed, centred.
    pub fn for_stage(x: f64, y: f64, w: f64, h: f64) -> Viewport {
        let w = w.max(1.0);
        let h = h.max(1.0);
        let scale = w.min(h) / WORLD_SIZE;
        Viewport {
            stage_x: x,
            stage_y: y,
            stage_w: w,
            stage_h: h,
            scale,
            cx: x + w / 2.0,
            cy: y + h / 2.0,
        }
    }

    /// World point -> widget pixel point.
    #[inline]
    pub fn px(&self, wx: f64, wy: f64) -> (f64, f64) {
        (self.cx + wx * self.scale, self.cy + wy * self.scale)
    }

    /// World length -> pixel length (same on both axes, by contract).
    #[inline]
    pub fn length(&self, world_len: f64) -> f64 {
        world_len * self.scale
    }

    pub fn organism_px_radius(&self) -> f64 {
        WORLD_RADIUS * self.scale
    }

    /// True if the organism's design radius cannot fit in the stage.
    pub fn clips(&self) -> bool {
        let r = self.organism_px_radius();
        r > self.stage_w / 2.0 + 0.5 || r > self.stage_h / 2.0 + 0.5
    }
}

/// GATE 1 helper: max deviation from circularity of a unit world circle.
/// Returns 0.0 for a correct viewport. Any non-zero value is a bug.
pub fn isotropy_error(vp: &Viewport) -> f64 {
    let mut radii = [0.0f64; 64];
    for (i, r) in radii.iter_mut().enumerate() {
        let t = i as f64 * std::f64::consts::TAU / 64.0;
        let (px, py) = vp.px(t.cos() * 100.0, t.sin() * 100.0);
        *r = (px - vp.cx).hypot(py - vp.cy);
    }
    let mn = radii.iter().cloned().fold(f64::INFINITY, f64::min);
    let mx = radii.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    mx - mn
}
