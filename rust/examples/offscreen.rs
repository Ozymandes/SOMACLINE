//! Headless frame capture, the Rust twin of qa/offscreen.py.
//!
//! offscreen <out.png> [--width W] [--height H] [--specimen N] [--seconds S]

use std::cell::RefCell;
use std::rc::Rc;


fn surface_to_argb(surf: &cairo::ImageSurface) -> cairo::ImageSurface {
    // cairo-rs refuses an exclusive data lend while any other reference
    // (including a live Context) exists, so: blit into a fresh surface with
    // the context scoped out, then read.
    let mut s = surf.clone();
    s.flush();
    let (w, h, stride) = (s.width(), s.height(), s.stride());
    let mut copy = cairo::ImageSurface::create(cairo::Format::ARgb32, w, h).unwrap();
    {
        let cr = cairo::Context::new(&copy).unwrap();
        cr.set_source_surface(&s, 0.0, 0.0).unwrap();
        cr.paint().unwrap();
    }
    copy.flush();
    let data: Vec<u8> = {
        let d = copy.data().unwrap();
        d.to_vec()
    };
    cairo::ImageSurface::create_for_data(data, cairo::Format::ARgb32, w, h, stride).unwrap()
}

fn main() {
    std::env::remove_var("CARGO_MANIFEST_DIR");
    let mut args = std::env::args().skip(2);
    let out = std::env::args().nth(1).unwrap_or_else(|| "/tmp/off.png".into());
    let mut width = 1400;
    let mut height = 880;
    let mut specimen = 0usize;
    let mut seconds = 6.0f64;
    while let Some(a) = args.next() {
        let mut val = || args.next().and_then(|v| v.parse().ok()).unwrap_or(0.0);
        match a.as_str() {
            "--width" => width = val() as i32,
            "--height" => height = val() as i32,
            "--specimen" => specimen = val() as usize,
            "--seconds" => seconds = val(),
            _ => {}
        }
    }
    let (w, h) = (width.max(1), height.max(1));
    abyssal::ui::fonts::ensure_user_fonts();
    abyssal::skin::hidpi::set_scale(1.0);

    // Normalised channels derived exactly as telemetry.source derives them.
    let tel = abyssal::signals::Telemetry {
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
    let sp = abyssal::species::by_index(specimen);
    let mut org = abyssal::mathforms::SourceBody::build(
        sp.source,
        20260920 + specimen as u64,
    );
    let mut phys = abyssal::physiology::PhysiologyModel::new();
    let dt = 1.0 / 60.0;
    for _ in 0..(seconds / dt) as usize {
        org.update(dt, &phys.update(dt, &tel, 0.0));
    }

    let surface = cairo::ImageSurface::create(cairo::Format::ARgb32, w, h).unwrap();
    let cr = cairo::Context::new(&surface).unwrap();
    let l = abyssal::layout::resolve(w as f64, h as f64);
    let glass = abyssal::ui::console::stage_content(&l);
    let vp = abyssal::viewport::Viewport::for_stage(glass.x, glass.y, glass.w, glass.h);
    let history = Rc::new(RefCell::new(abyssal::telemetry::History::new(5.0)));
    let mut m = abyssal::ui::console::ConsoleModel::new(sp, specimen, history.clone(), false);
    m.mode_state = "armed".into();
    let p = *phys.current();
    m.phase = (org.time() * 0.31) % 2.0;
    m.rotation = 0.08 + 0.42 * p.agitation;
    let (px, py, pw) = org.points();
    let tot = pw.iter().sum::<f32>() as f64;
    let tot = if tot == 0.0 { 1.0 } else { tot };
    let fx = px.iter().zip(pw.iter()).map(|(x, w)| x * w).sum::<f32>() as f64;
    let fy = py.iter().zip(pw.iter()).map(|(y, w)| y * w).sum::<f32>() as f64;
    m.coords = (fx / tot / 400.0, fy / tot / 400.0, 0.0);
    m.magnification = (vp.scale * 10.0).max(0.1);
    m.field_mm = (vp.stage_w.min(vp.stage_h) / vp.scale / 400.0).max(0.01);
    // Prime a real 60 s history at the app's telemetry cadence.
    {
        let cap = history.borrow().cap;
        for i in 0..cap {
            let if_ = i as f64;
            let cpu = (tel.cpu_pct + 9.0 * (if_ * 0.05).sin() + 4.0 * (if_ * 0.61).sin())
                .clamp(0.0, 100.0);
            let tempc = tel.temp_c.unwrap_or(60.0) - 3.0 + 3.5 * (if_ * 0.037).sin()
                + 1.2 * (if_ * 0.4).sin();
            let mem = tel.mem_used_gb - 0.6 + 0.4 * (if_ * 0.013).sin()
                + 0.2 * (if_ / cap as f64);
            let frame = 1000.0 / 60.0 + 1.4 * (if_ * 0.29).sin()
                + if i % 97 < 3 { 6.0 } else { 0.0 };
            history.borrow_mut().push(cpu, Some(tempc), mem, frame);
        }
    }
    let mut light = abyssal::lighting::LightField::new();
    let mut renderer = abyssal::ui::console::Renderer::new();
    let under = renderer.layer_under(&l, &m);
    let over = renderer.layer_over(&l, &m, &tel);
    // Python draws console.draw_under / draw_over straight; the Rust cached
    // layers hold the same content - blit them at their natural logical size.
    for layer in under.iter().chain(over.iter()) {
        let src = surface_to_argb(&layer.surface);
        let lw = src.width();
        let sc = lw as f64 / w as f64;
        cr.set_source_surface(&src, 0.0, 0.0).unwrap();
        if (sc - 1.0).abs() > 1e-9 {
            cr.scale(1.0 / sc, 1.0 / sc);
        }
        cr.paint().unwrap();
        cr.identity_matrix();
    }
    if l.stage.valid() {
        let _ = cr.rectangle(glass.x, glass.y, glass.w, glass.h);
        let _ = cr.clip();
        let mut caches = abyssal::render::RenderCaches::new();
        abyssal::render::draw_organism(&cr, &vp, &org, &mut caches);
        cr.reset_clip();
    }
    let regions = renderer.regions(&l, &m, &tel, 60.0, 1000.0 / 60.0, under.as_ref(), over.as_ref());
    for reg in &regions {
        if !reg.rect.valid() {
            continue;
        }
        let src = surface_to_argb(&reg.layer.surface);
        let lw = src.width();
        let sc = lw as f64 / reg.rect.w.max(1.0);
        cr.set_source_surface(&src, reg.rect.x, reg.rect.y).unwrap();
        if (sc - 1.0).abs() > 1e-9 {
            cr.scale(1.0 / sc, 1.0 / sc);
        }
        cr.paint().unwrap();
        cr.identity_matrix();
    }
    surface.flush();
    let mut png = std::fs::File::create(&out).unwrap();
    surface.write_to_png(&mut png).unwrap();
    println!("{out}  {w}x{h} specimen {specimen}");
}
