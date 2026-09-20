#!/bin/sh
set -eu

CONFIG_DIR="${CONFIG_DIR:-/var/lib/leocastra}"
UPLINKS_FILE="${UPLINKS_FILE:-$CONFIG_DIR/uplinks}"
SRTLA_SEND_BIN="${SRTLA_SEND_BIN:-srtla_send}"
METRICS_BIND="${METRICS_BIND:-127.0.0.1:9099}"
STATUS_PORT="${STATUS_PORT:-8088}"
STATUS_SERVER="${STATUS_SERVER:-/usr/local/lib/leocastra-field-agent-status.py}"
export CONFIG_DIR UPLINKS_FILE METRICS_BIND STATUS_PORT
export PYTHONPATH="/usr/local/lib${PYTHONPATH:+:$PYTHONPATH}"
export RUST_LOG="${RUST_LOG:-info}"

mkdir -p "$CONFIG_DIR" "$(dirname "$UPLINKS_FILE")"
if [ ! -s "$UPLINKS_FILE" ]; then
  printf '%s\n' "AUTO" > "$UPLINKS_FILE"
fi

is_private_ip() {
  case "$1" in
    10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*) return 0 ;;
  esac
  return 1
}

skip_iface() {
  # Kit LAN NICs are for OBS only on public/cloud ingest. For private LAN studio
  # (offline lab), eth/enp must bond so USB cellular is not required.
  case "$1" in
    lo|docker*|br-*|veth*|cni*|flannel*|virbr*) return 0 ;;
  esac
  if [ "${ALLOW_LAN_BOND:-0}" = "1" ]; then
    return 1
  fi
  case "$1" in
    eth*|enp*|eno*|ens*) return 0 ;;
  esac
  return 1
}

iface_for_ip() {
  ip -4 -o addr show scope global 2>/dev/null \
    | awk -v ip="$1" '$4 ~ ("^" ip "/") { print $2; exit }'
}

resolve_dest_ip() {
  python3 - "$1" <<'PY'
import socket, sys
host = sys.argv[1].strip()
if not host:
    raise SystemExit(1)
try:
    print(socket.gethostbyname(host))
except OSError:
    raise SystemExit(1)
PY
}

gateway_for_iface() {
  iface="$1"
  dest="$2"
  gw=$(ip -4 route show default dev "$iface" 2>/dev/null | awk '{
    for (i = 1; i <= NF; i++) if ($i == "via") { print $(i + 1); exit }
  }')
  if [ -z "$gw" ] && [ -n "$dest" ]; then
    gw=$(ip -4 route get "$dest" dev "$iface" 2>/dev/null | awk '{
      for (i = 1; i <= NF; i++) if ($i == "via") { print $(i + 1); exit }
    }')
  fi
  if [ -z "$gw" ]; then
    gw=$(ip -4 route show dev "$iface" 2>/dev/null | awk '/via/ {
      for (i = 1; i <= NF; i++) if ($i == "via") { print $(i + 1); exit }
    }')
  fi
  printf '%s' "$gw"
}

clear_policy_tables() {
  table=110
  while [ "$table" -le 119 ]; do
    while ip rule del lookup "$table" 2>/dev/null; do
      :
    done
    ip route flush table "$table" 2>/dev/null || true
    table=$((table + 1))
  done
}

