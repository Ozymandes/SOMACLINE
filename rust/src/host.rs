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

use crate::layout::{self, Layout, LayoutState, Rect};
use crate::lighting::LightField;
use crate::physiology::PhysiologyModel;
use crate::render::RenderCaches;
use crate::signals::Telemetry;
use crate::species;
use crate::telemetry::{History, TelemetrySource};
use crate::ui::console::{self, CtlKind, ConsoleModel, Renderer};
use crate::ui::debug::DebugInfo;
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

    pub fn probe_sample(&mut self) {
        if let (Some(layout), Some(vp)) = (self.last_layout, self.last_vp) {
            self.log_probe(&layout, &vp, "sample");
        }
    }
}
