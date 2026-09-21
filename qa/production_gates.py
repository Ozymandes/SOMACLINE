#!/usr/bin/env python3
"""Production gates for the six-module machine (GATES P1-P12).

    python3 qa/production_gates.py

    P1  all six accepted module assets exist (masters and runtime sprites)
    P2  every module has valid alpha: clear outside, opaque body
    P3  required apertures are genuinely transparent
    P4  selector wells accept the real key bounds, on one exact pitch
    P5  no green matte around the active key
    P6  no fixed runtime values baked into the hardware (data bays blank)
    P7  no duplicate outer fasteners between modules
    P8  telemetry rack seating matches the reference geometry
    P9  static composition cache is deterministic
    P10 graph histories are bounded
    P11 compact layout does not use a crushed selector bank
    P12 unfocused / hidden draw cadence is reduced
"""

from __future__ import annotations

import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from abyssal.core import layout as LY  # noqa: E402
from abyssal.core.layout import LayoutState, resolve  # noqa: E402
from abyssal.core.signals import Telemetry  # noqa: E402
from abyssal.organism.species import by_index  # noqa: E402
from abyssal.skin import hidpi  # noqa: E402
from abyssal.skin import modules as MOD  # noqa: E402
from abyssal.skin.surface import SPRITE_ROOT  # noqa: E402
from abyssal.telemetry.history import SPAN_S, History  # noqa: E402
from abyssal.ui import console  # noqa: E402
from abyssal.ui import selector as SEL  # noqa: E402

MASTERS = os.path.join(ROOT, "assets", "modules", "masters")
CANON = [(900, 700), (1200, 900), (1448, 1086), (1920, 1080), (700, 560)]
TEL = Telemetry(cpu_load=0.14, memory_pressure=0.6, temperature=0.8,
                temp_available=True, cpu_pct=14.0, mem_used_gb=8.2,
                mem_total_gb=13.5, temp_c=82.0, temp_label="CPU")


def _rgba(name: str) -> np.ndarray:
    return np.asarray(Image.open(os.path.join(SPRITE_ROOT, name + ".png"))
                      .convert("RGBA"))


def _surface_array(surf) -> np.ndarray:
    surf.flush()
    h, st = surf.get_height(), surf.get_stride()
    return np.frombuffer(bytes(surf.get_data()), np.uint8).reshape(h, st)[
        :, : surf.get_width() * 4].reshape(h, surf.get_width(), 4)


def p1_exist():
    bad = []
    names = ("shell", "header", "observation", "rack", "selector", "footer")
    for n in names:
        if not os.path.exists(os.path.join(SPRITE_ROOT, "module", n + ".png")):
            bad.append(f"runtime module/{n}.png missing")
    masters = os.listdir(MASTERS) if os.path.isdir(MASTERS) else []
    for tag in ("m01", "m02", "m03", "m04", "m05", "m06"):
        if sum(1 for f in masters if f.startswith(tag)) != 1:
            bad.append(f"expected exactly one accepted master {tag}_*")
    if len(MOD.ALL) != 6:
        bad.append(f"registry declares {len(MOD.ALL)} modules, not 6")
    print(f"  6 runtime modules, 6 accepted masters, registry of {len(MOD.ALL)}")
    return bad


_MASTER_TAG = {"module/shell": "m01", "module/header": "m02",
               "module/observation": "m03", "module/rack": "m04",
               "module/selector": "m05", "module/footer": "m06"}


