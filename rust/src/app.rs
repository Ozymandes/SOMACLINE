//! SOMACLINE — GTK4 host, kept as the development reference. Port of app.py.
//!
//! This file is now ONLY the GTK adaptation layer: window, widget, frame
//! clock, GDK textures, GTK event controllers. Everything it decides with is
//! in `crate::host::Core`, which the winit host drives identically.
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
use std::time::Instant;

use gtk4::glib;
use gtk4::prelude::*;
use gtk4::subclass::prelude::*;
use gtk4::{Application, ApplicationWindow, EventControllerKey, EventControllerMotion,
           GestureClick, Snapshot};

use crate::host::{Cmd, Core, Options, TELEMETRY_INTERVAL_MS};
use crate::render;
use crate::ui::console::Layer;
use crate::ui::debug;
use crate::ui::fonts;

pub use crate::host::{APP_ID, FPS_FOCUSED, FPS_UNFOCUSED, PRESS_FEEDBACK_S, TELEMETRY_HZ};

/// GDK texture cache: layer id -> texture, LRU bounded (surfaces held by the
/// console renderer). This is the one piece of state that is GTK's alone -
/// GSK composites textures, so every cached layer is uploaded once.
#[derive(Default)]
struct TextureCache {
    textures: HashMap<u64, gdk4::MemoryTexture>,
    order: VecDeque<u64>,
}

impl TextureCache {
    fn get(&mut self, layer: &Layer) -> gdk4::MemoryTexture {
        if let Some(tex) = self.textures.get(&layer.id) {
            self.order.retain(|id| *id != layer.id);
            self.order.push_back(layer.id);
            return tex.clone();
        }
        // cairo-rs refuses an exclusive data lend while any other reference
        // exists; the console cache always holds one, so the upload goes
        // through hidpi::copy_exclusive (which preserves the device scale -
        // see its docs). Uploads happen only when a layer's content changes,
        // so the copy is off the hot path.
        let (w, h, stride) = (
            layer.surface.width(),
            layer.surface.height(),
            layer.surface.stride(),
        );
        let bytes: glib::Bytes = {
            let mut copy = crate::skin::hidpi::copy_exclusive(&layer.surface);
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
        self.order.push_back(layer.id);
        while self.order.len() > 8 {
            let old = self.order.pop_front().unwrap();
            self.textures.remove(&old);
        }
        tex
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
        pub(super) textures: RefCell<TextureCache>,
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

    /// Carry out what an input asked for, in GTK's idiom.
    fn obey(&self, cmd: Cmd) -> bool {
        match cmd {
            Cmd::Ignored => false,
            Cmd::Redraw => {
                self.queue_draw();
                true
            }
            Cmd::Quit => {
                if let Some(win) = self.window() {
                    win.close();
                }
                true
            }
            Cmd::Fullscreen => {
                if let Some(win) = self.window() {
                    if win.is_fullscreen() {
                        win.unfullscreen();
                    } else {
                        win.fullscreen();
                    }
                }
                true
            }
        }
    }

    // ------------------------------------------------------------ frame loop
    pub fn on_tick(&self, clock: &gdk4::FrameClock) -> glib::ControlFlow {
        let Some(win) = self.window() else {
            return glib::ControlFlow::Continue;
        };
        let now_us = clock.frame_time() as f64;
        let (active, susp) = (win.is_active(), suspended(&win));
        let drew = self.with_core(|core| {
            let fps = core.cadence_now(active, susp);
            match core.due(now_us, fps) {
                Some(dt) => {
                    core.advance(dt);
                    true
                }
                None => false,
            }
        });
        if drew {
            self.queue_draw();
        }
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

        let (layout, glass, vp) = self.with_core(|core| core.frame_geometry(width, height));

        // LAYERS 0-1: background, shell, glass, graticule (static texture)
        let under = self.with_core(|core| core.renderer.layer_under(&layout, &core.model));
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
        let tex = self.imp().textures.borrow_mut().get(layer);
        let (w, h) = (layer.surface.width(), layer.surface.height());
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
        let cmd = self.with_core(|core| core.click(x, y));
        self.obey(cmd);
    }

    pub fn on_motion(&self, x: f64, y: f64) {
        let cmd = self.with_core(|core| core.motion(x, y));
        self.obey(cmd);
    }

    pub fn on_leave(&self) {
        let cmd = self.with_core(|core| core.leave());
        self.obey(cmd);
    }

    pub fn on_key(&self, keyval: gdk4::Key) -> bool {
        let Some(name) = keyval.name().map(|s| s.to_string()) else {
            return false;
        };
        let cmd = self.with_core(|core| core.key_command(&name));
        self.obey(cmd)
    }

    // ------------------------------------------------------------- telemetry
    pub fn on_telemetry(&self) -> glib::ControlFlow {
        self.with_core(|core| core.sample_telemetry());
        glib::ControlFlow::Continue
    }

    pub fn on_probe_sample(&self) -> glib::ControlFlow {
        self.with_core(|core| core.probe_sample());
        glib::ControlFlow::Continue
    }
}

// ---------------------------------------------------------------- window/app
fn build_window(app: &Application, opts: &Options) -> MonitorView {
    let win = ApplicationWindow::builder()
        .application(app)
        .title("Somacline")
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
