#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${TDT_CLIENT_BUILD_ROOT:-/tmp/tdt-client-build}"

GETH_SRC="$ROOT_DIR/deps/go-ethereum-v1.17.3"
GETH_BUILD="$BUILD_ROOT/go-ethereum-v1.17.3"
GETH_OUT="$GETH_SRC/build/bin/geth"

if [[ ! -d "$GETH_SRC/.git" && ! -f "$GETH_SRC/.git" ]]; then
  echo "missing geth source: $GETH_SRC" >&2
  exit 1
fi

GETH_COMMIT="$(git -C "$GETH_SRC" rev-parse HEAD)"
GETH_TAG="$(git -C "$GETH_SRC" describe --tags --exact-match HEAD)"

mkdir -p "$BUILD_ROOT"
if [[ ! -d "$GETH_BUILD/.git" ]]; then
  git clone "$GETH_SRC" "$GETH_BUILD"
fi

git -C "$GETH_BUILD" fetch --tags "$GETH_SRC" "$GETH_COMMIT"
git -C "$GETH_BUILD" checkout --detach "$GETH_COMMIT"

(
  cd "$GETH_BUILD"
  go run build/ci.go install ./cmd/geth
)

mkdir -p "$(dirname "$GETH_OUT")"
install -m 0755 "$GETH_BUILD/build/bin/geth" "$GETH_OUT"

echo "built geth $GETH_TAG $GETH_COMMIT -> $GETH_OUT"
echo
"$GETH_OUT" version
