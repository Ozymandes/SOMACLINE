//! Rough per-frame cost of the simulation + raster path (release mode).
//! Not a bench harness — a smoke-scale sanity number for the integrator.

use abyssal::mathforms::SourceBody;
use abyssal::render::{draw_organism, RenderCaches};
use abyssal::signals::Physiology;
use abyssal::viewport::Viewport;
use cairo::{Context, ImageSurface};
use std::time::Instant;

#[test]
fn frame_cost_sanity() {
    let mut caches = RenderCaches::new();
    let w = 900i32;
    let h = 700i32;
    let layout = abyssal::layout::resolve(w as f64, h as f64);
    let stage = layout.stage;
    let vp = Viewport::for_stage(stage.x, stage.y, stage.w, stage.h);
    let mut surf = ImageSurface::create(cairo::Format::ARgb32, w, h).unwrap();

    let p = Physiology::default();
    for key in ["s01", "s02", "s03", "s04", "s05"] {
        let mut org = SourceBody::build(key, 20260920);
        // warmup
        for i in 0..30 {
            org.update(1.0 / 60.0, &p);
            let cr = Context::new(&surf).unwrap();
            draw_organism(&cr, &vp, &org, &mut caches);
            drop(cr);
            surf.flush();
        }
        let n = org.n_points();
        let t0 = Instant::now();
        let frames = 300;
        for _ in 0..frames {
            org.update(1.0 / 60.0, &p);
        }
        let sim = t0.elapsed().as_secs_f64() / frames as f64 * 1000.0;
        let t0 = Instant::now();
        for _ in 0..frames {
            let cr = Context::new(&surf).unwrap();
            draw_organism(&cr, &vp, &org, &mut caches);
            drop(cr);
            surf.flush();
        }
        let raster = t0.elapsed().as_secs_f64() / frames as f64 * 1000.0;
        println!(
            "{key}: n={n} sim={sim:.3}ms raster(900x700)={raster:.3}ms"
        );
    }
}
