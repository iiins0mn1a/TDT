# Ethereum-like Network Checkpoint/Restore PoC

This PoC does not implement Ethereum protocols. It covers a topology closer to a testnet skeleton:

- `bootnode` accepts TCP neighbor connections from multiple peers
- `peer-a`, `peer-b`, and `peer-c` maintain long-lived TCP sessions
- UDP carries discovery-like `discover_ping` / `discover_pong` traffic
- after restore, all peers must keep progressing without TCP reconnect churn

Run from `experiments/synthetic`:

```bash
CRIU_BIN=/path/to/criu \
python3 checkpoint-network-eth-poc/orchestrator_verify.py \
  --shadow-bin /path/to/shadow \
  --config checkpoint-network-eth-poc/shadow_eth_poc.yaml \
  --work-dir /tmp/tdt-synthetic-eth-poc \
  --clean-data \
  --verify-label cp_eth_poc_verify
```
