#!/usr/bin/env python3
"""GATE 3 in the REAL window: steady-state FPS per size, under the compositor.

Headless ImageSurface timings are indicative; these are the numbers that count.
"""
from __future__ import annotations
import json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = "/tmp/claude-1000/abyssal-qa"
sys.path.insert(0, ROOT)
from qa.torture import (clients, d_float, d_resize, d_fullscreen, dsp, find_win,  # noqa: E402
                        focus, hypr, settle, wait_for_window)

SIZES = [(420, 340), (700, 560), (900, 700), (1150, 720), (1400, 860)]
DWELL = 7.0


def run() -> int:
    probe = os.path.join(SCRATCH, "perf.jsonl")
    if os.path.exists(probe):
        os.remove(probe)
    proc = subprocess.Popen(
        [sys.executable, "-m", "abyssal.app", "--probe", probe,
         "--width", "900", "--height", "700"],
        cwd=ROOT, stdout=open(os.path.join(SCRATCH, "perf.out"), "w"),
        stderr=subprocess.STDOUT)
    try:
        w = wait_for_window(proc.pid)
        addr = w["address"]
        focus(addr); dsp(d_float(True)); settle(0.8)
        for sw, sh in SIZES:
            focus(addr); dsp(d_resize(sw, sh))
            time.sleep(DWELL)
        focus(addr); dsp(d_fullscreen("fullscreen"))
        time.sleep(DWELL)
        focus(addr); dsp(d_fullscreen("fullscreen"))
        time.sleep(2.0)
        alive = proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill()

    rows = [json.loads(l) for l in open(probe) if l.strip()]
    # Keep only steady-state samples, and drop the first after each resize.
    buckets: dict = {}
    for r in rows:
        if r["event"] != "sample" or r["fps"] <= 0:
            continue
        buckets.setdefault((r["w"], r["h"], r["state"]), []).append(r["fps"])
    print(f"\n=== GATE 3 — LIVE (window on Hyprland, dwell {DWELL:.0f}s) ===")
    print(f"  {'logical':>12} {'device@1.6':>12} {'state':<11} {'n':>3} "
          f"{'fps min':>8} {'fps med':>8}")
    for (sw, sh, st), v in sorted(buckets.items(), key=lambda kv: kv[0][0] * kv[0][1]):
        v = sorted(v[1:]) or sorted(v)
        print(f"  {sw:>5.0f}x{sh:<5.0f} {sw*1.6:>5.0f}x{sh*1.6:<5.0f} {st:<11} "
              f"{len(v):>3} {min(v):>8.1f} {v[len(v)//2]:>8.1f}")
    print(f"\n  app alive at end: {alive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
