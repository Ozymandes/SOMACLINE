//! Restrained dynamic lighting over the hardware skin. Port of `core/lighting.py`.
//!
//! DISCIPLINE: every emitter's strength is clamped to MAX_STRENGTH; the whole
//! pass is clamped again via per-emitter alpha; radii are capped at MAX_RADIUS.
//! The falloff is a cached A8 mask, not a per-frame gradient.

use std::collections::HashMap;
use std::collections::VecDeque;

use cairo::{Context, ImageSurface, Operator};

pub const MAX_STRENGTH: f64 = 0.30;
pub const MAX_TOTAL: f64 = 0.42;
/// Hard ceiling on one emitter's radius, in pixels.
pub const MAX_RADIUS: f64 = 240.0;

#[derive(Clone, Copy, Debug)]
pub struct Emitter {
    pub x: f64,
    pub y: f64,
    pub radius: f64,
    pub rgb: crate::theme::RGB,
    pub strength: f64,
}

/// Optional clip rectangle for the paint pass.
#[derive(Clone, Copy, Debug)]
pub struct Clip {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

/// Collects emitters during a frame, then paints them once.
#[derive(Default)]
pub struct LightField {
    emitters: Vec<Emitter>,
}

impl LightField {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn clear(&mut self) {
        self.emitters.clear();
    }

    pub fn add(&mut self, x: f64, y: f64, radius: f64, rgb: crate::theme::RGB, strength: f64) {
        if radius <= 1.0 || strength <= 0.002 {
            return;
        }
        self.emitters.push(Emitter {
            x,
            y,
            radius: radius.min(MAX_RADIUS),
            rgb,
            strength: strength.clamp(0.0, MAX_STRENGTH),
        });
    }

    /// A screen spilling onto the metal around it.
    pub fn glow(&mut self, r: &crate::layout::Rect, rgb: crate::theme::RGB, strength: f64, spread: f64) {
        let radius = r.w.max(r.h) * spread;
        self.add(r.cx(), r.cy(), radius, rgb, strength);
    }

    pub fn count(&self) -> usize {
        self.emitters.len()
    }

    /// Composite every emitter additively, each drawn straight onto the target
    /// with OPERATOR_ADD, alpha pre-scaled by MAX_TOTAL.
    pub fn paint(&self, cr: &Context, clip: Option<Clip>) {
        if self.emitters.is_empty() {
            return;
        }
        cr.save().unwrap();
        if let Some(c) = clip {
            cr.rectangle(c.x, c.y, c.w, c.h);
            cr.clip();
        }
        cr.set_operator(Operator::Add);
        for e in &self.emitters {
            let a = e.strength * MAX_TOTAL;
            let (r, g, b) = e.rgb;
            let (mask, side) = falloff(e.radius);
            cr.set_source_rgba(r, g, b, a);
            cr.mask_surface(&mask, (e.x - side as f64 * 0.5).round(), (e.y - side as f64 * 0.5).round()).unwrap();
        }
        cr.set_operator(Operator::Over);
        cr.restore().unwrap();
    }
}

/// Quantisation of the falloff radius, in pixels.
const RADIUS_STEP: f64 = 8.0;
const FALLOFF_LIMIT: usize = 24;

struct FalloffCache {
    map: HashMap<i32, (ImageSurface, i32)>,
    order: VecDeque<i32>,
}

impl FalloffCache {
    fn get(&mut self, q: i32) -> (ImageSurface, i32) {
        if let Some(hit) = self.map.get(&q) {
            let hit = hit.clone();
            self.touch(q);
            return hit;
        }
        let ent = build_falloff(q);
        self.map.insert(q, ent.clone());
        self.order.push_back(q);
        while self.order.len() > FALLOFF_LIMIT {
            if let Some(old) = self.order.pop_front() {
                self.map.remove(&old);
            }
        }
        ent
    }

    fn touch(&mut self, q: i32) {
        if let Some(pos) = self.order.iter().position(|&k| k == q) {
            self.order.remove(pos);
            self.order.push_back(q);
        }
    }
}

impl Default for FalloffCache {
    fn default() -> Self {
        FalloffCache { map: HashMap::new(), order: VecDeque::new() }
    }
}

thread_local! {
    static FALLOFF: std::cell::RefCell<FalloffCache> = std::cell::RefCell::new(FalloffCache::default());
}

fn build_falloff(q: i32) -> (ImageSurface, i32) {
    let rad = q as f64 * RADIUS_STEP;
    let side = (rad * 2.0) as i32 + 2;
    let surf = ImageSurface::create(cairo::Format::A8, side, side).unwrap();
    let c = Context::new(&surf).unwrap();
    let mid = side as f64 * 0.5;
    let g = cairo::RadialGradient::new(mid, mid, 0.0, mid, mid, rad.max(1.0));
    g.add_color_stop_rgba(0.0, 0.0, 0.0, 0.0, 1.0);
    g.add_color_stop_rgba(0.45, 0.0, 0.0, 0.0, 0.38);
    g.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0);
    c.set_source(&g).unwrap();
    c.arc(mid, mid, rad, 0.0, 6.283185307179586);
    c.fill().unwrap();
    surf.flush();
    (surf, side)
}

pub fn falloff(radius: f64) -> (ImageSurface, i32) {
    let q = ((radius / RADIUS_STEP).round() as i32).max(1);
    FALLOFF.with(|f| f.borrow_mut().get(q))
}

// Emitter colours, matched to the instrument palette.
pub const CYAN: crate::theme::RGB = (0.42, 0.78, 0.92);
pub const CHARTREUSE: crate::theme::RGB = (0.72, 0.88, 0.28);
pub const AMBER: crate::theme::RGB = (0.96, 0.62, 0.18);
pub const RED: crate::theme::RGB = (0.90, 0.26, 0.20);

pub fn state_rgb(state: &str) -> crate::theme::RGB {
    match state {
        "nominal" => CHARTREUSE,
        "active" | "standby" => CYAN,
        "warning" => AMBER,
        "critical" => RED,
        _ => CYAN,
    }
}
