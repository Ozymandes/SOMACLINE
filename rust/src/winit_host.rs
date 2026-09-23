//! ABYSSAL ORGANISM MONITOR — winit + softbuffer host.
//!
//! The same machine as `app.rs` drives, with GTK removed. This file owns
//! exactly what a host must own and nothing else: the event loop, the window,
//! the frame pacer that `GdkFrameClock` used to provide, the two glib timers
//! (telemetry at 5 Hz, probe at 2 s), and the translation of winit's input
//! vocabulary into the one `Core` already speaks. Every decision it makes is
//! made by `Core`, identically to the GTK host.
//!
//! ## Coordinate contract
//!
//! winit reports PHYSICAL pixels and a floating scale factor; GTK reported an
//! integer LOGICAL size. Everything above the host - layout, hit testing, the
//! console - is logical, so the conversion happens once, here, in `metrics()`:
//!
//! ```text
//! logical = round(physical / scale_factor)      (matches GTK's integer size)
//! ds      = physical / logical                  (per axis, EXACT)
//! ```
//!
//! The target surface then carries `ds` and every draw is logical, so nothing
//! can be cropped or stretched by a rounding disagreement. `hidpi::set_scale`
//! separately receives the RAW scale factor, exactly as the GTK host passed it
//! `gdk_surface_get_scale()`, and quantises it for the caches on its own.

use std::num::NonZeroU32;
use std::rc::Rc;
use std::time::{Duration, Instant};

use winit::application::ApplicationHandler;
use winit::dpi::{LogicalSize, PhysicalPosition};
use winit::event::{ElementState, MouseButton, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::keyboard::{Key, NamedKey};
use winit::window::{Fullscreen, Window, WindowId};

use crate::host::{Cmd, Core, DeviceLayers, Options, Target, APP_ID, TELEMETRY_HZ};
use crate::present::{FramePresenter, SoftbufferPresenter};
use crate::skin::hidpi;
use crate::ui::fonts;

/// Probe cadence, matching the GTK host's 2 s glib timeout.
const PROBE_INTERVAL: Duration = Duration::from_secs(2);

/// How long to wait for a requested frame before concluding that nothing is
/// looking. On Wayland, winit defers `RedrawRequested` to the compositor's
/// frame callback, and an occluded or unmapped surface receives no callbacks
/// at all - that silence IS the occlusion signal, and it is what GTK read
/// explicitly from `xdg_toplevel`'s SUSPENDED state. 250 ms is far longer
/// than a refresh (16.7 ms at 60 Hz) and short enough to resume instantly.
const FRAME_STALL: Duration = Duration::from_millis(250);

/// The resolved geometry of one frame: logical size for the machine, physical
/// size for the buffer, and the exact per-axis scale that ties them together.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Metrics {
    pub phys_w: u32,
    pub phys_h: u32,
    pub logical_w: f64,
    pub logical_h: f64,
    pub ds_x: f64,
    pub ds_y: f64,
    /// The window's raw scale factor, as the compositor reports it.
    pub scale: f64,
}

/// The one place physical and logical are reconciled. Pure, so it is gated by
/// a test at every scale the app is expected to meet.
pub fn metrics(phys_w: u32, phys_h: u32, scale: f64) -> Metrics {
    let scale = if scale.is_finite() && scale > 0.0 { scale } else { 1.0 };
    let phys_w = phys_w.max(1);
    let phys_h = phys_h.max(1);
    let logical_w = (phys_w as f64 / scale).round().max(1.0);
    let logical_h = (phys_h as f64 / scale).round().max(1.0);
    Metrics {
        phys_w,
        phys_h,
        logical_w,
        logical_h,
        // EXACT, not the nominal scale: logical*ds must land on the last
        // physical pixel or the frame is cropped or fringed.
        ds_x: phys_w as f64 / logical_w,
        ds_y: phys_h as f64 / logical_h,
        scale,
    }
}

impl Metrics {
    pub fn target(&self) -> Target {
        Target {
            logical_w: self.logical_w,
            logical_h: self.logical_h,
            phys_w: self.phys_w as i32,
            phys_h: self.phys_h as i32,
            ds_x: self.ds_x,
            ds_y: self.ds_y,
            scale: self.scale,
        }
    }
}

