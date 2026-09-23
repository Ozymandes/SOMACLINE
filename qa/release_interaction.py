#!/usr/bin/env python3
"""RELEASE GATE: every documented control must change what the window SHOWS.

    python3 qa/release_interaction.py rust/target/release/abyssal-winit

Asserts on pixels, not on intent: each control is driven against the real
window and the result is read back off the screen.

Two things about this environment, learned the hard way:

  - `wtype` drops roughly two injected key presses in five on Hyprland 0.56.
    Every press is therefore retried until the window is observed to react,
    and a control that never reacts in N tries fails. The counts printed
    ("after 3 press(es)") are the injector's flakiness, not the app's.
  - Comparison regions must avoid anything that animates. The glass, the
    readouts and the graphs all move on their own, so a whole-frame diff is
    meaningless. The regions below were measured: the selector-bank row and
    the band under the footer show ZERO change with no input.
"""
import json, os, subprocess, sys, time
import numpy as np
from PIL import Image
S = os.environ.get("ABYSSAL_QA_OUT", "/tmp/abyssal-qa")
os.makedirs(S, exist_ok=True)
def hypr(*a): return subprocess.run(["hyprctl",*a],capture_output=True,text=True).stdout.strip()
def dsp(c): hypr("dispatch",c)
def app():
    for c in json.loads(hypr("-j","clients")):
        if "byssal" in (c.get("class") or ""): return c
def active(): return json.loads(hypr("-j","activewindow")).get("address")

