//! ABYSSAL ORGANISM MONITOR — host. Port of app.py.
//!
//! HOST CONTRACT: the window contains exactly ONE widget (MonitorView). There
//! is no widget tree to relayout, no GL context beyond GSK's, and no render
//! thread. A resize CANNOT churn rendering state: the next draw receives
//! different width/height, from which a fresh Layout and Viewport are computed.
//! Simulation state is a function of TIME ONLY and is never touched by geometry.
//!
//! Frame loop: GdkFrameClock tick -> advance sim -> queue_draw.
//! Draw: pure function of (sim state, width, height) + cached layers.

use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::rc::Rc;
use std::time::Instant;

use gtk4::glib;
use gtk4::prelude::*;
use gtk4::subclass::prelude::*;
use gtk4::{Application, ApplicationWindow, EventControllerKey, EventControllerMotion,
           GestureClick, Snapshot};

use crate::layout::{self, Layout, LayoutState};
use crate::lighting::LightField;
use crate::physiology::PhysiologyModel;
use crate::render::{self, RenderCaches};
use crate::signals::Telemetry;
use crate::species;
use crate::telemetry::{History, TelemetrySource};
use crate::ui::console::{self, CtlKind, ConsoleModel, Layer, Renderer};
use crate::ui::debug::{self, DebugInfo};
use crate::ui::fonts;
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

/// The core simulation + presentation state. One instance, owned by the view.
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

    // texture cache: layer id -> texture, LRU bounded (surfaces held by console)
    pub textures: HashMap<u64, gdk4::MemoryTexture>,
    pub texture_order: VecDeque<u64>,

    pub last_layout: Option<Layout>,
    pub last_vp: Option<Viewport>,
}

