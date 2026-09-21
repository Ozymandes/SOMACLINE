#!/usr/bin/env python3
"""Measured resource use of the REAL app under Hyprland, per layout and state.

    python3 qa/perf_live.py [--dwell 8] [--out report.json]

For each layout (compact / instrument / archive) the app is launched floating
on a scratch workspace and measured in three window states:

    focused     the monitor window has focus
    unfocused   visible, but a focus-sink window (a second instance capped at
                1 FPS) has focus
    hidden      the scratch workspace is switched away, so the compositor
                suspends the surface

Per state it records process CPU % (utime+stime from /proc/<pid>/stat over the
dwell), RSS, and the app's own probe: FPS, draw / simulation / telemetry ms,
and the cadence it chose. The user's workspace is restored at the end.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qa.torture import (d_float, d_resize, dsp, find_win, focus, hypr,  # noqa: E402
                        settle, wait_for_window)

SCRATCH = os.environ.get("ABYSSAL_SCRATCH", "/tmp/claude-1000/abyssal-qa")
LAYOUTS = [("compact", 600, 480), ("instrument", 900, 700),
           ("archive", 1440, 900)]
TICK = os.sysconf("SC_CLK_TCK")


def cpu_ticks(pid: int) -> int:
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return int(f[11]) + int(f[12])          # utime + stime


def rss_mb(pid: int) -> float:
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


def measure(pid: int, probe: str, dwell: float) -> dict:
    n0 = sum(1 for _ in open(probe)) if os.path.exists(probe) else 0
    t0, c0 = time.time(), cpu_ticks(pid)
    time.sleep(dwell)
    t1, c1 = time.time(), cpu_ticks(pid)
    rows = [json.loads(x) for x in list(open(probe))[n0:] if x.strip()]
    rows = [r for r in rows if r.get("event") == "sample"]
    last = rows[-1] if rows else {}

    def med(k):
        v = sorted(r[k] for r in rows if k in r)
        return v[len(v) // 2] if v else None
    return {"cpu_pct": 100.0 * (c1 - c0) / TICK / (t1 - t0),
            "rss_mb": rss_mb(pid), "fps": med("fps"),
            "draw_ms": med("draw_avg_ms"), "sim_ms": med("sim_ms"),
            "tel_ms": med("tel_ms"), "cadence": last.get("cadence"),
            "active": last.get("active"), "samples": len(rows),
            "frames_during": (rows[-1]["frames"] - rows[0]["frames"])
            if len(rows) > 1 else None}


def under_cursor(addr: str, w: int, h: int) -> None:
    """Focus a window and park it under the pointer.

    Hyprland focus follows the mouse: whatever is under the cursor takes
    focus back as soon as the pointer moves. Parking the window that SHOULD
    have focus under the pointer makes the focused/unfocused states stick on
    a desktop that is in use.
    """
    try:
        x, y = (int(float(v)) for v in hypr("cursorpos").replace(",", " ").split())
    except Exception:
        x, y = 800, 500
    focus(addr)
    dsp(f"hl.dsp.window.move({{ x = {max(0, x - w // 2)}, y = {max(0, y - h // 2)}, exact = true }})")
    focus(addr)


def ensure_size(pid: int, addr: str, w: int, h: int) -> bool:
    for _ in range(4):
        focus(addr); dsp(d_resize(w, h)); settle(0.8)
        c = find_win(pid)
        if c and abs(c["size"][0] - w) <= 2 and abs(c["size"][1] - h) <= 2:
            return True
    return False


def launch(args: list[str], out: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "abyssal.app", *args],
                            cwd=ROOT, stdout=open(out, "w"),
                            stderr=subprocess.STDOUT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell", type=float, default=8.0)
    ap.add_argument("--workspace", type=int, default=9)
    ap.add_argument("--out")
    o = ap.parse_args()
    os.makedirs(SCRATCH, exist_ok=True)
    original = json.loads(hypr("-j", "activeworkspace"))["id"]
    report: dict = {}
    procs: list[subprocess.Popen] = []
    try:
        dsp(f"hl.dsp.focus({{ workspace = '{o.workspace}' }})")
        settle(0.5)
        sink = launch(["--width", "320", "--height", "260", "--fps-cap", "1"],
                      os.path.join(SCRATCH, "sink.out"))
        procs.append(sink)
        sw = wait_for_window(sink.pid)
        focus(sw["address"]); dsp(d_float(True)); settle(0.3)
        focus(sw["address"]); dsp(d_resize(320, 260)); settle(0.3)
        for name, w, h in LAYOUTS:
            probe = os.path.join(SCRATCH, f"perf_{name}.jsonl")
            if os.path.exists(probe):
                os.remove(probe)
            p = launch(["--width", str(w), "--height", str(h),
                        "--probe", probe], os.path.join(SCRATCH, f"{name}.out"))
            procs.append(p)
            win = wait_for_window(p.pid)
            focus(win["address"]); dsp(d_float(True)); settle(0.4)
            if not ensure_size(p.pid, win["address"], w, h):
                print(f"  WARNING {name}: window did not reach {w}x{h}")
            settle(2.0)
            res = {"size": [w, h]}
            under_cursor(win["address"], w, h); settle(1.0)
            res["focused"] = measure(p.pid, probe, o.dwell)
            under_cursor(sw["address"], 320, 260); settle(1.0)
            res["unfocused"] = measure(p.pid, probe, o.dwell)
            dsp(f"hl.dsp.focus({{ workspace = '{o.workspace - 1}' }})")
            settle(1.0)
            res["hidden"] = measure(p.pid, probe, o.dwell)
            dsp(f"hl.dsp.focus({{ workspace = '{o.workspace}' }})")
            settle(0.8)
            state = json.loads([x for x in open(probe) if x.strip()][-1])["state"]
            res["state"] = state
            report[name] = res
            p.terminate()
            p.wait(timeout=5)
            procs.remove(p)
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        hypr("dispatch", f"hl.dsp.focus({{ workspace = '{original}' }})")

    print(f"\n{'layout':11s} {'state':10s} {'window':9s} {'CPU %':>6s} "
          f"{'RSS MB':>7s} {'FPS':>6s} {'draw':>6s} {'sim':>6s} {'tel':>6s} "
          f"{'cadence':>7s}")
    for name, r in report.items():
        for k in ("focused", "unfocused", "hidden"):
            m = r[k]
            f = lambda v, d=2: "-" if v is None else f"{v:.{d}f}"  # noqa: E731
            print(f"{name:11s} {r['state']:10s} {k:9s} {f(m['cpu_pct'], 1):>6s} "
                  f"{f(m['rss_mb'], 0):>7s} {f(m['fps'], 1):>6s} "
                  f"{f(m['draw_ms']):>6s} {f(m['sim_ms']):>6s} "
                  f"{f(m['tel_ms']):>6s} {f(m['cadence'], 0):>7s}")
    if o.out:
        json.dump(report, open(o.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