/// GDK's name for a winit key, so `Core::key_command` needs only one table.
fn gdk_key_name(key: &Key) -> Option<String> {
    match key {
        Key::Named(n) => Some(
            match n {
                NamedKey::ArrowLeft => "Left",
                NamedKey::ArrowRight => "Right",
                NamedKey::ArrowUp => "Up",
                NamedKey::ArrowDown => "Down",
                NamedKey::Escape => "Escape",
                NamedKey::F1 => "F1",
                NamedKey::F2 => "F2",
                NamedKey::F11 => "F11",
                _ => return None,
            }
            .to_string(),
        ),
        Key::Character(s) => Some(s.to_string()),
        _ => None,
    }
}

struct App {
    core: Core,
    opts: Options,
    epoch: Instant,
    window: Option<Rc<Window>>,
    presenter: Option<SoftbufferPresenter>,
    metrics: Option<Metrics>,
    focused: bool,
    occluded: bool,
    fullscreen: bool,
    cursor: Option<PhysicalPosition<f64>>,
    dev: DeviceLayers,
    /// When the frame currently in flight was asked for, if any.
    frame_pending: Option<Instant>,
    next_telemetry: Instant,
    next_probe: Instant,
    quit_at: Option<Instant>,
    exit_code: i32,
}

impl App {
    fn new(opts: Options) -> App {
        // Display faces must exist in a scanned font directory before the
        // first Pango font map is built (ui/fonts.py contract). Core::new
        // builds the Renderer, which calls chrome::init(), so this is the
        // last moment - same ordering as the GTK host's build_window.
        fonts::ensure_user_fonts();
        let now = Instant::now();
        let quit_at = if opts.quit_after > 0.0 {
            Some(now + Duration::from_secs_f64(opts.quit_after))
        } else {
            None
        };
        App {
            core: Core::new(opts.clone()),
            opts,
            epoch: now,
            window: None,
            presenter: None,
            metrics: None,
            focused: true,
            occluded: false,
            fullscreen: false,
            cursor: None,
            dev: DeviceLayers::new(),
            frame_pending: None,
            next_telemetry: now,
            next_probe: now + PROBE_INTERVAL,
            quit_at,
            exit_code: 0,
        }
    }

    fn now_us(&self) -> f64 {
        self.epoch.elapsed().as_micros() as f64
    }

    /// Ask for a frame and remember that one is in flight.
    fn request_frame(&mut self) {
        if let Some(w) = self.window.as_ref() {
            w.request_redraw();
            if self.frame_pending.is_none() {
                self.frame_pending = Some(Instant::now());
            }
        }
    }

    /// Re-read the window's size and scale, and match the presenter to them.
    fn sync_metrics(&mut self) {
        let Some(window) = self.window.as_ref() else { return };
        let size = window.inner_size();
        let m = metrics(size.width, size.height, window.scale_factor());
        if self.metrics == Some(m) {
            return;
        }
        self.metrics = Some(m);
        if let (Some(p), Some(w), Some(h)) = (
            self.presenter.as_mut(),
            NonZeroU32::new(m.phys_w),
            NonZeroU32::new(m.phys_h),
        ) {
            if let Err(e) = p.resize(w, h) {
                eprintln!("abyssal: {e}");
            }
        }
    }

    fn obey(&mut self, cmd: Cmd, event_loop: &ActiveEventLoop) {
        match cmd {
            Cmd::Ignored => {}
            Cmd::Redraw => self.request_frame(),
            Cmd::Quit => event_loop.exit(),
            Cmd::Fullscreen => {
                self.fullscreen = !self.fullscreen;
                if let Some(w) = self.window.as_ref() {
                    w.set_fullscreen(if self.fullscreen {
                        Some(Fullscreen::Borderless(None))
                    } else {
                        None
                    });
                }
            }
        }
    }

    fn redraw(&mut self) {
        self.frame_pending = None;
        self.sync_metrics();
        let (Some(m), Some(window), Some(presenter)) = (
            self.metrics,
            self.window.as_ref(),
            self.presenter.as_mut(),
        ) else {
            return;
        };
        // The caches quantise the RAW scale factor, exactly as under GTK.
        hidpi::set_scale(m.scale);
        let core = &mut self.core;
        let dev = &mut self.dev;
        let target = m.target();
        // Tell the compositor a frame is coming, so it can time its own.
        window.pre_present_notify();
        let res = presenter.present(m.ds_x, m.ds_y, &mut |cr, age| {
            core.compose_damaged(cr, target, dev, age)
        });
        if let Err(e) = res {
            eprintln!("abyssal: {e}");
        }
    }
}

