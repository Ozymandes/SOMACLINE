//! The presentation boundary: composed CPU frame -> screen.
//!
//! The renderer above this line produces a cairo frame in LOGICAL coordinates
//! and knows nothing about how it is shown. Everything below it is the host's
//! pixel plumbing. Keeping the seam explicit is what leaves room for a wgpu
//! presenter later without the machine noticing; it is deliberately the
//! smallest thing that can hold that line - three methods, no framework.
//!
//! ## The device-scale contract (read before touching this file)
//!
//! Three scales are in play and confusing any two of them is the bug class
//! that malformed the GTK window once already:
//!
//! - **the window's scale factor** (e.g. 1.6 under Hyprland fractional
//!   scaling) - what the compositor asks for;
//! - **the cache scale**, `skin::hidpi::scale()`, which QUANTISES that to
//!   quarter steps (1.6 -> 1.5) so a fractional scale cannot churn the layer
//!   caches. Every cached surface is rendered at this scale and carries it as
//!   its own cairo device scale;
//! - **the target's device scale**, set here, which must be exactly
//!   `physical / logical` per axis so that logical coordinates land on the
//!   real pixel grid with no cropping and no stretching.
//!
//! Under GTK, GSK reconciled the second and third on the GPU for free when it
//! drew a 1.5-scale texture into a 1.6 surface. Softbuffer has no GPU, so
//! cairo resamples on the CPU instead - correct, and measurably not free.

use std::num::NonZeroU32;

use cairo::Context;

/// What a host must be able to do with a composed frame.
pub trait FramePresenter {
    /// Match the presenter to a new physical size, in device pixels.
    fn resize(&mut self, width: NonZeroU32, height: NonZeroU32) -> Result<(), String>;

    /// The current physical size, in device pixels.
    fn dimensions(&self) -> (u32, u32);

    /// Compose one frame and put it on the screen.
    ///
    /// `draw` receives a cairo context whose target is the presentation buffer
    /// itself, carrying device scale `(ds_x, ds_y)` - so the callback draws in
    /// LOGICAL coordinates exactly as the offscreen renderer does.
    fn present(
        &mut self,
        ds_x: f64,
        ds_y: f64,
        draw: &mut dyn FnMut(&Context),
    ) -> Result<(), String>;
}

#[cfg(feature = "winit-host")]
mod soft {
    use super::*;
    use std::rc::Rc;

    use cairo::{Format, ImageSurface};

    use softbuffer::{Context as SbContext, Surface as SbSurface};
    use winit::window::Window;

    type Win = Rc<Window>;

    /// Softbuffer presenter: cairo composes straight into the shared-memory
    /// buffer the compositor will read.
    ///
    /// There is exactly ONE copy of the frame in the whole path - the one
    /// softbuffer performs inside `present()` when it hands the buffer over.
    /// The composed pixels are never staged in an intermediate surface, which
    /// is the whole point of `create_for_data_unsafe` below.
    pub struct SoftbufferPresenter {
        surface: SbSurface<Win, Win>,
        width: u32,
        height: u32,
    }

    impl SoftbufferPresenter {
        pub fn new(window: Win) -> Result<SoftbufferPresenter, String> {
            let context = SbContext::new(window.clone())
                .map_err(|e| format!("softbuffer context: {e}"))?;
            let surface = SbSurface::new(&context, window)
                .map_err(|e| format!("softbuffer surface: {e}"))?;
            // The Context is only needed to build the Surface; softbuffer's
            // Surface owns what it needs from it.
            Ok(SoftbufferPresenter {
                surface,
                width: 0,
                height: 0,
            })
        }
    }

    impl FramePresenter for SoftbufferPresenter {
        fn resize(&mut self, width: NonZeroU32, height: NonZeroU32) -> Result<(), String> {
            if (width.get(), height.get()) == (self.width, self.height) {
                return Ok(());
            }
            self.surface
                .resize(width, height)
                .map_err(|e| format!("softbuffer resize: {e}"))?;
            self.width = width.get();
            self.height = height.get();
            Ok(())
        }

        fn dimensions(&self) -> (u32, u32) {
            (self.width, self.height)
        }

        fn present(
            &mut self,
            ds_x: f64,
            ds_y: f64,
            draw: &mut dyn FnMut(&Context),
        ) -> Result<(), String> {
            let (w, h) = (self.width, self.height);
            if w == 0 || h == 0 {
                return Ok(());
            }
            let stride = Format::ARgb32
                .stride_for_width(w)
                .map_err(|e| format!("cairo stride: {e}"))?;
            debug_assert_eq!(stride as u32, w * 4, "ARGB32 stride must be 4 bytes/px");

            let mut buffer = self
                .surface
                .buffer_mut()
                .map_err(|e| format!("softbuffer buffer: {e}"))?;
            debug_assert_eq!(buffer.len() as u32, w * h);

            {
                // SAFETY: `surf` borrows `buffer`'s pixels for the length of
                // this block only. The ImageSurface and its Context are both
                // dropped before `buffer` is presented or released, the size
                // and stride are exactly the ones softbuffer allocated, and
                // softbuffer's u32-per-pixel little-endian 0RGB layout is
                // byte-identical to cairo's native-endian ARGB32 for an
                // opaque frame (the composition clears to opaque black).
                let ptr = buffer.as_mut_ptr() as *mut u8;
                let surf = unsafe {
                    ImageSurface::create_for_data_unsafe(
                        ptr,
                        Format::ARgb32,
                        w as i32,
                        h as i32,
                        stride,
                    )
                }
                .map_err(|e| format!("cairo surface over buffer: {e}"))?;
                surf.set_device_scale(ds_x, ds_y);
                {
                    let cr = Context::new(&surf)
                        .map_err(|e| format!("cairo context: {e}"))?;
                    draw(&cr);
                }
                surf.flush();
            }

            buffer
                .present()
                .map_err(|e| format!("softbuffer present: {e}"))
        }
    }
}

#[cfg(feature = "winit-host")]
pub use soft::SoftbufferPresenter;
