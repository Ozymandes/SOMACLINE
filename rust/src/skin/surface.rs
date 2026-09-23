//! Sprite loading, scale-safe 9-slice rendering, and a bounded surface cache.
//! Port of skin/surface.py.
//!
//! What is cached is purely derived pixels: "this panel, rendered at this
//! exact integer size". A miss costs a re-render, never a crash and never a
//! visual difference, because the cache key IS the full set of inputs.
//! Surfaces are written once when rendered (GSK snapshots may alias them).
//!
//! PREMULTIPLIED ALPHA: sprites come from `ImageSurface::from_png`, which
//! produces ARGB32 with alpha already premultiplied, exactly what Cairo's
//! compositor wants.
//!
//! 9-SLICE CONTRACT: corners drawn 1:1, edges stretched along ONE axis only,
//! centre stretched freely -- fasteners stay sharp at any panel size.

use std::collections::HashMap;
use std::collections::VecDeque;
use std::path::PathBuf;

use cairo::Context;
use cairo::Filter;
use cairo::ImageSurface;
use cairo::Operator;

use crate::skin::hidpi;

/// Bounded so a long resize drag cannot grow memory without limit.
const MAX_SCALED: usize = 64;

/// Floor on the stretched middle band, as a share of the drawn panel.
pub const MIN_MIDDLE_SHARE: f64 = 0.34;

/// Floor on the proportional border scale (ONE factor for both axes so
/// corners stay square and no screw is ovalised).
pub const MIN_BORDER_SCALE: f64 = 0.45;

fn sprite_roots() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(manifest) = std::env::var("CARGO_MANIFEST_DIR") {
        v.push(PathBuf::from(manifest).join("../assets/sprites"));
    }
    if let Ok(exe) = std::env::current_exe() {
        for up in ["../../../assets/sprites", "../../../../assets/sprites"] {
            v.push(exe.parent().unwrap_or(std::path::Path::new(".")).join(up));
        }
    }
    v.push(PathBuf::from("assets/sprites"));
    v
}

#[derive(Clone)]
struct CacheStats {
    loads: u64,
    hits: u64,
    misses: u64,
    missing: u64,
}

/// A decoded source sprite, and whether anything has asked for it since the
/// last time residency was reviewed.
struct Source {
    surf: ImageSurface,
    used: bool,
}

thread_local! {
    /// `None` records that a sprite is NOT on disk, so a missing asset does
    /// not get a failed `open` back in every frame that wants it.
    static BASE: std::cell::RefCell<HashMap<&'static str, Option<Source>>> =
        std::cell::RefCell::new(HashMap::new());
    static SCALED: std::cell::RefCell<IndexedLru> = std::cell::RefCell::new(IndexedLru::new());
    static STATS: std::cell::RefCell<CacheStats> = std::cell::RefCell::new(CacheStats {
        loads: 0, hits: 0, misses: 0, missing: 0,
    });
}

