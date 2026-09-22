"""The two display faces, installed where fontconfig can find them.

WHAT THIS IS
------------
The console's hero type is set in Astro (major titles) and its technical
support type in Microgramma (labels, rails, microcopy). Both ship in
assets/fonts/; Pango resolves families through fontconfig, so the faces have
to exist in a scanned font directory before the first Pango font map is
built. `ensure_user_fonts()` copies them into ~/.local/share/fonts/abyssal/
(only when the bytes actually differ) and refreshes that directory's cache.

It is called once, from chrome.py, BEFORE the module creates its Pango
context - so every consumer (app, offscreen harness, QA gates) sees the same
two faces without each having to remember to ask. Everything is idempotent
and fails soft: if the copy or the cache refresh is impossible the console
still runs, on its mono fallback.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from pathlib import Path

#: repo assets -> installed names (kept, so an fc-list shows what they are).
_FONTS = ("astro.ttf", "microgrammanormal.ttf")

_DONE = False


def _digest(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_user_fonts() -> None:
    """Install the bundled faces into the user font directory, once."""
    global _DONE
    if _DONE:
        return
    _DONE = True
    try:
        src_dir = Path(__file__).resolve().parents[2] / "assets" / "fonts"
        dst_dir = Path.home() / ".local" / "share" / "fonts" / "abyssal"
        changed = False
        dst_dir.mkdir(parents=True, exist_ok=True)
        for name in _FONTS:
            src, dst = src_dir / name, dst_dir / name
            if not src.exists():
                continue
            if dst.exists() and _digest(dst) == _digest(src):
                continue
            shutil.copyfile(src, dst)
            changed = True
        if changed:
            subprocess.run(["fc-cache", "-f", str(dst_dir)], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30)
    except Exception:
        # Best effort only: the console falls back to its mono family.
        pass
