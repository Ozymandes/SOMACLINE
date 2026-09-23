#!/usr/bin/env bash
# SOMACLINE - computational morphology instrument. Development launcher.
#
# THIS IS THE RELEASE BUILD. It links no GTK: the crate's gtk4/gdk4/gio
# dependencies are optional and off here, so nothing of the GTK stack is
# mapped. run-rust.sh (GTK) is kept as a development reference and parity
# oracle, not as a fallback.
#
# This runs in place, from the checkout, and rebuilds on demand. To install
# the application properly - binary, assets, desktop entry, icon - use
# ./install.sh instead.
set -e
cd "$(dirname "$(readlink -f "$0")")"
BIN="rust/target/release/somacline"
if [ ! -x "$BIN" ] || [ -n "$(find rust/src -newer "$BIN" -name '*.rs' 2>/dev/null | head -1)" ]; then
  (cd rust && cargo build --release --no-default-features --features winit-host)
fi
exec "$BIN" "$@"