def p2_alpha():
    bad = []
    for M in MOD.ALL:
        a = _rgba(M.name)[..., 3].astype(int)
        h, w = a.shape
        if (M.sw, M.sh) != (w, h):
            bad.append(f"{M.name}: registry {M.sw}x{M.sh} != sprite {w}x{h}")
        # Runtime sprites are cropped to the body, so their canvas edge IS
        # the body's antialiased edge. "Outside the body" is therefore tested
        # where it exists: outside each rounded corner (runtime) and on the
        # whole canvas border of the generated master.
        corner = max(a[:3, :3].max(), a[:3, -3:].max(),
                     a[-3:, :3].max(), a[-3:, -3:].max())
        if corner != 0:
            bad.append(f"{M.name}: alpha {corner} outside a rounded corner")
        src = [f for f in os.listdir(MASTERS)
               if f.startswith(_MASTER_TAG[M.name])]
        ma = np.asarray(Image.open(os.path.join(MASTERS, src[0]))
                        .convert("RGBA"))[..., 3].astype(int)
        border = np.concatenate([ma[0], ma[-1], ma[:, 0], ma[:, -1]])
        if border.max() > 8:
            bad.append(f"{M.name}: master canvas border alpha {border.max()}")
        clear, opaque = (a == 0).mean(), (a == 255).mean()
        part = 1.0 - clear - opaque
        if opaque < 0.30:
            bad.append(f"{M.name}: only {opaque:.2f} fully opaque")
        if part > 0.06:
            bad.append(f"{M.name}: {part:.3f} partial alpha - matte or dither")
        print(f"  {M.name:20s} {w}x{h}  clear {clear:.3f}  opaque {opaque:.3f}"
              f"  partial {part:.4f}  corners {corner}  master border "
              f"{border.max()}")
    return bad


def _clear_inside(M, key):
    """Max alpha inside an aperture, excluding its rounded corners: the
    aperture is tested as the union of its two corner-free bands."""
    x, y, w, h = M.bays[key]
    a = _rgba(M.name)[..., 3]
    e = 3
    rad = max(e, int(min(w, h) * 0.08))
    horiz = a[int(y) + rad:int(y + h) - rad, int(x) + e:int(x + w) - e]
    vert = a[int(y) + e:int(y + h) - e, int(x) + rad:int(x + w) - rad]
    return int(max(horiz.max(), vert.max())), horiz.size + vert.size


def p3_apertures():
    bad = []
    checks = [(MOD.OBSERVATION, "aperture")] + \
             [(MOD.SELECTOR, f"well_{i}") for i in range(5)] + \
             [(MOD.SELECTOR, "rocker_opening"), (MOD.SELECTOR, "mode_opening")]
    for M, key in checks:
        amax, n = _clear_inside(M, key)
        if amax != 0:
            bad.append(f"{M.name}:{key} max alpha {amax} inside aperture")
    print(f"  {len(checks)} apertures, alpha exactly 0 inside every one")
    return bad


def p4_wells():
    bad = []
    kw, kh = SEL.KEY_ASPECT, 1.0
    for W, H in CANON:
        L = resolve(W, H)
        if not L.show_controls:
            continue
        P = MOD.SELECTOR.place(L.controls)
        geo, _ = console.control_geometry(L)
        sizes = set()
        for i in range(5):
            k, well = geo.key_rect(i), P.bay(f"well_{i}")
            sizes.add((round(k.w, 6), round(k.h, 6)))
            if not (k.x <= well.x + 1e-6 and k.right >= well.right - 1e-6
                    and k.y <= well.y + 1e-6 and k.bottom >= well.bottom - 1e-6):
                bad.append(f"{W}x{H}: key {i} does not cover its well")
            if abs(k.w / k.h - kw / kh) > 0.002:
                bad.append(f"{W}x{H}: key {i} aspect {k.w / k.h:.4f}")
            if i < 4:
                d = geo.key_rect(i + 1).x - k.x
                if abs(d - geo.pitch) > 1e-6:
                    bad.append(f"{W}x{H}: pitch {i}->{i + 1} {d} != {geo.pitch}")
                if k.right >= geo.key_rect(i + 1).x:
                    bad.append(f"{W}x{H}: keys {i},{i + 1} overlap")
            lr = geo.label_rect(i)
            if geo.ledge and lr.y < k.bottom:
                bad.append(f"{W}x{H}: label {i} starts inside key {i}")
        if len(sizes) != 1:
            bad.append(f"{W}x{H}: keys differ in size {sizes}")
        cells = [P.bay(f"well_{i}") for i in range(5)]
        if len({(round(c.w, 6), round(c.h, 6)) for c in cells}) != 1:
            bad.append(f"{W}x{H}: wells differ in size")
    # the trued wells in the sprite itself: one size, one pitch
    xs = [MOD.SELECTOR.bays[f"well_{i}"][0] for i in range(5)]
    steps = {round(b - a, 3) for a, b in zip(xs, xs[1:])}
    if len(steps) != 1:
        bad.append(f"sprite wells not on one pitch: {steps}")
    print(f"  wells trued to pitch {MOD.SEL_PITCH:.3f}px; keys cover wells, "
          f"one size, exact pitch, labels clear, at {len(CANON)} sizes")
    return bad


