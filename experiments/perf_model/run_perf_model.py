#!/usr/bin/env python3
"""Build a first-pass performance model for TDT steady-state throughput.

This script intentionally does not change simulated application configuration.
It reuses the existing checkpoint-study runner in performance mode and reports
simulation-layer throughput signals that can guide later Shadow-only
optimization work.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


TDT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = TDT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tdt_config  # noqa: E402


CONTROL_CMD_RE = re.compile(r'Control socket received: Request \{ cmd: "([^"]+)"')
CHECKPOINT_PHASE_RE = re.compile(
    r"phase timings: freeze_ms=(?P<freeze>[0-9.]+) shmem_ms=(?P<shmem>[0-9.]+) "
    r"snapshot_ms=(?P<snapshot>[0-9.]+) audit_ms=(?P<audit>[0-9.]+) "
    r"criu_ms=(?P<criu>[0-9.]+) json_ms=(?P<json>[0-9.]+) total_ms=(?P<total>[0-9.]+)"
)
RESTORE_PHASE_RE = re.compile(
    r"phase timings: load_ms=(?P<load>[0-9.]+) shmem_ms=(?P<shmem>[0-9.]+) "
    r"host_shmem_ms=(?P<host_shmem>[0-9.]+) criu_ms=(?P<criu>[0-9.]+) total_ms=(?P<total>[0-9.]+)"
)
SCHEDULER_COUNTER_RE = re.compile(
    r"TDT scheduler counters: parallelism=(?P<parallelism>[0-9]+) "
    r"windows=(?P<windows>[0-9]+) "
    r"host_scans=(?P<host_scans>[0-9]+) "
    r"host_executes=(?P<host_executes>[0-9]+) "
    r"host_scans_per_execute=(?P<host_scans_per_execute>[0-9.]+) "
    r"scheduler_scope_wall_ns=(?P<scheduler_scope_wall_ns>[0-9]+) "
    r"host_execute_wall_ns=(?P<host_execute_wall_ns>[0-9]+) "
    r"worker_busy_percent=(?P<worker_busy_percent>[0-9.]+) "
    r"window_max_worker_body_wall_ns=(?P<window_max_worker_body_wall_ns>[0-9]+) "
    r"scheduler_scope_over_window_max_percent=(?P<scheduler_scope_over_window_max_percent>[0-9.]+) "
    r"(?:window_max_worker_body_continue_receive_wall_ns=(?P<window_max_worker_body_continue_receive_wall_ns>[0-9]+) "
    r"estimated_async_continue_overlap_savings_ns=(?P<estimated_async_continue_overlap_savings_ns>[0-9]+) "
    r"estimated_async_continue_overlap_savings_percent=(?P<estimated_async_continue_overlap_savings_percent>[0-9.]+) )?"
    r"(?:async_scope_drain_hosts=(?P<async_scope_drain_hosts>[0-9]+) "
    r"async_scope_reenter_opportunities=(?P<async_scope_reenter_opportunities>[0-9]+) "
    r"async_scope_drain_wall_ns=(?P<async_scope_drain_wall_ns>[0-9]+) "
    r"(?:async_boundary_pending_hosts=(?P<async_boundary_pending_hosts>[0-9]+) "
    r"async_boundary_pending_continuations=(?P<async_boundary_pending_continuations>[0-9]+) )?)?"
    r"packet_events=(?P<packet_events>[0-9]+) "
    r"local_events=(?P<local_events>[0-9]+) "
    r"cpu_delayed_events=(?P<cpu_delayed_events>[0-9]+) "
    r"packet_event_wall_ns=(?P<packet_event_wall_ns>[0-9]+) "
    r"local_event_wall_ns=(?P<local_event_wall_ns>[0-9]+) "
    r"resume_process_events=(?P<resume_process_events>[0-9]+) "
    r"resume_process_wall_ns=(?P<resume_process_wall_ns>[0-9]+) "
    r"start_application_events=(?P<start_application_events>[0-9]+) "
    r"start_application_wall_ns=(?P<start_application_wall_ns>[0-9]+) "
    r"shutdown_process_events=(?P<shutdown_process_events>[0-9]+) "
    r"shutdown_process_wall_ns=(?P<shutdown_process_wall_ns>[0-9]+) "
    r"relay_forward_events=(?P<relay_forward_events>[0-9]+) "
    r"relay_forward_wall_ns=(?P<relay_forward_wall_ns>[0-9]+) "
    r"syscall_condition_wake_events=(?P<syscall_condition_wake_events>[0-9]+) "
    r"syscall_condition_wake_wall_ns=(?P<syscall_condition_wake_wall_ns>[0-9]+) "
    r"prepare_poll_timeout_completion_events=(?P<prepare_poll_timeout_completion_events>[0-9]+) "
    r"prepare_poll_timeout_completion_wall_ns=(?P<prepare_poll_timeout_completion_wall_ns>[0-9]+) "
    r"restore_blocked_syscall_condition_events=(?P<restore_blocked_syscall_condition_events>[0-9]+) "
    r"restore_blocked_syscall_condition_wall_ns=(?P<restore_blocked_syscall_condition_wall_ns>[0-9]+) "
    r"timer_expire_events=(?P<timer_expire_events>[0-9]+) "
    r"timer_expire_wall_ns=(?P<timer_expire_wall_ns>[0-9]+) "
    r"legacy_tcp_deferred_events=(?P<legacy_tcp_deferred_events>[0-9]+) "
    r"legacy_tcp_deferred_wall_ns=(?P<legacy_tcp_deferred_wall_ns>[0-9]+) "
    r"exec_continuation_events=(?P<exec_continuation_events>[0-9]+) "
    r"exec_continuation_wall_ns=(?P<exec_continuation_wall_ns>[0-9]+) "
    r"opaque_events=(?P<opaque_events>[0-9]+) "
    r"opaque_wall_ns=(?P<opaque_wall_ns>[0-9]+) "
    r"undescribed_events=(?P<undescribed_events>[0-9]+) "
    r"undescribed_wall_ns=(?P<undescribed_wall_ns>[0-9]+) "
    r"worker_bodies=(?P<worker_bodies>[0-9]+) "
    r"worker_body_wall_ns=(?P<worker_body_wall_ns>[0-9]+) "
    r"avg_worker_body_wall_ns=(?P<avg_worker_body_wall_ns>[0-9.]+) "
    r"max_worker_body_wall_ns=(?P<max_worker_body_wall_ns>[0-9]+) "
    r"worker_body_max_to_avg=(?P<worker_body_max_to_avg>[0-9.]+)"
    r"(?: worker_body_continue_receive_wall_ns=(?P<worker_body_continue_receive_wall_ns>[0-9]+) "
    r"max_worker_body_continue_receive_wall_ns=(?P<max_worker_body_continue_receive_wall_ns>[0-9]+))?"
    r"(?: top_hosts=(?P<top_hosts>.*?) top_worker_bodies=(?P<top_worker_bodies>.*))?"
)
SCHEDULER_TOP_HOST_RE = re.compile(
    r"HostId\((?P<host_id>[0-9]+)\):"
    r"name=(?P<name>[^:]+):"
    r"count=(?P<count>[0-9]+):"
    r"wall_ms=(?P<wall_ms>[0-9.]+):"
    r"syscall_wake_ms=(?P<syscall_wake_ms>[0-9.]+)"
)
SCHEDULER_TOP_WORKER_BODY_RE = re.compile(
    r"window=(?P<window>[0-9]+):"
    r"thread=(?P<thread>[0-9]+):"
    r"body_ms=(?P<body_ms>[0-9.]+):"
    r"(?:(?:continue_receive_ms=(?P<continue_receive_ms>[0-9.]+):"
    r"continue_receive_pct=(?P<continue_receive_pct>[0-9.]+):))?"
    r"host_scans=(?P<host_scans>[0-9]+):"
    r"host_executes=(?P<host_executes>[0-9]+):"
    r"host=(?P<host_id>HostId\([0-9]+\)|none)"
    r"(?::name=(?P<name>[^:]+):"
    r"host_count=(?P<host_count>[0-9]+):"
    r"host_wall_ms=(?P<host_wall_ms>[0-9.]+):"
    r"host_syscall_wake_ms=(?P<host_syscall_wake_ms>[0-9.]+))?"
)
NETWORK_COUNTER_RE = re.compile(
    r"TDT network counters: packet_pushes=(?P<packet_pushes>[0-9]+) "
    r"cross_host_packet_pushes=(?P<cross_host_packet_pushes>[0-9]+) "
    r"event_queue_lock_wait_ns=(?P<event_queue_lock_wait_ns>[0-9]+) "
    r"avg_event_queue_lock_wait_ns=(?P<avg_event_queue_lock_wait_ns>[0-9.]+) "
    r"max_event_queue_lock_wait_ns=(?P<max_event_queue_lock_wait_ns>[0-9]+) "
    r"avg_event_queue_len_after=(?P<avg_event_queue_len_after>[0-9.]+) "
    r"max_event_queue_len_after=(?P<max_event_queue_len_after>[0-9]+)"
)
MANAGED_THREAD_COUNTER_RE = re.compile(
    r"TDT managed-thread counters: continue_plugin_calls=(?P<continue_plugin_calls>[0-9]+) "
    r"continue_plugin_wall_ns=(?P<continue_plugin_wall_ns>[0-9]+) "
    r"continue_plugin_receive_wall_ns=(?P<continue_plugin_receive_wall_ns>[0-9]+) "
    r"continue_plugin_lock_wall_ns=(?P<continue_plugin_lock_wall_ns>[0-9]+) "
    r"(?:continue_plugin_prepare_wall_ns=(?P<continue_plugin_prepare_wall_ns>[0-9]+) "
    r"(?:continue_plugin_runahead_wall_ns=(?P<continue_plugin_runahead_wall_ns>[0-9]+) "
    r"continue_plugin_clock_state_wall_ns=(?P<continue_plugin_clock_state_wall_ns>[0-9]+) "
    r"continue_plugin_unlock_wall_ns=(?P<continue_plugin_unlock_wall_ns>[0-9]+) )?"
    r"continue_plugin_send_wall_ns=(?P<continue_plugin_send_wall_ns>[0-9]+) "
    r"continue_plugin_time_update_wall_ns=(?P<continue_plugin_time_update_wall_ns>[0-9]+) )?"
    r"syscall_handler_calls=(?P<syscall_handler_calls>[0-9]+) "
    r"syscall_handler_wall_ns=(?P<syscall_handler_wall_ns>[0-9]+) "
    r"syscall_continue_calls=(?P<syscall_continue_calls>[0-9]+) "
    r"syscall_continue_wall_ns=(?P<syscall_continue_wall_ns>[0-9]+)"
    r"(?: syscall_top=(?P<syscall_top>.*?)(?: continue_exchange_top=(?P<continue_exchange_top>.*))?)?\s*$"
)
CONTINUE_EXCHANGE_ITEM_RE = re.compile(
    r"(?P<sent>[A-Za-z]+)->(?P<received>[A-Za-z]+):"
    r"calls=(?P<calls>[0-9]+):"
    r"wall_ms=(?P<wall_ms>[0-9.]+):"
    r"receive_ms=(?P<receive_ms>[0-9.]+)"
)
SYSCALL_TOP_ITEM_RE = re.compile(
    r"(?P<name>[^,(]+)\((?P<number>[0-9]+)\):"
    r"handler_ms=(?P<handler_ms>[0-9.]+):"
    r"continue_ms=(?P<continue_ms>[0-9.]+):"
    r"continue_avg_ns=(?P<continue_avg_ns>[0-9.]+):"
    r"handler_calls=(?P<handler_calls>[0-9]+):"
    r"continue_calls=(?P<continue_calls>[0-9]+):"
    r"done=(?P<done>[0-9]+):"
    r"block=(?P<block>[0-9]+):"
    r"native=(?P<native>[0-9]+):"
    r"synthetic=(?P<synthetic>[0-9]+)"
    r"(?::fd_top=(?P<fd_top>[^,:]*))?"
    r"(?::fd_kind_top=(?P<fd_kind_top>[^,]*))?"
)
SYSCALL_CONDITION_COUNTER_RE = re.compile(
    r"TDT syscall-condition counters: schedule_attempts=(?P<schedule_attempts>[0-9]+) "
    r"scheduled_wakeups=(?P<scheduled_wakeups>[0-9]+) "
    r"skipped_already_scheduled=(?P<skipped_already_scheduled>[0-9]+) "
    r"trigger_enters=(?P<trigger_enters>[0-9]+) "
    r"trigger_continues=(?P<trigger_continues>[0-9]+) "
    r"trigger_reblocks=(?P<trigger_reblocks>[0-9]+) "
    r"trigger_missing_process=(?P<trigger_missing_process>[0-9]+) "
    r"trigger_stopped_process=(?P<trigger_stopped_process>[0-9]+) "
    r"trigger_missing_thread=(?P<trigger_missing_thread>[0-9]+) "
    r"notify_status_changed=(?P<notify_status_changed>[0-9]+) "
    r"notify_timeout_expired=(?P<notify_timeout_expired>[0-9]+) "
    r"signal_wakeups_scheduled=(?P<signal_wakeups_scheduled>[0-9]+) "
    r"signal_wakeups_blocked=(?P<signal_wakeups_blocked>[0-9]+)"
    r"(?: trigger_lookup_wall_ns=(?P<trigger_lookup_wall_ns>[0-9]+) "
    r"satisfied_check_wall_ns=(?P<satisfied_check_wall_ns>[0-9]+) "
    r"host_continue_wall_ns=(?P<host_continue_wall_ns>[0-9]+) "
    r"wake_continue_wall_ns=(?P<wake_continue_wall_ns>[0-9]+) "
    r"wake_reblock_wall_ns=(?P<wake_reblock_wall_ns>[0-9]+))?"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TDT performance-model probes")
    parser.add_argument("--tdt-config", default="")
    parser.add_argument("--setups", default="1,4,8")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--results-dir", default="/tmp/tdt-perf-model-results")
    parser.add_argument("--work-root", default="/tmp/tpfm")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument(
        "--perf-counters",
        choices=("on", "off"),
        default="on",
        help="Enable detailed Shadow TDT perf counters. Use 'off' for cleaner throughput probes.",
    )
    parser.add_argument(
        "--checkpoint-criu-jobs",
        type=int,
        default=0,
        help="Set SHADOW_CHECKPOINT_CRIU_JOBS for Shadow checkpoint dumps; 0 keeps the environment/default",
    )
    return parser.parse_args()


def host_load_snapshot() -> dict[str, Any]:
    """Capture lightweight host-load context for judging perf-sample noise."""
    snapshot: dict[str, Any] = {}
    try:
        snapshot["loadavg"] = list(os.getloadavg())
    except OSError:
        snapshot["loadavg"] = None

    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid,ppid,pcpu,pmem,comm,args", "--sort=-pcpu"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        lines = proc.stdout.splitlines()
        snapshot["top_processes"] = lines[:11]
    except Exception as e:
        snapshot["top_processes_error"] = str(e)

    return snapshot


def validate_control_socket_path(work_root: Path, setup: int) -> None:
    """Fail early if the generated Shadow control socket path is too long."""
    socket_path = (
        work_root
        / "checkpoint-study"
        / f"performance-setup-{setup}-trial-1"
        / "control.sock"
    )
    # Linux sockaddr_un sun_path is usually 108 bytes including the terminator.
    # Keep a little slack so the failure points to this runner rather than Shadow.
    if len(str(socket_path)) >= 104:
        raise ValueError(
            "Generated control socket path is too long for Unix domain sockets: "
            f"{socket_path} ({len(str(socket_path))} bytes). Use a shorter --work-root."
        )


def default_config_path() -> Path:
    local = TDT_ROOT / "tdt_config.local.toml"
    if local.exists():
        return local
    return TDT_ROOT / "tdt_config.toml"


def selected_setups(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def write_experiment_config(tdt_config_path: Path, results_dir: Path, work_root: Path, setups: list[int]) -> Path:
    path = results_dir / "perf-model-experiment.toml"
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
                f"setups = {setups}",
                "validators_per_beacon = 4",
                "warmup_step_seconds = 60",
                "max_warmup_seconds = 1200",
                "settle_seconds = 60",
                "comparison_window_seconds = 120",
                "performance_trials = 1",
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


def run_performance(
    config_path: Path,
    results_dir: Path,
    work_root: Path,
    setup: int,
    trials: int,
    timeout: int,
    env: dict[str, str],
) -> dict[str, Any]:
    runner = TDT_ROOT / "experiments/checkpoint-study/run_study.py"
    log_path = results_dir / "logs" / f"performance-setup-{setup}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("wb") as log_file:
        proc = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--config",
                str(config_path),
                "--mode",
                "performance",
                "--setup",
                str(setup),
                "--trials",
                str(trials),
                "--results-dir",
                str(results_dir / "checkpoint-study"),
                "--work-root",
                str(work_root / "checkpoint-study"),
            ],
            cwd=TDT_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    elapsed = time.perf_counter() - started
    result_path = results_dir / "checkpoint-study" / f"performance-setup-{setup}.json"
    data: dict[str, Any] = {}
    if result_path.exists():
        data = json.loads(result_path.read_text(encoding="utf-8"))
    shadow_logs = [
        str(work_root / "checkpoint-study" / f"performance-setup-{setup}-trial-{trial}" / "performance.log")
        for trial in range(1, trials + 1)
    ]
    return {
        "setup": setup,
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "log_path": str(log_path),
        "shadow_log_paths": shadow_logs,
        "result_path": str(result_path),
        "data": data,
    }


def count_control_commands(log_paths: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_path in log_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = CONTROL_CMD_RE.search(line)
                if match:
                    cmd = match.group(1)
                    counts[cmd] = counts.get(cmd, 0) + 1
    return dict(sorted(counts.items()))


def checkpoint_phase_timings(log_paths: list[str]) -> list[dict[str, float]]:
    timings = []
    for raw_path in log_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = CHECKPOINT_PHASE_RE.search(line)
                if match:
                    timings.append({key: float(value) for key, value in match.groupdict().items()})
    return timings


def restore_phase_timings(log_paths: list[str]) -> list[dict[str, float]]:
    timings = []
    for raw_path in log_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = RESTORE_PHASE_RE.search(line)
                if match:
                    timings.append({key: float(value) for key, value in match.groupdict().items()})
    return timings


def scheduler_counters(log_paths: list[str]) -> list[dict[str, Any]]:
    counters = []
    for raw_path in log_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = SCHEDULER_COUNTER_RE.search(line)
                if match:
                    groups = match.groupdict()
                    top_hosts = groups.pop("top_hosts", None)
                    top_worker_bodies = groups.pop("top_worker_bodies", None)
                    parsed: dict[str, Any] = {
                        key: float(value) for key, value in groups.items() if value is not None
                    }
                    if top_hosts:
                        parsed["top_hosts"] = parse_scheduler_top_hosts(top_hosts)
                    if top_worker_bodies:
                        parsed["top_worker_bodies"] = parse_scheduler_top_worker_bodies(
                            top_worker_bodies
                        )
                    counters.append(parsed)
    return counters


def parse_scheduler_top_hosts(raw: str) -> list[dict[str, Any]]:
    items = []
    for part in raw.split(","):
        match = SCHEDULER_TOP_HOST_RE.fullmatch(part.strip())
        if not match:
            continue
        groups = match.groupdict()
        items.append(
            {
                "host_id": int(groups["host_id"]),
                "name": groups["name"],
                "count": int(groups["count"]),
                "wall_ms": float(groups["wall_ms"]),
                "syscall_wake_ms": float(groups["syscall_wake_ms"]),
            }
        )
    return items


def parse_scheduler_top_worker_bodies(raw: str) -> list[dict[str, Any]]:
    items = []
    for part in raw.split(","):
        match = SCHEDULER_TOP_WORKER_BODY_RE.fullmatch(part.strip())
        if not match:
            continue
        groups = match.groupdict()
        item: dict[str, Any] = {
            "window": int(groups["window"]),
            "thread": int(groups["thread"]),
            "body_ms": float(groups["body_ms"]),
            "host_scans": int(groups["host_scans"]),
            "host_executes": int(groups["host_executes"]),
            "host_id": groups["host_id"],
            "name": groups.get("name") or "",
        }
        for key in ("host_count", "host_wall_ms", "host_syscall_wake_ms"):
            value = groups.get(key)
            if value is not None:
                item[key] = float(value) if "." in value else int(value)
        for key in ("continue_receive_ms", "continue_receive_pct"):
            value = groups.get(key)
            if value is not None:
                item[key] = float(value)
        items.append(item)
    return items


def network_counters(log_paths: list[str]) -> list[dict[str, float]]:
    counters = []
    for raw_path in log_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = NETWORK_COUNTER_RE.search(line)
                if match:
                    counters.append({key: float(value) for key, value in match.groupdict().items()})
    return counters


def managed_thread_counters(log_paths: list[str]) -> list[dict[str, float]]:
    counters = []
    for raw_path in log_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = MANAGED_THREAD_COUNTER_RE.search(line)
                if match:
                    groups = match.groupdict()
                    syscall_top = groups.pop("syscall_top", None)
                    continue_exchange_top = groups.pop("continue_exchange_top", None)
                    parsed: dict[str, Any] = {
                        key: float(value) for key, value in groups.items() if value is not None
                    }
                    if syscall_top:
                        parsed["syscall_top"] = parse_syscall_top(syscall_top)
                    if continue_exchange_top:
                        parsed["continue_exchange_top"] = parse_continue_exchange_top(
                            continue_exchange_top
                        )
                    counters.append(parsed)
    return counters


def parse_continue_exchange_top(raw: str) -> list[dict[str, Any]]:
    items = []
    for part in raw.split(","):
        match = CONTINUE_EXCHANGE_ITEM_RE.fullmatch(part.strip())
        if not match:
            continue
        groups = match.groupdict()
        items.append(
            {
                "sent": groups["sent"],
                "received": groups["received"],
                "calls": int(groups["calls"]),
                "wall_ms": float(groups["wall_ms"]),
                "receive_ms": float(groups["receive_ms"]),
            }
        )
    return items


def parse_syscall_top(raw: str) -> list[dict[str, Any]]:
    items = []
    for part in raw.split(","):
        match = SYSCALL_TOP_ITEM_RE.fullmatch(part.strip())
        if not match:
            continue
        groups = match.groupdict()
        item: dict[str, Any] = {"name": groups["name"], "number": int(groups["number"])}
        for key in (
            "handler_ms",
            "continue_ms",
            "continue_avg_ns",
            "handler_calls",
            "continue_calls",
            "done",
            "block",
            "native",
            "synthetic",
        ):
            value = groups[key]
            item[key] = float(value) if "." in value else int(value)
        fd_top = groups.get("fd_top")
        if fd_top:
            item["fd_top"] = [
                {"fd": int(fd), "calls": int(count)}
                for fd, count in (entry.split("=", 1) for entry in fd_top.split(";") if entry)
            ]
        fd_kind_top = groups.get("fd_kind_top")
        if fd_kind_top:
            item["fd_kind_top"] = [
                {"kind": kind, "calls": int(count)}
                for kind, count in (
                    entry.split("=", 1) for entry in fd_kind_top.split(";") if entry
                )
            ]
        items.append(item)
    return items


def syscall_condition_counters(log_paths: list[str]) -> list[dict[str, float]]:
    counters = []
    for raw_path in log_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = SYSCALL_CONDITION_COUNTER_RE.search(line)
                if match:
                    counters.append({key: float(value) for key, value in match.groupdict().items()})
    return counters


def median_value(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def median_phase_value(phase_timings: list[dict[str, float]], key: str) -> float | None:
    return median_value([timing[key] for timing in phase_timings if key in timing])


def ms_to_seconds(value: float | int | None) -> float:
    return 0.0 if value is None else float(value) / 1000.0


def median_ns_per_event(stats: list[dict[str, float]], event_key: str, wall_key: str) -> float | None:
    event_count = median_phase_value(stats, event_key)
    wall_ns = median_phase_value(stats, wall_key)
    if event_count is None or wall_ns is None or event_count == 0:
        return None
    return wall_ns / event_count


def simulated_seconds(data: dict[str, Any]) -> float | None:
    trials = data.get("trials") or []
    if not trials:
        return None
    total = 0.0
    for trial in trials:
        total += float(trial.get("ready_elapsed_seconds") or 0)
        total += 60.0  # settle_seconds from write_experiment_config()
        total += 120.0  # comparison_window_seconds from write_experiment_config()
        total += 60.0  # post-restore probe window
    return total


def summarize_case(case: dict[str, Any]) -> dict[str, Any]:
    data = case.get("data") or {}
    summary = data.get("summary") or {}
    trials = data.get("trials") or []
    first_trial = trials[0] if trials else {}
    logical_probe_seconds = 60
    checkpoint_sizes = first_trial.get("checkpoint_sizes") or {}
    sim_seconds = simulated_seconds(data)
    elapsed_seconds = float(case["elapsed_seconds"])
    phase_timings = checkpoint_phase_timings(case.get("shadow_log_paths") or [])
    restore_timings = restore_phase_timings(case.get("shadow_log_paths") or [])
    scheduler_stats = scheduler_counters(case.get("shadow_log_paths") or [])
    network_stats = network_counters(case.get("shadow_log_paths") or [])
    managed_thread_stats = managed_thread_counters(case.get("shadow_log_paths") or [])
    syscall_condition_stats = syscall_condition_counters(case.get("shadow_log_paths") or [])
    checkpoint_ms = summary.get("checkpoint_ms_median")
    restore_ms = summary.get("restore_ms_median")
    managed_backup_ms = first_trial.get("managed_external_backup_ms")
    managed_restore_ms = first_trial.get("managed_external_restore_ms")
    cp_restore_wall_seconds = (
        ms_to_seconds(checkpoint_ms)
        + ms_to_seconds(restore_ms)
        + ms_to_seconds(managed_backup_ms)
        + ms_to_seconds(managed_restore_ms)
    )
    steady_wall_seconds = max(elapsed_seconds - cp_restore_wall_seconds, 0.0)
    worker_body_wall_ns = median_phase_value(scheduler_stats, "worker_body_wall_ns")
    worker_body_continue_receive_wall_ns = median_phase_value(
        scheduler_stats, "worker_body_continue_receive_wall_ns"
    )
    max_worker_body_wall_ns = median_phase_value(scheduler_stats, "max_worker_body_wall_ns")
    max_worker_body_continue_receive_wall_ns = median_phase_value(
        scheduler_stats, "max_worker_body_continue_receive_wall_ns"
    )
    summary_out = {
        "setup": case["setup"],
        "passed": case["returncode"] == 0 and bool(data),
        "elapsed_seconds": elapsed_seconds,
        "simulated_seconds": sim_seconds,
        "simulated_seconds_per_wall_second": None
        if sim_seconds is None or elapsed_seconds == 0
        else sim_seconds / elapsed_seconds,
        "checkpoint_ms_median": checkpoint_ms,
        "restore_ms_median": restore_ms,
        "managed_external_backup_ms": managed_backup_ms,
        "managed_external_restore_ms": managed_restore_ms,
        "cp_restore_wall_seconds": cp_restore_wall_seconds,
        "cp_restore_wall_percent": None
        if elapsed_seconds == 0
        else (cp_restore_wall_seconds / elapsed_seconds) * 100.0,
        "steady_wall_seconds_estimate": steady_wall_seconds,
        "steady_simulated_seconds_per_wall_second": None
        if sim_seconds is None or steady_wall_seconds == 0
        else sim_seconds / steady_wall_seconds,
        "checkpoint_bundle_bytes": checkpoint_sizes.get("checkpoint_bundle_bytes"),
        "managed_external_bundle_bytes": first_trial.get("managed_external_bundle_bytes"),
        "post_restore_probe_seconds": logical_probe_seconds,
        "control_command_counts": count_control_commands(case.get("shadow_log_paths") or []),
        "checkpoint_phase_timings": phase_timings,
        "restore_phase_timings": restore_timings,
        "scheduler_counters": scheduler_stats,
        "scheduler_top_hosts": next(
            (
                stat["top_hosts"]
                for stat in reversed(scheduler_stats)
                if stat.get("top_hosts")
            ),
            [],
        ),
        "scheduler_top_worker_bodies": next(
            (
                stat["top_worker_bodies"]
                for stat in reversed(scheduler_stats)
                if stat.get("top_worker_bodies")
            ),
            [],
        ),
        "scheduler_parallelism": median_phase_value(scheduler_stats, "parallelism"),
        "scheduler_windows": median_phase_value(scheduler_stats, "windows"),
        "scheduler_host_scans": median_phase_value(scheduler_stats, "host_scans"),
        "scheduler_host_executes": median_phase_value(scheduler_stats, "host_executes"),
        "scheduler_host_scans_per_execute": median_phase_value(scheduler_stats, "host_scans_per_execute"),
        "scheduler_worker_busy_percent": median_phase_value(scheduler_stats, "worker_busy_percent"),
        "scheduler_window_max_worker_body_wall_ms": None
        if median_phase_value(scheduler_stats, "window_max_worker_body_wall_ns") is None
        else median_phase_value(scheduler_stats, "window_max_worker_body_wall_ns") / 1_000_000.0,
        "scheduler_scope_over_window_max_percent": median_phase_value(scheduler_stats, "scheduler_scope_over_window_max_percent"),
        "scheduler_window_max_worker_body_continue_receive_wall_ms": None
        if median_phase_value(scheduler_stats, "window_max_worker_body_continue_receive_wall_ns") is None
        else median_phase_value(scheduler_stats, "window_max_worker_body_continue_receive_wall_ns") / 1_000_000.0,
        "scheduler_estimated_async_continue_overlap_savings_ms": None
        if median_phase_value(scheduler_stats, "estimated_async_continue_overlap_savings_ns") is None
        else median_phase_value(scheduler_stats, "estimated_async_continue_overlap_savings_ns") / 1_000_000.0,
        "scheduler_estimated_async_continue_overlap_savings_percent": median_phase_value(
            scheduler_stats, "estimated_async_continue_overlap_savings_percent"
        ),
        "scheduler_async_scope_drain_hosts": median_phase_value(
            scheduler_stats, "async_scope_drain_hosts"
        ),
        "scheduler_async_scope_reenter_opportunities": median_phase_value(
            scheduler_stats, "async_scope_reenter_opportunities"
        ),
        "scheduler_async_scope_drain_wall_ms": None
        if median_phase_value(scheduler_stats, "async_scope_drain_wall_ns") is None
        else median_phase_value(scheduler_stats, "async_scope_drain_wall_ns") / 1_000_000.0,
        "scheduler_async_boundary_pending_hosts": median_phase_value(
            scheduler_stats, "async_boundary_pending_hosts"
        ),
        "scheduler_async_boundary_pending_continuations": median_phase_value(
            scheduler_stats, "async_boundary_pending_continuations"
        ),
        "scheduler_packet_events": median_phase_value(scheduler_stats, "packet_events"),
        "scheduler_local_events": median_phase_value(scheduler_stats, "local_events"),
        "scheduler_cpu_delayed_events": median_phase_value(scheduler_stats, "cpu_delayed_events"),
        "scheduler_packet_event_wall_ms": None
        if median_phase_value(scheduler_stats, "packet_event_wall_ns") is None
        else median_phase_value(scheduler_stats, "packet_event_wall_ns") / 1_000_000.0,
        "scheduler_local_event_wall_ms": None
        if median_phase_value(scheduler_stats, "local_event_wall_ns") is None
        else median_phase_value(scheduler_stats, "local_event_wall_ns") / 1_000_000.0,
        "scheduler_packet_event_ns_per_event": median_ns_per_event(
            scheduler_stats, "packet_events", "packet_event_wall_ns"
        ),
        "scheduler_local_event_ns_per_event": median_ns_per_event(
            scheduler_stats, "local_events", "local_event_wall_ns"
        ),
        "scheduler_resume_process_events": median_phase_value(scheduler_stats, "resume_process_events"),
        "scheduler_start_application_events": median_phase_value(scheduler_stats, "start_application_events"),
        "scheduler_shutdown_process_events": median_phase_value(scheduler_stats, "shutdown_process_events"),
        "scheduler_relay_forward_events": median_phase_value(scheduler_stats, "relay_forward_events"),
        "scheduler_relay_forward_wall_ms": None
        if median_phase_value(scheduler_stats, "relay_forward_wall_ns") is None
        else median_phase_value(scheduler_stats, "relay_forward_wall_ns") / 1_000_000.0,
        "scheduler_relay_forward_ns_per_event": median_ns_per_event(
            scheduler_stats, "relay_forward_events", "relay_forward_wall_ns"
        ),
        "scheduler_syscall_condition_wake_events": median_phase_value(scheduler_stats, "syscall_condition_wake_events"),
        "scheduler_syscall_condition_wake_wall_ms": None
        if median_phase_value(scheduler_stats, "syscall_condition_wake_wall_ns") is None
        else median_phase_value(scheduler_stats, "syscall_condition_wake_wall_ns") / 1_000_000.0,
        "scheduler_syscall_condition_wake_ns_per_event": median_ns_per_event(
            scheduler_stats, "syscall_condition_wake_events", "syscall_condition_wake_wall_ns"
        ),
        "scheduler_prepare_poll_timeout_completion_events": median_phase_value(scheduler_stats, "prepare_poll_timeout_completion_events"),
        "scheduler_restore_blocked_syscall_condition_events": median_phase_value(scheduler_stats, "restore_blocked_syscall_condition_events"),
        "scheduler_timer_expire_events": median_phase_value(scheduler_stats, "timer_expire_events"),
        "scheduler_timer_expire_wall_ms": None
        if median_phase_value(scheduler_stats, "timer_expire_wall_ns") is None
        else median_phase_value(scheduler_stats, "timer_expire_wall_ns") / 1_000_000.0,
        "scheduler_timer_expire_ns_per_event": median_ns_per_event(
            scheduler_stats, "timer_expire_events", "timer_expire_wall_ns"
        ),
        "scheduler_legacy_tcp_deferred_events": median_phase_value(scheduler_stats, "legacy_tcp_deferred_events"),
        "scheduler_legacy_tcp_deferred_wall_ms": None
        if median_phase_value(scheduler_stats, "legacy_tcp_deferred_wall_ns") is None
        else median_phase_value(scheduler_stats, "legacy_tcp_deferred_wall_ns") / 1_000_000.0,
        "scheduler_legacy_tcp_deferred_ns_per_event": median_ns_per_event(
            scheduler_stats, "legacy_tcp_deferred_events", "legacy_tcp_deferred_wall_ns"
        ),
        "scheduler_exec_continuation_events": median_phase_value(scheduler_stats, "exec_continuation_events"),
        "scheduler_opaque_events": median_phase_value(scheduler_stats, "opaque_events"),
        "scheduler_undescribed_events": median_phase_value(scheduler_stats, "undescribed_events"),
        "scheduler_avg_worker_body_wall_ns": median_phase_value(scheduler_stats, "avg_worker_body_wall_ns"),
        "scheduler_max_worker_body_wall_ns": max_worker_body_wall_ns,
        "scheduler_worker_body_max_to_avg": median_phase_value(scheduler_stats, "worker_body_max_to_avg"),
        "scheduler_worker_body_continue_receive_wall_ms": None
        if worker_body_continue_receive_wall_ns is None
        else worker_body_continue_receive_wall_ns / 1_000_000.0,
        "scheduler_worker_body_continue_receive_percent": None
        if not worker_body_wall_ns or worker_body_continue_receive_wall_ns is None
        else (worker_body_continue_receive_wall_ns / worker_body_wall_ns) * 100.0,
        "scheduler_max_worker_body_continue_receive_wall_ms": None
        if max_worker_body_continue_receive_wall_ns is None
        else max_worker_body_continue_receive_wall_ns / 1_000_000.0,
        "scheduler_max_worker_body_continue_receive_percent": None
        if not max_worker_body_wall_ns or max_worker_body_continue_receive_wall_ns is None
        else (max_worker_body_continue_receive_wall_ns / max_worker_body_wall_ns) * 100.0,
        "scheduler_scope_wall_ms": None
        if median_phase_value(scheduler_stats, "scheduler_scope_wall_ns") is None
        else median_phase_value(scheduler_stats, "scheduler_scope_wall_ns") / 1_000_000.0,
        "host_execute_wall_ms": None
        if median_phase_value(scheduler_stats, "host_execute_wall_ns") is None
        else median_phase_value(scheduler_stats, "host_execute_wall_ns") / 1_000_000.0,
        "network_counters": network_stats,
        "network_packet_pushes": median_phase_value(network_stats, "packet_pushes"),
        "network_cross_host_packet_pushes": median_phase_value(network_stats, "cross_host_packet_pushes"),
        "network_avg_event_queue_lock_wait_ns": median_phase_value(network_stats, "avg_event_queue_lock_wait_ns"),
        "network_max_event_queue_lock_wait_ns": median_phase_value(network_stats, "max_event_queue_lock_wait_ns"),
        "network_avg_event_queue_len_after": median_phase_value(network_stats, "avg_event_queue_len_after"),
        "network_max_event_queue_len_after": median_phase_value(network_stats, "max_event_queue_len_after"),
        "managed_thread_counters": managed_thread_stats,
        "managed_syscall_top": next(
            (
                stat["syscall_top"]
                for stat in reversed(managed_thread_stats)
                if stat.get("syscall_top")
            ),
            [],
        ),
        "managed_continue_exchange_top": next(
            (
                stat["continue_exchange_top"]
                for stat in reversed(managed_thread_stats)
                if stat.get("continue_exchange_top")
            ),
            [],
        ),
        "managed_continue_plugin_calls": median_phase_value(managed_thread_stats, "continue_plugin_calls"),
        "managed_continue_plugin_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_receive_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_receive_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_receive_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_lock_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_lock_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_lock_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_prepare_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_prepare_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_prepare_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_runahead_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_runahead_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_runahead_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_clock_state_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_clock_state_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_clock_state_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_unlock_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_unlock_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_unlock_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_send_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_send_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_send_wall_ns") / 1_000_000.0,
        "managed_continue_plugin_time_update_wall_ms": None
        if median_phase_value(managed_thread_stats, "continue_plugin_time_update_wall_ns") is None
        else median_phase_value(managed_thread_stats, "continue_plugin_time_update_wall_ns") / 1_000_000.0,
        "managed_syscall_handler_calls": median_phase_value(managed_thread_stats, "syscall_handler_calls"),
        "managed_syscall_handler_wall_ms": None
        if median_phase_value(managed_thread_stats, "syscall_handler_wall_ns") is None
        else median_phase_value(managed_thread_stats, "syscall_handler_wall_ns") / 1_000_000.0,
        "managed_syscall_continue_calls": median_phase_value(managed_thread_stats, "syscall_continue_calls"),
        "managed_syscall_continue_wall_ms": None
        if median_phase_value(managed_thread_stats, "syscall_continue_wall_ns") is None
        else median_phase_value(managed_thread_stats, "syscall_continue_wall_ns") / 1_000_000.0,
        "syscall_condition_counters": syscall_condition_stats,
        "syscond_schedule_attempts": median_phase_value(syscall_condition_stats, "schedule_attempts"),
        "syscond_scheduled_wakeups": median_phase_value(syscall_condition_stats, "scheduled_wakeups"),
        "syscond_skipped_already_scheduled": median_phase_value(
            syscall_condition_stats, "skipped_already_scheduled"
        ),
        "syscond_trigger_enters": median_phase_value(syscall_condition_stats, "trigger_enters"),
        "syscond_trigger_continues": median_phase_value(
            syscall_condition_stats, "trigger_continues"
        ),
        "syscond_trigger_reblocks": median_phase_value(syscall_condition_stats, "trigger_reblocks"),
        "syscond_notify_status_changed": median_phase_value(
            syscall_condition_stats, "notify_status_changed"
        ),
        "syscond_notify_timeout_expired": median_phase_value(
            syscall_condition_stats, "notify_timeout_expired"
        ),
        "syscond_trigger_lookup_wall_ms": None
        if median_phase_value(syscall_condition_stats, "trigger_lookup_wall_ns") is None
        else median_phase_value(syscall_condition_stats, "trigger_lookup_wall_ns") / 1_000_000.0,
        "syscond_satisfied_check_wall_ms": None
        if median_phase_value(syscall_condition_stats, "satisfied_check_wall_ns") is None
        else median_phase_value(syscall_condition_stats, "satisfied_check_wall_ns") / 1_000_000.0,
        "syscond_host_continue_wall_ms": None
        if median_phase_value(syscall_condition_stats, "host_continue_wall_ns") is None
        else median_phase_value(syscall_condition_stats, "host_continue_wall_ns") / 1_000_000.0,
        "syscond_wake_continue_wall_ms": None
        if median_phase_value(syscall_condition_stats, "wake_continue_wall_ns") is None
        else median_phase_value(syscall_condition_stats, "wake_continue_wall_ns") / 1_000_000.0,
        "syscond_wake_reblock_wall_ms": None
        if median_phase_value(syscall_condition_stats, "wake_reblock_wall_ns") is None
        else median_phase_value(syscall_condition_stats, "wake_reblock_wall_ns") / 1_000_000.0,
        "checkpoint_criu_ms": median_phase_value(phase_timings, "criu"),
        "checkpoint_json_ms": median_phase_value(phase_timings, "json"),
        "checkpoint_total_phase_ms": median_phase_value(phase_timings, "total"),
        "restore_criu_phase_ms": median_phase_value(restore_timings, "criu"),
        "restore_total_phase_ms": median_phase_value(restore_timings, "total"),
        "log_path": case["log_path"],
        "shadow_log_paths": case.get("shadow_log_paths") or [],
        "result_path": case["result_path"],
    }
    syscond_host_continue_ms = summary_out.get("syscond_host_continue_wall_ms")
    managed_syscall_handler_ms = summary_out.get("managed_syscall_handler_wall_ms")
    managed_syscall_continue_ms = summary_out.get("managed_syscall_continue_wall_ms")
    if None not in (
        syscond_host_continue_ms,
        managed_syscall_handler_ms,
        managed_syscall_continue_ms,
    ):
        managed_resume_ms = managed_syscall_handler_ms + managed_syscall_continue_ms
        residual_ms = syscond_host_continue_ms - managed_resume_ms
        summary_out["syscond_handler_plus_continue_wall_ms"] = managed_resume_ms
        summary_out["syscond_host_continue_residual_wall_ms"] = residual_ms
        summary_out["syscond_host_continue_residual_percent"] = (
            (residual_ms / syscond_host_continue_ms) * 100.0
            if syscond_host_continue_ms
            else None
        )
    else:
        summary_out["syscond_handler_plus_continue_wall_ms"] = None
        summary_out["syscond_host_continue_residual_wall_ms"] = None
        summary_out["syscond_host_continue_residual_percent"] = None
    return summary_out


def render_report(
    results_dir: Path,
    summaries: list[dict[str, Any]],
    host_load_before: dict[str, Any],
    host_load_after: dict[str, Any],
) -> None:
    lines = [
        "# TDT Performance Model",
        "",
        "This report records simulation-layer throughput probes without changing simulated application configuration.",
        "",
        "| Setup | Pass | Elapsed s | Sim s / wall s | Steady sim s / wall s | CP+restore wall % | Sched windows | Worker busy % | Scope/max-body % | Packet events | Local events | Resume | Relay | Syscall wake | Poll timeout | Restore wake | Timer | TCP deferred | Exec cont. | Opaque | Undesc. | Packet pushes | Avg queue lock ns | Checkpoint ms | Restore ms | Control cmds |",
        "| ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in summaries:
        bundle = item.get("checkpoint_bundle_bytes")
        bundle_mb = "" if bundle is None else f"{bundle / 1_000_000:.2f}"
        lines.append(
            "| {setup} | {passed} | {elapsed:.2f} | {sim_rate} | {steady_rate} | {cp_restore_pct} | {windows} | {worker_busy} | {scope_over_max} | {packet_events} | {local_events} | {resume_events} | {relay_events} | {syscall_wake} | {poll_timeout} | {restore_wake} | {timer_events} | {tcp_deferred} | {exec_cont} | {opaque} | {undescribed} | {packet_pushes} | {avg_queue_lock} | {checkpoint} | {restore} | {cmds} |".format(
                setup=item["setup"],
                passed="yes" if item["passed"] else "no",
                elapsed=item["elapsed_seconds"],
                sim_rate=""
                if item.get("simulated_seconds_per_wall_second") is None
                else f"{item['simulated_seconds_per_wall_second']:.2f}",
                steady_rate=""
                if item.get("steady_simulated_seconds_per_wall_second") is None
                else f"{item['steady_simulated_seconds_per_wall_second']:.2f}",
                cp_restore_pct=""
                if item.get("cp_restore_wall_percent") is None
                else f"{item['cp_restore_wall_percent']:.2f}",
                windows="" if item.get("scheduler_windows") is None else f"{item['scheduler_windows']:.0f}",
                worker_busy=""
                if item.get("scheduler_worker_busy_percent") is None
                else f"{item['scheduler_worker_busy_percent']:.2f}",
                scope_over_max=""
                if item.get("scheduler_scope_over_window_max_percent") is None
                else f"{item['scheduler_scope_over_window_max_percent']:.2f}",
                packet_events=""
                if item.get("scheduler_packet_events") is None
                else f"{item['scheduler_packet_events']:.0f}",
                local_events=""
                if item.get("scheduler_local_events") is None
                else f"{item['scheduler_local_events']:.0f}",
                cpu_delayed=""
                if item.get("scheduler_cpu_delayed_events") is None
                else f"{item['scheduler_cpu_delayed_events']:.0f}",
                resume_events=""
                if item.get("scheduler_resume_process_events") is None
                else f"{item['scheduler_resume_process_events']:.0f}",
                relay_events=""
                if item.get("scheduler_relay_forward_events") is None
                else f"{item['scheduler_relay_forward_events']:.0f}",
                syscall_wake=""
                if item.get("scheduler_syscall_condition_wake_events") is None
                else f"{item['scheduler_syscall_condition_wake_events']:.0f}",
                poll_timeout=""
                if item.get("scheduler_prepare_poll_timeout_completion_events") is None
                else f"{item['scheduler_prepare_poll_timeout_completion_events']:.0f}",
                restore_wake=""
                if item.get("scheduler_restore_blocked_syscall_condition_events") is None
                else f"{item['scheduler_restore_blocked_syscall_condition_events']:.0f}",
                timer_events=""
                if item.get("scheduler_timer_expire_events") is None
                else f"{item['scheduler_timer_expire_events']:.0f}",
                tcp_deferred=""
                if item.get("scheduler_legacy_tcp_deferred_events") is None
                else f"{item['scheduler_legacy_tcp_deferred_events']:.0f}",
                exec_cont=""
                if item.get("scheduler_exec_continuation_events") is None
                else f"{item['scheduler_exec_continuation_events']:.0f}",
                opaque=""
                if item.get("scheduler_opaque_events") is None
                else f"{item['scheduler_opaque_events']:.0f}",
                undescribed=""
                if item.get("scheduler_undescribed_events") is None
                else f"{item['scheduler_undescribed_events']:.0f}",
                packet_pushes=""
                if item.get("network_packet_pushes") is None
                else f"{item['network_packet_pushes']:.0f}",
                avg_queue_lock=""
                if item.get("network_avg_event_queue_lock_wait_ns") is None
                else f"{item['network_avg_event_queue_lock_wait_ns']:.1f}",
                checkpoint="" if item.get("checkpoint_ms_median") is None else f"{item['checkpoint_ms_median']:.2f}",
                restore="" if item.get("restore_ms_median") is None else f"{item['restore_ms_median']:.2f}",
                cmds=", ".join(f"{key}={value}" for key, value in item.get("control_command_counts", {}).items()),
            )
        )
    lines.extend(
        [
            "",
            "## Task Wall Time",
            "",
            "| Setup | Packet wall ms | Local wall ms | Worker body receive ms | Worker body receive % | Max body receive % | Est async overlap ms | Est async overlap % | Relay ms/ns | Syscall wake ms/ns | Timer ms/ns | TCP deferred ms/ns |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summaries:
        def ms_ns(ms_key: str, ns_key: str) -> str:
            ms = item.get(ms_key)
            ns = item.get(ns_key)
            if ms is None or ns is None:
                return ""
            return f"{ms:.2f}/{ns:.1f}"

        lines.append(
            "| {setup} | {packet_wall} | {local_wall} | {body_receive_wall} | {body_receive_pct} | {max_body_receive_pct} | {async_overlap_wall} | {async_overlap_pct} | {relay} | {syscall} | {timer} | {tcp} |".format(
                setup=item["setup"],
                packet_wall=""
                if item.get("scheduler_packet_event_wall_ms") is None
                else f"{item['scheduler_packet_event_wall_ms']:.2f}",
                local_wall=""
                if item.get("scheduler_local_event_wall_ms") is None
                else f"{item['scheduler_local_event_wall_ms']:.2f}",
                body_receive_wall=""
                if item.get("scheduler_worker_body_continue_receive_wall_ms") is None
                else f"{item['scheduler_worker_body_continue_receive_wall_ms']:.2f}",
                body_receive_pct=""
                if item.get("scheduler_worker_body_continue_receive_percent") is None
                else f"{item['scheduler_worker_body_continue_receive_percent']:.1f}",
                max_body_receive_pct=""
                if item.get("scheduler_max_worker_body_continue_receive_percent") is None
                else f"{item['scheduler_max_worker_body_continue_receive_percent']:.1f}",
                async_overlap_wall=""
                if item.get("scheduler_estimated_async_continue_overlap_savings_ms") is None
                else f"{item['scheduler_estimated_async_continue_overlap_savings_ms']:.2f}",
                async_overlap_pct=""
                if item.get("scheduler_estimated_async_continue_overlap_savings_percent") is None
                else f"{item['scheduler_estimated_async_continue_overlap_savings_percent']:.1f}",
                relay=ms_ns(
                    "scheduler_relay_forward_wall_ms",
                    "scheduler_relay_forward_ns_per_event",
                ),
                syscall=ms_ns(
                    "scheduler_syscall_condition_wake_wall_ms",
                    "scheduler_syscall_condition_wake_ns_per_event",
                ),
                timer=ms_ns(
                    "scheduler_timer_expire_wall_ms",
                    "scheduler_timer_expire_ns_per_event",
                ),
                tcp=ms_ns(
                    "scheduler_legacy_tcp_deferred_wall_ms",
                    "scheduler_legacy_tcp_deferred_ns_per_event",
                ),
            )
        )
    lines.extend(
        [
            "",
            "## Async Scope Drain",
            "",
            "| Setup | Pending hosts drained | Re-enter opportunities | Re-enter % | Drain wall ms | Boundary pending hosts | Boundary pending continuations |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summaries:
        drained = item.get("scheduler_async_scope_drain_hosts")
        reenter = item.get("scheduler_async_scope_reenter_opportunities")
        reenter_pct = None if not drained else (reenter or 0.0) / drained * 100.0
        lines.append(
            "| {setup} | {drained} | {reenter} | {reenter_pct} | {drain_wall} | {boundary_hosts} | {boundary_continuations} |".format(
                setup=item["setup"],
                drained="" if drained is None else f"{drained:.0f}",
                reenter="" if reenter is None else f"{reenter:.0f}",
                reenter_pct="" if reenter_pct is None else f"{reenter_pct:.2f}",
                drain_wall=""
                if item.get("scheduler_async_scope_drain_wall_ms") is None
                else f"{item['scheduler_async_scope_drain_wall_ms']:.2f}",
                boundary_hosts=""
                if item.get("scheduler_async_boundary_pending_hosts") is None
                else f"{item['scheduler_async_boundary_pending_hosts']:.0f}",
                boundary_continuations=""
                if item.get("scheduler_async_boundary_pending_continuations") is None
                else f"{item['scheduler_async_boundary_pending_continuations']:.0f}",
            )
        )
    lines.extend(
        [
            "",
            "## Scheduler Top Hosts",
            "",
            "| Setup | Rank | Host | Executes | Wall ms | Syscall wake ms | Wake wall % |",
            "| ---: | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summaries:
        for rank, host in enumerate(item.get("scheduler_top_hosts") or [], start=1):
            wall_ms = host["wall_ms"]
            wake_ms = host["syscall_wake_ms"]
            wake_pct = 0.0 if wall_ms == 0 else (wake_ms / wall_ms) * 100.0
            lines.append(
                "| {setup} | {rank} | {name}({host_id}) | {count} | {wall_ms:.3f} | {wake_ms:.3f} | {wake_pct:.1f} |".format(
                    setup=item["setup"],
                    rank=rank,
                    name=host["name"],
                    host_id=host["host_id"],
                    count=host["count"],
                    wall_ms=wall_ms,
                    wake_ms=wake_ms,
                    wake_pct=wake_pct,
                )
            )
    lines.extend(
        [
            "",
            "## Scheduler Slowest Worker Bodies",
            "",
            "| Setup | Rank | Window | Thread | Body ms | Continue receive ms | Continue receive % | Host scans | Host executes | Top host | Host wall ms | Host syscall wake ms |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for item in summaries:
        for rank, body in enumerate(item.get("scheduler_top_worker_bodies") or [], start=1):
            host_label = body.get("host_id", "")
            if body.get("name"):
                host_label = f"{body['name']}({host_label})"
            lines.append(
                "| {setup} | {rank} | {window} | {thread} | {body_ms:.3f} | {continue_receive_ms} | {continue_receive_pct} | {host_scans} | {host_executes} | {host_label} | {host_wall_ms} | {host_wake_ms} |".format(
                    setup=item["setup"],
                    rank=rank,
                    window=body["window"],
                    thread=body["thread"],
                    body_ms=body["body_ms"],
                    continue_receive_ms=""
                    if body.get("continue_receive_ms") is None
                    else f"{body['continue_receive_ms']:.3f}",
                    continue_receive_pct=""
                    if body.get("continue_receive_pct") is None
                    else f"{body['continue_receive_pct']:.1f}",
                    host_scans=body["host_scans"],
                    host_executes=body["host_executes"],
                    host_label=host_label,
                    host_wall_ms=""
                    if body.get("host_wall_ms") is None
                    else f"{body['host_wall_ms']:.3f}",
                    host_wake_ms=""
                    if body.get("host_syscall_wake_ms") is None
                    else f"{body['host_syscall_wake_ms']:.3f}",
                )
            )
    lines.extend(
        [
            "",
            "## Managed Thread Wall Time",
            "",
            "| Setup | continue_plugin calls | continue_plugin ms | receive ms | lock ms | prepare ms | runahead ms | clock state ms | unlock ms | send ms | time update ms | syscall handler calls | syscall handler ms | syscall continue calls | syscall continue ms |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summaries:
        lines.append(
            "| {setup} | {cont_calls} | {cont_ms} | {recv_ms} | {lock_ms} | {prepare_ms} | {runahead_ms} | {clock_state_ms} | {unlock_ms} | {send_ms} | {time_update_ms} | {handler_calls} | {handler_ms} | {sys_cont_calls} | {sys_cont_ms} |".format(
                setup=item["setup"],
                cont_calls=""
                if item.get("managed_continue_plugin_calls") is None
                else f"{item['managed_continue_plugin_calls']:.0f}",
                cont_ms=""
                if item.get("managed_continue_plugin_wall_ms") is None
                else f"{item['managed_continue_plugin_wall_ms']:.2f}",
                recv_ms=""
                if item.get("managed_continue_plugin_receive_wall_ms") is None
                else f"{item['managed_continue_plugin_receive_wall_ms']:.2f}",
                lock_ms=""
                if item.get("managed_continue_plugin_lock_wall_ms") is None
                else f"{item['managed_continue_plugin_lock_wall_ms']:.2f}",
                prepare_ms=""
                if item.get("managed_continue_plugin_prepare_wall_ms") is None
                else f"{item['managed_continue_plugin_prepare_wall_ms']:.2f}",
                runahead_ms=""
                if item.get("managed_continue_plugin_runahead_wall_ms") is None
                else f"{item['managed_continue_plugin_runahead_wall_ms']:.2f}",
                clock_state_ms=""
                if item.get("managed_continue_plugin_clock_state_wall_ms") is None
                else f"{item['managed_continue_plugin_clock_state_wall_ms']:.2f}",
                unlock_ms=""
                if item.get("managed_continue_plugin_unlock_wall_ms") is None
                else f"{item['managed_continue_plugin_unlock_wall_ms']:.2f}",
                send_ms=""
                if item.get("managed_continue_plugin_send_wall_ms") is None
                else f"{item['managed_continue_plugin_send_wall_ms']:.2f}",
                time_update_ms=""
                if item.get("managed_continue_plugin_time_update_wall_ms") is None
                else f"{item['managed_continue_plugin_time_update_wall_ms']:.2f}",
                handler_calls=""
                if item.get("managed_syscall_handler_calls") is None
                else f"{item['managed_syscall_handler_calls']:.0f}",
                handler_ms=""
                if item.get("managed_syscall_handler_wall_ms") is None
                else f"{item['managed_syscall_handler_wall_ms']:.2f}",
                sys_cont_calls=""
                if item.get("managed_syscall_continue_calls") is None
                else f"{item['managed_syscall_continue_calls']:.0f}",
                sys_cont_ms=""
                if item.get("managed_syscall_continue_wall_ms") is None
                else f"{item['managed_syscall_continue_wall_ms']:.2f}",
            )
        )
    lines.extend(
        [
            "",
            "## Managed Continue Exchanges",
            "",
            "| Setup | Rank | Sent | Received | Calls | Wall ms | Receive ms | Receive % |",
            "| ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summaries:
        for rank, exchange in enumerate(item.get("managed_continue_exchange_top") or [], start=1):
            receive_pct = (
                0.0
                if exchange["wall_ms"] == 0
                else (exchange["receive_ms"] / exchange["wall_ms"]) * 100.0
            )
            lines.append(
                "| {setup} | {rank} | {sent} | {received} | {calls} | {wall_ms:.3f} | {receive_ms:.3f} | {receive_pct:.1f} |".format(
                    setup=item["setup"],
                    rank=rank,
                    sent=exchange["sent"],
                    received=exchange["received"],
                    calls=exchange["calls"],
                    wall_ms=exchange["wall_ms"],
                    receive_ms=exchange["receive_ms"],
                    receive_pct=receive_pct,
                )
            )
    lines.extend(
        [
            "",
            "## Managed Thread Top Syscalls",
            "",
            "| Setup | Rank | Syscall | Handler ms | Continue ms | Continue avg ns | Handler calls | Continue calls | Done | Block | Native | Synthetic | Top fds | Kind top |",
            "| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for item in summaries:
        for rank, syscall in enumerate(item.get("managed_syscall_top") or [], start=1):
            top_fds = "; ".join(
                f"{entry['fd']}={entry['calls']}" for entry in syscall.get("fd_top") or []
            )
            kind_top = "; ".join(
                f"{entry['kind']}={entry['calls']}"
                for entry in syscall.get("fd_kind_top") or []
            )
            lines.append(
                "| {setup} | {rank} | {name}({number}) | {handler_ms:.3f} | {continue_ms:.3f} | {continue_avg_ns:.1f} | {handler_calls} | {continue_calls} | {done} | {block} | {native} | {synthetic} | {top_fds} | {kind_top} |".format(
                    setup=item["setup"],
                    rank=rank,
                    name=syscall["name"],
                    number=syscall["number"],
                    handler_ms=syscall["handler_ms"],
                    continue_ms=syscall["continue_ms"],
                    continue_avg_ns=syscall["continue_avg_ns"],
                    handler_calls=syscall["handler_calls"],
                    continue_calls=syscall["continue_calls"],
                    done=syscall["done"],
                    block=syscall["block"],
                    native=syscall["native"],
                    synthetic=syscall["synthetic"],
                    top_fds=top_fds,
                    kind_top=kind_top,
                )
            )
    lines.extend(
        [
            "",
            "## Syscall Condition Wakeups",
            "",
            "| Setup | schedule attempts | scheduled | skipped scheduled | trigger enters | continues | reblocks | status notifications | timeout notifications | lookup ms | satisfied ms | host continue ms | handler+continue ms | residual ms | residual % | wake continue ms | wake reblock ms |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summaries:
        lines.append(
            "| {setup} | {attempts} | {scheduled} | {skipped} | {enters} | {continues} | {reblocks} | {status} | {timeout} | {lookup_ms} | {satisfied_ms} | {host_continue_ms} | {handler_plus_continue_ms} | {residual_ms} | {residual_pct} | {wake_continue_ms} | {wake_reblock_ms} |".format(
                setup=item["setup"],
                attempts=""
                if item.get("syscond_schedule_attempts") is None
                else f"{item['syscond_schedule_attempts']:.0f}",
                scheduled=""
                if item.get("syscond_scheduled_wakeups") is None
                else f"{item['syscond_scheduled_wakeups']:.0f}",
                skipped=""
                if item.get("syscond_skipped_already_scheduled") is None
                else f"{item['syscond_skipped_already_scheduled']:.0f}",
                enters=""
                if item.get("syscond_trigger_enters") is None
                else f"{item['syscond_trigger_enters']:.0f}",
                continues=""
                if item.get("syscond_trigger_continues") is None
                else f"{item['syscond_trigger_continues']:.0f}",
                reblocks=""
                if item.get("syscond_trigger_reblocks") is None
                else f"{item['syscond_trigger_reblocks']:.0f}",
                status=""
                if item.get("syscond_notify_status_changed") is None
                else f"{item['syscond_notify_status_changed']:.0f}",
                timeout=""
                if item.get("syscond_notify_timeout_expired") is None
                else f"{item['syscond_notify_timeout_expired']:.0f}",
                lookup_ms=""
                if item.get("syscond_trigger_lookup_wall_ms") is None
                else f"{item['syscond_trigger_lookup_wall_ms']:.2f}",
                satisfied_ms=""
                if item.get("syscond_satisfied_check_wall_ms") is None
                else f"{item['syscond_satisfied_check_wall_ms']:.2f}",
                host_continue_ms=""
                if item.get("syscond_host_continue_wall_ms") is None
                else f"{item['syscond_host_continue_wall_ms']:.2f}",
                handler_plus_continue_ms=""
                if item.get("syscond_handler_plus_continue_wall_ms") is None
                else f"{item['syscond_handler_plus_continue_wall_ms']:.2f}",
                residual_ms=""
                if item.get("syscond_host_continue_residual_wall_ms") is None
                else f"{item['syscond_host_continue_residual_wall_ms']:.2f}",
                residual_pct=""
                if item.get("syscond_host_continue_residual_percent") is None
                else f"{item['syscond_host_continue_residual_percent']:.2f}",
                wake_continue_ms=""
                if item.get("syscond_wake_continue_wall_ms") is None
                else f"{item['syscond_wake_continue_wall_ms']:.2f}",
                wake_reblock_ms=""
                if item.get("syscond_wake_reblock_wall_ms") is None
                else f"{item['syscond_wake_reblock_wall_ms']:.2f}",
            )
        )
    lines.extend(
        [
            "",
            "Next modeling target: tie dominant task types to per-host wall-time skew and packet fanout.",
            "",
            "## Host Load Snapshot",
            "",
            "These samples help flag noisy performance runs; they are not simulation outputs.",
            "",
            f"- before loadavg: {host_load_before.get('loadavg')}",
            f"- after loadavg: {host_load_after.get('loadavg')}",
            "",
            "Top processes before:",
            "",
            "```",
            *host_load_before.get("top_processes", []),
            "```",
            "",
            "Top processes after:",
            "",
            "```",
            *host_load_after.get("top_processes", []),
            "```",
            "",
        ]
    )
    (results_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    config_path = Path(args.tdt_config).resolve() if args.tdt_config else default_config_path()
    config = tdt_config.load_tdt_config(config_path)
    results_dir = Path(args.results_dir).resolve()
    work_root = Path(args.work_root).resolve()
    setups = selected_setups(args.setups)
    results_dir.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    for setup in setups:
        validate_control_socket_path(work_root, setup)

    experiment_config = write_experiment_config(config_path, results_dir, work_root, setups)
    env = os.environ.copy()
    env["CRIU_BIN"] = str(config.criu_bin)
    env.setdefault("SHADOW_RESTORE_PROTOCOL_MODE", config.checkpoint_restore.restore_protocol_mode)
    env["SHADOW_TDT_PERF_COUNTERS"] = "1" if args.perf_counters == "on" else "0"
    if args.checkpoint_criu_jobs > 0:
        env["SHADOW_CHECKPOINT_CRIU_JOBS"] = str(args.checkpoint_criu_jobs)

    host_load_before = host_load_snapshot()
    cases = [
        run_performance(experiment_config, results_dir, work_root, setup, args.trials, args.timeout, env)
        for setup in setups
    ]
    host_load_after = host_load_snapshot()
    summaries = [summarize_case(case) for case in cases]
    output = {
        "tdt_config": str(config_path),
        "results_dir": str(results_dir),
        "work_root": str(work_root),
        "setups": setups,
        "trials": args.trials,
        "host_load_before": host_load_before,
        "host_load_after": host_load_after,
        "cases": cases,
        "summary": summaries,
    }
    (results_dir / "perf-model.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    render_report(results_dir, summaries, host_load_before, host_load_after)
    print(json.dumps({"passed": all(item["passed"] for item in summaries), "results_dir": str(results_dir)}))
    return 0 if all(item["passed"] for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
