#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

SHADOW_BIN="${SHADOW_BIN:-$REPO_ROOT/deps/shadow/build/src/main/shadow}"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/experiment_results}"
NATIVE_ITERS="${NATIVE_ITERS:-100000}"

printf '[time-test] shadow=%s\n' "$SHADOW_BIN"
printf '[time-test] results=%s\n' "$RESULTS_DIR"

make clean
make
mkdir -p "$RESULTS_DIR"

printf '\n[time-test] native benchmark\n'
./time_benchmark "$NATIVE_ITERS" | tee "$RESULTS_DIR/native_test.log"

printf '\n[time-test] shadow benchmark\n'
rm -rf shadow.data
"$SHADOW_BIN" shadow_time_test.yaml 2>&1 | tee "$RESULTS_DIR/shadow_test.log"

SHADOW_STDOUT="shadow.data/hosts/time-test-frequent/time_benchmark.1000.stdout"
if [[ -f "$SHADOW_STDOUT" ]]; then
  cp "$SHADOW_STDOUT" "$RESULTS_DIR/shadow_output.log"
else
  echo "missing Shadow stdout: $SHADOW_STDOUT" >&2
  exit 1
fi

printf '\n[time-test] shadow strace benchmark\n'
rm -rf shadow.data
"$SHADOW_BIN" --strace-logging-mode=standard shadow_time_test.yaml > "$RESULTS_DIR/shadow_strace_run.log" 2>&1

STRACE_FILE="shadow.data/hosts/time-test-frequent/time_benchmark.1000.strace"
if [[ -f "$STRACE_FILE" ]]; then
  python3 scripts/analyze_strace.py "$STRACE_FILE" | tee "$RESULTS_DIR/strace_analysis.log"
else
  echo "missing Shadow strace: $STRACE_FILE" >&2
  exit 1
fi

{
  echo 'Time syscall microbenchmark report'
  echo '==================================='
  echo
  echo 'Native:'
  grep -A2 '测试1' "$RESULTS_DIR/native_test.log" || true
  echo
  echo 'Shadow:'
  grep -A2 '测试1' "$RESULTS_DIR/shadow_output.log" || true
  echo
  echo 'Strace clock_gettime:'
  grep 'clock_gettime' "$RESULTS_DIR/strace_analysis.log" || true
} > "$RESULTS_DIR/comparison_report.txt"

printf '\n[time-test] completed: %s\n' "$RESULTS_DIR/comparison_report.txt"
