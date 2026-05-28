# Synthetic checkpoint/restore experiments

These experiments were migrated from Shadow's checkpoint-network regression directories. They exercise Shadow run-control checkpoint/restore against small synthetic TCP/UDP and Ethereum-like process topologies.

Common environment:

```bash
export SHADOW_BIN=/path/to/shadow
export CRIU_BIN=/path/to/criu
```

Example:

```bash
python3 checkpoint-network-multihost/orchestrator_verify.py \
  --shadow-bin "$SHADOW_BIN" \
  --config checkpoint-network-multihost/shadow_network.yaml \
  --work-dir /tmp/tdt-synthetic-multihost \
  --clean-data \
  --verify-label cp_network_verify
```

The verifier scripts keep their original command-line interfaces; pass explicit `--shadow-bin`, `--config`, and `--work-dir` paths when running from automation.
