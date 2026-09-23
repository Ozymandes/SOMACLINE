//! Host-neutral application core. Extracted verbatim from `app.rs` (the GTK
//! host) so that a second host can drive the same machine.
//!
//! This module knows nothing about GTK, winit, or how pixels reach a screen.
//! It owns the simulation, the console model, the caches, the instrumentation
//! and the *meaning* of every input; a host owns the window, the clock and the
//! presentation. The two GTK-only responsibilities that used to live in `Core`
//! - the `GdkTexture` upload cache and "is this window active/suspended" - are
//! now the host's.

use std::cell::RefCell;
use std::io::Write;
use std::rc::Rc;
use std::time::Instant;

use cairo::Context;

use crate::layout::{self, Layout, LayoutState, Rect};
use crate::lighting::LightField;
use crate::physiology::PhysiologyModel;
use crate::render::RenderCaches;
use crate::signals::Telemetry;
use crate::species;
use crate::telemetry::{History, TelemetrySource};
use crate::render;
use crate::ui::console::{self, CtlKind, ConsoleModel, Layer, Renderer};
use crate::ui::debug::{self, DebugInfo};
use crate::viewport::{self, Viewport};

pub const APP_ID: &str = "dev.abyssal.OrganismMonitor";
/// Telemetry cadence. /proc and /sys are read here and ONLY here - never at
/// render rate. 5 Hz gives every 60-second graph 300 samples.
pub const TELEMETRY_HZ: f64 = 5.0;
pub const TELEMETRY_INTERVAL_MS: u64 = (1000.0 / TELEMETRY_HZ) as u64;

/// Adaptive draw rate: 60 FPS focused, 30 visible but unfocused, nothing
/// while the compositor reports the window suspended.
pub const FPS_FOCUSED: f64 = 60.0;
pub const FPS_UNFOCUSED: f64 = 30.0;

/// How long a selector key stays visibly depressed after a click, in seconds.
pub const PRESS_FEEDBACK_S: f64 = 0.13;

/// How often residency is reviewed, and therefore how long a source sprite
/// nothing is asking for survives.
///
/// The sources exist to RENDER the derived sizes; once a layout has settled,
/// every frame blits the derived surface and the masters behind it are dead
/// weight - measured at 781x468: 16 sources, 20.9 MB resident, against
/// 0.11 MB of derived surfaces actually in use. Releasing them, and returning
/// the arena they were pinning, takes PSS from 57.9 MB to 28.1 MB headless.
///
/// It is not free. The next layout change pays to decode them again: a resize
/// frame goes from 56 ms (it already rebuilds both full-window layers) to
/// 161 ms, once, at the start of a drag. Four seconds is long enough that a
/// drag, a specimen sweep or a fullscreen toggle never pays it twice, and
/// short enough that a window left alone gives the memory back promptly.
pub const SETTLE_S: f64 = 4.0;

/// glibc's "give the free pages back". Releasing the sprite sources frees
/// blocks all over the arena; without this the arena keeps the pages and the
/// process looks exactly as large as it did before. Measured: with the
/// sources still held it is worth 0.9 MB, with them released, 10.7 MB.
#[cfg(target_env = "gnu")]
fn trim_heap() {
    extern "C" {
        fn malloc_trim(pad: usize) -> i32;
    }
    // SAFETY: malloc_trim only returns already-free pages to the kernel. It
    // touches no live allocation and has no effect other than on residency.
    unsafe {
        malloc_trim(0);
    }
}

#[cfg(not(target_env = "gnu"))]
fn trim_heap() {}

/// The only things an input can ask of a host. Every semantic decision is
/// taken inside `Core`; the host merely carries these out in its own idiom.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cmd {
    /// Nothing changed; the host need not do anything.
    Ignored,
    /// State changed: schedule a draw.
    Redraw,
    /// Close the window / quit the application.
    Quit,
    /// Toggle fullscreen.
    Fullscreen,
}

/// Parsed command line (mirrors app.py's argparse).
#[derive(Clone, Debug)]
pub struct Options {
    pub width: i32,
    pub height: i32,
    pub seed: u64,
    pub specimen: usize,
    pub debug: bool,
    pub calibration: bool,
    pub probe: Option<String>,
    pub quit_after: f64,
    pub qa_temp: f64,
    pub fps_cap: f64,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            width: 900,
            height: 700,
            seed: 20260920,
            specimen: 0,
            debug: false,
            calibration: false,
            probe: None,
            quit_after: 0.0,
            qa_temp: 0.0,
            fps_cap: 0.0,
        }
    }
}

impl Options {
    pub fn parse(args: &[String]) -> Options {
        let mut o = Options::default();
        let mut it = args.iter();
        while let Some(a) = it.next() {
            let mut val = || it.next().cloned().unwrap_or_default();
            match a.as_str() {
                "--width" => o.width = val().parse().unwrap_or(900),
                "--height" => o.height = val().parse().unwrap_or(700),
                "--seed" => o.seed = val().parse().unwrap_or(20260920),
                "--specimen" => o.specimen = val().parse().unwrap_or(0),
                "--debug" => o.debug = true,
                "--calibration" => o.calibration = true,
                "--probe" => o.probe = Some(val()),
                "--quit-after" => o.quit_after = val().parse().unwrap_or(0.0),
                "--qa-temp" => o.qa_temp = val().parse().unwrap_or(0.0),
                "--fps-cap" => o.fps_cap = val().parse().unwrap_or(0.0),
                _ => {}
            }
        }
        o
    }
}

/// The core simulation + presentation state. One instance, owned by the host.
pub struct Core {
    pub opts: Options,
    pub t0: Instant,

    // --- simulation state ---
    /// One organism per specimen, built lazily and then kept for the life of
    /// the process. A switch selects an existing instance, never constructs.
    pub organisms: Vec<Option<crate::mathforms::SourceBody>>,
    pub species_index: usize,
    pub phys_model: PhysiologyModel,
    pub telemetry_src: TelemetrySource,
    pub telemetry: Telemetry,

    // --- console presentation state ---
    pub model: ConsoleModel,
    pub renderer: Renderer,
    pub rcaches: RenderCaches,
    pub light: LightField,
    pub press_until: f64,
    pub press_index: Option<usize>,
    pub switches: usize,

    // --- instrumentation ---
    pub frames: u64,
    pub resizes: u64,
    pub rebuilds: u64, // must stay 0 forever; nothing rebuilds
    pub last_size: (i32, i32),
    pub last_state: Option<LayoutState>,
    pub fps: f64,
    pub frame_ms: f64,
    pub sim_ms: f64,
    pub draw_ms: f64,
    pub tel_ms: f64,
    pub cadence: f64,
    pub next_us: f64,
    pub draw_acc: f64,
    pub draw_avg: f64,
    pub fps_accum: u64,
    pub fps_t0: Instant,
    pub last_tick_us: f64,
    pub show_debug: bool,
    pub show_calibration: bool,
    pub probe: Option<std::fs::File>,

    pub last_layout: Option<Layout>,
    pub last_vp: Option<Viewport>,

    // --- residency ---
    /// When residency is next reviewed. See `SETTLE_S`.
    settle_at: Option<f64>,
}

impl Core {
    pub fn new(opts: Options) -> Core {
        let n = species::COUNT;
        let idx = opts.specimen.min(n - 1);
        let diag = opts.debug;
        let calib = opts.calibration;
        let seed = opts.seed;
        let history = Rc::new(RefCell::new(History::new(TELEMETRY_HZ)));
        let mut organisms: Vec<Option<crate::mathforms::SourceBody>> =
            (0..n).map(|_| None).collect();
        organisms[idx] = Some(crate::mathforms::SourceBody::build(
            species::by_index(idx).source,
            seed + idx as u64,
        ));
        let probe_file = opts.probe.as_ref().and_then(|p| {
            std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(p)
                .ok()
        });
        let model = ConsoleModel::new(
            species::by_index(idx),
            idx,
            history.clone(),
            diag,
        );
        Core {
            opts,
            t0: Instant::now(),
            organisms,
            species_index: idx,
            phys_model: PhysiologyModel::new(),
            telemetry_src: TelemetrySource::new(),
            telemetry: Telemetry::new(),
            model,
            renderer: Renderer::new(),
            rcaches: RenderCaches::new(),
            light: LightField::new(),
            press_until: 0.0,
            press_index: None,
            switches: 0,
            frames: 0,
            resizes: 0,
            rebuilds: 0,
            last_size: (0, 0),
            last_state: None,
            fps: 0.0,
            frame_ms: 0.0,
            sim_ms: 0.0,
            draw_ms: 0.0,
            tel_ms: 0.0,
            cadence: FPS_FOCUSED,
            next_us: 0.0,
            draw_acc: 0.0,
            draw_avg: 0.0,
            fps_accum: 0,
            fps_t0: Instant::now(),
            last_tick_us: 0.0,
            show_debug: diag,
            show_calibration: calib,
            probe: probe_file,
            last_layout: None,
            last_vp: None,
            settle_at: None,
        }
    }

