# Checkpoint Network Ethereum Multiprocess

This synthetic experiment models an Ethereum-like host layout where each Shadow host runs multiple cooperating processes:

- `execution`: local TCP RPC server
- `beacon`: local TCP RPC client/server plus cross-host P2P TCP/UDP
- `validator`: local TCP RPC client

The workload uses `epoll`, `eventfd`, and `timerfd` in each process so checkpoint/restore covers richer async-runtime descriptor state than the smaller network smoke tests.

Run from `experiments/synthetic`:

```bash
CRIU_BIN=/path/to/criu \
python3 checkpoint-network-eth-multiproc/orchestrator_verify.py \
  --shadow-bin /path/to/shadow \
  --config checkpoint-network-eth-multiproc/shadow_eth_multiproc.yaml \
  --work-dir /tmp/tdt-synthetic-eth-multiproc \
  --clean-data \
  --verify-label cp_eth_multiproc_verify
```
