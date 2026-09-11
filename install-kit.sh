#!/bin/sh
# Compatibility wrapper. Prefer: sudo ./install.sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
exec bash "$ROOT/install.sh" "$@"
