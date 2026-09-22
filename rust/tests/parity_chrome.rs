//! Chrome-layer parity gates: geometry and math ported from the Python
//! chrome/segment/selector/skin modules, checked against golden values
//! produced by the Python implementation on this same checkout.

use abyssal::layout::Rect;
use abyssal::skin::catalog as catalog;
use abyssal::skin::hidpi;
use abyssal::skin::surface::{self, NineSlice};
use abyssal::ui::segment;
use abyssal::ui::selector;

#[test]
fn hidpi_roundtrip_and_surface() {
    hidpi::set_scale(1.0);
    assert_eq!(hidpi::scale(), 1.0);
    // quarter-step quantisation, clamped to 1..=4 (Python: round(s*4)/4)
    hidpi::set_scale(1.6);
    assert_eq!(hidpi::scale(), 1.5);
    hidpi::set_scale(9.9);
    assert_eq!(hidpi::scale(), 4.0);
    hidpi::set_scale(0.2);
    assert_eq!(hidpi::scale(), 1.0);

    hidpi::set_scale(2.0);
    let s = hidpi::surface(100.0, 50.0);
    assert_eq!((s.width(), s.height()), (200, 100));
    assert_eq!(s.device_scale(), (2.0, 2.0));
    hidpi::set_scale(1.0);
    let s1 = hidpi::surface(100.0, 50.0);
    assert_eq!((s1.width(), s1.height()), (100, 50));
}

#[test]
fn sprite_loads_real_asset_and_missing_returns_none() {
    // golden: python sprite_size("frame/observation_bezel") == (1280, 900)
    let (w, h) = surface::sprite_size("frame/observation_bezel");
    assert_eq!((w, h), (1280, 900));
    assert!(surface::sprite("frame/observation_bezel").is_some());
    assert!(surface::sprite_dyn("lamp/primary_off").is_some());
    assert_eq!(surface::sprite_size("nope/does_not_exist"), (0, 0));
    assert!(surface::sprite_dyn("nope/does_not_exist").is_none());
    let st = surface::skin_stats();
    assert!(st.missing >= 1);
}

/// Python golden: catalog.OBSERVATION_BEZEL.content(10,20,500,400)
const OBS_CONTENT: (f64, f64, f64, f64) = (53.2, 58.25, 411.8, 319.9);
/// Python golden: catalog.GRAPH_WELL.content(0,0,300,200)
const WELL_CONTENT: (f64, f64, f64, f64) = (22.95, 13.5, 253.65, 166.7);
/// Python golden: catalog.PLATE.content(5,5,100,26)
const PLATE_CONTENT: (f64, f64, f64, f64) =
    (16.85945945945946, 13.884, 77.07567567567568, 8.362666666666666);

fn close4(a: (f64, f64, f64, f64), b: (f64, f64, f64, f64)) -> bool {
    a.0 - b.0 < 1e-9
        && b.0 - a.0 < 1e-9
        && a.1 - b.1 < 1e-9
        && b.1 - a.1 < 1e-9
        && a.2 - b.2 < 1e-9
        && b.2 - a.2 < 1e-9
        && a.3 - b.3 < 1e-9
        && b.3 - a.3 < 1e-9
}

#[test]
fn nine_slice_content_matches_python() {
    assert!(close4(
        catalog::OBSERVATION_BEZEL.content(10.0, 20.0, 500.0, 400.0),
        OBS_CONTENT
    ));
    assert!(close4(
        catalog::GRAPH_WELL.content(0.0, 0.0, 300.0, 200.0),
        WELL_CONTENT
    ));
    assert!(close4(
        catalog::PLATE.content(5.0, 5.0, 100.0, 26.0),
        PLATE_CONTENT
    ));
}

#[test]
fn nine_slice_min_size_and_synthetic_inset() {
    // min_size: left+right+1, top+bottom+1
    let ns = NineSlice::new("frame/plate_recessed", 24, 18, 24, 18, 32.0, 30.0, 26.0, 28.0);
    assert_eq!(ns.min_size(), (49, 37));
    // A synthetic panel whose sprite is missing must still map pads sensibly:
    // unknown sprite -> (0,0) source size -> shrink path with unit factors.
    let ghost = NineSlice::new("nope/ghost", 10, 8, 10, 8, 12.0, 9.0, 12.0, 9.0);
    let (x, y, w, h) = ghost.content(0.0, 0.0, 400.0, 300.0);
    assert!((w - 400.0).abs() < 1e-6 || w < 400.0);
    assert!(x >= 0.0 && y >= 0.0 && h >= 0.0);
}

#[test]
fn segment_measure_matches_python_and_is_stable() {
    // Python golden: measure("12.4", 20.0, CYAN) == 51.8
    let a = segment::measure("12.4", 20.0, &segment::CYAN);
    assert!((a - 51.8).abs() < 1e-9, "got {a}");
    let b = segment::measure("12.4", 20.0, &segment::CYAN);
    assert_eq!(a, b);
    assert!(a > 0.0);
    // fit_height: largest height where the text fits the box
    let h = segment::fit_height("12.4", 51.8, 100.0, &segment::CYAN);
    assert!((h - 20.0).abs() < 1e-9);
}

