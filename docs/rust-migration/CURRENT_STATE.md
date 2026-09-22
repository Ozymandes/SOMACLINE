# CURRENT STATE — Abyssal Rust Port (handoff snapshot)

STATUS:
RUST CORE HEALTHY
VISUAL PARITY FAILED
CAPTURE HARNESS UNRELIABLE
OPTIMIZATION PAUSED
NEXT OWNER: OPUS

## Pointers

- Latest relevant commit: `b6e0e14` — "WIP: Rust host parity handoff before Opus repair"
  (parent `d84c709` — console machine port, 35/35 gates green)
- Rust run: `./run-rust.sh` (or `cd rust && cargo build --release && ./target/release/abyssal`)
- Python run (GOLDEN REFERENCE): `./run-python.sh`
- Key QA:
  - `cd rust && cargo test` — 35/35 parity gates (telemetry bit-exact, renderer bit-exact
    paint, chrome 12/12, console 10/10)
  - `python3 qa/offscreen.py /tmp/py.png --width 900 --height 700` — Python headless frame
  - `cargo run --release --example offscreen -- /tmp/rs.png --width 900 --height 700` — Rust twin
  - `python3 qa/perf_live.py` — live resource harness (Python; reuse for Rust later)
- Key docs:
  - `docs/rust-migration/OPUS_VISUAL_PARITY_HANDOFF.md` — the full handoff (READ FIRST)
  - `docs/rust-migration/CURRENT_STATE.md` — this file
  - `docs/rust-migration/framework-floor.md` — GTK PSS floor (~100 MB; 20 MB target impossible with GTK)
  - `docs/rust-migration/phase-a/parity-spec.md` — acceptance contract (G-R1..G-R45)
  - `docs/rust-migration/PORT-CHROME.md`, `PORT-CONSOLE.md` — lane port notes
- Known blockers:
  1. Live Rust UI visually malformed vs Python (six-module composition broken under
     compositor; headless ds=1.0 is near pixel-perfect — suspect device-scale convention
     mismatch; see handoff §C)
  2. Screenshot harness unreliable (Hyprland refused exact resize; grim captured desktop
     region; rebuild per handoff §H — prefer offscreen diffing)
  3. `rust/src/app.rs` contains DBG instrumentation to strip (handoff Appendix)
