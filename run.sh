#!/usr/bin/env bash
# Abyssal Organism Monitor launcher.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec python3 -m abyssal.app "$@"