    pub fn now(&self) -> f64 {
        self.t0.elapsed().as_secs_f64()
    }

    pub fn organism(&mut self, i: usize) -> &mut crate::mathforms::SourceBody {
        let seed = self.opts.seed;
        self.organisms[i].get_or_insert_with(|| {
            crate::mathforms::SourceBody::build(species::by_index(i).source, seed + i as u64)
        })
    }

    /// Switch channels. Never resets the clock or the telemetry history.
    pub fn select_specimen(&mut self, i: usize, tactile: bool) {
        let i = i % species::COUNT;
        if tactile {
            self.press_index = Some(i);
            self.press_until = self.now() + PRESS_FEEDBACK_S;
        }
        if i != self.species_index {
            self.species_index = i;
            self.organism(i);
            self.model.species = species::by_index(i);
            self.model.active = i;
            self.switches += 1;
            self.model.switches = self.switches;
        }
    }

    pub fn cycle_specimen(&mut self, step: isize) {
        let i = (self.species_index as isize + step).rem_euclid(species::COUNT as isize);
        self.select_specimen(i as usize, false);
    }

    fn cycle_mode(&mut self) {
        let order = ["inactive", "armed", "active", "error"];
        let cur = order
            .iter()
            .position(|s| *s == self.model.mode_state)
            .unwrap_or(0);
        self.model.mode_state = order[(cur + 1) % order.len()].to_string();
    }

    /// Target frames per second for the current window state; 0 = idle.
    /// `active` is "has focus", `suspended` is "the compositor says nothing is
    /// looking at this surface".
    pub fn cadence_now(&self, active: bool, suspended: bool) -> f64 {
        if suspended {
            return 0.0;
        }
        if self.opts.fps_cap > 0.0 {
            return self.opts.fps_cap;
        }
        if active {
            FPS_FOCUSED
        } else {
            FPS_UNFOCUSED
        }
    }

    pub fn log_probe(&mut self, layout: &Layout, vp: &Viewport, event: &str) {
        let Some(f) = self.probe.as_mut() else { return };
        let org_time = self
            .organisms
            .get(self.species_index)
            .and_then(|o| o.as_ref())
            .map(|o| o.time())
            .unwrap_or(0.0);
        let _ = writeln!(f, "{{\"event\":\"{}\",\"wall\":{:.3},\"w\":{:.1},\"h\":{:.1},\"state\":\"{}\",\"stage\":[{:.1},{:.1}],\"scale\":{:.6},\"iso_err\":{:.2e},\"clips\":{},\"sim_time\":{:.6},\"frames\":{},\"resizes\":{},\"rebuilds\":{},\"fps\":{:.2},\"draw_ms\":{:.3},\"sim_ms\":{:.3},\"tel_ms\":{:.3},\"draw_avg_ms\":{:.3},\"cadence\":{:.1},\"active\":true}}",
            event,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0),
            layout.width, layout.height, layout.state.name(),
            vp.stage_w, vp.stage_h, vp.scale, viewport::isotropy_error(vp),
            vp.clips(), org_time, self.frames, self.resizes, self.rebuilds,
            self.fps, self.draw_ms, self.sim_ms, self.tel_ms, self.draw_avg,
            self.cadence,
        );
    }

    /// Push live observation-field values into the console model.
    pub fn refresh_field(&mut self, vp: &Viewport) {
        let p = *self.phys_model.current();
        let org = self.organisms[self.species_index].as_ref().unwrap();
        let mdl = &mut self.model;
        mdl.phase = (org.time() * 0.31) % 2.0;
        mdl.rotation = 0.08 + 0.42 * p.agitation;
        mdl.behavior = if p.agitation > 0.66 {
            "AGITATED".to_string()
        } else if p.agitation > 0.33 {
            "ACTIVE".to_string()
        } else {
            "STABLE".to_string()
        };
        mdl.flux = p.flux;
        mdl.surge = p.surge;
        // The viewport scale IS the magnification, so the readout is true.
        mdl.magnification = (vp.scale * 10.0).max(0.1);
        mdl.field_mm = (vp.stage_w.min(vp.stage_h) / vp.scale / 400.0).clamp(0.01, f64::MAX);
    }

    pub fn tick_fps(&mut self) {
        self.fps_accum += 1;
        let now = Instant::now();
        let elapsed = now.duration_since(self.fps_t0).as_secs_f64();
        if elapsed >= 0.5 {
            // windowed mean, so the probe cannot alias onto one kind of frame
            self.draw_avg = self.draw_acc / (self.fps_accum.max(1)) as f64;
            self.draw_acc = 0.0;
            self.fps = self.fps_accum as f64 / elapsed;
            self.frame_ms = elapsed / self.fps_accum.max(1) as f64 * 1000.0;
            self.fps_accum = 0;
            self.fps_t0 = now;
        }
    }

    pub fn debug_info(&self, layout: &Layout, gdk_scale: f64) -> DebugInfo {
        let p = self.phys_model.current();
        DebugInfo {
            surf_w: (layout.width * gdk_scale) as i64,
            surf_h: (layout.height * gdk_scale) as i64,
            gdk_scale,
            fps: self.fps,
            frame_ms: self.frame_ms,
            sim_ms: self.sim_ms,
            draw_ms: self.draw_ms,
            sim_time: self.organisms[self.species_index]
                .as_ref()
                .map(|o| o.time())
                .unwrap_or(0.0),
            frames: self.frames,
            resizes: self.resizes,
            rebuilds: self.rebuilds,
            ag: p.agitation,
            pu: p.pulse,
            de: p.density,
            species: species::by_index(self.species_index).name.to_string(),
            switches: self.switches,
            notes: if self.telemetry.notes.is_empty() {
                "(probing)".to_string()
            } else {
                self.telemetry.notes.clone()
            },
        }
    }

    // =================================================================== tick

    /// One simulation step. `dt` is already clamped by the host's pacer.
    pub fn advance(&mut self, dt: f64) {
        let t0 = Instant::now();
        let telemetry = self.telemetry.clone();
        let phys = self.phys_model.update(dt, &telemetry, 0.0);
        self.organism(self.species_index).update(dt, &phys);
        self.sim_ms = t0.elapsed().as_secs_f64() * 1000.0;
    }

    /// The pacer that `GdkFrameClock` used to drive, in host-neutral form.
    ///
    /// `now_us` is a monotonic microsecond stamp. Returns `Some(dt)` when this
    /// tick is due (the host should then `advance(dt)` and draw), `None` when
    /// the tick is early or the cadence is zero. Pacing is a fixed schedule (a
    /// phase accumulator), not "time since the last frame"; half a refresh of
    /// tolerance takes the nearest tick.
    pub fn due(&mut self, now_us: f64, fps: f64) -> Option<f64> {
        self.cadence = fps;
        if fps <= 0.0 {
            // Nothing is looking: no simulation, no draw. The next visible
            // tick resumes with a clamped dt, so nothing teleports.
            return None;
        }
        let interval_us = 1_000_000.0 / fps;
        let tol = (interval_us * 0.5).min(4000.0);
        if self.next_us != 0.0 && now_us < self.next_us - tol {
            return None;
        }
        self.next_us = if self.next_us != 0.0 && now_us - self.next_us < interval_us {
            self.next_us + interval_us
        } else {
            now_us + interval_us
        };
        let dt = if self.last_tick_us == 0.0 {
            1.0 / 60.0
        } else {
            (now_us - self.last_tick_us) / 1_000_000.0
        };
        self.last_tick_us = now_us;
        // Clamp: an occluded/unmapped window stops ticking; on resume the
        // gap must not teleport the organism.
        Some(dt.clamp(0.0, 0.1))
    }

    /// The monotonic stamp, in microseconds, of the next scheduled frame.
    /// A host with its own wait loop uses this as its deadline.
    pub fn next_frame_us(&self) -> f64 {
        self.next_us
    }

    // ============================================================ frame setup

    /// Everything a frame needs that is derived from the window size. THE
    /// ENTIRE RESIZE RESPONSE, IN FULL - no allocation, no rebuild, no reset.
    /// Returns (layout, glass rect, viewport).
    pub fn frame_geometry(&mut self, width: f64, height: f64) -> (Layout, Rect, Viewport) {
        let layout = layout::resolve(width, height);
        let glass = console::stage_content(&layout);
        let vp = Viewport::for_stage(glass.x, glass.y, glass.w, glass.h);

        if (width as i32, height as i32) != self.last_size {
            self.resizes += 1;
            self.last_size = (width as i32, height as i32);
            self.log_probe(&layout, &vp, "resize");
        }
        if self.last_state != Some(layout.state) {
            self.last_state = Some(layout.state);
            self.log_probe(&layout, &vp, "state");
        }

        // Momentary key feedback expires on a clock, not on a frame count.
        if self.press_index.is_some() && self.now() >= self.press_until {
            self.press_index = None;
        }
        self.model.pressed = self.press_index;
        self.refresh_field(&vp);
        (layout, glass, vp)
    }

    // ================================================================== input

    /// A click in LOGICAL window coordinates.
    pub fn click(&mut self, x: f64, y: f64) -> Cmd {
        let Some(layout) = self.last_layout else { return Cmd::Ignored };
        let Some((kind, idx)) = console::hit_controls(&layout, x, y) else {
            return Cmd::Ignored;
        };
        match kind {
            CtlKind::Key => self.select_specimen(idx, true),
            CtlKind::Cycle => self.cycle_specimen(if idx > 0 { 1 } else { -1 }),
            CtlKind::Mode => self.cycle_mode(),
        }
        Cmd::Redraw
    }

    /// Pointer motion in LOGICAL window coordinates.
    pub fn motion(&mut self, x: f64, y: f64) -> Cmd {
        let Some(layout) = self.last_layout else { return Cmd::Ignored };
        let focus = match console::hit_controls(&layout, x, y) {
            Some((CtlKind::Key, idx)) => Some(idx),
            _ => None,
        };
        if self.model.focus != focus {
            self.model.focus = focus;
            Cmd::Redraw
        } else {
            Cmd::Ignored
        }
    }

    pub fn leave(&mut self) -> Cmd {
        if self.model.focus.is_some() {
            self.model.focus = None;
            Cmd::Redraw
        } else {
            Cmd::Ignored
        }
    }

    /// A key press, named the way GDK names keys ("Right", "Escape", "F11",
    /// "n", "1"). Every host normalises into this vocabulary, so the binding
    /// table exists exactly once.
    pub fn key_command(&mut self, name: &str) -> Cmd {
        let chars = name.chars().next();
        if name.len() == 1 && chars.map(|c| c.is_ascii_digit()).unwrap_or(false) {
            let d = chars.unwrap();
            if d != '0' {
                let n = (d as u8 - b'1') as usize;
                if n < species::COUNT {
                    self.select_specimen(n, true);
                    return Cmd::Redraw;
                }
            }
            return Cmd::Ignored;
        }
        match name {
            "Right" | "Down" | "n" | "N" => {
                self.cycle_specimen(1);
                Cmd::Redraw
            }
            "Left" | "Up" | "p" | "P" => {
                self.cycle_specimen(-1);
                Cmd::Redraw
            }
            "m" | "M" => {
                self.cycle_mode();
                Cmd::Redraw
            }
            "q" | "Q" | "Escape" => Cmd::Quit,
            "F1" => {
                self.show_debug = !self.show_debug;
                self.model.diag = self.show_debug;
                Cmd::Redraw
            }
            "F2" => {
                self.show_calibration = !self.show_calibration;
                Cmd::Redraw
            }
            "f" | "F" | "F11" => Cmd::Fullscreen,
            _ => Cmd::Ignored,
        }
    }

    // ============================================================== telemetry

    /// One telemetry sample. Called at TELEMETRY_HZ and nowhere else.
    pub fn sample_telemetry(&mut self) {
        let t0 = Instant::now();
        let mut tel = self.telemetry_src.sample();
        if self.opts.qa_temp != 0.0 {
            // QA only (--qa-temp): substitute the temperature READING so
            // the thermal states can be photographed on a cool machine.
            tel.temp_c = Some(self.opts.qa_temp);
            tel.temp_available = true;
            tel.temperature = ((self.opts.qa_temp - 30.0) / 65.0).clamp(0.0, 1.0);
        }
        let frame_ms = self.frame_ms;
        self.telemetry = tel.clone();
        self.model.history.borrow_mut().push(
            tel.cpu_pct,
            tel.temp_c,
            tel.mem_used_gb,
            frame_ms,
        );
        self.tel_ms = t0.elapsed().as_secs_f64() * 1000.0;
    }

    /// Review sprite residency. Called by the host from its idle path; costs
    /// one float compare per wakeup, and a scan of a sixteen-entry map every
    /// `SETTLE_S`.
    ///
    /// The policy is second chance, not a flush - see
    /// `skin::surface::release_unused_sources`. The first review after a
    /// layout is built marks everything; the next one lets go of whatever has
    /// not been asked for since. Returns the bytes released, so a host can log
    /// or gate on it.
    pub fn settle(&mut self, now_s: f64) -> usize {
        if now_s < self.settle_at.unwrap_or(0.0) {
            return 0;
        }
        self.settle_at = Some(now_s + SETTLE_S);
        let freed = crate::skin::surface::release_unused_sources();
        if freed > 0 {
            // Freeing surfaces from all over the arena is not the same as
            // giving the pages back; without this the process stays exactly
            // as large as it was.
            trim_heap();
        }
        freed
    }

    pub fn probe_sample(&mut self) {
        if let (Some(layout), Some(vp)) = (self.last_layout, self.last_vp) {
            self.log_probe(&layout, &vp, "sample");
        }
    }
}

