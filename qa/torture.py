#!/usr/bin/env python3
"""GATE 0 harness: drive the real window through real Hyprland transitions.

Runs the app on a scratch workspace and cycles it through tile / resize /
fullscreen / float / resize-floating / tile, repeatedly, while the app appends
a JSONL geometry probe. Then asserts the resize contract actually held.

Usage:  python3 qa/torture.py [--cycles N] [--workspace N] [--shots]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.environ.get("ABYSSAL_SCRATCH", "/tmp/claude-1000/abyssal-qa")


# NOTE: Hyprland 0.56 on Omarchy fronts hyprctl (and the IPC socket) with a
# LUA dispatcher API. The classic `hyprctl dispatch resizeactive exact 900 700`
# string form is a syntax error here; it must be
# `hyprctl dispatch "hl.dsp.window.resize({ x = 900, y = 700, exact = true })"`.
# Getting this wrong fails SILENTLY as far as window size is concerned, which
# is how the first run of this harness produced a false negative.

def hypr(*args: str) -> str:
    return subprocess.run(["hyprctl", *args], capture_output=True,
                          text=True).stdout.strip()


def dsp(expr: str) -> str:
    """Run one Lua dispatcher expression; raise if Hyprland rejects it."""
    out = hypr("dispatch", expr)
    if out.startswith("error"):
        raise RuntimeError(f"hyprctl rejected {expr!r}: {out.splitlines()[0]}")
    return out


def clients() -> list[dict]:
    try:
        return json.loads(hypr("-j", "clients"))
    except Exception:
        return []


def find_win(pid: int) -> dict | None:
    for c in clients():
        if c.get("pid") == pid:
            return c
    return None


def focus(addr: str) -> None:
    dsp(f"hl.dsp.focus({{ window = 'address:{addr}' }})")


def d_float(on: bool) -> str:
    return f"hl.dsp.window.float({{ action = '{'on' if on else 'off'}' }})"


def d_fullscreen(mode: str) -> str:
    return f"hl.dsp.window.fullscreen({{ mode = '{mode}', action = 'toggle' }})"


def d_resize(w: int, h: int) -> str:
    return f"hl.dsp.window.resize({{ x = {w}, y = {h}, exact = true }})"


def d_nudge(dx: int, dy: int) -> str:
    """Relative resize — this is what moves a TILED window's split."""
    return f"hl.dsp.window.resize({{ x = {dx}, y = {dy}, relative = true }})"


