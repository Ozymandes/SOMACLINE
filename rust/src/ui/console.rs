//! The physical console: six generated hardware modules, live content in them.
//! Complete port of `ui/console.py`.
//!
//! COMPOSITION (identical to Python):
//!   layer_under   background, 01 shell, glass, graticule      (static)
//!   (host)        the organism, clipped to the glass           (per frame)
//!   layer_over    03 bezel, 02 header, 04 rack, 05 selector
//!                 plate, 06 rail and all static type           (static)
//!   regions()     header clock, rack readouts, selector keys   (on change)
//!
//! Static layers and regions are cached on their complete inputs, at the
//! display's device scale. Pixel identity is carried by a monotonic
//! [`Layer::id`]: stable while content is unchanged, fresh whenever pixels
//! are re-rendered, so the host's texture cache can never serve stale pixels
//! (Python keys the same job on `id(surface)`).
//!
//! LIGHTING: exactly as in Python, the light pass lives INSIDE the layers —
//! constant emitters (glass spill, LIVE lamp) are painted into `layer_over`;
//! each region paints its own emitters clipped to the region rect. The host
//! needs no extra call: `layer_over` + `regions` are self-contained.

use std::cell::RefCell;
use std::rc::Rc;

use cairo::{Context, ImageSurface, LinearGradient};
use glib::translate::IntoGlib;
use pango::Weight;

use crate::layout::{Layout, LayoutState, Rect, TypeScale};
use crate::lighting::{Clip, LightField, AMBER as L_AMBER, CHARTREUSE as L_CHART,
                      CYAN as L_CYAN};
use crate::signals::Telemetry;
use crate::skin::catalog;
use crate::skin::hidpi;
use crate::skin::modules as module_defs;
use crate::skin::modules::Placed;
use crate::skin::surface::{draw_nine, draw_sprite, draw_sprite_fit};
use crate::species::{CATALOGUE, COUNT, Species};
use crate::telemetry::history::History;
use crate::theme::{rgba, INK, INK_BRIGHT, INK_DIM, INK_TECH, LIME, RULE};
use crate::ui::chrome;
use crate::ui::segment as seg;
use crate::ui::selector as sel;

type RGB = (f64, f64, f64);

const TAU: f64 = std::f64::consts::TAU;

/// Scope graticule ink, measured off the reference field: a cool blue-grey
/// that reads clearly against the glass without competing with the specimen.
const SCOPE: RGB = (0.36, 0.52, 0.62);

/// chrome refuses to draw below this; ask for it or do not draw at all.
const MIN_TEXT: f64 = 7.0;

/// WORLD_RADIUS / WORLD_SIZE. Kept local so ui/ does not import the world.
const SCOPE_R: f64 = 0.46;

/// The bezel's inner bevel overlaps the glass rect, so annotations placed
/// flush to the glass edge are cut by metal. Everything drawn on the field is
/// inset by this much first.
const FIELD_INSET_X: f64 = 0.055;
const FIELD_INSET_Y: f64 = 0.075;

/// Shell frame scale relative to the chassis: the reference's perimeter is
/// ~15-25 px on a 1368 px enclosure, and the shell sprite's frame is 97 src px
/// wide, so its fixed parts are drawn at 0.17 of the chassis scale.
const SHELL_K: f64 = 0.17;

/// Bay padding as a share of the bay box, with pixel floors.
const BAY_PAD_X: f64 = 0.050;
const BAY_PAD_Y: f64 = 0.115;
const BAY_PAD_X_MIN: f64 = 3.0;
const BAY_PAD_Y_MIN: f64 = 2.0;

// Cache limits, exactly as Python's OrderedDict bounds.
const TXT_LIMIT: usize = 700;
const RING_LIMIT: usize = 8;
const TRACE_LIMIT: usize = 16;
const LAYER_LIMIT: usize = 2;
const REGION_LIMIT: usize = 8;

// ==========================================================================
// pinned public API (the host codes against exactly this)
// ==========================================================================

/// Which control was hit by hit_controls().
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CtlKind {
    Key,
    Cycle,
    Mode,
}

/// A cached raster layer. `id` is stable for the lifetime of the surface;
/// a content change produces a NEW id and surface (pixels written once).
#[derive(Clone)]
pub struct Layer {
    pub id: u64,
    pub surface: ImageSurface,
}

/// One live region: a cached surface plus its logical-pixel placement.
#[derive(Clone)]
pub struct Region {
    pub layer: Layer,
    pub rect: Rect,
}

/// Console presentation state, mutated by the host and read by the renderer.
pub struct ConsoleModel {
    pub species: &'static Species,
    pub active: usize,
    pub pressed: Option<usize>,
    pub focus: Option<usize>,
    /// "inactive" | "armed" | "active" | "error"
    pub mode_state: String,
    /// "neutral" | "prev" | "next": press state of the cycle rocker
    pub cycle_state: String,
    /// Specimen indices whose keys render disabled.
    pub disabled: Vec<usize>,
    pub switches: usize,
    /// 60 s of telemetry at the telemetry cadence, owned by the host. The
    /// graphs read it; nothing in the draw path writes to it.
    pub history: Rc<RefCell<History>>,
    // Live observation-field values. Set by the host each frame; every one of
    // these is rendered in code, never baked into the glass.
    pub phase: f64,    // radians
    pub rotation: f64, // rpm
    pub coords: (f64, f64, f64),
    pub behavior: String,
    pub magnification: f64,
    pub field_mm: f64,
    pub aperture: String,
    pub flux: f64,
    pub surge: f64,
    /// Diagnostics overlay (F1).
    pub diag: bool,
}

impl ConsoleModel {
    pub fn new(
        species: &'static Species,
        active: usize,
        history: Rc<RefCell<History>>,
        diag: bool,
    ) -> ConsoleModel {
        ConsoleModel {
            species,
            active,
            pressed: None,
            focus: None,
            mode_state: "armed".to_string(),
            cycle_state: "neutral".to_string(),
            disabled: Vec::new(),
            switches: 0,
            history,
            phase: 0.0,
            rotation: 0.0,
            coords: (0.0, 0.0, 0.0),
            behavior: "STABLE".to_string(),
            magnification: 4.0,
            field_mm: 2.50,
            aperture: "f/1.8".to_string(),
            flux: 0.0,
            surge: 0.0,
            diag,
        }
    }
}

// ==========================================================================
// pure geometry - shared by draw, hit test and QA
// ==========================================================================

/// (specimen bank, mode key rect). Both may be invalid/empty.
///
/// One pure function, three consumers: the draw, the hit test and the QA
/// gate. They cannot disagree about where a key is, because there is only one
/// answer to ask for.
pub fn control_geometry(l: &Layout) -> (sel::BankGeometry, Rect) {
    if !l.show_controls || !l.controls.valid() {
        return (sel::layout(Rect::NONE, COUNT), Rect::NONE);
    }
    let p = module_defs::SELECTOR.place(l.controls, None);
    let geo = sel::from_module(&p, COUNT);
    (geo, geo.aux_r)
}

/// The two-way cycle rocker, seated in the trough's left end.
pub fn cycle_rect(geo: &sel::BankGeometry, _mode: Rect) -> Rect {
    if geo.valid() {
        geo.aux_l
    } else {
        Rect::NONE
    }
}

/// The glass inside the stage frame: where the organism may draw.
///
/// The viewport is built from THIS, not from the raw stage, so the specimen
/// sits behind the frame's inner lip instead of under its metal.
pub fn stage_content(l: &Layout) -> Rect {
    let s = l.stage;
    if !s.valid() {
        return s;
    }
    module_defs::OBSERVATION.place(s, None).bay("aperture")
}

/// `('key', i) | ('mode', 0) | ('cycle', d) | None` — what the pointer is over.
///
/// Tests the SAME rectangles that were drawn: `control_geometry` is the only
/// source of key positions in the program. The cycle rocker reports direction
/// as 0 (back / left half) or 1 (forward / right half); the host maps those
/// onto -1 / +1.
pub fn hit_controls(l: &Layout, px: f64, py: f64) -> Option<(CtlKind, usize)> {
    let (geo, mode) = control_geometry(l);
    if let Some(i) = sel::hit(&geo, px, py) {
        return Some((CtlKind::Key, i));
    }
    if mode.valid()
        && mode.x <= px
        && px <= mode.right()
        && mode.y <= py
        && py <= mode.bottom()
    {
        return Some((CtlKind::Mode, 0));
    }
    let cyc = cycle_rect(&geo, mode);
    if cyc.valid()
        && cyc.x <= px
        && px <= cyc.right()
        && cyc.y <= py
        && py <= cyc.bottom()
    {
        return Some((CtlKind::Cycle, if px < cyc.cx() { 0 } else { 1 }));
    }
    None
}

/// Header and its status rail are ONE physical fascia, so they are one rect.
///
/// The generated header asset is a single panel carrying both rows of bays.
pub fn header_panel(l: &Layout) -> Rect {
    let h = l.header;
    if !h.valid() {
        return h;
    }
    if l.show_status && l.status.valid() {
        return Rect::new(h.x, h.y, h.w, l.status.bottom() - h.y);
    }
    h
}

/// The glass, inset clear of the bezel's inner lip.
pub fn field_area(l: &Layout) -> Rect {
    let g = stage_content(l);
    if !g.valid() {
        return g;
    }
    g.inset(g.w * FIELD_INSET_X, Some(g.h * FIELD_INSET_Y))
}

/// The specimen's own coordinate frame, in widget pixels.
///
/// THE observation field's single source of truth. Its centre and design
/// radius are derived exactly as `core.viewport.Viewport` derives them from
/// the same glass rect, so everything drawn against a Scope is registered to
/// the creature rather than to the widget it happens to sit in.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Scope {
    pub cx: f64,
    pub cy: f64,
    /// WORLD_RADIUS in pixels
    pub rad: f64,
    /// how far the drawn crosshair reaches
    pub axis_x0: f64,
    pub axis_x1: f64,
    pub axis_y0: f64,
    pub axis_y1: f64,
}

impl Scope {
    const ZERO: Scope = Scope {
        cx: 0.0,
        cy: 0.0,
        rad: 0.0,
        axis_x0: 0.0,
        axis_x1: 0.0,
        axis_y0: 0.0,
        axis_y1: 0.0,
    };
    #[allow(dead_code)]
    pub fn left(&self) -> f64 {
        self.cx - self.rad
    }
    #[allow(dead_code)]
    pub fn right(&self) -> f64 {
        self.cx + self.rad
    }
    #[allow(dead_code)]
    pub fn top(&self) -> f64 {
        self.cy - self.rad
    }
    #[allow(dead_code)]
    pub fn bottom(&self) -> f64 {
        self.cy + self.rad
    }
}

/// Build the Scope for this layout. Pure; safe to call every frame.
pub fn scope_of(l: &Layout) -> Scope {
    let g = stage_content(l);
    if !g.valid() {
        return Scope::ZERO;
    }
    let rad = g.w.min(g.h) * SCOPE_R;
    let f = field_area(l);
    Scope {
        cx: g.cx(),
        cy: g.cy(),
        rad,
        axis_x0: f.x.max(g.cx() - g.w * 0.47),
        axis_x1: f.right().min(g.cx() + g.w * 0.47),
        axis_y0: f.y.max(g.cy() - g.h * 0.47),
        axis_y1: f.bottom().min(g.cy() + g.h * 0.47),
    }
}

/// True when the full six-module machine is drawn (INSTRUMENT/ARCHIVE).
pub fn six_module(l: &Layout) -> bool {
    l.state != LayoutState::Compact && l.readout_vertical
}

pub fn shell_k(l: &Layout) -> f64 {
    let c = l.chassis;
    if l.state != LayoutState::Compact {
        SHELL_K * (c.w / 1368.0).min(c.h / 1028.0) * (1600.0 / 1368.0)
    } else {
        (c.w.min(c.h) * 0.030 / 97.0).max(0.10)
    }
}

// ==========================================================================
// measuring-only context (Python's _NULL_CR)
// ==========================================================================

thread_local! {
    static NULL_CTXT: RefCell<Option<(ImageSurface, Context)>> =
        const { RefCell::new(None) };
}

/// A throwaway context for geometry helpers that only measure. One 1x1
/// surface for the life of the process, and a single implementation of each
/// layout rule instead of a measuring copy that can silently drift.
fn null_cr() -> Context {
    NULL_CTXT.with(|slot| {
        let mut slot = slot.borrow_mut();
        if slot.is_none() {
            let surf =
                ImageSurface::create(cairo::Format::A8, 1, 1).expect("null surface");
            let cr = Context::new(&surf).expect("null context");
            *slot = Some((surf, cr));
        }
        slot.as_ref().unwrap().1.clone()
    })
}

// ==========================================================================
// cache-key helpers: quantised floats hashed on bit patterns
// ==========================================================================

/// Python's `round()`: half-to-EVEN, not half-away-from-zero. The glyph-run
/// blit lands on `round(ox - pad)`, and `ox` is a centre minus half a measured
/// width, so exact .5 offsets are common; rounding them the other way puts a
/// centred label one whole pixel off the Python original.
#[inline]
fn py_round(v: f64) -> f64 {
    let f = v.floor();
    let d = v - f;
    if d > 0.5 {
        f + 1.0
    } else if d < 0.5 {
        f
    } else if (f as i64) % 2 == 0 {
        f
    } else {
        f + 1.0
    }
}

#[inline]
fn bits(v: f64) -> u64 {
    // Normalise -0.0 so a negated zero cannot churn a cache key.
    if v == 0.0 {
        0
    } else {
        v.to_bits()
    }
}

fn weight_bits(w: Weight) -> i32 {
    w.into_glib()
}