BIN = sys.argv[1]
subprocess.run(["pkill","-x","abyssal-winit"]); time.sleep(1)
p = subprocess.Popen([BIN,"--quit-after","300","--width","900","--height","700"],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
w=None; t0=time.monotonic()
while w is None and time.monotonic()-t0 < 20: time.sleep(0.25); w=app()
assert w, "no window"
addr=w["address"]
WX, WY = 300, 150          # fully on-screen: a truncated grab is not a frame
dsp(f'hl.dsp.window.float{{window="address:{addr}", enable=true}}'); time.sleep(0.8)
for _ in range(8):
    dsp(f'hl.dsp.window.resize{{window="address:{addr}", x=900, y=700, exact=true}}'); time.sleep(0.7)
    dsp(f'hl.dsp.window.move{{window="address:{addr}", x={WX}, y={WY}}}'); time.sleep(0.7)
    g = app()
    if g["size"] == [900,700] and g["at"] == [WX,WY]: break
else:
    raise SystemExit(f"FAIL: could not place the window: {app()['at']} {app()['size']}")
def refocus():
    for _ in range(20):
        if active()==addr: return
        dsp(f'hl.dsp.focus{{window="address:{addr}"}}'); time.sleep(0.3)
    raise SystemExit("FAIL: lost focus and could not recover")
refocus(); time.sleep(2.0)

def shot(tag=""):
    g=app(); r=f"{g['at'][0]},{g['at'][1]} {g['size'][0]}x{g['size'][1]}"
    subprocess.run(["grim","-g",r,f"{S}/K-{tag}.png"])
    return np.asarray(Image.open(f"{S}/K-{tag}.png").convert("RGB")).astype(np.int16)

TITLE = (slice(110,200), slice(150,900))       # "SPECIMEN <name>" engraving
MODE  = (slice(880,940), slice(705,755))       # the mode key, right of the bank
OVER  = (slice(985,1100), slice(40,1400))      # below every live element:
                                               # measured zero drift with no input
def title(i): return i[TITLE]

def send(k, named=False):
    refocus()
    subprocess.run(["wtype"] + (["-k",k] if named else [k]))
    time.sleep(0.9)

def press_until(k, ref, region=TITLE, named=False, tries=8, thresh=40):
    """Press until the window reacts. Returns (reacted, image, attempts)."""
    for i in range(1, tries+1):
        send(k, named)
        img = shot(f"{k}-{i}")
        if int(np.abs(img[region]-ref[region]).max()) > thresh:
            return True, img, i
    return False, shot(f"{k}-fail"), tries

results=[]
def check(name, ok, detail=""):
    results.append((name,ok,detail)); print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")

# ---- digits select distinct specimens -----------------------------------
titles=[]
for d in "12345":
    for _ in range(3):      # selection is absolute, so repeats are harmless
        send(d)
    titles.append(title(shot(f"sp{d}")))
mins = min(int(np.abs(a-b).max()) for i,a in enumerate(titles) for b in titles[i+1:])
check("digits 1-5 select five DISTINCT specimens", mins > 40,
      f"min pairwise title diff {mins}")

# ---- n / p ---------------------------------------------------------------
send("1"); send("1"); t1 = shot("c0")
ok, tn, n = press_until("n", t1)
check("n advances the specimen", ok, f"after {n} press(es)")
ok, tp, n = press_until("p", tn)
check("p steps back", ok and int(np.abs(title(tp)-title(t1)).max()) < 12,
      f"after {n} press(es); returned to specimen 1")

# ---- arrows --------------------------------------------------------------
ok, tr, n = press_until("Right", tp, named=True)
check("Right advances the specimen", ok, f"after {n} press(es)")
ok, tl, n = press_until("Left", tr, named=True)
check("Left steps back", ok and int(np.abs(title(tl)-title(t1)).max()) < 12,
      f"after {n} press(es); returned to specimen 1")

# ---- m cycles the mode key ----------------------------------------------
b0 = shot("m0")
ok, b1, n = press_until("m", b0, region=MODE, thresh=30)
check("m cycles the mode key", ok, f"after {n} press(es)")

# ---- F1 / F2 overlays ----------------------------------------------------
px = lambda a, b, r: int(((np.abs(a[r]-b[r]).max(axis=2)) > 30).sum())
f0 = shot("f0")
# The overlay band has ZERO live drift (measured), so its state is unambiguous.
seq = []
for i in range(12):
    send("F1", named=True)
    seq.append(px(shot(f"f1-{i}"), f0, OVER) > 1000)
    if seq.count(True) and seq.count(False) and not seq[-1]:
        break
check("F1 toggles the diagnostic overlay on and off",
      any(seq) and not seq[-1],
      f"observed {''.join('1' if x else '0' for x in seq)} over {len(seq)} presses "
      f"({sum(seq)} landed on)")

f0 = shot("f0b")
# The calibration circles reach x 215-266; the organism's own animation never
# starts before x 267 (measured). This strip is touched by F2 and by nothing
# else that moves.
GLASS = (slice(320,900), slice(212,264))
seq = []
for i in range(12):
    send("F2", named=True)
    seq.append(px(shot(f"f2-{i}"), f0, GLASS) > 60)
    if seq.count(True) and seq.count(False) and not seq[-1]:
        break
check("F2 toggles calibration geometry on and off",
      any(seq) and not seq[-1],
      f"observed {''.join('1' if x else '0' for x in seq)} over {len(seq)} presses")

# ---- pointer: click an engraved selector key ----------------------------
send("1"); send("1"); before = shot("h0")
g = app(); ax, ay = g['at']
hit = None
# engraved key centres, measured off a placed 900x700 window (logical px)
for lx, label in [(256,"key 03"), (191,"key 02"), (322,"key 04")]:
    dsp(f'hl.dsp.cursor.move{{x={ax+lx}, y={ay+563}}}'); time.sleep(0.7)
    subprocess.run(["wlrctl","pointer","click","left"]); time.sleep(1.1)
    after = shot("h1-"+label.replace(" ",""))
    if int(np.abs(title(after)-title(before)).max()) > 40:
        hit = label; break
check("clicking an engraved selector key selects that specimen", hit is not None,
      f"{hit or 'no key responded'}")

# ---- F11 fullscreen ------------------------------------------------------
send("F11", named=True); time.sleep(2.2)
fs = app()
if fs["size"][0] < 1600:
    send("F11", named=True); time.sleep(2.2); fs = app()
check("F11 enters fullscreen", fs["size"][0] >= 1600, f"size {fs['size']}")
send("F11", named=True); time.sleep(2.2)
back = app()
if back and back["size"][0] >= 1600:
    send("F11", named=True); time.sleep(2.2); back = app()
check("F11 leaves fullscreen", back["size"] == [900,700], f"size {back['size']}")

# ---- q quits -------------------------------------------------------------
for _ in range(8):
    send("q")
    if p.poll() is not None: break
    time.sleep(0.6)
rc = p.poll()
check("q quits", rc == 0, f"exit code {rc}")
out, err = p.communicate()
check("silent on stdout and stderr", len(out)==0 and len(err)==0,
      f"{len(out)} / {len(err)} bytes {err[:200]!r}")

bad=[r for r in results if not r[1]]
print(f"\n  {len(results)-len(bad)}/{len(results)} interaction checks passed")
sys.exit(1 if bad else 0)