def p5_matte():
    """The lit key may glow inside its own silhouette, never in a square."""
    bad = []
    # (a) the approved active sprites carry no matte: corners clear
    for i in range(5):
        a = _rgba(f"specimen/key_{i + 1:02d}_active")[..., 3]
        c = max(a[0, 0], a[0, -1], a[-1, 0], a[-1, -1])
        if c:
            bad.append(f"key_{i + 1:02d}_active corner alpha {c}")
    # (b) the rendered socket around the lit key is the same metal whether or
    #     not that key is lit: sample a ring just outside the key rectangle
    L = resolve(900, 700)
    hidpi.set_scale(1.0)
    m = console.ConsoleModel(species=by_index(0), active=0)
    geo, _ = console.control_geometry(L)
    k = geo.key_rect(0)
    ring = []
    for W in (0.10, 0.18):
        pad = k.w * W
        ring += [(k.x - pad, k.cy), (k.right + pad, k.cy),
                 (k.cx, k.y - pad * 0.5), (k.x - pad, k.y + k.h * 0.2),
                 (k.right + pad, k.bottom - k.h * 0.2)]

    def region(active):
        m.active = active
        console.clear_static_cache()
        u = console.layer_under(L, m)
        o = console.layer_over(L, m, TEL)
        regs = {r.name: r for r in console.regions(L, m, TEL, 60, 16.6, u, o)}
        r = regs["keys"]
        return r, _surface_array(r.surface).astype(int)
    r_on, lit = region(0)
    r_off, unlit = region(1)
    worst = 0
    for (x, y) in ring:
        px, py = int(x - r_on.rect.x), int(y - r_on.rect.y)
        if 0 <= py < lit.shape[0] and 0 <= px < lit.shape[1]:
            g = lit[py, px, 1] - unlit[py, px, 1]
            worst = max(worst, g)
    if worst > 10:
        bad.append(f"green lift of {worst}/255 outside the lit key: a matte")
    print(f"  active sprites have clear corners; socket green lift outside the"
          f" lit key = {worst}/255 (limit 10)")
    return bad


_DATA = ("title", "epithet", "live_window", "clock", "rail_", "_graph",
         "_numeric", "_state", "_title", "bay_", "terminal")


def p6_blank():
    bad = []
    n = 0
    for M in MOD.ALL:
        im = _rgba(M.name).astype(float)
        lum = 0.2126 * im[..., 0] + 0.7152 * im[..., 1] + 0.0722 * im[..., 2]
        for key, (x, y, w, h) in M.bays.items():
            if not any(t in key for t in _DATA) or key == "live":
                continue
            ins = max(4, int(min(w, h) * 0.18))
            r = lum[int(y) + ins:int(y + h) - ins, int(x) + ins:int(x + w) - ins]
            if r.size == 0:
                continue
            n += 1
            # live type is bright (>100); a blank recess floor stays near 20
            if r.max() > 60.0:
                bad.append(f"{M.name}:{key} max luminance {r.max():.0f} - "
                           f"something is painted in a live bay")
    print(f"  {n} data bays across the six modules, every floor blank "
          f"(max luminance <= 60)")
    return bad