// =========================================================================
// FRAME COMPOSITION (host-independent)
// =========================================================================

/// Blit one cached layer at a LOGICAL position.
///
/// The layer surface carries its own cairo device scale (the quantised cache
/// scale), so cairo maps it back to logical size by itself and the caller
/// works purely in logical coordinates - the same contract `examples/offscreen.rs`
/// renders under, and the one whose loss malformed the GTK window once.
///
/// `pad` clamps the source at its edge. A full-window layer needs it whenever
/// the target's device scale differs from the cache scale (fractional
/// scaling), because the bilinear resample would otherwise sample past the
/// last row and column and leave a transparent seam - GSK's texture sampler
/// clamps for the same reason. A *region* must NOT pad: it is blitted with an
/// unbounded `paint()`, and padding would smear the patch across the window.
fn paint_layer(cr: &Context, layer: Option<&Layer>, x: f64, y: f64, pad: bool) {
    let Some(layer) = layer else { return };
    if cr.set_source_surface(&layer.surface, x, y).is_err() {
        return;
    }
    if pad {
        cr.source().set_extend(cairo::Extend::Pad);
    }
    let _ = cr.paint();
}

/// Where a composed frame is going: logical size for the machine, physical
/// size for the buffer, and the exact per-axis scale that ties them together.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Target {
    pub logical_w: f64,
    pub logical_h: f64,
    pub phys_w: i32,
    pub phys_h: i32,
    pub ds_x: f64,
    pub ds_y: f64,
    /// The window's raw scale factor, for the debug overlay to print.
    pub scale: f64,
}

impl Target {
    /// A target whose device scale is exactly `ds` - the headless case.
    pub fn exact(logical_w: f64, logical_h: f64, ds: f64) -> Target {
        let (pw, ph) = ((logical_w * ds).ceil() as i32, (logical_h * ds).ceil() as i32);
        Target {
            logical_w,
            logical_h,
            phys_w: pw,
            phys_h: ph,
            ds_x: pw as f64 / logical_w,
            ds_y: ph as f64 / logical_h,
            scale: ds,
        }
    }
}

/// A rectangle of the presentation buffer, in whole DEVICE pixels.
///
/// Damage is expressed in buffer pixels, and so is every repaint decision, so
/// this is the one currency both sides of the seam understand. The matching
/// LOGICAL rectangle is recovered by dividing by the target's device scale -
/// exact, because the edges were snapped ONTO the device grid in the first
/// place.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct DeviceRect {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
}

impl DeviceRect {
    pub fn is_empty(&self) -> bool {
        self.w == 0 || self.h == 0
    }

    fn right(&self) -> u32 {
        self.x + self.w
    }

    fn bottom(&self) -> u32 {
        self.y + self.h
    }

    fn intersects(&self, o: &DeviceRect) -> bool {
        !self.is_empty()
            && !o.is_empty()
            && self.x < o.right()
            && o.x < self.right()
            && self.y < o.bottom()
            && o.y < self.bottom()
    }

    /// The smallest device-aligned rect that contains the LOGICAL rect `r`,
    /// clamped to the buffer. Growing OUTWARD is what makes a partial repaint
    /// seamless: a fractional logical edge would otherwise leave the pixel it
    /// half-covers holding a blend of two different frames.
    fn snap(r: Rect, t: &Target) -> DeviceRect {
        if !(r.w > 0.0 && r.h > 0.0) {
            return DeviceRect { x: 0, y: 0, w: 0, h: 0 };
        }
        let x0 = ((r.x * t.ds_x).floor()).clamp(0.0, t.phys_w as f64) as u32;
        let y0 = ((r.y * t.ds_y).floor()).clamp(0.0, t.phys_h as f64) as u32;
        let x1 = ((r.right() * t.ds_x).ceil()).clamp(0.0, t.phys_w as f64) as u32;
        let y1 = ((r.bottom() * t.ds_y).ceil()).clamp(0.0, t.phys_h as f64) as u32;
        DeviceRect { x: x0, y: y0, w: x1.saturating_sub(x0), h: y1.saturating_sub(y0) }
    }

