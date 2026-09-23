#!/usr/bin/env python3
"""One controlled perf sample per host, in an IDENTICAL floating window.

    python3 qa/perf_hosts.py run-rust.sh       gtk   --dwell 8
    python3 qa/perf_hosts.py run-rust-winit.sh winit --dwell 8

Four window states are measured: focused, unfocused-but-visible, covered by a
fullscreen window (TRUE occlusion), and moved to a workspace that is not on
screen. It also records RSS/PSS, thread count, startup to mapped window, and
the app's own probe (FPS, draw/sim ms, rebuilds). Focus is restored at the end.

Hyprland 0.56.2 notes, learned the hard way:
  - `hl.dsp.workspace.change_id` RENAMES a workspace; it does NOT switch to
    one. Hiding a window with it silently does nothing and the "hidden" figure
    comes out equal to the unfocused one. This script moves the WINDOW instead
    and asserts `offworkspace_really_hidden` before trusting the number.
  - after a resize, let Hyprland's resize animation settle before measuring or
    screenshotting, or the reported rect and the real surface disagree.
"""
import json, os, subprocess, sys, time

ROOT = "/home/seeno/abyssal-organism-monitor"
TICK = os.sysconf("SC_CLK_TCK")
SCRATCH_WS = 4   # must already exist: change_id will not create one

def hypr(*a):
    return subprocess.run(["hyprctl", *a], capture_output=True, text=True).stdout.strip()

def dispatch(code):
    return hypr("dispatch", code)

def clients():
    return json.loads(hypr("-j", "clients"))

def find():
    for c in clients():
        if "omacline" in (c.get("class") or ""):
            return c
    return None

def cpu_ticks(pid):
    f = open(f"/proc/{pid}/stat").read()
    fields = f[f.rindex(")") + 2:].split()
    return int(fields[11]) + int(fields[12])      # utime + stime

def mem(pid):
    out = {}
    for line in open(f"/proc/{pid}/smaps_rollup"):
        k, _, v = line.partition(":")
        if k in ("Rss", "Pss", "Anonymous"):
            out[k] = int(v.split()[0]) / 1024.0
    return out

def threads(pid):
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("Threads:"):
            return int(line.split()[1])
    return 0

def measure(pid, dwell):
    t0, c0 = time.monotonic(), cpu_ticks(pid)
    time.sleep(dwell)
    t1, c1 = time.monotonic(), cpu_ticks(pid)
    return (c1 - c0) / TICK / (t1 - t0) * 100.0

def main():
    launcher, label = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    w = int(args[args.index("--w") + 1]) if "--w" in args else 781
    h = int(args[args.index("--h") + 1]) if "--h" in args else 468
    dwell = float(args[args.index("--dwell") + 1]) if "--dwell" in args else 6.0

    probe = f"/tmp/perf-{label}.jsonl"
    if os.path.exists(probe):
        os.remove(probe)
    home_ws = json.loads(hypr("-j", "activeworkspace"))["id"]
    others = [c["address"] for c in clients()
              if c["workspace"]["id"] == home_ws and "omacline" not in (c.get("class") or "")]

    t_start = time.monotonic()
    p = subprocess.Popen([os.path.join(ROOT, launcher), "--probe", probe,
                          "--quit-after", str(dwell * 4 + 40)],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    win = None
    while win is None and time.monotonic() - t_start < 25:
        time.sleep(0.25)
        win = find()
    if win is None:
        print(f"{label}: window never appeared"); p.kill(); return 1
    startup = time.monotonic() - t_start        # spawn -> mapped window
    addr = win["address"]
    pid = win["pid"]

    dispatch(f'hl.dsp.window.float{{window="address:{addr}", enable=true}}')
    time.sleep(0.4)
    dispatch(f'hl.dsp.window.resize{{window="address:{addr}", x={w}, y={h}, exact=true}}')
    time.sleep(0.4)
    dispatch(f'hl.dsp.focus{{window="address:{addr}"}}')
    time.sleep(1.5)
    got = find()
    geo = (got["size"], got["at"])

    res = {"label": label, "size": got["size"], "startup_s": round(startup, 2),
           "pid": pid}
    res["cpu_focused"] = measure(pid, dwell)
    res["mem"] = mem(pid)
    res["threads"] = threads(pid)

    if others:
        dispatch(f'hl.dsp.focus{{window="address:{others[0]}"}}')
        time.sleep(1.5)
        res["cpu_unfocused"] = measure(pid, dwell)
        # TRUE occlusion: another window fullscreened on top of ours
        dispatch(f'hl.dsp.window.fullscreen{{window="address:{others[0]}"}}')
        time.sleep(1.5)
        res["cpu_covered"] = measure(pid, dwell)
        dispatch(f'hl.dsp.window.fullscreen{{window="address:{others[0]}"}}')
        time.sleep(1.0)
    else:
        res["cpu_unfocused"] = None
        res["cpu_covered"] = None

    # Genuinely hidden: move the window to a workspace that is not on screen.
    # (`workspace.change_id` RENAMES a workspace; it does not switch to one.)
    away = next(w["id"] for w in json.loads(hypr("-j", "workspaces"))
                if w["id"] != home_ws)
    dispatch(f'hl.dsp.window.move{{window="address:{addr}", workspace="{away}"}}')
    time.sleep(1.0)
    if others:
        dispatch(f'hl.dsp.focus{{window="address:{others[0]}"}}')
    time.sleep(2.0)
    res["offworkspace_really_hidden"] = (
        find()["workspace"]["id"] != json.loads(hypr("-j", "activeworkspace"))["id"])
    res["cpu_offworkspace"] = measure(pid, dwell)
    res["mem_hidden"] = mem(pid)
    dispatch(f'hl.dsp.window.move{{window="address:{addr}", workspace="{home_ws}"}}')
    time.sleep(1.0)

    # the app's own probe: the last few 2 s samples taken while focused
    samples = []
    if os.path.exists(probe):
        for line in open(probe):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("event") == "sample":
                samples.append(r)
    if samples:
        focused = samples[: max(1, int(dwell // 2))]
        n = len(focused)
        res["fps"] = sum(s["fps"] for s in focused) / n
        res["draw_ms"] = sum(s["draw_avg_ms"] for s in focused) / n
        res["sim_ms"] = sum(s["sim_ms"] for s in focused) / n
        res["rebuilds"] = focused[-1]["rebuilds"]
        res["state"] = focused[-1]["state"]
    p.kill()
    p.wait()
    if others:
        dispatch(f'hl.dsp.focus{{window="address:{others[0]}"}}')
    print(json.dumps(res))
    return 0

sys.exit(main())