def p7_fasteners():
    bad = []
    if MOD.SHELL.screws:
        bad.append("the shell declares its own screws")
    closest = 1e9
    for W, H in CANON:
        L = resolve(W, H)
        rects = {"header": (MOD.HEADER, L.header), "obs": (MOD.OBSERVATION, L.stage),
                 "rack": (MOD.RACK, L.readout), "sel": (MOD.SELECTOR, L.controls),
                 "foot": (MOD.FOOTER, L.footer)}
        pts = []
        for tag, (M, r) in rects.items():
            if not r.valid:
                continue
            P = M.place(r)
            for cx, cy, rad in M.screws:
                x, y = P.point(cx, cy)
                pts.append((tag, x, y, rad * P.k))
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                a, b = pts[i], pts[j]
                if a[0] == b[0]:
                    continue
                d = math.hypot(a[1] - b[1], a[2] - b[2])
                closest = min(closest, d / (a[3] + b[3]))
                if d < (a[3] + b[3]) * 1.6:
                    bad.append(f"{W}x{H}: {a[0]} and {b[0]} screws {d:.1f}px apart")
    print(f"  shell carries 0 screws; closest screws of different modules are "
          f"{closest:.2f} screw-diameters apart (limit 1.6)")
    return bad


def p8_rack():
    bad = []
    L = resolve(1448, 1086)
    r, (x, y, w, h) = L.readout, LY.REF_RACK
    d = max(abs(r.x - x), abs(r.y - y), abs(r.right - (x + w)), abs(r.bottom - (y + h)))
    if d > 3.0:
        bad.append(f"rack is {d:.1f}px from the reference rectangle")
    ref_m = LY.REF_CHASSIS[0] + LY.REF_CHASSIS[2] - (x + w)
    got_m = L.chassis.right - r.right
    if abs(got_m - ref_m) > 2.0:
        bad.append(f"rack right margin {got_m:.1f}px, reference {ref_m:.1f}px")
    for W, H in CANON:
        L = resolve(W, H)
        if L.state is LayoutState.COMPACT:
            continue
        if abs(L.readout.y - L.stage.y) > 0.5:
            bad.append(f"{W}x{H}: rack top {L.readout.y:.1f} != stage top {L.stage.y:.1f}")
        if abs(L.readout.bottom - L.controls.bottom) > 0.5:
            bad.append(f"{W}x{H}: rack bottom != selector bottom")
        if L.readout.right > L.chassis.right - 4.0:
            bad.append(f"{W}x{H}: rack not seated inside the shell")
        if L.readout.x < L.stage.right:
            bad.append(f"{W}x{H}: rack overlaps the observation assembly")
    print(f"  reference 1448x1086: rack within {d:.1f}px of target, right margin "
          f"{got_m:.1f}px (ref {ref_m:.0f}); top/bottom seated at "
          f"{len(CANON)} sizes")
    return bad


def p9_deterministic():
    """Same inputs -> same pixels. The wall clock is an INPUT (the clock
    region's), so it is pinned for the test; left free, a second boundary
    falling between two renders changes the clock region legitimately."""
    real_time = console.time
    console.time = types.SimpleNamespace(
        strftime=lambda fmt, *a: "12:34:56" if "%H" in fmt else "2026-01-01")
    try:
        return _p9()
    finally:
        console.time = real_time


def _p9():
    bad = []
    m = console.ConsoleModel(species=by_index(2), active=2, history=History(5.0))
    for ds in (1.0, 1.6):
        hidpi.set_scale(ds)
        for W, H in ((900, 700), (1448, 1086), (600, 480)):
            L = resolve(W, H)
            shots = []
            for _ in range(2):
                console.clear_static_cache()
                u = console.layer_under(L, m)
                o = console.layer_over(L, m, TEL)
                regs = console.regions(L, m, TEL, 60, 16.6, u, o)
                shots.append([bytes(s.get_data()) for s in
                              [u, o] + [r.surface for r in regs]])
            if shots[0] != shots[1]:
                bad.append(f"{W}x{H}@{ds}: cold renders differ")
            # a cache hit must return the identical surface object
            u2 = console.layer_under(L, m)
            if u2 is not u:
                bad.append(f"{W}x{H}@{ds}: warm under-layer was rebuilt")
    hidpi.set_scale(1.0)
    print("  static layers + regions byte-identical across cold renders at 1.0x"
          " and 1.6x; warm hits reuse the surface")
    return bad