    /// Back to LOGICAL coordinates. Exact by construction: the edges are
    /// integers on the device grid, so `logical * ds` lands on them again and
    /// cairo's rasteriser sees a pixel-aligned rectangle with no partial
    /// coverage anywhere on its border.
    fn logical(&self, t: &Target) -> Rect {
        Rect::new(
            self.x as f64 / t.ds_x,
            self.y as f64 / t.ds_y,
            self.w as f64 / t.ds_x,
            self.h as f64 / t.ds_y,
        )
    }
}

/// What a frame actually wrote, and therefore what the host must damage.
#[derive(Clone, Debug, PartialEq)]
pub enum Painted {
    /// Every pixel of the buffer.
    Whole,
    /// Only these device rectangles; the rest of the buffer is untouched and
    /// still holds the frame it held on entry.
    Rects(Vec<DeviceRect>),
}

/// How many frames of dirty history are kept. Softbuffer's Wayland backend is
/// double-buffered, so a returned buffer's age is 2 in steady state; anything
/// older than this forces a whole frame, which is always correct.
const DIRTY_HISTORY: usize = 4;

/// The static picture, pre-composed at the target's device resolution, plus
/// the dirty-rectangle bookkeeping that lets a frame repaint only what moved.
///
/// ## Why the static layers are composed once
///
/// Under GTK this cache did not need to exist: GSK uploaded each cached layer
/// once and the GPU resampled the 1.5-scale texture into the 1.6-scale window
/// every frame for free. Cairo does that resample on the CPU, and measured at
/// 781x468 / ds 1.6 it cost 0.98 ms (under) + 1.08 ms (over) per frame.
///
/// `base` goes one step further than caching the two resamples separately: it
/// is the WHOLE static picture - opaque black, then `under`, then `over` - as
/// one opaque device-resolution surface. A frame that needs the static
/// backdrop restores it with a single 1:1 copy instead of a clear plus two
/// full-window source-over composites.
///
/// It is bit-identical, not an approximation. `base` is built by exactly the
/// operations a frame used to perform, in the same order, into a surface of
/// the same format and device scale; a 1:1 unscaled copy of the result is a
/// byte copy. Source-over associativity on premultiplied alpha is what makes
/// the resample step exact, and it is asserted byte for byte by
/// `the_device_cache_is_bit_identical_to_resampling_every_frame`.
///
/// ## Why the glass is kept separately
///
/// The organism is sandwiched: `under`, organism, `over`. `base` has `over`
/// already composited, so the glass cannot simply be drawn on top of it. The
/// two crops hold the sandwich's bread at device resolution for the glass
/// rectangle alone - measured at 14 % of the window - so the sandwich is
/// rebuilt over that rectangle and nowhere else.
///
/// Cost: `base` (one full-window ARGB32) plus two glass-sized crops. At
/// 1250x749 with a 372x349 glass that is 3.6 + 2 x 0.5 = 4.6 MB, against the
/// 7.1 MB the two separate full-window device layers used to hold.
#[derive(Default)]
pub struct DeviceLayers {
    key: Option<(u64, u64, i32, i32, u64, u64)>,
    /// opaque black + `under` + `over`, at the target's device resolution
    base: Option<cairo::ImageSurface>,
    /// opaque black + `under`, cropped to `glass`
    glass_base: Option<cairo::ImageSurface>,
    /// `over` alone, cropped to `glass`
    glass_over: Option<cairo::ImageSurface>,
    /// the glass, snapped outward onto the device grid
    glass: DeviceRect,
    /// what each of the last `DIRTY_HISTORY` frames changed, newest last
    dirty: std::collections::VecDeque<Vec<DeviceRect>>,
    /// (region layer id, rect) as of the previous frame, to spot changes
    last_regions: Vec<(u64, DeviceRect)>,
}

impl DeviceLayers {
    pub fn new() -> DeviceLayers {
        DeviceLayers::default()
    }

    /// Bytes currently held, for the record.
    pub fn bytes(&self) -> usize {
        [&self.base, &self.glass_base, &self.glass_over]
            .iter()
            .filter_map(|s| s.as_ref())
            .map(|s| (s.stride() as usize) * (s.height() as usize))
            .sum()
    }

    /// Forget every frame-to-frame assumption. The next frame will be whole.
    ///
    /// The host calls this whenever the presentation buffer's history cannot
    /// be trusted - after a resize, or when softbuffer reports an age it has
    /// no dirty record for.
    pub fn forget_history(&mut self) {
        self.dirty.clear();
        self.last_regions.clear();
    }

    fn resample(src: &Layer, t: &Target) -> Option<cairo::ImageSurface> {
        let dst = cairo::ImageSurface::create(cairo::Format::ARgb32, t.phys_w, t.phys_h).ok()?;
        dst.set_device_scale(t.ds_x, t.ds_y);
        {
            let cr = Context::new(&dst).ok()?;
            paint_layer(&cr, Some(src), 0.0, 0.0, true);
        }
        dst.flush();
        Some(dst)
    }

    /// An exact 1:1 copy of `r` out of `src`. Both surfaces carry the target's
    /// device scale and the rect is device-aligned, so the pattern transform
    /// is an integer translation and the copy is a byte copy.
    fn crop(src: &cairo::ImageSurface, r: DeviceRect, t: &Target) -> Option<cairo::ImageSurface> {
        if r.is_empty() {
            return None;
        }
        let dst = cairo::ImageSurface::create(cairo::Format::ARgb32, r.w as i32, r.h as i32).ok()?;
        dst.set_device_scale(t.ds_x, t.ds_y);
        {
            let lr = r.logical(t);
            let cr = Context::new(&dst).ok()?;
            cr.set_operator(cairo::Operator::Source);
            cr.set_source_surface(src, -lr.x, -lr.y).ok()?;
            cr.paint().ok()?;
        }
        dst.flush();
        Some(dst)
    }

    /// Rebuild only when a layer's identity, the buffer size or the glass
    /// changes. A `Layer` id is stable for the lifetime of its pixels, so this
    /// is exact: a content change produces a new id.
    ///
    /// Returns true when anything was rebuilt, which invalidates every
    /// frame-to-frame assumption the caller may have held.
    fn prepare(
        &mut self,
        under: Option<&Layer>,
        over: Option<&Layer>,
        t: &Target,
        glass: Rect,
    ) -> bool {
        let g = DeviceRect::snap(glass, t);
        let key = (
            under.map(|l| l.id).unwrap_or(0),
            over.map(|l| l.id).unwrap_or(0),
            t.phys_w,
            t.phys_h,
            ((g.x as u64) << 32) | g.y as u64,
            ((g.w as u64) << 32) | g.h as u64,
        );
        if self.key == Some(key) {
            return false;
        }
        self.glass = g;
        self.base = None;
        self.glass_base = None;
        self.glass_over = None;

        // black + under, full window. This IS the frame's first two steps.
        let built = (|| -> Option<cairo::ImageSurface> {
            let base =
                cairo::ImageSurface::create(cairo::Format::ARgb32, t.phys_w, t.phys_h).ok()?;
            base.set_device_scale(t.ds_x, t.ds_y);
            {
                let cr = Context::new(&base).ok()?;
                cr.set_operator(cairo::Operator::Source);
                cr.set_source_rgb(0.0, 0.0, 0.0);
                cr.paint().ok()?;
                cr.set_operator(cairo::Operator::Over);
                paint_layer(&cr, under, 0.0, 0.0, true);
            }
            base.flush();
            // the glass's bread, taken before `over` lands on it
            self.glass_base = Self::crop(&base, g, t);
            // `over`, resampled once: bit-identical to painting it per frame
            if let Some(o) = over {
                let dev_over = Self::resample(o, t)?;
                self.glass_over = Self::crop(&dev_over, g, t);
                let cr = Context::new(&base).ok()?;
                Self::blit(&cr, Some(&dev_over));
            }
            base.flush();
            Some(base)
        })();
        self.base = built;
        self.key = Some(key);
        self.forget_history();
        true
    }

    /// Blit a prepared surface 1:1. Its device scale equals the target's, so
    /// cairo takes the unscaled path.
    fn blit(cr: &Context, surf: Option<&cairo::ImageSurface>) {
        let Some(s) = surf else { return };
        if cr.set_source_surface(s, 0.0, 0.0).is_err() {
            return;
        }
        let _ = cr.paint();
    }

