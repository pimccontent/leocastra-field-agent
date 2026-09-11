#!/bin/sh
set -eu

CONFIG_DIR="${CONFIG_DIR:-/var/lib/leocastra}"
UPLINKS_FILE="${UPLINKS_FILE:-$CONFIG_DIR/uplinks}"
SRTLA_SEND_BIN="${SRTLA_SEND_BIN:-srtla_send}"
METRICS_BIND="${METRICS_BIND:-127.0.0.1:9099}"
STATUS_PORT="${STATUS_PORT:-8088}"
STATUS_SERVER="${STATUS_SERVER:-/usr/local/lib/leocastra-field-agent-status.py}"
export CONFIG_DIR UPLINKS_FILE METRICS_BIND STATUS_PORT

mkdir -p "$CONFIG_DIR" "$(dirname "$UPLINKS_FILE")"
if [ ! -s "$UPLINKS_FILE" ]; then
  printf '%s\n' "AUTO" > "$UPLINKS_FILE"
fi

resolve_uplinks() {
  if grep -v '^[[:space:]]*#' "$UPLINKS_FILE" | grep -q '^AUTO[[:space:]]*$'; then
    resolved="$CONFIG_DIR/uplinks.resolved"
    ip -4 -o addr show scope global 2>/dev/null \
      | awk '{print $4}' \
      | cut -d/ -f1 \
      | grep -v '^127\.' \
      > "$resolved" || true
    if [ ! -s "$resolved" ]; then
      echo "AUTO uplinks requested but no global IPv4 addresses were found." >&2
      return 1
    fi
    UPLINKS_FILE="$resolved"
    export UPLINKS_FILE
  fi
}

load_runtime() {
  eval "$(python3 - "$CONFIG_DIR" <<'PY'
import json, os, shlex, sys
from pathlib import Path
d = Path(sys.argv[1])
cfg = {}
p = d / "config.json"
if p.exists():
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
host = str(cfg.get("leocastraHost") or os.environ.get("LEOCASTRA_HOST") or "").strip()
bonded = str(cfg.get("bondedPort") or os.environ.get("BONDED_PORT") or "10180")
listen = str(cfg.get("listenPort") or os.environ.get("SRT_LISTEN_PORT") or "4001")
studio = str(cfg.get("studioUrl") or os.environ.get("STUDIO_URL") or "")
mode = "classic" if cfg.get("schedulerMode") == "classic" else "enhanced"
try:
    latency = int(cfg.get("latencyMs") or os.environ.get("LATENCY_MS") or 4000)
except Exception:
    latency = 4000
timeout = max(8000, min(60000, latency * 2))
print("LEOCASTRA_HOST=" + shlex.quote(host))
print("BONDED_PORT=" + shlex.quote(bonded))
print("SRT_LISTEN_PORT=" + shlex.quote(listen))
print("STUDIO_URL=" + shlex.quote(studio))
print("SRTLA_MODE=" + shlex.quote(mode))
print("CONN_TIMEOUT_MS=" + shlex.quote(str(timeout)))
print("LATENCY_MS=" + shlex.quote(str(latency)))
PY
)"
}

SEND_PID=""
if [ -f "$STATUS_SERVER" ] && [ -n "$STATUS_PORT" ] && [ "$STATUS_PORT" != "0" ]; then
  python3 "$STATUS_SERVER" >/tmp/leocastra-agent-status.log 2>&1 &
  STATUS_PID=$!
  trap 'kill "$STATUS_PID" 2>/dev/null || true; kill "$SEND_PID" 2>/dev/null || true' EXIT INT TERM
fi

while true; do
  load_runtime
  export LEOCASTRA_HOST BONDED_PORT SRT_LISTEN_PORT STUDIO_URL LATENCY_MS
  if [ -z "${LEOCASTRA_HOST:-}" ]; then
    echo "Waiting for ingest host in Settings???" >&2
    sleep 3
    continue
  fi
  UPLINKS_FILE="${CONFIG_DIR}/uplinks"
  export UPLINKS_FILE
  if ! resolve_uplinks; then
    sleep 3
    continue
  fi
  rm -f "$CONFIG_DIR/restart.flag"
  "$SRTLA_SEND_BIN" \
    --metrics-bind "$METRICS_BIND" \
    --mode "${SRTLA_MODE:-enhanced}" \
    --conn-timeout-ms "${CONN_TIMEOUT_MS:-15000}" \
    "$SRT_LISTEN_PORT" "$LEOCASTRA_HOST" "$BONDED_PORT" "$UPLINKS_FILE" &
  SEND_PID=$!
  echo "$SEND_PID" > "$CONFIG_DIR/srtla.pid"
  while kill -0 "$SEND_PID" 2>/dev/null; do
    if [ -f "$CONFIG_DIR/restart.flag" ]; then
      rm -f "$CONFIG_DIR/restart.flag"
      kill "$SEND_PID" 2>/dev/null || true
      wait "$SEND_PID" 2>/dev/null || true
      break
    fi
    sleep 1
  done
  wait "$SEND_PID" 2>/dev/null || true
  echo "Bond sender exited; restarting in 2s" >&2
  sleep 2
done