impl ApplicationHandler for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let mut attrs = Window::default_attributes()
            .with_title("Abyssal Organism Monitor")
            .with_inner_size(LogicalSize::new(
                self.opts.width as f64,
                self.opts.height as f64,
            ));
        // The app id is what Hyprland rules and the QA harness match on; the
        // GTK host got it from its GApplication id.
        #[cfg(all(unix, not(any(target_os = "macos", target_os = "android"))))]
        {
            use winit::platform::wayland::WindowAttributesExtWayland;
            attrs = WindowAttributesExtWayland::with_name(attrs, APP_ID, "abyssal");
        }
        let window = match event_loop.create_window(attrs) {
            Ok(w) => Rc::new(w),
            Err(e) => {
                eprintln!("abyssal: cannot create window: {e}");
                self.exit_code = 1;
                event_loop.exit();
                return;
            }
        };
        match SoftbufferPresenter::new(window.clone()) {
            Ok(p) => self.presenter = Some(p),
            Err(e) => {
                eprintln!("abyssal: {e}");
                self.exit_code = 1;
                event_loop.exit();
                return;
            }
        }
        self.window = Some(window);
        self.sync_metrics();
    }

    fn window_event(
        &mut self,
        event_loop: &ActiveEventLoop,
        _id: WindowId,
        event: WindowEvent,
    ) {
        match event {
            WindowEvent::CloseRequested | WindowEvent::Destroyed => event_loop.exit(),
            WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. } => {
                self.sync_metrics();
                self.request_frame();
            }
            // The compositor may show a surface it has not been given a new
            // frame for, so nothing about the previous frame survives being
            // uncovered: start again from a whole one.
            WindowEvent::Occluded(false) => {
                self.occluded = false;
                self.dev.forget_history();
                self.request_frame();
            }
            WindowEvent::Focused(f) => self.focused = f,
            WindowEvent::Occluded(o) => {
                self.occluded = o;
                if o {
                    self.frame_pending = None;
                }
            }
            WindowEvent::RedrawRequested => self.redraw(),
            WindowEvent::CursorMoved { position, .. } => {
                self.cursor = Some(position);
                let Some(m) = self.metrics else { return };
                let cmd = self
                    .core
                    .motion(position.x / m.ds_x, position.y / m.ds_y);
                self.obey(cmd, event_loop);
            }
            WindowEvent::CursorLeft { .. } => {
                self.cursor = None;
                let cmd = self.core.leave();
                self.obey(cmd, event_loop);
            }
            WindowEvent::MouseInput { state, button, .. } => {
                if state != ElementState::Pressed || button != MouseButton::Left {
                    return;
                }
                let (Some(m), Some(pos)) = (self.metrics, self.cursor) else { return };
                let cmd = self.core.click(pos.x / m.ds_x, pos.y / m.ds_y);
                self.obey(cmd, event_loop);
            }
            WindowEvent::KeyboardInput { event, .. } => {
                if event.state != ElementState::Pressed {
                    return;
                }
                let Some(name) = gdk_key_name(&event.logical_key) else { return };
                let cmd = self.core.key_command(&name);
                self.obey(cmd, event_loop);
            }
            _ => {}
        }
    }

    /// The frame pacer and the two timers GTK got from glib.
    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        let now = Instant::now();
        if let Some(deadline) = self.quit_at {
            if now >= deadline {
                event_loop.exit();
                return;
            }
        }
        // /proc and /sys are read here and ONLY here. This keeps running while
        // the surface is occluded, exactly as the glib timeout did, so the
        // 60 s graphs have no hole when the window comes back.
        let tel_interval = Duration::from_secs_f64(1.0 / TELEMETRY_HZ);
        if now >= self.next_telemetry {
            self.core.sample_telemetry();
            self.next_telemetry = now + tel_interval;
        }
        if now >= self.next_probe {
            self.core.probe_sample();
            self.next_probe = now + PROBE_INTERVAL;
        }

        let mut wake = self.next_telemetry.min(self.next_probe);

        // Never run ahead of the compositor. While a frame is in flight the
        // simulation waits, which is what couples the loop to the display's
        // refresh; and when the surface is not being shown, no frame callback
        // ever arrives, so the loop settles into one no-op wakeup every
        // FRAME_STALL. That silence is this host's SUSPENDED signal.
        match self.frame_pending {
            Some(since) if since.elapsed() < FRAME_STALL => {
                wake = wake.min(since + FRAME_STALL);
            }
            Some(_) => {
                // Stalled: ask again, but advance NOTHING. GTK's suspended
                // window simulated nothing either, and `due` clamps the dt
                // when the frames come back, so the organism cannot teleport.
                self.frame_pending = None;
                self.request_frame();
                wake = wake.min(now + FRAME_STALL);
            }
            None => {
                let fps = self.core.cadence_now(self.focused, self.occluded);
                if let Some(dt) = self.core.due(self.now_us(), fps) {
                    self.core.advance(dt);
                    self.request_frame();
                }
                if fps > 0.0 {
                    let ahead = self.core.next_frame_us() - self.now_us();
                    wake = wake.min(now + Duration::from_micros(ahead.max(0.0) as u64));
                }
            }
        }
        if let Some(q) = self.quit_at {
            wake = wake.min(q);
        }
        event_loop.set_control_flow(ControlFlow::WaitUntil(wake));
    }
}

