//! Parity gate for the organism + rasteriser port.
//!
//! The golden dumps are produced from the Python oracle by
//! `tools/parity_dump.py` (run from the repo root). This test replays the
//! EXACT same deterministic protocol and compares.

use abyssal::mathforms::{state_hue, SourceBody};
use abyssal::pointfield::{accumulate_physio, paint_physio, FieldCache};
use abyssal::render::build_lut;
use abyssal::signals::Physiology;
use cairo::{Context, ImageSurface};

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden");

// The protocol, byte-identical to tools/parity_dump.py.
const DT: f64 = 1.0 / 60.0;
const DUMP_FRAMES: [usize; 4] = [60, 180, 300, 419];
const SEED: u64 = 20260920;

fn rest() -> Physiology {
    Physiology::default()
}

fn loaded() -> Physiology {
    Physiology {
        agitation: 0.8,
        pulse: 0.9,
        density: 0.75,
        flux: 0.6,
        surge: 0.5,
        vitality: 1.0,
        activity: 0.7,
        stress: 0.6,
        excite: 0.8,
        tension: 0.3,
    }
}

fn phys_at(frame: usize) -> Physiology {
    if frame <= 120 {
        rest()
    } else if frame <= 240 {
        loaded()
    } else {
        rest()
    }
}

fn read_f32(name: &str) -> Vec<f32> {
    let bytes = std::fs::read(format!("{GOLDEN}/{name}"))
        .unwrap_or_else(|e| panic!("golden {name}: {e}"));
    bytes
        .chunks_exact(4)
        .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .collect()
}

/// Compare two slices; returns (max abs diff, first bad index).
fn cmp_f32(got: &[f32], want: &[f32], tol: f32, what: &str) -> (f32, Option<usize>) {
    assert_eq!(got.len(), want.len(), "{what}: length mismatch");
    let mut max_d = 0.0f32;
    let mut first_bad = None;
    for (i, (&g, &w)) in got.iter().zip(want.iter()).enumerate() {
        let d = (g - w).abs();
        if d > max_d {
            max_d = d;
        }
        if d > tol && first_bad.is_none() {
            first_bad = Some(i);
        }
    }
    if let Some(i) = first_bad {
        panic!(
            "{what}: max err {max_d:.3e} > tol {tol:.0e}; first bad at {i}: got {} want {}",
            got[i], want[i]
        );
    }
    (max_d, first_bad)
}

fn read_json_array_f64(name: &str) -> Vec<f64> {
    let text = std::fs::read_to_string(format!("{GOLDEN}/{name}")).unwrap();
    let inner = text.trim().trim_start_matches('[').trim_end_matches(']');
    inner.split(',').map(|v| v.trim().parse().unwrap()).collect()
}

/// Extract "lo": [a,b,c] / "hi": [...] from paint_lohi.json.
fn read_lohi(name: &str) -> ((f64, f64, f64), (f64, f64, f64)) {
    let text = std::fs::read_to_string(format!("{GOLDEN}/{name}")).unwrap();
    let get = |key: &str| {
        let pos = text.find(key).unwrap();
        let open = text[pos..].find('[').unwrap() + pos;
        let close = text[open..].find(']').unwrap() + open;
        let vals: Vec<f64> = text[open + 1..close]
            .split(',')
            .map(|v| v.trim().parse().unwrap())
            .collect();
        (vals[0], vals[1], vals[2])
    };
    (get("\"lo\""), get("\"hi\""))
}

#[test]
fn state_hue_matches_python() {
    let want = read_json_array_f64("hue.json");
    let acts = [0.0, 0.1, 0.25, 0.5, 0.64, 0.8, 0.95, 1.0];
    for (&a, &w) in acts.iter().zip(want.iter()) {
        let g = state_hue(a);
        assert!(
            (g - w).abs() <= 1e-12,
            "state_hue({a}) = {g} vs {w}"
        );
    }
}

#[test]
fn pulse_lut_matches_python() {
    let want = read_f32("lut.f32");
    assert_eq!(want.len(), 256 * 3);
    let got = build_lut();
    let mut max_d = 0.0f32;
    for i in 0..256 {
        for ch in 0..3 {
            max_d = max_d.max((got[i][ch] - want[i * 3 + ch]).abs());
        }
    }
    assert!(max_d <= 1e-6, "LUT max err {max_d:.3e}");
}

#[test]
fn anatomy_matches_python() {
    let mut worst = 0.0f32;
    for key in ["s01", "s02", "s03", "s04", "s05"] {
        let org = SourceBody::build(key, SEED);
        let (gu, glat, ggrp) = org.anatomy();
        let want_u = read_f32(&format!("{key}_u.f32"));
        let want_lat = read_f32(&format!("{key}_lat.f32"));
        let want_grp = read_f32(&format!("{key}_grp.f32"));
        let (d, _) = cmp_f32(gu, &want_u, 1e-6, &format!("{key} u"));
        worst = worst.max(d);
        let (d, _) = cmp_f32(glat, &want_lat, 1e-6, &format!("{key} lat"));
        worst = worst.max(d);
        let (d, _) = cmp_f32(ggrp, &want_grp, 1e-6, &format!("{key} grp"));
        worst = worst.max(d);
    }
    println!("anatomy max err: {worst:.3e}");
}

