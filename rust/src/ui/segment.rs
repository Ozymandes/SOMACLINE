//! Procedural seven-segment electronic display. Port of ui/segment.py.
//!
//! Drawing is pure Cairo path work, so it stays crisp at any size and needs
//! no cache. Geometry is authored in a unit cell and scaled by the requested
//! digit height. The look: vintage digital watch / laboratory electronics /
//! aerospace instrumentation. Restrained.

use cairo::Context;
use cairo::LineJoin;

/// Segment identity, in the conventional order:
///      a
///    f   b
///      g
///    e   c
///      d
pub const SEG_A: usize = 0;
pub const SEG_B: usize = 1;
pub const SEG_C: usize = 2;
pub const SEG_D: usize = 3;
pub const SEG_E: usize = 4;
pub const SEG_F: usize = 5;
pub const SEG_G: usize = 6;

/// Which segments are lit for each supported character.
fn glyphs(ch: char) -> Option<&'static [usize]> {
    Some(match ch {
        '0' => &[SEG_A, SEG_B, SEG_C, SEG_D, SEG_E, SEG_F],
        '1' => &[SEG_B, SEG_C],
        '2' => &[SEG_A, SEG_B, SEG_G, SEG_E, SEG_D],
        '3' => &[SEG_A, SEG_B, SEG_G, SEG_C, SEG_D],
        '4' => &[SEG_F, SEG_G, SEG_B, SEG_C],
        '5' => &[SEG_A, SEG_F, SEG_G, SEG_C, SEG_D],
        '6' => &[SEG_A, SEG_F, SEG_G, SEG_E, SEG_C, SEG_D],
        '7' => &[SEG_A, SEG_B, SEG_C],
        '8' => &[SEG_A, SEG_B, SEG_C, SEG_D, SEG_E, SEG_F, SEG_G],
        '9' => &[SEG_A, SEG_B, SEG_C, SEG_D, SEG_F, SEG_G],
        '-' => &[SEG_G],
        '_' => &[SEG_D],
        'A' => &[SEG_A, SEG_B, SEG_C, SEG_E, SEG_F, SEG_G],
        'C' => &[SEG_A, SEG_D, SEG_E, SEG_F],
        'E' => &[SEG_A, SEG_D, SEG_E, SEG_F, SEG_G],
        'F' => &[SEG_A, SEG_E, SEG_F, SEG_G],
        'G' => &[SEG_A, SEG_C, SEG_D, SEG_E, SEG_F],
        'H' => &[SEG_B, SEG_C, SEG_E, SEG_F, SEG_G],
        'J' => &[SEG_B, SEG_C, SEG_D],
        'L' => &[SEG_D, SEG_E, SEG_F],
        'P' => &[SEG_A, SEG_B, SEG_E, SEG_F, SEG_G],
        'S' => &[SEG_A, SEG_C, SEG_D, SEG_F, SEG_G],
        'U' => &[SEG_B, SEG_C, SEG_D, SEG_E, SEG_F],
        'Y' => &[SEG_B, SEG_C, SEG_D, SEG_F, SEG_G],
        'b' => &[SEG_C, SEG_D, SEG_E, SEG_F, SEG_G],
        'c' => &[SEG_D, SEG_E, SEG_G],
        'd' => &[SEG_B, SEG_C, SEG_D, SEG_E, SEG_G],
        'h' => &[SEG_C, SEG_E, SEG_F, SEG_G],
        'n' => &[SEG_C, SEG_E, SEG_G],
        'o' => &[SEG_C, SEG_D, SEG_E, SEG_G],
        'r' => &[SEG_E, SEG_G],
        't' => &[SEG_D, SEG_E, SEG_F, SEG_G],
        'u' => &[SEG_C, SEG_D, SEG_E],
        _ => return None,
    })
}

/// Characters that occupy a narrow cell rather than a full digit cell.
fn narrow(ch: char) -> Option<f64> {
    match ch {
        '.' => Some(0.34),
        ':' => Some(0.34),
        '/' => Some(0.58),
        '\'' => Some(0.30),
        '\u{00b0}' => Some(0.46),
        '%' => Some(0.66),
        ' ' => Some(0.34),
        _ => None,
    }
}

