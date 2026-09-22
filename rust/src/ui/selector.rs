//! The specimen selector: five engraved creature keys in a mounting trough.
//! Port of ui/selector.py.
//!
//! SEPARATION: `layout()` is a pure function from a rect to key rectangles,
//! serving drawing, hit testing and label placement from one geometry so the
//! visible bank and the clickable bank can never drift apart.
//!
//! SCALING: raster hardware plates drawn with ONE uniform scale factor at
//! their authored aspect, always. Extra width becomes spacing, never stretch.

use cairo::Context;

use crate::layout::Rect;
use crate::skin::catalog as catalog;
use crate::skin::modules::Placed;
use crate::skin::surface::{draw_sprite, sprite_size};

/// Authored proportions of one key plate (tools/build_sprites.py: 256 x 267).
pub const KEY_ASPECT: f64 = 256.0 / 267.0;

/// Gap between adjacent keys, as a share of key width.
const GAP_SHARE: f64 = 0.17;

/// Height of the engraved identifier ledge beneath the keys, as a share of
/// key height, and the floor below which it is dropped rather than crushed.
const LEDGE_SHARE: f64 = 0.145;
const LEDGE_MIN: f64 = 8.0;

/// Wall of the mounting trough, as a share of its height.
const WALL: f64 = 0.055;

/// Share of the trough width the five keys may occupy.
const SPAN_SHARE: f64 = 0.78;

/// Auxiliary control heights, as a share of key height, and their aspects.
const MODE_H: f64 = 0.55;
const CYCLE_H: f64 = 0.33;
const MODE_ASPECT: f64 = 160.0 / 226.0;
const CYCLE_ASPECT: f64 = 192.0 / 113.0;

/// Mechanical travel of a pressed key, as a share of key height.
pub const PRESS_TRAVEL: f64 = 0.022;

/// The keys' seat: one shared downward translation, in key heights.
pub const SEAT_DROP: f64 = 3.0 / 168.0;

/// Where the trough, every key and every identifier landed, in pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BankGeometry {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub key_x: f64, // left edge of key 0
    pub key_y: f64,
    pub key_w: f64,
    pub key_h: f64,
    pub pitch: f64,   // key_w + gap
    pub ledge: f64,   // height of the identifier ledge, 0 if dropped
    pub n: usize,
    pub aux_l: Rect,
    pub aux_r: Rect,
    /// Top of the engraved label ledge when the mounting plate provides one
    /// below the key; 0 means directly under it.
    pub ledge_y: f64,
}

impl BankGeometry {
    pub fn key_rect(&self, i: usize) -> Rect {
        Rect::new(
            self.key_x + i as f64 * self.pitch,
            self.key_y,
            self.key_w,
            self.key_h,
        )
    }

    /// The engraved identifier ledge beneath key `i`.
    pub fn label_rect(&self, i: usize) -> Rect {
        let k = self.key_rect(i);
        Rect::new(
            k.x,
            if self.ledge_y != 0.0 { self.ledge_y } else { k.bottom() },
            k.w,
            self.ledge,
        )
    }

    /// Centre and radius of the key's illuminated strip, for the light pass.
    pub fn lamp_point(&self, i: usize) -> (f64, f64, f64) {
        let k = self.key_rect(i);
        (k.cx(), k.y + k.h * 0.13, k.w * 0.30)
    }

    /// The bounding box of the five keys, for seating the backing rail.
    pub fn keys_rect(&self) -> Rect {
        Rect::new(
            self.key_x,
            self.key_y,
            self.pitch * (self.n as f64 - 1.0) + self.key_w,
            self.key_h,
        )
    }

    pub fn valid(&self) -> bool {
        self.w > 8.0 && self.key_w > 6.0 && self.key_h > 6.0
    }
}

/// Key geometry from the placed SELECTOR module's own trued wells. Pure.
pub fn from_module(p: &Placed, n: usize) -> BankGeometry {
    let k0 = p.bay("key_0");
    let k1 = p.bay("key_1");
    let ledge = p.bay("ledge_0");
    if !k0.valid() {
        return layout(Rect::NONE, n);
    }
    let lh = if ledge.h >= LEDGE_MIN { ledge.h } else { 0.0 };
    let r = p.rect();
    BankGeometry {
        x: r.x,
        y: r.y,
        w: r.w,
        h: r.h,
        key_x: k0.x,
        key_y: k0.y + k0.h * SEAT_DROP,
        key_w: k0.w,
        key_h: k0.h,
        pitch: k1.x - k0.x,
        ledge: lh,
        n,
        aux_l: p.bay("rocker"),
        aux_r: p.bay("mode"),
        ledge_y: ledge.y + (ledge.h - lh) * 0.5,
    }
}

/// Trough width / trough height at the bank's natural proportions.
pub fn natural_aspect(n: usize) -> f64 {
    let inner = 1.0 - 2.0 * WALL;
    let key_h = inner * (1.0 - LEDGE_SHARE);
    let key_w = key_h * KEY_ASPECT;
    let span = n as f64 * key_w + (n as f64 - 1.0) * key_w * GAP_SHARE;
    span / SPAN_SHARE
}

