//! Instrument chrome: the quiet lab-panel typography around the organism.
//! Port of ui/chrome.py.
//!
//! Immediate-mode Cairo + PangoCairo. Two entry points, both safe to call
//! every frame:
//!
//!     draw_background(cr, w, h)
//!     draw_chrome(cr, layout, tel, fps, frame_ms)
//!
//! Design rules enforced here: hairlines and type only; nothing is ever
//! drawn outside the Rect it belongs to; degenerate rects draw nothing; all
//! Pango state is cached in thread-local maps, so a frame allocates
//! essentially nothing beyond the formatted value strings.

use std::collections::HashMap;

use cairo::Antialias;
use cairo::Context;
use pango::prelude::*;
use pango::AttrList;
use pango::EllipsizeMode;
use pango::FontDescription;
use pango::Weight;

use crate::layout::Layout;
use crate::signals::Telemetry;
use crate::theme::*;

use crate::ui::fonts;

/// Runs once before the first Pango font map is built: installs the bundled
/// faces (chrome.py does this at import time; Rust callers do it explicitly).
pub fn init() {
    fonts::ensure_user_fonts();
}

// --------------------------------------------------------------------------
// constants
// --------------------------------------------------------------------------

pub const TITLE: &str = "ABYSSAL ORGANISM MONITOR";
pub const SPECIMEN: &str = "PLUMIRADIA QUADRILOBATA";
pub const SUBTITLE: &str = "THE QUARTERED PLUME";

pub const MIN_PT: f64 = 7.0; // never render type smaller than this
pub const CAP_RATIO: f64 = 0.74; // fallback cap-height guess before measurement
pub const FPS_FULL: f64 = 75.0; // FPS meter maps 0..75
pub const WARN_LEVEL: f64 = 0.85; // above this a value turns AMBER

pub const W_NORMAL: Weight = Weight::Normal;
pub const W_MEDIUM: Weight = Weight::Medium;

// "A,B,monospace" fallback chains, compiled against the theme constants
// (asserted below so a theme edit cannot silently desync them).
pub const FAMILY: &str = "JetBrainsMono Nerd Font,Noto Sans Mono,monospace";
pub const FAMILY_HERO: &str = "Astro,JetBrainsMono Nerd Font,monospace";
pub const FAMILY_LABEL: &str = "Microgramma,JetBrainsMono Nerd Font,monospace";

const fn str_eq(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let (ab, bb) = (a.as_bytes(), b.as_bytes());
    let mut i = 0;
    while i < ab.len() {
        if ab[i] != bb[i] {
            return false;
        }
        i += 1;
    }
    true
}

const _: () = {
    assert!(str_eq(FONT_MONO, "JetBrainsMono Nerd Font"));
    assert!(str_eq(FONT_MONO_FALLBACK, "Noto Sans Mono"));
    assert!(str_eq(FONT_DISPLAY, "Astro"));
    assert!(str_eq(FONT_TECH, "Microgramma"));
};

pub const LABELS: [&str; 4] = ["CPU", "TEMP", "MEM", "FPS"];
/// (index, warns?) -- FPS never warns; a high frame rate is not a fault.
pub const WARNS: [bool; 4] = [true, true, true, false];

fn hostname() -> String {
    let raw = std::fs::read_to_string("/proc/sys/kernel/hostname")
        .unwrap_or_else(|_| "LOCAL".to_string());
    let h = raw.trim().split('.').next().unwrap_or("LOCAL");
    if h.is_empty() {
        "LOCAL".to_string()
    } else {
        h.to_uppercase()
    }
}

// --------------------------------------------------------------------------
// cached pango plumbing
// --------------------------------------------------------------------------

const SCALE: f64 = 1024.0; // pango::SCALE