apply_source_routes() {
  dest="$1"
  sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null 2>&1 || true
  sysctl -w net.ipv4.conf.default.rp_filter=2 >/dev/null 2>&1 || true
  clear_policy_tables
  table=110
  while read -r ip; do
    [ -n "$ip" ] || continue
    iface=$(iface_for_ip "$ip" || true)
    if [ -z "$iface" ] || skip_iface "$iface"; then
      echo "Skipping non-uplink $ip ${iface:+dev $iface}" >&2
      continue
    fi
    sysctl -w "net.ipv4.conf.${iface}.rp_filter=2" >/dev/null 2>&1 || true
    # USB modem DHCP often shares 192.168.0.0/24 with kit Ethernet — isolate ARP.
    case "$iface" in
      usb*|enx*|wwan*|cdc*)
        sysctl -w "net.ipv4.conf.${iface}.arp_filter=1" >/dev/null 2>&1 || true
        eth_same=$(python3 - "$ip" <<'PY'
import subprocess, sys
ip = sys.argv[1]
parts = ip.split(".")
if len(parts) != 4:
    raise SystemExit(0)
try:
    cand = tuple(int(x) for x in parts)
except ValueError:
    raise SystemExit(0)
out = subprocess.check_output(
    ["ip", "-4", "-o", "addr", "show", "scope", "global"],
    text=True,
    errors="replace",
)
for line in out.splitlines():
    cols = line.split()
    if len(cols) < 4:
        continue
    name = cols[1]
    if not name.startswith(("eth", "enp", "eno", "ens")):
        continue
    addr = cols[3].split("/")[0].split(".")
    if len(addr) != 4:
        continue
    try:
        lan = tuple(int(x) for x in addr)
    except ValueError:
        continue
    if cand[:3] == lan[:3]:
        print(name)
        raise SystemExit(0)
raise SystemExit(0)
PY
)
        if [ -n "$eth_same" ]; then
          sysctl -w "net.ipv4.conf.${eth_same}.arp_filter=1" >/dev/null 2>&1 || true
          prefix=$(printf '%s.%s.%s.0/24' "$(echo "$ip" | cut -d. -f1-3)")
          eth_ip=$(ip -4 -o addr show dev "$eth_same" scope global | awk '{print $4}' | head -1 | cut -d/ -f1)
          if [ -n "$eth_ip" ]; then
            ip route replace "$prefix" dev "$eth_same" proto kernel scope link src "$eth_ip" metric 50 2>/dev/null || true
          fi
          ip route replace "$prefix" dev "$iface" proto kernel scope link src "$ip" metric 700 2>/dev/null || true
          gw_usb=$(gateway_for_iface "$iface" "$dest")
          if [ -n "$gw_usb" ]; then
            ip route replace default via "$gw_usb" dev "$iface" proto dhcp src "$ip" metric 700 2>/dev/null || true
            ip route replace "$gw_usb/32" dev "$iface" metric 700 2>/dev/null || true
          fi
          echo "Isolated USB $ip on $iface from kit LAN $eth_same ($prefix)" >&2
        fi
        ;;
    esac
    gw=$(gateway_for_iface "$iface" "$dest")
    ip route flush table "$table" 2>/dev/null || true
    ip -4 route show dev "$iface" 2>/dev/null | while read -r line; do
      # shellcheck disable=SC2086
      ip route replace $line table "$table" 2>/dev/null || true
    done
    if [ -n "$gw" ]; then
      ip route replace default via "$gw" dev "$iface" table "$table" 2>/dev/null \
        || ip route replace default dev "$iface" table "$table" 2>/dev/null || true
      ip route replace "$gw/32" dev "$iface" table "$table" 2>/dev/null || true
      echo "Source route $ip -> $dest via $gw dev $iface table $table" >&2
    else
      ip route replace default dev "$iface" table "$table" 2>/dev/null || true
      echo "Source route $ip -> $dest on-link dev $iface table $table" >&2
    fi
    ip rule add from "$ip" lookup "$table" pref "$table" 2>/dev/null || true
    table=$((table + 1))
    if [ "$table" -gt 119 ]; then
      break
    fi
  done < "$UPLINKS_FILE"
}

filter_unroutable() {
  dest="$1"
  filtered="$CONFIG_DIR/uplinks.routable"
  probed="$filtered.ok"
  : > "$filtered"
  : > "$probed"
  while read -r ip; do
    [ -n "$ip" ] || continue
    iface=$(iface_for_ip "$ip" || true)
    if [ -z "$iface" ] || skip_iface "$iface"; then
      continue
    fi
    if [ -n "$dest" ] && ! ip route get "$dest" from "$ip" >/dev/null 2>&1; then
      echo "Dropping $ip: no route to ingest $dest" >&2
      continue
    fi
    if [ -f "$CONFIG_DIR/uplinks.skip" ] && awk -v ip="$ip" '$1==ip { found=1 } END { exit !found }' "$CONFIG_DIR/uplinks.skip"; then
      echo "Dropping $ip: recently failed SRTLA on this path" >&2
      continue
    fi
    printf '%s\n' "$ip" >> "$filtered"
    # Private LAN studio: probe the ingest host itself (no public internet needed).
    if [ "${ALLOW_LAN_BOND:-0}" = "1" ] && [ -n "$dest" ]; then
      if ping -c 1 -W 2 -I "$iface" "$dest" >/dev/null 2>&1 \
        || ping -c 1 -W 2 -I "$ip" "$dest" >/dev/null 2>&1 \
        || python3 -c 'import socket, sys
ip, iface, dest = sys.argv[1], sys.argv[2], sys.argv[3]
# TCP to studio HTTP (or any open port) proves L3 reachability.
for port in (80, 3004, 3002, 443):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        bound = False
        if iface:
            try:
                sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BINDTODEVICE", 25), (iface + "\0").encode())
                bound = True
            except OSError:
                bound = False
        if not bound:
            sock.bind((ip, 0))
        sock.settimeout(2)
        sock.connect((dest, port))
        raise SystemExit(0)
    except OSError:
        pass
    finally:
        sock.close()
raise SystemExit(1)
' "$ip" "$iface" "$dest"; then
        echo "Uplink $ip on $iface reaches LAN ingest $dest" >&2
        printf '%s\n' "$ip" >> "$probed"
      else
        echo "Uplink $ip on $iface cannot reach LAN ingest $dest" >&2
      fi
      continue
    fi
    # Isolated LAN routers still advertise a default. If any uplink can reach
    # a public resolver, keep only those; otherwise keep the route-get set.
    # Some USB tethers (Spreadtrum/Android) block ICMP but still carry TCP/UDP.
    if ping -c 1 -W 2 -I "$iface" 1.1.1.1 >/dev/null 2>&1 \
      || ping -c 1 -W 2 -I "$iface" 8.8.8.8 >/dev/null 2>&1 \
      || ping -c 1 -W 2 -I "$ip" 1.1.1.1 >/dev/null 2>&1 \
      || ping -c 1 -W 2 -I "$ip" 8.8.8.8 >/dev/null 2>&1 \
      || python3 -c 'import socket, sys
ip, iface = sys.argv[1], sys.argv[2]
for dest, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        bound = False
        if iface:
            try:
                sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BINDTODEVICE", 25), (iface + "\0").encode())
                bound = True
            except OSError:
                bound = False
        if not bound:
            sock.bind((ip, 0))
        sock.settimeout(3)
        sock.connect((dest, port))
        raise SystemExit(0)
    except OSError:
        pass
    finally:
        sock.close()
raise SystemExit(1)
' "$ip" "$iface"
    then
      echo "Uplink $ip on $iface has internet" >&2
      printf '%s\n' "$ip" >> "$probed"
    else
      echo "Uplink $ip on $iface failed internet probe" >&2
    fi
  done < "$UPLINKS_FILE"
  if [ -s "$probed" ]; then
    cp "$probed" "$filtered"
  else
    echo "No uplink passed internet probe toward $dest" >&2
    return 1
  fi
  UPLINKS_FILE="$filtered"
  export UPLINKS_FILE
}