def wait_for_window(pid: int, timeout: float = 15.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        w = find_win(pid)
        if w and w.get("size", [0, 0])[0] > 0:
            return w
        time.sleep(0.15)
    raise RuntimeError(f"window for pid {pid} never appeared")


def settle(s: float = 0.55) -> None:
    time.sleep(s)


class Torture:
    def __init__(self, opts) -> None:
        self.opts = opts
        self.probe = os.path.join(SCRATCH, "probe.jsonl")
        self.shotdir = os.path.join(SCRATCH, "shots")
        os.makedirs(self.shotdir, exist_ok=True)
        if os.path.exists(self.probe):
            os.remove(self.probe)
        self.proc: subprocess.Popen | None = None
        self.addr = ""
        self.steps: list[tuple[str, str]] = []
        self.ghosts: list[subprocess.Popen] = []

    # -- lifecycle -------------------------------------------------------
    def launch(self) -> None:
        env = dict(os.environ)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "abyssal.app",
             "--probe", self.probe, "--debug",
             "--width", "900", "--height", "700"],
            cwd=ROOT, env=env,
            stdout=open(os.path.join(SCRATCH, "app.out"), "w"),
            stderr=subprocess.STDOUT,
        )
        w = wait_for_window(self.proc.pid)
        self.addr = w["address"]
        print(f"  window {self.addr} at {w['size']}  floating={w['floating']}")

    def spawn_ghost(self) -> None:
        """A second tiled window, so the monitor is forced to share the tile."""
        import shutil
        for term in ("foot", "alacritty", "kitty", "ghostty"):
            if shutil.which(term):
                p = subprocess.Popen([term, "-e", "sleep", "9000"],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                self.ghosts.append(p)
                settle(1.2)
                print(f"  ghost tile: {term}")
                return
        print("  (no terminal found; splitratio steps will be no-ops)")

    def cleanup(self) -> None:
        for g in self.ghosts:
            try:
                g.terminate()
            except Exception:
                pass
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- steps -----------------------------------------------------------
    def step(self, name: str, expr: str = "", shot: bool = False) -> None:
        if expr:
            focus(self.addr)
            dsp(expr)
        settle()
        if not self.alive():
            raise RuntimeError(f"APP DIED during step '{name}' "
                               f"(exit {self.proc.returncode})")
        w = find_win(self.proc.pid)
        size = w["size"] if w else [0, 0]
        self.steps.append((name, f"{size[0]}x{size[1]}"))
        print(f"    {name:<26} {size[0]}x{size[1]}")
        if shot and self.opts.shots and w:
            self.shot(name, w)

    def shot(self, name: str, w: dict) -> None:
        x, y = w["at"]
        sw, sh = w["size"]
        out = os.path.join(self.shotdir, f"{name}.png")
        subprocess.run(["grim", "-g", f"{x},{y} {sw}x{sh}", out],
                       capture_output=True)

    def cycle(self, n: int) -> None:
        print(f"  --- cycle {n} ---")
        self.step(f"c{n}-tile", d_float(False), shot=True)
        # Tiled resizes: relative nudges move the split, which is what a user
        # actually does with SUPER+arrow in a tiling WM.
        self.step(f"c{n}-tile-narrow", d_nudge(-260, 0), shot=True)
        self.step(f"c{n}-tile-narrower", d_nudge(-160, -120))
        self.step(f"c{n}-tile-wide", d_nudge(500, 0), shot=True)
        self.step(f"c{n}-tile-tall", d_nudge(0, 180))
        self.step(f"c{n}-tile-back", d_nudge(-80, -60))
        self.step(f"c{n}-fullscreen", d_fullscreen("fullscreen"), shot=True)
        self.step(f"c{n}-unfullscreen", d_fullscreen("fullscreen"))
        self.step(f"c{n}-maximize", d_fullscreen("maximized"), shot=True)
        self.step(f"c{n}-unmaximize", d_fullscreen("maximized"))
        self.step(f"c{n}-float", d_float(True), shot=True)
        for w, h in [(420, 340), (700, 560), (900, 700), (1150, 720),
                     (1400, 860), (300, 900), (1800, 420), (240, 200)]:
            self.step(f"c{n}-float-{w}x{h}", d_resize(w, h),
                      shot=(w, h) in ((420, 340), (900, 700),
                                      (1400, 860), (240, 200)))
        self.step(f"c{n}-retile", d_float(False), shot=True)

    def rapid_resize(self, seconds: float = 6.0) -> None:
        """Hammer resize far faster than a human can, to shake out races."""
        print("  --- rapid resize storm ---")
        focus(self.addr)
        dsp(d_float(True))
        settle(0.4)
        t0 = time.time()
        i = 0
        import math
        while time.time() - t0 < seconds:
            w = int(300 + 900 * (0.5 + 0.5 * math.sin(i * 0.4)))
            h = int(220 + 620 * (0.5 + 0.5 * math.cos(i * 0.31)))
            hypr("dispatch", d_resize(w, h))
            i += 1
            time.sleep(0.05)
        settle()
        if not self.alive():
            raise RuntimeError("APP DIED during rapid resize storm")
        print(f"    survived {i} resizes in {seconds:.0f}s")
        self.steps.append(("rapid-storm", f"{i} resizes"))


def analyse(probe_path: str) -> tuple[bool, list[str]]:
    rows = []
    with open(probe_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    problems: list[str] = []
    if len(rows) < 20:
        problems.append(f"only {len(rows)} probe events recorded")

    # 1. simulation must never reset or run backwards
    prev = -1.0
    resets = 0
    for r in rows:
        if r["sim_time"] < prev - 1e-6:
            resets += 1
        prev = r["sim_time"]
    if resets:
        problems.append(f"simulation clock went BACKWARDS {resets}x (reset!)")

    # 2. nothing may ever rebuild
    bad = [r for r in rows if r["rebuilds"] != 0]
    if bad:
        problems.append(f"{len(bad)} events reported rebuilds != 0")

    # 3. geometry must stay isotropic (circles stay circles)
    worst_iso = max(r["iso_err"] for r in rows)
    if worst_iso > 1e-6:
        problems.append(f"isotropy error {worst_iso:.3e} px (organism stretched)")

    # 4. the organism must not clip at any size the harness visited
    clipped = [r for r in rows if r["clips"]]
    if clipped:
        problems.append(f"{len(clipped)} events clipped the organism")

    # 5. all three responsive states must have been exercised
    states = {r["state"] for r in rows}
    missing = {"COMPACT", "INSTRUMENT", "ARCHIVE"} - states
    if missing:
        problems.append(f"never entered states: {sorted(missing)}")

    sizes = {(r["w"], r["h"]) for r in rows}
    print("\n=== PROBE ANALYSIS ===")
    print(f"  events          {len(rows)}")
    print(f"  distinct sizes  {len(sizes)}")
    print(f"  resize count    {rows[-1]['resizes'] if rows else 0}")
    print(f"  states seen     {sorted(states)}")
    print(f"  sim clock       {rows[0]['sim_time']:.2f}s -> {rows[-1]['sim_time']:.2f}s"
          f"   (monotonic: {'YES' if not resets else 'NO'})")
    print(f"  worst isotropy  {worst_iso:.3e} px")
    print(f"  rebuilds        {max(r['rebuilds'] for r in rows)}")
    fps = [r["fps"] for r in rows if r["fps"] > 0]
    if fps:
        print(f"  fps at events   min {min(fps):.1f}  med "
              f"{sorted(fps)[len(fps)//2]:.1f}  max {max(fps):.1f}")
    return (not problems), problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--workspace", type=int, default=9)
    ap.add_argument("--shots", action="store_true")
    ap.add_argument("--no-ghost", dest="ghost", action="store_false",
                    default=True,
                    help="do not open a second tiled window")
    opts = ap.parse_args()

    os.makedirs(SCRATCH, exist_ok=True)
    original_ws = ""
    try:
        original_ws = json.loads(hypr("-j", "activeworkspace"))["id"]
    except Exception:
        pass

    t = Torture(opts)
    ok = False
    try:
        dsp(f"hl.dsp.focus({{ workspace = '{opts.workspace}' }})")
        settle(0.5)
        print("LAUNCH")
        t.launch()
        if opts.ghost:
            t.spawn_ghost()
        for n in range(1, opts.cycles + 1):
            t.cycle(n)
        t.rapid_resize()
        # end where we started: a normal tile
        t.step("final-tile", d_float(False), shot=True)
        time.sleep(1.0)
        if not t.alive():
            raise RuntimeError("APP DIED at end of run")
        print(f"\n  app still alive after {len(t.steps)} transitions")
        ok = True
    except Exception as e:
        print(f"\n!!! TORTURE FAILED: {e}")
    finally:
        t.cleanup()
        if original_ws:
            hypr("dispatch", f"hl.dsp.focus({{ workspace = '{original_ws}' }})")

    passed, problems = analyse(t.probe) if os.path.exists(t.probe) else (False, ["no probe file"])
    print("\n=== GATE 0 ===")
    if ok and passed:
        print("  PASS — no crash, no reset, no stretch, no clip")
        return 0
    print("  FAIL")
    for p in problems:
        print(f"   - {p}")
    if not ok:
        print("   - run did not complete")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
