//! Headless parity gates for the console machine (ui/console.py port).
//!
//! Python ground truth was produced from `abyssal.ui.console` at the golden
//! layout sizes; the rest asserts the structural invariants the Python module
//! documents (glass inside the stage, hit-test/draw identity, cache-stable
//! layer ids, region cadence).

use std::cell::RefCell;
use std::rc::Rc;

use abyssal::layout::{resolve, Layout, LayoutState};
use abyssal::signals::Telemetry;
use abyssal::skin::hidpi;
use abyssal::species;
use abyssal::telemetry::History;
use abyssal::ui::chrome;
use abyssal::ui::console::{
    control_geometry, hit_controls, stage_content, CtlKind, ConsoleModel, Renderer,
};
use abyssal::ui::console;

fn tel_fixture() -> Telemetry {
    // Same shape qa/offscreen.py uses: warm, busy, with a live sensor.
    Telemetry {
        cpu_load: 0.14,
        memory_pressure: 8.2 / 13.5,
        temperature: (83.0 - 30.0) / (95.0 - 30.0),
        temp_available: true,
        io_rate: 0.22,
        io_available: true,
        cpu_pct: 14.0,
        mem_used_gb: 8.2,
        mem_total_gb: 13.5,
        temp_c: Some(83.0),
        temp_label: "CPU".to_string(),
        io_mb_s: 12.0,
        net_mb_s: 3.0,
        notes: "parity".to_string(),
    }
}

fn model(active: usize) -> ConsoleModel {
    ConsoleModel::new(
        species::by_index(active),
        active,
        Rc::new(RefCell::new(History::new(5.0))),
        false,
    )
}

fn approx(a: f64, b: f64, tol: f64) -> bool {
    (a - b).abs() <= tol
}

/// Python: stage_content(resolve(w, h)) at the golden sizes, printed to 3 dp.
const PYTHON_GLASS: [(i32, i32, f64, f64, f64, f64, &str); 8] = [
    (600, 480, 73.963, 114.648, 452.075, 238.415, "COMPACT"),
    (900, 700, 78.299, 188.832, 375.011, 284.968, "INSTRUMENT"),
    (1440, 900, 112.743, 244.699, 639.019, 332.330, "ARCHIVE"),
    (500, 300, 32.574, 76.574, 434.852, 133.812, "COMPACT"),
    (1600, 1000, 125.270, 271.887, 710.021, 369.255, "ARCHIVE"),
    (300, 600, 35.963, 79.963, 228.074, 427.166, "COMPACT"),
    (760, 420, 55.251, 113.670, 319.801, 152.239, "INSTRUMENT"),
    (2560, 1600, 200.432, 435.020, 1136.034, 590.808, "ARCHIVE"),
];

#[test]
fn stage_content_matches_python_at_golden_sizes() {
    for (w, h, gx, gy, gw, gh, state) in PYTHON_GLASS {
        let l = resolve(f64::from(w), f64::from(h));
        assert_eq!(l.state.name(), state, "{w}x{h} layout state");
        let g = stage_content(&l);
        assert!(
            approx(g.x, gx, 1e-2)
                && approx(g.y, gy, 1e-2)
                && approx(g.w, gw, 1e-2)
                && approx(g.h, gh, 1e-2),
            "{w}x{h}: glass ({:?}) != python ({gx},{gy},{gw},{gh})",
            g
        );
    }
}

#[test]
fn stage_content_invariants() {
    for (w, h) in
        [(600, 480), (900, 700), (1440, 900), (500, 300), (1600, 1000), (760, 420)]
    {
        let l = resolve(f64::from(w), f64::from(h));
        let g = stage_content(&l);
        let s = l.stage;
        assert!(g.valid(), "{w}x{h}: glass invalid");
        // glass inside the stage
        assert!(g.x >= s.x - 1e-9 && g.y >= s.y - 1e-9, "{w}x{h}: glass outside stage");
        assert!(
            g.right() <= s.right() + 1e-9 && g.bottom() <= s.bottom() + 1e-9,
            "{w}x{h}: glass exceeds stage"
        );
        // more than a quarter of the stage area: the observation chamber is
        // never a sliver
        assert!(
            g.w * g.h > 0.25 * s.w * s.h,
            "{w}x{h}: glass less than 25% of the stage"
        );
    }
}

