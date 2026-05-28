#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

has_config_arg=false
for arg in "$@"; do
  if [[ "$arg" == "--config" || "$arg" == --config=* ]]; then
    has_config_arg=true
    break
  fi
done

if [[ "$has_config_arg" == false && -f "$SCRIPT_DIR/tdt_config.local.toml" ]]; then
  exec python3 "$SCRIPT_DIR/scripts/tdt_orchestrator.py" --config "$SCRIPT_DIR/tdt_config.local.toml" "$@"
fi

exec python3 "$SCRIPT_DIR/scripts/tdt_orchestrator.py" "$@"