/// Appearance of one readout. Colours are linear 0..1 RGB.
#[derive(Clone, Copy, Debug)]
pub struct SegmentStyle {
    pub lit: (f64, f64, f64),
    pub thickness: f64, // of digit HEIGHT
    pub slant: f64,     // italic shear, fraction of height
    pub gap: f64,       // gap between adjacent segments, of height
    pub aspect: f64,    // digit width / height
    pub tracking: f64,  // inter-digit space, of height
    pub ghost: f64,     // unlit segment alpha
    pub bloom: f64,     // halo strength, 0 disables
    pub alpha: f64,
}

impl Default for SegmentStyle {
    fn default() -> Self {
        SegmentStyle {
            lit: (0.62, 0.86, 0.94),
            thickness: 0.118,
            slant: 0.045,
            gap: 0.030,
            aspect: 0.560,
            tracking: 0.190,
            ghost: 0.070,
            bloom: 0.30,
            alpha: 1.0,
        }
    }
}

// Palette for the instrument's readout moods.
pub const CYAN: SegmentStyle = SegmentStyle { lit: (0.60, 0.86, 0.95), thickness: 0.118, slant: 0.045, gap: 0.030, aspect: 0.560, tracking: 0.190, ghost: 0.070, bloom: 0.30, alpha: 1.0 };
pub const AMBER: SegmentStyle = SegmentStyle { lit: (0.98, 0.64, 0.16), thickness: 0.118, slant: 0.045, gap: 0.030, aspect: 0.560, tracking: 0.190, ghost: 0.070, bloom: 0.30, alpha: 1.0 };
pub const RED: SegmentStyle = SegmentStyle { lit: (0.92, 0.26, 0.22), thickness: 0.118, slant: 0.045, gap: 0.030, aspect: 0.560, tracking: 0.190, ghost: 0.070, bloom: 0.30, alpha: 1.0 };
pub const PALE: SegmentStyle = SegmentStyle { lit: (0.84, 0.90, 0.95), thickness: 0.118, slant: 0.045, gap: 0.030, aspect: 0.560, tracking: 0.190, ghost: 0.070, bloom: 0.30, alpha: 1.0 };

/// Chamfered segment body, as a real LED module has.
/// `kind` is 'h' or 'v'; (x0,y0)-(x1,y1) is the segment's centre line.
fn seg_poly(kind: char, x0: f64, y0: f64, x1: f64, y1: f64, t: f64) -> [(f64, f64); 6] {
    let h = t * 0.5;
    if kind == 'h' {
        [
            (x0, y0),
            (x0 + h, y0 - h),
            (x1 - h, y0 - h),
            (x1, y0),
            (x1 - h, y0 + h),
            (x0 + h, y0 + h),
        ]
    } else {
        [
            (x0, y0),
            (x0 + h, y0 + h),
            (x0 + h, y1 - h),
            (x0, y1),
            (x0 - h, y1 - h),
            (x0 - h, y0 + h),
        ]
    }
}

/// Centre lines of all seven segments inside a w x h digit cell.
#[allow(clippy::type_complexity)]
fn cell_segments(w: f64, h: f64, t: f64, gap: f64) -> [(usize, char, f64, f64, f64, f64); 7] {
    let hh = t * 0.5;
    let mid = h * 0.5;
    let xl = hh;
    let xr = w - hh; // vertical centre lines
    let yt = hh;
    let yb = h - hh; // horizontal centre lines
    let hx0 = gap + hh;
    let hx1 = w - gap - hh; // horizontal span
    let vy_top = gap + hh; // vertical span, upper pair
    let vy_mid_hi = mid - gap * 0.5;
    let vy_mid_lo = mid + gap * 0.5;
    let vy_bot = h - gap - hh;
    [
        (SEG_A, 'h', hx0, yt, hx1, yt),
        (SEG_D, 'h', hx0, yb, hx1, yb),
        (SEG_G, 'h', hx0, mid, hx1, mid),
        (SEG_F, 'v', xl, vy_top, xl, vy_mid_hi),
        (SEG_E, 'v', xl, vy_mid_lo, xl, vy_bot),
        (SEG_B, 'v', xr, vy_top, xr, vy_mid_hi),
        (SEG_C, 'v', xr, vy_mid_lo, xr, vy_bot),
    ]
}

