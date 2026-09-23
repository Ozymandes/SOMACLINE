#!/usr/bin/env python3
"""RELEASE GATE: residency under sustained real use.

    python3 qa/release_soak.py rust/target/release/somacline [rounds]

Each round drives the real window through every specimen, the cycle and mode
keys, a fullscreen round trip, three floating sizes and a retile, then waits
past SETTLE_S so the sprite residency review runs. It reports PSS/RSS after
each round, the open file descriptors, the thread count and whether anything
reached stdout or stderr.

Read the memory numbers against the WINDOW SIZE, not against each other:
surfaces scale with window area, so a round that ends tiled larger legitimately
costs more. What this gate is looking for is growth that does not come back.
"""
import json, os, subprocess, sys, time
def hypr(*a): return subprocess.run(["hyprctl",*a],capture_output=True,text=True).stdout.strip()
def dsp(c): hypr("dispatch",c)
def app():
    for c in json.loads(hypr("-j","clients")):
        if "omacline" in (c.get("class") or ""): return c
def active(): return json.loads(hypr("-j","activewindow")).get("address")
def mem(pid):
    o={}
    for l in open(f"/proc/{pid}/smaps_rollup"):
        k,_,v=l.partition(":")
        if k in ("Rss","Pss","Anonymous"): o[k]=round(int(v.split()[0])/1024.0,1)
    return o
def fds(pid):
    d=f"/proc/{pid}/fd"
    out=[]
    for f in os.listdir(d):
        try: out.append(os.readlink(os.path.join(d,f)))
        except OSError: pass
    return out

binary = sys.argv[1]; rounds = int(sys.argv[2]) if len(sys.argv)>2 else 6
subprocess.run(["pkill","-x","somacline"]); time.sleep(1)
p = subprocess.Popen([binary,"--quit-after","400"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
w=None; t0=time.monotonic()
while w is None and time.monotonic()-t0 < 20:
    time.sleep(0.25); w=app()
addr=w["address"]; pid=w["pid"]
for _ in range(15):
    dsp(f'hl.dsp.focus{{window="address:{addr}"}}'); time.sleep(0.3)
    if active()==addr: break
print(f"  pid {pid}  start {mem(pid)}")
hist=[]
for r in range(rounds):
    # specimens
    for k in "123456":
        subprocess.run(["wtype",k]); time.sleep(0.25)
    # cycle + mode
    for k in ["n","p","m"]:
        subprocess.run(["wtype",k]); time.sleep(0.25)
    # fullscreen round trip
    dsp(f'hl.dsp.window.fullscreen{{window="address:{addr}"}}'); time.sleep(1.6)
    dsp(f'hl.dsp.window.fullscreen{{window="address:{addr}"}}'); time.sleep(1.6)
    # float + two sizes + back
    dsp(f'hl.dsp.window.float{{window="address:{addr}", enable=true}}'); time.sleep(0.6)
    for (ww,hh) in [(600,520),(1200,800),(900,700)]:
        dsp(f'hl.dsp.window.resize{{window="address:{addr}", x={ww}, y={hh}, exact=true}}'); time.sleep(0.9)
    dsp(f'hl.dsp.window.float{{window="address:{addr}", enable=false}}'); time.sleep(0.8)
    for _ in range(10):
        dsp(f'hl.dsp.focus{{window="address:{addr}"}}'); time.sleep(0.25)
        if active()==addr: break
    time.sleep(6.0)   # let settle fire
    m=mem(pid); hist.append(m)
    print(f"  round {r+1}: {m}  alive={p.poll() is None}")
extra=[f for f in fds(pid) if f.startswith("/tmp") or f.endswith("(deleted)")]
print(f"  open fds: {len(fds(pid))}, suspicious: {extra}")
print(f"  threads: {open(f'/proc/{pid}/status').read().split('Threads:')[1].split()[0]}")
p.terminate()
try: p.wait(5)
except Exception: p.kill()
out,err=p.communicate()
print(f"  stdout {len(out)} bytes, stderr {len(err)} bytes: {err[:300]!r}")
first,last=hist[1]["Pss"],hist[-1]["Pss"]
print(f"  PSS round2 {first} -> round{len(hist)} {last}  delta {last-first:+.1f} MB")
print("VERDICT:", "GROWS" if last-first > 8 else "stable")
