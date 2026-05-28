# Checkpoint Network Ethereum Shadow-YAML

This higher-fidelity synthetic checkpoint/restore gate mirrors the target real-client deployment shape more closely than `checkpoint-network-eth-multiproc`:

- one shared execution endpoint (`geth-node`)
- four beacon hosts
- four validator hosts
- one recorder helper process on each beacon host
- host-IP RPC instead of loopback-only RPC
- beacon peer discovery through a shared `beacon_peers.txt` file

The synthetic app still uses `epoll`, `eventfd`, and `timerfd`, so restore continues to exercise async-runtime descriptor state.

Run from `experiments/synthetic`:

```bash
CRIU_BIN=/path/to/criu \
python3 checkpoint-network-eth-shadowyaml/orchestrator_verify.py \
  --shadow-bin /path/to/shadow \
  --config checkpoint-network-eth-shadowyaml/shadow_eth_shadowyaml.yaml \
  --work-dir /tmp/tdt-synthetic-eth-shadowyaml-stable \
  --clean-data \
  --verify-label cp_eth_shadowyaml_verify \
  --scenario stable
```

The verifier supports `--scenario stable` and `--scenario peer-bootstrap`.
