#!/usr/bin/env python3
"""Capture clean product screenshots (no debug overlay) at chosen sizes."""
from __future__ import annotations
import os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = "/tmp/claude-1000/abyssal-qa/final"
sys.path.insert(0, ROOT)
from qa.torture import d_float, d_resize, dsp, find_win, focus, hypr, settle, wait_for_window  # noqa: E402

import json  # noqa: E402

SHOTS = [("compact", 460, 380, []), ("instrument", 900, 700, []),
         ("instrument-tall", 760, 620, []), ("archive", 1400, 880, []),
         ("calibration", 900, 700, ["--calibration"]),
         ("specimen-2-trispira", 1400, 880, ["--specimen", "1"]),
         ("specimen-3-pentafida", 1400, 880, ["--specimen", "2"]),
         ("specimen-4-hexastoma", 1400, 880, ["--specimen", "3"]),
         ("specimen-5-bifida", 1400, 880, ["--specimen", "4"])]


def monitor_rect():
    m = json.loads(hypr("-j", "monitors"))[0]
    s = m["scale"]
    return 0, 0, int(m["width"] / s), int(m["height"] / s)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    dsp("hl.dsp.focus({ workspace = '9' })"); settle(0.6)
    mx, my, mw, mh = monitor_rect()
    print(f"monitor logical {mw}x{mh}")
    for name, w, h, extra in SHOTS:
        proc = subprocess.Popen(
            [sys.executable, "-m", "abyssal.app", "--width", str(w),
             "--height", str(h), *extra],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            win = wait_for_window(proc.pid)
            focus(win["address"]); dsp(d_float(True)); settle(0.6)
            focus(win["address"]); dsp(d_resize(w, h)); settle(0.4)
            # Park it fully on-screen so grim never captures off-monitor white.
            px = max(mx + 8, mx + (mw - w) // 2)
            py = max(my + 8, my + (mh - h) // 2)
            focus(win["address"])
            dsp(f"hl.dsp.window.move({{ x = {px}, y = {py}, exact = true }})")
            time.sleep(3.0)   # let the organism settle into a nice pose
            win = find_win(proc.pid)
            x, y = win["at"]; sw, sh = win["size"]
            # clamp to the monitor so grim never captures off-screen white
            x0, y0 = max(mx, x), max(my, y)
            x1, y1 = min(mx + mw, x + sw), min(my + mh, y + sh)
            path = os.path.join(OUT, f"{name}.png")
            subprocess.run(["grim", "-g", f"{x0},{y0} {x1-x0}x{y1-y0}", path],
                           capture_output=True)
            print(f"  {name:<16} window {sw}x{sh} at {x},{y} -> captured "
                  f"{x1-x0}x{y1-y0}  {'OK' if os.path.exists(path) else 'FAIL'}")
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill()
            settle(0.4)
    hypr("dispatch", "hl.dsp.focus({ workspace = '1' })")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
