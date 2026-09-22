//! The two display faces, installed where fontconfig can find them.
//! Port of ui/fonts.py.
//!
//! The console's hero type is set in Astro (major titles) and its technical
//! support type in Microgramma (labels, rails, microcopy). Both ship in
//! assets/fonts/; Pango resolves families through fontconfig, so the faces
//! have to exist in a scanned font directory before the first Pango font map
//! is built. Everything is idempotent and fails soft: if the copy or the
//! cache refresh is impossible the console still runs, on its mono fallback.

use std::cell::Cell;
use std::path::{Path, PathBuf};

const FONTS: [&str; 2] = ["astro.ttf", "microgrammanormal.ttf"];

/// Candidate locations of the repo's assets/fonts directory.
fn font_sources() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(manifest) = std::env::var("CARGO_MANIFEST_DIR") {
        // crate lives in <repo>/rust
        v.push(Path::new(&manifest).join("../assets/fonts"));
    }
    if let Ok(exe) = std::env::current_exe() {
        // <repo>/rust/target/release/abyssal -> <repo>
        for up in ["../../../assets/fonts", "../../../../assets/fonts"] {
            v.push(exe.parent().unwrap_or(Path::new(".")).join(up));
        }
    }
    v.push(PathBuf::from("assets/fonts"));
    v
}

fn digest_eq(src: &Path, dst: &Path) -> bool {
    match (std::fs::read(src), std::fs::read(dst)) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

/// Install the bundled faces into the user font directory, once per process.
pub fn ensure_user_fonts() {
    thread_local! {
        static DONE: Cell<bool> = const { Cell::new(false) };
    }
    if DONE.with(|d| d.get()) {
        return;
    }
    DONE.with(|d| d.set(true));

    let Some(home) = std::env::var_os("HOME") else { return };
    let dst_dir = PathBuf::from(home).join(".local/share/fonts/abyssal");
    let Ok(_) = std::fs::create_dir_all(&dst_dir) else { return };
    let mut changed = false;
    for src_dir in font_sources() {
        let mut any = false;
        for name in FONTS {
            let src = src_dir.join(name);
            if !src.is_file() {
                continue;
            }
            any = true;
            let dst = dst_dir.join(name);
            if !dst.is_file() || !digest_eq(&src, &dst) {
                if std::fs::copy(&src, &dst).is_ok() {
                    changed = true;
                }
            }
        }
        if any {
            break; // first source dir that actually ships the faces
        }
    }
    if changed {
        let _ = std::process::Command::new("fc-cache")
            .arg("-f")
            .arg(&dst_dir)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
}