    /// Restore the static backdrop over exactly `r`, replacing whatever the
    /// buffer held there. `Operator::Source` because `base` is opaque and the
    /// old contents must not show through.
    fn restore(cr: &Context, base: Option<&cairo::ImageSurface>, r: DeviceRect, t: &Target) {
        let Some(base) = base else { return };
        if r.is_empty() {
            return;
        }
        let lr = r.logical(t);
        let _ = cr.save();
        cr.rectangle(lr.x, lr.y, lr.w, lr.h);
        cr.clip();
        cr.set_operator(cairo::Operator::Source);
        if cr.set_source_surface(base, 0.0, 0.0).is_ok() {
            let _ = cr.paint();
        }
        let _ = cr.restore();
    }

    /// The union of the dirty sets of the last `age` frames, plus `now`.
    ///
    /// A buffer of age `n` holds the frame from `n` frames ago, so everything
    /// that has changed since then has to be repainted into it. Returns None
    /// when the history cannot cover that reach and the whole frame is owed.
    fn repaint_set(&self, age: u8, now: &[DeviceRect]) -> Option<Vec<DeviceRect>> {
        if age == 0 {
            return None;
        }
        let back = age as usize - 1; // frames before this one that must be redone
        if back > self.dirty.len() {
            return None;
        }
        let mut out: Vec<DeviceRect> = now.to_vec();
        for set in self.dirty.iter().rev().take(back) {
            for r in set {
                if !out.contains(r) {
                    out.push(*r);
                }
            }
        }
        out.retain(|r| !r.is_empty());
        Some(out)
    }

    fn remember(&mut self, now: Vec<DeviceRect>, regions: Vec<(u64, DeviceRect)>) {
        self.dirty.push_back(now);
        while self.dirty.len() > DIRTY_HISTORY {
            self.dirty.pop_front();
        }
        self.last_regions = regions;
    }
}

impl Core {
    /// Compose one whole frame into `cr`, in LOGICAL coordinates.
    ///
    /// The headless entry point and the one every parity harness drives:
    /// every pixel is written, exactly as it always was.
    pub fn compose_frame(&mut self, cr: &Context, t: Target, dev: &mut DeviceLayers) {
        self.compose_damaged(cr, t, dev, 0);
    }

    /// Compose one frame, writing only what has changed since the frame this
    /// buffer already holds.
    ///
    /// `age` is softbuffer's: the number of frames ago this buffer was last
    /// presented, and 0 when its contents are undefined. At `age == 0` this is
    /// `compose_frame` and returns `Painted::Whole`; otherwise it restores the
    /// static backdrop over the dirty rectangles only, redraws the organism
    /// and any region that lands in them, and returns exactly the rectangles
    /// it touched for the host to pass to `present_with_damage`.
    ///
    /// The sequence inside a repainted rectangle is the same four-layer
    /// sequence a whole frame uses - black, `under`, organism, `over`,
    /// regions - so the result is bit-identical to a whole frame. That is
    /// asserted by `a_damaged_frame_is_bit_identical_to_a_whole_one`.
    pub fn compose_damaged(
        &mut self,
        cr: &Context,
        t: Target,
        dev: &mut DeviceLayers,
        age: u8,
    ) -> Painted {
        let (width, height) = (t.logical_w, t.logical_h);
        if width < 1.0 || height < 1.0 {
            return Painted::Whole;
        }
        let t0 = Instant::now();
        let (layout, glass, vp) = self.frame_geometry(width, height);

        // LAYERS 0-1: background, shell, glass, graticule (static)
        let under = self.renderer.layer_under(&layout, &self.model);
        // LAYER 3: the structural modules and static type (static)
        let over = self
            .renderer
            .layer_over(&layout, &self.model, &self.telemetry);
        let rebuilt = dev.prepare(under.as_ref(), over.as_ref(), &t, glass);

        // LAYERS 4-6: live regions, each re-rendered only on its own change.
        let regions = self.renderer.regions(
            &layout,
            &self.model,
            &self.telemetry,
            self.fps,
            self.frame_ms,
            under.as_ref(),
            over.as_ref(),
        );
        let placed: Vec<(u64, DeviceRect)> = regions
            .iter()
            .filter(|r| r.rect.valid())
            .map(|r| (r.layer.id, DeviceRect::snap(r.rect, &t)))
            .collect();

        // What changed since the previous frame: the glass always (the
        // organism moves), plus any region whose surface is not the one that
        // was there last time, plus the footprint a region has just left.
        let mut now: Vec<DeviceRect> = Vec::new();
        if !dev.glass.is_empty() {
            now.push(dev.glass);
        }
        for (id, r) in &placed {
            if !dev.last_regions.contains(&(*id, *r)) && !now.contains(r) {
                now.push(*r);
            }
        }
        for (id, r) in &dev.last_regions {
            if !placed.contains(&(*id, *r)) && !now.contains(r) {
                now.push(*r);
            }
        }

        // The debug overlay is drawn free-hand across the window, so it has no
        // rectangle to damage; it forces whole frames and costs what it costs.
        let whole = rebuilt || self.show_debug || dev.base.is_none();
        let repaint = if whole { None } else { dev.repaint_set(age, &now) };

        // `now` is this frame's DIRTY set - what changed since the frame
        // before it - and the history of those sets is what a buffer of age
        // `k` is brought up to date with. When the static picture itself
        // changes, EVERY pixel changed, and recording only the glass and the
        // regions is a lie the next frame pays for.
        //
        // It is paid one frame later, in the other pooled buffer. Selecting a
        // specimen rebuilds the `over` layer, so the name engraved in the
        // header changes; that frame is composed whole and looks right. The
        // next frame takes the other buffer, whose age is 2, so it holds the
        // frame BEFORE the switch - the old name - and repaints only what the
        // (understated) history claims changed. The old name survives. The
        // result alternates old, new, old, new at the refresh rate: a label
        // that flickers violently between two specimens while every single
        // screenshot of it looks correct.
        let now = if whole {
            vec![DeviceRect { x: 0, y: 0, w: t.phys_w.max(0) as u32, h: t.phys_h.max(0) as u32 }]
        } else {
            now
        };

        match &repaint {
            // ---- whole frame -------------------------------------------
            // One opaque 1:1 copy in place of a clear plus two full-window
            // source-over composites. `base` is opaque, so Operator::Source
            // is safe and the undefined contents of a fresh buffer cannot
            // show through - the job the black scrub used to do.
            None => {
                let _ = cr.save();
                cr.set_operator(cairo::Operator::Source);
                match dev.base.as_ref() {
                    Some(b) => DeviceLayers::blit(cr, Some(b)),
                    None => {
                        cr.set_source_rgb(0.0, 0.0, 0.0);
                        let _ = cr.paint();
                    }
                }
                let _ = cr.restore();
            }
            // ---- partial frame -----------------------------------------
            Some(rects) => {
                for r in rects {
                    if *r == dev.glass {
                        continue; // the glass gets its own sandwich below
                    }
                    DeviceLayers::restore(cr, dev.base.as_ref(), *r, &t);
                }
            }
        }

        // LAYER 2: the organism - the only per-frame raster, glass only.
        // Its bread is laid down again first, because `base` already has
        // `over` composited and the organism belongs underneath it.
        if glass.valid() && !dev.glass.is_empty() {
            let gl = dev.glass.logical(&t);
            let _ = cr.save();
            cr.rectangle(gl.x, gl.y, gl.w, gl.h);
            cr.clip();
            if repaint.is_some() || dev.glass_base.is_some() {
                let _ = cr.save();
                cr.set_operator(cairo::Operator::Source);
                if let Some(gb) = dev.glass_base.as_ref() {
                    if cr.set_source_surface(gb, gl.x, gl.y).is_ok() {
                        let _ = cr.paint();
                    }
                }
                let _ = cr.restore();
            }
            {
                let _ = cr.save();
                cr.rectangle(glass.x, glass.y, glass.w, glass.h);
                cr.clip();
                let Core {
                    organisms,
                    species_index,
                    rcaches,
                    show_calibration,
                    ..
                } = self;
                let org = organisms[*species_index].as_ref().unwrap();
                render::draw_organism(cr, &vp, org, rcaches);
                if *show_calibration {
                    render::draw_calibration(cr, &vp, org);
                }
                let _ = cr.restore();
            }
            if let Some(go) = dev.glass_over.as_ref() {
                if cr.set_source_surface(go, gl.x, gl.y).is_ok() {
                    let _ = cr.paint();
                }
            }
            let _ = cr.restore();
        }

        // Regions sit on top of the static picture, so any region whose
        // rectangle was restored has to be laid down again even if its own
        // pixels did not change.
        for (reg, (_, dr)) in regions.iter().zip(placed.iter()) {
            let touched = match &repaint {
                None => true,
                Some(rects) => {
                    rects.iter().any(|r| r.intersects(dr)) || dr.intersects(&dev.glass)
                }
            };
            if touched {
                paint_layer(cr, Some(&reg.layer), reg.rect.x, reg.rect.y, false);
            }
        }

        self.draw_ms = t0.elapsed().as_secs_f64() * 1000.0;
        self.draw_acc += self.draw_ms;
        if self.show_debug {
            let info = self.debug_info(&layout, t.scale);
            debug::draw_debug(cr, &layout, &vp, &info);
        }
        self.frames += 1;
        self.tick_fps();
        self.last_layout = Some(layout);
        self.last_vp = Some(vp);

        match repaint {
            None => {
                dev.remember(now, placed);
                Painted::Whole
            }
            Some(rects) => {
                dev.remember(now, placed);
                Painted::Rects(rects)
            }
        }
    }
}

