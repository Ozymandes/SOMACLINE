//! Accumulating point-field rasteriser. Port of `organism/pointfield.py`.
//!
//! The source sketches are point clouds: ten to forty thousand `point()` calls
//! a frame, composited additively at a low alpha, so that where the equation
//! crowds the plane the image brightens. That accumulation IS the creature.
//! Points are accumulated into buffers and the result is blitted as one
//! surface:
//!
//! ```text
//!     equation  ->  (x, y, weight) in source coordinates
//!     transform ->  world units, then stage pixels
//!     splat     ->  accumulate over a flat pixel index
//!     map       ->  intensity to premultiplied ARGB32
//!     blit      ->  one cairo surface
//! ```
//!
//! PERSISTENCE: one source composites with `background(6, 96)` - a
//! translucent wash rather than a clear - so each frame keeps ~62% of the
//! last. That is reproduced by retaining the accumulator between frames.
//!
//! ACCUMULATION PARITY: numpy's `bincount` adds weights in sample order into
//! an f64 accumulator per pixel; the f32 buffer then receives the ROUNDED
//! f64 total. This port reproduces that exactly (per-pixel f64 totals,
//! single pass in sample order, one f32 rounding per flush).
//!
//! THE OUTPUT IMAGE IS NEVER REUSED: the cairo surface the result is handed
//! over in is allocated fresh every frame. Once a surface has been painted
//! into a GTK frame, GSK may hold a snapshot of it, and Cairo forbids
//! marking a snapshotted surface dirty. A new surface per frame means no
//! pixel a snapshot can see is ever written again.

use crate::theme::RGB;
use cairo::Context;
use cairo::ImageSurface;

/// Bounded cache of accumulators, keyed by (species key, w, h).
pub struct FieldEntry {
    pub key: String,
    pub w: usize,
    pub h: usize,
    pub stride: usize,
    /// The accumulator persists between frames for trailing species.
    pub acc: Vec<f32>,
    /// Excitation-weighted density (E) and hue-weighted excitation (H).
    pub acc_e: Vec<f32>,
    pub acc_h: Vec<f32>,
}

pub struct FieldCache {
    entries: Vec<FieldEntry>,
    limit: usize,
}

impl Default for FieldCache {
    fn default() -> Self {
        FieldCache::new(3)
    }
}

impl FieldCache {
    pub fn new(limit: usize) -> Self {
        FieldCache { entries: Vec::new(), limit }
    }

    pub fn clear(&mut self) {
        self.entries.clear();
    }

    /// (entries, bytes) currently held. The accumulators are the largest
    /// single allocation in the program at a big window, so they are counted.
    pub fn inventory(&self) -> (usize, usize) {
        let b = self
            .entries
            .iter()
            .map(|e| e.w * e.h * 4 * 3) // acc, acc_e, acc_h, f32 each
            .sum();
        (self.entries.len(), b)
    }

    /// The keys currently resident, for the ownership inventory.
    pub fn keys(&self) -> Vec<(String, usize, usize)> {
        self.entries.iter().map(|e| (e.key.clone(), e.w, e.h)).collect()
    }

    /// Fetch (or create) the entry for (key, w, h), LRU order, bounded.
    pub fn entry(&mut self, key: &str, w: usize, h: usize) -> &mut FieldEntry {
        let stride =
            cairo::Format::ARgb32.stride_for_width(w as u32).unwrap() as usize;
        if let Some(pos) = self.entries.iter().position(|e| e.key == key && e.w == w && e.h == h) {
            let ent = self.entries.remove(pos);
            self.entries.push(ent);
        } else {
            let ent = FieldEntry {
                key: key.to_string(),
                w,
                h,
                stride,
                acc: vec![0.0; w * h],
                acc_e: vec![0.0; w * h],
                acc_h: vec![0.0; w * h],
            };
            self.entries.push(ent);
            while self.entries.len() > self.limit {
                self.entries.remove(0);
            }
        }
        self.entries.last_mut().unwrap()
    }
}