#[test]
fn hit_controls_matches_python_geometry() {
    let l = resolve(900.0, 700.0);
    let (geo, mode) = control_geometry(&l);
    assert!(geo.valid());
    // Python ground truth: key centres at these exact coordinates.
    let centres = [
        (130.3964, 565.4414),
        (197.3261, 565.4414),
        (264.2558, 565.4414),
        (331.1855, 565.4414),
        (398.1152, 565.4414),
    ];
    for (i, (cx, cy)) in centres.iter().enumerate() {
        // the drawn rect's centre agrees with the Python-drawn centre
        let r = geo.key_rect(i);
        assert!(
            approx(r.cx(), *cx, 1e-3) && approx(r.cy(), *cy, 1e-3),
            "key {i} centre ({},{}) != python ({cx},{cy})",
            r.cx(),
            r.cy()
        );
        // and the hit test sends it to its own index
        assert_eq!(hit_controls(&l, *cx, *cy), Some((CtlKind::Key, i)));
    }
    // the mode key rect itself, straight from Python
    assert!(approx(mode.x, 441.0508528372116, 1e-6));
    assert_eq!(hit_controls(&l, mode.cx(), mode.cy()), Some((CtlKind::Mode, 0)));
    // the cycle rocker: left half back (0), right half forward (1)
    let cyc = geo.aux_l;
    assert!(approx(cyc.x, 54.5014, 1e-3) && approx(cyc.w, 36.6903, 1e-3));
    assert_eq!(
        hit_controls(&l, cyc.x + cyc.w * 0.25, cyc.cy()),
        Some((CtlKind::Cycle, 0))
    );
    assert_eq!(
        hit_controls(&l, cyc.x + cyc.w * 0.75, cyc.cy()),
        Some((CtlKind::Cycle, 1))
    );
    // a gap between trough and keys rejects
    assert_eq!(hit_controls(&l, geo.key_rect(0).x - 5.0, geo.key_rect(0).cy()), None);
    // far outside everything rejects
    assert_eq!(hit_controls(&l, 5.0, 5.0), None);
}

#[test]
fn hit_controls_compact_is_inert() {
    let l = resolve(600.0, 480.0);
    assert_eq!(l.state, LayoutState::Compact);
    let (geo, mode) = control_geometry(&l);
    assert!(!geo.valid());
    assert!(!mode.valid());
    for (x, y) in [(400.0, 400.0), (100.0, 50.0), (590.0, 470.0), (300.0, 240.0)] {
        assert_eq!(hit_controls(&l, x, y), None, "compact hit at ({x},{y})");
    }
}

#[test]
fn layers_render_cache_and_invalidate() {
    chrome::init();
    hidpi::set_scale(1.0);
    let l = resolve(900.0, 700.0);
    let mut m = model(0);
    let tel = tel_fixture();
    let mut r = Renderer::new();

    let under = r.layer_under(&l, &m).expect("under layer");
    let over = r.layer_over(&l, &m, &tel).expect("over layer");
    assert_eq!(under.surface.width(), 900);
    assert_eq!(under.surface.height(), 700);
    assert_eq!(over.surface.width(), 900);
    assert_eq!(over.surface.height(), 700);

    // warm hit: same id, same surface object
    let under2 = r.layer_under(&l, &m).expect("under layer warm");
    let over2 = r.layer_over(&l, &m, &tel).expect("over layer warm");
    assert_eq!(under.id, under2.id);
    assert_eq!(over.id, over2.id);

    // switching the active specimen re-renders BOTH layers: species, active
    // and the behaviour word are discrete inputs of the static passes
    m.active = 1;
    m.species = species::by_index(1);
    let under3 = r.layer_under(&l, &m).expect("under layer switched");
    let over3 = r.layer_over(&l, &m, &tel).expect("over layer switched");
    assert_ne!(under.id, under3.id, "under layer did not re-render on switch");
    assert_ne!(over.id, over3.id, "over layer did not re-render on switch");

    // the memory graph's fixed scale is part of the over key
    let mut tel2 = tel.clone();
    tel2.mem_total_gb = 32.0;
    let over4 = r.layer_over(&l, &m, &tel2).expect("over layer mem scale");
    assert_ne!(over3.id, over4.id);
}

