#!/usr/bin/env python3
"""Deterministic golden dumps for the Rust port parity gates.

Run from the repo root:  python3 tools/parity_dump.py

Writes rust/tests/golden/:
  s0X_perm.f32, s0X_u.f32, s0X_lat.f32, s0X_grp.f32   anatomy (one file per species)
  s0X_f<N>_{x,y,w,e,h}.f32                            point clouds at protocol frames
  physio.json                                          PhysiologyModel replay
  history.json                                         Ring window() replay
  layout.json                                          resolve() at probe sizes
  paint.bin                                            paint_physio end-to-end surface
  lut.json                                             PULSE_LUT (256x3)

The Rust side replays the EXACT same protocol (rust/tests/parity.rs) and
compares within tolerance. Files are little-endian float32 unless .json.
"""

import json
import os
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from abyssal.core.layout import resolve  # noqa: E402
from abyssal.core.physiology import PhysiologyModel  # noqa: E402
from abyssal.core.signals import Physiology, Telemetry  # noqa: E402
from abyssal.core.theme import CYAN, CYAN_DEEP, AMBER  # noqa: E402
from abyssal.organism import mathforms as MF  # noqa: E402
from abyssal.organism import sources as S  # noqa: E402
from abyssal.organism.render import PULSE_LUT  # noqa: E402
from abyssal.telemetry.history import History  # noqa: E402

OUT = os.path.join(ROOT, "rust", "tests", "golden")
SEED = 20260920


def w_f32(name: str, arr) -> None:
    a = np.ascontiguousarray(arr, dtype=np.float32)
    with open(os.path.join(OUT, name), "wb") as fh:
        fh.write(a.tobytes())


def dump_anatomy() -> None:
    for s in S.SOURCES:
        org = MF.BODIES[s.key](s, seed=SEED)
        w_f32(f"{s.key}_perm.f32", org._perm)
        w_f32(f"{s.key}_u.f32", org._u)
        w_f32(f"{s.key}_lat.f32", org._lat)
        w_f32(f"{s.key}_grp.f32", org._grp)


# The frame protocol: identical in the Rust replay.
REST = Physiology()
LOADED = Physiology(agitation=0.8, pulse=0.9, density=0.75, flux=0.6, surge=0.5,
                    vitality=1.0, activity=0.7, stress=0.6, excite=0.8, tension=0.3)
DT = 1.0 / 60.0
DUMP_FRAMES = (60, 180, 300, 419)  # after this many update() calls


def phys_at(frame: int) -> Physiology:
    if frame <= 120:
        return REST
    if frame <= 240:
        return LOADED
    return REST


def dump_frames() -> None:
    for s in S.SOURCES:
        org = MF.BODIES[s.key](s, seed=SEED)
        for frame in range(1, max(DUMP_FRAMES) + 1):
            org.update(DT, phys_at(frame))
            if frame in DUMP_FRAMES:
                tag = f"{s.key}_f{frame}"
                x, y, w = org.points()
                e, h = org.excitation()
                w_f32(f"{tag}_x.f32", x)
                w_f32(f"{tag}_y.f32", y)
                w_f32(f"{tag}_w.f32", w)
                w_f32(f"{tag}_e.f32", e)
                w_f32(f"{tag}_h.f32", h)


def dump_physio() -> None:
    """Replay a fixed telemetry protocol through PhysiologyModel."""
    steps = []
    tel = []
    for i in range(600):
        ph = (i % 300) / 300.0
        cpu = 0.05 + 0.8 * (0.5 + 0.5 * np.sin(i / 47.0))
        mem = 0.6 + 0.3 * ph
        hot = 60.0 + 30.0 * (0.5 + 0.5 * np.sin(i / 150.0))
        io = max(0.0, np.sin(i / 23.0)) * 0.8
        t = Telemetry(
            cpu_load=float(min(1.0, cpu)),
            memory_pressure=float(mem),
            temperature=float(min(1.0, (hot - 30.0) / 65.0)),
            io_rate=float(io),
            temp_available=True,
            io_available=True,
            cpu_pct=float(min(1.0, cpu)) * 100.0,
            mem_used_gb=float(6.0 + 20.0 * mem),
            mem_total_gb=32.0,
            temp_c=float(hot),
            temp_label="Tctl",
            io_mb_s=float(200.0 * io),
            net_mb_s=float(80.0 * io),
            notes="golden",
        )
        tel.append(t)
    model = PhysiologyModel()
    rec = []
    for i, t in enumerate(tel):
        render = (0.5 + 0.5 * np.sin(i / 91.0)) * 0.4
        p = model.update(1.0 / 30.0, t, float(render))
        rec.append([p.agitation, p.pulse, p.density, p.flux, p.surge,
                    p.vitality, p.activity, p.stress, p.excite, p.tension])
    with open(os.path.join(OUT, "physio.json"), "w") as fh:
        json.dump({"dt": 1.0 / 30.0, "steps": rec}, fh)
    # telemetry inputs, for the Rust replay
    with open(os.path.join(OUT, "telemetry.json"), "w") as fh:
        json.dump([{
            "cpu_load": t.cpu_load, "memory_pressure": t.memory_pressure,
            "temperature": t.temperature, "io_rate": t.io_rate,
            "temp_available": t.temp_available, "io_available": t.io_available,
        } for t in tel], fh)


