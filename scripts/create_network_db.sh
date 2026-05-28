#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

NODE_COUNT="${NODE_COUNT:-4}"
RUNTIME_DIR="${RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
NETWORK_DIR="$RUNTIME_DIR/network"

log "create-network-db: NODE_COUNT=$NODE_COUNT runtime=$RUNTIME_DIR"

reset_peer_file "$RUNTIME_DIR"

for dir in "$NETWORK_DIR/bootnode" "$NETWORK_DIR"/node-*; do
    if [[ -e "$dir" ]]; then
        rm -rf "$dir"
    fi
done

mkdir -p "$NETWORK_DIR/bootnode"

for file in genesis.ssz genesis.json config.yml; do
    if [[ ! -f "$NETWORK_DIR/$file" ]]; then
        log_error "Missing file: $NETWORK_DIR/$file"
        exit 1
    fi
done

for ((i=1; i<=NODE_COUNT; i++)); do
    node_dir="$NETWORK_DIR/node-$i"
    if [[ $i -eq 1 ]]; then
        mkdir -p "$node_dir"/{execution,consensus,logs}
        cp "$NETWORK_DIR/genesis.json" "$node_dir/execution/genesis.json"
        cp "$NETWORK_DIR/genesis.ssz" "$node_dir/consensus/genesis.ssz"
        cp "$NETWORK_DIR/config.yml" "$node_dir/consensus/config.yml"
        : > "$node_dir/geth_password.txt"
        openssl rand -hex 32 > "$node_dir/execution/jwtsecret"
        chmod 600 "$node_dir/execution/jwtsecret"
    else
        mkdir -p "$node_dir"/{consensus,logs}
        cp "$NETWORK_DIR/genesis.ssz" "$node_dir/consensus/genesis.ssz"
        cp "$NETWORK_DIR/config.yml" "$node_dir/consensus/config.yml"
    fi
done

log_success "Network database created successfully for $NODE_COUNT node(s)"
