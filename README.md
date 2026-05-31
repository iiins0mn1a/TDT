# TDT

TDT is a standalone real-client harness for running Ethereum-like deployments inside Shadow. It prepares a small testnet with one shared geth execution node, multiple Prysm beacon nodes, and matching Prysm validators, then drives Shadow through smoke, checkpoint/restore, and deterministic replay workflows.

The repository is intentionally organized as an orchestration layer. Shadow, Prysm, and go-ethereum are kept as Git submodules under `deps/`, so the harness can pin the exact simulator and client revisions used by an experiment without mixing their source trees into TDT itself.

## Repository Layout

- `assets/`: seed genesis and chain configuration inputs copied into each runtime.
- `scripts/`: orchestration, config loading, network database creation, log checks, and checkpoint/restore control helpers.
- `experiments/checkpoint-study/`: repeatable determinism and latency runner for setup sizes 1, 4, and 8.
- `tdt_config.up_to_date.toml`: default latest-client run configuration on this branch.
- `tdt_config.toml`: baseline run configuration kept for comparison.
- `run_tdt.sh`: compatibility entrypoint for interactive and one-shot runs.
- `deps/shadow`: Shadow fork with deterministic checkpoint/restore work.
- `deps/prysm-v7.1.4`: latest Prysm release used by the up-to-date real-client nodes.
- `deps/go-ethereum-v1.17.3`: latest geth release used by the up-to-date execution client.
- `deps/prysm` and `deps/go-ethereum`: baseline clients kept for comparison.
- `runtime/`: generated working directory; ignored by Git.

## Checkout

Clone with submodules:

```bash
git clone --recurse-submodules git@github.com:iiins0mn1a/TDT.git
cd TDT
```

For an existing clone:

```bash
git submodule update --init --recursive
```

The pinned Shadow submodule currently points at `iiins0mn1a/shadow-gen`
branch `up-to-date`, commit `66fcc1535266cfd33bebdf7fe1498d446a889c84`.

## Build Prerequisites

Build the dependencies before running TDT:

```bash
cmake --build deps/shadow/build -j4 --target shadow
# Build Prysm targets with the project-local Bazel setup.
# Build geth so deps/go-ethereum/build/bin/geth exists.
```

On the `up-to-date` branch, the latest-client experiment is pinned separately
from the baseline clients:

- `deps/go-ethereum-v1.17.3`
- `deps/prysm-v7.1.4`

Build the up-to-date geth binary with:

```bash
./scripts/build_up_to_date_clients.sh
```

The script builds from a temporary local clone before copying the binary back to
`deps/go-ethereum-v1.17.3/build/bin/geth`. This keeps geth's embedded VCS
metadata tied to the geth submodule commit rather than the TDT superproject.

TDT also needs a CRIU binary that works with the modified Shadow checkpoint/restore path. Set it through `CRIU_BIN` or the active TDT config.

## Configuration

Edit `tdt_config.up_to_date.toml` to control cluster size, runtime duration,
checkpoint/restore windows, and binary overrides for the latest-client setup.
This branch uses it by default from `run_tdt.sh`, `tdt_orchestrator.py`, and the
local suite unless `TDT_CONFIG` or an explicit `--config` is supplied.

The latest-client config resolves binaries from:

- `deps/shadow/build/src/main/shadow`
- `deps/prysm-v7.1.4/dist/prysmctl-v7.1.4-linux-amd64`
- `deps/prysm-v7.1.4/dist/beacon-chain-v7.1.4-linux-amd64`
- `deps/prysm-v7.1.4/dist/validator-v7.1.4-linux-amd64`
- `deps/go-ethereum-v1.17.3/build/bin/geth`

For the baseline clients, pass `--config tdt_config.toml` or set
`TDT_CONFIG=tdt_config.local.toml` if you maintain a local override. The
baseline config resolves binaries from:

- `deps/shadow/build/src/main/shadow`
- `deps/prysm/bazel-bin/cmd/prysmctl/prysmctl_/prysmctl`
- `deps/prysm/bazel-bin/cmd/beacon-chain/beacon-chain_/beacon-chain`
- `deps/prysm/bazel-bin/cmd/validator/validator_/validator`
- `deps/go-ethereum/build/bin/geth`

The default restore protocol mode is `deterministic_v2`.

## Usage

Interactive panel:

```bash
./run_tdt.sh
```

Command shell:

```bash
./run_tdt.sh --command-shell
```

One-shot smoke run:

```bash
./run_tdt.sh --mode smoke --non-interactive
```

One-shot checkpoint/restore run:

```bash
CRIU_BIN=/path/to/criu ./run_tdt.sh --mode cprestore --non-interactive
```

Latest-client up-to-date suite:

```bash
python3 experiments/run_local_suite.py \
  --results-dir /tmp/tdt-up-to-date-suite \
  --work-root /tmp/tdt-up-to-date-suite-work \
  --checkpoint-criu-jobs 32
```

Determinism guard for the real-client checkpoint study:

```bash
python3 experiments/checkpoint-study/run_study.py --mode determinism --setup 4 --results-dir /tmp/tdt-guard-setup4
```

A passing determinism run means the post-checkpoint application-log window exactly matches the post-restore replay window for geth, beacon, and validator logs.

The checkpoint study defaults to `../../tdt_config.up_to_date.toml` on this
branch. Pass `--config` with another experiment TOML if you need to compare
against the baseline config.

## Current Limitation

Managed external rollback currently covers only the paths listed in `checkpoint_restore.managed_external_paths`, which default to:

- `runtime/network/`
- `runtime/beacon_peers.txt`

Host-side `runtime/shadow.data/` is not externally rewound. The deterministic replay experiment excludes recorder-helper logs and host-side Shadow artifacts from its oracle.
