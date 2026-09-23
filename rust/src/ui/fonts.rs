//! Optional display faces, registered if the operator has supplied them.
//!
//! The instrument's hero type is Astro and its technical support type is
//! Microgramma. **Neither is distributed with this program.** They are
//! third-party faces and Somacline has no redistribution rights to them, so
//! `assets/fonts/` is empty in the repository and nothing font-shaped is ever
//! installed by the packaging.
//!
//! What this module does is narrow: if the operator has put those files into
//! `assets/fonts/` themselves, it copies them into their own font directory
//! and refreshes the cache, so Pango can resolve the families. Files the
//! operator supplied, moved on the operator's own machine - not redistribution.
//!
//! If the files are absent, this does nothing at all and the instrument runs
//! on fontconfig's substitutions. That is the normal case and it is supported:
//! the layout is metric-driven, so the machine composes correctly whatever the
//! faces resolve to. It just is not wearing its own typography.

use std::cell::Cell;
use std::path::{Path, PathBuf};

const FONTS: [&str; 2] = ["astro.ttf", "microgrammanormal.ttf"];

/// Candidate locations of the assets/fonts directory.
fn font_sources() -> Vec<PathBuf> {
    crate::skin::asset_roots("fonts")
}

fn digest_eq(src: &Path, dst: &Path) -> bool {
    match (std::fs::read(src), std::fs::read(dst)) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

/// Register the optional faces, once per process. A no-op when they are not
/// present, which is the default state of a fresh checkout.
pub fn ensure_user_fonts() {
    thread_local! {
        static DONE: Cell<bool> = const { Cell::new(false) };
    }
    if DONE.with(|d| d.get()) {
        return;
    }
    DONE.with(|d| d.set(true));

    let Some(home) = std::env::var_os("HOME") else { return };
    let dst_dir = PathBuf::from(home).join(".local/share/fonts/somacline");
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