/// Cache key for a derived surface: nine-slice ("9") or stretched sprite ("s"),
/// with the hidpi scale folded in.
#[derive(Clone, PartialEq, Eq, Hash)]
enum ScaleKey {
    Nine(&'static str, i32, i32, i32, i32, i32, i32, i32),
    Sprite(&'static str, i32, i32, i32),
}

/// the hidpi scale folded into cache keys, in quarter steps
#[inline]
fn ds_key() -> i32 {
    (hidpi::scale() * 4.0) as i32
}

struct IndexedLru {
    map: HashMap<ScaleKey, ImageSurface>,
    order: VecDeque<ScaleKey>,
}

impl IndexedLru {
    fn new() -> Self {
        IndexedLru { map: HashMap::new(), order: VecDeque::new() }
    }

    fn get(&mut self, key: &ScaleKey) -> Option<ImageSurface> {
        if let Some(s) = self.map.get(key) {
            let s = s.clone();
            if let Some(pos) = self.order.iter().position(|k| k == key) {
                self.order.remove(pos);
            }
            self.order.push_back(key.clone());
            STATS.with_borrow_mut(|st| st.hits += 1);
            return Some(s);
        }
        None
    }

    fn put(&mut self, key: ScaleKey, surf: ImageSurface) {
        self.map.insert(key.clone(), surf);
        self.order.push_back(key);
        while self.order.len() > MAX_SCALED {
            if let Some(old) = self.order.pop_front() {
                self.map.remove(&old);
            }
        }
    }

    fn clear(&mut self) {
        self.map.clear();
        self.order.clear();
    }

    fn len(&self) -> usize {
        self.map.len()
    }
}

/// (sprites, bytes) held by the full-resolution source cache, and the same
/// for the derived-size LRU. Reading a cache's size changes no output.
pub fn cache_inventory() -> ((usize, usize), (usize, usize)) {
    fn bytes(s: &ImageSurface) -> usize {
        (s.stride() as usize) * (s.height() as usize)
    }
    let base = BASE.with_borrow(|m| {
        (
            m.values().flatten().count(),
            m.values().flatten().map(|e| bytes(&e.surf)).sum(),
        )
    });
    let scaled = SCALED.with_borrow(|l| (l.map.len(), l.map.values().map(bytes).sum()));
    (base, scaled)
}

/// The largest source sprites resident, biggest first, for the inventory.
pub fn base_cache_entries() -> Vec<(&'static str, i32, i32, usize)> {
    let mut v: Vec<_> = BASE.with_borrow(|m| {
        m.iter()
            .filter_map(|(k, v)| {
                v.as_ref().map(|e| {
                    (
                        *k,
                        e.surf.width(),
                        e.surf.height(),
                        (e.surf.stride() as usize) * (e.surf.height() as usize),
                    )
                })
            })
            .collect()
    });
    v.sort_by_key(|e| std::cmp::Reverse(e.3));
    v
}

// --------------------------------------------------------------------- load
// (asset-root cache)
thread_local! {
    static RESOLVED_ROOT: std::sync::OnceLock<PathBuf> = const { std::sync::OnceLock::new() };
}

/// Load `assets/sprites/<name>.png` once. None if absent.
///
/// A missing sprite is not fatal: the app must still run (and still be
/// testable) on a checkout where the asset pass has not been built.
pub fn sprite(name: &'static str) -> Option<ImageSurface> {
    // Resolve the asset root once, preferring a location that has the assets.
    let resolved = RESOLVED_ROOT.with(|r| {
        r.get_or_init(|| {
            for cand in sprite_roots() {
                if cand.join("frame/observation_bezel.png").is_file() {
                    return cand;
                }
            }
            sprite_roots().into_iter().next().unwrap_or_default()
        })
        .clone()
    });

    BASE.with_borrow_mut(|base| {
        if let Some(hit) = base.get_mut(name) {
            if let Some(src) = hit {
                src.used = true;
                return Some(src.surf.clone());
            }
            return None;
        }
        let path = resolved.join(format!("{name}.png"));
        let surf = std::fs::File::open(&path)
            .ok()
            .and_then(|mut file| ImageSurface::create_from_png(&mut file).ok());
        STATS.with_borrow_mut(|st| {
            if surf.is_some() {
                st.loads += 1;
            } else {
                st.missing += 1;
            }
        });
        base.insert(name, surf.clone().map(|s| Source { surf: s, used: true }));
        surf
    })
}

/// Same as `sprite`, for runtime-constructed names (lamp/mode/specimen keys).
pub fn sprite_dyn(name: &str) -> Option<ImageSurface> {
    match leak_name(name) {
        Some(s) => sprite(s),
        None => None,
    }
}

thread_local! {
    static NAME_POOL: std::cell::RefCell<HashMap<String, &'static str>> =
        std::cell::RefCell::new(HashMap::new());
}

/// Intern a dynamic sprite name so it can live in the `&'static str` caches.
fn leak_name(name: &str) -> Option<&'static str> {
    NAME_POOL.with_borrow_mut(|pool| {
        if let Some(s) = pool.get(name) {
            return Some(*s);
        }
        if !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '/' || c == '_') {
            return None; // never leak arbitrary bytes
        }
        let leaked: &'static str = Box::leak(name.to_string().into_boxed_str());
        pool.insert(name.to_string(), leaked);
        Some(leaked)
    })
}

