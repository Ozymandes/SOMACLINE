//! Device scale for every derived-pixel cache. Port of skin/hidpi.py.
//!
//! Every cache in this program allocates through `surface()`, at device
//! resolution, and carries `scale()` in its key. The host sets the scale once
//! per frame from the real target. Headless use leaves it at 1.0.

use std::cell::Cell;

use cairo::ImageSurface;

thread_local! {
    static DS: Cell<f64> = const { Cell::new(1.0) };
}

/// Quantised to quarter steps so a fractional scale cannot churn caches.
pub fn set_scale(s: f64) {
    let q = (s * 4.0).round() / 4.0;
    DS.with(|d| d.set(q.clamp(1.0, 4.0)));
}

pub fn scale() -> f64 {
    DS.with(|d| d.get())
}

/// An ARGB32 surface of LOGICAL size w x h at device resolution.
pub fn surface(w: f64, h: f64) -> ImageSurface {
    let ds = scale();
    let s = ImageSurface::create(
        cairo::Format::ARgb32,
        ((w * ds).ceil() as i32).max(1),
        ((h * ds).ceil() as i32).max(1),
    )
    .expect("hidpi surface");
    s.set_device_scale(ds, ds);
    s
}

/// The device scale of a context's target (1.0 if it has none).
pub fn from_context(cr: &cairo::Context) -> f64 {
    cr.target().device_scale().0
}
