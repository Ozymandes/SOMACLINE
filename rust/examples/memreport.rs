//! Where the resident bytes are. One line per owner, at a real geometry.
//!
//!     memreport [--width W] [--height H] [--cache-ds C] [--target-ds T]
//!               [--all-specimens] [--resizes]
//!
//! `--all-specimens` cycles every specimen, `--resizes` walks the three
//! layouts, so the report shows what a session ACCUMULATES rather than what a
//! single steady window holds.

use abyssal::host::{Core, DeviceLayers, Options, Target, SETTLE_S};

extern "C" {
    fn malloc_trim(pad: usize) -> i32;
}

fn mb(b: usize) -> f64 {
    b as f64 / 1048576.0
}

fn rollup() -> (f64, f64, f64) {
    let mut rss = 0.0;
    let mut pss = 0.0;
    let mut anon = 0.0;
    if let Ok(s) = std::fs::read_to_string("/proc/self/smaps_rollup") {
        for line in s.lines() {
            let (k, v) = line.split_once(':').unwrap_or(("", ""));
            let n = v.split_whitespace().next().and_then(|x| x.parse::<f64>().ok());
            match (k, n) {
                ("Rss", Some(n)) => rss = n / 1024.0,
                ("Pss", Some(n)) => pss = n / 1024.0,
                ("Anonymous", Some(n)) => anon = n / 1024.0,
                _ => {}
            }
        }
    }
    (rss, pss, anon)
}