#[test]
fn frames_match_python() {
    // observed max errors, reported at the end
    let mut report: Vec<String> = Vec::new();
    for key in ["s01", "s02", "s03", "s04", "s05"] {
        let mut org = SourceBody::build(key, SEED);
        for frame in 1..=419 {
            org.update(DT, &phys_at(frame));
            if DUMP_FRAMES.contains(&frame) {
                let tag = format!("{key}_f{frame}");
                let (gx, gy, gw) = org.points();
                let (ge, gh) = org.excitation();
                let n = org.n_points();
                let want_x = &read_f32(&format!("{tag}_x.f32"))[..n];
                let want_y = &read_f32(&format!("{tag}_y.f32"))[..n];
                let want_w = &read_f32(&format!("{tag}_w.f32"))[..n];
                let want_e = &read_f32(&format!("{tag}_e.f32"))[..n];
                let want_h = &read_f32(&format!("{tag}_h.f32"))[..n];
                let (dx, _) = cmp_f32(gx, want_x, 1e-2, &format!("{tag} x"));
                let (dy, _) = cmp_f32(gy, want_y, 1e-2, &format!("{tag} y"));
                let (dw, _) = cmp_f32(gw, want_w, 1e-4, &format!("{tag} w"));
                let (de, _) = cmp_f32(ge, want_e, 1e-3, &format!("{tag} e"));
                let (dh, _) = cmp_f32(gh, want_h, 1e-3, &format!("{tag} h"));
                report.push(format!(
                    "{tag}: n={n} x={dx:.2e} y={dy:.2e} w={dw:.2e} e={de:.2e} h={dh:.2e}"
                ));
            }
        }
    }
    for line in &report {
        println!("{line}");
    }
}

#[test]
fn paint_matches_python() {
    let w = 200usize;
    let h = 150usize;
    let persist = 0.6235f64;
    let px = read_f32("paint_px.f32");
    let py = read_f32("paint_py.f32");
    let wt = read_f32("paint_w.f32");
    let ex = read_f32("paint_e.f32");
    let hu = read_f32("paint_h.f32");

    let mut cache = FieldCache::new(3);
    {
        let ent = cache.entry("golden", w, h);
        for _ in 0..3 {
            accumulate_physio(ent, &px, &py, &wt, &ex, &hu, persist);
        }
    }

    // accumulator parity
    let mut max_acc = 0.0f32;
    {
        let ent = cache.entry("golden", w, h);
        let (da, _) = cmp_f32(&ent.acc, &read_f32("paint_acc.f32"), 1e-4, "acc");
        let (de, _) = cmp_f32(&ent.acc_e, &read_f32("paint_acc_e.f32"), 1e-4, "acc_e");
        let (dh, _) = cmp_f32(&ent.acc_h, &read_f32("paint_acc_h.f32"), 1e-4, "acc_h");
        max_acc = max_acc.max(da).max(de).max(dh);
    }
    println!("accumulators max err: {max_acc:.3e}");

    let lut = build_lut();

    // paint parity: cool ramp, ink 0.376471
    let (n_same, n_diff, max_byte) = render_and_compare(
        &mut cache,
        w,
        h,
        abyssal::theme::CYAN_DEEP,
        abyssal::theme::CYAN,
        0.376471,
        &lut,
        "paint.bin",
    );
    println!("paint.bin: same={n_same} diff={n_diff} max_byte_delta={max_byte}");
    assert!(
        (n_diff as f64) as f32 / (n_diff + n_same) as f32 <= 0.005,
        "paint.bin: {} differing of {}",
        n_diff,
        n_diff + n_same
    );
    assert!(max_byte <= 2, "paint.bin: max byte delta {max_byte}");

    // warm variant
    let (lo, hi) = read_lohi("paint_lohi.json");
    let (n_same, n_diff, max_byte) = render_and_compare(
        &mut cache, w, h, lo, hi, 0.454902, &lut, "paint_warm.bin",
    );
    println!("paint_warm.bin: same={n_same} diff={n_diff} max_byte_delta={max_byte}");
    assert!(
        (n_diff as f64) as f32 / (n_diff + n_same) as f32 <= 0.005,
        "paint_warm.bin: {} differing of {}",
        n_diff,
        n_diff + n_same
    );
    assert!(max_byte <= 2, "paint_warm.bin: max byte delta {max_byte}");
}

/// Render paint_physio onto a fresh ARGB32 surface and byte-compare with the
/// golden dump. Returns (identical bytes, differing bytes, max |delta|).
fn render_and_compare(
    cache: &mut FieldCache,
    w: usize,
    h: usize,
    lo: (f64, f64, f64),
    hi: (f64, f64, f64),
    ink: f64,
    lut: &[[f32; 3]; 256],
    golden: &str,
) -> (usize, usize, u8) {
    let mut surf = ImageSurface::create(cairo::Format::ARgb32, w as i32, h as i32).unwrap();
    {
        let cr = Context::new(&surf).unwrap();
        let ent = cache.entry("golden", w, h);
        paint_physio(&cr, ent, 0.0, 0.0, lo, hi, lut, ink, 2.2, 1.0);

    } // the Context must be gone before the surface data can be mapped
    surf.flush();
    let data = surf.data().unwrap();
    let want = std::fs::read(format!("{GOLDEN}/{golden}")).unwrap();
    assert_eq!(data.len(), want.len(), "{}: size mismatch", golden);
    let mut same = 0usize;
    let mut diff = 0usize;
    let mut max_byte = 0u8;
    for (g, wv) in data.iter().zip(want.iter()) {
        let d = g.abs_diff(*wv);
        if d == 0 {
            same += 1;
        } else {
            diff += 1;
            max_byte = max_byte.max(d);
        }
    }
    (same, diff, max_byte)
}
