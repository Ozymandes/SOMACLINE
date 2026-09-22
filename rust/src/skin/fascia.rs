//! Generated fascia panels, and the bays that were manufactured into them.
//! Port of skin/fascia.py.
//!
//! A `Fascia` is a sprite PLUS the measured source-pixel rectangle of every
//! recess cut into it. `place()` scales the panel to a target rect and
//! returns the same bays in widget pixels, so text is drawn INSIDE a bay and
//! cannot land on a bevel crest or spill onto bare metal.

use cairo::Context;

use crate::layout::Rect;
use crate::skin::surface::{draw_nine, sprite_size, MIN_BORDER_SCALE, NineSlice};

/// One fascia positioned on the panel, with its bays in widget pixels.
#[derive(Clone, Copy)]
pub struct Placed {
    pub fascia: &'static Fascia,
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub kx: f64,
    pub ky: f64,
}

impl Placed {
    /// The recess named `key`, in widget pixels. Invalid Rect if absent.
    pub fn bay(&self, key: &str) -> Rect {
        let Some(&[sx, sy, sw, sh]) = self.fascia.bay_src(key) else {
            return Rect::NONE;
        };
        let x0 = self.mx(sx);
        let x1 = self.mx(sx + sw);
        let y0 = self.my(sy);
        let y1 = self.my(sy + sh);
        Rect::new(x0, y0, (x1 - x0).max(0.0), (y1 - y0).max(0.0))
    }

    pub fn point(&self, sx: f64, sy: f64) -> (f64, f64) {
        (self.mx(sx), self.my(sy))
    }

    /// A source length that must stay round (a lamp diameter), in pixels.
    pub fn scale_y(&self, src_len: f64) -> f64 {
        src_len * self.ky
    }

    fn mx(&self, v: f64) -> f64 {
        let f = self.fascia;
        self.x + axis(v, f.sw, f.cap_l, f.cap_r, self.w, self.kx)
    }

    fn my(&self, v: f64) -> f64 {
        let f = self.fascia;
        self.y + axis(v, f.sh, f.cap_t, f.cap_b, self.h, self.ky)
    }
}

/// Map a source coordinate through one axis of the three-band slice.
fn axis(v: f64, size: i32, cap0: i32, cap1: i32, dst: f64, k: f64) -> f64 {
    let d0 = cap0 as f64 * k;
    let d1 = cap1 as f64 * k;
    if v <= cap0 as f64 {
        return v * k;
    }
    if v >= (size - cap1) as f64 {
        return dst - (size as f64 - v) * k;
    }
    let mid_s = (size - cap0 - cap1) as f64;
    if mid_s <= 0.0 {
        return d0;
    }
    d0 + (v - cap0 as f64) * ((dst - d0 - d1) / mid_s)
}

/// A generated panel and the recesses manufactured into it.
#[derive(Clone, Copy)]
pub struct Fascia {
    pub name: &'static str,
    pub sw: i32,
    pub sh: i32,
    pub cap_l: i32,
    pub cap_t: i32,
    pub cap_r: i32,
    pub cap_b: i32,
    /// key -> (x, y, w, h) in SOURCE pixels.
    pub bays: &'static [(&'static str, [f64; 4])],
}

impl Fascia {
    fn bay_src(&self, key: &str) -> Option<&[f64; 4]> {
        self.bays.iter().find(|(k, _)| *k == key).map(|(_, v)| v)
    }

    pub fn aspect(&self) -> f64 {
        self.sw as f64 / self.sh as f64
    }

    pub fn nine(&self) -> NineSlice {
        NineSlice::new(self.name, self.cap_l, self.cap_t, self.cap_r, self.cap_b,
                       0.0, 0.0, 0.0, 0.0)
    }

    /// Position (do not draw) the fascia in `r`, returning its bay map.
    pub fn place(&'static self, r: Rect) -> Placed {
        let w = r.w.max(1.0);
        let h = r.h.max(1.0);
        // One factor per axis, clamped exactly as NineSlice::shrink clamps the
        // drawn slice, so the map and the metal agree at every size.
        let k = (w / self.sw as f64).min(h / self.sh as f64);
        let k = k.max(MIN_BORDER_SCALE).min(1.0);
        let mut kx = k;
        let mut ky = k;
        let lr = (self.cap_l + self.cap_r) as f64 * kx;
        let tb = (self.cap_t + self.cap_b) as f64 * ky;
        if lr > w * 0.66 && lr > 0.0 {
            kx *= (0.0f64).max((w * 0.66) / lr);
        }
        if tb > h * 0.66 && tb > 0.0 {
            ky *= (0.0f64).max((h * 0.66) / tb);
        }
        Placed { fascia: self, x: r.x, y: r.y, w, h, kx, ky }
    }

