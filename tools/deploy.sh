#!/usr/bin/env bash
# Copy the code from this laptop to the Pi (~/ballin-board, which lives on the data partition).
#   bash tools/deploy.sh [host]
# Falls back to the "scoreboard-ip" SSH alias (see ~/.ssh/config) when scoreboard.local
# doesn't resolve (Windows mDNS is sometimes flaky).
set -euo pipefail
HOST="${1:-}"
if [ -z "$HOST" ]; then
    # Reach the board by name, else by its LAN IP (on the same WiFi as the board).
    for HOST in dacino@scoreboard.local scoreboard-ip; do
        ssh -o ConnectTimeout=6 -o BatchMode=yes "$HOST" true 2>/dev/null && break
    done
fi
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar -czf "$TMP/bb.tgz" --exclude=__pycache__ ballin assets tools systemd setup
scp -q "$TMP/bb.tgz" "$HOST:/tmp/bb.tgz"
ssh "$HOST" 'mkdir -p ~/ballin-board && tar -xzf /tmp/bb.tgz -C ~/ballin-board && rm /tmp/bb.tgz && echo "deployed $(date +%T)"'