/// Advance width of `text` at `height`, matching what draw() emits.
pub fn measure(text: &str, height: f64, st: &SegmentStyle) -> f64 {
    let dw = height * st.aspect;
    let track = height * st.tracking;
    let mut w = 0.0;
    for (i, ch) in text.chars().enumerate() {
        if i > 0 {
            w += track;
        }
        w += match narrow(ch) {
            Some(n) => height * n,
            None => dw,
        };
    }
    w
}

fn path_poly(cr: &Context, pts: &[(f64, f64)], ox: f64, oy: f64, shear: f64, h: f64) {
    for (i, &(px, py)) in pts.iter().enumerate() {
        // Shear about the cell's vertical centre so digits lean like a watch.
        let sx = ox + px + (h * 0.5 - py) * shear;
        let sy = oy + py;
        if i == 0 {
            cr.move_to(sx, sy);
        } else {
            cr.line_to(sx, sy);
        }
    }
    cr.close_path();
}

/// Draw `text` with its LEFT-TOP at (x, y). Returns the advance width.
///
/// Unsupported characters are skipped rather than raising: a readout must
/// never be able to take the frame down.
pub fn draw(cr: &Context, text: &str, x: f64, y: f64, height: f64, st: &SegmentStyle) -> f64 {
    if height < 2.0 || text.is_empty() {
        return 0.0;
    }

    let dw = height * st.aspect;
    let t = (height * st.thickness).max(1.0);
    let gap = height * st.gap;
    let track = height * st.tracking;
    let shear = st.slant;
    let (r, g, b) = st.lit;
    let a = st.alpha.clamp(0.0, 1.0);

    let geo = cell_segments(dw, height, t, gap);
    let mut cx = x;

    for (i, ch) in text.chars().enumerate() {
        if i > 0 {
            cx += track;
        }
        if let Some(n) = narrow(ch) {
            let nw = height * n;
            draw_narrow(cr, ch, cx, y, nw, height, t, st, a);
            cx += nw;
            continue;
        }
        let Some(lit) = glyphs(ch) else {
            cx += dw;
            continue;
        };
        let on = |sid: usize| lit.contains(&sid);

        // Ghost pass: every unlit segment, barely there.
        if st.ghost > 0.0 {
            cr.set_source_rgba(r, g, b, st.ghost * a);
            for &(sid, kind, ax, ay, bx, by) in geo.iter() {
                if on(sid) {
                    continue;
                }
                path_poly(cr, &seg_poly(kind, ax, ay, bx, by, t), cx, y, shear, height);
                let _ = cr.fill();
            }
        }

        // Bloom pass: a soft halo, approximated by one fattened low-alpha copy.
        if st.bloom > 0.0 && !lit.is_empty() {
            cr.set_source_rgba(r, g, b, 0.16 * st.bloom * a);
            cr.set_line_width(t * 0.9);
            cr.set_line_join(LineJoin::Round);
            for &sid in lit {
                let (_, kind, ax, ay, bx, by) = geo[sid];
                path_poly(cr, &seg_poly(kind, ax, ay, bx, by, t), cx, y, shear, height);
                let _ = cr.stroke();
            }
        }

        cr.set_source_rgba(r, g, b, a);
        for &sid in lit {
            let (_, kind, ax, ay, bx, by) = geo[sid];
            path_poly(cr, &seg_poly(kind, ax, ay, bx, by, t), cx, y, shear, height);
            let _ = cr.fill();
        }
        cx += dw;
    }

    cx - x
}

