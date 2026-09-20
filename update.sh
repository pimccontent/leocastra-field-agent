#!/usr/bin/env bash
# Safe Field Agent update: pull main, rebuild image, health-check UI.
# Does not wipe ./data (config, uplinks). Bond settings stay intact.
#
#   cd /opt/leocastra-field-agent && sudo ./update.sh
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$ROOT"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo ./update.sh" >&2
  exit 1
fi

if [[ ! -f docker-compose.yml ]]; then
  echo "No docker-compose.yml in $ROOT — is this the Field Agent install dir?" >&2
  exit 1
fi

echo "Updating Field Agent in $ROOT ..."
if [[ -d .git ]]; then
  git fetch origin
  git checkout main
  # Refuse if local tracked files would be overwritten; keep operator edits safe.
  if ! git merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
    echo "Local commits diverge from origin/main. Resolve git state, then re-run." >&2
    git status -sb >&2
    exit 1
  fi
  git pull --ff-only origin main
else
  echo "Not a git checkout; skipping pull (rebuild only)." >&2
fi

COMPOSE=(docker compose -f docker-compose.yml)
if [[ -f docker-compose.kit.yml ]]; then
  COMPOSE+=(-f docker-compose.kit.yml)
fi

echo "Rebuilding and restarting container ..."
"${COMPOSE[@]}" up -d --build

echo "Waiting for Web UI on :8088 ..."
ok=0
for _ in $(seq 1 45); do
  if curl -fsS -m 2 -o /dev/null http://127.0.0.1:8088/; then
    ok=1
    break
  fi
  sleep 2
done

if [[ "$ok" -ne 1 ]]; then
  echo "UI did not answer after update. Recent logs:" >&2
  docker logs --tail 80 leocastra-field-agent >&2 || true
  exit 1
fi

REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "Update OK — revision $REV — http://127.0.0.1:8088"
echo "Bond config preserved under ./data (or /var/lib/leocastra in container)."