#[cfg(test)]
mod paint_tests {
    use super::*;
    use crate::skin::hidpi;

    /// Build a `Layer` whose surface is a `w x h` LOGICAL block of solid
    /// colour rendered at cache scale `cache_ds`, with a marker in the very
    /// last logical pixel of each corner.
    fn probe_layer(cache_ds: f64, w: f64, h: f64) -> Layer {
        hidpi::set_scale(cache_ds);
        let surf = hidpi::surface(w, h);
        {
            let cr = Context::new(&surf).unwrap();
            cr.set_source_rgb(0.15, 0.35, 0.55);
            cr.paint().unwrap();
            cr.set_source_rgb(1.0, 0.0, 0.0);
            for (cx, cy) in [(0.0, 0.0), (w - 2.0, 0.0), (0.0, h - 2.0), (w - 2.0, h - 2.0)] {
                cr.rectangle(cx, cy, 2.0, 2.0);
                cr.fill().unwrap();
            }
        }
        surf.flush();
        Layer { id: 1, surface: surf }
    }

    fn target(phys_w: i32, phys_h: i32, ds_x: f64, ds_y: f64) -> cairo::ImageSurface {
        let s = cairo::ImageSurface::create(cairo::Format::ARgb32, phys_w, phys_h).unwrap();
        s.set_device_scale(ds_x, ds_y);
        s
    }

    fn px(s: &mut cairo::ImageSurface, x: i32, y: i32) -> (u8, u8, u8, u8) {
        s.flush();
        let stride = s.stride() as usize;
        let d = s.data().unwrap();
        let o = y as usize * stride + x as usize * 4;
        (d[o + 2], d[o + 1], d[o], d[o + 3]) // r, g, b, a
    }

    /// A full-window layer must cover EVERY device pixel of the target, at
    /// every combination of cache scale and target scale - most of all the
    /// fractional one, where the cache is rendered at 1.5 and the window is
    /// 1.6, and the bilinear resample would otherwise leave a transparent
    /// seam along the last row and column.
    ///
    /// This is the gate for the bug class that malformed the GTK window: it
    /// cannot fail at ds 1.0, so it is driven at the scales that can.
    #[test]
    fn a_padded_layer_fills_the_whole_target_at_every_scale() {
        // (cache scale, target scale) - equal, upscaling, and downscaling
        for (cache_ds, win_ds) in [
            (1.0, 1.0),
            (1.5, 1.5),
            (2.0, 2.0),
            (1.5, 1.6),   // Hyprland fractional: quantised cache, real window
            (1.5, 1.0),
            (1.0, 2.0),
        ] {
            let (lw, lh) = (120.0_f64, 80.0_f64);
            let layer = probe_layer(cache_ds, lw, lh);
            let (pw, ph) = ((lw * win_ds) as i32, (lh * win_ds) as i32);
            let mut tgt = target(pw, ph, pw as f64 / lw, ph as f64 / lh);
            {
                let cr = Context::new(&tgt).unwrap();
                paint_layer(&cr, Some(&layer), 0.0, 0.0, true);
            }
            let tag = format!("cache {cache_ds} -> window {win_ds}");
            // corners: the marker must reach the outermost device pixel
            for (x, y) in [(0, 0), (pw - 1, 0), (0, ph - 1), (pw - 1, ph - 1)] {
                let (r, g, b, a) = px(&mut tgt, x, y);
                assert!(a > 250, "{tag}: device pixel {x},{y} is transparent (a={a}) - the layer did not reach the edge");
                assert!(r > 150 && g < 90 && b < 90,
                        "{tag}: device pixel {x},{y} is {r},{g},{b} - expected the corner marker");
            }
            // centre: the body colour, i.e. the layer is not shrunk into a
            // corner (the exact failure of the texture-copy regression)
            let (r, g, b, a) = px(&mut tgt, pw / 2, ph / 2);
            assert!(a > 250 && b > r && b > 100,
                    "{tag}: centre is {r},{g},{b},{a} - expected the layer body");
        }
        hidpi::set_scale(1.0);
    }

    /// The device-resolution cache must be BIT-IDENTICAL to resampling every
    /// frame, not merely close: resampling a layer once into a device-scaled
    /// surface and blitting that 1:1 has to produce the same bytes as
    /// resampling it straight into the frame. (Source-over associativity on
    /// premultiplied alpha; cairo uses the same filter either way.) If this
    /// ever drifts, the cache is an approximation and the parity table is a
    /// lie - so it is asserted, byte for byte, at the fractional scale.
    #[test]
    fn the_device_cache_is_bit_identical_to_resampling_every_frame() {
        for (cache_ds, win_ds) in [(1.5_f64, 1.6_f64), (1.5, 1.0), (1.0, 1.6), (2.0, 1.6)] {
            let (lw, lh) = (240.0_f64, 150.0_f64);
            let layer = probe_layer(cache_ds, lw, lh);
            let (pw, ph) = ((lw * win_ds) as i32, (lh * win_ds) as i32);
            let t = Target {
                logical_w: lw,
                logical_h: lh,
                phys_w: pw,
                phys_h: ph,
                ds_x: pw as f64 / lw,
                ds_y: ph as f64 / lh,
                scale: win_ds,
            };

            // (a) straight into the frame, every frame
            let direct = target(pw, ph, t.ds_x, t.ds_y);
            {
                let cr = Context::new(&direct).unwrap();
                paint_layer(&cr, Some(&layer), 0.0, 0.0, true);
            }
            // (b) resampled once, then blitted 1:1
            let cached = target(pw, ph, t.ds_x, t.ds_y);
            {
                let dev = DeviceLayers::resample(&layer, &t).expect("device copy");
                let cr = Context::new(&cached).unwrap();
                DeviceLayers::blit(&cr, Some(&dev));
            }
            let mut direct = direct;
            let mut cached = cached;
            direct.flush();
            cached.flush();
            let (a, b) = (direct.data().unwrap(), cached.data().unwrap());
            assert_eq!(a.len(), b.len());
            let diff = a.iter().zip(b.iter()).filter(|(x, y)| x != y).count();
            assert_eq!(
                diff, 0,
                "cache {cache_ds} -> window {win_ds}: {diff} of {} bytes differ - \
                 the device cache is NOT bit-identical",
                a.len()
            );
        }
        hidpi::set_scale(1.0);
    }