/// Python golden for layout(Rect(0,0,770,152), 5):
/// key_x 84.7, key_y 13.476..., key_w 105.739..., key_h 110.282...,
/// pitch 123.715..., ledge 18.702..., valid
#[test]
fn selector_layout_matches_python_golden() {
    let g = selector::layout(Rect::new(0.0, 0.0, 770.0, 152.0), 5);
    assert!(g.valid());
    assert_eq!(g.n, 5);
    assert!((g.key_x - 84.7).abs() < 1e-9, "key_x {}", g.key_x);
    assert!((g.key_y - 13.47639762816207).abs() < 1e-9, "key_y {}", g.key_y);
    assert!((g.key_w - 105.73943661971829).abs() < 1e-9, "key_w {}", g.key_w);
    assert!((g.key_h - 110.28292803697181).abs() < 1e-9, "key_h {}", g.key_h);
    assert!((g.pitch - 123.71514084507041).abs() < 1e-9, "pitch {}", g.pitch);
    assert!((g.ledge - 18.702952708024455).abs() < 1e-9, "ledge {}", g.ledge);
}

#[test]
fn selector_hit_roundtrips_key_centers_and_rejects_gaps() {
    let g = selector::layout(Rect::new(0.0, 0.0, 770.0, 152.0), 5);
    for i in 0..g.n {
        let k = g.key_rect(i);
        assert_eq!(selector::hit(&g, k.cx(), k.cy()), Some(i), "key {i}");
        // a point in the gap right of key i must not hit anything
        assert_eq!(selector::hit(&g, k.x + g.key_w + 2.0, k.cy()), None);
    }
    // outside the key band vertically: no hit
    assert_eq!(selector::hit(&g, g.keys_rect().cx(), g.key_y - 1.0), None);
    assert_eq!(selector::hit(&g, -5.0, g.key_y + 2.0), None);
}

#[test]
fn selector_natural_aspect_matches_python() {
    // Python golden: natural_aspect(5) == 5.312984615384615
    let a = selector::natural_aspect(5);
    assert!((a - 5.312984615384615).abs() < 1e-9, "got {a}");
}

#[test]
fn selector_key_states_and_plates() {
    // disabled > pressed > latched > focus > idle
    assert_eq!(selector::key_state(0, 0, None, None, &[0]), "disabled");
    assert_eq!(selector::key_state(1, 2, Some(1), None, &[]), "pressed");
    assert_eq!(selector::key_state(2, 2, None, None, &[]), "latched");
    assert_eq!(selector::key_state(3, 0, None, Some(3), &[]), "focus");
    assert_eq!(selector::key_state(4, 0, None, None, &[]), "idle");
    assert_eq!(selector::plate("pressed"), "active");
    assert_eq!(selector::plate("latched"), "active");
    assert_eq!(selector::plate("idle"), "inactive");
}

#[test]
fn catalog_sprite_names_match_python() {
    assert_eq!(catalog::lamp("small", "warning"), "lamp/small_warning");
    assert_eq!(catalog::lamp("primary", "bogus"), "lamp/primary_off");
    assert_eq!(catalog::specimen_key(0, "inactive"), "specimen/key_01_inactive");
    assert_eq!(catalog::specimen_key(7, "active"), "specimen/key_03_active");
    assert_eq!(catalog::mode_key("error"), "mode/error");
    assert_eq!(catalog::cycle_key("weird"), "cycle/neutral");
    assert_eq!(catalog::selector_cell("latched"), "selector/cell_latched");
}

#[test]
fn modules_bays_present_and_selector_pitch_trued() {
    use abyssal::skin::modules as modules;
    let bays = modules::SELECTOR.bays();
    assert_eq!(bays.len(), 5 + 5 + 5 + 5);
    let k0 = modules::SELECTOR.bay_src_pub("key_0").unwrap();
    let k1 = modules::SELECTOR.bay_src_pub("key_1").unwrap();
    assert!((k1[0] - k0[0] - modules::SEL_PITCH).abs() < 1e-6);
    assert!(modules::RACK.bay_src_pub("r0_graph").is_some());
    assert!(modules::RACK.bay_src_pub("r3_numeric").is_some());
    assert_eq!(modules::ALL.len(), 6);
    for m in modules::ALL {
        assert!(m.aspect() > 0.0);
    }
}

#[test]
fn fascia_bays_place_into_rects() {
    use abyssal::skin::fascia as fascia;
    let r = Rect::new(100.0, 50.0, 800.0, 120.0);
    let p = fascia::HEADER.place(r);
    let title = p.bay("title");
    assert!(title.valid());
    // bays stay inside the panel
    assert!(title.x >= r.x - 1.0 && title.right() <= r.right() + 1.0);
    assert!(title.y >= r.y - 1.0 && title.bottom() <= r.bottom() + 1.0);
    // a missing bay is an invalid rect
    assert!(!p.bay("nope").valid());
    // rail order is preserved left-to-right
    let r0 = p.bay("rail_0");
    let r1 = p.bay("rail_1");
    let r2 = p.bay("rail_2");
    assert!(r0.right() <= r1.x && r1.right() <= r2.x);
}
