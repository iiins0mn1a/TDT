#!/usr/bin/env python3
"""Run the local TDT correctness suite.

The final line printed by this script is intentionally only YES or NO. The
required suite covers real-client deterministic replay and synthetic
checkpoint/restore regressions. Shadow's own upstream tests are reference-only
and never affect the YES/NO result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any


TDT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = TDT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tdt_config  # noqa: E402


@dataclass(frozen=True)
class SuiteCase:
    name: str
    command: list[str]
    cwd: Path
    log_path: Path
    timeout_seconds: int
    result_path: Path | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local TDT correctness suite")
    parser.add_argument("--tdt-config", default="", help="TDT config path")
    parser.add_argument("--results-dir", default="/tmp/tdt-local-suite-results")
    parser.add_argument("--work-root", default="/tmp/tdt-local-suite")
    parser.add_argument("--skip-real", action="store_true")
    parser.add_argument("--skip-synthetic", action="store_true")
    parser.add_argument("--skip-performance", action="store_true")
    parser.add_argument("--with-shadow-reference", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--case-timeout", type=int, default=1800)
    parser.add_argument("--performance-timeout", type=int, default=2400)
    parser.add_argument("--shadow-test-timeout", type=int, default=900)
    parser.add_argument("--shadow-test-jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument(
        "--checkpoint-criu-jobs",
        type=int,
        default=0,
        help="Set SHADOW_CHECKPOINT_CRIU_JOBS for Shadow checkpoint dumps; 0 keeps the environment/default",
    )
    return parser.parse_args()


def default_config_path() -> Path:
    env_config = os.environ.get("TDT_CONFIG")
    if env_config:
        return Path(env_config).resolve()
    up_to_date = TDT_ROOT / "tdt_config.up_to_date.toml"
    if up_to_date.exists():
        return up_to_date
    local = TDT_ROOT / "tdt_config.local.toml"
    if local.exists():
        return local
    return TDT_ROOT / "tdt_config.toml"


def run_command(case: SuiteCase, env: dict[str, str]) -> dict[str, Any]:
    case.log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    timed_out = False
    returncode: int | None = None
    with case.log_path.open("wb") as log_file:
        try:
            proc = subprocess.run(
                case.command,
                cwd=case.cwd,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=case.timeout_seconds,
                check=False,
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = None
    elapsed = time.perf_counter() - started
    return {
        "name": case.name,
        "command": case.command,
        "cwd": str(case.cwd),
        "log_path": str(case.log_path),
        "elapsed_seconds": elapsed,
        "timeout_seconds": case.timeout_seconds,
        "timed_out": timed_out,
        "returncode": returncode,
    }


def write_local_experiment_config(tdt_config_path: Path, results_dir: Path, work_root: Path) -> Path:
    path = results_dir / "local-suite-experiment.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "[base]",
                f'tdt_config = "{tdt_config_path}"',
                f'results_dir = "{results_dir / "checkpoint-study"}"',
                f'work_root = "{work_root / "checkpoint-study"}"',
                "",
                "[experiment]",
                "setups = [1, 4, 8]",
                "validators_per_beacon = 4",
                "warmup_step_seconds = 60",
                "max_warmup_seconds = 1200",
                "settle_seconds = 60",
                "comparison_window_seconds = 120",
                "performance_trials = 3",
                'managed_external_paths = ["network", "beacon_peers.txt"]',
                'checkpoint_label_prefix = "checkpoint_study"',
                "hex_normalization = true",
                'restore_protocol_mode = "deterministic_v2"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def build_real_client_cases(config_path: Path, results_dir: Path, work_root: Path, timeout: int) -> list[SuiteCase]:
    study_runner = TDT_ROOT / "experiments/checkpoint-study/run_study.py"
    experiment_config = write_local_experiment_config(config_path, results_dir, work_root)
    cases: list[SuiteCase] = []
    for setup in (1, 4, 8):
        case_results = results_dir / "real-client" / f"setup-{setup}"
        cases.append(
            SuiteCase(
                name=f"real-client-determinism-setup-{setup}",
                command=[
                    sys.executable,
                    str(study_runner),
                    "--config",
                    str(experiment_config),
                    "--mode",
                    "determinism",
                    "--setup",
                    str(setup),
                    "--results-dir",
                    str(case_results),
                    "--work-root",
                    str(work_root / "real-client" / f"setup-{setup}"),
                ],
                cwd=TDT_ROOT,
                log_path=results_dir / "logs" / f"real-client-setup-{setup}.log",
                timeout_seconds=timeout,
                result_path=case_results / f"determinism-setup-{setup}.json",
            )
        )
    return cases


def build_synthetic_cases(config: tdt_config.TdtConfig, results_dir: Path, work_root: Path, timeout: int) -> list[SuiteCase]:
    synthetic = TDT_ROOT / "experiments/synthetic"
    shadow_bin = str(config.shadow_bin)
    common = ["--shadow-bin", shadow_bin, "--clean-data"]
    return [
        SuiteCase(
            name="synthetic-multihost-full",
            command=[
                sys.executable,
                str(synthetic / "checkpoint-network-multihost/orchestrator_verify.py"),
                *common,
                "--config",
                str(synthetic / "checkpoint-network-multihost/shadow_network.yaml"),
                "--work-dir",
                str(work_root / "synthetic/multihost-full"),
                "--verify-label",
                "cp_network_verify",
            ],
            cwd=synthetic,
            log_path=results_dir / "logs/synthetic-multihost-full.log",
            timeout_seconds=timeout,
        ),
        SuiteCase(
            name="synthetic-multihost-tcp",
            command=[
                sys.executable,
                str(synthetic / "checkpoint-network-multihost/orchestrator_verify.py"),
                *common,
                "--config",
                str(synthetic / "checkpoint-network-multihost/shadow_tcp_only.yaml"),
                "--work-dir",
                str(work_root / "synthetic/multihost-tcp"),
                "--verify-label",
                "cp_tcp_only",
                "--mode",
                "tcp",
                "--post-restore-step-ns",
                "1000000000",
                "--post-restore-steps",
                "10",
            ],
            cwd=synthetic,
            log_path=results_dir / "logs/synthetic-multihost-tcp.log",
            timeout_seconds=timeout,
        ),
        SuiteCase(
            name="synthetic-eth-poc",
            command=[
                sys.executable,
                str(synthetic / "checkpoint-network-eth-poc/orchestrator_verify.py"),
                *common,
                "--config",
                str(synthetic / "checkpoint-network-eth-poc/shadow_eth_poc.yaml"),
                "--work-dir",
                str(work_root / "synthetic/eth-poc"),
                "--verify-label",
                "cp_eth_poc_verify",
            ],
            cwd=synthetic,
            log_path=results_dir / "logs/synthetic-eth-poc.log",
            timeout_seconds=timeout,
        ),
        SuiteCase(
            name="synthetic-eth-multiproc",
            command=[
                sys.executable,
                str(synthetic / "checkpoint-network-eth-multiproc/orchestrator_verify.py"),
                *common,
                "--config",
                str(synthetic / "checkpoint-network-eth-multiproc/shadow_eth_multiproc.yaml"),
                "--work-dir",
                str(work_root / "synthetic/eth-multiproc"),
                "--verify-label",
                "cp_eth_multiproc_verify",
            ],
            cwd=synthetic,
            log_path=results_dir / "logs/synthetic-eth-multiproc.log",
            timeout_seconds=timeout,
        ),
        SuiteCase(
            name="synthetic-eth-shadowyaml-stable",
            command=[
                sys.executable,
                str(synthetic / "checkpoint-network-eth-shadowyaml/orchestrator_verify.py"),
                *common,
                "--config",
                str(synthetic / "checkpoint-network-eth-shadowyaml/shadow_eth_shadowyaml.yaml"),
                "--work-dir",
                str(work_root / "synthetic/eth-shadowyaml-stable"),
                "--verify-label",
                "cp_eth_shadowyaml_verify",
                "--scenario",
                "stable",
            ],
            cwd=synthetic,
            log_path=results_dir / "logs/synthetic-eth-shadowyaml-stable.log",
            timeout_seconds=timeout,
        ),
        SuiteCase(
            name="synthetic-eth-shadowyaml-bootstrap",
            command=[
                sys.executable,
                str(synthetic / "checkpoint-network-eth-shadowyaml/orchestrator_verify.py"),
                *common,
                "--config",
                str(synthetic / "checkpoint-network-eth-shadowyaml/shadow_eth_shadowyaml.yaml"),
                "--work-dir",
                str(work_root / "synthetic/eth-shadowyaml-bootstrap"),
                "--verify-label",
                "cp_eth_shadowyaml_verify_bootstrap",
                "--scenario",
                "peer-bootstrap",
            ],
            cwd=synthetic,
            log_path=results_dir / "logs/synthetic-eth-shadowyaml-bootstrap.log",
            timeout_seconds=timeout,
        ),
    ]


def build_shadow_reference_case(config: tdt_config.TdtConfig, results_dir: Path, timeout: int, jobs: int) -> SuiteCase:
    shadow_root = config.shadow_bin.parents[3]
    return SuiteCase(
        name="reference-shadow-218",
        command=["./setup", "test", "-j", str(jobs), "-t", "120"],
        cwd=shadow_root,
        log_path=results_dir / "logs/reference-shadow-218.log",
        timeout_seconds=timeout,
    )


def build_performance_case(
    config_path: Path,
    results_dir: Path,
    work_root: Path,
    timeout: int,
    checkpoint_criu_jobs: int,
) -> SuiteCase:
    performance_work_root = work_root / "performance"
    longest_socket_path = (
        performance_work_root
        / "checkpoint-study"
        / "performance-setup-8-trial-1"
        / "control.sock"
    )
    if len(str(longest_socket_path)) >= 104:
        digest = hashlib.sha1(str(results_dir).encode("utf-8")).hexdigest()[:10]
        performance_work_root = Path("/tmp") / f"tdtpf-{digest}"

    command = [
        sys.executable,
        str(TDT_ROOT / "experiments/perf_model/run_perf_model.py"),
        "--tdt-config",
        str(config_path),
        "--setups",
        "1,4,8",
        "--trials",
        "1",
        "--results-dir",
        str(results_dir / "performance"),
        "--work-root",
        str(performance_work_root),
        "--timeout",
        str(timeout),
    ]
    if checkpoint_criu_jobs > 0:
        command.extend(["--checkpoint-criu-jobs", str(checkpoint_criu_jobs)])
    return SuiteCase(
        name="reference-performance-1-4-8",
        command=command,
        cwd=TDT_ROOT,
        log_path=results_dir / "logs/reference-performance-1-4-8.log",
        timeout_seconds=timeout,
        result_path=results_dir / "performance/perf-model.json",
    )


def evaluate_real_client_result(case_result: dict[str, Any], result_path: Path | None) -> tuple[bool, dict[str, Any]]:
    detail: dict[str, Any] = {}
    if case_result["returncode"] != 0 or case_result["timed_out"]:
        return False, detail
    if result_path is None or not result_path.exists():
        return False, {"error": f"missing result json: {result_path}"}
    data = json.loads(result_path.read_text(encoding="utf-8"))
    detail = {
        "result_path": str(result_path),
        "passed": data.get("passed"),
        "strict_passed": data.get("strict_passed"),
        "determinism_class": data.get("determinism_class"),
        "allowed_order_only_mismatches": len(data.get("allowed_order_only_mismatches", [])),
        "comparisons": len(data.get("comparisons", [])),
        "mismatches": len(data.get("first_mismatches", [])),
        "checkpoint_elapsed_ms": data.get("checkpoint_elapsed_ms"),
        "restore_elapsed_ms": data.get("restore_elapsed_ms"),
    }
    return bool(data.get("passed")) and not data.get("first_mismatches"), detail


def summarize_required(case: SuiteCase, raw: dict[str, Any]) -> dict[str, Any]:
    passed = raw["returncode"] == 0 and not raw["timed_out"]
    detail: dict[str, Any] = {}
    if case.name.startswith("real-client"):
        passed, detail = evaluate_real_client_result(raw, case.result_path)
    return {**raw, "passed": passed, "detail": detail}


def print_case(result: dict[str, Any], json_only: bool) -> None:
    if json_only:
        return
    status = "PASS" if result["passed"] else "FAIL"
    print(f"[{status}] {result['name']} elapsed={result['elapsed_seconds']:.2f}s log={result['log_path']}")


def write_outputs(results_dir: Path, suite: dict[str, Any]) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "suite-result.json").write_text(
        json.dumps(suite, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    results_dir = Path(args.results_dir).resolve()
    work_root = Path(args.work_root).resolve()
    config_path = Path(args.tdt_config).resolve() if args.tdt_config else default_config_path()
    config = tdt_config.load_tdt_config(config_path)

    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["CRIU_BIN"] = str(config.criu_bin)
    env.setdefault("SHADOW_RESTORE_PROTOCOL_MODE", config.checkpoint_restore.restore_protocol_mode)
    if config.simulation.fast_file_sync:
        env.setdefault("SHADOW_FAST_FILE_SYNC", "1")
    if args.checkpoint_criu_jobs > 0:
        env["SHADOW_CHECKPOINT_CRIU_JOBS"] = str(args.checkpoint_criu_jobs)

    required_cases: list[SuiteCase] = []
    if not args.skip_real:
        required_cases.extend(build_real_client_cases(config_path, results_dir, work_root, args.case_timeout))
    if not args.skip_synthetic:
        required_cases.extend(build_synthetic_cases(config, results_dir, work_root, args.case_timeout))

    required_results: list[dict[str, Any]] = []
    for case in required_cases:
        raw = run_command(case, env)
        result = summarize_required(case, raw)
        required_results.append(result)
        print_case(result, args.json_only)
        if args.fail_fast and not result["passed"]:
            break

    reference_result: dict[str, Any] = {"enabled": False, "passed": None}
    if args.with_shadow_reference:
        ref_case = build_shadow_reference_case(config, results_dir, args.shadow_test_timeout, args.shadow_test_jobs)
        raw = run_command(ref_case, env)
        reference_result = {**raw, "enabled": True, "passed": raw["returncode"] == 0 and not raw["timed_out"]}
        if not args.json_only:
            status = "PASS" if reference_result["passed"] else "FAIL"
            print(f"[{status}] {reference_result['name']} elapsed={reference_result['elapsed_seconds']:.2f}s log={reference_result['log_path']}")

    performance_result: dict[str, Any] = {"enabled": False, "passed": None}
    if not args.skip_performance:
        perf_case = build_performance_case(
            config_path,
            results_dir,
            work_root,
            args.performance_timeout,
            args.checkpoint_criu_jobs,
        )
        raw = run_command(perf_case, env)
        detail: dict[str, Any] = {}
        if perf_case.result_path is not None and perf_case.result_path.exists():
            perf_data = json.loads(perf_case.result_path.read_text(encoding="utf-8"))
            detail = {
                "result_path": str(perf_case.result_path),
                "report_path": str(results_dir / "performance/REPORT.md"),
                "summary": perf_data.get("summary", []),
            }
        performance_result = {
            **raw,
            "enabled": True,
            "passed": raw["returncode"] == 0 and not raw["timed_out"],
            "detail": detail,
        }
        if not args.json_only:
            status = "PASS" if performance_result["passed"] else "FAIL"
            print(
                f"[{status}] {performance_result['name']} "
                f"elapsed={performance_result['elapsed_seconds']:.2f}s "
                f"log={performance_result['log_path']}"
            )

    passed = bool(required_cases) and all(item["passed"] for item in required_results)
    answer = "YES" if passed else "NO"
    suite = {
        "passed": passed,
        "answer": answer,
        "tdt_config": str(config_path),
        "results_dir": str(results_dir),
        "work_root": str(work_root),
        "required": {
            "cases": required_results,
            "real_client_determinism": [x for x in required_results if x["name"].startswith("real-client")],
            "synthetic_cp_restore": [x for x in required_results if x["name"].startswith("synthetic")],
        },
        "reference": {
            "shadow_218": reference_result,
            "performance_1_4_8": performance_result,
        },
    }
    write_outputs(results_dir, suite)
    if args.json_only:
        print(json.dumps(suite, sort_keys=True))
    print(answer)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
