#!/usr/bin/env bash
# LeoCastra Field Agent - one-command Ubuntu Server install (mini PC).
#
# From this folder:
#   sudo ./install.sh
#
# Piped (public repo):
#   curl -fsSL https://raw.githubusercontent.com/pimccontent/leocastra-field-agent/main/install.sh | sudo bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/leocastra-field-agent}"
REPO_OWNER="${REPO_OWNER:-pimccontent}"
REPO_NAME="${REPO_NAME:-leocastra-field-agent}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || pwd)"

usage() {
  cat <<EOF
LeoCastra Field Agent - one-command install (Ubuntu Server)

  sudo ./install.sh

  curl -fsSL https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/main/install.sh | sudo bash

After install, open http://<kit-ip>:8088 from another device on the same LAN.
SSH: ssh <user>@<kit-ip>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --github-token|--token) GITHUB_TOKEN="${2:-}"; shift 2 ;;
    --branch) GIT_BRANCH="${2:-}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Please run as root (use sudo)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

warn_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
  echo "Host: ${PRETTY_NAME:-unknown} ($(uname -m))"
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "Warning: this installer is tested on Ubuntu Server. Continuing anyway." >&2
  fi
  case "${VERSION_ID:-}" in
    22.04|24.04) ;;
    20.04)
      echo "Warning: Ubuntu 20.04 works but is past standard support. Prefer 22.04 or 24.04 LTS Server." >&2
      ;;
    *)
      echo "Warning: untested Ubuntu ${VERSION_ID:-unknown}. Prefer 22.04 or 24.04 LTS Server (no desktop)." >&2
      ;;
  esac
}

ensure_dns() {
  if getent ahostsv4 github.com >/dev/null 2>&1; then
    return 0
  fi
  echo "DNS is not resolving. Setting 8.8.8.8 / 1.1.1.1 on the current default interface." >&2
  local iface
  iface="$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
  if command -v resolvectl >/dev/null 2>&1 && [[ -n "$iface" ]]; then
    resolvectl dns "$iface" 8.8.8.8 1.1.1.1 || true
    resolvectl domain "$iface" '~.' || true
  fi
  if ! getent ahostsv4 github.com >/dev/null 2>&1; then
    mkdir -p /etc/systemd/resolved.conf.d
    cat >/etc/systemd/resolved.conf.d/99-leocastra-dns.conf <<'EOF'
[Resolve]
DNS=8.8.8.8 1.1.1.1
FallbackDNS=9.9.9.9
EOF
    systemctl restart systemd-resolved 2>/dev/null || true
  fi
  if ! getent ahostsv4 github.com >/dev/null 2>&1; then
    echo "github.com still does not resolve. Fix DNS, then re-run this installer." >&2
    echo "  ping -c 2 8.8.8.8" >&2
    echo "  getent ahostsv4 github.com" >&2
    exit 1
  fi
}

prepare_host() {
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg lsb-release git openssh-server \
    iproute2 procps python3
  systemctl enable --now ssh || systemctl enable --now sshd
  cat >/etc/sysctl.d/99-leocastra-field-agent.conf <<'EOF'
# Loose reverse-path filter so multi-homed SRTLA uplinks (Ethernet + usb0) can receive replies.
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
EOF
  sysctl --system >/dev/null 2>&1 || sysctl -p /etc/sysctl.d/99-leocastra-field-agent.conf >/dev/null 2>&1 || true
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 22/tcp
    ufw allow 8088/tcp
    ufw allow 4001/udp
  fi
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

resolve_kit_dir() {
  if [[ -f "${SCRIPT_DIR}/docker-compose.yml" ]]; then
    echo "${SCRIPT_DIR}"
    return 0
  fi
  if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
    echo "${INSTALL_DIR}"
    return 0
  fi
  echo ""
}

clone_url() {
  if [[ -n "$GITHUB_TOKEN" ]]; then
    echo "https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO_OWNER}/${REPO_NAME}.git"
  else
    echo "https://github.com/${REPO_OWNER}/${REPO_NAME}.git"
  fi
}

fetch_kit_if_needed() {
  local kit
  kit="$(resolve_kit_dir)"
  if [[ -n "$kit" ]]; then
    echo "$kit"
    return 0
  fi
  echo "Fetching Field Agent into ${INSTALL_DIR}..." >&2
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 -b "$GIT_BRANCH" "$(clone_url)" "$INSTALL_DIR"
  echo "$INSTALL_DIR"
}

print_ui_urls() {
  echo ""
  echo "========================================"
  echo "  Field Agent Web UI"
  echo "========================================"
  local found=0
  local iface cidr ip
  while read -r iface cidr; do
    [[ -n "${iface:-}" && -n "${cidr:-}" ]] || continue
    case "$iface" in
      docker*|br-*|veth*|cni*|flannel*|virbr*|lo) continue ;;
    esac
    ip="${cidr%%/*}"
    echo "  http://${ip}:8088"
    echo "  ssh <user>@${ip}"
    found=1
  done < <(ip -4 -o addr show scope global 2>/dev/null | awk '{print $2, $4}')
  if [[ "$found" -eq 0 ]]; then
    echo "  No LAN IPv4 found yet. Plug in Ethernet, then run:"
    echo "    ip -4 -o addr show scope global"
    echo "  Open http://<that-ip>:8088 from a phone or laptop on the same network."
  else
    echo "========================================"
    echo "Open the http:// URL from a phone or laptop on the same LAN."
    echo "There is no clickable link on this server screen."
  fi
}

wait_for_ui() {
  local i
  echo "Waiting for Web UI on :8088 ..."
  for i in $(seq 1 45); do
    if curl -fsS -m 2 -o /dev/null http://127.0.0.1:8088/; then
      echo "Web UI is listening on http://127.0.0.1:8088"
      return 0
    fi
    sleep 2
  done
  echo "Web UI did not answer on :8088. Last container logs:" >&2
  docker logs --tail 80 leocastra-field-agent >&2 || true
  docker ps -a --filter name=leocastra-field-agent >&2 || true
  return 1
}

warn_os
prepare_host
ensure_dns
install_docker_if_needed
systemctl enable docker
systemctl start docker

KIT_DIR="$(fetch_kit_if_needed)"
cd "$KIT_DIR"
if [[ ! -f .env ]]; then
  cp env.example .env
  echo "Created .env from env.example. Set ingest host and bonded port in the Web UI."
fi

if [[ -f docker-compose.kit.yml ]]; then
  docker compose -f docker-compose.yml -f docker-compose.kit.yml up -d --build
else
  docker compose up -d --build
fi

print_ui_urls
wait_for_ui || true
echo "Then open Settings, save ingest host and bonded port, start listening in studio, and use only this kit on that bonded port."