// ==========================================================================
// cached text: one rasterised glyph run per complete input set
// ==========================================================================

#[derive(Clone, PartialEq)]
struct TxtKey {
    text: String,
    size_r: i64,
    weight: i32,
    tracking_r: i64,
    align: u8,
    max_w_r: i64,
    rgb: [i64; 3],
    alpha_r: i64,
    ds: u64,
    family: &'static str,
}

thread_local! {
    static TXT: RefCell<Vec<(TxtKey, (ImageSurface, f64, f64, f64))>> =
        const { RefCell::new(Vec::new()) };
}

/// Draw one line of instrument text, via a cache of rendered glyph runs.
///
/// The console draws on the order of 140 strings a frame and almost all of
/// them - TARGET, PROCESSOR, MODE:, the axis ticks, the footer keys - are
/// byte-identical from frame to frame. Shaping and rasterising them every
/// time was the single largest cost in the draw.
///
/// Each distinct (text, size, weight, tracking, colour, alpha, align, max_w,
/// family, device scale) is rasterised once into a small surface and then
/// blitted. The key is the complete set of inputs, so this cannot change what
/// is drawn; a miss costs one ordinary render. Positions are rounded to whole
/// pixels, which also stops microcopy shimmering as values change around it.
/// Returns the width actually drawn.
fn show(
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
    if text.is_empty() || size < 1.0 {
        return 0.0;
    }
    let key = TxtKey {
        text: text.to_string(),
        size_r: (size * 100.0).round() as i64,
        weight: weight_bits(weight),
        tracking_r: (tracking * 1000.0).round() as i64,
        align: align as u8,
        max_w_r: (max_w * 10.0).round() as i64,
        rgb: [
            (rgb.0 * 1000.0).round() as i64,
            (rgb.1 * 1000.0).round() as i64,
            (rgb.2 * 1000.0).round() as i64,
        ],
        alpha_r: (alpha * 1000.0).round() as i64,
        ds: bits(hidpi::scale()),
        family,
    };
    TXT.with(|cache| {
        let mut cache = cache.borrow_mut();
        if let Some(pos) = cache.iter().position(|(k, _)| *k == key) {
            let ent = cache.remove(pos);
            cache.push(ent);
        } else {
            let mut w = chrome::text_w(text, size, weight, tracking, family);
            if max_w > 0.0 {
                w = w.min(max_w);
            }
            let pad = (size * 0.8).max(3.0);
            let base_in = size * 1.7;
            // Python truncates the cache-surface size to whole logical px
            // (`int(...)`) before hidpi allocates it; ceil()ing instead adds a
            // transparent column but also a different device-pixel grid.
            let sw = ((w + pad * 2.0) as i64).max(1) as f64;
            let sh = ((base_in + size * 1.1 + pad) as i64).max(1) as f64;
            let surf = hidpi::surface(sw, sh);
            let c2 = Context::new(&surf).expect("txt context");
            chrome::show(&c2, text, size, weight, tracking, pad, base_in, rgb,
                         alpha, 'l', max_w, family);
            surf.flush();
            cache.push((key, (surf, w, pad, base_in)));
            while cache.len() > TXT_LIMIT {
                cache.remove(0);
            }
        }
        let (surf, w, pad, base_in) = cache.last().unwrap().1.clone();
        let ox = match align {
            'r' => x - w,
            'c' => x - w * 0.5,
            _ => x,
        };
        cr.save().ok();
        let _ = cr.set_source_surface(
            &surf,
            py_round(ox - pad),
            py_round(baseline - base_in),
        );
        let _ = cr.paint();
        cr.restore().ok();
        w
    })
}

// ==========================================================================
// small drawing primitives
// ==========================================================================

/// Segmented level fill for the meter trough, as the reference uses.
fn bargraph(cr: &Context, r: Rect, level: f64, rgb: RGB, segments: i32) {
    if r.w < 4.0 || r.h < 2.0 {
        return;
    }
    let lv = level.clamp(0.0, 1.0);
    let segments = segments.max(1);
    let gap = (r.w / segments as f64 * 0.30).max(0.6);
    let sw = (r.w - gap * (segments - 1) as f64) / segments as f64;
    if sw <= 0.2 {
        let c = rgba(rgb, 0.85);
        cr.set_source_rgba(c.0, c.1, c.2, c.3);
        cr.rectangle(r.x, r.y, r.w * lv, r.h);
        let _ = cr.fill();
        return;
    }
    let lit = lv * segments as f64;
    for i in 0..segments {
        let frac = (lit - i as f64).clamp(0.0, 1.0);
        let a = if frac <= 0.02 { 0.10 } else { 0.30 + 0.62 * frac };
        let c = rgba(rgb, a);
        cr.set_source_rgba(c.0, c.1, c.2, c.3);
        cr.rectangle(r.x + i as f64 * (sw + gap), r.y, sw, r.h);
        let _ = cr.fill();
    }
}

#[derive(Clone, PartialEq)]
struct RingKey {
    rad: i64,
    alpha_r: i64,
    ds: u64,
}

thread_local! {
    static RING_CACHE: RefCell<Vec<(RingKey, ImageSurface)>> =
        const { RefCell::new(Vec::new()) };
}

/// Dotted radial rings, cached.
///
/// A dashed arc is one of the more expensive things Cairo can be asked for
/// and these are redrawn every frame at a size that changes only on resize,
/// so the ring set is rasterised once per field size and then blitted.
fn rings(cr: &Context, cx: f64, cy: f64, rad: f64, alpha: f64) {
    if rad < 8.0 {
        return;
    }
    let key = RingKey {
        rad: rad.round() as i64,
        alpha_r: (alpha * 100.0).round() as i64,
        ds: bits(hidpi::scale()),
    };
    RING_CACHE.with(|cache| {
        let mut cache = cache.borrow_mut();
        let side = (rad * 2.0) as i32 + 4;
        let pos = cache.iter().position(|(k, _)| *k == key);
        let surf = match pos {
            Some(pos) => {
                let ent = cache.remove(pos);
                cache.push(ent);
                cache.last().unwrap().1.clone()
            }
            None => {
                let surf = hidpi::surface(f64::from(side), f64::from(side));
                let c2 = Context::new(&surf).expect("ring context");
                c2.set_line_width(1.0);
                let mid = f64::from(side) * 0.5;
                for (k, ka) in
                    [(0.22, 0.36), (0.42, 0.36), (0.62, 0.36), (0.81, 0.36), (1.00, 0.55)]
                {
                    let c = rgba(SCOPE, ka * alpha);
                    c2.set_source_rgba(c.0, c.1, c.2, c.3);
                    c2.set_dash(&[1.6, 5.0], 0.0);
                    c2.arc(mid, mid, rad * k, 0.0, TAU);
                    let _ = c2.stroke();
                }
                surf.flush();
                cache.push((key, surf.clone()));
                while cache.len() > RING_LIMIT {
                    cache.remove(0);
                }
                surf
            }
        };
        cr.save().ok();
        let _ = cr.set_source_surface(
            &surf,
            (cx - f64::from(side) * 0.5).round(),
            (cy - f64::from(side) * 0.5).round(),
        );
        let _ = cr.paint();
        cr.restore().ok();
    });
}

/// Observation graticule, drawn CONCENTRIC WITH THE SPECIMEN.
///
/// The rings, axes, ticks and cardinal marks are built from the scope - the
/// same centre and design radius the viewport gives the organism - not from
/// whatever rectangle happened to be left over after the annotation gutters
/// were taken.
fn polar_grid(cr: &Context, sc: &Scope, r: Rect, alpha: f64, x_min: f64) {
    if r.w < 60.0 || r.h < 60.0 {
        return;
    }
    let (cx, cy, rad) = (sc.cx, sc.cy, sc.rad);
    cr.save().ok();
    cr.rectangle(r.x, r.y, r.w, r.h);
    let _ = cr.clip();
    cr.set_line_width(1.0);

    rings(cr, cx, cy, rad, alpha);

    // axes reach the field edges, as an instrument's crosshair does. The X
    // axis starts after the radius ruler's gutter: it must never run through
    // the ruler's own labels.
    let c = rgba(SCOPE, 0.62 * alpha);
    cr.set_source_rgba(c.0, c.1, c.2, c.3);
    cr.move_to(r.x.max(sc.axis_x0).max(x_min), cy.round() + 0.5);
    cr.line_to(r.right().min(sc.axis_x1), cy.round() + 0.5);
    cr.move_to(cx.round() + 0.5, r.y.max(sc.axis_y0));
    cr.line_to(cx.round() + 0.5, r.bottom().min(sc.axis_y1));
    let _ = cr.stroke();

    // regular ticks along both axes, pitched off the design radius
    let c = rgba(SCOPE, 0.80 * alpha);
    cr.set_source_rgba(c.0, c.1, c.2, c.3);
    let step = rad * 0.2;
    let n = ((sc.axis_x1 - cx).max(sc.axis_y1 - cy) / step) as i32;
    for i in 1..=n {
        let d = i as f64 * step;
        let tk = if i % 5 == 0 { 5.5 } else { 3.0 };
        for sx in [-1.0, 1.0] {
            let px = cx + sx * d;
            if r.x < px
                && px < r.right()
                && sc.axis_x0 <= px
                && px <= sc.axis_x1
                && px > x_min
            {
                cr.move_to(px.round() + 0.5, cy - tk);
                cr.line_to(px.round() + 0.5, cy + tk);
            }
            let py = cy + sx * d;
            if r.y < py && py < r.bottom() && sc.axis_y0 <= py && py <= sc.axis_y1 {
                cr.move_to(cx - tk, py.round() + 0.5);
                cr.line_to(cx + tk, py.round() + 0.5);
            }
        }
    }
    let _ = cr.stroke();

    // cardinal crosses on the design radius
    for k in 0..4 {
        let ang = TAU * 0.25 * k as f64;
        let px = cx + ang.cos() * rad;
        let py = cy + ang.sin() * rad;
        let kk = (rad * 0.028).max(3.0);
        cr.move_to(px - kk, py);
        cr.line_to(px + kk, py);
        cr.move_to(px, py - kk);
        cr.line_to(px, py + kk);
    }
    let _ = cr.stroke();
    cr.restore().ok();
}

/// X+ / X- / Y+ / Y-, pinned to the SCOPE's own axes at the field edge.
fn cardinals(cr: &Context, sc: &Scope, r: Rect, t: &TypeScale, gutter: f64, alpha: f64) {
    if r.w < 260.0 || r.h < 200.0 {
        return;
    }
    let sz = t.micro.clamp(MIN_TEXT, 10.0);
    let pad = sz * 0.9;
    let x_lo = (r.x + gutter).max(sc.axis_x0);
    let cap = chrome::cap(sz, chrome::W_NORMAL, "");
    let marks: [(&str, f64, char, f64); 4] = [
        ("Y+", sc.cx, 'c', r.y.max(sc.axis_y0) + cap * 1.15),
        ("Y-", sc.cx, 'c', r.bottom().min(sc.axis_y1) - cap * 0.35),
        ("X-", x_lo + pad, 'l', sc.cy - cap * 0.55),
        ("X+", r.right().min(sc.axis_x1) - pad, 'r', sc.cy - cap * 0.55),
    ];
    for (lab, ax, al, base) in marks {
        show(cr, lab, sz, chrome::W_NORMAL, t.tracking, ax, base, INK, 0.92 * alpha,
             al, -1.0, "");
    }
}

/// A thin machined inset strip, drawn procedurally. Returns its interior.
///
/// The reference machine's archive rail and status bays are shallow recesses
/// cut into the fascia, not applied plaques. Drawing them in code rather than
/// 9-slicing a plaque means they stay correct at any height: a raster bevel
/// cannot shrink below its own thickness.
fn rail(cr: &Context, r: Rect, depth: f64) -> Rect {
    if r.w < 8.0 || r.h < 6.0 {
        return r;
    }
    cr.save().ok();
    let g = LinearGradient::new(r.x, r.y, r.x, r.bottom());
    g.add_color_stop_rgba(0.0, 0.085, 0.095, 0.105, 1.0);
    g.add_color_stop_rgba(0.55, 0.055, 0.062, 0.070, 1.0);
    g.add_color_stop_rgba(1.0, 0.075, 0.083, 0.092, 1.0);
    cr.set_source(&g).ok();
    cr.rectangle(r.x, r.y, r.w, r.h);
    let _ = cr.fill();
    cr.set_line_width(1.0);
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.55 * depth);
    cr.move_to(r.x, r.y + 0.5);
    cr.line_to(r.right(), r.y + 0.5);
    let _ = cr.stroke();
    cr.set_source_rgba(0.62, 0.66, 0.68, 0.30 * depth);
    cr.move_to(r.x, r.bottom() - 0.5);
    cr.line_to(r.right(), r.bottom() - 0.5);
    let _ = cr.stroke();
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.35 * depth);
    cr.move_to(r.x + 0.5, r.y);
    cr.line_to(r.x + 0.5, r.bottom());
    cr.move_to(r.right() - 0.5, r.y);
    cr.line_to(r.right() - 0.5, r.bottom());
    let _ = cr.stroke();
    cr.restore().ok();
    let pad = (r.h * 0.10).clamp(2.0, 6.0);
    Rect::new(r.x + pad * 1.6, r.y + pad, r.w - pad * 3.2, r.h - pad * 2.0)
}

