#!/usr/bin/env bash
# Abyssal Organism Monitor - native Rust launcher, winit + softbuffer host.
#
# This host links NO GTK: the crate's gtk4/gdk4/gio dependencies are optional
# and off here, so nothing of the GTK stack is mapped. run-rust.sh (GTK) stays
# the reference implementation and the fallback until this one is accepted.
set -e
cd "$(dirname "$(readlink -f "$0")")"
BIN="rust/target/release/abyssal-winit"
if [ ! -x "$BIN" ] || [ -n "$(find rust/src -newer "$BIN" -name '*.rs' 2>/dev/null | head -1)" ]; then
  (cd rust && cargo build --release --no-default-features --features winit-host)
fi
exec "$BIN" "$@"