/// Count density, excitation and hue into the three accumulators.
///
/// One index computation serves all three counts. A trailing species decays
/// all three together, so the colour of its trail stays the colour of the
/// pulse that laid it down. A clearing species (persist <= 0) assigns: the
/// count IS the frame, no zero-then-add.
pub fn accumulate_physio(
    ent: &mut FieldEntry,
    px: &[f32],
    py: &[f32],
    weight: &[f32],
    excite: &[f32],
    hue: &[f32],
    persist: f64,
) {
    let w = ent.w;
    let h = ent.h;
    let n = w * h;
    let persist32 = persist as f32;

    if persist > 0.0 {
        for v in ent.acc.iter_mut() {
            *v *= persist32;
        }
        for v in ent.acc_e.iter_mut() {
            *v *= persist32;
        }
        for v in ent.acc_h.iter_mut() {
            *v *= persist32;
        }
    }

    // bincount-equivalent: per-pixel f64 totals, added in sample order, then
    // ONE f32 rounding per pixel on flush - exactly like the NumPy original.
    let mut tot = vec![0.0f64; n];
    let mut tot_e = vec![0.0f64; n];
    let mut tot_h = vec![0.0f64; n];
    let mut any = false;
    for k in 0..px.len() {
        // numpy float->int cast truncates toward zero
        let ix = px[k] as i32;
        let iy = py[k] as i32;
        if ix >= 0 && (ix as usize) < w && iy >= 0 && (iy as usize) < h {
            any = true;
            let idx = iy as usize * w + ix as usize;
            let wt = weight[k] as f64;
            let we = weight[k] * excite[k];
            tot[idx] += wt;
            tot_e[idx] += we as f64;
            tot_h[idx] += (we * hue[k]) as f64;
        }
    }
    if !any {
        if persist <= 0.0 {
            ent.acc.iter_mut().for_each(|v| *v = 0.0);
            ent.acc_e.iter_mut().for_each(|v| *v = 0.0);
            ent.acc_h.iter_mut().for_each(|v| *v = 0.0);
        }
        return;
    }
    if persist > 0.0 {
        for k in 0..n {
            ent.acc[k] += tot[k] as f32;
            ent.acc_e[k] += tot_e[k] as f32;
            ent.acc_h[k] += tot_h[k] as f32;
        }
    } else {
        for k in 0..n {
            ent.acc[k] = tot[k] as f32;
            ent.acc_e[k] = tot_e[k] as f32;
            ent.acc_h[k] = tot_h[k] as f32;
        }
    }
}

/// Count points into the accumulator and return the lit-pixel count.
/// Points outside the buffer are dropped rather than clamped.
pub fn accumulate(
    ent: &mut FieldEntry,
    px: &[f32],
    py: &[f32],
    weight: &[f32],
    persist: f64,
) {
    let w = ent.w;
    let h = ent.h;
    let n = w * h;
    let persist32 = persist as f32;
    if persist > 0.0 {
        for v in ent.acc.iter_mut() {
            *v *= persist32;
        }
    } else {
        ent.acc.iter_mut().for_each(|v| *v = 0.0);
    }
    let mut tot = vec![0.0f64; n];
    let mut any = false;
    for k in 0..px.len() {
        let ix = px[k] as i32;
        let iy = py[k] as i32;
        if ix >= 0 && (ix as usize) < w && iy >= 0 && (iy as usize) < h {
            any = true;
            let idx = iy as usize * w + ix as usize;
            tot[idx] += weight[k] as f64;
        }
    }
    if !any {
        return;
    }
    for k in 0..n {
        ent.acc[k] += tot[k] as f32;
    }
}

/// RGB tuple channel access by index (R=0, G=1, B=2).
#[inline]
fn rgb_ch(c: RGB, ch: usize) -> f64 {
    match ch {
        0 => c.0,
        1 => c.1,
        _ => c.2,
    }
}

/// Excitation -> extra luminance, and -> how far a pixel's colour leaves the
/// resting density ramp for the pulse palette.
pub const EXCITE_GLOW: f64 = 1.15;
pub const EXCITE_MIX: f64 = 1.6;