    /// Switching specimen rebuilds the static picture, and the OTHER pooled
    /// buffer still holds the frame from before the switch. If the frame that
    /// rebuilt only records the glass and the regions as its dirty set, the
    /// next frame repaints only those into a buffer carrying the old header,
    /// and the engraved specimen name alternates old, new, old, new at the
    /// refresh rate - violent flicker that no single screenshot can catch.
    ///
    /// This drives two alternating buffers at the age softbuffer really
    /// reports, switches specimen in the middle, and requires every frame to
    /// be byte-identical to a whole one. It FAILS without the whole-buffer
    /// dirty record.
    #[test]
    fn switching_specimen_does_not_leave_the_old_chrome_in_the_other_buffer() {
        let (cache_ds, win_ds) = (1.5_f64, 1.6_f64);
        let (lw, lh) = (781.0_f64, 468.0_f64);
        let (pw, ph) = ((lw * win_ds).round() as i32, (lh * win_ds).round() as i32);
        let t = Target {
            logical_w: lw, logical_h: lh, phys_w: pw, phys_h: ph,
            ds_x: pw as f64 / lw, ds_y: ph as f64 / lh, scale: win_ds,
        };
        crate::ui::fonts::ensure_user_fonts();
        console::pin_clock("04:17:33");
        let opts = Options { width: lw as i32, height: lh as i32, ..Options::default() };
        let mut core = Core::new(opts.clone());
        let mut ref_core = Core::new(opts);
        let mut dev = DeviceLayers::new();
        let mut ref_dev = DeviceLayers::new();
        // the pool: two buffers, alternating, exactly as softbuffer holds them
        let bufs = [target(pw, ph, t.ds_x, t.ds_y), target(pw, ph, t.ds_x, t.ds_y)];
        let refbuf = target(pw, ph, t.ds_x, t.ds_y);
        let refcr = Context::new(&refbuf).unwrap();

        for frame in 0..20 {
            hidpi::set_scale(cache_ds);
            // switch specimen part way through, then switch back
            if frame == 6 {
                core.select_specimen(2, false);
                ref_core.select_specimen(2, false);
            }
            if frame == 12 {
                core.select_specimen(0, false);
                ref_core.select_specimen(0, false);
            }
            core.advance(1.0 / 60.0);
            ref_core.advance(1.0 / 60.0);
            // `fps` is wall-clock instrumentation that feeds the rack region's
            // cache key; pin it on both so the comparison is about composition
            // and nothing else.
            for c in [&mut core, &mut ref_core] {
                c.fps = 60.0;
                c.frame_ms = 1000.0 / 60.0;
            }
            let cr = Context::new(&bufs[frame % 2]).unwrap();
            let age = if frame < 2 { 0 } else { 2 };
            core.compose_damaged(&cr, t, &mut dev, age);
            ref_core.compose_frame(&refcr, t, &mut ref_dev);
            drop(cr);
            bufs[frame % 2].flush();
            refbuf.flush();
            let mut a = hidpi::copy_exclusive(&bufs[frame % 2]);
            let mut b = hidpi::copy_exclusive(&refbuf);
            let (da, db) = (a.data().unwrap(), b.data().unwrap());
            let diff = da.iter().zip(db.iter()).filter(|(x, y)| x != y).count();
            assert_eq!(
                diff, 0,
                "frame {frame} (specimen {}): {diff} of {} bytes differ from a whole \
                 frame - stale chrome survived in the pooled buffer",
                core.species_index, da.len()
            );
        }
        hidpi::set_scale(1.0);
    }

