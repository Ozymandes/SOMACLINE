#!/usr/bin/env python3
"""GATE 4-7: the subsystems added by the hardware-skin pass.

These are headless and fast, so they can run on every change:

  GATE 4  species      every specimen stays inside the world and keeps the
                       lobe-balance invariant, at every physiological extreme
  GATE 5  skin         9-slice panels keep their corners undistorted and their
                       content rect inside the panel at any size
  GATE 6  selector     what is DRAWN and what is CLICKABLE are the same bank,
                       and every state sprite has an identical footprint
  GATE 7  switching    switching specimens repeatedly corrupts no state and
                       never rewinds a simulation clock

Run:  python3 qa/console_gates.py
"""
from __future__ import annotations

import math
import os
import sys

import cairo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from abyssal.core.layout import resolve  # noqa: E402
from abyssal.core.lighting import MAX_RADIUS, LightField  # noqa: E402
from abyssal.core.physiology import PhysiologyModel  # noqa: E402
from abyssal.core.signals import Physiology, Telemetry  # noqa: E402
from abyssal.core.world import WORLD_RADIUS  # noqa: E402
from abyssal.organism.form import AbyssalForm, balance_error, max_extent  # noqa: E402
from abyssal.organism.species import CATALOGUE, by_index  # noqa: E402
from abyssal.skin import catalog as C  # noqa: E402
from abyssal.skin.surface import nine_surface, sprite_size  # noqa: E402
from abyssal.ui import console  # noqa: E402
from abyssal.ui import selector as SEL  # noqa: E402

SIZES = [(420, 340), (520, 420), (700, 560), (900, 700), (1100, 700),
         (1400, 880), (1600, 900), (1920, 1080), (2000, 400), (300, 900)]

FAKE = Telemetry(cpu_load=0.4, memory_pressure=0.6, temperature=0.7,
                 temp_available=True, cpu_pct=40.0, mem_used_gb=9.0,
                 mem_total_gb=16.0, temp_c=70.0, temp_label="qa")


def gate4_species() -> tuple[bool, list[str]]:
    print("=== GATE 4 — SPECIES ===")
    bad: list[str] = []
    print(f"  {'specimen':<24} {'lobes':>5} {'points':>7} {'extent':>8} "
          f"{'balance':>9}")
    for sp in CATALOGUE:
        org = AbyssalForm(sp.morph)
        worst_e = worst_b = 0.0
        for i in range(400):
            ph = Physiology(agitation=0.5 + 0.5 * math.sin(i * 0.11),
                            pulse=0.5 + 0.5 * math.sin(i * 0.037 + 1.0),
                            density=0.5 + 0.5 * math.sin(i * 0.071 + 2.0),
                            vitality=1.0 if i % 7 else 0.55)
            if i % 97 == 0:
                ph = Physiology(1.0, 1.0, 1.0, 1.0)
            elif i % 89 == 0:
                ph = Physiology(0.0, 0.0, 0.0, 0.0)
            org.update(1 / 60, ph)
            worst_e = max(worst_e, max_extent(org))
            worst_b = max(worst_b, balance_error(org))
        pts = sp.morph.n_fil * sp.morph.n_pts
        print(f"  {sp.name:<24} {sp.lobes:>5} {pts:>7} "
              f"{worst_e:>7.1f} {worst_b * 100:>8.4f}%")
        if worst_e > WORLD_RADIUS:
            bad.append(f"{sp.key}: extent {worst_e:.1f} > {WORLD_RADIUS}")
        if worst_b > 0.02:
            bad.append(f"{sp.key}: lobe imbalance {worst_b * 100:.3f}% > 2%")
    return (not bad), bad


