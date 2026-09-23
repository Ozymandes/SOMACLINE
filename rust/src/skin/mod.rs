// PORT-LANE: skin (raster hardware skin)
pub mod surface;
pub mod modules;
pub mod fascia;
pub mod catalog;
pub mod hidpi;

use std::path::PathBuf;

/// Every place `assets/<kind>` may live, best first.
///
/// Three layouts have to work and they are genuinely different shapes:
///
/// - **a checkout**, where the binary sits in `rust/target/release/` and the
///   assets are two or three levels up;
/// - **an install**, where the binary is in `<prefix>/bin/` (or
///   `<prefix>/lib/abyssal/`) and the assets are in
///   `<prefix>/share/abyssal/assets/`;
/// - **anything else**, which is what `ABYSSAL_ASSETS` is for - it names the
///   assets directory outright and is tried before everything.
///
/// Resolution is by probe, not by guess: the caller keeps the first candidate
/// that actually holds what it is looking for, and a missing asset is never
/// fatal - the console still runs, on its fallbacks.
pub fn asset_roots(kind: &str) -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Some(dir) = std::env::var_os("ABYSSAL_ASSETS") {
        v.push(PathBuf::from(dir).join(kind));
    }
    if let Ok(manifest) = std::env::var("CARGO_MANIFEST_DIR") {
        // the crate lives in <repo>/rust
        v.push(PathBuf::from(manifest).join("../assets").join(kind));
    }
    if let Ok(exe) = std::env::current_exe() {
        let dir = exe.parent().map(PathBuf::from).unwrap_or_default();
        for up in [
            "../share/abyssal/assets",      // <prefix>/bin        -> <prefix>/share
            "../../share/abyssal/assets",   // <prefix>/lib/abyssal -> <prefix>/share
            "../../../assets",              // <repo>/rust/target/release
            "../../../../assets",
        ] {
            v.push(dir.join(up).join(kind));
        }
    }
    v.push(PathBuf::from("/usr/share/abyssal/assets").join(kind));
    v.push(PathBuf::from("assets").join(kind));
    v
}
