//! Where does a winit frame's time go? Composes N frames and times the parts.
//!
//!     framebench [--width W] [--height H] [--cache-ds C] [--target-ds T] [--n N]
//!
//! `--cache-ds` is what `skin::hidpi` quantises to (what the layer caches are
//! rendered at); `--target-ds` is the window's real device scale. When they
//! differ, every full-window blit is a bilinear resample.

use std::time::Instant;

use abyssal::host::{Core, DeviceLayers, Options, Target};

fn main() {
    std::env::remove_var("CARGO_MANIFEST_DIR");
    let mut args = std::env::args().skip(1);
    let (mut w, mut h) = (781.0_f64, 468.0_f64);
    let (mut cache_ds, mut target_ds) = (1.5_f64, 1.6_f64);
    let mut n = 240usize;
    while let Some(a) = args.next() {
        let mut val = || args.next().and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0);
        match a.as_str() {
            "--width" => w = val(),
            "--height" => h = val(),
            "--cache-ds" => cache_ds = val(),
            "--target-ds" => target_ds = val(),
            "--n" => n = val() as usize,
            _ => {}
        }
    }
    abyssal::ui::fonts::ensure_user_fonts();
    let mut core = Core::new(Options { width: w as i32, height: h as i32, ..Options::default() });
    core.telemetry = abyssal::signals::Telemetry {
        cpu_load: 0.14, memory_pressure: 0.6, temperature: 0.8, io_rate: 0.22,
        temp_available: true, io_available: true, cpu_pct: 14.0,
        mem_used_gb: 8.2, mem_total_gb: 13.5, temp_c: Some(83.0),
        temp_label: "CPU".into(), ..Default::default()
    };
    core.fps = 60.0;
    core.frame_ms = 1000.0 / 60.0;
    for _ in 0..120 { core.advance(1.0 / 60.0); }

    abyssal::skin::hidpi::set_scale(cache_ds);
    let (pw, ph) = ((w * target_ds).round() as i32, (h * target_ds).round() as i32);
    let surface = cairo::ImageSurface::create(cairo::Format::ARgb32, pw, ph).unwrap();
    surface.set_device_scale(pw as f64 / w, ph as f64 / h);
    let cr = cairo::Context::new(&surface).unwrap();
    let target = Target {
        logical_w: w, logical_h: h, phys_w: pw, phys_h: ph,
        ds_x: pw as f64 / w, ds_y: ph as f64 / h, scale: target_ds,
    };
    let mut dev = DeviceLayers::new();

    // warm every cache first; only steady-state frames are timed
    for _ in 0..8 { core.compose_frame(&cr, target, &mut dev); }

    // --- whole frame (composition only; the sim is timed separately) ---
    let t = Instant::now();
    for _ in 0..n { core.compose_frame(&cr, target, &mut dev); }
    let whole = t.elapsed().as_secs_f64() * 1000.0 / n as f64;
    let t = Instant::now();
    for _ in 0..n { core.advance(1.0 / 60.0); }
    let sim = t.elapsed().as_secs_f64() * 1000.0 / n as f64;

    // --- the parts, timed on their own. The two full-window blits below are
    // what a frame WOULD cost without the device-resolution cache: they are
    // the resample GSK used to do on the GPU. compose_frame no longer pays
    // them, so they do not sum into it. ---
    let layout = abyssal::layout::resolve(w, h);
    let under = core.renderer.layer_under(&layout, &core.model).unwrap();
    let over = core.renderer.layer_over(&layout, &core.model, &core.telemetry).unwrap();

    let clear = bench(n, || {
        let _ = cr.save();
        cr.set_operator(cairo::Operator::Source);
        cr.set_source_rgb(0.0, 0.0, 0.0);
        let _ = cr.paint();
        let _ = cr.restore();
    });
    let blit_under = bench(n, || {
        let _ = cr.set_source_surface(&under.surface, 0.0, 0.0);
        cr.source().set_extend(cairo::Extend::Pad);
        let _ = cr.paint();
    });
    let blit_over = bench(n, || {
        let _ = cr.set_source_surface(&over.surface, 0.0, 0.0);
        cr.source().set_extend(cairo::Extend::Pad);
        let _ = cr.paint();
    });
    let glass = abyssal::ui::console::stage_content(&layout);
    let vp = abyssal::viewport::Viewport::for_stage(glass.x, glass.y, glass.w, glass.h);
    let organism = {
        let t = Instant::now();
        for _ in 0..n {
            let _ = cr.save();
            cr.rectangle(glass.x, glass.y, glass.w, glass.h);
            cr.clip();
            let Core { organisms, species_index, rcaches, .. } = &mut core;
            let org = organisms[*species_index].as_ref().unwrap();
            abyssal::render::draw_organism(&cr, &vp, org, rcaches);
            let _ = cr.restore();
        }
        t.elapsed().as_secs_f64() * 1000.0 / n as f64
    };

    // is the under layer fully opaque? (if so the black clear is redundant)
    let opaque = {
        let mut c = abyssal::skin::hidpi::copy_exclusive(&under.surface);
        let stride = c.stride() as usize;
        let (iw, ih) = (c.width() as usize, c.height() as usize);
        let d = c.data().unwrap();
        (0..ih).all(|y| (0..iw).all(|x| d[y * stride + x * 4 + 3] == 255))
    };

    println!("{w}x{h} logical, cache ds {cache_ds} -> target ds {target_ds} ({pw}x{ph} device), n={n}");
    println!("  compose_frame         {whole:7.3} ms      (simulation, separately: {sim:7.3} ms)");
    println!("    of which black clear{clear:7.3} ms");
    println!("    of which organism   {organism:7.3} ms");
    println!("  device layer cache    {:7.2} MB", dev.bytes() as f64 / 1048576.0);
    println!("  AVOIDED per frame by that cache - the resample GSK did on the GPU:");
    println!("    under               {blit_under:7.3} ms");
    println!("    over                {blit_over:7.3} ms");
    println!("  under layer fully opaque: {opaque}");

    // Does the `over` layer put anything inside the glass? If it does not,
    // the whole static picture could be pre-composed into ONE surface and the
    // organism drawn last; if it does, the organism must stay sandwiched.
    {
        let mut c = abyssal::skin::hidpi::copy_exclusive(&over.surface);
        let stride = c.stride() as usize;
        let (cw, ch) = (c.width() as usize, c.height() as usize);
        let ds = cache_ds;
        let (x0, y0) = ((glass.x * ds) as usize, (glass.y * ds) as usize);
        let (x1, y1) = (((glass.x + glass.w) * ds) as usize, ((glass.y + glass.h) * ds) as usize);
        let d = c.data().unwrap();
        let mut n_opaque = 0usize;
        let mut max_a = 0u8;
        let mut total = 0usize;
        for y in y0..y1.min(ch) {
            for x in x0..x1.min(cw) {
                let a = d[y * stride + x * 4 + 3];
                total += 1;
                if a > 0 { n_opaque += 1; }
                if a > max_a { max_a = a; }
            }
        }
        println!("  over inside glass: {n_opaque}/{total} px non-transparent, max alpha {max_a}");
    }
}

fn bench(n: usize, mut f: impl FnMut()) -> f64 {
    let t = Instant::now();
    for _ in 0..n { f(); }
    t.elapsed().as_secs_f64() * 1000.0 / n as f64
}