def dump_history() -> None:
    hist = History(5.0)
    for i in range(300):
        cpu = 3.0 + 90.0 * (0.5 + 0.5 * np.sin(i / 31.0))
        temp = None if i % 7 == 0 else 55.0 + 25.0 * (0.5 + 0.5 * np.cos(i / 19.0))
        mem = 5.0 + 20.0 * (0.5 + 0.5 * np.sin(i / 13.0))
        frame = 16.0 + 2.0 * np.sin(i / 5.0)
        hist.push(float(cpu), temp, float(mem), float(frame))
    out = {}
    for ch in ("cpu", "thermal", "memory", "frame"):
        ring = hist[ch]
        win = ring.window(48)
        out[ch] = {
            "len": len(ring),
            "last": ring.last(),
            "peak": ring.peak(),
            "window48": [None if np.isnan(v) else float(v) for v in win],
        }
    with open(os.path.join(OUT, "history.json"), "w") as fh:
        json.dump(out, fh)


def dump_layout() -> None:
    sizes = [(600, 480), (900, 700), (1440, 900), (500, 300), (1600, 1000),
             (300, 600), (760, 420), (2560, 1600)]
    out = []
    for (w, h) in sizes:
        L = resolve(w, h)

        def r(rr):
            return [rr.x, rr.y, rr.w, rr.h]

        out.append({
            "w": w, "h": h, "state": L.state.value,
            "header": r(L.header), "stage": r(L.stage), "readout": r(L.readout),
            "controls": r(L.controls), "footer": r(L.footer),
            "chassis": r(L.chassis),
            "type": [L.type.title, L.type.specimen, L.type.subtitle, L.type.label,
                     L.type.value, L.type.micro, L.type.tracking],
            "show": [L.show_subtitle, L.show_meta, L.show_controls,
                     L.show_footer, L.show_chassis, L.show_status],
            "readout_vertical": L.readout_vertical,
        })
    with open(os.path.join(OUT, "layout.json"), "w") as fh:
        json.dump(out, fh)


def dump_paint() -> None:
    """End-to-end paint_physio parity: same accumulators -> same ARGB32 bytes."""
    import cairo

    w, h = 200, 150
    rng = np.random.default_rng(7)
    n = 9000
    px = rng.uniform(0, w - 1, n).astype(np.float32)
    py = rng.uniform(0, h - 1, n).astype(np.float32)
    wt = rng.uniform(0.2, 2.0, n).astype(np.float32)
    ex = rng.uniform(0.0, 1.0, n).astype(np.float32)
    hu = rng.uniform(0.0, 1.0, n).astype(np.float32)
    persist = 0.6235

    from abyssal.organism import pointfield as PF

    ent = PF.field_buffers("golden", w, h)
    for _ in range(3):
        PF.accumulate_physio(ent, px, py, wt, ex, hu, persist)

    w_f32("paint_px.f32", px)
    w_f32("paint_py.f32", py)
    w_f32("paint_w.f32", wt)
    w_f32("paint_e.f32", ex)
    w_f32("paint_h.f32", hu)
    w_f32("paint_acc.f32", ent["acc"])
    w_f32("paint_acc_e.f32", ent["acc_e"])
    w_f32("paint_acc_h.f32", ent["acc_h"])

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    PF.paint_physio(cr, ent, 0.0, 0.0, CYAN_DEEP, CYAN,
                    PULSE_LUT, ink=0.376471, scale_up=1.0)
    surf.flush()
    with open(os.path.join(OUT, "paint.bin"), "wb") as fh:
        fh.write(bytes(surf.get_data()))
    # warm-tinted variant, exercising the lo/hi ramp mix
    lo = (CYAN_DEEP[0] + (0.34 - CYAN_DEEP[0]) * 0.2,
          CYAN_DEEP[1] + (0.20 - CYAN_DEEP[1]) * 0.2,
          CYAN_DEEP[2] + (0.06 - CYAN_DEEP[2]) * 0.2)
    hi = (CYAN[0] + (AMBER[0] - CYAN[0]) * 0.2,
          CYAN[1] + (AMBER[1] - CYAN[1]) * 0.2,
          CYAN[2] + (AMBER[2] - CYAN[2]) * 0.2)
    surf2 = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr2 = cairo.Context(surf2)
    PF.paint_physio(cr2, ent, 0.0, 0.0, lo, hi, PULSE_LUT, ink=0.454902,
                    scale_up=1.0)
    surf2.flush()
    with open(os.path.join(OUT, "paint_warm.bin"), "wb") as fh:
        fh.write(bytes(surf2.get_data()))
    with open(os.path.join(OUT, "paint_lohi.json"), "w") as fh:
        json.dump({"lo": list(lo), "hi": list(hi)}, fh)


def dump_lut() -> None:
    lut = np.asarray(PULSE_LUT, dtype=np.float32)
    with open(os.path.join(OUT, "lut.f32"), "wb") as fh:
        fh.write(lut.tobytes())
    # state_hue reference points
    from abyssal.organism.mathforms import state_hue
    hues = [state_hue(a) for a in (0.0, 0.1, 0.25, 0.5, 0.64, 0.8, 0.95, 1.0)]
    with open(os.path.join(OUT, "hue.json"), "w") as fh:
        json.dump(hues, fh)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    dump_anatomy()
    dump_frames()
    dump_physio()
    dump_history()
    dump_layout()
    dump_paint()
    dump_lut()
    n = len(os.listdir(OUT))
    print(f"golden dumps written: {n} files in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