    /// Draw the panel into `r` and hand back its bays. One call, one panel.
    pub fn draw(&'static self, cr: &Context, r: Rect, alpha: f64) -> Placed {
        let p = self.place(r);
        if sprite_size(self.name).0 > 0 {
            draw_nine(cr, &self.nine(), r.x, r.y, r.w, r.h, alpha);
        }
        p
    }
}

// --------------------------------------------------------------------------
// The measured library
// --------------------------------------------------------------------------
/// Top command fascia. Four information bays over a three-bay status rail.
pub static HEADER: Fascia = Fascia {
    name: "frame/header_fascia",
    sw: 1600,
    sh: 214,
    cap_l: 66,
    cap_t: 22,
    cap_r: 64,
    cap_b: 24,
    bays: &[
        ("title", [78.0, 29.0, 545.0, 95.0]),
        ("epithet", [635.0, 29.0, 401.0, 95.0]),
        ("live", [1048.0, 29.0, 239.0, 95.0]),
        ("clock", [1299.0, 28.0, 225.0, 96.0]),
        ("rail_0", [78.0, 140.0, 480.0, 49.0]),
        ("rail_1", [570.0, 140.0, 459.0, 49.0]),
        ("rail_2", [1042.0, 140.0, 482.0, 49.0]),
        ("live_lamp", [1086.0, 46.0, 60.0, 60.0]),
        ("live_window", [1145.0, 53.0, 114.0, 46.0]),
    ],
};

/// Bottom archive rail: a machined badge boss, eight key/value bays, and a
/// three-line block at the right end for the standing motto.
pub static FOOTER: Fascia = Fascia {
    name: "frame/footer_rail",
    sw: 1600,
    sh: 241,
    cap_l: 52,
    cap_t: 40,
    cap_r: 50,
    cap_b: 44,
    bays: &[
        ("badge", [63.0, 52.0, 138.0, 137.0]),
        ("bay_0", [212.0, 52.0, 127.0, 137.0]),
        ("bay_1", [350.0, 52.0, 126.0, 137.0]),
        ("bay_2", [487.0, 52.0, 126.0, 137.0]),
        ("bay_3", [624.0, 52.0, 127.0, 137.0]),
        ("bay_4", [761.0, 52.0, 127.0, 137.0]),
        ("bay_5", [899.0, 52.0, 127.0, 137.0]),
        ("bay_6", [1037.0, 52.0, 127.0, 137.0]),
        ("bay_7", [1175.0, 52.0, 127.0, 137.0]),
        ("motto", [1316.0, 52.0, 222.0, 137.0]),
    ],
};

/// One telemetry module: graph recess, numeric recess, compact status recess,
/// identity plaque, title bay and four annunciator wells.
pub static MODULE: Fascia = Fascia {
    name: "frame/module_shell",
    sw: 1152,
    sh: 371,
    cap_l: 76,
    cap_t: 20,
    cap_r: 30,
    cap_b: 30,
    bays: &[
        ("plaque", [83.0, 19.0, 199.0, 59.0]),
        ("title", [287.0, 21.0, 589.0, 52.0]),
        ("graph", [82.0, 111.0, 379.0, 226.0]),
        ("numeric", [501.0, 111.0, 395.0, 226.0]),
        ("status", [936.0, 110.0, 188.0, 227.0]),
        ("rail", [15.0, 62.0, 27.0, 298.0]),
        ("lamp_0", [896.0, 27.0, 42.0, 42.0]),
        ("lamp_1", [957.0, 27.0, 42.0, 42.0]),
        ("lamp_2", [1017.0, 27.0, 42.0, 42.0]),
        ("lamp_3", [1077.0, 27.0, 42.0, 42.0]),
        ("legend", [880.0, 69.0, 260.0, 11.0]),
    ],
};

/// Four-bay mounting rack. Generated, and deliberately NOT used at runtime.
pub static RACK: Fascia = Fascia {
    name: "frame/telemetry_rack",
    sw: 896,
    sh: 1120,
    cap_l: 52,
    cap_t: 25,
    cap_r: 53,
    cap_b: 63,
    bays: &[
        ("bay_0", [115.0, 64.0, 711.0, 227.0]),
        ("bay_1", [115.0, 318.0, 711.0, 220.0]),
        ("bay_2", [115.0, 565.0, 711.0, 218.0]),
        ("bay_3", [115.0, 811.0, 711.0, 222.0]),
    ],
};

/// The five-position selector fascia: engraved label ledges above each well.
pub static SELECTOR_BANK: Fascia = Fascia {
    name: "selector/bank_empty",
    sw: 1280,
    sh: 351,
    cap_l: 40,
    cap_t: 14,
    cap_r: 40,
    cap_b: 14,
    bays: &[],
};