/// A machined separator between compartments: dark groove, bright lip.
fn divider(cr: &Context, x: f64, y0: f64, y1: f64, alpha: f64) {
    if y1 - y0 < 3.0 {
        return;
    }
    cr.save().ok();
    cr.set_line_width(1.0);
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.55 * alpha);
    cr.move_to(x.round() + 0.5, y0);
    cr.line_to(x.round() + 0.5, y1);
    let _ = cr.stroke();
    cr.set_source_rgba(0.55, 0.60, 0.62, 0.22 * alpha);
    cr.move_to(x.round() + 1.5, y0);
    cr.line_to(x.round() + 1.5, y1);
    let _ = cr.stroke();
    cr.restore().ok();
}

fn hairline(cr: &Context, x0: f64, x1: f64, y: f64, rgb: RGB, alpha: f64) {
    if x1 - x0 < 2.0 {
        return;
    }
    cr.save().ok();
    cr.set_line_width(1.0);
    let c = rgba(rgb, alpha);
    cr.set_source_rgba(c.0, c.1, c.2, c.3);
    cr.move_to(x0, y.round() + 0.5);
    cr.line_to(x1, y.round() + 0.5);
    let _ = cr.stroke();
    cr.restore().ok();
}

// ==========================================================================
// MODULE 01: the master shell
// ==========================================================================

/// MODULE 01: the master shell. The bottom layer of the machine.
///
/// It carries no screws of its own - every fastener on this machine belongs
/// to the child module it holds down - and nothing passes through it.
fn draw_chassis(cr: &Context, l: &Layout) {
    let c = l.chassis;
    if !l.show_chassis || !c.valid() {
        return;
    }
    if module_defs::SHELL.available() {
        module_defs::SHELL.draw(cr, c, Some(shell_k(l)), 1.0);
    } else {
        draw_nine(cr, &catalog::PLATE, c.x, c.y, c.w, c.h, 1.0);
    }
}

// ==========================================================================
// typography inside a hardware bay
// ==========================================================================

/// The writable interior of a bay: the recess minus its internal padding.
fn bay_inner(r: Rect, sx: f64, sy: f64) -> Rect {
    if !r.valid() {
        return r;
    }
    let mut px = (r.w * BAY_PAD_X).max(BAY_PAD_X_MIN) * sx;
    let mut py = (r.h * BAY_PAD_Y).max(BAY_PAD_Y_MIN) * sy;
    px = px.min(r.w * 0.30);
    py = py.min(r.h * 0.30);
    Rect::new(r.x + px, r.y + py, r.w - px * 2.0, r.h - py * 2.0)
}