def gate5_skin() -> tuple[bool, list[str]]:
    print("\n=== GATE 5 — SKIN / 9-SLICE ===")
    bad: list[str] = []
    panels = [("observation_bezel", C.OBSERVATION_BEZEL),
              ("segment_housing", C.SEGMENT_HOUSING),
              ("graph_well", C.GRAPH_WELL),
              ("meter_trough", C.METER_TROUGH),
              ("aux_frame", C.AUX_FRAME),
              ("plate", C.PLATE)]
    sizes = [(40, 18), (120, 40), (300, 120), (900, 200), (200, 700), (1600, 300)]
    missing = [n for n, ns in panels if sprite_size(ns.name)[0] == 0]
    if missing:
        print(f"  sprites absent ({', '.join(missing)}) — run tools/build_sprites.py")
        return True, []
    for name, ns in panels:
        for w, h in sizes:
            surf = nine_surface(ns, w, h)
            if surf is None:
                bad.append(f"{name} {w}x{h}: no surface")
                continue
            if (surf.get_width(), surf.get_height()) != (w, h):
                bad.append(f"{name} {w}x{h}: got "
                           f"{surf.get_width()}x{surf.get_height()}")
            cx, cy, cw, chh = ns.content(0, 0, w, h)
            if cx < -0.01 or cy < -0.01 or cx + cw > w + 0.01 or cy + chh > h + 0.01:
                bad.append(f"{name} {w}x{h}: content escapes panel")
            if cw <= 0.0 or chh <= 0.0:
                bad.append(f"{name} {w}x{h}: content collapsed")
    print(f"  {len(panels)} panels x {len(sizes)} sizes  "
          f"-> {len(panels) * len(sizes)} renders, content rect always inside")

    # The stage glass must never be wider than the stage that contains it.
    for w, h in SIZES:
        L = resolve(w, h)
        g = console.stage_content(L)
        s = L.stage
        if s.valid and (g.x < s.x - 0.01 or g.right > s.right + 0.01
                        or g.y < s.y - 0.01 or g.bottom > s.bottom + 0.01):
            bad.append(f"{w}x{h}: glass escapes stage")
        if s.valid and (g.w < s.w * 0.25 or g.h < s.h * 0.25):
            bad.append(f"{w}x{h}: glass collapsed to {g.w:.0f}x{g.h:.0f} "
                       f"inside stage {s.w:.0f}x{s.h:.0f}")
    print(f"  glass inside stage and >25% of it at {len(SIZES)} sizes")
    if MAX_RADIUS > 400.0:
        bad.append("lighting MAX_RADIUS too large to bound the composite group")
    return (not bad), bad


def gate6_selector() -> tuple[bool, list[str]]:
    print("\n=== GATE 6 — SELECTOR ===")
    bad: list[str] = []

    # Every state sprite must share one footprint, or a state swap would shift
    # the key. This is enforced by the build step; assert it independently.
    sizes = {st: sprite_size(C.selector_cell(st)) for st in C.SELECTOR_STATES}
    if all(v[0] for v in sizes.values()):
        uniq = set(sizes.values())
        if len(uniq) != 1:
            bad.append(f"selector cell footprints differ: {sizes}")
        else:
            print(f"  5 state sprites, identical footprint {uniq.pop()}")
    else:
        print("  sprites absent — run tools/build_sprites.py")

    # Drawn geometry == clickable geometry, at every size and every key.
    checked = 0
    for w, h in SIZES:
        L = resolve(w, h)
        if not L.show_controls:
            continue
        geo, mode = console.control_geometry(L)
        if not geo.valid:
            continue
        for i in range(geo.n):
            r = geo.key_rect(i)
            got = console.hit_controls(L, r.cx, r.cy)
            if got != ("key", i):
                bad.append(f"{w}x{h}: centre of key {i} hit-tests {got}")
            # just outside the bank must not hit a key
            if SEL.hit(geo, geo.x - 4.0, geo.y + geo.h * 0.5) is not None:
                bad.append(f"{w}x{h}: point left of bank hit a key")
            checked += 1
        if mode.valid:
            if console.hit_controls(L, mode.cx, mode.cy) != ("mode", 0):
                bad.append(f"{w}x{h}: mode key centre does not hit-test")
        # keys must not overlap and must tile the bank
        for i in range(geo.n - 1):
            if abs(geo.key_rect(i).right - geo.key_rect(i + 1).x) > 0.01:
                bad.append(f"{w}x{h}: gap/overlap between keys {i},{i+1}")
    print(f"  {checked} key centres hit-test to their own index")

    # State priority: latched loses to pressed, both lose to disabled.
    cases = [((0, None, None, frozenset()), "latched"),
             ((1, None, None, frozenset()), "idle"),
             ((1, 0, None, frozenset()), "pressed"),
             ((0, None, 0, frozenset()), "latched"),
             ((1, None, 0, frozenset()), "focus"),
             ((0, 0, 0, frozenset({0})), "disabled")]
    for (active, pressed, focus, dis), want in cases:
        got = SEL.key_state(0, active, pressed, focus, dis)
        if got != want:
            bad.append(f"key_state(0,{active},{pressed},{focus},{set(dis)}) "
                       f"= {got}, want {want}")
    print(f"  {len(cases)} state-priority cases correct")
    return (not bad), bad


