# TDT performance model

This directory contains simulation-layer performance probes. The probes do not
change Prysm, Geth, validator counts, slot timing, peer topology, or any other
simulated application configuration.

Run:

```bash
python3 experiments/perf_model/run_perf_model.py --setups 1,4,8 --trials 1
```

Use `--perf-counters off` for throughput comparisons. Detailed counters are
useful for locating hot paths, but they add measurement overhead and should not
be treated as the clean speed baseline.

Outputs:

- `perf-model.json`: machine-readable case results and summaries
- `REPORT.md`: compact table for human inspection

The first modeling goal is to separate correctness from throughput. Correctness
is guarded by `experiments/run_local_suite.py`; this probe records checkpoint
latency, restore latency, artifact size, and total wall-clock cost so later
Shadow-only instrumentation can attach scheduler, event, packet, and worker
parallelism counters to the same setup matrix.