fn draw_narrow(
    cr: &Context,
    ch: char,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    t: f64,
    st: &SegmentStyle,
    a: f64,
) {
    let (r, g, b) = st.lit;
    cr.set_source_rgba(r, g, b, a);
    let s = t * 0.82;
    match ch {
        '.' => {
            cr.rectangle(x + (w - s) * 0.5, y + h - s, s, s);
            let _ = cr.fill();
        }
        ':' => {
            cr.rectangle(x + (w - s) * 0.5, y + h * 0.32 - s * 0.5, s, s);
            cr.rectangle(x + (w - s) * 0.5, y + h * 0.70 - s * 0.5, s, s);
            let _ = cr.fill();
        }
        '\'' => {
            cr.rectangle(x + (w - s) * 0.5, y + h * 0.10, s, s * 1.4);
            let _ = cr.fill();
        }
        '/' => {
            cr.set_line_width(t * 0.80);
            cr.move_to(x + w * 0.88, y + h * 0.10);
            cr.line_to(x + w * 0.12, y + h * 0.90);
            let _ = cr.stroke();
        }
        '%' => {
            // Per-cent as a display element: two apertures and a virgule.
            cr.set_line_width((t * 0.74).max(1.0));
            cr.move_to(x + w * 0.86, y + h * 0.16);
            cr.line_to(x + w * 0.14, y + h * 0.88);
            let _ = cr.stroke();
            let d = (t * 1.25).max(1.5);
            cr.rectangle(x + w * 0.06, y + h * 0.10, d, d);
            cr.rectangle(x + w * 0.94 - d, y + h * 0.90 - d, d, d);
            let _ = cr.fill();
        }
        '\u{00b0}' => {
            // A ring in the upper third of the cell, struck at segment
            // thickness so it carries the same weight as the digits beside it.
            let rad = w * 0.42;
            cr.set_line_width((t * 0.74).max(1.0));
            if st.bloom > 0.0 {
                cr.set_source_rgba(r, g, b, 0.16 * st.bloom * a);
                cr.set_line_width((t * 1.35).max(1.4));
                cr.new_path();
                cr.arc(x + w * 0.5, y + h * 0.20 + rad, rad, 0.0, 6.283185307179586);
                let _ = cr.stroke();
                cr.set_source_rgba(r, g, b, a);
                cr.set_line_width((t * 0.74).max(1.0));
            }
            cr.new_path();
            cr.arc(x + w * 0.5, y + h * 0.20 + rad, rad, 0.0, 6.283185307179586);
            let _ = cr.stroke();
        }
        _ => {}
    }
}

/// Right-aligned variant: readouts stay pinned while their digits change.
pub fn draw_right(cr: &Context, text: &str, right_x: f64, y: f64, height: f64, st: &SegmentStyle) -> f64 {
    let w = measure(text, height, st);
    draw(cr, text, right_x - w, y, height, st);
    w
}

/// Largest digit height where `text` fits inside the box.
pub fn fit_height(text: &str, box_w: f64, box_h: f64, st: &SegmentStyle) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    let unit = measure(text, 1.0, st);
    if unit <= 0.0 {
        return 0.0;
    }
    (box_h.min(box_w / unit)).max(0.0)
}

/// Draw a UNIT beside a readout, in the same display technology.
pub fn draw_unit(
    cr: &Context,
    text: &str,
    x: f64,
    y: f64,
    digit_h: f64,
    st: &SegmentStyle,
    scale: f64,
) -> f64 {
    if text.is_empty() || digit_h < 4.0 {
        return 0.0;
    }
    let uh = digit_h * scale;
    let uy = y + digit_h - uh;
    // A unit is a caption within the display: slightly dimmer than the value
    // it qualifies, which is what stops it competing with the digits.
    let us = SegmentStyle {
        lit: st.lit,
        thickness: st.thickness * 1.10,
        slant: st.slant,
        gap: st.gap,
        aspect: st.aspect,
        tracking: st.tracking * 0.9,
        ghost: st.ghost * 0.55,
        bloom: st.bloom * 0.8,
        alpha: st.alpha * 0.92,
    };
    draw(cr, text, x, uy, uh, &us)
}

pub fn measure_unit(text: &str, digit_h: f64, st: &SegmentStyle, scale: f64) -> f64 {
    if text.is_empty() {
        0.0
    } else {
        measure(text, digit_h * scale, st)
    }
}
