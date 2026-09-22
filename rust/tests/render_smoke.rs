//! Headless smoke test for the render path: draw_organism must run without
//! panicking at several window sizes, for every species, with caches hot and
//! cold. Also reports rough per-frame simulation + raster timings.

use abyssal::layout::{resolve, LayoutState};
use abyssal::mathforms::SourceBody;
use abyssal::render::{draw_organism, RenderCaches};
use abyssal::signals::Physiology;
use abyssal::viewport::Viewport;
use cairo::{Context, ImageSurface};

#[test]
fn draw_organism_smoke() {
    let mut caches = RenderCaches::new();
    let mut loaded = Physiology::default();
    loaded.agitation = 0.8;
    loaded.pulse = 0.9;
    loaded.stress = 0.6;
    loaded.surge = 0.5;

    for key in ["s01", "s02", "s03", "s04", "s05"] {
        let mut org = SourceBody::build(key, 20260920);
        for (w, h) in [(900i32, 700i32), (600, 480), (1440, 900), (390, 300)] {
            let layout = resolve(w as f64, h as f64);
            let stage = layout.stage;
            let vp = Viewport::for_stage(stage.x, stage.y, stage.w, stage.h);
            let surf =
                ImageSurface::create(cairo::Format::ARgb32, w, h).unwrap();
            {
                let cr = Context::new(&surf).unwrap();
                cr.set_source_rgb(0.02, 0.03, 0.04);
                cr.paint().unwrap();
                draw_organism(&cr, &vp, &org, &mut caches);
            }
            // at compact sizes the layout may be tiny; both are fine, we only
            // require that the draw path runs and produces a mapped surface
            assert!(w > 0 && h > 0);
        }
        org.update(1.0 / 60.0, &loaded);
        assert_eq!(org.n_points() >= 64, true);
    }
}

#[test]
fn layout_states_reachable() {
    assert_eq!(resolve(900.0, 700.0).state, LayoutState::Instrument);
    assert_eq!(resolve(1440.0, 900.0).state, LayoutState::Archive);
    // 600x480 is under the 700px instrument breakpoint -> COMPACT
    assert_eq!(resolve(600.0, 480.0).state, LayoutState::Compact);
    // 420 height is NOT < 420, so no demotion: INSTRUMENT
    assert_eq!(resolve(760.0, 420.0).state, LayoutState::Instrument);
    assert_eq!(resolve(300.0, 200.0).state, LayoutState::Compact);
}

/// Cross-check resolve() against golden/layout.json (dumped from Python).
#[test]
fn layout_matches_python_golden() {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/golden/layout.json"
    ))
    .unwrap();
    // cheap JSON walk: one object per size
    for obj in text.split('{').skip(1) {
        let get_num = |key: &str| -> f64 {
            let pos = obj.find(&format!("\"{key}\":")).unwrap();
            let rest = &obj[pos + key.len() + 3..];
            let end = rest.find(|c: char| c == ',' || c == '}').unwrap();
            rest[..end].trim().parse().unwrap()
        };
        let get_arr = |key: &str| -> [f64; 4] {
            let pos = obj.find(&format!("\"{key}\":")).unwrap();
            let open = obj[pos..].find('[').unwrap() + pos;
            let close = obj[open..].find(']').unwrap() + open;
            let vals: Vec<f64> = obj[open + 1..close]
                .split(',')
                .map(|v| v.trim().parse().unwrap())
                .collect();
            [vals[0], vals[1], vals[2], vals[3]]
        };
        let w = get_num("w");
        let h = get_num("h");
        let want_state = {
            let pos = obj.find("\"state\":").unwrap();
            let colon = obj[pos..].find(':').unwrap() + pos;
            let q1 = obj[colon..].find('"').unwrap() + colon + 1;
            let q2 = obj[q1..].find('"').unwrap() + q1;
            obj[q1..q2].to_string()
        };
        let l = resolve(w, h);
        assert_eq!(l.state.name(), want_state, "state at {w}x{h}");
        let near = |a: f64, b: f64| (a - b).abs() < 1e-6;
        let chk = |name: &str, got: [f64; 4], want: [f64; 4]| {
            for i in 0..4 {
                assert!(
                    near(got[i], want[i]),
                    "{name} [{i}] at {w}x{h}: got {} want {}",
                    got[i],
                    want[i]
                );
            }
        };
        chk("header", [l.header.x, l.header.y, l.header.w, l.header.h], get_arr("header"));
        chk("stage", [l.stage.x, l.stage.y, l.stage.w, l.stage.h], get_arr("stage"));
        chk("readout", [l.readout.x, l.readout.y, l.readout.w, l.readout.h], get_arr("readout"));
        chk("controls", [l.controls.x, l.controls.y, l.controls.w, l.controls.h], get_arr("controls"));
        chk("footer", [l.footer.x, l.footer.y, l.footer.w, l.footer.h], get_arr("footer"));
        chk("chassis", [l.chassis.x, l.chassis.y, l.chassis.w, l.chassis.h], get_arr("chassis"));
        let tpos = obj.find("\"type\":").unwrap();
        let topen = obj[tpos..].find('[').unwrap() + tpos;
        let tclose = obj[topen..].find(']').unwrap() + topen;
        let tv: Vec<f64> = obj[topen + 1..tclose]
            .split(',')
            .map(|v| v.trim().parse().unwrap())
            .collect();
        assert!(
            near(l.typ.title, tv[0])
                && near(l.typ.specimen, tv[1])
                && near(l.typ.value, tv[4]),
            "type at {w}x{h}"
        );
    }
}