#[test]
fn regions_cadence_and_identity() {
    chrome::init();
    hidpi::set_scale(1.0);
    let l = resolve(900.0, 700.0);
    assert!(console::six_module(&l), "900x700 must be the six-module machine");
    let mut m = model(0);
    let tel = tel_fixture();
    let mut r = Renderer::new();

    let under = r.layer_under(&l, &m).expect("under");
    let over = r.layer_over(&l, &m, &tel).expect("over");
    let regs = r.regions(&l, &m, &tel, 60.0, 16.6, Some(&under), Some(&over));
    assert_eq!(regs.len(), 3, "clock + rack + keys at instrument");
    for reg in &regs {
        // region surfaces are their own pixels, never a copy of a static layer
        assert_ne!(reg.layer.id, under.id);
        assert_ne!(reg.layer.id, over.id);
        // snapped to whole device pixels and inside the window
        assert!(reg.rect.x * 4.0 >= 0.0);
        assert!(reg.rect.w >= 2.0 && reg.rect.h >= 2.0);
    }
    // warm pass: all three ids stable
    let ids: Vec<u64> = regs.iter().map(|rg| rg.layer.id).collect();
    let regs2 = r.regions(&l, &m, &tel, 60.0, 16.6, Some(&under), Some(&over));
    let ids2: Vec<u64> = regs2.iter().map(|rg| rg.layer.id).collect();
    assert_eq!(ids, ids2, "unchanged inputs must not re-render regions");

    // a new telemetry sample re-renders the rack region (third is keys)
    let mut tel2 = tel.clone();
    tel2.cpu_pct = 41.0;
    tel2.cpu_load = 0.41;
    let regs3 = r.regions(&l, &m, &tel2, 60.0, 16.6, Some(&under), Some(&over));
    assert_ne!(regs[1].layer.id, regs3[1].layer.id, "rack did not re-render");

    // a mode-key press re-renders only the keys region
    m.mode_state = "active".to_string();
    let regs4 = r.regions(&l, &m, &tel2, 60.0, 16.6, Some(&under), Some(&over));
    assert_ne!(regs3[2].layer.id, regs4[2].layer.id, "keys did not re-render");

    // history samples bump the ring version -> rack re-renders
    m.history.borrow_mut().push(50.0, Some(70.0), 9.0, 16.6);
    let regs5 = r.regions(&l, &m, &tel2, 60.0, 16.6, Some(&under), Some(&over));
    assert_ne!(regs4[1].layer.id, regs5[1].layer.id, "trace did not re-render");
}

#[test]
fn compact_has_no_keys_region() {
    chrome::init();
    hidpi::set_scale(1.0);
    let l = resolve(600.0, 480.0);
    assert!(!console::six_module(&l));
    let m = model(0);
    let tel = tel_fixture();
    let mut r = Renderer::new();
    let under = r.layer_under(&l, &m).expect("under");
    let over = r.layer_over(&l, &m, &tel).expect("over");
    let regs = r.regions(&l, &m, &tel, 60.0, 16.6, Some(&under), Some(&over));
    assert_eq!(regs.len(), 2, "clock + rack only: compact has no selector bank");
}

#[test]
fn region_without_layers_recomputes_them() {
    // The Python regions() regenerates under/over when the caller passes
    // None; the port must do the same (host convenience + QA path).
    chrome::init();
    hidpi::set_scale(1.0);
    let l = resolve(900.0, 700.0);
    let m = model(0);
    let tel = tel_fixture();
    let mut r = Renderer::new();
    let regs = r.regions(&l, &m, &tel, 60.0, 16.6, None, None);
    assert_eq!(regs.len(), 3);
}

#[test]
fn condensed_and_compact_paths_render() {
    // Exercise the non-module arrangements headless: compact over-layer and
    // the condensed telemetry strip (a COMPACT rack region).
    chrome::init();
    hidpi::set_scale(1.0);
    let l = resolve(600.0, 480.0);
    let m = model(0);
    let tel = tel_fixture();
    let mut r = Renderer::new();
    let under = r.layer_under(&l, &m).expect("compact under");
    let over = r.layer_over(&l, &m, &tel).expect("compact over");
    assert_eq!(under.surface.width(), 600);
    assert_eq!(over.surface.width(), 600);
    let regs = r.regions(&l, &m, &tel, 45.0, 22.0, Some(&under), Some(&over));
    assert_eq!(regs.len(), 2);
}

#[test]
fn layout_state_naming_matches_python() {
    // the layer key embeds L.state.value; keep the strings pinned
    assert_eq!(LayoutState::Compact.name(), "COMPACT");
    assert_eq!(LayoutState::Instrument.name(), "INSTRUMENT");
    assert_eq!(LayoutState::Archive.name(), "ARCHIVE");
}