pub fn sprite_size(name: &str) -> (i32, i32) {
    match sprite_dyn(name) {
        Some(s) => (s.width(), s.height()),
        None => (0, 0),
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SkinStats {
    pub loads: u64,
    pub hits: u64,
    pub misses: u64,
    pub missing: u64,
    pub cached: usize,
    pub loaded: usize,
}

pub fn skin_stats() -> SkinStats {
    STATS.with_borrow(|st| SkinStats {
        loads: st.loads,
        hits: st.hits,
        misses: st.misses,
        missing: st.missing,
        cached: SCALED.with_borrow(|s| s.len()),
        loaded: BASE.with_borrow(|b| b.values().flatten().count()),
    })
}

pub fn clear_cache() {
    SCALED.with_borrow_mut(|s| s.clear());
}

/// Hand back the source sprites nothing has asked for since the last review,
/// keeping every derived surface. Returns the bytes released.
///
/// A source sprite exists to be RENDERED INTO a derived size. Once a layout
/// has settled, what every frame blits is the `SCALED` entry, and the masters
/// behind it - `module/shell` is 1600x1172, 7.15 MB - are dead weight that is
/// never evicted and never shrinks. Measured at 781x468: 16 sources, 20.9 MB
/// resident, against 0.11 MB of derived surfaces actually in use.
///
/// It is a SECOND-CHANCE policy, not a flush, because a few sprites are drawn
/// straight from their source every time rather than through the derived
/// cache - the header fascia and the small lamps, traced live. Flushing those
/// too just makes the next second decode them again: measured as a 2.5 MB
/// sawtooth every 4 s, with a PNG decode inside a frame each time. A sprite
/// read since the last review keeps its place; one that was not is let go.
///
/// This changes no pixel. A miss re-reads the PNG, and the file on disk is
/// the source of truth, so this is a cache being returned to it.
pub fn release_unused_sources() -> usize {
    BASE.with_borrow_mut(|base| {
        let mut freed = 0usize;
        base.retain(|_, v| match v {
            // the record that a sprite is missing costs nothing and saves an
            // `open` per frame; it always stays
            None => true,
            Some(src) if src.used => {
                src.used = false;
                true
            }
            Some(src) => {
                freed += (src.surf.stride() as usize) * (src.surf.height() as usize);
                false
            }
        });
        freed
    })
}

/// Hand back every source sprite, used or not. The unconditional form, for
/// gates and for an inventory that wants to see the floor.
pub fn release_sources() -> usize {
    BASE.with_borrow_mut(|base| {
        let mut freed = 0usize;
        base.retain(|_, v| match v {
            None => true,
            Some(src) => {
                freed += (src.surf.stride() as usize) * (src.surf.height() as usize);
                false
            }
        });
        freed
    })
}


// ---------------------------------------------------------------- 9-slice
/// A panel plus the four insets that make it scale safely.
///
/// `pad_*` are the CONTENT insets measured in the SOURCE sprite; `content()`
/// maps them through the 9-slice rather than using them directly.
#[derive(Clone, Copy, Debug)]
pub struct NineSlice {
    pub name: &'static str,
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
    pub pad_l: f64,
    pub pad_t: f64,
    pub pad_r: f64,
    pub pad_b: f64,
}

impl NineSlice {
    pub const fn new(
        name: &'static str,
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
        pad_l: f64,
        pad_t: f64,
        pad_r: f64,
        pad_b: f64,
    ) -> NineSlice {
        NineSlice { name, left, top, right, bottom, pad_l, pad_t, pad_r, pad_b }
    }

    pub fn min_size(&self) -> (i32, i32) {
        (self.left + self.right + 1, self.top + self.bottom + 1)
    }

    /// The usable recess inside this panel drawn at (x, y, w, h).
    pub fn content(&self, x: f64, y: f64, w: f64, h: f64) -> (f64, f64, f64, f64) {
        let (sw, sh) = sprite_size(self.name);
        let (kx, ky) = self.shrink(w, h);
        let pl = map_pad(
            self.pad_l,
            self.left,
            sw as f64 - self.left as f64 - self.right as f64,
            self.left as f64 * kx,
            w - (self.left + self.right) as f64 * kx,
        );
        let pr = map_pad(
            self.pad_r,
            self.right,
            sw as f64 - self.left as f64 - self.right as f64,
            self.right as f64 * kx,
            w - (self.left + self.right) as f64 * kx,
        );
        let pt = map_pad(
            self.pad_t,
            self.top,
            sh as f64 - self.top as f64 - self.bottom as f64,
            self.top as f64 * ky,
            h - (self.top + self.bottom) as f64 * ky,
        );
        let pb = map_pad(
            self.pad_b,
            self.bottom,
            sh as f64 - self.top as f64 - self.bottom as f64,
            self.bottom as f64 * ky,
            h - (self.top + self.bottom) as f64 * ky,
        );
        (x + pl, y + pt, (w - pl - pr).max(0.0), (h - pt - pb).max(0.0))
    }

    /// Per-axis factor applied to BOTH the slice borders and the content pads.
    fn shrink(&self, w: f64, h: f64) -> (f64, f64) {
        let (sw, sh) = sprite_size(self.name);
        let (mut kx, mut ky) = if sw > 0 && sh > 0 {
            let k = (w / sw as f64).min(h / sh as f64);
            let k = k.max(MIN_BORDER_SCALE).min(1.0);
            (k, k)
        } else {
            (1.0, 1.0)
        };
        let lr = (self.left + self.right) as f64 * kx;
        let tb = (self.top + self.bottom) as f64 * ky;
        let max_lr = w * (1.0 - MIN_MIDDLE_SHARE);
        let max_tb = h * (1.0 - MIN_MIDDLE_SHARE);
        if lr > max_lr && lr > 0.0 {
            kx *= (0.0f64).max(max_lr / lr);
        }
        if tb > max_tb && tb > 0.0 {
            ky *= (0.0f64).max(max_tb / tb);
        }
        (kx, ky)
    }
}

/// Map one source-space content pad into drawn space through the 9-slice.
fn map_pad(pad: f64, inset: i32, src_mid: f64, dst_inset: f64, dst_mid: f64) -> f64 {
    let inset = inset as f64;
    if inset <= 0.0 {
        return pad;
    }
    if pad <= inset {
        return pad * (dst_inset / inset);
    }
    if src_mid <= 0.0 {
        return dst_inset;
    }
    dst_inset + (pad - inset) * (dst_mid / src_mid)
}

fn blit_region(
    cr: &Context,
    src: &ImageSurface,
    sx: f64,
    sy: f64,
    sw: f64,
    sh: f64,
    dx: f64,
    dy: f64,
    dw: f64,
    dh: f64,
) {
    if sw <= 0.0 || sh <= 0.0 || dw <= 0.0 || dh <= 0.0 {
        return;
    }
    let _ = cr.save();
    cr.rectangle(dx, dy, dw, dh);
    cr.clip();
    cr.translate(dx, dy);
    cr.scale(dw / sw, dh / sh);
    let _ = cr.set_source_surface(src, -sx, -sy);
    let pat = cr.source();
    pat.set_filter(Filter::Good);
    pat.set_extend(cairo::Extend::Pad);
    let _ = cr.paint();
    let _ = cr.restore();
}

fn render_nine(ns: &NineSlice, w: i32, h: i32) -> Option<ImageSurface> {
    let src = sprite(ns.name)?;
    let (sw, sh) = (src.width(), src.height());

    // Shrink borders (and, via the same factor, the content pads) so the
    // stretched middle never collapses and opposite corners never overlap.
    let (kx, ky) = ns.shrink(w as f64, h as f64);
    let l = (ns.left as f64 * kx) as i32;
    let r = (ns.right as f64 * kx) as i32;
    let t = (ns.top as f64 * ky) as i32;
    let b = (ns.bottom as f64 * ky) as i32;

    let out = hidpi::surface(w as f64, h as f64);
    let cr = Context::new(&out).ok()?;
    cr.set_operator(Operator::Source);

    let smx = sw - ns.left - ns.right;
    let smy = sh - ns.top - ns.bottom;
    let dmx = w - l - r;
    let dmy = h - t - b;

    let cols: [(f64, f64, f64, f64); 3] = [
        (0.0, ns.left as f64, 0.0, l as f64),
        (ns.left as f64, smx as f64, l as f64, dmx as f64),
        ((sw - ns.right) as f64, ns.right as f64, (w - r) as f64, r as f64),
    ];
    let rows: [(f64, f64, f64, f64); 3] = [
        (0.0, ns.top as f64, 0.0, t as f64),
        (ns.top as f64, smy as f64, t as f64, dmy as f64),
        ((sh - ns.bottom) as f64, ns.bottom as f64, (h - b) as f64, b as f64),
    ];

    for (sy, sh_, dy, dh_) in rows {
        for (sx, sw_, dx, dw_) in cols {
            blit_region(&cr, &src, sx, sy, sw_, sh_, dx, dy, dw_, dh_);
        }
    }
    out.flush();
    Some(out)
}

/// Cached 9-sliced panel at an exact integer size.
pub fn nine_surface(ns: &NineSlice, w: f64, h: f64) -> Option<ImageSurface> {
    let wi = w.round() as i32;
    let hi = h.round() as i32;
    if wi < 1 || hi < 1 {
        return None;
    }
    let key = ScaleKey::Nine(ns.name, ns.left, ns.top, ns.right, ns.bottom, wi, hi, ds_key());
    if let Some(hit) = SCALED.with_borrow_mut(|s| s.get(&key)) {
        return Some(hit);
    }
    STATS.with_borrow_mut(|st| st.misses += 1);
    let surf = render_nine(ns, wi, hi)?;
    SCALED.with_borrow_mut(|s| s.put(key, surf.clone()));
    Some(surf)
}

pub fn draw_nine(
    cr: &Context,
    ns: &NineSlice,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    alpha: f64,
) -> bool {
    let Some(surf) = nine_surface(ns, w, h) else {
        return false;
    };
    let _ = cr.save();
    cr.translate(x.round(), y.round());
    let _ = cr.set_source_surface(&surf, 0.0, 0.0);
    if alpha >= 0.999 {
        let _ = cr.paint();
    } else {
        let _ = cr.paint_with_alpha(alpha.max(0.0));
    }
    let _ = cr.restore();
    true
}

// ---------------------------------------------------------------- sprites
fn scaled_sprite(name: &'static str, w: i32, h: i32) -> Option<ImageSurface> {
    let key = ScaleKey::Sprite(name, w, h, ds_key());
    if let Some(hit) = SCALED.with_borrow_mut(|s| s.get(&key)) {
        return Some(hit);
    }
    let src = sprite(name)?;
    STATS.with_borrow_mut(|st| st.misses += 1);
    let out = hidpi::surface(w as f64, h as f64);
    let cr = Context::new(&out).ok()?;
    cr.set_operator(Operator::Source);
    cr.scale(
        w as f64 / src.width() as f64,
        h as f64 / src.height() as f64,
    );
    let _ = cr.set_source_surface(&src, 0.0, 0.0);
    cr.source().set_filter(Filter::Good);
    let _ = cr.paint();
    out.flush();
    SCALED.with_borrow_mut(|s| s.put(key, out.clone()));
    Some(out)
}

fn paint_stretched(cr: &Context, surf: &ImageSurface, alpha: f64) {
    let _ = cr.set_source_surface(surf, 0.0, 0.0);
    if alpha >= 0.999 {
        let _ = cr.paint();
    } else {
        let _ = cr.paint_with_alpha(alpha.max(0.0));
    }
}

/// Draw a sprite stretched to exactly w x h.
pub fn draw_sprite(
    cr: &Context,
    name: &str,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    alpha: f64,
) -> bool {
    let wi = w.round() as i32;
    let hi = h.round() as i32;
    if wi < 1 || hi < 1 {
        return false;
    }
    let Some(name_s) = leak_name(name) else { return false };
    let Some(surf) = scaled_sprite(name_s, wi, hi) else {
        return false;
    };
    let _ = cr.save();
    cr.translate(x.round(), y.round());
    paint_stretched(cr, &surf, alpha);
    let _ = cr.restore();
    true
}

/// Draw centred on (cx, cy) at `target_h`, PRESERVING ASPECT.
/// Returns the drawn (w, h).
pub fn draw_sprite_fit(
    cr: &Context,
    name: &str,
    cx: f64,
    cy: f64,
    target_h: f64,
    alpha: f64,
) -> (f64, f64) {
    let (sw, sh) = sprite_size(name);
    if sw == 0 || sh == 0 || target_h < 1.0 {
        return (0.0, 0.0);
    }
    let k = target_h / sh as f64;
    let w = sw as f64 * k;
    let h = target_h;
    draw_sprite(cr, name, cx - w / 2.0, cy - h / 2.0, w, h, alpha);
    (w, h)
}

/// Draw a sprite rotated a quarter turn, filling (x, y, w, h).
pub fn draw_sprite_rot90(
    cr: &Context,
    name: &str,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    alpha: f64,
) -> bool {
    let Some(name_s) = leak_name(name) else { return false };
    if sprite(name_s).is_none() || w < 1.0 || h < 1.0 {
        return false;
    }
    let _ = cr.save();
    cr.translate(x + w, y);
    cr.rotate(1.5707963267948966);
    // after the rotation the sprite's own width runs down the screen
    let Some(surf) = scaled_sprite(name_s, h.round() as i32, w.round() as i32) else {
        let _ = cr.restore();
        return false;
    };
    paint_stretched(cr, &surf, alpha);
    let _ = cr.restore();
    true
}

/// Largest aspect-preserving size of `name` fitting inside a box.
pub fn fit_box(name: &str, box_w: f64, box_h: f64) -> (f64, f64) {
    let (sw, sh) = sprite_size(name);
    if sw == 0 || sh == 0 {
        return (0.0, 0.0);
    }
    let k = (box_w / sw as f64).min(box_h / sh as f64);
    (sw as f64 * k, sh as f64 * k)
}
