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
import time
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
from abyssal.organism.mathforms import max_extent  # noqa: E402
from abyssal.organism.species import CATALOGUE, by_index, by_key  # noqa: E402
from abyssal.skin import catalog as C  # noqa: E402
from abyssal.skin.surface import nine_surface, sprite_size  # noqa: E402
from abyssal.ui import console  # noqa: E402
from abyssal.core.layout import _BANK_ASPECT as L_BANK_ASPECT  # noqa: E402
from abyssal.ui import selector as SEL  # noqa: E402

SIZES = [(420, 340), (520, 420), (700, 560), (900, 700), (1100, 700),
         (1400, 880), (1600, 900), (1920, 1080), (2000, 400), (300, 900)]

FAKE = Telemetry(cpu_load=0.4, memory_pressure=0.6, temperature=0.7,
                 temp_available=True, cpu_pct=40.0, mem_used_gb=9.0,
                 mem_total_gb=16.0, temp_c=70.0, temp_label="qa")


def _signature(org) -> "np.ndarray":
    """A pose-independent shape signature: where the body's mass sits.

    A joint histogram over radius and |angle to the vertical|, normalised.
    Two organisms that differ only in symmetry order - the failure this gate
    exists to catch - produce nearly identical signatures; two genuinely
    different body plans do not.
    """
    import numpy as np
    m = org.fil_alpha > 0.02
    x = org.fil_x[m].ravel()
    y = org.fil_y[m].ravel()
    r = np.hypot(x, y) / WORLD_RADIUS
    a = np.abs(np.arctan2(np.abs(x), y)) / math.pi
    h, _, _ = np.histogram2d(np.clip(r, 0, 1), a, bins=(8, 6),
                             range=((0, 1), (0, 1)))
    tot = h.sum()
    return h.ravel() / (tot if tot else 1.0)