thread_local! {
    static PCTX: std::cell::RefCell<Option<pango::Context>> = const { std::cell::RefCell::new(None) };
    static LAY: std::cell::RefCell<Option<pango::Layout>> = const { std::cell::RefCell::new(None) };
    static FD_CACHE: std::cell::RefCell<HashMap<(u32, u8, &'static str), FontDescription>> =
        std::cell::RefCell::new(HashMap::new());
    static ATTR_CACHE: std::cell::RefCell<HashMap<i32, AttrList>> =
        std::cell::RefCell::new(HashMap::new());
    static CAP_CACHE: std::cell::RefCell<HashMap<(u32, u8, &'static str), f64>> =
        std::cell::RefCell::new(HashMap::new());
    static TW_CACHE: std::cell::RefCell<HashMap<(String, u32, u8, u32, &'static str), f64>> =
        std::cell::RefCell::new(HashMap::new());
    static BG_CACHE: std::cell::RefCell<HashMap<i32, cairo::LinearGradient>> =
        std::cell::RefCell::new(HashMap::new());
}

const TW_LIMIT: usize = 4096;
const BG_STEPS: i32 = 6;
const BG_TOP_SPAN: f64 = 0.40;
const BG_BOT_SPAN: f64 = 0.32;

fn ensure_layout() {
    LAY.with_borrow_mut(|lay| {
        if lay.is_some() {
            return;
        }
        let font_map = pangocairo::FontMap::default();
        let pctx = font_map.create_context();
        let layout = pango::Layout::new(&pctx);
        layout.set_single_paragraph_mode(true);
        PCTX.with_borrow_mut(|c| *c = Some(pctx));
        *lay = Some(layout);
    });
}

fn weight_code(w: Weight) -> u8 {
    use glib::translate::IntoGlib;
    w.into_glib() as u8
}

fn size_key(size: f64) -> u32 {
    // quantise to 1/4 px so nearby sizes share cache entries safely
    (size * 4.0).round() as u32
}

fn fd(size: f64, weight: Weight, family: &'static str) -> FontDescription {
    let key = (size_key(size), weight_code(weight), family);
    if let Some(hit) = FD_CACHE.with_borrow(|c| c.get(&key).cloned()) {
        return hit;
    }
    let mut fd = FontDescription::new();
    fd.set_family(if family.is_empty() { FAMILY } else { family });
    fd.set_weight(weight);
    fd.set_absolute_size((size * SCALE) as i32 as f64);
    FD_CACHE.with_borrow_mut(|c| c.insert(key, fd.clone()));
    fd
}

fn attrs(tracking: f64) -> Option<AttrList> {
    if tracking <= 0.01 {
        return None;
    }
    let key = (tracking * 100.0).round() as i32;
    if let Some(hit) = ATTR_CACHE.with_borrow(|c| c.get(&key).cloned()) {
        return Some(hit);
    }
    let al = AttrList::new();
    al.insert(pango::AttrInt::new_letter_spacing(
        (tracking * SCALE).round() as i32,
    ));
    ATTR_CACHE.with_borrow_mut(|c| c.insert(key, al.clone()));
    Some(al)
}

fn prepare(text: &str, size: f64, weight: Weight, tracking: f64, family: &'static str) {
    ensure_layout();
    LAY.with_borrow_mut(|lay| {
        let lay = lay.as_ref().unwrap();
        lay.set_width(-1);
        lay.set_ellipsize(EllipsizeMode::None);
        lay.set_font_description(Some(&fd(size, weight, family)));
        lay.set_attributes(attrs(tracking).as_ref());
        lay.set_text(text);
    });
}

/// Memoised visual advance width, with the trailing half letter-space removed.
pub fn text_w(text: &str, size: f64, weight: Weight, tracking: f64, family: &'static str) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    let key = (
        text.to_string(),
        (size * 100.0).round() as u32,
        weight_code(weight),
        (tracking * 1000.0).round() as u32,
        family,
    );
    if let Some(hit) = TW_CACHE.with_borrow(|c| c.get(&key).copied()) {
        return hit;
    }
    prepare(text, size, weight, tracking, family);
    let w = LAY.with_borrow(|lay| {
        let (w, _) = lay.as_ref().unwrap().size();
        (w as f64 / SCALE - tracking).max(0.0)
    });
    TW_CACHE.with_borrow_mut(|c| {
        if c.len() >= TW_LIMIT {
            c.clear();
        }
        c.insert(key, w);
    });
    w
}

/// Cap height in px -- the visual height of an all-caps line.
pub fn cap(size: f64, weight: Weight, family: &'static str) -> f64 {
    let key = (size_key(size), weight_code(weight), family);
    if let Some(hit) = CAP_CACHE.with_borrow(|c| c.get(&key).copied()) {
        return hit;
    }
    prepare("H", size, weight, 0.0, family);
    let c = LAY.with_borrow(|lay| {
        let (ink, _) = lay.as_ref().unwrap().extents();
        let h = ink.height() as f64 / SCALE;
        if h <= 0.0 {
            size * CAP_RATIO
        } else {
            h
        }
    });
    CAP_CACHE.with_borrow_mut(|c2| c2.insert(key, c));
    c
}

/// Largest size <= `size` whose text fits `max_w`. 0.0 = does not fit.
pub fn fit(text: &str, size: f64, weight: Weight, tracking: f64, max_w: f64) -> f64 {
    if text.is_empty() || max_w <= 1.0 {
        return 0.0;
    }
    let w = text_w(text, size, weight, tracking, "");
    if w <= max_w {
        return size;
    }
    // Mono advance is very close to linear in size; one guess plus a couple
    // of corrective steps converges immediately.
    let guess = size * (max_w / w);
    let mut s = (guess * 2.0).floor() / 2.0;
    for _ in 0..4 {
        if s < MIN_PT {
            return 0.0;
        }
        if text_w(text, s, weight, tracking, "") <= max_w {
            return s;
        }
        s -= 0.5;
    }
    0.0
}

/// Draw one line, positioned by baseline. Returns its visual width.
#[allow(clippy::too_many_arguments)]
pub fn show(
    cr: &Context,
    text: &str,
    size: f64,
    weight: Weight,
    tracking: f64,
    x: f64,
    baseline: f64,
    rgb: RGB,
    alpha: f64,
    align: char,
    max_w: f64,
    family: &'static str,
) -> f64 {
    if text.is_empty() || size < MIN_PT - 0.01 {
        return 0.0;
    }
    ensure_layout();
    prepare(text, size, weight, tracking, family);
    let mut w = LAY.with_borrow(|lay| {
        let (lw, _) = lay.as_ref().unwrap().size();
        lw as f64 / SCALE - tracking
    });
    let lay = LAY.with_borrow(|lay| lay.as_ref().unwrap().clone());
    if max_w > 0.0 && w > max_w {
        // last-resort guard: ellipsize rather than bleed out of the rect
        lay.set_width(((max_w + tracking) * SCALE) as i32);
        lay.set_ellipsize(EllipsizeMode::End);
        w = w.min(max_w);
    }
    let ox = match align {
        'r' => x - w,
        'c' => x - w * 0.5,
        _ => x,
    } - tracking * 0.5;
    let _ = cr.save();
    if alpha >= 0.999 {
        cr.set_source_rgb(rgb.0, rgb.1, rgb.2);
    } else {
        cr.set_source_rgba(rgb.0, rgb.1, rgb.2, alpha);
    }
    cr.move_to(ox, baseline - lay.baseline() as f64 / SCALE);
    pangocairo::functions::show_layout(cr, &lay);
    let _ = cr.restore();
    w
}

// --------------------------------------------------------------------------
// primitives
// --------------------------------------------------------------------------

pub fn hairline(cr: &Context, x0: f64, x1: f64, y: f64, rgb: RGB, alpha: f64) {
    if x1 - x0 < 1.0 {
        return;
    }
    let _ = cr.save();
    cr.set_line_width(1.0);
    if alpha >= 0.999 {
        cr.set_source_rgb(rgb.0, rgb.1, rgb.2);
    } else {
        cr.set_source_rgba(rgb.0, rgb.1, rgb.2, alpha);
    }
    let yy = y.floor() + 0.5;
    cr.move_to(x0, yy);
    cr.line_to(x1, yy);
    let _ = cr.stroke();
    let _ = cr.restore();
}

/// 2px understated bar: RULE track, CYAN (or AMBER) fill.
fn meter(cr: &Context, x: f64, y: f64, w: f64, level: f64, warn: bool) {
    if w < 4.0 {
        return;
    }
    let yy = y.floor();
    cr.set_source_rgb(RULE.0, RULE.1, RULE.2);
    cr.rectangle(x, yy, w, 2.0);
    let _ = cr.fill();
    let lv = level.clamp(0.0, 1.0);
    if lv <= 0.004 {
        return;
    }
    let fill = if warn { AMBER } else { CYAN };
    cr.set_source_rgba(fill.0, fill.1, fill.2, 0.88);
    cr.rectangle(x, yy, (w * lv).max(1.5), 2.0);
    let _ = cr.fill();
}

fn dot(cr: &Context, x: f64, y: f64, r: f64) {
    let _ = cr.save();
    cr.set_source_rgba(LIME.0, LIME.1, LIME.2, 0.9);
    cr.arc(x, y, r, 0.0, std::f64::consts::TAU);
    let _ = cr.fill();
    let _ = cr.restore();
}

// --------------------------------------------------------------------------
// background
// --------------------------------------------------------------------------

/// Flat ABYSS field with a barely-there vignette toward FRAME.
pub fn draw_background(cr: &Context, w: f64, h: f64) {
    let _ = cr.save();
    cr.set_source_rgb(ABYSS.0, ABYSS.1, ABYSS.2);
    cr.rectangle(0.0, 0.0, w, h);
    let _ = cr.fill();

    if h > 8.0 {
        let key = h as i32;
        let grad_present = BG_CACHE.with_borrow(|c| c.contains_key(&key));
        if !grad_present {
            let grad = cairo::LinearGradient::new(0.0, 0.0, 0.0, h);
            // Quadratic falloff on both edges: the alpha derivative reaches
            // zero at the junction, so there is no visible crease.
            for i in 0..=BG_STEPS {
                let f = i as f64 / BG_STEPS as f64;
                let a = (1.0 - f) * (1.0 - f);
                grad.add_color_stop_rgba(f * BG_TOP_SPAN, FRAME.0, FRAME.1, FRAME.2, 0.50 * a);
                grad.add_color_stop_rgba(
                    1.0 - f * BG_BOT_SPAN,
                    FRAME.0,
                    FRAME.1,
                    FRAME.2,
                    0.38 * a,
                );
            }
            BG_CACHE.with_borrow_mut(|c| {
                if c.len() > 12 {
                    c.clear();
                }
                c.insert(key, grad);
            });
        }
        BG_CACHE.with_borrow(|c| {
            if let Some(grad) = c.get(&key) {
                let _ = cr.set_source(grad);
                cr.rectangle(0.0, 0.0, w, h);
                let _ = cr.fill();
            }
        });
    }
    let _ = cr.restore();
}

// --------------------------------------------------------------------------
// header
// --------------------------------------------------------------------------

fn header(cr: &Context, l: &Layout) {
    let r = l.header;
    if !r.valid() {
        return;
    }
    let t = l.typ;

    let rule_y = r.bottom().floor() - 0.5;
    let avail_h = rule_y - r.y - 3.0;
    let avail_w = r.w;
    if avail_h < MIN_PT * 0.7 || avail_w < 8.0 {
        return;
    }

    // --- specimen: the anchor. Fitted horizontally, then vertically. --------
    let mut s_spec = fit(SPECIMEN, t.specimen, W_MEDIUM, 0.0, avail_w);
    if s_spec <= 0.0 {
        return;
    }
    let mut cap_spec = cap(s_spec, W_MEDIUM, "");
    if cap_spec > avail_h {
        s_spec = fit(
            SPECIMEN,
            (avail_h / CAP_RATIO * 2.0).floor() / 2.0,
            W_MEDIUM,
            0.0,
            avail_w,
        );
        if s_spec <= 0.0 {
            return;
        }
        cap_spec = cap(s_spec, W_MEDIUM, "");
        if cap_spec > avail_h {
            return;
        }
    }
    let w_spec = text_w(SPECIMEN, s_spec, W_MEDIUM, 0.0, "");

    // --- title: dropped before the specimen is ever compromised ------------
    let mut s_title = fit(TITLE, t.title, W_NORMAL, t.tracking, avail_w);
    let mut cap_title = if s_title > 0.0 { cap(s_title, W_NORMAL, "") } else { 0.0 };
    let gap = (t.title * 0.62).max(4.0);
    if s_title > 0.0 && cap_title + gap + cap_spec > avail_h {
        s_title = 0.0;
        cap_title = 0.0;
    }

    let block_h = cap_spec + if s_title > 0.0 { cap_title + gap } else { 0.0 };
    let top = r.y + ((avail_h - block_h) * 0.45).max(0.0);

    let y_title = if s_title > 0.0 { top + cap_title } else { 0.0 };
    let y_spec = top + block_h;

    let mut w_title = 0.0;
    if s_title > 0.0 {
        w_title = show(
            cr, TITLE, s_title, W_NORMAL, t.tracking, r.x, y_title, INK_DIM, 1.0,
            'l', avail_w, "",
        );
    }

    show(
        cr, SPECIMEN, s_spec, W_MEDIUM, 0.0, r.x, y_spec, INK_BRIGHT, 1.0,
        'l', avail_w, "",
    );

    // --- subtitle: right-aligned on the specimen baseline, or dropped ------
    if l.show_subtitle {
        let s_sub = fit(
            SUBTITLE,
            t.subtitle,
            W_NORMAL,
            t.tracking,
            (avail_w - w_spec - t.specimen * 1.6).max(0.0),
        );
        if s_sub > 0.0 {
            show(
                cr, SUBTITLE, s_sub, W_NORMAL, t.tracking, r.right(), y_spec,
                INK_DIM, 1.0, 'r', -1.0, "",
            );
        }
    }

    // --- one small nominal dot, top right ----------------------------------
    let dot_r = (t.micro * 0.26).max(1.6);
    let dot_x = r.right() - dot_r;
    if s_title > 0.0 {
        if r.x + w_title + dot_r * 6.0 < dot_x {
            dot(cr, dot_x, y_title - cap_title * 0.42, dot_r);
        }
    } else if r.x + w_spec + dot_r * 6.0 < dot_x {
        dot(cr, dot_x, y_spec - cap_spec * 0.42, dot_r);
    }

    hairline(cr, r.x, r.right(), rule_y, RULE, 1.0);
}

// --------------------------------------------------------------------------
// metadata block (ARCHIVE)
// --------------------------------------------------------------------------

const META_LABELS: [&str; 4] = ["CLASS", "MORPH", "LOCUS", "STATE"];
const META_VALUES: [&str; 4] = ["SYNTHETIC", "QUADRILOBATE", "", ""];

fn meta_block(cr: &Context, l: &Layout) {
    // ARCHIVE: a quiet identity block at the top of the side column.
    let m = l.meta;
    if !m.valid() || m.w < 60.0 || m.h < 24.0 {
        return;
    }

    let t = l.typ;
    let s_lab = t.micro;
    let s_val = t.label;
    let cap_val = cap(s_val, W_NORMAL, "");
    let rows = META_LABELS.len();

    let usable = m.h - 4.0;
    if usable < rows as f64 * (cap_val + 3.0) {
        return;
    }
    let row_h = (usable / rows as f64).min(cap_val * 2.5);

    let mut lab_w = 0.0f64;
    for lab in META_LABELS {
        let w = text_w(lab, s_lab, W_NORMAL, t.tracking, "");
        if w > lab_w {
            lab_w = w;
        }
    }
    let mut val_x = m.x + lab_w + (t.label * 1.2).max(10.0);
    let mut val_max = m.right() - val_x;
    if val_max < 30.0 {
        val_x = m.x + lab_w + 8.0;
        val_max = m.right() - val_x;
        if val_max < 16.0 {
            return;
        }
    }

    let host = hostname();
    let y = m.y + 2.0;
    let mut last = y + cap_val;
    for i in 0..rows {
        let base = y + i as f64 * row_h + cap_val;
        if base > m.bottom() - 2.0 {
            break;
        }
        let mut val = META_VALUES[i].to_string();
        if val.is_empty() {
            val = match i {
                2 => host.clone(),
                _ => l.state.name().to_string(),
            };
        }
        show(
            cr, META_LABELS[i], s_lab, W_NORMAL, t.tracking, m.x, base, INK_DIM,
            1.0, 'l', lab_w + t.tracking, "",
        );
        show(
            cr, &val, s_val, W_NORMAL, 0.0, m.right(), base, INK, 1.0, 'r',
            val_max, "",
        );
        last = base;
    }

    // Close the block with one hairline.
    hairline(
        cr,
        m.x,
        m.right(),
        (m.bottom() - 1.0).min(last + row_h * 0.60),
        RULE,
        1.0,
    );
}

// --------------------------------------------------------------------------
// readouts
// --------------------------------------------------------------------------

/// Preference-ordered `(text, split)` pairs for one readout.
fn value_candidates(idx: usize, tel: &Telemetry, fps: f64) -> Vec<(String, usize)> {
    if idx == 0 {
        let s = format!("{}%", (tel.cpu_pct + 0.5) as i64);
        return vec![(s.clone(), s.len() - 1)];
    }
    if idx == 1 {
        let Some(c_val) = tel.temp_c else {
            return vec![("--".to_string(), 0)];
        };
        let c = (c_val + 0.5) as i64;
        let a = format!("{c}\u{00b0}C");
        let b = format!("{c}\u{00b0}");
        return vec![(a.clone(), a.len() - 2), (b.clone(), b.len() - 1)];
    }
    if idx == 2 {
        let used = tel.mem_used_gb;
        let total = tel.mem_total_gb;
        let pct = format!("{}%", (tel.memory_pressure * 100.0 + 0.5) as i64);
        if total <= 0.0 {
            return vec![(pct.clone(), pct.len() - 1)];
        }
        let a = format!("{used:.1}/{total:.1}G");
        let b = format!("{used:.1}G");
        let split = a.find('/').unwrap_or(a.len());
        return vec![(a.clone(), split), (b.clone(), b.len() - 1), (pct.clone(), pct.len() - 1)];
    }
    let f = if fps.is_nan() { 0.0 } else { fps };
    let f = f.max(0.0);
    let s = format!("{}", (f + 0.5) as i64);
    vec![(s.clone(), s.len())]
}

fn levels(idx: usize, tel: &Telemetry, fps: f64) -> f64 {
    match idx {
        0 => tel.cpu_load,
        1 => tel.temperature,
        2 => tel.memory_pressure,
        _ => {
            let f = if fps.is_nan() { 0.0 } else { fps };
            f / FPS_FULL
        }
    }
}

/// Choose the richest value string that fits, shrinking only as a last resort.
fn pick_value(idx: usize, tel: &Telemetry, fps: f64, size: f64, max_w: f64) -> (String, usize, f64) {
    let cands = value_candidates(idx, tel, fps);
    for (s, split) in &cands {
        if text_w(s, size, W_NORMAL, 0.0, "") <= max_w {
            return (s.clone(), *split, size);
        }
    }
    let (last, split) = cands.last().cloned().unwrap_or_default();
    let fitted = fit(&last, size, W_NORMAL, 0.0, max_w);
    if fitted > 0.0 {
        (last, split, fitted)
    } else {
        (String::new(), 0, 0.0)
    }
}

/// Value in two tones: magnitude bright, unit one step quieter.
#[allow(clippy::too_many_arguments)]
fn show_value(
    cr: &Context,
    text: &str,
    split: usize,
    size: f64,
    x: f64,
    baseline: f64,
    warn: bool,
    align: char,
    max_w: f64,
) {
    if text.is_empty() {
        return;
    }
    let head: String = text.chars().take(split).collect();
    let tail: String = text.chars().skip(split).collect();
    let col = if warn { AMBER } else { INK_BRIGHT };
    if head.is_empty() {
        show(
            cr, &tail, size, W_NORMAL, 0.0, x, baseline,
            if warn { AMBER } else { INK_DIM }, 1.0, align, max_w, "",
        );
        return;
    }
    let left = if align == 'r' {
        let mut total = text_w(text, size, W_NORMAL, 0.0, "");
        if total > max_w && max_w > 0.0 {
            total = max_w;
        }
        x - total
    } else {
        x
    };
    let hw = show(cr, &head, size, W_NORMAL, 0.0, left, baseline, col, 1.0, 'l', max_w, "");
    if !tail.is_empty() {
        if warn {
            show(cr, &tail, size, W_NORMAL, 0.0, left + hw, baseline, AMBER, 0.55, 'l', max_w - hw, "");
        } else {
            show(cr, &tail, size, W_NORMAL, 0.0, left + hw, baseline, INK, 1.0, 'l', max_w - hw, "");
        }
    }
}

/// Stacked cell: label / value / meter, vertically centred in (y, h).
#[allow(clippy::too_many_arguments)]
fn readout_cell(
    cr: &Context,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    label: &str,
    value: &str,
    split: usize,
    vsize: f64,
    lsize: f64,
    tracking: f64,
    level: f64,
    warn: bool,
    meter_w: f64,
) {
    if w < 8.0 || h < 6.0 {
        return;
    }
    let cap_l = if lsize > 0.0 { cap(lsize, W_NORMAL, "") } else { 0.0 };
    let mut cap_v = if vsize > 0.0 { cap(vsize, W_NORMAL, "") } else { 0.0 };

    let mut g1 = (lsize * 0.72).max(3.0);
    let mut g2 = (vsize * 0.48).max(4.0);
    let mut show_label = lsize > 0.0;
    let mut show_meter = meter_w >= 6.0;
    let mut vsize = vsize;

    let need = |cap_v: f64, g1: f64, g2: f64, show_label: bool, show_meter: bool, cap_l: f64| -> f64 {
        let mut n = cap_v;
        if show_label {
            n += cap_l + g1;
        }
        if show_meter {
            n += g2 + 2.0;
        }
        n
    };

    if need(cap_v, g1, g2, show_label, show_meter, cap_l) > h {
        // 1. tighten the gaps
        g1 = 2.0;
        g2 = 3.0;
    }
    if need(cap_v, g1, g2, show_label, show_meter, cap_l) > h && show_meter {
        // 2. lose the meter
        show_meter = false;
    }
    if need(cap_v, g1, g2, show_label, show_meter, cap_l) > h && show_label {
        // 3. a modest trim of the value is worth more than losing the label,
        //    but only a modest one -- never shrink type into mush.
        let target = h - cap_l - g1;
        if target > MIN_PT * CAP_RATIO {
            let ns = (target / CAP_RATIO * 2.0).floor() / 2.0;
            let ns = ns.max(MIN_PT).min(vsize);
            if ns >= vsize * 0.7 && cap(ns, W_NORMAL, "") + cap_l + g1 <= h {
                vsize = ns;
                cap_v = cap(ns, W_NORMAL, "");
            }
        }
    }
    if need(cap_v, g1, g2, show_label, show_meter, cap_l) > h && show_label {
        // 4. lose the label
        show_label = false;
    }
    if need(cap_v, g1, g2, show_label, show_meter, cap_l) > h {
        // 5. only now shrink the value hard
        vsize = (h / CAP_RATIO * 2.0).floor() / 2.0;
        if vsize < MIN_PT {
            return;
        }
        cap_v = cap(vsize, W_NORMAL, "");
        if cap_v > h {
            return;
        }
    }

    let mut top = y + ((h - need(cap_v, g1, g2, show_label, show_meter, cap_l)) * 0.5).max(0.0);
    if show_label {
        show(cr, label, lsize, W_NORMAL, tracking, x, top + cap_l, INK_DIM, 1.0, 'l', w, "");
        top += cap_l + g1;
    }
    let hot = warn && level > WARN_LEVEL;
    show_value(cr, value, split, vsize, x, top + cap_v, hot, 'l', w);
    top += cap_v;
    if show_meter {
        meter(cr, x, top + g2, meter_w, level, hot);
    }
}

fn readouts_row(cr: &Context, l: &Layout, tel: &Telemetry, fps: f64) {
    let r = l.readout;
    let t = l.typ;
    let cell = r.w / 4.0;
    let gutter = (cell * 0.11).clamp(4.0, 18.0);
    let inner = cell - gutter;
    if inner < 10.0 {
        return;
    }
    let meter_cap = inner.min(t.value * 9.0);

    let mut lsize = t.label;
    if text_w("TEMP", lsize, W_NORMAL, t.tracking, "") > inner {
        lsize = fit("TEMP", lsize, W_NORMAL, t.tracking, inner);
    }

    for i in 0..4 {
        let x = r.x + i as f64 * cell;
        let (value, split, vsize) = pick_value(i, tel, fps, t.value, inner);
        if value.is_empty() {
            continue;
        }
        readout_cell(
            cr,
            x,
            r.y,
            inner,
            r.h,
            LABELS[i],
            &value,
            split,
            vsize,
            lsize,
            t.tracking,
            levels(i, tel, fps),
            WARNS[i],
            meter_cap,
        );
    }
}

/// ARCHIVE side column: label left / value right, meter spanning beneath.
fn readouts_column(cr: &Context, l: &Layout, tel: &Telemetry, fps: f64) {
    let r = l.readout;
    let t = l.typ;
    if r.w < 60.0 {
        return;
    }

    let cap_v = cap(t.value, W_NORMAL, "");
    let cap_l = cap(t.label, W_NORMAL, "");
    let gap_m = (t.value * 0.46).max(8.0);
    let item_h = cap_v + gap_m + 2.0;
    // Keep the ladder off the rect edges.
    let mut inset = (r.h * 0.07).min(cap_v * 0.85);
    if r.h - 2.0 * inset < item_h {
        inset = 0.0;
    }
    let ry = r.y + inset;
    let rh = r.h - 2.0 * inset;
    // Spread the four gauges down the column, but cap the stride so a very
    // tall window does not turn the panel into four lost specks.
    let mut slot = ((rh - item_h) / 3.0).min(item_h * 4.6);
    if slot < item_h {
        slot = ((rh - item_h) / 3.0).max(item_h);
    }
    let mut block = slot * 3.0 + item_h;
    if block > rh {
        slot = ((rh - item_h) / 3.0).max(0.0);
        block = slot * 3.0 + item_h;
    }
    let top = ry + ((rh - block) * 0.5).max(0.0);

    let mut lab_w = 0.0f64;
    for lab in LABELS {
        let w = text_w(lab, t.label, W_NORMAL, t.tracking, "");
        if w > lab_w {
            lab_w = w;
        }
    }
    let val_max = (r.w - lab_w - t.label * 1.4).max(20.0);

    for i in 0..4 {
        let y = top + i as f64 * slot;
        if y + item_h > r.bottom() + 0.5 {
            break;
        }
        let (value, split, vsize) = pick_value(i, tel, fps, t.value, val_max);
        if value.is_empty() {
            continue;
        }
        let base = y + cap_v;
        let level = levels(i, tel, fps);
        let warn = WARNS[i] && level > WARN_LEVEL;
        if cap_l <= cap_v {
            show(
                cr, LABELS[i], t.label, W_NORMAL, t.tracking, r.x, base, INK_DIM,
                1.0, 'l', lab_w + t.tracking, "",
            );
        }
        show_value(cr, &value, split, vsize, r.right(), base, warn, 'r', val_max);
        meter(cr, r.x, base + gap_m, r.w, level, warn);
    }
}

// --------------------------------------------------------------------------
// mode word
// --------------------------------------------------------------------------

fn mode_word(cr: &Context, l: &Layout) {
    let t = l.typ;
    let word = l.state.name();
    let mut band_top = if l.readout.valid() { l.readout.bottom() } else { l.height - l.pad };
    let mut band_h = l.height - band_top;
    if band_h < 4.0 {
        band_top = l.height - l.pad;
        band_h = l.pad;
    }
    let cap_v = cap(t.micro, W_NORMAL, "");
    let mut size = t.micro;
    if cap_v > band_h - 1.0 {
        size = fit(
            word,
            ((band_h - 1.0) / CAP_RATIO * 2.0).floor() / 2.0,
            W_NORMAL,
            t.tracking,
            l.width - 2.0 * l.pad,
        );
        if size <= 0.0 {
            return;
        }
    }
    size = fit(word, size, W_NORMAL, t.tracking, l.width - 2.0 * l.pad);
    if size <= 0.0 {
        return;
    }
    let mut base = band_top + (band_h - cap(size, W_NORMAL, "")) * 0.5 + cap(size, W_NORMAL, "");
    base = base.min(l.height - 1.0);
    show(
        cr, word, size, W_NORMAL, t.tracking, l.width - l.pad, base, INK_DIM,
        0.85, 'r', -1.0, "",
    );
}

// --------------------------------------------------------------------------
// entry point
// --------------------------------------------------------------------------

pub fn draw_chrome(cr: &Context, layout: &Layout, tel: &Telemetry, fps: f64, _frame_ms: f64) {
    let _ = cr.save();
    cr.set_antialias(Antialias::Default);
    header(cr, layout);
    if layout.show_meta {
        meta_block(cr, layout);
    }
    if layout.readout.valid() {
        if layout.readout_vertical {
            readouts_column(cr, layout, tel, fps);
        } else {
            readouts_row(cr, layout, tel, fps);
        }
    }
    mode_word(cr, layout);
    let _ = cr.restore();
}
