//! Framework floor probe: how much PSS does each framework stage cost?
//!
//! Usage: floor <stage> [dwell_seconds]
//!   empty                    no GTK at all
//!   gtk                      GTK4 app, window shown, never drawn
//!   gtk-cairo                + a 900x700 drawing area repainting cairo per frame
//!   gtk-cairo-pango          + Pango text shaping/drawing on every frame
//!   gtk-cairo-pango-fonts    + the bundled Astro/Microgramma faces installed
//!                            and used for the drawn text
//!
//! After the dwell it prints one JSON line: rss_kb / pss_kb / anon_kb /
//! swap_kb from /proc/self/smaps_rollup, then exits.

use std::str::FromStr;

use gtk4::prelude::*;
use gtk4::{Application, ApplicationWindow, DrawingArea};


const APP_ID: &str = "dev.abyssal.floor";

fn smaps_rollup() -> (f64, f64, f64, f64) {
    let mut rss = 0.0;
    let mut pss = 0.0;
    let mut anon = 0.0;
    let mut swap = 0.0;
    if let Ok(text) = std::fs::read_to_string("/proc/self/smaps_rollup") {
        for line in text.lines() {
            let mut it = line.split_whitespace();
            let field = it.next().unwrap_or("");
            let val: f64 = it.next().and_then(|v| f64::from_str(v).ok()).unwrap_or(0.0);
            match field {
                "Rss:" => rss = val,
                "Pss:" => pss = val,
                "Anonymous:" => anon = val,
                "Swap:" => swap = val,
                _ => {}
            }
        }
    }
    (rss, pss, anon, swap)
}

/// Copy the bundled faces into the user font directory, like ui/fonts.py.
fn ensure_user_fonts() {
    let Some(home) = std::env::var_os("HOME") else { return };
    let home = std::path::PathBuf::from(home);
    // probe lives in rust/floor; assets are three levels up from it
    let src_dir = std::path::Path::new("assets/fonts");
    let dst_dir = home.join(".local/share/fonts/abyssal");
    let _ = std::fs::create_dir_all(&dst_dir);
    let mut changed = false;
    for name in ["astro.ttf", "microgrammanormal.ttf"] {
        let (src, dst) = (src_dir.join(name), dst_dir.join(name));
        if let Ok(bytes) = std::fs::read(&src) {
            let differs = match std::fs::read(&dst) {
                Ok(old) => old != bytes,
                Err(_) => true,
            };
            if differs {
                let _ = std::fs::write(&dst, &bytes);
                changed = true;
            }
        }
    }
    if changed {
        let _ = std::process::Command::new("fc-cache")
            .arg("-f")
            .arg(&dst_dir)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let stage: String = args.get(1).cloned().unwrap_or_else(|| "empty".to_string());
    let dwell: u64 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(8);

    let fonts = stage.ends_with("fonts");
    if fonts {
        ensure_user_fonts();
    }

    if stage == "empty" {
        std::thread::sleep(std::time::Duration::from_secs(dwell));
        report(stage, dwell);
        return;
    }

    let app = Application::builder().application_id(APP_ID).build();
    app.connect_activate(move |app| {
        let win = ApplicationWindow::builder()
            .application(app)
            .title("floor")
            .default_width(900)
            .default_height(700)
            .build();

        if stage.starts_with("gtk-cairo") {
            let area = DrawingArea::new();
            area.set_content_width(900);
            area.set_content_height(700);
            let use_pango = stage.contains("pango");
            let droid = std::cell::Cell::new(0u64);
            area.set_draw_func(move |_area, cr, w, h| {
                let n = droid.get() as f64;
                droid.set(droid.get() + 1);
                // cheap but real per-frame cairo work: a moving gradient disc
                let cx = (w as f64) * (0.5 + 0.3 * (n * 0.05).sin());
                let cy = (h as f64) * (0.5 + 0.3 * (n * 0.043).cos());
                let g = cairo::RadialGradient::new(cx, cy, 10.0, cx, cy, 300.0);
                g.add_color_stop_rgba(0.0, 0.2, 0.5, 0.7, 0.9);
                g.add_color_stop_rgba(1.0, 0.02, 0.04, 0.08, 0.0);
                cr.set_source(&g).unwrap();
                cr.arc(cx, cy, 300.0, 0.0, std::f64::consts::TAU);
                cr.fill().unwrap();
                if use_pango {
                    let fam = if fonts { "Astro 24" } else { "sans 24" };
                    let lay = pangocairo::functions::create_layout(&cr);
                    let mut desc = pango::FontDescription::from_string(fam);
                    desc.set_weight(pango::Weight::Bold);
                    lay.set_font_description(Some(&desc));
                    lay.set_text("ABYSSAL ORGANISM MONITOR AQS-0042");
                    cr.set_source_rgba(0.7, 0.85, 0.95, 0.9);
                    cr.move_to(20.0 + 5.0 * (n * 0.03).sin(), 30.0);
                    pangocairo::functions::show_layout(&cr, &lay);
                    desc.set_family("Microgramma");
                    lay.set_font_description(Some(&desc));
                    lay.set_text("TCTL 067.4C  CPU 042.1%  MEM 18.4/32.0GB");
                    cr.move_to(20.0, 70.0 + 3.0 * (n * 0.021).cos());
                    pangocairo::functions::show_layout(&cr, &lay);
                }
            });
            // drive ~60fps
            let area_repaint = area.clone();
            glib::timeout_add_local(std::time::Duration::from_millis(16), move || {
                area_repaint.queue_draw();
                glib::ControlFlow::Continue
            });
            win.set_child(Some(&area));
        }

        win.present();
        let app_quit = app.clone();
        let stage_owned = stage.clone();
        glib::timeout_add_local(std::time::Duration::from_secs(dwell), move || {
            report(&stage_owned, dwell);
            app_quit.quit();
            glib::ControlFlow::Break
        });
    });
    let no_args: &'static [String] = Box::leak(Vec::new().into_boxed_slice());
    app.run_with_args(no_args);
}

fn report(stage: &str, dwell: u64) {
    let (rss, pss, anon, swap) = smaps_rollup();
    println!(
        "{{\"stage\":\"{}\",\"dwell_s\":{},\"rss_kb\":{},\"pss_kb\":{},\"anon_kb\":{},\"swap_kb\":{}}}",
        stage, dwell, rss, pss, anon, swap
    );
}
