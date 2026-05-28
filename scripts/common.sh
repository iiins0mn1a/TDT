#!/usr/bin/env bash

if [[ -n "${_TDT_COMMON_SH_LOADED:-}" ]]; then
    return
fi
_TDT_COMMON_SH_LOADED=1

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] ✓${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] ⚠${NC} $*"
}

log_error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ✗${NC} $*" >&2
}

TDT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVENT_ROOT="$(cd "$TDT_DIR/.." && pwd)"
REPOS_DIR="$EVENT_ROOT/repos"
ASSETS_DIR="$TDT_DIR/assets"
DEFAULT_RUNTIME_DIR="$TDT_DIR/runtime"

DEFAULT_SHADOW_BIN="$REPOS_DIR/shadow-gen/build/src/main/shadow"
DEFAULT_GETH_BIN="$REPOS_DIR/go-ethereum/build/bin/geth"
DEFAULT_PRYSMCTL_BIN="$REPOS_DIR/prysm/bazel-bin/cmd/prysmctl/prysmctl_/prysmctl"
DEFAULT_BEACON_BIN="$REPOS_DIR/prysm/bazel-bin/cmd/beacon-chain/beacon-chain_/beacon-chain"
DEFAULT_VALIDATOR_BIN="$REPOS_DIR/prysm/bazel-bin/cmd/validator/validator_/validator"

ensure_binary() {
    local path="$1"
    local label="$2"
    if [[ ! -x "$path" ]]; then
        log_error "$label not found or not executable: $path"
        exit 1
    fi
}

prepare_runtime_layout() {
    local runtime_dir="$1"
    mkdir -p "$runtime_dir" "$runtime_dir/network"
}

copy_seed_inputs() {
    local runtime_dir="$1"
    mkdir -p "$runtime_dir/network"
    cp "$ASSETS_DIR/config.yml" "$runtime_dir/network/config.yml"
    cp "$ASSETS_DIR/genesis.json" "$runtime_dir/network/genesis.json"
}

reset_peer_file() {
    local runtime_dir="$1"
    : > "$runtime_dir/beacon_peers.txt"
}

backup_external_state() {
    local runtime_dir="$1"
    local backup_root="$2"
    rm -rf "$backup_root"
    mkdir -p "$backup_root"
    cp -a "$runtime_dir/network" "$backup_root/network"
    if [[ -f "$runtime_dir/beacon_peers.txt" ]]; then
        cp "$runtime_dir/beacon_peers.txt" "$backup_root/beacon_peers.txt"
    else
        : > "$backup_root/beacon_peers.txt"
    fi
}

restore_external_state() {
    local runtime_dir="$1"
    local backup_root="$2"
    rm -rf "$runtime_dir/network"
    cp -a "$backup_root/network" "$runtime_dir/network"
    cp "$backup_root/beacon_peers.txt" "$runtime_dir/beacon_peers.txt"
}