def p10_history():
    bad = []
    h = History(5.0)
    before = h.nbytes
    for i in range(20000):
        h.push(i % 100, 40 + i % 30, 8.0, 16.6)
    if h.nbytes != before:
        bad.append("history grew")
    if h.cap != int(SPAN_S * 5.0):
        bad.append(f"cap {h.cap} != {SPAN_S} s at 5 Hz")
    for k in History.CHANNELS:
        if len(h[k]) != h.cap:
            bad.append(f"{k}: {len(h[k])} samples")
        if len(h[k].window(173)) != 173:
            bad.append(f"{k}: window not column-sized")
    print(f"  4 rings x {h.cap} samples ({SPAN_S:.0f} s at 5 Hz), {h.nbytes} bytes"
          f" before and after 20000 pushes")
    return bad


def p11_compact():
    bad = []
    sizes = [(600, 480), (460, 380), (680, 700), (1800, 420), (2000, 400), (300, 900)]
    for W, H in sizes:
        L = resolve(W, H)
        if L.state is not LayoutState.COMPACT:
            bad.append(f"{W}x{H}: expected COMPACT, got {L.state.value}")
            continue
        if L.show_controls or L.controls.valid:
            bad.append(f"{W}x{H}: compact shows a selector bank")
        if console.hit_controls(L, W * 0.5, H * 0.9) is not None:
            bad.append(f"{W}x{H}: compact has clickable keys")
    print(f"  {len(sizes)} compact sizes: no selector bank, no key hit areas "
          f"(keys 1-5 and arrows still switch)")
    return bad


def p12_cadence():
    bad = []
    from abyssal.app import FPS_FOCUSED, FPS_UNFOCUSED, Monitor

    def stub(active, suspended, cap=0.0):
        return types.SimpleNamespace(opts=types.SimpleNamespace(fps_cap=cap),
                                     is_active=lambda: active,
                                     _suspended=lambda: suspended)
    got = {"focused": Monitor.cadence(stub(True, False)),
           "unfocused": Monitor.cadence(stub(False, False)),
           "hidden": Monitor.cadence(stub(True, True))}
    if got["focused"] != FPS_FOCUSED or got["focused"] > 60.0:
        bad.append(f"focused cadence {got['focused']}")
    if not 0 < got["unfocused"] <= FPS_FOCUSED * 0.5:
        bad.append(f"unfocused cadence {got['unfocused']} not reduced")
    if got["hidden"] != 0.0:
        bad.append(f"hidden cadence {got['hidden']} (should stop)")
    if FPS_UNFOCUSED >= FPS_FOCUSED:
        bad.append("unfocused rate is not lower than focused")
    print(f"  cadence focused {got['focused']:.0f} / unfocused "
          f"{got['unfocused']:.0f} / hidden {got['hidden']:.0f} FPS")
    return bad


GATES = [("P1 module assets exist", p1_exist),
         ("P2 valid alpha", p2_alpha),
         ("P3 apertures transparent", p3_apertures),
         ("P4 wells accept keys", p4_wells),
         ("P5 no active-key matte", p5_matte),
         ("P6 no baked values", p6_blank),
         ("P7 no duplicate fasteners", p7_fasteners),
         ("P8 rack seating", p8_rack),
         ("P9 deterministic cache", p9_deterministic),
         ("P10 bounded history", p10_history),
         ("P11 compact, no bank", p11_compact),
         ("P12 reduced cadence", p12_cadence)]


def main() -> int:
    results = []
    for name, fn in GATES:
        print(f"\n=== {name} ===")
        bad = fn()
        for b in bad:
            print(f"  FAIL {b}")
        results.append((name, not bad))
    print("\n=== SUMMARY ===")
    for name, ok in results:
        print(f"  {name:28s} {'PASS' if ok else 'FAIL'}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
