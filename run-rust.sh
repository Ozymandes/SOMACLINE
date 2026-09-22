#!/usr/bin/env bash
# Abyssal Organism Monitor - native Rust launcher (development).
# The proven Python implementation stays available via run-python.sh.
set -e
cd "$(dirname "$(readlink -f "$0")")"
BIN="rust/target/release/abyssal"
if [ ! -x "$BIN" ] || [ -n "$(find rust/src -newer "$BIN" -name '*.rs' 2>/dev/null | head -1)" ]; then
  (cd rust && cargo build --release)
fi
exec "$BIN" "$@"
