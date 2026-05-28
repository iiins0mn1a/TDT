# TDT Checkpoint Study

This experiment measures two real-client properties of Shadow checkpoint/restore
using the TDT orchestrator and the `1 shared geth + N beacon + N validator`
setup family:

1. Determinism after restore
2. Checkpoint/restore phase latency

## What it measures

### Determinism

For each setup (`1`, `4`, `8` beacon nodes), the runner:

1. prepares a fresh TDT runtime
2. launches the real-client testnet
3. advances until the network is checkpoint-ready
4. waits an additional settle window
5. issues a checkpoint
6. records application log offsets at the checkpoint boundary
7. runs for a fixed comparison window and stores the resulting log slice
8. restores the managed external state and the checkpoint
9. runs for the same comparison window and stores the replay log slice
10. compares the two log windows

The determinism oracle is application logs only:

- `geth`
- `beacon`
- `validator`

Recorder helper logs and host-side `shadow.data` artifacts are excluded.

### Performance

For each setup, the runner performs several trials and records:

- checkpoint latency: from sending the `checkpoint` command to receiving completion
- restore latency: from sending the `restore` command to restore completion / reconnection readiness

Managed external-state rollback is still performed to make restore valid, but it
is not included in the primary latency metric.

## Usage

Run all experiments:

```bash
python3 run_study.py --mode all
```

Run only determinism for a single setup:

```bash
python3 run_study.py --mode determinism --setup 4
```

Run only performance for all setups with more trials:

```bash
python3 run_study.py --mode performance --setup all --trials 5
```

## Output

- `results/determinism-setup-<N>.json`
- `results/performance-setup-<N>.json`
- `results/REPORT.md`
- `results/diffs/` for determinism mismatches

## Notes

- The experiment assumes the current TDT limitation that managed external
  rollback covers only `network/` and `beacon_peers.txt`.
- The experiment compares the post-checkpoint window to the post-restore replay
  window within the same orchestrated run.
- Minimal normalization is applied to hex addresses when enabled in the config.
