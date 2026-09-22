//! Cairo rendering of the specimen. The only module that turns world ->
//! pixels. Port of `organism/render.py`.
//!
//! NO cairo transform is ever set. Every coordinate is pushed through
//! Viewport::px / Viewport::length, which are uniform by construction, so a
//! world circle is a screen circle at any window size or aspect ratio.
//! Nothing here mutates the organism.
//!
//! COLOUR: two ramps. The density ramp runs deep-field blue where the cloud
//! is thin to core cyan where the equation crowds. The pulse palette - the
//! propagating body pulse - carries its own colour per point: abyssal blue at
//! rest, bio-green in healthy operation, amber -> orange under stress. The
//! field counts excitation and hue alongside density, so each PIXEL takes the
//! colour of the pulse passing through it and the rest of the body stays cyan.

use crate::mathforms::SourceBody;
use crate::pointfield::{self, FieldCache};
use crate::theme::{AMBER, CYAN, CYAN_DEEP, LIME};
use crate::viewport::Viewport;
use crate::world::WORLD_RADIUS;
use cairo::{Context, ImageSurface};

/// The deep-field wash behind the specimen, in world units.
const WASH_R: f64 = 430.0;

/// Radius of the field buffer, in world units: the design radius plus a
/// margin for the few soft pixels a point spreads into.
pub const R_FIELD: f64 = WORLD_RADIUS + 4.0;

/// Largest side the point field is counted at, in pixels. Above it the field
/// is scaled up on the blit - soft one- and two-pixel points, so a modest
/// upscale is invisible, and it bounds the cost at any window size.
pub const MAX_FIELD_PX: f64 = 760.0;

const WASH_STEP: f64 = 6.0;
const WASH_LIMIT: usize = 6;

pub fn hex(h: u32) -> (f64, f64, f64) {
    (
        ((h >> 16) & 0xff) as f64 / 255.0,
        ((h >> 8) & 0xff) as f64 / 255.0,
        (h & 0xff) as f64 / 255.0,
    )
}

/// The pulse palette, as (hue position, colour) stops. Blue -> green ->
/// amber -> orange, deliberately without purple or red: a bioluminescent
/// specimen, not a spectrum.
pub const PULSE_PALETTE: [(f64, u32); 7] = [
    (0.00, 0x4d9eff), // quiescent: electric abyssal blue
    (0.16, 0x8ce0f8), // base: cold pale cyan
    (0.34, 0x4cf0c8), // rousing: aqua
    (0.50, 0x5cff8a), // healthy excitation: bioluminescent green
    (0.64, 0xb4f25a), // high load: lime -> warm green
    (0.80, 0xffc445), // thermal stress: amber
    (1.00, 0xff7a1e), // high stress: orange
];

/// Build the 256-entry pulse LUT: np.interp per channel over linspace(0,1,256),
/// f64 interpolation, one f32 rounding at the end - exactly like Python.
pub fn build_lut() -> [[f32; 3]; 256] {
    let mut lut = [[0.0f32; 3]; 256];
    for (i, slot) in lut.iter_mut().enumerate() {
        let q = if i == 255 { 1.0 } else { i as f64 * (1.0 / 255.0) };
        // find the segment: last j with pos[j] <= q
        let mut j = 0;
        while j + 1 < PULSE_PALETTE.len() && q > PULSE_PALETTE[j + 1].0 {
            j += 1;
        }
        for ch in 0..3 {
            let (p0, c0) = PULSE_PALETTE[j];
            let (p1, c1) = PULSE_PALETTE[j + 1];
            let (lo, hi) = match ch {
                0 => (hex(c0).0, hex(c1).0),
                1 => (hex(c0).1, hex(c1).1),
                _ => (hex(c0).2, hex(c1).2),
            };
            // numpy compiled_interp: slope*(x - x0) + y0
            let slope = (hi - lo) / (p1 - p0);
            slot[ch] = (slope * (q - p0) + lo) as f32;
        }
    }
    lut
}