fn main() {
    std::env::remove_var("CARGO_MANIFEST_DIR");
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let num = |k: &str, d: f64| -> f64 {
        argv.iter()
            .position(|a| a == k)
            .and_then(|i| argv.get(i + 1))
            .and_then(|v| v.parse().ok())
            .unwrap_or(d)
    };
    let has = |k: &str| argv.iter().any(|a| a == k);
    let (w, h) = (num("--width", 781.0), num("--height", 468.0));
    let (cache_ds, target_ds) = (num("--cache-ds", 1.5), num("--target-ds", 1.6));

    abyssal::ui::fonts::ensure_user_fonts();
    let mut core = Core::new(Options { width: w as i32, height: h as i32, ..Options::default() });
    core.telemetry = abyssal::signals::Telemetry {
        cpu_load: 0.14, memory_pressure: 0.6, temperature: 0.8, io_rate: 0.22,
        temp_available: true, io_available: true, cpu_pct: 14.0,
        mem_used_gb: 8.2, mem_total_gb: 13.5, temp_c: Some(83.0),
        temp_label: "CPU".into(), ..Default::default()
    };
    let mut dev = DeviceLayers::new();

    let run = |core: &mut Core, dev: &mut DeviceLayers, w: f64, h: f64, n: usize| {
        abyssal::skin::hidpi::set_scale(cache_ds);
        let (pw, ph) = ((w * target_ds).round() as i32, (h * target_ds).round() as i32);
        let surface = cairo::ImageSurface::create(cairo::Format::ARgb32, pw, ph).unwrap();
        surface.set_device_scale(pw as f64 / w, ph as f64 / h);
        let cr = cairo::Context::new(&surface).unwrap();
        let t = Target {
            logical_w: w, logical_h: h, phys_w: pw, phys_h: ph,
            ds_x: pw as f64 / w, ds_y: ph as f64 / h, scale: target_ds,
        };
        for _ in 0..n {
            core.advance(1.0 / 60.0);
            core.compose_damaged(&cr, t, dev, 2);
        }
    };

    run(&mut core, &mut dev, w, h, num("--frames", 90.0) as usize);
    if has("--settle") {
        // two residency reviews, exactly as the host runs them
        core.settle(1000.0);
        core.settle(1000.0 + SETTLE_S);
    }
    if has("--settle") {
        // two reviews, as the host would run them
        core.settle(1000.0);
        core.settle(1000.0 + SETTLE_S);
    }
    if has("--resizes") {
        for (rw, rh) in [(600.0, 520.0), (1400.0, 880.0), (1000.0, 420.0)] {
            run(&mut core, &mut dev, rw, rh, 20);
        }
        run(&mut core, &mut dev, w, h, 20);
    }
    if has("--all-specimens") {
        for i in 0..abyssal::species::COUNT {
            core.select_specimen(i, false);
            run(&mut core, &mut dev, w, h, 20);
        }
        core.select_specimen(0, false);
        run(&mut core, &mut dev, w, h, 20);
    }

    println!("{w}x{h} logical, cache ds {cache_ds} -> target ds {target_ds}\
              {}{}",
                      if has("--resizes") { ", after three other layouts" } else { "" },
             if has("--all-specimens") { ", after every specimen" } else { "" });
    println!();
    println!("  OWNER                          COUNT        MB   lifetime");
    let (lu, lo, lr) = core.renderer.layer_inventory();
    println!("  console under layers        {:>7}  {:>8.2}   until size/state/specimen change", lu.0, mb(lu.1));
    println!("  console over layers         {:>7}  {:>8.2}   until size/state/specimen change", lo.0, mb(lo.1));
    println!("  console region surfaces     {:>7}  {:>8.2}   until their own inputs change", lr.0, mb(lr.1));
    let (tn, tb) = abyssal::ui::console::text_cache_inventory();
    println!("  glyph run surfaces          {:>7}  {:>8.2}   process (bounded LRU)", tn, mb(tb));
    let (rn, rb) = abyssal::ui::console::ring_cache_inventory();
    println!("  lamp/ring surfaces          {:>7}  {:>8.2}   process (bounded LRU)", rn, mb(rb));
    let (gn, gb) = abyssal::ui::console::trace_cache_inventory();
    println!("  graph trace surfaces        {:>7}  {:>8.2}   until the trace changes", gn, mb(gb));
    println!("  device frame cache          {:>7}  {:>8.2}   until layer/size change",
             if dev.bytes() > 0 { 3 } else { 0 }, mb(dev.bytes()));
    let (fn_, fb) = core.rcaches.fields.inventory();
    println!("  point-field accumulators    {:>7}  {:>8.2}   until evicted (LRU)", fn_, mb(fb));
    for (k, fw, fh) in core.rcaches.fields.keys() {
        println!("      {k:<22} {fw:>4} x {fh:<4}  {:>8.2}", mb(fw * fh * 4 * 3));
    }
    let ((bn, bb), (sn, sb)) = abyssal::skin::surface::cache_inventory();
    println!("  sprite sources, full res    {:>7}  {:>8.2}   reviewed every SETTLE_S (second chance)", bn, mb(bb));
    for (k, sw, sh, b) in abyssal::skin::surface::base_cache_entries().iter().take(8) {
        println!("      {k:<22} {sw:>4} x {sh:<4}  {:>8.2}", mb(*b));
    }
    println!("  sprite derived sizes (LRU)  {:>7}  {:>8.2}   bounded at 64 entries", sn, mb(sb));
    let (wn, wb) = core.rcaches.wash_inventory();
    println!("  deep-field wash discs       {:>7}  {:>8.2}   until evicted (FIFO)", wn, mb(wb));
    let mut orgs = 0usize;
    let mut orgb = 0usize;
    for o in core.organisms.iter().flatten() {
        orgs += 1;
        orgb += o.bytes();
    }
    println!("  organism point arrays       {:>7}  {:>8.2}   process (never freed)", orgs, mb(orgb));

    if has("--release-bench") {
        let freed = abyssal::skin::surface::release_sources();
        println!();
        println!("  release_sources freed {:.2} MB", mb(freed));
        // what a MISS costs: force every source back by composing a frame at
        // a size nothing is cached for
        let t = std::time::Instant::now();
        run(&mut core, &mut dev, w + 2.0, h + 2.0, 1);
        println!("  first frame after release, at a NEW size: {:.1} ms",
                 t.elapsed().as_secs_f64() * 1000.0);
        let t = std::time::Instant::now();
        run(&mut core, &mut dev, w + 2.0, h + 2.0, 30);
        println!("  next 30 frames:                           {:.3} ms each",
                 t.elapsed().as_secs_f64() * 1000.0 / 30.0);
        let ((bn2, bb2), _) = abyssal::skin::surface::cache_inventory();
        println!("  sources reloaded: {bn2} ({:.2} MB)", mb(bb2));
        // the same size step with the sources ALREADY warm, for comparison
        let t = std::time::Instant::now();
        run(&mut core, &mut dev, w + 4.0, h + 4.0, 1);
        println!("  a WARM resize frame (sources present):    {:.1} ms",
                 t.elapsed().as_secs_f64() * 1000.0);
        // pure decode cost, sprite by sprite
        abyssal::skin::surface::release_sources();
        for n in ["module/shell", "module/observation", "module/rack", "frame/chassis"] {
            let t = std::time::Instant::now();
            let got = abyssal::skin::surface::sprite_dyn(n).is_some();
            println!("      decode {n:<22} {:.1} ms  (found {got})",
                     t.elapsed().as_secs_f64() * 1000.0);
        }
    }

    if has("--release") {
        let freed = abyssal::skin::surface::release_sources();
        println!();
        println!("  release_sources freed {:.2} MB", mb(freed));
    }
    if has("--trim") {
        let before = rollup();
        let r = unsafe { malloc_trim(0) };
        let after = rollup();
        println!();
        println!("  malloc_trim(0) -> {r}: RSS {:.1} -> {:.1} MB, anon {:.1} -> {:.1} MB",
                 before.0, after.0, before.2, after.2);
    }
    let (rss, pss, anon) = rollup();
    println!();
    println!("  process: RSS {rss:.1} MB   PSS {pss:.1} MB   anonymous {anon:.1} MB");

    // Where the anonymous bytes actually sit: the heap arena against the
    // large individual mmaps, so "accounted cache" can be checked against
    // "resident anonymous" instead of assumed equal to it.
    if let Ok(maps) = std::fs::read_to_string("/proc/self/smaps") {
        let mut heap = 0.0f64;
        let mut big: Vec<(f64, String)> = Vec::new();
        let mut cur = String::new();
        let mut is_heap = false;
        let mut is_anon = false;
        for line in maps.lines() {
            if line.contains('-') && !line.starts_with(' ') && !line.contains(":  ") {
                let f: Vec<&str> = line.split_whitespace().collect();
                cur = f.get(5).unwrap_or(&"[anon]").to_string();
                is_heap = cur == "[heap]";
                is_anon = f.len() <= 5 || cur == "[heap]";
            } else if let Some(v) = line.strip_prefix("Rss:") {
                let kb: f64 = v.split_whitespace().next().unwrap_or("0").parse().unwrap_or(0.0);
                if is_heap {
                    heap += kb / 1024.0;
                } else if is_anon && kb > 512.0 {
                    big.push((kb / 1024.0, cur.clone()));
                }
            }
        }
        big.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
        println!("  heap arena (brk): {heap:.1} MB");
        let shown: f64 = big.iter().map(|(v, _)| v).sum();
        println!("  large anon mmaps (>0.5 MB): {:.1} MB in {} mappings: {}",
                 shown, big.len(),
                 big.iter().take(10).map(|(v, _)| format!("{v:.1}"))
                    .collect::<Vec<_>>().join(", "));
    }
}