    /// The pre-composed `base` must be BIT-IDENTICAL to the clear-plus-two-
    /// full-window-composites sequence it replaced. The whole saving rests on
    /// that: if compositing `under` and `over` into an opaque surface once and
    /// copying it were merely *close* to doing it every frame, every parity
    /// number in the docs would be describing a frame the app no longer draws.
    ///
    /// Driven at the fractional scale, where the layers are resampled, and at
    /// whole scales, where they are not.
    #[test]
    fn the_precomposed_base_is_bit_identical_to_composing_every_frame() {
        for (cache_ds, win_ds) in [(1.5_f64, 1.6_f64), (1.5, 1.0), (1.0, 1.0), (2.0, 2.0)] {
            let (lw, lh) = (240.0_f64, 150.0_f64);
            let under = probe_layer(cache_ds, lw, lh);
            hidpi::set_scale(cache_ds);
            // an `over` layer with real translucency, so the composite is not
            // trivially the top one
            let over = {
                let surf = hidpi::surface(lw, lh);
                {
                    let cr = Context::new(&surf).unwrap();
                    cr.set_source_rgba(0.9, 0.8, 0.1, 0.4);
                    cr.rectangle(10.0, 10.0, lw - 20.0, 40.0);
                    cr.fill().unwrap();
                    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0);
                    cr.rectangle(0.0, lh - 6.0, lw, 6.0);
                    cr.fill().unwrap();
                }
                surf.flush();
                Layer { id: 2, surface: surf }
            };
            let (pw, ph) = ((lw * win_ds) as i32, (lh * win_ds) as i32);
            let t = Target {
                logical_w: lw, logical_h: lh, phys_w: pw, phys_h: ph,
                ds_x: pw as f64 / lw, ds_y: ph as f64 / lh, scale: win_ds,
            };

            // (a) the sequence a frame used to run, every frame
            let direct = target(pw, ph, t.ds_x, t.ds_y);
            {
                let cr = Context::new(&direct).unwrap();
                cr.set_operator(cairo::Operator::Source);
                cr.set_source_rgb(0.0, 0.0, 0.0);
                cr.paint().unwrap();
                cr.set_operator(cairo::Operator::Over);
                paint_layer(&cr, Some(&under), 0.0, 0.0, true);
                paint_layer(&cr, Some(&over), 0.0, 0.0, true);
            }
            // (b) composed once into `base`, then copied 1:1
            let cached = target(pw, ph, t.ds_x, t.ds_y);
            {
                let mut dev = DeviceLayers::new();
                assert!(dev.prepare(Some(&under), Some(&over), &t, Rect::NONE));
                let cr = Context::new(&cached).unwrap();
                cr.set_operator(cairo::Operator::Source);
                DeviceLayers::blit(&cr, dev.base.as_ref());
            }
            let mut direct = hidpi::copy_exclusive(&direct);
            let mut cached = hidpi::copy_exclusive(&cached);
            let (a, b) = (direct.data().unwrap(), cached.data().unwrap());
            let diff = a.iter().zip(b.iter()).filter(|(x, y)| x != y).count();
            assert_eq!(
                diff, 0,
                "cache {cache_ds} -> window {win_ds}: {diff} of {} bytes differ - \
                 the pre-composed base is NOT bit-identical",
                a.len()
            );
            // and it must be fully opaque, or softbuffer presents it darkened
            assert!(
                b.chunks_exact(4).all(|p| p[3] == 255),
                "cache {cache_ds} -> window {win_ds}: base is not fully opaque"
            );
        }
        hidpi::set_scale(1.0);
    }

    /// The whole point of the damage path: a frame composed by repainting
    /// only the dirty rectangles must be BIT-IDENTICAL to one composed from
    /// scratch. Not close - identical. If it ever drifts, the window is
    /// showing a frame no parity harness has ever looked at.
    ///
    /// Driven through the real `Core`, at the real fractional scale, over
    /// enough frames for the organism to move, the clock to be issued and the
    /// region cache to turn over. Two buffers are carried in lock-step: one
    /// always composed whole, one composed damaged with a truthful age, and
    /// every frame is compared byte for byte.
    #[test]
    fn a_damaged_frame_is_bit_identical_to_a_whole_one() {
        for (cache_ds, win_ds) in [(1.5_f64, 1.6_f64), (1.0, 1.0), (2.0, 2.0), (1.25, 1.25)] {
            let (lw, lh) = (781.0_f64, 468.0_f64);
            let (pw, ph) = ((lw * win_ds).round() as i32, (lh * win_ds).round() as i32);
            let t = Target {
                logical_w: lw,
                logical_h: lh,
                phys_w: pw,
                phys_h: ph,
                ds_x: pw as f64 / lw,
                ds_y: ph as f64 / lh,
                scale: win_ds,
            };
            crate::ui::fonts::ensure_user_fonts();
            // The header clock is the one non-deterministic thing in a frame;
            // two composes a millisecond apart can straddle a second boundary.
            console::pin_clock("04:17:33");
            let opts = Options { width: lw as i32, height: lh as i32, ..Options::default() };
            let mut whole_core = Core::new(opts.clone());
            let mut dirty_core = Core::new(opts);
            let mut whole_dev = DeviceLayers::new();
            let mut dirty_dev = DeviceLayers::new();
            let whole_buf = target(pw, ph, t.ds_x, t.ds_y);
            let dirty_buf = target(pw, ph, t.ds_x, t.ds_y);
            let whole_cr = Context::new(&whole_buf).unwrap();
            let dirty_cr = Context::new(&dirty_buf).unwrap();

            // A truthful double-buffered age: the damaged frame is composed
            // into ONE buffer here, so after the first frame it is always
            // looking at the frame it drew one frame ago.
            for frame in 0..24 {
                hidpi::set_scale(cache_ds);
                whole_core.advance(1.0 / 60.0);
                dirty_core.advance(1.0 / 60.0);
                let painted =
                    dirty_core.compose_damaged(&dirty_cr, t, &mut dirty_dev,
                                               if frame == 0 { 0 } else { 1 });
                whole_core.compose_frame(&whole_cr, t, &mut whole_dev);
                if frame == 0 {
                    assert_eq!(painted, Painted::Whole, "frame 0 must be whole");
                } else if frame > 2 {
                    assert!(matches!(painted, Painted::Rects(_)),
                            "cache {cache_ds} -> {win_ds}: frame {frame} never went partial");
                }
                whole_buf.flush();
                dirty_buf.flush();
                // an exclusive copy: the live Context still holds a reference
                // to the buffer itself, and cairo refuses a data lend then
                let mut a = hidpi::copy_exclusive(&whole_buf);
                let mut b = hidpi::copy_exclusive(&dirty_buf);
                let (da, db) = (a.data().unwrap(), b.data().unwrap());
                let diff = da.iter().zip(db.iter()).filter(|(x, y)| x != y).count();
                assert_eq!(
                    diff, 0,
                    "cache {cache_ds} -> window {win_ds}, frame {frame}: {diff} of {} \
                     bytes differ - the damaged frame is NOT bit-identical",
                    da.len()
                );
            }
        }
        hidpi::set_scale(1.0);
    }

    /// A partial frame must damage EVERY pixel it changed and no pixel it did
    /// not. Under-damaging leaves the compositor showing a stale rectangle;
    /// over-damaging is merely wasteful. This drives a buffer of age 2 - what
    /// softbuffer's double-buffered Wayland backend actually reports - against
    /// a reference that is whole every frame, and checks that every byte the
    /// reference changed two frames running falls inside the reported rects.
    #[test]
    fn damage_covers_every_pixel_a_partial_frame_changed() {
        let (cache_ds, win_ds) = (1.5_f64, 1.6_f64);
        let (lw, lh) = (781.0_f64, 468.0_f64);
        let (pw, ph) = ((lw * win_ds).round() as i32, (lh * win_ds).round() as i32);
        let t = Target {
            logical_w: lw, logical_h: lh, phys_w: pw, phys_h: ph,
            ds_x: pw as f64 / lw, ds_y: ph as f64 / lh, scale: win_ds,
        };
        crate::ui::fonts::ensure_user_fonts();
        console::pin_clock("04:17:33");
        let opts = Options { width: lw as i32, height: lh as i32, ..Options::default() };
        let mut core = Core::new(opts);
        let mut dev = DeviceLayers::new();
        // two alternating buffers, exactly as softbuffer pools them
        let bufs = [target(pw, ph, t.ds_x, t.ds_y), target(pw, ph, t.ds_x, t.ds_y)];
        let mut prev: Option<Vec<u8>> = None;
        for frame in 0..24 {
            hidpi::set_scale(cache_ds);
            core.advance(1.0 / 60.0);
            let buf = &bufs[frame % 2];
            let cr = Context::new(buf).unwrap();
            // age 0 for the first use of each buffer, then a true 2
            let age = if frame < 2 { 0 } else { 2 };
            let painted = core.compose_damaged(&cr, t, &mut dev, age);
            buf.flush();
            let mut copy = hidpi::copy_exclusive(buf);
            let cur = copy.data().unwrap().to_vec();
            if let (Some(p), Painted::Rects(rects)) = (prev.as_ref(), &painted) {
                let stride = copy.stride() as usize;
                for y in 0..ph as usize {
                    for x in 0..pw as usize {
                        let o = y * stride + x * 4;
                        if p[o..o + 4] == cur[o..o + 4] {
                            continue;
                        }
                        let inside = rects.iter().any(|r| {
                            x >= r.x as usize && x < r.right() as usize
                                && y >= r.y as usize && y < r.bottom() as usize
                        });
                        assert!(inside,
                            "frame {frame}: device pixel {x},{y} changed but is outside \
                             every damage rect {rects:?}");
                    }
                }
            }
            // the reference for the NEXT use of this same buffer
            if frame % 2 == 1 {
                prev = Some(cur);
            }
        }
        hidpi::set_scale(1.0);
    }

    /// Releasing the sprite sources must change NO pixel. The whole case for
    /// `Core::settle` is that the masters are a cache of what is on disk, so
    /// a frame composed after they are handed back has to be byte-identical
    /// to one composed while they were held - including after a layout change
    /// forces every one of them to be decoded again.
    #[test]
    fn releasing_the_sprite_sources_changes_no_pixel() {
        let (cache_ds, win_ds) = (1.5_f64, 1.5_f64);
        crate::ui::fonts::ensure_user_fonts();
        console::pin_clock("04:17:33");
        let shot = |release_first: bool, lw: f64, lh: f64| -> Vec<u8> {
            let (pw, ph) = ((lw * win_ds) as i32, (lh * win_ds) as i32);
            let t = Target {
                logical_w: lw, logical_h: lh, phys_w: pw, phys_h: ph,
                ds_x: pw as f64 / lw, ds_y: ph as f64 / lh, scale: win_ds,
            };
            let mut core = Core::new(Options {
                width: lw as i32, height: lh as i32, ..Options::default()
            });
            let mut dev = DeviceLayers::new();
            let buf = target(pw, ph, t.ds_x, t.ds_y);
            {
                let cr = Context::new(&buf).unwrap();
                hidpi::set_scale(cache_ds);
                // warm everything, then optionally hand the sources back and
                // make the next frame decode them all over again
                core.compose_frame(&cr, t, &mut dev);
                if release_first {
                    let freed = crate::skin::surface::release_sources();
                    assert!(freed > 0, "nothing was resident to release");
                }
                core.compose_frame(&cr, t, &mut dev);
            }
            let mut copy = hidpi::copy_exclusive(&buf);
            let v = copy.data().unwrap().to_vec();
            v
        };
        for (lw, lh) in [(781.0_f64, 468.0_f64), (600.0, 520.0)] {
            let held = shot(false, lw, lh);
            let released = shot(true, lw, lh);
            let diff = held.iter().zip(released.iter()).filter(|(a, b)| a != b).count();
            assert_eq!(
                diff, 0,
                "{lw}x{lh}: {diff} of {} bytes differ after releasing the sprite sources",
                held.len()
            );
        }
        hidpi::set_scale(1.0);
    }

    /// The first review must only MARK, and the second must let go of what
    /// has not been asked for since - and of nothing that has.
    #[test]
    fn settle_gives_back_only_what_nothing_asked_for() {
        crate::ui::fonts::ensure_user_fonts();
        let mut core = Core::new(Options { width: 781, height: 468, ..Options::default() });
        let (pw, ph) = (781, 468);
        let t = Target {
            logical_w: 781.0, logical_h: 468.0, phys_w: pw, phys_h: ph,
            ds_x: 1.0, ds_y: 1.0, scale: 1.0,
        };
        let mut dev = DeviceLayers::new();
        let buf = target(pw, ph, 1.0, 1.0);
        let cr = Context::new(&buf).unwrap();
        hidpi::set_scale(1.0);
        crate::skin::surface::release_sources();
        core.compose_frame(&cr, t, &mut dev);
        let ((n0, b0), _) = crate::skin::surface::cache_inventory();
        assert!(n0 > 0 && b0 > 0, "a frame resident nothing: {n0} sources");

        // first review marks; everything was just used, so nothing goes
        assert_eq!(core.settle(1000.0), 0, "the first review let something go");
        assert_eq!(core.settle(1000.0 + SETTLE_S / 2.0), 0, "a review fired early");
        let ((n1, _), _) = crate::skin::surface::cache_inventory();
        assert_eq!(n1, n0, "the first review dropped {} sources", n0 - n1);

        // ask for exactly one of them again, then review: it stays, the rest go
        let kept = crate::skin::surface::base_cache_entries()[0].0;
        assert!(crate::skin::surface::sprite(kept).is_some());
        let freed = core.settle(1000.0 + SETTLE_S);
        assert!(freed > 0, "the second review let nothing go");
        let ((n2, b2), _) = crate::skin::surface::cache_inventory();
        assert_eq!(n2, 1, "expected only the re-read source to survive, got {n2}");
        assert!(b2 > 0);
        assert_eq!(
            crate::skin::surface::base_cache_entries()[0].0, kept,
            "the review let go of the one source that was asked for"
        );
        crate::skin::surface::release_sources();
    }

    /// A region must NOT pad: it is blitted with an unbounded `paint()`, so
    /// padding would smear the patch across the whole window.
    #[test]
    fn an_unpadded_region_stays_inside_its_rect() {
        for win_ds in [1.0_f64, 1.5, 1.6] {
            let layer = probe_layer(1.5, 20.0, 12.0);
            let (lw, lh) = (120.0_f64, 80.0_f64);
            let (pw, ph) = ((lw * win_ds) as i32, (lh * win_ds) as i32);
            let mut tgt = target(pw, ph, pw as f64 / lw, ph as f64 / lh);
            {
                let cr = Context::new(&tgt).unwrap();
                paint_layer(&cr, Some(&layer), 40.0, 30.0, false);
            }
            // inside the patch
            let (_, _, _, a_in) = px(&mut tgt, (50.0 * win_ds) as i32, (36.0 * win_ds) as i32);
            assert!(a_in > 250, "ds {win_ds}: region did not land at its rect");
            // outside it: untouched
            let (_, _, _, a_out) = px(&mut tgt, (5.0 * win_ds) as i32, (5.0 * win_ds) as i32);
            assert_eq!(a_out, 0, "ds {win_ds}: region smeared outside its rect");
        }
        hidpi::set_scale(1.0);
    }
}
