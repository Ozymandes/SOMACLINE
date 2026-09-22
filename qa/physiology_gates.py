#!/usr/bin/env python3
"""PHYSIOLOGY GATES (PH1-PH7): the telemetry-driven organism condition.

    python3 qa/physiology_gates.py

The organisms must EXPERIENCE the machine's telemetry, not merely display it.
These gates run the REAL PhysiologyModel and the REAL SourceBody pulse layer -
no hand-built stand-in fields - and hold them to:

    PH1  condition model   the activity condition is smoothed, hysteretic,
                           rate-limited, and its hue mapping is monotone
    PH2  five x three      every species, driven through QUIESCENT / NORMAL /
                           STRESSED by real telemetry samples: finite fields,
                           hue ordered blue -> green -> orange, no explosion
    PH3  pulse kinematics  the wavefront travels through anatomy; its speed
                           rises with CPU excitation; determinism is bitwise
    PH4  personalities     each species' documented behaviour is present and
                           bounded (lag, alternation, mirror asymmetry, chase,
                           curl)
    PH5  rest identity    with every drive zeroed, geometry is the canonical
                           equation and is independent of the pulse phase; no
                           telemetry response leaks into the source equation
    PH6  resize safety     rendering at any viewport never mutates physiology
                           state, and re-rendering is pixel-identical
    PH7  palette          the pulse LUT covers blue -> cyan -> green -> lime ->
                           amber -> orange without purple or red
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cairo  # noqa: E402

from abyssal.core.physiology import (  # noqa: E402
    ACTIVITY_DEADBAND, ACTIVITY_RATE, AGITATION_RANGE, PULSE_RANGE,
    PhysiologyModel, _lerp, condition, smoothstep)


def _lerp_pub(t: float) -> float:
    """The pulse target the model assumes with no thermal sensor."""
    return _lerp(*PULSE_RANGE, t)
from abyssal.core.signals import Physiology, Telemetry  # noqa: E402
from abyssal.core.viewport import Viewport  # noqa: E402
from abyssal.core.world import WORLD_RADIUS  # noqa: E402
from abyssal.organism import mathforms as MF  # noqa: E402
from abyssal.organism import render as ORG_RENDER  # noqa: E402
from abyssal.organism.species import CATALOGUE, by_index  # noqa: E402

DT = 1.0 / 60.0

#: The three driven states, as REAL telemetry samples. Ranges follow a laptop:
#: k10temp Tctl idles 58-64 C, a busy cool machine sits near 74 C, and 92 C
#: under full load is hot but ordinary.
TEL_QUIESCENT = Telemetry(
    cpu_load=0.03, memory_pressure=0.42, temperature=0.30, temp_available=True,
    cpu_pct=3.0, mem_used_gb=5.7, mem_total_gb=13.5, temp_c=58.0,
    temp_label="K10TEMP", notes="physiology QA")
TEL_NORMAL = Telemetry(
    cpu_load=0.35, memory_pressure=0.61, temperature=0.45, temp_available=True,
    cpu_pct=35.0, mem_used_gb=8.2, mem_total_gb=13.5, temp_c=74.0,
    temp_label="K10TEMP", notes="physiology QA")
TEL_STRESSED = Telemetry(
    cpu_load=0.92, memory_pressure=0.93, temperature=0.86, temp_available=True,
    cpu_pct=92.0, mem_used_gb=12.6, mem_total_gb=13.5, temp_c=92.0,
    temp_label="K10TEMP", notes="physiology QA")
STATES = (("QUIESCENT", TEL_QUIESCENT), ("NORMAL", TEL_NORMAL),
          ("STRESSED", TEL_STRESSED))


def drive(specimen: int, tel: Telemetry, seconds: float = 6.0):
    """A REAL organism driven by the REAL model to steady state."""
    org = by_index(specimen).build()
    pm = PhysiologyModel()
    for _ in range(int(seconds / DT)):
        org.update(DT, pm.update(DT, tel))
    return org, pm


# --------------------------------------------------------------------------
def _circular_lag(r0: np.ndarray, r1: np.ndarray) -> float:
    """Phase of group 1 behind group 0, in cycles 0..1, from circular means.

    The per-point phase is a fraction in 0..1; its distribution wraps the
    circle, so plain means or medians land wherever the wrap leaves them.
    Treating the fractions as angles, the circular mean of each group sits at
    that group's own offset, and the argument difference is the lag - the
    common slope along the body cancels exactly.
    """
    a0, a1 = 2.0 * np.pi * r0, 2.0 * np.pi * r1
    m0 = math.atan2(float(np.sin(a0).mean()), float(np.cos(a0).mean()))
    m1 = math.atan2(float(np.sin(a1).mean()), float(np.cos(a1).mean()))
    return ((m0 - m1) / (2.0 * np.pi)) % 1.0


def ph1_condition_model() -> list[str]:
    print("=== PH1 — CONDITION MODEL ===")
    bad: list[str] = []
    # hue mapping: monotone, ends in the documented bands
    hs = [MF.state_hue(a / 20.0) for a in range(21)]
    if any(b < a - 1e-9 for a, b in zip(hs, hs[1:])):
        bad.append(f"state_hue not monotone: {hs}")
    if not hs[0] <= 0.12:
        bad.append(f"state_hue(0)={hs[0]:.3f} is not abyssal blue")
    if not hs[-1] >= 0.90:
        bad.append(f"state_hue(1)={hs[-1]:.3f} is not orange")
    # normalisation: a cool busy machine is NOT stressed; a hot idle one is
    busy_cool = condition(Telemetry(cpu_load=0.60, memory_pressure=0.5,
                                    temp_available=True, temp_c=66.0))
    hot_idle = condition(Telemetry(cpu_load=0.05, memory_pressure=0.5,
                                   temp_available=True, temp_c=92.0))
    if not (busy_cool[2] < 0.05):
        bad.append(f"cool load reads hot: stress={busy_cool[2]:.3f}")
    if not (hot_idle[2] > 0.80):
        bad.append(f"92 C idle reads cool: stress={hot_idle[2]:.3f}")
    if not (0.30 < busy_cool[0] < 0.80):
        bad.append(f"60% cool load activity out of band: {busy_cool[0]:.3f}")
    # hysteresis: sub-deadband wobble never adopts a new target
    pm = PhysiologyModel()
    tel = TEL_NORMAL
    for _ in range(600):
        pm.update(DT, tel)
    held = pm._held
    jitter = Telemetry(cpu_load=tel.cpu_load + 0.005, memory_pressure=0.61,
                       temperature=0.45, temp_available=True, temp_c=74.05)
    for _ in range(120):
        pm.update(DT, jitter if _ % 2 else tel)
    if abs(pm._held - held) > 1e-12:
        bad.append("sensor jitter inside the deadband moved the held target")
    # rate limit: a full-scale jump moves the condition at most RATE*dt per
    # frame, in either direction
    pm2 = PhysiologyModel()
    prev = pm2.current.activity
    steps = []
    for i in range(600):
        pm2.update(DT, TEL_STRESSED if i < 300 else TEL_QUIESCENT)
        steps.append(abs(pm2.current.activity - prev))
        prev = pm2.current.activity
    if max(steps) > ACTIVITY_RATE * DT * (1.0 + 1e-6) + 1e-12:
        bad.append(f"condition moved {max(steps):.5f} in one frame; "
                   f"rate limit is {ACTIVITY_RATE * DT:.5f}")
    # asymmetric EMA: the organism rouses faster than it calms
    pm3 = PhysiologyModel()
    seen = []
    for i in range(600):
        pm3.update(DT, TEL_STRESSED if i < 300 else TEL_QUIESCENT)
        seen.append(pm3.current.activity)
    up = next(i for i in range(1, 300) if seen[i] >= 0.5 * seen[299])
    dn = next(i for i in range(300, 600)
              if seen[i] <= 0.5 * seen[299] + 0.5 * seen[0])
    if not up < dn:
        bad.append(f"condition rouses ({up} frames) no faster than it calms ({dn})")
    print(f"  state_hue: 0->{hs[0]:.3f}, 0.5->{hs[10]:.3f}, 1->{hs[20]:.3f} "
          f"(monotone, blue -> orange)")
    print(f"  cool 60% load: activity {busy_cool[0]:.3f}, thermal stress "
          f"{busy_cool[2]:.3f}; 92 C idle: stress {hot_idle[2]:.3f}")
    print(f"  deadband holds ({ACTIVITY_DEADBAND}), rate limit "
          f"{ACTIVITY_RATE}/s holds, rouse {up} frames < calm {dn} frames")
    return bad


def ph2_five_by_three() -> list[str]:
    print("=== PH2 — FIVE SPECIES x THREE STATES ===")
    bad: list[str] = []
    for idx, sp in enumerate(CATALOGUE):
        hues, acts, es = [], [], []
        for sname, tel in STATES:
            org, pm = drive(idx, tel)
            p = pm.current
            x, y, w = org.points()
            e, h = org.excitation()
            n = x.size
            if not (np.isfinite(x).all() and np.isfinite(y).all()
                    and np.isfinite(w).all() and np.isfinite(e).all()
                    and np.isfinite(h).all() and np.isfinite(org.hue)):
                bad.append(f"{sp.key}/{sname}: non-finite physiology field")
            me = MF.max_extent(org)
            if me > WORLD_RADIUS:
                bad.append(f"{sp.key}/{sname}: extent {me:.1f} escapes the "
                           f"world ({WORLD_RADIUS:.1f})")
            if not (0.0 <= e.min() and e.max() <= 1.0 + 1e-6):
                bad.append(f"{sp.key}/{sname}: excitation out of 0..1")
            if not (0.0 <= h.min() and h.max() <= 1.0 + 1e-6):
                bad.append(f"{sp.key}/{sname}: hue out of 0..1")
            hues.append(float(h.mean()))
            acts.append(p.activity)
            es.append(p.stress)
        if not (acts[0] < acts[1] < acts[2]):
            bad.append(f"{sp.key}: activity not ordered across states {acts}")
        if not (hues[0] < hues[1] < hues[2]):
            bad.append(f"{sp.key}: mean hue not blue->green->orange {hues}")
        if not (es[2] > 0.9 > es[1]):
            bad.append(f"{sp.key}: stress band wrong {es}")
        print(f"  {sp.key:<10} hue {hues[0]:.3f} -> {hues[1]:.3f} -> "
              f"{hues[2]:.3f}   activity {acts[0]:.3f} -> {acts[1]:.3f} -> "
              f"{acts[2]:.3f}")
    return bad


def ph3_pulse_kinematics() -> list[str]:
    print("=== PH3 — PULSE KINEMATICS ===")
    bad: list[str] = []
    for idx, sp in enumerate(CATALOGUE):
        org, pm = drive(idx, TEL_QUIESCENT)
        w0, u0 = org.wave, 4.0
        for _ in range(int(4.0 / DT)):
            org.update(DT, pm.update(DT, TEL_QUIESCENT))
        spd_idle = (org.wave - w0) / 4.0
        expect_idle = org.PULSE_HZ * (1.0 + org.PULSE_GAIN * pm.current.excite)
        if abs(spd_idle - expect_idle) > 0.12 * expect_idle + 0.02:
            bad.append(f"{sp.key}: idle wave speed {spd_idle:.4f} != model "
                       f"{expect_idle:.4f}")
        # load: the cadence must rise with CPU excitation
        for _ in range(int(6.0 / DT)):
            org.update(DT, pm.update(DT, TEL_NORMAL))
        w1 = org.wave
        for _ in range(int(4.0 / DT)):
            org.update(DT, pm.update(DT, TEL_NORMAL))
        spd_load = (org.wave - w1) / 4.0
        if not spd_load > spd_idle * 1.5:
            bad.append(f"{sp.key}: CPU does not raise pulse cadence "
                       f"({spd_idle:.3f} -> {spd_load:.3f})")
        # the front travels forward along the body coordinate: correlate the
        # excitation profile over u between two instants. The correlation
        # peak sits at the distance the front covered in 0.3 s, and np.roll
        # with a negative shift aligns a profile that has moved UP the bins,
        # so the travel is -best_lag.
        def profile(o, bins=96):
            e, _ = o.excitation()
            u = o._u[:e.size]
            hst, _ = np.histogram(u, bins=bins, range=(0.0, 1.0), weights=e)
            return hst
        p0 = profile(org)
        travel = spd_load * 0.3
        for _ in range(18):
            org.update(DT, pm.update(DT, TEL_NORMAL))
        p1 = profile(org)
        lags = np.arange(-48, 48) / 96.0
        best, best_lag = -1.0, 0.0
        for L in lags:
            sh = np.roll(p1, int(round(L * 96)))
            c = float((p0 * sh).sum())
            if c > best:
                best, best_lag = c, L
        moved = -best_lag
        if not (0.6 * travel - 0.02 <= moved <= travel + 0.06):
            bad.append(f"{sp.key}: profile correlation travel {moved:.3f} "
                       f"implausible for wave speed {spd_load:.3f}")
        print(f"  {sp.key:<10} wave {spd_idle:.3f} -> {spd_load:.3f} cyc/s "
              f"under load; front travels +{moved:.3f} u in 0.3 s")
    # determinism: identical inputs, bitwise identical state
    for idx, sp in enumerate(CATALOGUE):
        a, pa = drive(idx, TEL_NORMAL)
        b, pb = drive(idx, TEL_NORMAL)
        xa, ya, wa = a.points(); xb, yb, wb = b.points()
        ea, ha = a.excitation(); eb, hb = b.excitation()
        if not (np.array_equal(xa, xb) and np.array_equal(ya, yb)
                and np.array_equal(wa, wb) and np.array_equal(ea, eb)
                and np.array_equal(ha, hb) and a.wave == b.wave
                and pa.current == pb.current):
            bad.append(f"{sp.key}: identical drives gave different state")
    print("  determinism: 5/5 species bitwise identical on repeat runs")
    return bad


def ph4_personalities() -> list[str]:
    print("=== PH4 — SPECIES PERSONALITIES ===")
    bad: list[str] = []

    # 01 tips lag the spine; stress makes the front ragged along the body.
    # The lag is read from the PRE-MOD phase offset: rebuild the wave
    # coordinate exactly as the species computes it and subtract it from the
    # wave term - what remains is the species' own per-point offset, with no
    # modular wrap to lie about signs.
    org, _ = drive(0, TEL_NORMAL)
    n0 = org._live
    u = org._u[:n0]
    lat = org._lat[:n0]
    s_raw = org._phase(n0, Physiology()).copy()
    offset = (org.wave - org.WAVES * u) - s_raw
    lag = float(offset[lat > 0.75].mean() - offset[lat < 0.25].mean())
    if not 0.02 < lag < 0.12:
        bad.append(f"01: filament tips do not lag the spine (lag {lag:.3f})")
    org, _ = drive(0, TEL_STRESSED)
    s = org._s[:org._live]
    u = org._u[:org._live]
    lat2 = org._lat[:org._live]
    expect = (np.float32(org.wave) - u - np.float32(0.07) * lat2)
    resid = (s - expect + 0.5) % 1.0 - 0.5
    ragged = abs((resid * np.sin(17.0 * u + 2.3 * org.wave)).mean() * 2)
    if ragged < 0.02:
        bad.append(f"01: stressed front is not ragged ({ragged:.4f})")
    org, _ = drive(0, TEL_QUIESCENT)
    s = org._s[:org._live]
    u = org._u[:org._live]
    lat3 = org._lat[:org._live]
    expect = (np.float32(org.wave) - u - np.float32(0.07) * lat3)
    resid = (s - expect + 0.5) % 1.0 - 0.5
    if abs(resid).max() > 1e-5:
        bad.append("01: front modulation present at rest")
    print(f"  01 sigmata    tips lag spine by {lag:.3f}; ragged front "
          f"{ragged:.3f} under stress, 0 at rest")

    # 02 bodies alternate; stress desynchronises and warms one first
    # The lag is read from the per-point phase relative to the body
    # coordinate, which is constant per body: r = (s - u) mod 1 clusters at
    # the body's own offset, and medians survive the modular wrap.
    org, _ = drive(1, TEL_QUIESCENT)
    n1 = org._live
    u = org._u[:n1]
    g = org._grp[:n1]
    s_raw = org._phase(n1, Physiology()).copy()
    offset = (org.wave - org.WAVES * u) - s_raw
    rest_lag = float(offset[g > 0.5].mean() - offset[g < 0.5].mean())
    if not 0.35 < rest_lag < 0.65:
        bad.append(f"02: bodies do not alternate at rest (lag {rest_lag:.3f})")
    org, pm = drive(1, TEL_STRESSED)
    s = org._s[:org._live]
    e, h = org.excitation()
    g = org._grp[:org._live]
    desync = abs((s[g > 0.5].mean() - s[g < 0.5].mean() + 0.5) % 1.0 - 0.5)
    if desync < 0.01:
        bad.append("02: stress does not desynchronise the pair")
    dhue = abs(h[g < 0.5].mean() - h[g > 0.5].mean())
    if dhue < 0.005:
        bad.append("02: stress does not warm one body before the other")
    print(f"  02 coniugata  alternation lag {rest_lag:.3f}; desync {desync:.3f}, "
          f"warm split {dhue:.4f} under stress")

    # 03 mirror symmetric at rest; heat lags one side
    org, _ = drive(2, TEL_QUIESCENT)
    s = org._s[:org._live]
    g = org._grp[:org._live]
    sym = abs(s[g < 0.5].mean() - s[g > 0.5].mean())
    if sym > 0.01:
        bad.append(f"03: mirror not symmetric at rest ({sym:.4f})")
    org, _ = drive(2, TEL_STRESSED)
    s = org._s[:org._live]
    e, h = org.excitation()
    g = org._grp[:org._live]
    asym = abs((s[g < 0.5].mean() - s[g > 0.5].mean() + 0.5) % 1.0 - 0.5)
    dhue = abs(h[g < 0.5].mean() - h[g > 0.5].mean())
    if asym < 0.005 or dhue < 0.005:
        bad.append(f"03: heat does not break the plane (asym {asym:.4f}, "
                   f"dhue {dhue:.4f})")
    print(f"  03 rostrata   mirror symmetry {sym:.4f} at rest; side lag "
          f"{asym:.4f} + warm split {dhue:.4f} under stress")

    # 04 the four-stage chase; stress synchronises the flare
    org, pm = drive(3, TEL_QUIESCENT, 8.0)
    leads = []
    for _ in range(24):
        e, _ = org.excitation()
        g4 = org._grp[:e.size].astype(int)
        means = [e[g4 == q].mean() for q in range(4)]
        leads.append(int(np.argmax(means)))
        for _ in range(12):
            org.update(DT, pm.update(DT, TEL_QUIESCENT))
    rotated = zip(leads, leads[1:] + leads[:1])
    if not all((b - a) % 4 <= 1 for a, b in rotated):
        bad.append(f"04: chase not sequential round the plumes: {leads}")
    org, _ = drive(3, TEL_STRESSED, 8.0)
    e, _ = org.excitation()
    g4 = org._grp[:e.size].astype(int)
    means = [e[g4 == q].mean() for q in range(4)]
    spread_hi = max(means) - min(means)
    org, _ = drive(3, TEL_QUIESCENT, 8.0)
    e, _ = org.excitation()
    g4 = org._grp[:e.size].astype(int)
    means0 = [e[g4 == q].mean() for q in range(4)]
    spread_lo = max(means0) - min(means0)
    if not spread_hi < spread_lo * 0.6:
        bad.append(f"04: stress does not pull the plumes together "
                   f"({spread_lo:.4f} -> {spread_hi:.4f})")
    print(f"  04 articulata chase leads {leads[:8]}...; flare spread "
          f"{spread_lo:.4f} -> {spread_hi:.4f} under stress")

    # 05 ribs lag the shaft; heat curls the feather
    org, _ = drive(4, TEL_NORMAL)
    n5 = org._live
    lat = org._lat[:n5]
    u5 = org._u[:n5]
    s_raw = org._phase(n5, Physiology()).copy()
    offset = (org.wave - org.WAVES * u5) - s_raw
    rib_lag = float(offset[lat > 0.6].mean() - offset[lat < 0.2].mean())
    if not 0.03 < rib_lag < 0.16:
        bad.append(f"05: ribs do not lag the shaft ({rib_lag:.3f})")
    x0, y0, _ = drive(4, TEL_QUIESCENT)[0].points()
    x1, y1, _ = drive(4, TEL_STRESSED)[0].points()
    n = min(x0.size, x1.size)
    x1, y1 = x1[:n], y1[:n]
    cx, cy = org.src.frame_cx, org.src.frame_cy
    a0 = np.arctan2(y0[:n] - cy, x0[:n] - cx)
    a1 = np.arctan2(y1 - cy, x1 - cx)
    curl = float(np.abs((a1 - a0 + np.pi) % (2 * np.pi) - np.pi).mean())
    if curl < 0.01:
        bad.append(f"05: heat does not curl the feather ({curl:.4f} rad)")
    print(f"  05 solitaria  ribs lag shaft {rib_lag:.3f}; stress curl "
          f"{curl:.4f} rad mean")
    return bad


def ph5_rest_identity() -> list[str]:
    print("=== PH5 — REST IDENTITY ===")
    bad: list[str] = []
    # The perturbation contract, as gate 4 states it: the five original
    # drives zeroed (density full so the whole equation is expressed) and the
    # four condition fields zero. In this state the geometry is the published
    # equation - and the pulse layer must add NOTHING to it.
    rest = Physiology(agitation=0.0, pulse=0.0, density=1.0, flux=0.0,
                      surge=0.0)
    for idx, sp in enumerate(CATALOGUE):
        a = sp.build()
        b = sp.build()
        for _ in range(240):
            a.update(DT, rest)
            b.update(DT, rest)
        # The pulse phase is private state; push it apart and prove the
        # resting geometry does not care. Colour pulses at rest; shape does
        # not.
        b.wave += 0.373
        b.aux += 1.1
        b._jit += 2.0
        b.update(DT, rest)
        a.update(DT, rest)
        xa, ya, wa = a.points()
        xb, yb, wb = b.points()
        if not (np.array_equal(xa, xb) and np.array_equal(ya, yb)
                and np.array_equal(wa, wb)):
            bad.append(f"{sp.key}: resting geometry depends on pulse phase")
        e, h = a.excitation()
        if not (np.isfinite(e).all() and np.isfinite(h).all()):
            bad.append(f"{sp.key}: non-finite resting physiology")
        if not 0.0 < e.mean() < 0.25:
            bad.append(f"{sp.key}: resting pulse not subtle ({e.mean():.3f})")
        if abs(h.mean() - MF.state_hue(0.0)) > 0.02:
            bad.append(f"{sp.key}: resting hue is not abyssal blue")
    # The condition fields: zero telemetry -> zero condition. The five
    # original drives keep their designed idle floors (the creature breathes
    # with no load at all); the four new fields must NOT.
    pm = PhysiologyModel()
    for _ in range(600):
        pm.update(DT, Telemetry())
    p = pm.current
    if (p.activity, p.stress, p.excite, p.tension) != (0.0, 0.0, 0.0, 0.0):
        bad.append(f"zero telemetry gives non-zero condition: "
                   f"activity={p.activity} stress={p.stress} "
                   f"excite={p.excite} tension={p.tension}")
    # With no thermal sensor the model assumes a temperate machine
    # (temperature 0.35); pulse settles on that assumption, agitation on its
    # floor.
    pm2 = PhysiologyModel()
    for _ in range(7200):
        pm2.update(DT, Telemetry())
    p2 = pm2.current
    if abs(p2.agitation - AGITATION_RANGE[0]) > 1e-4:
        bad.append(f"idle agitation floor moved: {p2.agitation:.4f}")
    if abs(p2.pulse - _lerp_pub(0.35)) > 0.005:
        bad.append(f"no-sensor thermal assumption moved: pulse "
                   f"{p2.pulse:.4f}")
    print("  5/5 species: resting geometry bitwise independent of pulse "
          "phase; resting colour subtle abyssal blue; zero telemetry gives "
          "zero condition, idle floors intact")
    return bad


def ph6_resize_safety() -> list[str]:
    print("=== PH6 — RENDER/RESIZE SAFETY ===")
    bad: list[str] = []
    for idx, sp in enumerate(CATALOGUE):
        org, pm = drive(idx, TEL_NORMAL)
        wave = org.wave
        aux = org.aux
        x0, y0, w0 = org.points()
        e0, h0 = org.excitation()
        snaps = []
        for side in (420, 900, 1400, 233):
            h = max(240, int(side * 0.7))
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, side, h)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0.02, 0.03, 0.05)
            cr.paint()
            vp = Viewport.for_stage(0, 0, float(side), float(h))
            ORG_RENDER.draw_organism(cr, vp, org)
            surf.flush()
            snaps.append((side, h, np.ndarray(shape=(h, side, 4), dtype=np.uint8,
                                              buffer=surf.get_data()).copy()))
        x1, y1, w1 = org.points()
        e1, h1 = org.excitation()
        if not (np.array_equal(x0, x1) and np.array_equal(y0, y1)
                and np.array_equal(w0, w1) and np.array_equal(e0, e1)
                and np.array_equal(h0, h1) and org.wave == wave
                and org.aux == aux):
            bad.append(f"{sp.key}: rendering mutated physiology state")
        # determinism of the raster: same size, same state -> same pixels.
        # A trailing specimen's accumulator carries its trail between renders
        # by design, so it is rendered repeatedly until the trail reaches its
        # steady state; the last two rasters must then be identical.
        side, hh, _ = snaps[1]
        again = None
        for attempt in range(64):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, side, hh)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0.02, 0.03, 0.05)
            cr.paint()
            vp = Viewport.for_stage(0, 0, float(side), float(hh))
            ORG_RENDER.draw_organism(cr, vp, org)
            surf.flush()
            prev, again = again, np.ndarray(
                shape=(hh, side, 4), dtype=np.uint8,
                buffer=surf.get_data()).copy()
        if not np.array_equal(prev, again):
            bad.append(f"{sp.key}: re-render at the same size differs")
    print("  5/5 species: 4 viewports x render leaves physiology untouched; "
          "raster deterministic")
    return bad


def ph7_palette() -> list[str]:
    print("=== PH7 — PULSE PALETTE ===")
    bad: list[str] = []
    lut = ORG_RENDER.PULSE_LUT
    # No purple, no red. Purple is red AND blue together over a quiet green;
    # red is a hot red channel over a dead green. Orange keeps green alive.
    r, g, b = lut[:, 0], lut[:, 1], lut[:, 2]
    purple = int(((r > 0.45) & (b > 0.45) & (g < 0.35)).sum())
    red = int(((r > 0.80) & (g < 0.25)).sum())
    if purple or red:
        bad.append(f"palette passes through purple ({purple}) or red ({red})")
    if not (b[0] > 0.5 and b[-1] < 0.5):
        bad.append("palette does not start blue and end warm")
    if not (g[100:180] > 0.7).any():
        bad.append("palette has no green band")
    if abs(r[-1] - 1.0) > 0.1:
        bad.append("palette does not reach orange")
    print(f"  7 stops, 256-entry LUT: blue {tuple(lut[8].round(2))} -> "
          f"green {tuple(lut[128].round(2))} -> orange {tuple(lut[248].round(2))}")
    return bad


def main() -> int:
    failures: list[str] = []
    for gate in (ph1_condition_model, ph2_five_by_three, ph3_pulse_kinematics,
                 ph4_personalities, ph5_rest_identity, ph6_resize_safety,
                 ph7_palette):
        failures += gate()
    print()
    if failures:
        print(f"=== PHYSIOLOGY GATES: {len(failures)} FAILURE(S) ===")
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print("=== PHYSIOLOGY GATES: ALL PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
