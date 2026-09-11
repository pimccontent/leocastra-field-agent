#!/usr/bin/env bash
# LeoCastra Field Agent - one-command Ubuntu install (mini server, no monitor).
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
LeoCastra Field Agent - one-command install (Ubuntu)

  sudo ./install.sh

  curl -fsSL https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/main/install.sh | sudo bash

After install, open http://<kit-ip>:8088 and save ingest host + bonded port.
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

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1; then
    return 0
  fi
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg lsb-release git
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
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin git
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
  apt-get update -y
  apt-get install -y git curl ca-certificates
  echo "Fetching Field Agent into ${INSTALL_DIR}..." >&2
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 -b "$GIT_BRANCH" "$(clone_url)" "$INSTALL_DIR"
  echo "$INSTALL_DIR"
}

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

KIT_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "Field Agent is installed and starts with this machine."
echo "  UI: http://${KIT_IP:-<kit-ip>}:8088"
echo "Open Settings, save ingest host and bonded port, then start listening in studio."