/// Shorten `text` until it fits `max_w`, ending in a single ellipsis.
///
/// Measured, not estimated: every face has per-glyph advances and the
/// caller's letter-spacing on top, so guessing character counts clips.
fn elide(
    text: &str,
    size: f64,
    weight: Weight,
    tracking: f64,
    max_w: f64,
    family: &'static str,
) -> String {
    if max_w <= 0.0 || text.is_empty() {
        return String::new();
    }
    if chrome::text_w(text, size, weight, tracking, family) <= max_w {
        return text.to_string();
    }
    let chars: Vec<char> = text.chars().collect();
    let (mut lo, mut hi) = (0usize, chars.len());
    while lo < hi {
        let mid = (lo + hi + 1) / 2;
        let mut cand: String = chars[..mid].iter().collect();
        cand.push('\u{2026}');
        if chrome::text_w(&cand, size, weight, tracking, family) <= max_w {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }
    if lo == 0 {
        String::new()
    } else {
        let mut s: String = chars[..lo].iter().collect();
        s.push('\u{2026}');
        s
    }
}

/// The longest of `cands` (longest first) that fits `max_w` whole.
///
/// Labels on this machine are shown complete or in a deliberately shorter
/// form - never ellipsised mid-word. Empty string if none fits.
fn fit_first(
    cands: &[&str],
    size: f64,
    weight: Weight,
    tracking: f64,
    max_w: f64,
    family: &'static str,
) -> String {
    for c in cands {
        if !c.is_empty() && chrome::text_w(c, size, weight, tracking, family) <= max_w {
            return (*c).to_string();
        }
    }
    String::new()
}

/// One line of type inside a bay. Returns the width actually drawn.
///
/// `baseline` is an absolute y; when omitted the line is centred on the bay's
/// own vertical middle by cap height, which is what keeps a row of bays on
/// one optical baseline even when their boxes differ by a pixel or two.
#[allow(clippy::too_many_arguments)]
fn bay_line(
    cr: &Context,
    r: Rect,
    text: &str,
    size: f64,
    weight: Weight,
    tracking: f64,
    rgb: RGB,
    alpha: f64,
    align: char,
    baseline: Option<f64>,
    x: Option<f64>,
    min_size: f64,
    family: &'static str,
) -> f64 {
    if text.is_empty() || !r.valid() {
        return 0.0;
    }
    let mut sz = size.max(min_size);
    let avail = if let Some(x) = x {
        match align {
            'l' => r.right() - x,
            'r' => x - r.x,
            _ => r.w,
        }
    } else {
        r.w
    };
    if avail <= 1.0 {
        return 0.0;
    }
    // Shrink before ellipsising: a slightly smaller full label beats a clipped
    // one, but only down to the legibility floor.
    while sz > min_size && chrome::text_w(text, sz, weight, tracking, family) > avail {
        sz = (sz - 0.5).max(min_size);
    }
    let txt = elide(text, sz, weight, tracking, avail, family);
    if txt.is_empty() {
        return 0.0;
    }
    let base = baseline.unwrap_or_else(|| r.cy() + chrome::cap(sz, weight, family) * 0.5);
    let ax = if let Some(x) = x {
        x
    } else {
        match align {
            'r' => r.right(),
            'c' => r.cx(),
            _ => r.x,
        }
    };
    show(cr, &txt, sz, weight, tracking, ax, base, rgb, alpha, align, avail, family)
}

/// A KEY / VALUE pair sharing one bay line: key left, value right of it.
///
/// The key column is measured from the key actually present, so a long key
/// never collides with its value and a short one never strands it.
#[allow(clippy::too_many_arguments)]
fn bay_pair(
    cr: &Context,
    r: Rect,
    key: &str,
    value: &str,
    size: f64,
    t: &TypeScale,
    value_rgb: RGB,
    key_rgb: RGB,
    alpha: f64,
    baseline: Option<f64>,
    family: &'static str,
) {
    if !r.valid() {
        return;
    }
    let sz = size.max(MIN_TEXT);
    let base = baseline
        .unwrap_or_else(|| r.cy() + chrome::cap(sz, chrome::W_NORMAL, family) * 0.5);
    let kw = show(cr, key, sz, chrome::W_NORMAL, t.tracking, r.x, base, key_rgb,
                  0.98 * alpha, 'l', r.w * 0.62, family);
    let vx = r.x + kw + sz * 0.85;
    if r.right() - vx > sz {
        bay_line(cr, Rect::new(vx, r.y, r.right() - vx, r.h), value, sz,
                 chrome::W_NORMAL, t.tracking * 0.5, value_rgb, 0.96 * alpha, 'l',
                 Some(base), None, MIN_TEXT, family);
    }
}

/// A KEY above its VALUE, both inside one bay, on fixed baselines.
///
/// This is the archive-rail arrangement: the key is a fixed micro caption
/// pinned to the top of the recess, the value sits on the lower baseline.
#[allow(clippy::too_many_arguments)]
fn bay_stack(
    cr: &Context,
    r: Rect,
    key: &str,
    value: &str,
    k_size: f64,
    v_size: f64,
    t: &TypeScale,
    value_rgb: RGB,
    alpha: f64,
    family: &'static str,
) {
    if !r.valid() {
        return;
    }
    let ks = k_size.max(MIN_TEXT);
    let vs = v_size.max(MIN_TEXT);
    let kc = chrome::cap(ks, chrome::W_NORMAL, family);
    let vc = chrome::cap(vs, chrome::W_NORMAL, family);
    let stack = kc * 1.15 + vc * 1.45;
    if stack > r.h {
        // Not enough recess for two lines. The VALUE is what the bay exists
        // to show, so the caption goes rather than both being clipped.
        bay_line(cr, r, value, vs.min(r.h * 0.86), chrome::W_NORMAL,
                 t.tracking * 0.5, value_rgb, 0.96 * alpha, 'l', None, None,
                 MIN_TEXT, family);
        return;
    }
    let top = r.y + ((r.h - stack) * 0.5).max(0.0);
    bay_line(cr, r, key, ks, chrome::W_NORMAL, t.tracking, INK_DIM, 0.82 * alpha,
             'l', Some(top + kc), None, MIN_TEXT, family);
    bay_line(cr, r, value, vs, chrome::W_NORMAL, t.tracking * 0.5, value_rgb,
             0.96 * alpha, 'l', Some(top + kc * 1.15 + vc * 1.30), None, MIN_TEXT,
             family);
}

// ==========================================================================
// header
// ==========================================================================

/// The command fascia, with every line of type seated in a real recess.
///
/// The generated header asset is a manufactured panel: four information bays
/// over a three-bay status rail, a lamp boss and a small window cast into the
/// third bay. Nothing here invents a black rectangle - it asks the asset where
/// its recesses are and writes inside them.
fn draw_header(
    cr: &Context,
    l: &Layout,
    m: &ConsoleModel,
    light: &mut LightField,
    static_pass: bool,
    live_pass: bool,
) {
    let r = header_panel(l);
    if !r.valid() {
        return;
    }
    let t = &l.typ;
    let sp = m.species;

    if !module_defs::HEADER.available()
        || l.state == LayoutState::Compact
        || r.w < 420.0
        || r.h < 34.0
    {
        draw_header_compact(cr, l, r, m, light, static_pass, live_pass);
        return;
    }

    let p = if static_pass {
        module_defs::HEADER.draw(cr, r, None, 1.0)
    } else {
        module_defs::HEADER.place(r, None)
    };

    // --- bay 1: instrument name over specimen name ------------------------
    // The two hero lines are the console's display type: Astro, the face the
    // major titles are set in. Sizes stay honest by measuring Astro itself.
    let b = if static_pass { bay_inner(p.bay("title"), 1.0, 1.0) } else { Rect::NONE };
    if b.valid() {
        let two = b.h >= 26.0;
        let name_sz = t.specimen.min(b.h * if two { 0.46 } else { 0.86 });
        let title_sz = t.title.min(b.h * 0.30);
        let hero = |sz: f64| chrome::cap(sz, chrome::W_NORMAL, chrome::FAMILY_HERO);
        if two {
            let stack = hero(title_sz) * 1.20 + hero(name_sz) * 1.36;
            let top = b.y + ((b.h - stack) * 0.5).max(0.0);
            bay_line(cr, b, "ABYSSAL ORGANISM MONITOR", title_sz, chrome::W_NORMAL,
                     t.tracking, INK_TECH, 0.96, 'l', Some(top + hero(title_sz)),
                     None, MIN_TEXT, chrome::FAMILY_HERO);
            let base2 = top + hero(title_sz) * 1.20 + hero(name_sz) * 1.30;
            let lead = show(cr, "SPECIMEN", title_sz, chrome::W_NORMAL, t.tracking,
                            b.x, base2, INK_TECH, 0.80, 'l', -1.0,
                            chrome::FAMILY_LABEL);
            let nx = b.x + lead + title_sz * 1.1;
            bay_line(cr, Rect::new(nx, b.y, b.right() - nx, b.h), sp.name, name_sz,
                     chrome::W_NORMAL, t.tracking * 0.7, INK_BRIGHT, 1.0, 'l',
                     Some(base2), None, MIN_TEXT, chrome::FAMILY_HERO);
        } else {
            bay_line(cr, b, sp.name, name_sz, chrome::W_NORMAL, t.tracking * 0.7,
                     INK_BRIGHT, 1.0, 'l', None, None, MIN_TEXT,
                     chrome::FAMILY_HERO);
        }
    }

    // --- bay 2: vernacular name over the terminal designation -------------
    let b = if static_pass { bay_inner(p.bay("epithet"), 1.0, 1.0) } else { Rect::NONE };
    if b.valid() {
        let sub_sz = (t.subtitle * 1.30).min(b.h * 0.46);
        let micro_sz = (t.micro * 0.92).min(b.h * 0.30).max(MIN_TEXT);
        let hero = |sz: f64| chrome::cap(sz, chrome::W_NORMAL, chrome::FAMILY_HERO);
        let tech = |sz: f64| chrome::cap(sz, chrome::W_NORMAL, chrome::FAMILY_LABEL);
        let stack = hero(sub_sz) * 1.28 + tech(micro_sz) * 1.50;
        if stack > b.h {
            // One line or none: the terminal designation is the line to lose.
            bay_line(cr, b, sp.epithet, sub_sz.min(b.h * 0.88), chrome::W_NORMAL,
                     t.tracking * 1.5, INK_BRIGHT, 0.94, 'c', None, None,
                     MIN_TEXT, chrome::FAMILY_HERO);
        } else {
            let top = b.y + ((b.h - stack) * 0.5).max(0.0);
            let ew = bay_line(cr, b, sp.epithet, sub_sz, chrome::W_NORMAL,
                              t.tracking * 1.5, INK_BRIGHT, 0.94, 'c',
                              Some(top + hero(sub_sz)), None, MIN_TEXT,
                              chrome::FAMILY_HERO);
            // Rule marks either side of the name, as the reference sets it.
            // Sized FROM the drawn text, so they can never cross it.
            let ry = top + hero(sub_sz) * 0.5;
            let rl = (sub_sz * 1.6).min((b.w - ew) * 0.5 - sub_sz * 0.8);
            if rl > sub_sz * 0.6 {
                for sgn in [-1.0, 1.0] {
                    let x0 = b.cx() + sgn * (ew * 0.5 + sub_sz * 0.55);
                    hairline(cr, x0.min(x0 + sgn * rl), x0.max(x0 + sgn * rl), ry,
                             INK_BRIGHT, 0.70);
                }
            }
            let cap_txt = fit_first(
                &["BIOCOMPUTATIONAL OBSERVATION TERMINAL", "OBSERVATION TERMINAL"],
                micro_sz, chrome::W_NORMAL, t.tracking, b.w, chrome::FAMILY_LABEL);
            if !cap_txt.is_empty() {
                show(cr, &cap_txt, micro_sz, chrome::W_NORMAL, t.tracking, b.cx(),
                     top + hero(sub_sz) * 1.28 + tech(micro_sz) * 1.40, INK_TECH,
                     0.82, 'c', -1.0, chrome::FAMILY_LABEL);
            }
        }
    }

    // --- bay 3: the LIVE annunciator, in the boss the asset provides ------
    let lamp = if static_pass { p.bay("live_lamp") } else { Rect::NONE };
    let win = if static_pass { p.bay("live_window") } else { Rect::NONE };
    if lamp.valid() {
        // The boss is cast into the fascia, so the lamp is sized to the boss
        // and centred in it - never to the bay, which is wider than the boss.
        let d = lamp.h.min(lamp.w) * 0.94;
        draw_sprite_fit(cr, &catalog::lamp("small", "nominal"), lamp.cx(), lamp.cy(),
                        d, 1.0);
        light.add(lamp.cx(), lamp.cy(), d * 2.2, L_CHART, 0.18);
    }
    if win.valid() {
        let wi = bay_inner(win, 0.8, 0.5);
        bay_line(cr, wi, "LIVE", t.label.min(wi.h * 0.86), chrome::W_NORMAL,
                 t.tracking, LIME, 0.97, 'c', None, None, MIN_TEXT,
                 chrome::FAMILY_LABEL);
    }

    // --- bay 4: the running clock -----------------------------------------
    // The clock is the one genuinely per-frame readout in this fascia, so it
    // is the one thing here that is NOT cached.
    let b = if live_pass { bay_inner(p.bay("clock"), 1.0, 1.0) } else { Rect::NONE };
    if b.valid() {
        let clk = clock_string();
        let dh = (b.h * 0.86).min(b.w / seg::measure(&clk, 1.0, &seg::CYAN).max(1e-6));
        let cw = seg::measure(&clk, dh, &seg::CYAN);
        seg::draw(cr, &clk, b.cx() - cw * 0.5, b.cy() - dh * 0.5, dh, &seg::CYAN);
        light.glow(&b, L_CYAN, 0.055, 0.55);
    }

    if static_pass {
        draw_status_bays(cr, &p, l, m);
    }
}

/// Two key/value pairs per rail bay. Live values, never placeholders.
fn status_bay_rows(m: &ConsoleModel) -> Vec<Vec<(&'static str, String, RGB)>> {
    let sp = m.species;
    vec![
        vec![
            ("SYS BUS", "ONLINE".to_string(), LIME),
            ("ARCHIVE", "READY".to_string(), INK_BRIGHT),
        ],
        vec![
            ("INSTR", "NOMINAL".to_string(), LIME),
            ("FIELD", sp.plan.to_string(), INK_BRIGHT),
        ],
        vec![
            ("SPECIMEN", sp.archive.to_string(), INK_BRIGHT),
            ("CHANNEL", format!("{}/{}", m.active + 1, COUNT), INK_BRIGHT),
        ],
    ]
}

/// The fascia's lower rail: three bays, two readouts each.
fn draw_status_bays(cr: &Context, p: &Placed, l: &Layout, m: &ConsoleModel) {
    let t = &l.typ;
    for (i, pairs) in status_bay_rows(m).into_iter().enumerate() {
        let b = bay_inner(p.bay(&format!("rail_{i}")), 1.0, 0.5);
        // A bay too short for its own type is left as blank machined metal.
        // Type crammed into a 6px recess is not density, it is a fault.
        if !b.valid() || b.w < 150.0 || b.h < MIN_TEXT + 1.0 {
            continue;
        }
        let sz = (t.micro * 1.14).min(b.h * 0.86).max(MIN_TEXT);
        let base =
            b.cy() + chrome::cap(sz, chrome::W_NORMAL, chrome::FAMILY_LABEL) * 0.5;

        let need = |pr: &(&'static str, String, RGB)| -> f64 {
            chrome::text_w(pr.0, sz, chrome::W_NORMAL, t.tracking, chrome::FAMILY_LABEL)
                + sz * 0.85
                + chrome::text_w(&pr.1, sz, chrome::W_NORMAL, t.tracking * 0.5,
                                 chrome::FAMILY_LABEL)
                + sz * 1.2
        };

        // Whole pairs only: the second pair is dropped before anything is
        // ellipsised. Pairs sit on even stations when they fit them, and
        // otherwise pack from the left at their measured widths.
        let mut shown = pairs;
        while !shown.is_empty() && shown.iter().map(&need).sum::<f64>() > b.w {
            shown.pop();
        }
        if shown.is_empty() {
            continue;
        }
        let station = b.w / shown.len() as f64;
        let mut x = b.x;
        for (j, pr) in shown.iter().enumerate() {
            x = x.max(b.x + j as f64 * station);
            let cell = Rect::new(x, b.y, b.right() - x, b.h);
            bay_pair(cr, cell, pr.0, &pr.1, sz, t, pr.2, INK_TECH, 1.0, Some(base),
                     chrome::FAMILY_LABEL);
            x += need(pr);
        }
    }
}

/// Narrow states: one machined strip, two bays, same typography rules.
///
/// Below the fascia's usable width its four bays would each be a few pixels
/// wide, so the panel is replaced by a shallow recess carrying the two lines
/// that still matter. This is a different arrangement, not a scaled one.
fn draw_header_compact(
    cr: &Context,
    l: &Layout,
    r: Rect,
    m: &ConsoleModel,
    light: &mut LightField,
    static_pass: bool,
    live_pass: bool,
) {
    let null = null_cr();
    let target = if static_pass { cr } else { &null };
    let inner = rail(target, r, 0.9);
    if !inner.valid() {
        return;
    }
    let t = &l.typ;
    let sp = m.species;
    let pad = (inner.h * 0.10).max(5.0);
    let left = Rect::new(inner.x + pad, inner.y, inner.w * 0.62 - pad, inner.h);
    let text_cr = if static_pass { cr } else { &null };
    let hero = |sz: f64| chrome::cap(sz, chrome::W_NORMAL, chrome::FAMILY_HERO);
    let two = inner.h >= 32.0;
    let name_sz = t.specimen.min(inner.h * if two { 0.46 } else { 0.70 });
    if two {
        let tsz = t.title.min(inner.h * 0.26);
        let stack = hero(tsz) * 1.20 + hero(name_sz) * 1.34;
        let top = inner.y + ((inner.h - stack) * 0.5).max(0.0);
        bay_line(text_cr, left, "ABYSSAL ORGANISM MONITOR", tsz, chrome::W_NORMAL,
                 t.tracking, INK, 0.78, 'l', Some(top + hero(tsz)), None, MIN_TEXT,
                 chrome::FAMILY_HERO);
        bay_line(text_cr, left, sp.name, name_sz, chrome::W_NORMAL,
                 t.tracking * 0.7, INK_BRIGHT, 1.0, 'l',
                 Some(top + hero(tsz) * 1.20 + hero(name_sz) * 1.28), None,
                 MIN_TEXT, chrome::FAMILY_HERO);
    } else {
        bay_line(text_cr, left, sp.name, name_sz, chrome::W_NORMAL,
                 t.tracking * 0.7, INK_BRIGHT, 1.0, 'l', None, None, MIN_TEXT,
                 chrome::FAMILY_HERO);
    }

    let rx = inner.x + inner.w * 0.64;
    let bay = rail(
        if static_pass { cr } else { &null },
        Rect::new(rx, inner.y + inner.h * 0.16, inner.right() - rx - pad,
                  inner.h * 0.68),
        0.8,
    );
    if !bay.valid() || bay.w < 54.0 {
        return;
    }
    let d = bay.h.min(13.0);
    let lx = bay.x + d * 1.25;
    if static_pass {
        draw_sprite_fit(cr, &catalog::lamp("small", "nominal"), bay.x + d * 0.6,
                        bay.cy(), d, 1.0);
    } else {
        light.add(bay.x + d * 0.6, bay.cy(), d * 2.2, L_CHART, 0.16);
    }
    let lw = bay_line(text_cr, Rect::new(lx, bay.y, bay.right() - lx, bay.h), "LIVE",
                      t.label.min(bay.h * 0.68), chrome::W_NORMAL, t.tracking, LIME,
                      0.96, 'l', None, None, MIN_TEXT, chrome::FAMILY_LABEL);
    let cx0 = lx + lw + d * 0.5;
    if live_pass && !static_pass && bay.right() - cx0 > 54.0 {
        let dh = (bay.h * 0.80).min(16.0);
        seg::draw_right(cr, &clock_string(), bay.right() - 2.0, bay.cy() - dh * 0.5,
                        dh, &seg::CYAN);
    }
}

// ==========================================================================
// observation bezel
// ==========================================================================

/// The bezel overlay. Called AFTER the organism so the metal occludes it.
fn draw_stage(cr: &Context, l: &Layout, _m: &ConsoleModel, light: &mut LightField) {
    let s = l.stage;
    if !s.valid() {
        return;
    }
    module_defs::OBSERVATION.draw(cr, s, None, 1.0);
    let glass = stage_content(l);
    if glass.valid() {
        light.glow(&glass, L_CYAN, 0.07, 0.40);
    }
}

/// Geometry of the observation field's six zones. Pure.
///
/// Both passes - the cached measuring furniture and the live readings - take
/// their positions from this one call, so a cached graticule and the text
/// drawn over it cannot disagree about where the gutter ends.
fn field_zones(l: &Layout) -> (Rect, Scope, Rect, f64, bool, bool) {
    let glass = stage_content(l);
    let sc = scope_of(l);
    let safe = field_area(l);
    let bracket = if glass.valid() { glass.w.min(glass.h) * 0.035 } else { 0.0 };
    let med = glass.w >= 360.0 && glass.h >= 250.0;
    let big = glass.w >= 560.0 && glass.h >= 330.0;
    (glass, sc, safe, bracket, med, big)
}

/// Registration brackets at the field corners, as an optical instrument has.
fn corner_brackets(cr: &Context, r: Rect, size: f64, alpha: f64) {
    if r.w < size * 4.0 || r.h < size * 4.0 {
        return;
    }
    cr.save().ok();
    cr.set_line_width(1.0);
    let c = rgba(INK, alpha);
    cr.set_source_rgba(c.0, c.1, c.2, c.3);
    for (cx, cy, dx, dy) in [
        (r.x, r.y, 1.0, 1.0),
        (r.right(), r.y, -1.0, 1.0),
        (r.x, r.bottom(), 1.0, -1.0),
        (r.right(), r.bottom(), -1.0, -1.0),
    ] {
        cr.move_to(cx + dx * 0.5, cy + dy * size);
        cr.line_to(cx + dx * 0.5, cy + dy * 0.5);
        cr.line_to(cx + dx * size, cy + dy * 0.5);
    }
    let _ = cr.stroke();
    cr.restore().ok();
}

/// The RADIUS (mm) axis down the left edge, graduated off the SCOPE.
///
/// Tick spacing comes from the scope's design radius, so 1.25 mm is exactly
/// the outermost ring the creature can reach. It describes the specimen on
/// screen rather than being a decorative column of numbers. Returns the
/// gutter width the SPECIMEN FIELD zone must start after.
fn radius_ruler(cr: &Context, sc: &Scope, r: Rect, t: &TypeScale, alpha: f64,
                indent: f64) -> f64 {
    if r.h < 170.0 || r.w < 210.0 || sc.rad < 40.0 {
        return 0.0;
    }
    let sz = (t.micro * 0.92).min(r.h * 0.030).max(MIN_TEXT);
    let lab_w = chrome::text_w("1.25", sz, chrome::W_NORMAL, t.tracking, "");
    let x_tick = r.x + lab_w + sz * 1.5;
    let steps = [1.25, 1.00, 0.75, 0.50, 0.25, 0.00, 0.25, 0.50, 0.75, 1.00, 1.25];
    // Half-span is the scope radius itself, clamped into the field.
    let span = sc.rad.min(r.h * 0.5 - chrome::cap(sz, chrome::W_NORMAL, "") * 2.2);
    cr.save().ok();
    cr.set_line_width(1.0);
    let c = rgba(SCOPE, 0.85 * alpha);
    cr.set_source_rgba(c.0, c.1, c.2, c.3);
    cr.move_to(x_tick.round() + 0.5, sc.cy - span);
    cr.line_to(x_tick.round() + 0.5, sc.cy + span);
    let _ = cr.stroke();
    cr.restore().ok();
    let cap = chrome::cap(sz, chrome::W_NORMAL, "");
    for (i, v) in steps.iter().enumerate() {
        let yy = sc.cy - span + (2.0 * span) * (i as f64 / (steps.len() - 1) as f64);
        cr.save().ok();
        cr.set_line_width(1.0);
        let c = rgba(SCOPE, 0.95 * alpha);
        cr.set_source_rgba(c.0, c.1, c.2, c.3);
        let tick = if i % 5 == 0 { 0.8 } else { 0.45 };
        cr.move_to(x_tick - sz * tick, yy.round() + 0.5);
        cr.line_to(x_tick, yy.round() + 0.5);
        let _ = cr.stroke();
        cr.restore().ok();
        show(cr, &format!("{v:.2}"), sz, chrome::W_NORMAL, t.tracking,
             x_tick - sz * 0.95, yy + cap * 0.5, INK, 0.92 * alpha, 'r', -1.0, "");
    }
    // The caption heads its own scale, and the gutter it reports back is wide
    // enough for the caption as well as the tick labels. "R (mm)", not
    // "RADIUS (mm)": the caption sets the width of the gutter, and every
    // pixel of that gutter is a pixel the other zones cannot use without
    // entering the specimen's disc. R is the standard notation on a scale.
    let cap_txt = "R (mm)";
    show(cr, cap_txt, sz, chrome::W_NORMAL, t.tracking, r.x + indent,
         sc.cy - span - cap * 1.6, INK_TECH, 0.95 * alpha, 'l', -1.0, "");
    let cap_w = chrome::text_w(cap_txt, sz, chrome::W_NORMAL, t.tracking, "");
    (x_tick - r.x + sz * 1.2).max(indent + cap_w + sz * 0.8)
}

/// The field's measuring furniture: glass, brackets, ruler, grid, scale.
///
/// Every mark here is a function of the field geometry alone. It changes on
/// resize and at no other time, which is exactly what makes it cacheable.
fn draw_field_static(cr: &Context, l: &Layout) {
    let (glass, sc, safe, bracket, med, _big) = field_zones(l);
    if !glass.valid() {
        return;
    }
    // The chassis plate sits behind everything, so the bezel's transparent
    // aperture would show METAL. Fill the glass first: this is the specimen
    // chamber, and it has to be the darkest thing on the machine.
    cr.save().ok();
    let g = LinearGradient::new(glass.x, glass.y, glass.x, glass.bottom());
    g.add_color_stop_rgba(0.0, 0.024, 0.040, 0.050, 1.0);
    g.add_color_stop_rgba(0.55, 0.014, 0.026, 0.034, 1.0);
    g.add_color_stop_rgba(1.0, 0.020, 0.034, 0.043, 1.0);
    cr.set_source(&g).ok();
    cr.rectangle(glass.x, glass.y, glass.w, glass.h);
    let _ = cr.fill();
    cr.restore().ok();

    let t = &l.typ;
    corner_brackets(cr, safe, bracket, 0.7);
    let gutter =
        if med { radius_ruler(cr, &sc, safe, t, 1.0, bracket * 0.85) } else { 0.0 };
    polar_grid(cr, &sc, glass, 1.0, if gutter > 0.0 { safe.x + gutter } else { -1e9 });
    cardinals(cr, &sc, safe, t, gutter, 1.0);
}

// ==========================================================================
// telemetry channels
// ==========================================================================

struct Channel {
    key: &'static str,
    title: &'static str,
    sub: &'static str,
    /// drawn IN the segment display
    unit: &'static str,
    /// shorter forms of `sub`, longest first
    short: &'static [&'static str],
}