resolve_uplinks() {
  if grep -v '^[[:space:]]*#' "$UPLINKS_FILE" | grep -q '^AUTO[[:space:]]*$'; then
    resolved="$CONFIG_DIR/uplinks.resolved"
    if [ "${ALLOW_LAN_BOND:-0}" = "1" ]; then
      # Offline LAN studio: prefer kit Ethernet toward private ingest.
      ip -4 -o addr show scope global 2>/dev/null \
        | awk '$2 !~ /^(lo|docker[0-9]*|br-|veth|cni|flannel|virbr)/ && !seen[$2]++ { print $4 }' \
        | cut -d/ -f1 \
        | grep -vE '^(127\.|172\.17\.)' \
        > "$resolved" || true
    else
      # Match status-server: never AUTO-bond kit LAN (eth/enp/eno/ens). Keep usb*/enx*.
      ip -4 -o addr show scope global 2>/dev/null \
        | awk '$2 !~ /^(lo|docker[0-9]*|br-|veth|cni|flannel|virbr|eth|enp|eno|ens)/ && !seen[$2]++ { print $4 }' \
        | cut -d/ -f1 \
        | grep -vE '^(127\.|172\.17\.)' \
        > "$resolved" || true
    fi
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
timeout = max(60000, min(180000, latency * 8))
# srtla_send clamps --conn-timeout-ms to 1000..=60000 (see --help). Passing
# 64000 for an 8000 ms Ghana window is rejected/ignored; keep the 60s floor.
timeout = min(60000, timeout)
print("LEOCASTRA_HOST=" + shlex.quote(host))
print("BONDED_PORT=" + shlex.quote(bonded))
print("SRT_LISTEN_PORT=" + shlex.quote(listen))
print("STUDIO_URL=" + shlex.quote(studio))
print("SRTLA_MODE=" + shlex.quote(mode))
print("SRTLA_QUALITY=" + ("0" if cfg.get("qualityScoring") is False else "1"))
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
  export LEOCASTRA_HOST BONDED_PORT SRT_LISTEN_PORT STUDIO_URL LATENCY_MS SRTLA_MODE SRTLA_QUALITY CONN_TIMEOUT_MS
  if [ -z "${LEOCASTRA_HOST:-}" ]; then
    echo "Waiting for ingest host in Settings." >&2
    sleep 3
    continue
  fi
  UPLINKS_FILE="${CONFIG_DIR}/uplinks"
  export UPLINKS_FILE
  DEST_IP=""
  DEST_IP=$(resolve_dest_ip "$LEOCASTRA_HOST" 2>/dev/null || true)
  ALLOW_LAN_BOND=0
  if is_private_ip "${DEST_IP:-}" || is_private_ip "${LEOCASTRA_HOST:-}"; then
    ALLOW_LAN_BOND=1
  fi
  export ALLOW_LAN_BOND
  if ! resolve_uplinks; then
    sleep 3
    continue
  fi
  apply_source_routes "$DEST_IP"
  if ! filter_unroutable "$DEST_IP"; then
    sleep 3
    continue
  fi
  echo "Bonding $(tr '\n' ' ' < "$UPLINKS_FILE") -> ${LEOCASTRA_HOST}:${BONDED_PORT} (${DEST_IP:-unresolved})" >&2
  rm -f "$CONFIG_DIR/restart.flag"
  QUALITY_ARGS=""
  if [ "${SRTLA_QUALITY:-1}" = "0" ]; then
    QUALITY_ARGS="--no-quality"
  fi
  "$SRTLA_SEND_BIN" \
    --metrics-bind "$METRICS_BIND" \
    --mode "${SRTLA_MODE:-enhanced}" \
    $QUALITY_ARGS \
    --conn-timeout-ms "${CONN_TIMEOUT_MS:-60000}" \
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