pub struct RenderCaches {
    pub fields: FieldCache,
    /// The 256-entry pulse palette, built once.
    pub lut: [[f32; 3]; 256],
    wash: Vec<(i32, ImageSurface, i32)>, // (q, surf, side), FIFO order
}

impl Default for RenderCaches {
    fn default() -> Self {
        RenderCaches::new()
    }
}

impl RenderCaches {
    pub fn new() -> Self {
        RenderCaches { fields: FieldCache::new(3), lut: build_lut(), wash: Vec::new() }
    }

    pub fn clear(&mut self) {
        self.fields.clear();
        self.wash.clear();
    }
}

/// The deep-field wash, rendered once per quantised radius. Filling a 900px
/// radial gradient was the most expensive single operation in the frame, and
/// its radius changes only on resize.
fn wash_disc(caches: &mut RenderCaches, radius: f64) -> Option<(ImageSurface, i32)> {
    let q = (radius / WASH_STEP).round() as i32;
    let q = q.max(1);
    // FIFO cache: Python's _wash does not reorder on hit.
    if let Some((_, surf, side)) = caches.wash.iter().find(|(k, _, _)| *k == q) {
        return Some((surf.clone(), *side));
    }
    let r = q as f64 * WASH_STEP;
    let side = (r * 2.0) as i32 + 2;
    if side < 2 || side > 8192 {
        return None;
    }
    let surf = ImageSurface::create(cairo::Format::ARgb32, side, side).ok()?;
    let c = Context::new(&surf).ok()?;
    let mid = side as f64 * 0.5;
    let g = cairo::RadialGradient::new(mid, mid, 0.0, mid, mid, r.max(1.0));
    for (off, a) in [
        (0.00, 0.070),
        (0.18, 0.048),
        (0.45, 0.024),
        (0.70, 0.010),
        (0.87, 0.003),
        (1.00, 0.000),
    ] {
        g.add_color_stop_rgba(off, CYAN_DEEP.0, CYAN_DEEP.1, CYAN_DEEP.2, a);
    }
    c.set_source(&g).ok()?;
    c.arc(mid, mid, r, 0.0, 6.283185307179586);
    c.fill().ok()?;
    surf.flush();
    caches.wash.push((q, surf.clone(), side));
    while caches.wash.len() > WASH_LIMIT {
        caches.wash.remove(0);
    }
    Some((surf, side))
}

#[inline]
fn mix(a: (f64, f64, f64), b: (f64, f64, f64), t: f64) -> (f64, f64, f64) {
    (
        a.0 + (b.0 - a.0) * t,
        a.1 + (b.1 - a.1) * t,
        a.2 + (b.2 - a.2) * t,
    )
}

