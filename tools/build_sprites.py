#!/usr/bin/env python3
"""Slice the 2K hardware masters into runtime sprites.

WHY THIS IS A BUILD STEP, NOT RUNTIME WORK
------------------------------------------
The masters in assets/hardware_v2 are 2688px wide and total ~130MB. Loading and
slicing those on every launch would cost seconds and hundreds of MB. This tool
runs once, emits small deterministic PNGs into assets/sprites/, and the app
loads only those.

It also fixes the one thing the image model would not hold steady: sprites that
are meant to be swapped in place must share an EXACT footprint. The generator
drifted a few percent on lamp diameters, so `_uniform_cells` re-centres every
state of a family onto one canonical canvas. Alignment becomes a property of
the pipeline rather than a hope about the prompt.

Run:  python3 tools/build_sprites.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "hardware_v2"
OUT = ROOT / "assets" / "sprites"

ALPHA_FLOOR = 24          # below this an alpha pixel counts as empty


# ---------------------------------------------------------------- utilities
def _load(rel: str) -> Image.Image:
    p = SRC / rel
    if not p.exists():
        raise SystemExit(f"missing master: {p}")
    return Image.open(p).convert("RGBA")


def _alpha(im: Image.Image) -> np.ndarray:
    return np.array(im.getchannel("A"))


def _bbox(a: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(a > ALPHA_FLOOR)
    if len(xs) == 0:
        raise ValueError("fully transparent image")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _col_runs(a: np.ndarray, min_w: int) -> list[tuple[int, int]]:
    """Horizontal extents of occupied column bands, for sprite-sheet splitting."""
    occupied = (a > ALPHA_FLOOR).any(axis=0)
    runs, start = [], None
    for i, v in enumerate(occupied):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_w:
                runs.append((start, i))
            start = None
    if start is not None and len(occupied) - start >= min_w:
        runs.append((start, len(occupied)))
    return runs


def _row_runs(a: np.ndarray, min_h: int) -> list[tuple[int, int]]:
    occupied = (a > ALPHA_FLOOR).any(axis=1)
    runs, start = [], None
    for i, v in enumerate(occupied):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_h:
                runs.append((start, i))
            start = None
    if start is not None and len(occupied) - start >= min_h:
        runs.append((start, len(occupied)))
    return runs


def _save(im: Image.Image, rel: str, target_w: int | None = None) -> None:
    if target_w and im.width != target_w:
        h = max(1, round(im.height * target_w / im.width))
        im = im.resize((target_w, h), Image.LANCZOS)
    dst = OUT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, optimize=True)
    print(f"  {rel:<44} {im.width:>4}x{im.height:<4}")


def _uniform_cells(cells: list[Image.Image], target_w: int) -> list[Image.Image]:
    """Re-centre a family of state sprites onto one shared canvas.

    Each cell is cropped to its own ink, then all are scaled by a SINGLE common
    factor (so relative size differences that are real - a pressed cap sitting
    lower - survive) and pasted centred into an identical canvas. Swapping any
    two sprites is then a pure pixel substitution.
    """
    tight = []
    for c in cells:
        x0, y0, x1, y1 = _bbox(_alpha(c))
        tight.append(c.crop((x0, y0, x1, y1)))
    max_w = max(t.width for t in tight)
    max_h = max(t.height for t in tight)
    k = target_w / max_w
    cw, ch = target_w, max(1, round(max_h * k))
    out = []
    for t in tight:
        w = max(1, round(t.width * k))
        h = max(1, round(t.height * k))
        r = t.resize((w, h), Image.LANCZOS)
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        canvas.paste(r, ((cw - w) // 2, (ch - h) // 2), r)
        out.append(canvas)
    return out


# ------------------------------------------------------------------ builders
def build_frames() -> None:
    """Panels used via 9-slice. Kept whole, just trimmed and downsampled."""
    print("frames:")
    spec = [
        ("bezels/observation_bezel_2k.png",              "frame/observation_bezel.png", 1280),
        ("displays/segment_display_housing_2k.png",      "frame/segment_housing.png",    896),
        ("displays/segment_display_housing_wide_2k.png", "frame/segment_housing_wide.png", 1152),
        ("displays/graph_well_2k.png",                   "frame/graph_well.png",         768),
        ("meters/bargraph_trough_2k.png",                "frame/meter_trough.png",       896),
        ("bezels/aux_view_frame_2k.png",                 "frame/aux_frame.png",          384),
        ("bezels/header_fascia_2k.png",                  "frame/header_fascia.png",     1600),
        ("bezels/footer_archive_rail_2k.png",            "frame/footer_rail.png",       1600),
        ("racks/telemetry_rack_2k.png",                  "frame/telemetry_rack.png",     896),
        ("modules/telemetry_module_shell_2k.png",        "frame/module_shell.png",      1152),
        ("chassis/chassis_master_2k.png",                "frame/chassis.png",           1600),
        ("plaques/blank_plaque_kit_2k.png",              "frame/plaque_kit.png",         768),
    ]
    for src, dst, w in spec:
        im = _load(src)
        x0, y0, x1, y1 = _bbox(_alpha(im))
        _save(im.crop((x0, y0, x1, y1)), dst, w)


def build_plates() -> None:
    """Split the plaque kit into generic stretchable metal plates.

    The composite panels (header, footer, rack, module, chassis) are not
    9-sliceable - they are arrangements, not frames. They are therefore
    ASSEMBLED at runtime, and assembling them needs one plain piece of console
    metal that can take any size. The blank equipment tag is exactly that, and
    it already carries the right bevel and grain.
    """
    print("plates:")
    im = _load("plaques/blank_plaque_kit_2k.png")
    rows = _row_runs(_alpha(im), min_h=int(im.height * 0.05))
    if len(rows) != 2:
        raise SystemExit(f"expected 2 plaques, found {len(rows)}")
    for (ry0, ry1), name, w in zip(rows, ["plate_wide", "plate_recessed"], [768, 640]):
        band = im.crop((0, ry0, im.width, ry1))
        bx0, by0, bx1, by1 = _bbox(_alpha(band))
        _save(band.crop((bx0, by0, bx1, by1)), f"frame/{name}.png", w)


def build_annunciators() -> None:
    """2 sizes x 6 states. Rows are split, then each row is made uniform."""
    print("annunciators:")
    im = _load("indicators/annunciator_family_2k.png")
    a = _alpha(im)
    rows = _row_runs(a, min_h=int(im.height * 0.05))
    if len(rows) != 2:
        raise SystemExit(f"expected 2 lamp rows, found {len(rows)}")
    names = ["off", "standby", "active", "nominal", "warning", "critical"]
    sizes = {"small": 72, "primary": 104}
    for (ry0, ry1), size_name in zip(rows, ["small", "primary"]):
        band = im.crop((0, ry0, im.width, ry1))
        cols = _col_runs(_alpha(band), min_w=int(im.width * 0.02))
        if len(cols) != 6:
            raise SystemExit(f"{size_name}: expected 6 lamps, found {len(cols)}")
        cells = [band.crop((cx0, 0, cx1, band.height)) for cx0, cx1 in cols]
        for name, cell in zip(names, _uniform_cells(cells, sizes[size_name])):
            _save(cell, f"lamp/{size_name}_{name}.png")


def build_selector() -> None:
    """The seated-states bank is the canonical source.

    Its five wells are pitched to within +/-2px, so the interior splits into
    five identical repeating cells. Each cell carries the fascia AND one key
    state, which is why a state swap never disturbs the surrounding metal.
    """
    print("selector:")
    im = _load("selector/selector_bank_states_2k.png")
    a = _alpha(im)
    x0, y0, x1, y1 = _bbox(a)
    part = im.crop((x0, y0, x1, y1))
    pa = _alpha(part)
    h, w = pa.shape

    # Locate the five dark key wells through a band across their middle.
    arr = np.array(part, dtype=float)
    lum = (arr[..., 0] * .3 + arr[..., 1] * .6 + arr[..., 2] * .1) * (arr[..., 3] / 255.)
    band = lum[int(h * .42):int(h * .62)].mean(axis=0)
    dark = band < band.max() * .42
    runs, s = [], None
    for i, d in enumerate(dark):
        if d and s is None:
            s = i
        elif not d and s is not None:
            if i - s > w * .05:
                runs.append((s, i))
            s = None
    if s is not None and w - s > w * .05:
        runs.append((s, w))
    if len(runs) != 5:
        raise SystemExit(f"expected 5 selector wells, found {len(runs)}")

    centres = [(s + e) / 2.0 for s, e in runs]
    pitch = (centres[-1] - centres[0]) / 4.0
    left = centres[0] - pitch / 2.0
    right = centres[-1] + pitch / 2.0

    # Caps must use the CELL's scale factor, not their own width, or their
    # height stops matching the cells they butt against.
    cell_w = 224
    k = cell_w / pitch
    for rel, box in (("selector/cap_left.png", (0, 0, max(1, round(left)), h)),
                     ("selector/cap_right.png", (min(w - 1, round(right)), 0, w, h))):
        piece = part.crop(box)
        _save(piece, rel, max(1, round(piece.width * k)))

    # Cell order in the master, as generated.
    #
    # The divider ribs sit exactly on the cell boundaries, so every cut cell
    # carries a HALF rib on each side and a join reconstructs a whole one -
    # except at the two outer cells, which have no rib on their outward side.
    # Placing those in an interior slot would leave a half-width seam.
    #
    # So every cell is built from ONE canonical interior fascia (which has both
    # half-ribs) with only the key-cap window swapped in. All five cells then
    # differ solely by cap state, and any state can occupy any slot.
    states = ["idle", "focus", "pressed", "latched", "disabled"]
    CANON = 2                                  # an interior cell: both half-ribs
    cap_x = int(pitch * 0.41)                  # cap window, inset well clear of the ribs
    cap_y0, cap_y1 = int(h * 0.19), int(h * 0.81)

    def cell_box(i: int) -> tuple[int, int, int, int]:
        return (max(0, round(left + i * pitch)), 0,
                min(w, round(left + (i + 1) * pitch)), h)

    cb = cell_box(CANON)
    canon = part.crop(cb)
    cx_mid = canon.width // 2

    cell_h = max(1, round(h * cell_w / pitch))
    for i, name in enumerate(states):
        src_cell = part.crop(cell_box(i))
        mid = src_cell.width // 2
        win = src_cell.crop((mid - cap_x, cap_y0, mid + cap_x, cap_y1))
        cell = canon.copy()
        cell.paste(win, (cx_mid - cap_x, cap_y0))
        _save(cell.resize((cell_w, cell_h), Image.LANCZOS),
              f"selector/cell_{name}.png")

    # Empty frame kept for reference / compact mode.
    emp = _load("selector/selector_bank_frame_2k.png")
    ex0, ey0, ex1, ey1 = _bbox(_alpha(emp))
    _save(emp.crop((ex0, ey0, ex1, ey1)), "selector/bank_empty.png", 1280)


def build_state_strip(src: str, out_dir: str, names: list[str], width: int) -> None:
    im = _load(src)
    cols = _col_runs(_alpha(im), min_w=int(im.width * 0.03))
    if len(cols) != len(names):
        raise SystemExit(f"{src}: expected {len(names)} states, found {len(cols)}")
    cells = [im.crop((c0, 0, c1, im.height)) for c0, c1 in cols]
    for name, cell in zip(names, _uniform_cells(cells, width)):
        _save(cell, f"{out_dir}/{name}.png")


def build_parts() -> None:
    """Split the kit sheets into individually placeable parts.

    The chassis is bolted together at runtime, so it needs a screw it can put
    at a corner and a rail it can run down a side - not a contact sheet. These
    come from the already-approved kits; no new generation.
    """
    print("parts:")
    fast = ["screw_large", "screw_small", "rivet", "tab", "bracket", "handle"]
    ctrl = ["knob", "toggle", "guarded", "selector_rotary", "rocker", "button"]
    for src, names, w in (("fasteners/fastener_kit_2k.png", fast, 128),
                          ("controls/control_kit_2k.png", ctrl, 128)):
        im = _load(src)
        rows = _row_runs(_alpha(im), min_h=int(im.height * 0.06))
        if len(rows) != 2:
            raise SystemExit(f"{src}: expected 2 rows, found {len(rows)}")
        k = 0
        for ry0, ry1 in rows:
            band = im.crop((0, ry0, im.width, ry1))
            cols = _col_runs(_alpha(band), min_w=int(im.width * 0.03))
            if len(cols) != 3:
                raise SystemExit(f"{src}: expected 3 per row, found {len(cols)}")
            for cx0, cx1 in cols:
                cell = band.crop((cx0, 0, cx1, band.height))
                bx0, by0, bx1, by1 = _bbox(_alpha(cell))
                _save(cell.crop((bx0, by0, bx1, by1)), f"part/{names[k]}.png", w)
                k += 1


def build_controls() -> None:
    print("mode key:")
    build_state_strip("controls/mode_key_states_2k.png", "mode",
                      ["inactive", "armed", "active", "error"], 160)
    print("cycle rocker:")
    # As generated the paddle tilts right-down in cell 2 and left-down in cell 3.
    build_state_strip("selector/cycle_rocker_states_2k.png", "cycle",
                      ["neutral", "next", "prev"], 192)
    print("kits:")
    for src, dst, w in [("fasteners/fastener_kit_2k.png", "kit/fasteners.png", 768),
                        ("controls/control_kit_2k.png", "kit/controls.png", 768)]:
        im = _load(src)
        x0, y0, x1, y1 = _bbox(_alpha(im))
        _save(im.crop((x0, y0, x1, y1)), dst, w)


def main() -> int:
    if not SRC.exists():
        raise SystemExit(f"no master library at {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)
    build_frames()
    build_plates()
    build_annunciators()
    build_parts()
    build_selector()
    build_controls()
    total = sum(p.stat().st_size for p in OUT.rglob("*.png"))
    n = len(list(OUT.rglob("*.png")))
    print(f"\n{n} sprites, {total/1e6:.1f} MB -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
