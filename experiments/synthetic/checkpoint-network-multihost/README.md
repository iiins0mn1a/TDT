# Multi-host Network Checkpoint/Restore Test

This is the smallest synthetic network checkpoint/restore gate:

- one long-lived TCP connection
- one pair of bidirectional UDP heartbeats
- TCP continues after restore without reconnecting
- UDP continues in both directions after restore

Files:

- `orchestrator_verify.py`: verifier
- `net_app.py`: TCP/UDP workload
- `shadow_network.yaml`: TCP + UDP topology
- `shadow_tcp_only.yaml`: TCP-only topology

Run from `experiments/synthetic`:

```bash
CRIU_BIN=/path/to/criu \
python3 checkpoint-network-multihost/orchestrator_verify.py \
  --shadow-bin /path/to/shadow \
  --config checkpoint-network-multihost/shadow_network.yaml \
  --work-dir /tmp/tdt-synthetic-multihost \
  --clean-data \
  --verify-label cp_network_verify
```

For TCP-only stepped restore:

```bash
CRIU_BIN=/path/to/criu \
python3 checkpoint-network-multihost/orchestrator_verify.py \
  --shadow-bin /path/to/shadow \
  --config checkpoint-network-multihost/shadow_tcp_only.yaml \
  --work-dir /tmp/tdt-synthetic-multihost-tcp \
  --clean-data \
  --verify-label cp_tcp_only \
  --mode tcp \
  --post-restore-step-ns 1000000000 \
  --post-restore-steps 10
```
