//! Headless capture through the WINIT host's composition path.
//!
//! `examples/offscreen.rs` is the proven parity artifact: it reproduces the
//! reference frame layer by layer, by hand. This one drives the real thing -
//! `host::Core::compose_frame`, the exact function the winit host calls every
//! frame - over the identical deterministic fixture, so the two PNGs can be
//! diffed against each other (does the new paint path change any pixel?) and
//! against `qa/offscreen.py` (does the winit host still match Python?).
//!
//!     compose <out.png> [--width W] [--height H] [--specimen N]
//!             [--seconds S] [--ds SCALE]

use abyssal::host::{Core, Options};

fn main() {
    std::env::remove_var("CARGO_MANIFEST_DIR");
    let mut args = std::env::args().skip(2);
    let out = std::env::args().nth(1).unwrap_or_else(|| "/tmp/compose.png".into());
    let mut width = 1400;
    let mut height = 880;
    let mut specimen = 0usize;
    let mut seconds = 6.0f64;
    let mut ds = 1.0f64;
    while let Some(a) = args.next() {
        let mut val = || args.next().and_then(|v| v.parse().ok()).unwrap_or(0.0);
        match a.as_str() {
            "--width" => width = val() as i32,
            "--height" => height = val() as i32,
            "--specimen" => specimen = val() as usize,
            "--seconds" => seconds = val(),
            "--ds" => ds = val(),
            _ => {}
        }
    }
    let (w, h) = (width.max(1), height.max(1));
    abyssal::ui::fonts::ensure_user_fonts();

    let mut core = Core::new(Options {
        width: w,
        height: h,
        specimen,
        ..Options::default()
    });

    // The same normalised channels qa/offscreen.py and examples/offscreen.rs
    // use, derived exactly as telemetry.source derives them.
    core.telemetry = abyssal::signals::Telemetry {
        cpu_load: 0.14,
        memory_pressure: 8.2 / 13.5,
        temperature: (83.0 - 30.0) / (95.0 - 30.0),
        io_rate: 0.22,
        temp_available: true,
        io_available: true,
        cpu_pct: 14.0,
        mem_used_gb: 8.2,
        mem_total_gb: 13.5,
        temp_c: Some(83.0),
        temp_label: "CPU".into(),
        ..Default::default()
    };
    // Core::advance IS the app's simulation step, so stepping it here is the
    // same integration the two reference harnesses perform by hand.
    let dt = 1.0 / 60.0;
    for _ in 0..(seconds / dt) as usize {
        core.advance(dt);
    }

    core.model.mode_state = "armed".into();
    {
        let org = core.organisms[specimen].as_ref().unwrap();
        let (px, py, pw) = org.points();
        let tot = pw.iter().sum::<f32>() as f64;
        let tot = if tot == 0.0 { 1.0 } else { tot };
        let fx = px.iter().zip(pw.iter()).map(|(x, w)| x * w).sum::<f32>() as f64;
        let fy = py.iter().zip(pw.iter()).map(|(y, w)| y * w).sum::<f32>() as f64;
        core.model.coords = (fx / tot / 400.0, fy / tot / 400.0, 0.0);
    }
    // Prime a real 60 s history at the app's telemetry cadence.
    {
        let cap = core.model.history.borrow().cap;
        let (cpu_pct, temp_c, mem_used_gb) = (
            core.telemetry.cpu_pct,
            core.telemetry.temp_c,
            core.telemetry.mem_used_gb,
        );
        for i in 0..cap {
            let if_ = i as f64;
            let cpu = (cpu_pct + 9.0 * (if_ * 0.05).sin() + 4.0 * (if_ * 0.61).sin())
                .clamp(0.0, 100.0);
            let tempc = temp_c.unwrap_or(60.0) - 3.0 + 3.5 * (if_ * 0.037).sin()
                + 1.2 * (if_ * 0.4).sin();
            let mem = mem_used_gb - 0.6 + 0.4 * (if_ * 0.013).sin()
                + 0.2 * (if_ / cap as f64);
            let frame = 1000.0 / 60.0 + 1.4 * (if_ * 0.29).sin()
                + if i % 97 < 3 { 6.0 } else { 0.0 };
            core.model.history.borrow_mut().push(cpu, Some(tempc), mem, frame);
        }
    }
    // The regions cache keys on the DISPLAYED fps, which the reference
    // harnesses pin at 60.
    core.fps = 60.0;
    core.frame_ms = 1000.0 / 60.0;

    // The live host's two scales, exactly as winit_host::redraw sets them:
    // the caches quantise the raw scale factor; the target carries the exact
    // physical/logical ratio.
    abyssal::skin::hidpi::set_scale(ds);
    let target = abyssal::host::Target::exact(w as f64, h as f64, ds);
    let (pw, ph) = (target.phys_w, target.phys_h);
    let surface =
        cairo::ImageSurface::create(cairo::Format::ARgb32, pw, ph).expect("target");
    surface.set_device_scale(target.ds_x, target.ds_y);
    {
        let cr = cairo::Context::new(&surface).unwrap();
        let mut dev = abyssal::host::DeviceLayers::new();
        core.compose_frame(&cr, target, &mut dev);
    }
    surface.flush();
    let mut png = std::fs::File::create(&out).unwrap();
    surface.write_to_png(&mut png).unwrap();
    println!("{out}  {w}x{h} ds {ds} specimen {specimen}  ({pw}x{ph} device)");
}