impl Channel {
    fn subs(&self) -> Vec<&'static str> {
        let mut v = vec![self.sub];
        v.extend_from_slice(self.short);
        v
    }
}

const CHANNELS: [Channel; 4] = [
    Channel {
        key: "cpu",
        title: "PROCESSOR",
        sub: "BIOCOMPUTATIONAL CORE",
        unit: "%",
        short: &["BIOCOMP. CORE", "BIOCOMPUT.", "CORE"],
    },
    Channel {
        key: "thermal",
        title: "THERMAL",
        sub: "ENVIRONMENT & METABOLIC",
        unit: "\u{00b0}C",
        short: &["ENVIRONMENT", "ENVIRON.", "ENV."],
    },
    Channel {
        key: "memory",
        title: "MEMORY",
        sub: "FIELD DATA & STATE",
        unit: "Gb",
        short: &["FIELD DATA", "DATA"],
    },
    Channel {
        key: "frame",
        title: "FRAME / RENDER",
        sub: "VISUALISATION PIPELINE",
        unit: "FPS",
        short: &["PIPELINE", "PIPE."],
    },
];

/// (display text, level 0..1, segment style, state word, lamp state).
fn channel_values(
    ch: &str,
    tel: &Telemetry,
    fps: f64,
) -> (String, f64, &'static seg::SegmentStyle, &'static str, &'static str) {
    match ch {
        "cpu" => {
            let lv = tel.cpu_load;
            let warn = lv > 0.85;
            (
                format!("{:.0}", tel.cpu_pct),
                lv,
                if warn { &seg::AMBER } else { &seg::CYAN },
                if warn { "ELEVATED" } else { "NOMINAL" },
                if warn { "warning" } else { "nominal" },
            )
        }
        "thermal" => {
            if !tel.temp_available || tel.temp_c.is_none() {
                ("--".to_string(), 0.0, &seg::CYAN, "NO SENSOR", "off")
            } else {
                let lv = tel.temperature;
                let hot = lv > 0.80;
                (
                    format!("{:.0}", tel.temp_c.unwrap_or(0.0)),
                    lv,
                    if lv > 0.94 {
                        &seg::RED
                    } else if hot {
                        &seg::AMBER
                    } else {
                        &seg::CYAN
                    },
                    if lv > 0.94 {
                        "AT LIMIT"
                    } else if hot {
                        "ELEVATED"
                    } else {
                        "NOMINAL"
                    },
                    if lv > 0.94 {
                        "critical"
                    } else if hot {
                        "warning"
                    } else {
                        "nominal"
                    },
                )
            }
        }
        "memory" => {
            let lv = tel.memory_pressure;
            let warn = lv > 0.88;
            (
                format!("{:.1}/{:.1}", tel.mem_used_gb, tel.mem_total_gb),
                lv,
                if warn { &seg::AMBER } else { &seg::CYAN },
                if warn { "HIGH" } else { "NOMINAL" },
                if warn { "warning" } else { "nominal" },
            )
        }
        _ => {
            let low = fps < 50.0;
            (
                format!("{fps:.0}"),
                (fps / 120.0).min(1.0),
                if low { &seg::AMBER } else { &seg::PALE },
                if low { "REDUCED" } else { "SMOOTH" },
                if low { "warning" } else { "nominal" },
            )
        }
    }
}

/// Fill one module's numeric recess: the value, its unit, and its meter.
///
/// The unit is drawn by the segment engine at a fixed fraction of the digit
/// height and bottom-aligned to the digits, so "83" and "°C" belong to one
/// display. Beneath them the bargraph trough - the generated meter asset, at
/// very close to its authored aspect - carries the same value as a level.
#[allow(clippy::too_many_arguments)]
fn draw_numeric(
    cr: &Context,
    bay: Rect,
    ch: &Channel,
    text: &str,
    level: f64,
    style: &'static seg::SegmentStyle,
    light: &mut LightField,
    alpha: f64,
    meter: bool,
) {
    let r = bay_inner(bay, 0.5, 0.5);
    if !r.valid() || r.w < 26.0 || r.h < 14.0 {
        return;
    }
    let mut trough_h = 0.0;
    if meter && r.h > 46.0 && r.w > 70.0 {
        trough_h = (r.h * 0.26).min((r.w / 8.2).max(10.0));
    }
    let disp = Rect::new(r.x, r.y, r.w, r.h - trough_h);

    // Reserve the unit first, then let the digits take everything that is
    // left. Sizing them the other way round is what makes a unit look like an
    // afterthought bolted to the right of a number.
    let unit = ch.unit;
    let u_unit = seg::measure_unit(unit, 1.0, style, 0.54);
    let u_text = seg::measure(text, 1.0, style);
    let gap_u = 0.16;
    let total = u_text + if unit.is_empty() { 0.0 } else { gap_u + u_unit };
    let dh = (disp.h * 0.90).min(disp.w / total.max(1e-6));
    if dh > 4.0 {
        let tw = u_text * dh;
        let uw = if unit.is_empty() { 0.0 } else { u_unit * dh };
        let gw = if unit.is_empty() { 0.0 } else { gap_u * dh };
        let x0 = disp.x + ((disp.w - tw - gw - uw) * 0.5).max(0.0);
        let y0 = disp.y + (disp.h - dh) * 0.5;
        seg::draw(cr, text, x0, y0, dh, style);
        if !unit.is_empty() {
            seg::draw_unit(cr, unit, x0 + tw + gw, y0, dh, style, 0.54);
        }
        light.glow(&disp, style.lit, 0.085 * alpha, 0.5);
    }

    if trough_h > 8.0 {
        let mt = Rect::new(r.x, r.bottom() - trough_h, r.w, trough_h);
        draw_nine(cr, &catalog::METER_TROUGH, mt.x, mt.y, mt.w, mt.h, 1.0);
        let (bx, by, bw, bh) = catalog::METER_TROUGH.content(mt.x, mt.y, mt.w, mt.h);
        bargraph(cr, Rect::new(bx, by, bw, bh), level, style.lit, 28);
    }
}

/// One rail carrying four readouts, for layouts with no room for modules.
///
/// This is a different arrangement of the same hardware, not a scaled-down
/// copy of the rack: at this size four separate module shells would be all
/// bevel and no information.
fn draw_condensed(
    cr: &Context,
    r: Rect,
    l: &Layout,
    tel: &Telemetry,
    fps: f64,
    light: &mut LightField,
) {
    let _ = light;
    let inner = rail(cr, r, 0.55);
    let (ix, iy, iw, ih) = (inner.x, inner.y, inner.w, inner.h);
    if iw < 40.0 || ih < 10.0 {
        return;
    }
    let n = CHANNELS.len();
    let cw = iw / n as f64;
    let t = &l.typ;
    for (i, ch) in CHANNELS.iter().enumerate() {
        let (text, level, style, _state, _lamp_state) =
            channel_values(ch.key, tel, fps);
        let cx0 = ix + i as f64 * cw;
        let cell = Rect::new(cx0, iy, cw, ih);

        // Three rows in a shallow rail: caption, readout, meter. Sized from
        // the interior so the caption clears the bevel and the meter clears
        // the bottom edge.
        let lab: String =
            ch.title.split(" /").next().unwrap_or("").chars().take(4).collect();
        let lab_sz = t.micro.min(ih * 0.24).max(5.0);
        show(cr, &lab, lab_sz, chrome::W_NORMAL, t.tracking, cell.x + cw * 0.06,
             cell.y + chrome::cap(lab_sz, chrome::W_NORMAL, "") * 1.35, INK_DIM,
             0.90, 'l', cw * 0.9, "");

        // The unit belongs to the display here too. A bare "83" on a console
        // that elsewhere reads 83 degrees C is the readout contradicting
        // itself.
        let u_unit = seg::measure_unit(ch.unit, 1.0, style, 0.54);
        let u_text = seg::measure(&text, 1.0, style);
        let total = u_text + 0.16 + u_unit;
        let dh = (ih * 0.44).min((cw * 0.88) / total.max(1e-6));
        if dh > 5.0 {
            let x0 = cell.x + cw * 0.06;
            let y0 = cell.y + ih * 0.40;
            let w = seg::draw(cr, &text, x0, y0, dh, style);
            seg::draw_unit(cr, ch.unit, x0 + w + dh * 0.16, y0, dh, style, 0.54);
        }
        let bar = Rect::new(cell.x + cw * 0.06, cell.bottom() - ih * 0.12, cw * 0.86,
                            (ih * 0.09).max(2.0));
        bargraph(cr, bar, level, style.lit, 14);
        if i > 0 {
            let c = rgba(RULE, 0.45);
            cr.set_source_rgba(c.0, c.1, c.2, c.3);
            cr.rectangle(cx0, iy + ih * 0.12, 1.0, ih * 0.76);
            let _ = cr.fill();
        }
    }
}

// ==========================================================================
// MODULE 04: the telemetry rack
// ==========================================================================
//
// The rack is ONE generated body with four manufactured rows. Each row has an
// ID plate, a title bay, four lamp sockets, a graph well, a numeric well, a
// meter trough and a state well - and every piece of data below is drawn into
// one of those recesses by name.
//
// CADENCE
//   static  (cached per size)   rack metal, plate IDs, titles, graph grids,
//                               scale values and captions
//   5 Hz    (cached per sample) each graph's 60-second trace
//   frame                       numerics, meters, state words, lamps

/// Fixed, meaningful scales. The graph shows history; it never autoscales.
struct Scale {
    lo: f64,
    hi: f64,
    /// top, middle, bottom
    ticks: [String; 3],
    caption: &'static str,
    /// a reference line (60 FPS budget)
    reference: Option<f64>,
}

fn graph_scale(ch: &str, tel: &Telemetry) -> Scale {
    match ch {
        "cpu" => Scale {
            lo: 0.0,
            hi: 100.0,
            ticks: ["100".into(), "50".into(), "0".into()],
            caption: "CPU LOAD  %",
            reference: None,
        },
        "thermal" => Scale {
            lo: 20.0,
            hi: 100.0,
            ticks: ["100".into(), "60".into(), "20".into()],
            caption: "CORE TEMP  \u{00b0}C",
            reference: None,
        },
        "memory" => {
            let tot = tel.mem_total_gb.max(1.0);
            Scale {
                lo: 0.0,
                hi: tot,
                ticks: [format!("{tot:.0}"), format!("{:.0}", tot * 0.5), "0".into()],
                caption: "RESIDENT  GB",
                reference: None,
            }
        }
        _ => Scale {
            lo: 0.0,
            hi: 50.0,
            ticks: ["50".into(), "25".into(), "0".into()],
            caption: "FRAME TIME  ms",
            reference: Some(1000.0 / 60.0),
        },
    }
}