/// Fit the bank inside `box`, aspect-true and centred. Pure.
pub fn layout(box_: Rect, n: usize) -> BankGeometry {
    if box_.w < 12.0 || box_.h < 10.0 {
        return BankGeometry {
            x: box_.x,
            y: box_.y,
            w: 0.0,
            h: 0.0,
            key_x: 0.0,
            key_y: 0.0,
            key_w: 0.0,
            key_h: 0.0,
            pitch: 0.0,
            ledge: 0.0,
            n,
            aux_l: Rect::NONE,
            aux_r: Rect::NONE,
            ledge_y: 0.0,
        };
    }
    let wall = (box_.h * WALL).max(2.0);
    let inner_h = box_.h - wall * 2.0;
    let inner_w = box_.w - wall * 2.0;

    let mut ledge = inner_h * LEDGE_SHARE;
    if ledge < LEDGE_MIN {
        ledge = 0.0;
    }
    let mut key_h = inner_h - ledge;
    let mut key_w = key_h * KEY_ASPECT;
    let mut gap = key_w * GAP_SHARE;
    let mut span = n as f64 * key_w + (n as f64 - 1.0) * gap;

    let budget = inner_w.min(box_.w * SPAN_SHARE);
    if span > budget {
        // Too narrow at this height: shrink the keys uniformly on BOTH axes.
        let k = budget / span;
        key_w *= k;
        key_h *= k;
        gap *= k;
        ledge *= k;
        if ledge < LEDGE_MIN {
            ledge = 0.0;
        }
        span = budget;
    }

    let key_x = box_.x + (box_.w - span) * 0.5;
    let key_y = box_.y + (box_.h - (key_h + ledge)) * 0.5 + key_h * SEAT_DROP;

    // Auxiliary seats in the metal left over at each end.
    let end = key_x - box_.x - wall;
    let cy = key_y + key_h * 0.5;
    let cyc_h = key_h * CYCLE_H;
    let cyc_w = cyc_h * CYCLE_ASPECT;
    let mode_h = key_h * MODE_H;
    let mode_w = mode_h * MODE_ASPECT;
    let aux_l = if end > cyc_w * 1.20 {
        Rect::new(
            box_.x + wall + (end - cyc_w) * 0.5,
            cy - cyc_h * 0.5,
            cyc_w,
            cyc_h,
        )
    } else {
        Rect::NONE
    };
    let aux_r = if end > mode_w * 1.60 {
        Rect::new(
            box_.right() - wall - (end + mode_w) * 0.5,
            cy - mode_h * 0.5,
            mode_w,
            mode_h,
        )
    } else {
        Rect::NONE
    };

    BankGeometry {
        x: box_.x,
        y: box_.y,
        w: box_.w,
        h: box_.h,
        key_x,
        key_y,
        key_w,
        key_h,
        pitch: key_w + gap,
        ledge,
        n,
        aux_l,
        aux_r,
        ledge_y: 0.0,
    }
}

/// Index of the key under (px, py), or None.
pub fn hit(geo: &BankGeometry, px: f64, py: f64) -> Option<usize> {
    if !geo.valid() {
        return None;
    }
    if !(geo.key_y..=geo.key_y + geo.key_h).contains(&py) {
        return None;
    }
    let rel = px - geo.key_x;
    if rel < 0.0 {
        return None;
    }
    let i = (rel / geo.pitch) as usize;
    if i >= geo.n {
        return None;
    }
    // Reject the gap between two keys: the pitch includes it.
    if rel - i as f64 * geo.pitch > geo.key_w {
        return None;
    }
    Some(i)
}

/// Resolve one key to a plate state. Order matters:
/// disabled > pressed > latched (the live specimen) > focus > idle.
pub fn key_state(
    i: usize,
    active: usize,
    pressed: Option<usize>,
    focus: Option<usize>,
    disabled: &[usize],
) -> &'static str {
    if disabled.contains(&i) {
        return "disabled";
    }
    if pressed == Some(i) {
        return "pressed";
    }
    if i == active {
        return "latched";
    }
    if focus == Some(i) {
        return "focus";
    }
    "idle"
}

/// Which of the two authored plates a resolved state uses.
pub fn plate(state: &str) -> &'static str {
    if state == "pressed" || state == "latched" {
        "active"
    } else {
        "inactive"
    }
}

pub fn sprite_name(i: usize, state: &str) -> String {
    catalog::specimen_key(i, plate(state))
}

pub fn sprites_available(n: usize) -> bool {
    (0..n).all(|i| {
        ["inactive", "active"]
            .iter()
            .all(|&s| sprite_size(&catalog::specimen_key(i, s)).0 > 0)
    })
}

/// Blit the five key plates. The mounting is drawn by the caller first.
pub fn draw_keys(
    cr: &Context,
    geo: &BankGeometry,
    active: usize,
    pressed: Option<usize>,
    focus: Option<usize>,
    disabled: &[usize],
    alpha: f64,
) {
    if !geo.valid() {
        return;
    }
    for i in 0..geo.n {
        let st = key_state(i, active, pressed, focus, disabled);
        let r = geo.key_rect(i);
        let dy = if st == "pressed" { geo.key_h * PRESS_TRAVEL } else { 0.0 };
        let a = if st == "disabled" { alpha * 0.42 } else { alpha };
        draw_sprite(cr, &sprite_name(i, st), r.x, r.y + dy, r.w, r.h, a);
    }
}
