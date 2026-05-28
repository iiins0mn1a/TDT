#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

runtime_dir="${1:-$DEFAULT_RUNTIME_DIR}"

if [[ "${2:-}" != "--force" && "${1:-}" != "--force" ]]; then
    read -r -p "Remove generated runtime state under $runtime_dir? (yes/no): " reply
    echo
    [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]] || {
        log "Aborted"
        exit 0
    }
fi

log "Cleaning runtime under $runtime_dir"
rm -rf \
    "$runtime_dir/shadow.data" \
    "$runtime_dir/network" \
    "$runtime_dir/network.bck" \
    "$runtime_dir/checkpoint.bck" \
    "$runtime_dir/checkpoint.json" \
    "$runtime_dir/rendered.shadow.yaml" \
    "$runtime_dir/shadow-test.log" \
    "$runtime_dir/cprestore.log" \
    "$runtime_dir/control.sock"
rm -f "$runtime_dir/beacon_peers.txt"
mkdir -p "$runtime_dir"
log_success "Runtime cleaned"
