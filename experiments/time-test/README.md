# Time syscall microbenchmark

This experiment compares native Linux `clock_gettime` cost with the same workload under Shadow, then runs Shadow with standard strace logging and summarizes syscall frequency.

```bash
SHADOW_BIN=/path/to/shadow ./run_experiment.sh
```

By default `SHADOW_BIN` resolves to `../../deps/shadow/build/src/main/shadow`. Results are written to `experiment_results/`.