/// (plot, caption band, type size) inside one graph well. Pure.
fn rack_zones(well: Rect, t: &TypeScale) -> (Rect, Rect, f64) {
    let g = well.inset((well.w * 0.035).max(2.0), Some((well.h * 0.05).max(2.0)));
    let sz = (t.micro * 0.84).min(g.h * 0.16).max(MIN_TEXT);
    let cap_h = if g.h > 40.0 { chrome::cap(sz, chrome::W_NORMAL, "") * 2.0 } else { 0.0 };
    let plot = Rect::new(g.x, g.y, g.w, g.h - cap_h);
    (plot, Rect::new(g.x, plot.bottom(), g.w, cap_h), sz)
}

/// Grid, scale values, 60 s divisions and caption: a real instrument.
fn rack_graph_static(cr: &Context, well: Rect, sc: &Scale, t: &TypeScale) {
    let (plot, cap, sz) = rack_zones(well, t);
    if plot.w < 20.0 || plot.h < 12.0 {
        return;
    }
    cr.save().ok();
    cr.set_line_width(1.0);
    // four horizontal divisions
    cr.set_source_rgba(0.30, 0.52, 0.62, 0.30);
    for i in 0..5 {
        let yy = (plot.y + plot.h * i as f64 / 4.0).round() + 0.5;
        cr.move_to(plot.x, yy);
        cr.line_to(plot.right(), yy);
    }
    let _ = cr.stroke();
    // six 10-second time divisions, dashed and quieter
    cr.set_source_rgba(0.30, 0.52, 0.62, 0.20);
    cr.set_dash(&[1.5, 3.0], 0.0);
    for i in 1..6 {
        let xx = (plot.x + plot.w * i as f64 / 6.0).round() + 0.5;
        cr.move_to(xx, plot.y);
        cr.line_to(xx, plot.bottom());
    }
    let _ = cr.stroke();
    cr.set_dash(&[], 0.0);
    if let Some(reference) = sc.reference {
        if sc.lo < reference && reference < sc.hi {
            let yy = (plot.bottom() - plot.h * (reference - sc.lo) / (sc.hi - sc.lo))
                .round()
                + 0.5;
            cr.set_source_rgba(0.72, 0.88, 0.29, 0.30);
            cr.set_dash(&[4.0, 2.5], 0.0);
            cr.move_to(plot.x, yy);
            cr.line_to(plot.right(), yy);
            let _ = cr.stroke();
            cr.set_dash(&[], 0.0);
        }
    }
    cr.restore().ok();
    // scale values, inside the plot at its left (oldest) edge
    let vs = (sz * 0.92).max(MIN_TEXT);
    let cap0 = chrome::cap(vs, chrome::W_NORMAL, "");
    if plot.h > vs * 3.2 && plot.w > 60.0 {
        let tx = plot.x + 2.0;
        show(cr, &sc.ticks[0], vs, chrome::W_NORMAL, t.tracking * 0.4, tx,
             plot.y + cap0 + 2.0, INK_TECH, 0.62, 'l', -1.0, "");
        if plot.h > vs * 6.0 {
            show(cr, &sc.ticks[1], vs, chrome::W_NORMAL, t.tracking * 0.4, tx,
                 plot.cy() + cap0 * 0.5, INK_TECH, 0.48, 'l', -1.0, "");
        }
        show(cr, &sc.ticks[2], vs, chrome::W_NORMAL, t.tracking * 0.4, tx,
             plot.bottom() - 2.0, INK_TECH, 0.62, 'l', -1.0, "");
    }
    if cap.h > 0.0 {
        let base = cap.cy()
            + chrome::cap(sz, chrome::W_NORMAL, chrome::FAMILY_LABEL) * 0.5;
        let span_w = show(cr, "60 s", sz, chrome::W_NORMAL, t.tracking * 0.5,
                          cap.right(), base, INK_TECH, 0.62, 'r', -1.0, "");
        bay_line(cr, Rect::new(cap.x, cap.y, cap.w - span_w - sz, cap.h),
                 sc.caption, sz, chrome::W_NORMAL, t.tracking * 0.6, INK_TECH,
                 0.86, 'l', Some(base), None, MIN_TEXT, chrome::FAMILY_LABEL);
    }
}

#[derive(Clone, PartialEq)]
struct TraceKey {
    hist: usize,
    ch: &'static str,
    pw: i32,
    ph: i32,
    version: u64,
    lo: u64,
    hi: u64,
    rgb: [u64; 3],
    ds: u64,
}

thread_local! {
    static TRACE_CACHE: RefCell<Vec<(TraceKey, ImageSurface)>> =
        const { RefCell::new(Vec::new()) };
}

/// The 60-second history, re-rendered only when a sample arrives.
#[allow(clippy::too_many_arguments)]
fn rack_trace(
    cr: &Context,
    well: Rect,
    hist: &Rc<RefCell<History>>,
    ch_key: &'static str,
    sc: &Scale,
    rgb: RGB,
    t: &TypeScale,
) {
    let (plot, _, _) = rack_zones(well, t);
    if plot.w < 20.0 || plot.h < 12.0 {
        return;
    }
    let pw = plot.w.round() as i32;
    let ph = plot.h.round() as i32;
    let mut hist_ref = hist.borrow_mut();
    let Some(channel) =
        History::CHANNELS.iter().copied().find(|c| c.name() == ch_key)
    else {
        return;
    };
    let ring = hist_ref.ring_mut(channel);
    let key = TraceKey {
        hist: std::ptr::from_ref(&*ring) as usize,
        ch: ch_key,
        pw,
        ph,
        version: ring.version,
        lo: bits(sc.lo),
        hi: bits(sc.hi),
        rgb: [bits(rgb.0), bits(rgb.1), bits(rgb.2)],
        ds: bits(hidpi::scale()),
    };
    let cached = TRACE_CACHE.with(|cache| {
        let mut cache = cache.borrow_mut();
        if let Some(pos) = cache.iter().position(|(k, _)| *k == key) {
            let ent = cache.remove(pos);
            cache.push(ent);
            Some(cache.last().unwrap().1.clone())
        } else {
            None
        }
    });
    let surf = match cached {
        Some(surf) => surf,
        None => {
            let surf = hidpi::surface(f64::from(pw), f64::from(ph));
            let c2 = Context::new(&surf).expect("trace context");
            let cols = pw.min(ring.cap as i32).max(2);
            let v = ring.window(cols);
            let span = (sc.hi - sc.lo).max(1e-6);
            let n = v.len();
            let mut pts: Vec<Vec<(f64, f64)>> = Vec::new();
            let mut run: Vec<(f64, f64)> = Vec::new();
            for (i, val) in v.iter().enumerate() {
                let val = *val;
                if val.is_nan() {
                    // NaN: no data yet for this column
                    if !run.is_empty() {
                        pts.push(std::mem::take(&mut run));
                    }
                    continue;
                }
                let f = ((val as f64) - sc.lo) / span;
                let f = f.clamp(0.0, 1.0);
                run.push((
                    f64::from(pw) * i as f64 / (n - 1) as f64,
                    1.5 + (f64::from(ph) - 3.0) * (1.0 - f),
                ));
            }
            if !run.is_empty() {
                pts.push(run);
            }
            for segment in &pts {
                if segment.len() < 2 {
                    continue;
                }
                c2.move_to(segment[0].0, f64::from(ph));
                for (x, y) in segment {
                    c2.line_to(*x, *y);
                }
                c2.line_to(segment.last().unwrap().0, f64::from(ph));
                let _ = c2.close_path();
                let g = LinearGradient::new(0.0, 0.0, 0.0, f64::from(ph));
                g.add_color_stop_rgba(0.0, rgb.0, rgb.1, rgb.2, 0.26);
                g.add_color_stop_rgba(1.0, rgb.0, rgb.1, rgb.2, 0.03);
                c2.set_source(&g).ok();
                let _ = c2.fill();
                c2.set_line_join(cairo::LineJoin::Round);
                c2.set_line_width((f64::from(ph) * 0.03).clamp(1.0, 1.8));
                c2.set_source_rgba(rgb.0, rgb.1, rgb.2, 0.95);
                c2.move_to(segment[0].0, segment[0].1);
                for (x, y) in &segment[1..] {
                    c2.line_to(*x, *y);
                }
                let _ = c2.stroke();
            }
            if let Some(last) = pts.last().and_then(|s| s.last()) {
                let (hx, hy) = *last;
                c2.set_source_rgba(rgb.0, rgb.1, rgb.2, 1.0);
                c2.arc(hx.min(f64::from(pw) - 2.0), hy,
                       (f64::from(ph) * 0.035).max(1.3), 0.0, TAU);
                let _ = c2.fill();
            }
            surf.flush();
            TRACE_CACHE.with(|cache| {
                let mut cache = cache.borrow_mut();
                cache.push((key, surf.clone()));
                while cache.len() > TRACE_LIMIT {
                    cache.remove(0);
                }
            });
            surf
        }
    };
    drop(hist_ref);
    cr.save().ok();
    let _ = cr.set_source_surface(&surf, plot.x.round(), plot.y.round());
    let _ = cr.paint();
    cr.restore().ok();
}

/// Deliberate short forms of the state words, for narrow state wells.
fn state_shorts(word: &str) -> &'static [&'static str] {
    match word {
        "NOMINAL" => &["NOM."],
        "ELEVATED" => &["ELEV."],
        "REDUCED" => &["RED."],
        "SMOOTH" => &["OK"],
        "HIGH" => &["HI"],
        "AT LIMIT" => &["LIMIT", "LIM."],
        "NO SENSOR" => &["N/A"],
        _ => &[],
    }
}

/// STATE caption over the state word, both centred in the narrow well.
fn rack_state(cr: &Context, well: Rect, word: &str, rgb: RGB, t: &TypeScale) {
    let r = well.inset((well.w * 0.06).max(1.5), Some((well.h * 0.10).max(2.0)));
    if !r.valid() || r.w < 16.0 {
        return;
    }
    let ksz = (t.micro * 0.84).min(r.h * 0.16).max(MIN_TEXT);
    let vsz = (t.label * 1.10).min(r.h * 0.26).min(r.w * 0.22).max(MIN_TEXT);
    let kcap = chrome::cap(ksz, chrome::W_NORMAL, chrome::FAMILY_LABEL);
    let vcap = chrome::cap(vsz, chrome::W_NORMAL, chrome::FAMILY_LABEL);
    let gap = kcap * 1.1;
    let stack = kcap + gap + vcap;
    let top = r.cy() - stack * 0.5;
    // The full word, shrunk by at most a quarter; then the short form.
    let mut shown = String::new();
    for cand in [vsz, vsz * 0.9, vsz * 0.8, vsz * 0.75] {
        let sz = cand.max(MIN_TEXT);
        if chrome::text_w(word, sz, chrome::W_NORMAL, t.tracking * 0.3,
                          chrome::FAMILY_LABEL)
            <= r.w
        {
            shown = word.to_string();
            break;
        }
    }
    if shown.is_empty() {
        shown = fit_first(state_shorts(word), vsz, chrome::W_NORMAL,
                          t.tracking * 0.3, r.w, chrome::FAMILY_LABEL);
    }
    if shown.is_empty() {
        return;
    }
    if chrome::text_w("STATE", ksz, chrome::W_NORMAL, t.tracking * 0.8,
                      chrome::FAMILY_LABEL)
        <= r.w
    {
        show(cr, "STATE", ksz, chrome::W_NORMAL, t.tracking * 0.8, r.cx(),
             top + kcap, INK_TECH, 0.72, 'c', -1.0, chrome::FAMILY_LABEL);
    }
    show(cr, &shown, vsz, chrome::W_NORMAL, t.tracking * 0.3, r.cx(), top + stack,
         rgb, 0.98, 'c', -1.0, chrome::FAMILY_LABEL);
}

/// Rack metal plus everything in it that only changes on resize.
fn draw_rack_static(cr: &Context, l: &Layout, tel: &Telemetry) {
    let r = l.readout;
    if !r.valid() {
        return;
    }
    let t = &l.typ;
    let p = module_defs::RACK.draw(cr, r, None, 1.0);
    for (i, ch) in CHANNELS.iter().enumerate() {
        let pl = p.bay(&format!("r{i}_plate"));
        if pl.h > 7.0 {
            let sz = (t.label * 1.05).min(pl.h * 0.62).max(MIN_TEXT);
            // engraved into the bright plate: dark cut, light lower lip
            let base = pl.cy() + chrome::cap(sz, chrome::W_NORMAL, "") * 0.5;
            show(cr, &format!("0{}", i + 1), sz, chrome::W_MEDIUM, t.tracking * 0.6,
                 pl.cx(), base + 1.0, (0.86, 0.88, 0.88), 0.50, 'c', -1.0, "");
            show(cr, &format!("0{}", i + 1), sz, chrome::W_MEDIUM, t.tracking * 0.6,
                 pl.cx(), base, (0.03, 0.04, 0.05), 1.0, 'c', -1.0, "");
        }
        let tb = p.bay(&format!("r{i}_title"));
        let tb = tb.inset((tb.h * 0.30).max(3.0), Some(0.0));
        if tb.valid() {
            let tsz = (t.label * 1.15).min(tb.h * 0.70).max(MIN_TEXT);
            let base =
                tb.cy() + chrome::cap(tsz, chrome::W_NORMAL, chrome::FAMILY_LABEL) * 0.5;
            let tw = bay_line(cr, tb, ch.title, tsz, chrome::W_NORMAL, t.tracking,
                              INK_BRIGHT, 0.97, 'l', Some(base), None, MIN_TEXT,
                              chrome::FAMILY_LABEL);
            let sx = tb.x + tw + tsz * 1.2;
            let ssz = (t.micro * 0.90).min(tb.h * 0.50).max(MIN_TEXT);
            let sub = fit_first(&ch.subs(), ssz, chrome::W_NORMAL, t.tracking,
                                tb.right() - sx, chrome::FAMILY_LABEL);
            if !sub.is_empty() {
                show(cr, &sub, ssz, chrome::W_NORMAL, t.tracking, tb.right(), base,
                     INK_TECH, 0.80, 'r', -1.0, chrome::FAMILY_LABEL);
            }
        }
        let sc = graph_scale(ch.key, tel);
        rack_graph_static(cr, p.bay(&format!("r{i}_graph")), &sc, t);
    }
}