/// Entry point: `abyssal::winit_host::run(&args)`.
pub fn run(args: &[String]) -> i32 {
    let opts = Options::parse(args);
    let event_loop = match EventLoop::new() {
        Ok(e) => e,
        Err(e) => {
            eprintln!("abyssal: cannot start event loop: {e}");
            return 1;
        }
    };
    event_loop.set_control_flow(ControlFlow::Wait);
    let mut app = App::new(opts);
    if let Err(e) = event_loop.run_app(&mut app) {
        eprintln!("abyssal: event loop: {e}");
        return 1;
    }
    app.exit_code
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Logical*ds must land EXACTLY on the last physical pixel, at every
    /// scale the app is expected to meet - including the fractional ones
    /// Hyprland hands out. A disagreement here is the whole cropped/stretched
    /// bug class, and it is invisible at scale 1.0.
    #[test]
    fn metrics_map_logical_onto_the_whole_buffer() {
        for (lw, lh) in [(900u32, 700u32), (600, 520), (1400, 880), (781, 468)] {
            for scale in [1.0_f64, 1.25, 1.5, 1.6, 1.75, 2.0] {
                let pw = (lw as f64 * scale).round() as u32;
                let ph = (lh as f64 * scale).round() as u32;
                let m = metrics(pw, ph, scale);
                assert_eq!(
                    (m.logical_w, m.logical_h),
                    (lw as f64, lh as f64),
                    "scale {scale}: logical size not recovered from {pw}x{ph}"
                );
                assert!(
                    (m.logical_w * m.ds_x - m.phys_w as f64).abs() < 1e-9
                        && (m.logical_h * m.ds_y - m.phys_h as f64).abs() < 1e-9,
                    "scale {scale}: logical*ds misses the buffer edge"
                );
                assert!(
                    (m.ds_x - scale).abs() < 0.01 && (m.ds_y - scale).abs() < 0.01,
                    "scale {scale}: exact ds {:.4}x{:.4} drifted from nominal",
                    m.ds_x, m.ds_y
                );
            }
        }
    }

    #[test]
    fn metrics_survive_degenerate_input() {
        let m = metrics(0, 0, 0.0);
        assert!(m.phys_w >= 1 && m.phys_h >= 1 && m.logical_w >= 1.0 && m.ds_x > 0.0);
        let m = metrics(100, 100, f64::NAN);
        assert_eq!(m.scale, 1.0);
    }

    #[test]
    fn winit_keys_speak_gdk_names() {
        assert_eq!(gdk_key_name(&Key::Named(NamedKey::ArrowRight)).as_deref(), Some("Right"));
        assert_eq!(gdk_key_name(&Key::Named(NamedKey::Escape)).as_deref(), Some("Escape"));
        assert_eq!(gdk_key_name(&Key::Named(NamedKey::F11)).as_deref(), Some("F11"));
        assert_eq!(gdk_key_name(&Key::Character("n".into())).as_deref(), Some("n"));
        assert_eq!(gdk_key_name(&Key::Character("3".into())).as_deref(), Some("3"));
        assert!(gdk_key_name(&Key::Named(NamedKey::Tab)).is_none());
    }
}
