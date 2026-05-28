#!/usr/bin/env bash
set -euo pipefail

IDX="$1"
RPC_PORT="$2"
HTTP_PORT="$3"
TCP_PORT="$4"
UDP_PORT="$5"
MON_PORT="$6"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RUNTIME_DIR="${TDT_RUNTIME_DIR:?TDT_RUNTIME_DIR must be set}"
NETWORK_DIR="$RUNTIME_DIR/network"
NODE_DIR="$NETWORK_DIR/node-$IDX"
JWT_SECRET="$NETWORK_DIR/node-1/execution/jwtsecret"
PEER_FILE="$RUNTIME_DIR/beacon_peers.txt"
BEACON_BINARY="$DEFAULT_BEACON_BIN"

PEER_FLAGS=()
if [[ -f "$PEER_FILE" && "$IDX" -gt 1 ]]; then
    for ((i=IDX-1, n=0; i>=1 && n<3; i--, n++)); do
        peer="$(sed -n "${i}p" "$PEER_FILE" || true)"
        if [[ -n "$peer" ]]; then
            PEER_FLAGS+=(--peer="$peer")
        fi
    done
fi

if [[ ${#PEER_FLAGS[@]} -gt 0 ]]; then
    printf "%s\n" "${PEER_FLAGS[@]}"
else
    echo "Warning: No peers found for beacon-$IDX"
fi

SPEC_LOG_NODE="node-$IDX" exec "$BEACON_BINARY" \
  --datadir="$NODE_DIR/consensus/beacondata" \
  --min-sync-peers=0 \
  --p2p-max-peers=64 \
  --genesis-state="$NODE_DIR/consensus/genesis.ssz" \
  --interop-eth1data-votes \
  --chain-config-file="$NODE_DIR/consensus/config.yml" \
  --contract-deployment-block=0 \
  --chain-id=32382 \
  --rpc-host="11.0.0.$((IDX - 1))" \
  --rpc-port="$RPC_PORT" \
  --grpc-gateway-host="11.0.0.$((IDX - 1))" \
  --grpc-gateway-port="$HTTP_PORT" \
  --execution-endpoint="http://11.0.2.10:8200" \
  --accept-terms-of-use \
  --jwt-secret="$JWT_SECRET" \
  --suggested-fee-recipient=0x123463a4B065722E99115D6c222f267d9cABb524 \
  --minimum-peers-per-subnet=0 \
  --p2p-tcp-port="$TCP_PORT" \
  --p2p-udp-port="$UDP_PORT" \
  --p2p-host-ip="11.0.0.$((IDX - 1))" \
  --monitoring-port="$MON_PORT" \
  --verbosity=info \
  "${PEER_FLAGS[@]}"
