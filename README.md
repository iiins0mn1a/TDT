# TDT

TDT is a standalone real-client harness for running Ethereum-like deployments inside Shadow. It prepares a small testnet with one shared geth execution node, multiple Prysm beacon nodes, and matching Prysm validators, then drives Shadow through smoke, checkpoint/restore, and deterministic replay workflows.

The repository is intentionally organized as an orchestration layer. Shadow, Prysm, and go-ethereum are kept as Git submodules under `deps/`, so the harness can pin the exact simulator and client revisions used by an experiment without mixing their source trees into TDT itself.

## Repository Layout

- `assets/`: seed genesis and chain configuration inputs copied into each runtime.
- `scripts/`: orchestration, config loading, network database creation, log checks, and checkpoint/restore control helpers.
- `experiments/checkpoint-study/`: repeatable determinism and latency runner for setup sizes 1, 4, and 8.
- `tdt_config.toml`: default human-edited run configuration.
- `run_tdt.sh`: compatibility entrypoint for interactive and one-shot runs.
- `deps/shadow`: Shadow fork with deterministic checkpoint/restore work.
- `deps/prysm`: Prysm fork used by the real-client nodes.
- `deps/go-ethereum`: geth source used for the execution client.
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

The pinned Shadow submodule currently points at `iiins0mn1a/shadow-gen` branch `spike-network-restore-protocol-rewrite`, commit `265cac9ad7967a717e4322443dfec8ea32a34f21`.

## Build Prerequisites

Build the dependencies before running TDT:

```bash
cmake --build deps/shadow/build -j4 --target shadow
# Build Prysm targets with the project-local Bazel setup.
# Build geth so deps/go-ethereum/build/bin/geth exists.
```

TDT also needs a CRIU binary that works with the modified Shadow checkpoint/restore path. Set it through `CRIU_BIN` or `tdt_config.toml`.

## Configuration

Edit `tdt_config.toml` to control cluster size, runtime duration, checkpoint/restore windows, and binary overrides. By default, TDT resolves binaries from the submodules:

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

Determinism guard for the real-client checkpoint study:

```bash
python3 experiments/checkpoint-study/run_study.py --mode determinism --setup 4 --results-dir /tmp/tdt-guard-setup4
```

A passing determinism run means the post-checkpoint application-log window exactly matches the post-restore replay window for geth, beacon, and validator logs.

## Current Limitation

Managed external rollback currently covers only the paths listed in `checkpoint_restore.managed_external_paths`, which default to:

- `runtime/network/`
- `runtime/beacon_peers.txt`

Host-side `runtime/shadow.data/` is not externally rewound. The deterministic replay experiment excludes recorder-helper logs and host-side Shadow artifacts from its oracle.
