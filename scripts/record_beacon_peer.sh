#!/usr/bin/env bash
set -euo pipefail

IDX="$1"
TCP_PORT="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RUNTIME_DIR="${TDT_RUNTIME_DIR:?TDT_RUNTIME_DIR must be set}"
HOST_DIR="$RUNTIME_DIR/shadow.data/hosts/prysm-beacon-$IDX"
PEER_FILE="$RUNTIME_DIR/beacon_peers.txt"

for _ in $(seq 1 120); do
    shopt -s nullglob
    files=("$HOST_DIR"/start_beacon_shadow.sh.*.stderr)
    shopt -u nullglob
    for log_file in "${files[@]}"; do
        addr="$(grep -aoE "/ip4/[0-9.]+/tcp/${TCP_PORT}/p2p/[A-Za-z0-9]+" "$log_file" | head -n 1 || true)"
        if [[ -n "$addr" ]]; then
            if ! grep -qxF "$addr" "$PEER_FILE" 2>/dev/null; then
                echo "$addr" >> "$PEER_FILE"
                echo "Recorded peer: $addr"
            fi
            exit 0
        fi
    done
    sleep 1
done

exit 0
