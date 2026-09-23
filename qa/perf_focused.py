#!/usr/bin/env python3
"""One controlled steady-state performance sample, focused.

    python3 qa/perf_focused.py run-rust-winit.sh winit --dwell 10 [--all]

Why this exists alongside `qa/perf_hosts.py`: that harness can report a
FOCUSED figure measured on an UNFOCUSED window. Its `hl.dsp.focus` fails
silently often enough to matter, the app then runs its 30 FPS unfocused
cadence for the whole dwell, and the number comes out at roughly 13 % instead
of roughly 24 %. This one asserts `activewindow == addr` before AND after the
dwell and rejects any sample whose FPS is not the focused cadence, retrying up
to four times.

CPU is utime+stime over the dwell; FPS and draw time come from the app's own
probe over the SAME window. `--all` adds unfocused, covered and off-workspace.
"""
import json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICK = os.sysconf("SC_CLK_TCK")

def hypr(*a):
    return subprocess.run(["hyprctl", *a], capture_output=True, text=True).stdout.strip()
def dispatch(c): return hypr("dispatch", c)
def clients(): return json.loads(hypr("-j", "clients"))
def find():
    for c in clients():
        if "byssal" in (c.get("class") or ""): return c
    return None
def cpu_ticks(pid):
    f = open(f"/proc/{pid}/stat").read()
    fl = f[f.rindex(")") + 2:].split()
    return int(fl[11]) + int(fl[12])
def mem(pid):
    out = {}
    for line in open(f"/proc/{pid}/smaps_rollup"):
        k, _, v = line.partition(":")
        if k in ("Rss", "Pss", "Anonymous"): out[k] = round(int(v.split()[0]) / 1024.0, 1)
    return out
def threads(pid):
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("Threads:"): return int(line.split()[1])
    return 0

def main():
    launcher, label = sys.argv[1], sys.argv[2]
    a = sys.argv[3:]
    g = lambda k, d: (type(d))(a[a.index(k)+1]) if k in a else d
    w, h, dwell, warm = g("--w",781), g("--h",468), g("--dwell",10.0), g("--warm",4.0)
    probe = os.path.join(os.environ.get("ABYSSAL_QA_OUT", "/tmp"), f"abyssal-perf-{label}.jsonl")
    os.makedirs(os.path.dirname(probe), exist_ok=True)
    if os.path.exists(probe): os.remove(probe)
    home_ws = json.loads(hypr("-j", "activeworkspace"))["id"]
    others = [c["address"] for c in clients()
              if c["workspace"]["id"] == home_ws and "byssal" not in (c.get("class") or "")]
    t0 = time.monotonic()
    p = subprocess.Popen([os.path.join(ROOT, launcher), "--probe", probe,
                          "--quit-after", str(dwell + warm + 40)],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    win = None
    while win is None and time.monotonic() - t0 < 25:
        time.sleep(0.2); win = find()
    if win is None: print("no window"); p.kill(); return 1
    startup = round(time.monotonic() - t0, 3)
    addr, pid = win["address"], win["pid"]
    dispatch(f'hl.dsp.window.float{{window="address:{addr}", enable=true}}'); time.sleep(0.4)
    dispatch(f'hl.dsp.window.resize{{window="address:{addr}", x={w}, y={h}, exact=true}}'); time.sleep(0.5)
    for _ in range(12):
        dispatch(f'hl.dsp.focus{{window="address:{addr}"}}')
        time.sleep(0.35)
        if json.loads(hypr("-j","activewindow")).get("address") == addr:
            break
    else:
        print("WARN: never focused", file=sys.stderr)
    time.sleep(warm)
    got = find()
    def frames():
        n = None
        for line in open(probe):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("event") == "sample": n = r
        return n
    for attempt in range(4):
        for _ in range(12):
            dispatch(f'hl.dsp.focus{{window="address:{addr}"}}')
            time.sleep(0.3)
            if json.loads(hypr("-j","activewindow")).get("address") == addr:
                break
        time.sleep(1.0)
        s0 = frames(); c0 = cpu_ticks(pid); t_a = time.monotonic()
        time.sleep(dwell)
        s1 = frames(); c1 = cpu_ticks(pid); t_b = time.monotonic()
        still = json.loads(hypr("-j","activewindow")).get("address") == addr
        fps = (s1["frames"]-s0["frames"])/(s1["wall"]-s0["wall"]) if s0 and s1 else 0
        if still and fps > 55:
            break
        print(f"  retry {attempt}: focused={still} fps={fps:.1f}", file=sys.stderr)
    else:
        print("WARN: never got a clean focused sample", file=sys.stderr)
    res = {"label": label, "size": got["size"], "startup_s": startup,
           "cpu_focused": round((c1-c0)/TICK/(t_b-t_a)*100, 2),
           "fps": round((s1["frames"]-s0["frames"])/(s1["wall"]-s0["wall"]), 2) if s0 and s1 else None,
           "draw_ms": round(s1["draw_avg_ms"], 3) if s1 else None,
           "sim_ms": round(s1["sim_ms"], 3) if s1 else None,
           "rebuilds": s1["rebuilds"] if s1 else None,
           "state": s1["state"] if s1 else None,
           "mem": mem(pid), "threads": threads(pid)}
    if "--all" in a:
        if others:
            dispatch(f'hl.dsp.focus{{window="address:{others[0]}"}}'); time.sleep(1.5)
            c0=cpu_ticks(pid); ta=time.monotonic(); time.sleep(dwell)
            res["cpu_unfocused"]=round((cpu_ticks(pid)-c0)/TICK/(time.monotonic()-ta)*100,2)
            dispatch(f'hl.dsp.window.fullscreen{{window="address:{others[0]}"}}'); time.sleep(1.5)
            c0=cpu_ticks(pid); ta=time.monotonic(); time.sleep(dwell)
            res["cpu_covered"]=round((cpu_ticks(pid)-c0)/TICK/(time.monotonic()-ta)*100,2)
            dispatch(f'hl.dsp.window.fullscreen{{window="address:{others[0]}"}}'); time.sleep(1.0)
        away = next(x["id"] for x in json.loads(hypr("-j","workspaces")) if x["id"] != home_ws)
        dispatch(f'hl.dsp.window.move{{window="address:{addr}", workspace="{away}"}}'); time.sleep(1.0)
        if others: dispatch(f'hl.dsp.focus{{window="address:{others[0]}"}}')
        time.sleep(2.0)
        res["offworkspace_really_hidden"] = (find()["workspace"]["id"] !=
            json.loads(hypr("-j","activeworkspace"))["id"])
        c0=cpu_ticks(pid); ta=time.monotonic(); time.sleep(dwell)
        res["cpu_offworkspace"]=round((cpu_ticks(pid)-c0)/TICK/(time.monotonic()-ta)*100,2)
        res["mem_hidden"]=mem(pid)
        dispatch(f'hl.dsp.window.move{{window="address:{addr}", workspace="{home_ws}"}}'); time.sleep(0.8)
    p.kill(); p.wait()
    if others: dispatch(f'hl.dsp.focus{{window="address:{others[0]}"}}')
    print(json.dumps(res))
    return 0
sys.exit(main())