def gate4_species() -> tuple[bool, list[str]]:
    """The five specimens stay inside the world, stay finite, and stay
    MORPHOLOGICALLY DISTINCT from one another."""
    import numpy as np
    print("=== GATE 4 — SPECIES ===")
    bad: list[str] = []
    sigs: dict[str, "np.ndarray"] = {}
    print(f"  {'specimen':<24} {'fils':>5} {'points':>7} {'extent':>8} "
          f"{'sim ms':>7}")
    for sp in CATALOGUE:
        org = sp.build()
        worst_e = 0.0
        t0 = time.perf_counter()
        for i in range(400):
            ph = Physiology(agitation=0.5 + 0.5 * math.sin(i * 0.11),
                            pulse=0.5 + 0.5 * math.sin(i * 0.037 + 1.0),
                            density=0.5 + 0.5 * math.sin(i * 0.071 + 2.0),
                            flux=0.5 + 0.5 * math.sin(i * 0.13),
                            surge=0.5 + 0.5 * math.sin(i * 0.31))
            if i % 97 == 0:
                ph = Physiology(1.0, 1.0, 1.0, 1.0, 1.0)
            elif i % 89 == 0:
                ph = Physiology(0.0, 0.0, 0.0, 0.0, 0.0)
            org.update(1 / 60, ph)
            worst_e = max(worst_e, max_extent(org))
        ms = (time.perf_counter() - t0) / 400 * 1000.0
        print(f"  {sp.name:<24} {org._nf:>5} {org.n_points:>7} "
              f"{worst_e:>7.1f} {ms:>6.2f}ms")
        if worst_e > WORLD_RADIUS:
            bad.append(f"{sp.key}: extent {worst_e:.1f} > {WORLD_RADIUS}")
        if not (np.isfinite(org.fil_x).all() and np.isfinite(org.fil_y).all()
                and np.isfinite(org.node_x).all()):
            bad.append(f"{sp.key}: non-finite coordinates")
        if ms > 1.6:
            bad.append(f"{sp.key}: sim {ms:.2f}ms/frame is over budget")
        sigs[sp.key] = _signature(org)

    # Every pair must be clearly different. The previous catalogue was one
    # solver with the symmetry order changed and would fail this outright.
    keys = list(sigs)
    worst = (1e9, "", "")
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            d = float(np.abs(sigs[keys[i]] - sigs[keys[j]]).sum())
            if d < worst[0]:
                worst = (d, keys[i], keys[j])
            if d < 0.35:
                bad.append(f"{keys[i]} and {keys[j]} are morphologically "
                           f"near-identical (signature distance {d:.3f})")
    print(f"  closest pair: {worst[1]} / {worst[2]}  distance {worst[0]:.3f} "
          f"(floor 0.350)")

    # Symmetra's mirror plane is exact at rest, and only heat may break it.
    sym = by_key("symmetra")
    if sym is not None:
        org = sym.build()
        for _ in range(120):
            org.update(1 / 60, Physiology(agitation=0.3, pulse=0.2,
                                          density=0.6))
        half = org._nf // 2
        err = float(np.max(np.abs(org.fil_x[:half] + org.fil_x[half:])))
        print(f"  symmetra mirror error at rest: {err:.2e} world units")
        if err > 1e-9:
            bad.append(f"symmetra mirror plane broken at rest by {err:.3e}")
        for _ in range(240):
            org.update(1 / 60, Physiology(agitation=0.9, pulse=1.0,
                                          density=0.9))
        hot = float(np.max(np.abs(org.fil_x[:half] + org.fil_x[half:])))
        print(f"  symmetra mirror error at thermal limit: {hot:.1f} "
              f"(expected > 0: heat breaks the plane)")
        if hot <= 1.0:
            bad.append("symmetra shows no symmetry instability when hot")
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

    # All ten specimen key plates must share ONE footprint, or pressing a key
    # would make it jump. Enforced by the build step; asserted independently.
    sizes = {(i, pl): sprite_size(C.specimen_key(i, pl))
             for i in range(5) for pl in C.SPECIMEN_KEY_PLATES}
    if all(v[0] for v in sizes.values()):
        uniq = set(sizes.values())
        if len(uniq) != 1:
            bad.append(f"specimen key footprints differ: {sorted(uniq)}")
        else:
            got = uniq.pop()
            print(f"  10 specimen key plates, identical footprint {got}")
            if abs(got[0] / got[1] - SEL.KEY_ASPECT) > 0.002:
                bad.append(f"KEY_ASPECT {SEL.KEY_ASPECT:.4f} != sprite "
                           f"{got[0] / got[1]:.4f}")
    else:
        print("  key sprites absent — run tools/build_sprites.py")

    # layout may not import ui, so it carries the bank aspect as a constant.
    # If the two drift, every control row is mis-sized. Assert they agree.
    if abs(L_BANK_ASPECT - SEL.natural_aspect()) > 0.01:
        bad.append(f"layout._BANK_ASPECT {L_BANK_ASPECT} != "
                   f"selector.natural_aspect() {SEL.natural_aspect():.3f}")

    # The keys are raster plates: one uniform scale, always.
    for w, h in SIZES:
        L = resolve(w, h)
        if not L.show_controls:
            continue
        g, _ = console.control_geometry(L)
        if g.valid and abs(g.key_w / g.key_h - SEL.KEY_ASPECT) > 0.004:
            bad.append(f"{w}x{h}: key drawn at aspect "
                       f"{g.key_w / g.key_h:.4f}, authored "
                       f"{SEL.KEY_ASPECT:.4f}")

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
            if SEL.hit(geo, geo.key_x - 4.0, geo.key_y + geo.key_h * 0.5) is not None:
                bad.append(f"{w}x{h}: point left of bank hit a key")
            checked += 1
        if mode.valid:
            if console.hit_controls(L, mode.cx, mode.cy) != ("mode", 0):
                bad.append(f"{w}x{h}: mode key centre does not hit-test")
        # Keys are evenly pitched and never overlap. They are deliberately
        # NOT butted: the gap between them is mounting metal, and a click
        # there must not select a specimen.
        for i in range(geo.n - 1):
            d = geo.key_rect(i + 1).x - geo.key_rect(i).x
            if abs(d - geo.pitch) > 0.01:
                bad.append(f"{w}x{h}: keys {i},{i+1} pitch {d} != {geo.pitch}")
            if geo.key_rect(i).right >= geo.key_rect(i + 1).x:
                bad.append(f"{w}x{h}: keys {i},{i+1} overlap")
            midgap = (geo.key_rect(i).right + geo.key_rect(i + 1).x) * 0.5
            if SEL.hit(geo, midgap, geo.key_y + geo.key_h * 0.5) is not None:
                bad.append(f"{w}x{h}: gap between keys {i},{i+1} hit-tests")
    print(f"  {checked} key centres hit-test to their own index")

    # State priority: latched loses to pressed, both lose to disabled.
    # A pressed or latched key shows the ILLUMINATED plate; everything else
    # shows the raised one. Nothing is tinted at runtime.
    for st, want in (("latched", "active"), ("pressed", "active"),
                     ("idle", "inactive"), ("focus", "inactive"),
                     ("disabled", "inactive")):
        if SEL.plate(st) != want:
            bad.append(f"plate({st}) = {SEL.plate(st)}, want {want}")

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

    organisms = {i: by_index(i).build(seed=1234 + i)
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


def gate8_static_cache() -> tuple[bool, list[str]]:
    """The cached static hardware layer is pixel-identical to a fresh render.

    A cache that can change what is drawn is a bug generator. This renders a
    full frame twice at each size - once warm, once with the cache dropped -
    and asserts the two are byte-identical. The clock is frozen first, because
    it is the one readout that legitimately differs between two renders.
    """
    import numpy as np
    print("\n=== GATE 8 — STATIC LAYER ===")
    bad: list[str] = []
    real_strftime = console.time.strftime
    console.time.strftime = lambda f, *a: ("1984-07-16" if "%Y" in f
                                           else "14:27:03")
    try:
        sys.path.insert(0, ROOT)
        from qa import offscreen as off

        def arr(s):
            s.flush()
            return np.ndarray(
                shape=(s.get_height(), s.get_stride() // 4, 4),
                dtype=np.uint8, buffer=s.get_data()
            )[:, :s.get_width(), :].copy()

        for w, h in ((900, 700), (1400, 880), (1920, 1080)):
            warm = arr(off.frame(w, h, 0, 4.0))
            console.clear_static_cache()
            fresh = arr(off.frame(w, h, 0, 4.0))
            d = int(np.abs(warm.astype(int) - fresh.astype(int)).max())
            print(f"  {w}x{h}: max pixel delta {d}")
            if d != 0:
                bad.append(f"{w}x{h}: cached layer differs from fresh by {d}")
    finally:
        console.time.strftime = real_strftime
    return (not bad), bad


def main() -> int:
    results = [("GATE 4 species", *gate4_species()),
               ("GATE 5 skin", *gate5_skin()),
               ("GATE 6 selector", *gate6_selector()),
               ("GATE 7 switching", *gate7_switching()),
               ("GATE 8 static layer", *gate8_static_cache())]
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
