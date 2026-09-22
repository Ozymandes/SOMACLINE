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

/// An exclusively-owned ARGB32 copy of `surf`, **carrying its device scale**.
///
/// cairo-rs refuses an exclusive data lend (`ImageSurface::data`) while any
/// other reference to a surface exists, and every console cache surface is
/// held by the renderer's cache, so a texture upload has to go through a copy.
///
/// The copy MUST keep the source's device scale. A cache surface is allocated
/// at device resolution with `set_device_scale(ds, ds)`, so cairo treats it as
/// a LOGICAL `w/ds x h/ds` image when it is used as a source: blitting it into
/// an unscaled surface of the same pixel size lands the whole picture in the
/// top-left `1/ds` of the copy and leaves the rest blank. With both scales
/// equal the transform is identity in device space and the copy is
/// byte-for-byte. At ds = 1.0 the two are indistinguishable, which is why this
/// has its own gate.
pub fn copy_exclusive(surf: &ImageSurface) -> ImageSurface {
    surf.flush();
    let (w, h) = (surf.width(), surf.height());
    let (dsx, dsy) = surf.device_scale();
    let copy = ImageSurface::create(cairo::Format::ARgb32, w, h).expect("copy surface");
    copy.set_device_scale(dsx, dsy);
    {
        let cr = cairo::Context::new(&copy).expect("copy context");
        cr.set_source_surface(surf, 0.0, 0.0).expect("copy source");
        cr.paint().expect("copy paint");
    }
    copy.flush();
    copy
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A device-scaled surface must survive the texture-upload copy intact.
    /// Without the device scale on the destination the picture shrinks by
    /// exactly `ds` and the live window renders six detached module fragments.
    #[test]
    fn copy_exclusive_preserves_device_scaled_content() {
        for ds in [1.0_f64, 1.5, 2.0] {
            set_scale(ds);
            let src = surface(100.0, 60.0);
            {
                // paint the full LOGICAL extent, plus a marker in each corner
                let cr = cairo::Context::new(&src).unwrap();
                cr.set_source_rgb(0.2, 0.4, 0.6);
                cr.paint().unwrap();
                cr.set_source_rgb(1.0, 0.0, 0.0);
                cr.rectangle(96.0, 56.0, 4.0, 4.0);
                cr.fill().unwrap();
            }
            let mut copy = copy_exclusive(&src);
            assert_eq!(copy.device_scale().0, ds, "ds {ds}: scale lost");
            let (w, h, stride) = (copy.width(), copy.height(), copy.stride());
            let data = copy.data().unwrap();
            // bottom-right DEVICE pixel must be the marker, not blank
            let off = (h as usize - 1) * stride as usize + (w as usize - 1) * 4;
            let (b, g, r, a) = (data[off], data[off + 1], data[off + 2], data[off + 3]);
            assert!(a > 250 && r > 200 && g < 40 && b < 40,
                    "ds {ds}: bottom-right is {r},{g},{b},{a} - content did not fill the copy");
        }
        set_scale(1.0);
    }
}