fn rack_placed(l: &Layout) -> Placed {
    module_defs::RACK.place(l.readout, None)
}

/// The rack's live pass: lamps, traces, numerics, meters, state words.
#[allow(clippy::too_many_arguments)]
fn draw_rack_live(
    cr: &Context,
    l: &Layout,
    tel: &Telemetry,
    fps: f64,
    frame_ms: f64,
    m: &ConsoleModel,
    light: &mut LightField,
) {
    let _ = frame_ms;
    let r = l.readout;
    if !r.valid() {
        return;
    }
    let t = &l.typ;
    let p = rack_placed(l);
    let hist = &m.history;
    for (i, ch) in CHANNELS.iter().enumerate() {
        let (text, level, style, state, lamp_state) =
            channel_values(ch.key, tel, fps);
        // lamps
        let d = p.bay(&format!("r{i}_lamp0")).h * 1.05;
        for k in 0..4usize {
            let lb = p.bay(&format!("r{i}_lamp{k}"));
            let st = if k == 3 {
                lamp_state
            } else if k == 0 {
                "standby"
            } else {
                "nominal"
            };
            draw_sprite_fit(cr, &catalog::lamp("small", st), lb.cx(), lb.cy(), d, 1.0);
            // Only a lamp that is SAYING something throws light: a warning
            // spill is information, sixteen green halos are noise.
            if st == "warning" || st == "critical" {
                light.add(lb.cx(), lb.cy(), d * 2.4, L_AMBER, 0.14);
            }
        }
        // graph: the 60 s history
        let well = p.bay(&format!("r{i}_graph"));
        let sc = graph_scale(ch.key, tel);
        rack_trace(cr, well, hist, ch.key, &sc, style.lit, t);
        // numeric: the value NOW, and its level in the meter trough
        draw_numeric(cr, p.bay(&format!("r{i}_numeric")), ch, &text, level, style,
                     light, 1.0, false);
        let mt = p.bay(&format!("r{i}_meter"));
        let mi = mt.inset((mt.h * 0.22).max(2.0), Some((mt.h * 0.28).max(1.5)));
        bargraph(cr, mi, level, style.lit, 26);
        rack_state(cr, p.bay(&format!("r{i}_state")), state, style.lit, t);
    }
}

// ==========================================================================
// controls
// ==========================================================================

/// MODULE 05: the selector mounting plate, and the approved keys in it.
///
/// STATIC   the generated plate (five trued transparent wells, two control
///          openings, label ledges) and the engraved channel identifiers.
/// LIVE     the approved key plates, rocker and mode key, and their light.
///
/// The plate's wells are genuinely open: whatever sits beneath a key is the
/// shell's recessed bed, never a black rectangle painted to hide a gap.
fn draw_controls(
    cr: &Context,
    l: &Layout,
    m: &ConsoleModel,
    light: &mut LightField,
    static_pass: bool,
    live_pass: bool,
) {
    let (geo, mode) = control_geometry(l);
    if !geo.valid() {
        return;
    }
    if static_pass {
        module_defs::SELECTOR.draw(cr, l.controls, None, 1.0);
        // The engraved channel identifiers are SERVICE type: they belong to
        // the diagnostics view (F1), where naming every channel matters.
        if m.diag && geo.ledge >= 5.0 {
            let t = &l.typ;
            let sz = (t.micro * 0.84).min(geo.ledge * 0.66).max(MIN_TEXT);
            for (i, sp) in CATALOGUE.iter().enumerate() {
                let lr = geo.label_rect(i);
                let mut txt = format!("{:02}  {}", i + 1, sp.archive);
                if chrome::text_w(&txt, sz, chrome::W_NORMAL, t.tracking * 0.35, "")
                    > lr.w
                {
                    txt = sp.archive.to_string();
                    if chrome::text_w(&txt, sz, chrome::W_NORMAL, t.tracking * 0.35,
                                      "")
                        > lr.w
                    {
                        txt = format!("{:02}", i + 1);
                    }
                }
                let live_i = i == m.active;
                // baseline on the trough floor, clear of the key above
                show(cr, &txt, sz, chrome::W_NORMAL, t.tracking * 0.35, lr.cx(),
                     lr.bottom() - lr.h * 0.16,
                     if live_i { INK_BRIGHT } else { INK_TECH },
                     if live_i { 1.0 } else { 0.86 }, 'c', lr.w, "");
            }
        }
    }
    if live_pass {
        draw_controls_live(cr, l, m, light, &geo, mode);
    }
}

/// The approved key plates, the aux controls and their light.
///
/// NO BACKING RECTANGLE. The authored active plate carries its own
/// illumination; the only addition here is a small bloom from its lamp strip.
fn draw_controls_live(
    cr: &Context,
    _l: &Layout,
    m: &ConsoleModel,
    light: &mut LightField,
    geo: &sel::BankGeometry,
    mode: Rect,
) {
    sel::draw_keys(cr, geo, m.active, m.pressed, m.focus, &m.disabled, 1.0);
    if m.active < geo.n && !m.disabled.contains(&m.active) {
        let (lx, ly, lrad) = geo.lamp_point(m.active);
        light.add(lx, ly, (lrad * 1.2).max(6.0), L_CHART, 0.10);
    }

    let cyc = cycle_rect(geo, mode);
    if cyc.valid() && cyc.w > 10.0 {
        draw_sprite(cr, &catalog::cycle_key(&m.cycle_state), cyc.x, cyc.y, cyc.w,
                    cyc.h, 1.0);
    }
    if mode.valid() && mode.w > 8.0 {
        draw_sprite(cr, &catalog::mode_key(&m.mode_state), mode.x, mode.y, mode.w,
                    mode.h, 1.0);
        if m.mode_state == "active" {
            light.add(mode.cx(), mode.cy(), mode.h * 0.5, L_CHART, 0.10);
        } else if m.mode_state == "error" {
            light.add(mode.cx(), mode.cy(), mode.h * 0.5, L_AMBER, 0.10);
        } else if m.mode_state == "armed" {
            light.add(mode.cx(), mode.cy(), mode.h * 0.45, L_CYAN, 0.06);
        }
    }
}

// ==========================================================================
// footer
// ==========================================================================

/// MODULE 06: the bottom status rail, with the archive record in its bays.
///
/// Seven information bays carry the specimen's archive record; the terminal
/// bay carries the system state. The badge boss at the left is cast metal
/// (part of the generated rail), so nothing is drawn there.
fn draw_footer(cr: &Context, l: &Layout, m: &ConsoleModel) {
    let r = l.footer;
    if !l.show_footer || !r.valid() {
        return;
    }
    let t = &l.typ;
    let sp = m.species;
    if !module_defs::FOOTER.available() || r.w < 420.0 || r.h < 18.0 {
        draw_footer_plain(cr, l, r, m);
        return;
    }
    let p = module_defs::FOOTER.draw(cr, r, None, 1.0);
    // One line per bay, one type size for the whole rail, as the reference
    // rail reads. Values are the SHORT forms so none is ever ellipsised; the
    // full archive record is in the header and the field. The rail is the
    // machine's technical voice: Microgramma.
    let sym = sp.symmetry_short();
    let vals: Vec<Vec<&str>> = vec![
        vec!["AOM-1"],
        vec![sp.archive],
        vec![first_word(sp.cls), "MATH."],
        vec![sp.origin, "SYNTH."],
        vec![sym, &sym[..sym.len().min(5)]],
        vec!["LIVE"],
        vec![&m.behavior, &m.behavior[..m.behavior.len().min(4)]],
    ];
    let b0 = p.bay("bay_0");
    let inner: Vec<Rect> = (0..vals.len())
        .map(|i| p.bay(&format!("bay_{i}")).inset((b0.h * 0.24).max(3.0), Some(0.0)))
        .collect();
    let mut vsz = (t.micro * 1.02).min(b0.h * 0.38).max(MIN_TEXT);
    let ksz = (t.micro * 0.80).min(b0.h * 0.26).max(MIN_TEXT);
    // one size for the rail: shrink (never below the floor) until every
    // value's FULL form fits, then fall back to short forms bay by bay
    while vsz > MIN_TEXT
        && vals
            .iter()
            .zip(inner.iter())
            .any(|(v, bi)| {
                chrome::text_w(v[0], vsz, chrome::W_NORMAL, t.tracking * 0.5,
                               chrome::FAMILY_LABEL)
                    > bi.w
            })
    {
        vsz = (vsz - 0.25).max(MIN_TEXT);
    }
    for (v, bi) in vals.iter().zip(inner.iter()) {
        let txt = fit_first(v, vsz, chrome::W_NORMAL, t.tracking * 0.5, bi.w,
                            chrome::FAMILY_LABEL);
        if bi.valid() && !txt.is_empty() {
            show(cr, &txt, vsz, chrome::W_NORMAL, t.tracking * 0.5, bi.x,
                 bi.cy() + chrome::cap(vsz, chrome::W_NORMAL, chrome::FAMILY_LABEL)
                     * 0.5,
                 INK_BRIGHT, 0.92, 'l', -1.0, chrome::FAMILY_LABEL);
        }
    }
    let tb = p.bay("terminal").inset((b0.h * 0.30).max(4.0),
                                     Some((b0.h * 0.10).max(1.5)));
    if tb.valid() {
        let base = tb.cy()
            + chrome::cap(vsz, chrome::W_NORMAL, chrome::FAMILY_LABEL) * 0.5;
        let w0 = show(cr, "SYSTEM BUS", ksz, chrome::W_NORMAL, t.tracking, tb.x,
                      base, INK_TECH, 0.80, 'l', -1.0, chrome::FAMILY_LABEL);
        let w1 = show(cr, "ONLINE", vsz, chrome::W_NORMAL, t.tracking * 0.6,
                      tb.x + w0 + vsz * 0.7, base, LIME, 0.96, 'l', -1.0,
                      chrome::FAMILY_LABEL);
        let mx = tb.x + w0 + w1 + vsz * 2.0;
        let motto = "OBSERVE \u{00b7} UNDERSTAND \u{00b7} EXTEND";
        // The motto is shown whole or not at all: an ellipsised motto is noise.
        if chrome::text_w(motto, ksz, chrome::W_NORMAL, t.tracking,
                          chrome::FAMILY_LABEL)
            <= tb.right() - mx
        {
            show(cr, motto, ksz, chrome::W_NORMAL, t.tracking, tb.right(), base,
                 INK_DIM, 0.85, 'r', -1.0, chrome::FAMILY_LABEL);
        }
    }
}

fn first_word(s: &str) -> &str {
    s.split_whitespace().next().unwrap_or("")
}

/// Narrow states: a shallow machined rail carrying as many bays as fit.
fn draw_footer_plain(cr: &Context, l: &Layout, r: Rect, m: &ConsoleModel) {
    let inner = rail(cr, r, 0.55);
    if !inner.valid() {
        return;
    }
    let sp = m.species;
    let t = &l.typ;
    let mut bays: Vec<(&str, String)> = vec![
        ("INSTRUMENT", "AOM-1".to_string()),
        ("ARCHIVE", sp.archive.to_string()),
        ("CLASS", sp.cls.to_string()),
        ("ORIGIN", sp.origin.to_string()),
        ("SYMMETRY", sp.symmetry.to_string()),
        ("MODE", "LIVE OBSERVATION".to_string()),
        ("FIELD", m.behavior.clone()),
        ("SYSTEM BUS", "ONLINE".to_string()),
    ];
    let avail = inner.w;
    let keep = if avail < 240.0 {
        3
    } else if avail < 520.0 {
        5
    } else if avail < 720.0 {
        6
    } else {
        bays.len()
    };
    bays.truncate(keep);
    let cw = avail / bays.len() as f64;
    let size = t.micro.min(inner.h * 0.34).max(MIN_TEXT);
    for (i, (k, v)) in bays.iter().enumerate() {
        let cell = Rect::new(
            inner.x + i as f64 * cw + cw * 0.06,
            inner.y,
            cw * 0.88,
            inner.h,
        );
        let vrgb = if v == "ONLINE" { LIME } else { INK_BRIGHT };
        bay_stack(cr, cell, k, v, size * 0.88, size, t, vrgb, 1.0,
                  chrome::FAMILY_LABEL);
        if i > 0 {
            divider(cr, inner.x + i as f64 * cw - cw * 0.02, inner.y, inner.bottom(),
                    0.7);
        }
    }
}

// ==========================================================================
// the wall clock (Python: time.strftime("%H:%M:%S"))
// ==========================================================================

fn clock_string() -> String {
    glib::DateTime::now_local()
        .ok()
        .and_then(|d| d.format("%H:%M:%S").ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "--:--:--".to_string())
}

// ==========================================================================
// the static hardware layer
// ==========================================================================
//
// Most of this console does not change between frames. The chassis, the
// bezels, the header fascia and its engraved names, the archive rail, the
// selector's mounting trough, the field's graticule and rulers - all of it is
// a pure function of the widget size and which specimen is selected. It is
// rendered ONCE into two surfaces and blitted.
//
// SAFETY
// ------
// The cache key carries every discrete input the static passes read. A live
// value cannot leak into a cached surface because the live pass is a
// different function; qa/console_gates.py GATE 8 renders each frame warm and
// cold and asserts the two are byte-identical. Dropping the cache between any
// two frames would change performance and nothing else.
//
// In Rust the id in each Layer plays the role of Python's surface identity:
// unchanged key -> same id; changed key -> fresh id + fresh surface.

