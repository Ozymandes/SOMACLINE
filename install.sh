#!/usr/bin/env bash
# Abyssal Organism Monitor - install / uninstall.
#
#   ./install.sh                      install into ~/.local
#   ./install.sh --prefix /usr/local  install system-wide (needs write access)
#   ./install.sh --uninstall          remove what was installed
#   DESTDIR=/tmp/stage ./install.sh   stage into a package root
#
# Installs the winit + softbuffer build. The GTK build is a development
# reference and is deliberately not installed.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
DESTDIR="${DESTDIR:-}"
ACTION=install
SKIP_BUILD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)    PREFIX="$2"; shift 2 ;;
    --prefix=*)  PREFIX="${1#*=}"; shift ;;
    --uninstall) ACTION=uninstall; shift ;;
    --no-build)  SKIP_BUILD=1; shift ;;
    -h|--help)   sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPID=dev.abyssal.OrganismMonitor
BIN=abyssal-monitor

D_BIN="$DESTDIR$PREFIX/bin"
D_SHARE="$DESTDIR$PREFIX/share/abyssal"
D_DESKTOP="$DESTDIR$PREFIX/share/applications"
D_ICON="$DESTDIR$PREFIX/share/icons/hicolor/scalable/apps"
D_DOC="$DESTDIR$PREFIX/share/doc/abyssal"

if [ "$ACTION" = uninstall ]; then
  rm -f  "$D_BIN/$BIN"
  rm -f  "$D_DESKTOP/$APPID.desktop"
  rm -f  "$D_ICON/$APPID.svg"
  rm -rf "$D_SHARE" "$D_DOC"
  # The display faces are copied into the user's font directory at first run,
  # not by this script, so they are only ours to remove for a real uninstall.
  [ -z "$DESTDIR" ] && rm -rf "$HOME/.local/share/fonts/abyssal"
  command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database -q "$DESTDIR$PREFIX/share/applications" 2>/dev/null || true
  [ -z "$DESTDIR" ] && command -v fc-cache >/dev/null 2>&1 &&
    fc-cache -f >/dev/null 2>&1 || true
  echo "abyssal: removed from $PREFIX"
  exit 0
fi

if [ "$SKIP_BUILD" = 0 ]; then
  echo "abyssal: building the release binary (winit + softbuffer)..."
  ( cd "$ROOT/rust" && cargo build --release --no-default-features --features winit-host )
fi

SRC_BIN="$ROOT/rust/target/release/abyssal-winit"
[ -x "$SRC_BIN" ] || { echo "abyssal: $SRC_BIN is missing; build first" >&2; exit 1; }

install -Dm755 "$SRC_BIN"                                   "$D_BIN/$BIN"
install -Dm644 "$ROOT/packaging/$APPID.desktop"             "$D_DESKTOP/$APPID.desktop"
install -Dm644 "$ROOT/packaging/$APPID.svg"                 "$D_ICON/$APPID.svg"
install -Dm644 "$ROOT/README.md"                            "$D_DOC/README.md"
install -Dm644 "$ROOT/CHANGELOG.md"                         "$D_DOC/CHANGELOG.md"

# Runtime assets, and ONLY the runtime assets: the sprite set the console is
# manufactured from and the two display faces. Everything else under assets/
# is design source - see docs/ASSETS.md.
mkdir -p "$D_SHARE/assets"
cp -r "$ROOT/assets/sprites" "$D_SHARE/assets/"
cp -r "$ROOT/assets/fonts"   "$D_SHARE/assets/"
find "$D_SHARE/assets" -type f -exec chmod 644 {} +
find "$D_SHARE/assets" -type d -exec chmod 755 {} +

if [ -z "$DESTDIR" ]; then
  command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 &&
    gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" 2>/dev/null || true
fi

echo "abyssal: installed"
echo "  binary   $PREFIX/bin/$BIN"
echo "  assets   $PREFIX/share/abyssal/assets  ($(du -sh "$D_SHARE/assets" | cut -f1))"
echo "  desktop  $PREFIX/share/applications/$APPID.desktop"
echo "  icon     $PREFIX/share/icons/hicolor/scalable/apps/$APPID.svg"
case ":$PATH:" in
  *":$PREFIX/bin:"*) echo "  run      $BIN" ;;
  *) echo "  run      $PREFIX/bin/$BIN   (add $PREFIX/bin to PATH for the short form)" ;;
esac