/// Draw one frame of the specimen. Caller has already painted the field.
pub fn draw_organism(
    cr: &Context,
    vp: &Viewport,
    org: &SourceBody,
    caches: &mut RenderCaches,
) {
    // ---- deep-field wash ---------------------------------------------------
    if let Some((disc, side)) = wash_disc(caches, vp.length(WASH_R)) {
        cr.save().unwrap();
        cr.set_source_surface(
            &disc,
            (vp.cx - side as f64 * 0.5).round(),
            (vp.cy - side as f64 * 0.5).round(),
        )
        .unwrap();
        cr.paint().unwrap();
        cr.restore().unwrap();
    }

    // ---- the point field ---------------------------------------------------
    // The field buffer covers the creature's own disc - the design radius -
    // and not the whole stage: the colour map is a per-PIXEL pass.
    let side_px = 2.0 * R_FIELD * vp.scale;
    if side_px < 8.0 {
        return;
    }
    let res = (MAX_FIELD_PX / side_px).min(1.0);
    let side = (side_px * res) as usize + 2;
    let (wx, wy, weight) = org.points();
    if wx.is_empty() {
        return;
    }

    let k = (vp.scale * res) as f32;
    let half = (side as f64 * 0.5) as f32;
    let n = wx.len();
    let mut px = vec![0.0f32; n];
    let mut py = vec![0.0f32; n];
    for i in 0..n {
        px[i] = wx[i] * k + half;
        py[i] = wy[i] * k + half;
    }
    let persist = org.persist();
    let ent = caches.fields.entry(org.src().key, side, side);
    let (exc, hue) = org.excitation();
    pointfield::accumulate_physio(ent, &px, &py, weight, exc, hue, persist);

    // Ink is scaled by how many BUFFER pixels one world unit covers: the same
    // cloud spread over four times the area must be counted four times as
    // strongly, or the creature fades out as the window grows.
    let eff = vp.scale * res;
    // Exposure: matched to references/Abbysal_Final.png. A presentation gain
    // on the counted density only - the equations are untouched.
    let mut ink = (org.src().ink * 1.20 / (eff * eff * 3.4).max(0.02)).min(5.0);
    // A trailing species converges to 1/(1-r) times a single frame's count;
    // divide the (soft-knee) equivalent of that gain back out.
    let r = org.persist();
    if r > 0.0 {
        ink *= (1.0 - r).powf(0.5) * 1.55;
    }

    // Only a faint thermal cast on the resting body: the stress colour is
    // carried locally by the pulse, never by tinting the whole creature.
    let warm = org.warmth() * 0.22;
    let lo = mix(CYAN_DEEP, (0.34, 0.20, 0.06), warm);
    let hi = mix(CYAN, AMBER, warm);
    let up = 1.0 / res;
    let ent = caches.fields.entry(org.src().key, side, side);
    pointfield::paint_physio(
        cr,
        ent,
        vp.cx - side as f64 * 0.5 * up,
        vp.cy - side as f64 * 0.5 * up,
        lo,
        hi,
        &caches.lut,
        ink,
        2.2,
        up,
    );
}

/// DEBUG overlay -- GATE 1 circularity check.
///
/// World circles at WORLD_RADIUS and r=250, a crosshair through the world
/// origin, and the specimen's current extent. If the stage is ever stretched
/// these circles show up as ellipses on the very first frame.
pub fn draw_calibration(cr: &Context, vp: &Viewport, org: &SourceBody) {
    let cx = vp.cx;
    let cy = vp.cy;
    let (l_r, l_g, l_b) = LIME;
    let tau = 6.283185307179586f64;

    cr.save().unwrap();
    cr.set_line_cap(cairo::LineCap::Round);
    cr.set_line_width(1.0);

    for (r_world, a) in [(WORLD_RADIUS, 0.26), (250.0, 0.17)] {
        cr.set_source_rgba(l_r, l_g, l_b, a);
        cr.new_path();
        cr.arc(cx, cy, vp.length(r_world), 0.0, tau);
        cr.stroke().unwrap();
    }

    cr.set_source_rgba(l_r, l_g, l_b, 0.13);
    let ext = vp.length(WORLD_RADIUS + 26.0);
    cr.new_path();
    cr.move_to(cx - ext, cy);
    cr.line_to(cx + ext, cy);
    cr.move_to(cx, cy - ext);
    cr.line_to(cx, cy + ext);
    cr.stroke().unwrap();

    cr.set_source_rgba(l_r, l_g, l_b, 0.19);
    cr.new_path();
    for k in 0..4 {
        let ang = std::f64::consts::PI * 0.25 + k as f64 * std::f64::consts::PI * 0.5;
        let (c, s) = (ang.cos(), ang.sin());
        let (x0, y0) = vp.px(c * 40.0, s * 40.0);
        let (x1, y1) = vp.px(c * WORLD_RADIUS, s * WORLD_RADIUS);
        cr.move_to(x0, y0);
        cr.line_to(x1, y1);
    }
    cr.stroke().unwrap();

    let e = org.max_extent();
    if e > 0.0 {
        cr.set_source_rgba(l_r, l_g, l_b, 0.38);
        cr.set_line_width(0.8);
        cr.new_path();
        cr.arc(cx, cy, vp.length(e), 0.0, tau);
        cr.stroke().unwrap();
    }
    cr.restore().unwrap();
}
