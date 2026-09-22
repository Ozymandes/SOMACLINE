// Probe: replicate the release-binary environment (no CARGO_MANIFEST_DIR,
// cwd = repo root) and ask the real skin loader what it sees.
fn main() {
    std::env::remove_var("CARGO_MANIFEST_DIR");
    abyssal::ui::fonts::ensure_user_fonts();
    for name in [
        "specimen/key_00_inactive",
        "specimen/key_00_active",
        "specimen/key_01_inactive",
        "cycle/neutral",
        "mode/armed",
        "module/selector",
    ] {
        let (w, h) = abyssal::skin::surface::sprite_size(name);
        println!("{name}: {w}x{h}");
    }
    let st = abyssal::skin::surface::skin_stats();
    println!("stats: {:?}", st);
}