impl Core {
    fn new(opts: Options) -> Core {
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
            textures: HashMap::new(),
            texture_order: VecDeque::new(),
            last_layout: None,
            last_vp: None,
        }
    }

    fn now(&self) -> f64 {
        self.t0.elapsed().as_secs_f64()
    }

    fn organism(&mut self, i: usize) -> &mut crate::mathforms::SourceBody {
        let seed = self.opts.seed;
        self.organisms[i].get_or_insert_with(|| {
            crate::mathforms::SourceBody::build(species::by_index(i).source, seed + i as u64)
        })
    }

    /// Switch channels. Never resets the clock or the telemetry history.
    pub fn select_specimen(&mut self, i: usize, tactile: bool, queue: &impl Fn()) {
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
        queue();
    }

    pub fn cycle_specimen(&mut self, step: isize, queue: &impl Fn()) {
        let i = (self.species_index as isize + step).rem_euclid(species::COUNT as isize);
        self.select_specimen(i as usize, false, queue);
    }

    /// Target frames per second for the current window state; 0 = idle.
    fn cadence_now(&self, win: &ApplicationWindow) -> f64 {
        if suspended(win) {
            return 0.0;
        }
        if self.opts.fps_cap > 0.0 {
            return self.opts.fps_cap;
        }
        if win.is_active() {
            FPS_FOCUSED
        } else {
            FPS_UNFOCUSED
        }
    }

    fn log_probe(&mut self, layout: &Layout, vp: &Viewport, event: &str) {
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
    fn refresh_field(&mut self, vp: &Viewport) {
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

    fn tick_fps(&mut self) {
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

    fn texture(&mut self, layer: &Layer) -> gdk4::MemoryTexture {
        if let Some(tex) = self.textures.get(&layer.id) {
            self.texture_order.retain(|id| *id != layer.id);
            self.texture_order.push_back(layer.id);
            return tex.clone();
        }
        // cairo-rs refuses an exclusive data lend while any other reference
        // exists; the console cache always holds one. Blit into a fresh
        // surface with the context scoped out, then read. Uploads happen only
        // when a layer's content changes, so the copy is off the hot path.
        let mut surf = layer.surface.clone();
        surf.flush();
        let (w, h, stride) = (surf.width(), surf.height(), surf.stride());
        let bytes: glib::Bytes = {
            let mut copy = cairo::ImageSurface::create(cairo::Format::ARgb32, w, h)
                .expect("copy surface");
            {
                let cr = cairo::Context::new(&copy).expect("copy context");
                cr.set_source_surface(&surf, 0.0, 0.0).expect("copy source");
                cr.paint().expect("copy paint");
            }
            copy.flush();
            let d = copy.data().expect("copy surface data");
            glib::Bytes::from(&d[..])
        };
        // cairo ARGB32 is native-endian premultiplied: B,G,R,A in memory here
        let tex = gdk4::MemoryTexture::new(
            w,
            h,
            gdk4::MemoryFormat::B8g8r8a8Premultiplied,
            &bytes,
            stride as usize,
        );
        self.textures.insert(layer.id, tex.clone());
        self.texture_order.push_back(layer.id);
        while self.texture_order.len() > 8 {
            let old = self.texture_order.pop_front().unwrap();
            self.textures.remove(&old);
        }
        tex
    }

    fn debug_info(&self, layout: &Layout, gdk_scale: f64) -> DebugInfo {
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
}

fn suspended(win: &ApplicationWindow) -> bool {
    win.native()
        .and_then(|n| n.surface())
        .and_then(|s| s.downcast::<gdk4::Toplevel>().ok())
        .map(|t| t.state().contains(gdk4::ToplevelState::SUSPENDED))
        .unwrap_or(false)
}

fn device_scale(widget: &impl IsA<gtk4::Widget>) -> f64 {
    widget
        .native()
        .and_then(|n| n.surface())
        .map(|s| s.scale())
        .unwrap_or(1.0)
}

pub mod imp {
    use super::*;

    #[derive(Default)]
    pub struct MonitorViewImp {
        pub core: RefCell<Option<Core>>,
        pub ds_logged: std::cell::Cell<bool>,
        pub reg_logged: std::cell::Cell<bool>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for MonitorViewImp {
        const NAME: &'static str = "AbyssalMonitorView";
        type Type = super::MonitorView;
        type ParentType = gtk4::Widget;
    }

    impl ObjectImpl for MonitorViewImp {}

    impl WidgetImpl for MonitorViewImp {
        fn snapshot(&self, snapshot: &Snapshot) {
            self.obj().draw_frame(snapshot);
        }
    }
}

glib::wrapper! {
    pub struct MonitorView(ObjectSubclass<imp::MonitorViewImp>)
        @extends gtk4::Widget,
        @implements gtk4::Accessible, gtk4::Buildable, gtk4::ConstraintTarget;
}

impl MonitorView {
    pub fn new(opts: &Options) -> MonitorView {
        let view: MonitorView = glib::Object::new();
        view.set_hexpand(true);
        view.set_vexpand(true);
        *view.imp().core.borrow_mut() = Some(Core::new(opts.clone()));
        view
    }

    pub fn core(&self) -> std::cell::Ref<'_, Core> {
        std::cell::Ref::map(self.imp().core.borrow(), |c| c.as_ref().unwrap())
    }

    pub fn core_mut(&self) -> std::cell::RefMut<'_, Core> {
        std::cell::RefMut::map(self.imp().core.borrow_mut(), |c| c.as_mut().unwrap())
    }

    pub fn with_core<R>(&self, f: impl FnOnce(&mut Core) -> R) -> R {
        let mut core = self.imp().core.borrow_mut();
        f(core.as_mut().unwrap())
    }

    fn window(&self) -> Option<ApplicationWindow> {
        self.root().and_downcast::<ApplicationWindow>()
    }

    // ------------------------------------------------------------ frame loop
    pub fn on_tick(&self, clock: &gdk4::FrameClock) -> glib::ControlFlow {
        let Some(win) = self.window() else {
            return glib::ControlFlow::Continue;
        };
        let now_us = clock.frame_time() as f64;
        let fps = self.with_core(|core| core.cadence_now(&win));
        self.with_core(|core| core.cadence = fps);
        if fps <= 0.0 {
            // Nothing is looking: no simulation, no draw. The next visible
            // tick resumes with a clamped dt, so nothing teleports.
            return glib::ControlFlow::Continue;
        }
        let interval_us = 1_000_000.0 / fps;
        // Pace on a fixed schedule (a phase accumulator), not on "time since
        // the last frame". Half a refresh of tolerance takes the nearest tick.
        let tol = (interval_us * 0.5).min(4000.0);
        let next_us = self.with_core(|core| core.next_us);
        if next_us != 0.0 && now_us < next_us - tol {
            return glib::ControlFlow::Continue;
        }
        let next = self.with_core(|core| {
            core.next_us = if core.next_us != 0.0 && now_us - core.next_us < interval_us {
                core.next_us + interval_us
            } else {
                now_us + interval_us
            };
            if core.last_tick_us == 0.0 {
                1.0 / 60.0
            } else {
                (now_us - core.last_tick_us) / 1_000_000.0
            }
        });
        let dt = self.with_core(|core| {
            core.last_tick_us = now_us;
            // Clamp: an occluded/unmapped window stops ticking; on resume the
            // gap must not teleport the organism.
            next.clamp(0.0, 0.1)
        });

        let t0 = Instant::now();
        self.with_core(|core| {
            let telemetry = core.telemetry.clone();
            let phys = core.phys_model.update(dt, &telemetry, 0.0);
            core.organism(core.species_index).update(dt, &phys);
            core.sim_ms = t0.elapsed().as_secs_f64() * 1000.0;
        });
        self.queue_draw();
        glib::ControlFlow::Continue
    }

    // ------------------------------------------------------------------ draw
    fn draw_frame(&self, snapshot: &Snapshot) {
        let width = self.width() as f64;
        let height = self.height() as f64;
        if width < 1.0 || height < 1.0 {
            return;
        }
        let t0 = Instant::now();
        let ds = device_scale(self);
        crate::skin::hidpi::set_scale(ds);

        // THE ENTIRE RESIZE RESPONSE, IN FULL:
        let layout = layout::resolve(width, height);
        let glass = console::stage_content(&layout);
        let vp = Viewport::for_stage(glass.x, glass.y, glass.w, glass.h);
        // (that is all — no allocation, no rebuild, no reset)

        if (width as i32, height as i32) != self.with_core(|c| c.last_size) {
            self.with_core(|core| {
                core.resizes += 1;
                core.last_size = (width as i32, height as i32);
                core.log_probe(&layout, &vp, "resize");
            });
        }
        if self.with_core(|c| c.last_state) != Some(layout.state) {
            self.with_core(|core| {
                core.last_state = Some(layout.state);
                core.log_probe(&layout, &vp, "state");
            });
        }

        // Momentary key feedback expires on a clock, not on a frame count.
        self.with_core(|core| {
            if core.press_index.is_some() && core.now() >= core.press_until {
                core.press_index = None;
            }
            core.model.pressed = core.press_index;
        });
        self.with_core(|core| core.refresh_field(&vp));

        // LAYERS 0-1: background, shell, glass, graticule (static texture)
        let under = self.with_core(|core| core.renderer.layer_under(&layout, &core.model));
        if self.with_core(|c| c.frames) % 60 == 0 {
            if let Some(u) = &under {
                eprintln!("DBG f={} under_dims={}x{} ds_raw={} hidpi={} widget={}x{}", self.with_core(|c| c.frames), u.surface.width(), u.surface.height(), device_scale(self), crate::skin::hidpi::scale(), self.width(), self.height());
            }
        }
        let over = self.with_core(|core| {
            core.renderer
                .layer_over(&layout, &core.model, &core.telemetry)
        });
        self.put_layer(snapshot, under.as_ref(), 0.0, 0.0);
        // LAYER 2: the organism - the only per-frame raster, glass only
        if glass.valid() {
            let rect = gtk4::graphene::Rect::new(
                glass.x as f32,
                glass.y as f32,
                glass.w as f32,
                glass.h as f32,
            );
            let cr = snapshot.append_cairo(&rect);
            let _ = cr.rectangle(glass.x, glass.y, glass.w, glass.h);
            let _ = cr.clip();
            self.with_core(|core| {
                let Core {
                    organisms,
                    species_index,
                    rcaches,
                    show_calibration,
                    ..
                } = core;
                let org = organisms[*species_index].as_ref().unwrap();
                render::draw_organism(&cr, &vp, org, rcaches);
                if *show_calibration {
                    render::draw_calibration(&cr, &vp, org);
                }
            });
        }
        // LAYER 3: the structural modules and static type (static texture)
        self.put_layer(snapshot, over.as_ref(), 0.0, 0.0);
        // LAYERS 4-6: live regions, each re-rendered only on its own change
        let regions = self.with_core(|core| {
            core.renderer.regions(
                &layout,
                &core.model,
                &core.telemetry,
                core.fps,
                core.frame_ms,
                under.as_ref(),
                over.as_ref(),
            )
        });
        for reg in &regions {
            self.put_layer(snapshot, Some(&reg.layer), reg.rect.x, reg.rect.y);
        }

        self.with_core(|core| {
            core.draw_ms = t0.elapsed().as_secs_f64() * 1000.0;
            core.draw_acc += core.draw_ms;
            if core.show_debug {
                let gdk_scale = ds;
                let info = core.debug_info(&layout, gdk_scale);
                let rect =
                    gtk4::graphene::Rect::new(0.0, 0.0, width as f32, height as f32);
                let cr = snapshot.append_cairo(&rect);
                debug::draw_debug(&cr, &layout, &vp, &info);
            }
            core.frames += 1;
            core.tick_fps();
            core.last_layout = Some(layout);
            core.last_vp = Some(vp);
        });
    }

    fn put_layer(&self, snapshot: &Snapshot, layer: Option<&Layer>, x: f64, y: f64) {
        let Some(layer) = layer else { return };
        let (tex, w, h) = self.with_core(|core| {
            let tex = core.texture(layer);
            (tex, layer.surface.width(), layer.surface.height())
        });
        // app.py divides by hidpi.scale() (the quantized scale the caches
        // were rendered at), never by the raw surface scale.
        let ds = crate::skin::hidpi::scale().max(0.001);
        let rect = gtk4::graphene::Rect::new(
            x as f32,
            y as f32,
            w as f32 / ds as f32,
            h as f32 / ds as f32,
        );
        snapshot.append_texture(&tex, &rect);
    }

    // ------------------------------------------------------------------ input
    pub fn on_click(&self, _n_press: i32, x: f64, y: f64) {
        let layout = self.with_core(|c| c.last_layout);
        let Some(layout) = layout else { return };
        let target = console::hit_controls(&layout, x, y);
        let Some((kind, idx)) = target else { return };
        match kind {
            CtlKind::Key => self.with_core(|core| {
                core.select_specimen(idx, true, &|| self.queue_draw())
            }),
            CtlKind::Cycle => self.with_core(|core| {
                core.cycle_specimen(if idx > 0 { 1 } else { -1 }, &|| self.queue_draw())
            }),
            CtlKind::Mode => {
                self.with_core(|core| {
                    let order = ["inactive", "armed", "active", "error"];
                    let cur = order
                        .iter()
                        .position(|s| *s == core.model.mode_state)
                        .unwrap_or(0);
                    core.model.mode_state = order[(cur + 1) % order.len()].to_string();
                });
                self.queue_draw();
            }
        }
    }

    pub fn on_motion(&self, x: f64, y: f64) {
        let layout = self.with_core(|c| c.last_layout);
        let Some(layout) = layout else { return };
        let target = console::hit_controls(&layout, x, y);
        let focus = match target {
            Some((CtlKind::Key, idx)) => Some(idx),
            _ => None,
        };
        let changed = self.with_core(|core| {
            if core.model.focus != focus {
                core.model.focus = focus;
                true
            } else {
                false
            }
        });
        if changed {
            self.queue_draw();
        }
    }

    pub fn on_leave(&self) {
        let changed = self.with_core(|core| {
            if core.model.focus.is_some() {
                core.model.focus = None;
                true
            } else {
                false
            }
        });
        if changed {
            self.queue_draw();
        }
    }

    pub fn on_key(&self, keyval: gdk4::Key) -> bool {
        let name = keyval.name().map(|s| s.to_string());
        let Some(name) = name else { return false };
        let chars = name.chars().next();
        if name.len() == 1 && chars.map(|c| c.is_ascii_digit()).unwrap_or(false) {
            let d = chars.unwrap();
            if d != '0' {
                let n = (d as u8 - b'1') as usize;
                if n < species::COUNT {
                    self.with_core(|core| {
                        core.select_specimen(n, true, &|| self.queue_draw())
                    });
                    return true;
                }
            }
        }
        match name.as_str() {
            "Right" | "Down" | "n" | "N" => {
                self.with_core(|core| core.cycle_specimen(1, &|| self.queue_draw()));
                true
            }
            "Left" | "Up" | "p" | "P" => {
                self.with_core(|core| core.cycle_specimen(-1, &|| self.queue_draw()));
                true
            }
            "m" | "M" => {
                self.with_core(|core| {
                    let order = ["inactive", "armed", "active", "error"];
                    let cur = order
                        .iter()
                        .position(|s| *s == core.model.mode_state)
                        .unwrap_or(0);
                    core.model.mode_state = order[(cur + 1) % order.len()].to_string();
                });
                self.queue_draw();
                true
            }
            "q" | "Q" | "Escape" => {
                if let Some(win) = self.window() {
                    win.close();
                }
                true
            }
            "F1" => {
                self.with_core(|core| {
                    core.show_debug = !core.show_debug;
                    core.model.diag = core.show_debug;
                });
                self.queue_draw();
                true
            }
            "F2" => {
                self.with_core(|core| core.show_calibration = !core.show_calibration);
                self.queue_draw();
                true
            }
            "f" | "F" | "F11" => {
                if let Some(win) = self.window() {
                    if win.is_fullscreen() {
                        win.unfullscreen();
                    } else {
                        win.fullscreen();
                    }
                }
                true
            }
            _ => false,
        }
    }

    // ------------------------------------------------------------- telemetry
    pub fn on_telemetry(&self) -> glib::ControlFlow {
        let t0 = Instant::now();
        self.with_core(|core| {
            let mut tel = core.telemetry_src.sample();
            if core.opts.qa_temp != 0.0 {
                // QA only (--qa-temp): substitute the temperature READING so
                // the thermal states can be photographed on a cool machine.
                tel.temp_c = Some(core.opts.qa_temp);
                tel.temp_available = true;
                tel.temperature =
                    ((core.opts.qa_temp - 30.0) / 65.0).clamp(0.0, 1.0);
            }
            let frame_ms = core.frame_ms;
            core.telemetry = tel.clone();
            core.model.history.borrow_mut().push(
                tel.cpu_pct,
                tel.temp_c,
                tel.mem_used_gb,
                frame_ms,
            );
            core.tel_ms = t0.elapsed().as_secs_f64() * 1000.0;
        });
        glib::ControlFlow::Continue
    }

    pub fn on_probe_sample(&self) -> glib::ControlFlow {
        self.with_core(|core| {
            if let (Some(layout), Some(vp)) = (core.last_layout, core.last_vp) {
                core.log_probe(&layout, &vp, "sample");
            }
        });
        glib::ControlFlow::Continue
    }
}

// ---------------------------------------------------------------- window/app
fn build_window(app: &Application, opts: &Options) -> MonitorView {
    let win = ApplicationWindow::builder()
        .application(app)
        .title("Abyssal Organism Monitor")
        .default_width(opts.width)
        .default_height(opts.height)
        .build();

    // Display faces must exist in a scanned font directory before the first
    // Pango font map is built (ui/fonts.py contract).
    fonts::ensure_user_fonts();

    let view = MonitorView::new(opts);
    win.set_child(Some(&view));

    let keys = EventControllerKey::new();
    {
        let view = view.clone();
        keys.connect_key_pressed(move |_c, keyval, _code, _mods| {
            if view.on_key(keyval) {
                glib::Propagation::Stop
            } else {
                glib::Propagation::Proceed
            }
        });
    }
    view.add_controller(keys);

    let click = GestureClick::new();
    {
        let view = view.clone();
        click.connect_pressed(move |_g, n_press, x, y| view.on_click(n_press, x, y));
    }
    view.add_controller(click);

    let motion = EventControllerMotion::new();
    {
        let view = view.clone();
        motion.connect_motion(move |_c, x, y| view.on_motion(x, y));
    }
    {
        let view = view.clone();
        motion.connect_leave(move |_c| view.on_leave());
    }
    view.add_controller(motion);

    view.add_tick_callback(|widget, clock| {
        let Some(view) = widget.downcast_ref::<MonitorView>() else {
            return glib::ControlFlow::Break;
        };
        view.on_tick(clock)
    });

    let view_t = view.clone();
    glib::timeout_add_local(
        std::time::Duration::from_millis(TELEMETRY_INTERVAL_MS),
        move || view_t.on_telemetry(),
    );
    {
        let view = view.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(2000), move || {
            view.on_probe_sample()
        });
    }

    win.present();
    view
}

/// Entry point: `abyssal::app::run(&args)`.
pub fn run(args: &[String]) -> i32 {
    let opts = Options::parse(args);
    let quit_after = opts.quit_after;
    let app = Application::builder()
        .application_id(APP_ID)
        .flags(gio::ApplicationFlags::NON_UNIQUE)
        .build();
    let opts_for_activate = opts.clone();
    app.connect_activate(move |app| {
        let view = build_window(app, &opts_for_activate);
        if quit_after > 0.0 {
            let app_q = app.clone();
            let view_q = view.clone();
            glib::timeout_add_local(
                std::time::Duration::from_secs_f64(quit_after),
                move || {
                    if let Some(win) = view_q.window() {
                        win.close();
                    }
                    app_q.quit();
                    glib::ControlFlow::Break
                },
            );
        }
    });
    let no_args: &'static [String] = Box::leak(Vec::new().into_boxed_slice());
    app.run_with_args(no_args).into()
}