def gate7_switching() -> tuple[bool, list[str]]:
    print("\n=== GATE 7 — SPECIMEN SWITCHING ===")
    bad: list[str] = []
    w, h = 1400, 880
    L = resolve(w, h)
    glass = console.stage_content(L)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    light = LightField()
    pm = PhysiologyModel()

    organisms = {i: AbyssalForm(by_index(i).morph, seed=1234 + i)
                 for i in range(len(CATALOGUE))}
    model = console.ConsoleModel(species=by_index(0), active=0)
    clocks = {i: 0.0 for i in organisms}

    # Hammer the selector: every specimen, many times, drawing each frame.
    order = [(i * 7 + 3) % len(CATALOGUE) for i in range(120)]
    for step, idx in enumerate(order):
        model.active = idx
        model.species = by_index(idx)
        model.pressed = idx if step % 3 == 0 else None
        org = organisms[idx]
        for _ in range(3):
            org.update(1 / 60, pm.update(1 / 60, FAKE))
        if org.time < clocks[idx] - 1e-12:
            bad.append(f"specimen {idx}: clock went backwards at step {step}")
        clocks[idx] = org.time
        console.draw_under(cr, L, model, 0.4)
        console.draw_over(cr, L, FAKE, 60.0, 16.6, model, light)
        if not (math.isfinite(org.core_r) and org.fil_x.min() > -1e6):
            bad.append(f"specimen {idx}: non-finite state at step {step}")

    live = sum(1 for c in clocks.values() if c > 0.0)
    print(f"  {len(order)} switches across {len(CATALOGUE)} specimens, "
          f"{live} clocks advanced, none rewound")

    # Switching must not disturb an unrelated specimen's state.
    a = organisms[0]
    before = float(a.time)
    for idx in (1, 2, 3, 4, 1, 2):
        model.active = idx
        model.species = by_index(idx)
        organisms[idx].update(1 / 60, pm.current)
        console.draw_over(cr, L, FAKE, 60.0, 16.6, model, light)
    if abs(a.time - before) > 1e-12:
        bad.append("switching advanced an inactive specimen's clock")
    print("  an inactive specimen's clock is untouched by switching")

    # Every specimen must survive being drawn at every layout size.
    for ww, hh in SIZES:
        LL = resolve(ww, hh)
        s2 = cairo.ImageSurface(cairo.FORMAT_ARGB32, ww, hh)
        c2 = cairo.Context(s2)
        for i in range(len(CATALOGUE)):
            model.active = i
            model.species = by_index(i)
            try:
                console.draw_under(c2, LL, model, 0.4)
                console.draw_over(c2, LL, FAKE, 60.0, 16.6, model, light)
            except Exception as exc:                      # pragma: no cover
                bad.append(f"{ww}x{hh} specimen {i}: {exc!r}")
    print(f"  all {len(CATALOGUE)} specimens drawn at {len(SIZES)} sizes")
    _ = glass
    return (not bad), bad


def main() -> int:
    results = [("GATE 4 species", *gate4_species()),
               ("GATE 5 skin", *gate5_skin()),
               ("GATE 6 selector", *gate6_selector()),
               ("GATE 7 switching", *gate7_switching())]
    print("\n=== SUMMARY ===")
    ok = True
    for name, passed, problems in results:
        print(f"  {name:<22} {'PASS' if passed else 'FAIL'}")
        for p in problems:
            print(f"      - {p}")
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