/// Map the three accumulators to colour, on LIT PIXELS ONLY, and blit.
///
/// A creature covers a small fraction of its field, so the colour map runs
/// over the pixels the equation actually reached. Per pixel:
///
/// ```text
///     alpha   soft-knee of density plus the pulse's extra glow
///     base    the resting ramp: deep-field blue -> core cyan by density
///     pulse   the palette at the pixel's own mean hue
///     colour  base -> pulse by the pixel's excitation fraction
/// ```
#[allow(clippy::too_many_arguments)]
pub fn paint_physio(
    cr: &Context,
    ent: &FieldEntry,
    x0: f64,
    y0: f64,
    rgb_lo: RGB,
    rgb_hi: RGB,
    lut: &[[f32; 3]; 256],
    ink: f64,
    knee: f64,
    scale_up: f64,
) {
    let w = ent.w;
    let h = ent.h;
    let stride = ent.stride;
    let thr = (0.4 / 255.0 / ink.max(1e-6)) as f32;

    // fresh image per frame - see "THE OUTPUT IMAGE IS NEVER REUSED"
    let mut backing = vec![0u8; stride * h];

    let mut lit: Vec<usize> = Vec::new();
    for k in 0..w * h {
        if ent.acc[k] > thr {
            lit.push(k);
        }
    }
    if !lit.is_empty() {
        let lut_n = lut.len() - 1; // 255
        for &k in &lit {
            let v = ent.acc[k];
            let mut e = ent.acc_e[k];
            let mut hs = ent.acc_h[k];

            // soft-knee alpha with the pulse's extra glow
            let mut a = v + e * EXCITE_GLOW as f32;
            a *= -(ink as f32);
            a = a.exp();
            a = 1.0f32 - a;
            if knee != 1.0 {
                a = a.powf((1.0 / knee) as f32);
            }
            a = a.clamp(0.0, 1.0);
            // mix toward the core colour with density
            let mut m = v * (ink * 0.55) as f32;
            m = m.clamp(0.0, 1.0);
            // the pixel's excitation fraction
            let mut x = e / v;
            x *= EXCITE_MIX as f32;
            x = x.clamp(0.0, 1.0);
            // hue: mean hue of the pixel, LUT-indexed
            e = e.max(1e-9f32);
            hs /= e;
            hs *= lut_n as f32;
            let li = (hs as i32).clamp(0, lut_n as i32) as usize; // trunc toward zero

            let a255 = a * 255.0f32;
            let mut pix: u32 = (a255 as u32) << 24;
            // (channel, shift): ARGB32 memory order is B,G,R,A
            for (ch, shift) in [(2usize, 0u32), (1, 8), (0, 16)] {
                let lo = rgb_ch(rgb_lo, ch);
                let hi = rgb_ch(rgb_hi, ch);
                let mut c = m * ((hi - lo) as f32);
                c += lo as f32;
                c += (lut[li][ch] - c) * x;
                c *= a255;
                pix |= (c as u32) << shift;
            }
            let o = k * 4;
            backing[o] = (pix & 0xff) as u8;
            backing[o + 1] = ((pix >> 8) & 0xff) as u8;
            backing[o + 2] = ((pix >> 16) & 0xff) as u8;
            backing[o + 3] = ((pix >> 24) & 0xff) as u8;
        }
    }

    let surf = ImageSurface::create_for_data(
        backing,
        cairo::Format::ARgb32,
        w as i32,
        h as i32,
        stride as i32,
    )
    .expect("paint_physio: surface");
    cr.save().unwrap();
    if scale_up != 1.0 {
        cr.translate(x0, y0);
        cr.scale(scale_up, scale_up);
        cr.set_source_surface(&surf, 0.0, 0.0).unwrap();
        let src = cr.source();
        src.set_filter(cairo::Filter::Good);
    } else {
        cr.set_source_surface(&surf, x0.round(), y0.round()).unwrap();
    }
    cr.paint().unwrap();
    cr.restore().unwrap();
}

/// Map accumulated counts to colour and blit the field (no physiology).
///
/// Two colours, not one: ramping from the deep-field blue at one hit toward
/// the core colour where the equation crowds gives the creature its internal
/// structure, using only the density the maths already produced.
#[allow(clippy::too_many_arguments)]
pub fn paint(
    cr: &Context,
    ent: &FieldEntry,
    x0: f64,
    y0: f64,
    rgb_lo: RGB,
    rgb_hi: RGB,
    ink: f64,
    knee: f64,
    scale_up: f64,
) {
    let w = ent.w;
    let h = ent.h;
    let stride = ent.stride;
    let mut backing = vec![0u8; stride * h];
    for k in 0..w * h {
        let v = ent.acc[k];
        // Soft-knee response: linear while sparse, compressing as it
        // saturates, which is what an additive alpha composite does.
        let mut a = v * -(ink as f32);
        a = a.exp();
        a = 1.0f32 - a;
        if knee != 1.0 {
            a = a.powf((1.0 / knee) as f32);
        }
        a = a.clamp(0.0, 1.0);
        // Mix toward the core colour with density.
        let m = (v * (ink * 0.55) as f32).clamp(0.0, 1.0);
        let a255 = a * 255.0f32;
        let mut pix: u32 = (a255 as u32) << 24;
        for (ch, shift) in [(2usize, 0u32), (1, 8), (0, 16)] {
            let lo = rgb_ch(rgb_lo, ch);
            let hi = rgb_ch(rgb_hi, ch);
            // premultiplied, as cairo ARGB32 requires
            let mut c = m * ((hi - lo) * 255.0) as f32;
            c += (lo * 255.0) as f32;
            c *= a;
            pix |= (c as u32) << shift;
        }
        let o = k * 4;
        backing[o] = (pix & 0xff) as u8;
        backing[o + 1] = ((pix >> 8) & 0xff) as u8;
        backing[o + 2] = ((pix >> 16) & 0xff) as u8;
        backing[o + 3] = ((pix >> 24) & 0xff) as u8;
    }
    let surf = ImageSurface::create_for_data(
        backing,
        cairo::Format::ARgb32,
        w as i32,
        h as i32,
        stride as i32,
    )
    .expect("paint: surface");
    cr.save().unwrap();
    if scale_up != 1.0 {
        cr.translate(x0, y0);
        cr.scale(scale_up, scale_up);
        cr.set_source_surface(&surf, 0.0, 0.0).unwrap();
        let src = cr.source();
        src.set_filter(cairo::Filter::Good);
    } else {
        cr.set_source_surface(&surf, x0.round(), y0.round()).unwrap();
    }
    cr.paint().unwrap();
    cr.restore().unwrap();
}