/// Every discrete input the static passes read. Nothing live belongs here.
///
/// `mem_total` is part of the key because the memory graph's fixed scale is
/// the installed memory; it is constant for the life of the process.
#[derive(Clone, PartialEq)]
struct LayerKey {
    w: i32,
    h: i32,
    state: LayoutState,
    species_key: &'static str,
    active: usize,
    behavior: String,
    show_chassis: bool,
    show_footer: bool,
    show_controls: bool,
    show_status: bool,
    mem_total: u64,
    ds: u64,
    diag: bool,
}

fn layer_key(l: &Layout, m: &ConsoleModel, mem_total: f64) -> LayerKey {
    LayerKey {
        w: l.width.round() as i32,
        h: l.height.round() as i32,
        state: l.state,
        species_key: m.species.key,
        active: m.active,
        behavior: m.behavior.clone(),
        show_chassis: l.show_chassis,
        show_footer: l.show_footer,
        show_controls: l.show_controls,
        show_status: l.show_status,
        mem_total: bits((mem_total * 10.0).round() / 10.0),
        ds: bits(hidpi::scale()),
        diag: m.diag,
    }
}

// --------------------------------------------------------------------------
// composition: static layers + live regions
// --------------------------------------------------------------------------
//
// The frame is composed from, bottom to top:
//
//   LAYER 0-1  under   background, 01 shell, glass, graticule     (static)
//   LAYER 2    organism, clipped to the glass                     (per frame)
//   LAYER 3    over    03 bezel, 02 header, 04 rack, 05 selector
//                      plate, 06 rail + all static type           (static)
//   LAYER 4-6  regions header clock, rack readouts, selector keys,
//                      each with its own light spill              (on change)
//
// A REGION is a small rectangle that is re-rendered only when its own inputs
// change - the clock once a second, the rack when a telemetry sample lands,
// the keys on input. It starts from the static composite of that rectangle,
// so placing it over the static layers is seamless, and it never overlaps the
// glass. Per frame, the only thing rasterised is the organism.

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct RegionIdent {
    x: u64,
    y: u64,
    w: u64,
    h: u64,
    ds: u64,
    under: u64,
    over: u64,
}

#[derive(Clone, PartialEq, Eq, Hash)]
struct TelKey {
    cpu_pct: i64,
    temp_c: Option<i64>,
    mem_used: i64,
    mem_total: i64,
    temp_available: bool,
    cpu_load: i64,
    temperature: i64,
    memory_pressure: i64,
}

impl TelKey {
    fn of(t: &Telemetry) -> TelKey {
        TelKey {
            cpu_pct: (t.cpu_pct * 10.0).round() as i64,
            temp_c: t.temp_c.map(bits).map(|b| b as i64),
            mem_used: (t.mem_used_gb * 100.0).round() as i64,
            mem_total: (t.mem_total_gb * 10.0).round() as i64,
            temp_available: t.temp_available,
            cpu_load: (t.cpu_load * 1000.0).round() as i64,
            temperature: (t.temperature * 1000.0).round() as i64,
            memory_pressure: (t.memory_pressure * 1000.0).round() as i64,
        }
    }
}

#[derive(Clone, PartialEq, Eq, Hash)]
enum RegionPayload {
    /// the wall clock, once a second
    Clock(String),
    /// telemetry: when a sample lands or the displayed FPS changes
    Rack(TelKey, i64, [u64; 4]),
    /// selector keys: on input only
    Keys {
        active: usize,
        pressed: Option<usize>,
        focus: Option<usize>,
        disabled: Vec<usize>,
        mode: String,
        cycle: String,
    },
}

#[derive(Clone, PartialEq, Eq, Hash)]
struct RegionKey {
    name: &'static str,
    ident: RegionIdent,
    payload: RegionPayload,
}

/// Grow `r` to whole DEVICE pixels, so a region's copy of the static layers
/// lands pixel-for-pixel on them and cannot leave a seam.
fn snap(r: Rect, pad: f64) -> Rect {
    let ds = hidpi::scale();
    let x0 = ((r.x - pad) * ds).floor();
    let y0 = ((r.y - pad) * ds).floor();
    let x1 = ((r.right() + pad) * ds).ceil();
    let y1 = ((r.bottom() + pad) * ds).ceil();
    Rect::new(x0 / ds, y0 / ds, (x1 - x0) / ds, (y1 - y0) / ds)
}

fn paint_region_live(
    cr: &Context,
    light: &mut LightField,
    name: &'static str,
    l: &Layout,
    m: &ConsoleModel,
    tel: &Telemetry,
    fps: f64,
    frame_ms: f64,
) {
    match name {
        "clock" => draw_header(cr, l, m, light, false, true),
        "rack" => {
            if six_module(l) {
                draw_rack_live(cr, l, tel, fps, frame_ms, m, light);
            } else {
                draw_condensed(cr, l.readout, l, tel, fps, light);
            }
        }
        "keys" => draw_controls(cr, l, m, light, false, true),
        _ => {}
    }
}

/// Cached console renderer: owns the static-chrome and live-region surfaces.
///
/// The host calls `layer_under` / `layer_over` / `regions` every frame; each
/// call is a key comparison against the caches. A content change re-renders
/// and issues a fresh `Layer::id`, which is what tells the host's texture
/// cache to re-upload.
pub struct Renderer {
    next_id: u64,
    under: Vec<(LayerKey, Layer)>,
    over: Vec<(LayerKey, Layer)>,
    regions: Vec<(RegionKey, Layer)>,
}

impl Default for Renderer {
    fn default() -> Self {
        Self::new()
    }
}

impl Renderer {
    pub fn new() -> Renderer {
        // Display faces must exist in a scanned font directory before the
        // first Pango font map is built (ui/fonts.py contract).
        chrome::init();
        Renderer {
            next_id: 1,
            under: Vec::new(),
            over: Vec::new(),
            regions: Vec::new(),
        }
    }

    fn fresh_layer(&mut self, surface: ImageSurface) -> Layer {
        let id = self.next_id;
        self.next_id += 1;
        Layer { id, surface }
    }

    /// LAYERS 0-1: background, shell, glass, graticule. None when nothing to draw.
    pub fn layer_under(&mut self, l: &Layout, m: &ConsoleModel) -> Option<Layer> {
        let key = layer_key(l, m, 0.0);
        if let Some(pos) = self.under.iter().position(|(k, _)| *k == key) {
            let ent = self.under.remove(pos);
            self.under.push(ent);
            return Some(self.under.last().unwrap().1.clone());
        }
        let (w, h) = (l.width.round() as i32, l.height.round() as i32);
        if w < 1 || h < 1 {
            return None;
        }
        let surf = hidpi::surface(w as f64, h as f64);
        {
            let c2 = Context::new(&surf).expect("under context");
            chrome::draw_background(&c2, l.width, l.height);
            draw_chassis(&c2, l);
            draw_field_static(&c2, l);
        }
        surf.flush();
        let layer = self.fresh_layer(surf);
        self.under.push((key, layer.clone()));
        while self.under.len() > LAYER_LIMIT {
            self.under.remove(0);
        }
        Some(layer)
    }

    /// LAYER 3: the structural modules and static type. None when nothing.
    pub fn layer_over(
        &mut self,
        l: &Layout,
        m: &ConsoleModel,
        tel: &Telemetry,
    ) -> Option<Layer> {
        let key = layer_key(l, m, tel.mem_total_gb);
        if let Some(pos) = self.over.iter().position(|(k, _)| *k == key) {
            let ent = self.over.remove(pos);
            self.over.push(ent);
            return Some(self.over.last().unwrap().1.clone());
        }
        let (w, h) = (l.width.round() as i32, l.height.round() as i32);
        if w < 1 || h < 1 {
            return None;
        }
        let six = six_module(l);
        let surf = hidpi::surface(w as f64, h as f64);
        {
            let c2 = Context::new(&surf).expect("over context");
            let mut light = LightField::new();
            draw_stage(&c2, l, m, &mut light);
            draw_header(&c2, l, m, &mut light, true, false);
            if six {
                draw_rack_static(&c2, l, tel);
            }
            draw_controls(&c2, l, m, &mut light, true, false);
            draw_footer(&c2, l, m);
            // constant emitters (the glass, the LIVE lamp) belong to the metal
            light.paint(&c2, None);
        }
        surf.flush();
        let layer = self.fresh_layer(surf);
        self.over.push((key, layer.clone()));
        while self.over.len() > LAYER_LIMIT {
            self.over.remove(0);
        }
        Some(layer)
    }

    /// The live regions for this frame. Each is cached on its OWN inputs.
    #[allow(clippy::too_many_arguments)]
    pub fn regions(
        &mut self,
        l: &Layout,
        m: &ConsoleModel,
        tel: &Telemetry,
        fps: f64,
        frame_ms: f64,
        under: Option<&Layer>,
        over: Option<&Layer>,
    ) -> Vec<Region> {
        let under_owned;
        let over_owned;
        let under: &Layer = match under {
            Some(u) => u,
            None => {
                under_owned = self.layer_under(l, m);
                under_owned.as_ref().expect("under layer")
            }
        };
        let over: &Layer = match over {
            Some(o) => o,
            None => {
                over_owned = self.layer_over(l, m, tel);
                over_owned.as_ref().expect("over layer")
            }
        };
        let mut out: Vec<Region> = Vec::new();
        let six = six_module(l);
        let hist_v: [u64; 4] = {
            let h = m.history.borrow();
            let mut v = [0u64; 4];
            for (i, c) in History::CHANNELS.iter().enumerate() {
                v[i] = h.ring(*c).version;
            }
            v
        };
        let fps_i = fps.round() as i64;
        let tel_key = TelKey::of(tel);

        // header clock: once a second
        let clk = clock_string();
        let hb = if six {
            let p = module_defs::HEADER.place(header_panel(l), None);
            if l.header.valid() {
                p.bay("clock")
            } else {
                Rect::NONE
            }
        } else {
            l.header
        };
        if hb.valid() {
            if let Some(reg) = self.region("clock", hb, RegionPayload::Clock(clk),
                                           under, over, l, m, tel, fps_i, frame_ms)
            {
                out.push(reg);
            }
        }

        // telemetry: when a sample lands or the displayed FPS changes
        if l.readout.valid() {
            if let Some(reg) = self.region("rack", l.readout,
                                           RegionPayload::Rack(tel_key, fps_i, hist_v),
                                           under, over, l, m, tel, fps_i, frame_ms)
            {
                out.push(reg);
            }
        }

        // selector keys: on input only
        if l.show_controls && l.controls.valid() {
            let mut disabled = m.disabled.clone();
            disabled.sort_unstable();
            disabled.dedup();
            if let Some(reg) = self.region(
                "keys",
                l.controls,
                RegionPayload::Keys {
                    active: m.active,
                    pressed: m.pressed,
                    focus: m.focus,
                    disabled,
                    mode: m.mode_state.clone(),
                    cycle: m.cycle_state.clone(),
                },
                under,
                over,
                l,
                m,
                tel,
                fps_i,
                frame_ms,
            ) {
                out.push(reg);
            }
        }
        out
    }

    /// One cached region: static composite + live content + clipped light.
    #[allow(clippy::too_many_arguments)]
    fn region(
        &mut self,
        name: &'static str,
        rect: Rect,
        payload: RegionPayload,
        under: &Layer,
        over: &Layer,
        l: &Layout,
        m: &ConsoleModel,
        tel: &Telemetry,
        fps_i: i64,
        frame_ms: f64,
    ) -> Option<Region> {
        let r = snap(rect, 6.0);
        if r.w < 2.0 || r.h < 2.0 {
            return None;
        }
        let key = RegionKey {
            name,
            ident: RegionIdent {
                x: bits(r.x),
                y: bits(r.y),
                w: bits(r.w),
                h: bits(r.h),
                ds: bits(hidpi::scale()),
                under: under.id,
                over: over.id,
            },
            payload,
        };
        if let Some(pos) = self.regions.iter().position(|(k, _)| *k == key) {
            let ent = self.regions.remove(pos);
            self.regions.push(ent);
            return Some(Region {
                layer: self.regions.last().unwrap().1.clone(),
                rect: r,
            });
        }
        let surf = hidpi::surface(r.w, r.h);
        {
            let c = Context::new(&surf).expect("region context");
            c.translate(-r.x, -r.y);
            for layer in [under, over] {
                let _ = c.set_source_surface(&layer.surface, 0.0, 0.0);
                let _ = c.paint();
            }
            let mut light = LightField::new();
            paint_region_live(&c, &mut light, name, l, m, tel, fps_i as f64,
                              frame_ms);
            light.paint(&c, Some(Clip { x: r.x, y: r.y, w: r.w, h: r.h }));
        }
        surf.flush();
        let layer = self.fresh_layer(surf);
        self.regions.push((key, layer.clone()));
        while self.regions.len() > REGION_LIMIT {
            self.regions.remove(0);
        }
        Some(Region { layer, rect: r })
    }

    /// Drop derived surfaces. Purely a memory operation; changes no output.
    pub fn clear_static_cache(&mut self) {
        self.under.clear();
        self.over.clear();
        self.regions.clear();
        TXT.with(|c| c.borrow_mut().clear());
        RING_CACHE.with(|c| c.borrow_mut().clear());
        TRACE_CACHE.with(|c| c.borrow_mut().clear());
    }
}
