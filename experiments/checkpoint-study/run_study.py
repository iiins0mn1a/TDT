#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import copy
import dataclasses
import difflib
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import tomllib

HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
SYNC_CONTRIBUTION_TEXT = "Submitted new sync contribution and proof"
MAX_UNIX_SOCKET_PATH = 107


@dataclasses.dataclass
class BasePaths:
    tdt_config: Path
    results_dir: Path
    work_root: Path


@dataclasses.dataclass
class ExperimentConfig:
    setups: list[int]
    validators_per_beacon: int
    warmup_step_seconds: int
    max_warmup_seconds: int
    settle_seconds: int
    comparison_window_seconds: int
    comparison_boundary_guard_seconds: int
    performance_trials: int
    managed_external_paths: list[str]
    checkpoint_label_prefix: str
    hex_normalization: bool
    restore_protocol_mode: str


@dataclasses.dataclass
class StudyConfig:
    config_path: Path
    base: BasePaths
    experiment: ExperimentConfig


@dataclasses.dataclass
class SliceSnapshot:
    stderr: str = ""
    stdout: str = ""


@dataclasses.dataclass
class ShadowSession:
    process: subprocess.Popen[bytes]
    socket_path: Path
    sock: socket.socket | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the TDT checkpoint study")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "experiment.toml"))
    parser.add_argument("--mode", choices=("determinism", "performance", "all"), default="all")
    parser.add_argument("--setup", default="all", help="1|4|8|all")
    parser.add_argument("--trials", type=int, default=None)
    parser.add_argument("--results-dir", default="")
    parser.add_argument("--work-root", default="")
    return parser.parse_args()


def log(message: str) -> None:
    print(f"[checkpoint-study] {message}")


def load_study_config(path: str | Path) -> StudyConfig:
    config_path = Path(path).resolve()
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    base_raw = data.get("base", {})
    exp_raw = data.get("experiment", {})
    base_dir = config_path.parent
    base = BasePaths(
        tdt_config=Path(base_raw.get("tdt_config", "")).resolve()
        if Path(base_raw.get("tdt_config", "")).is_absolute()
        else (base_dir / base_raw.get("tdt_config", "../../tdt_config.toml")).resolve(),
        results_dir=(base_dir / base_raw.get("results_dir", "results")).resolve(),
        work_root=Path(base_raw.get("work_root", "/tmp/tdt-checkpoint-study")).resolve(),
    )
    experiment = ExperimentConfig(
        setups=list(exp_raw.get("setups", [1, 4, 8])),
        validators_per_beacon=int(exp_raw.get("validators_per_beacon", 4)),
        warmup_step_seconds=int(exp_raw.get("warmup_step_seconds", 60)),
        max_warmup_seconds=int(exp_raw.get("max_warmup_seconds", 1200)),
        settle_seconds=int(exp_raw.get("settle_seconds", 60)),
        comparison_window_seconds=int(exp_raw.get("comparison_window_seconds", 120)),
        comparison_boundary_guard_seconds=int(exp_raw.get("comparison_boundary_guard_seconds", 3)),
        performance_trials=int(exp_raw.get("performance_trials", 3)),
        managed_external_paths=list(exp_raw.get("managed_external_paths", ["network", "beacon_peers.txt"])),
        checkpoint_label_prefix=str(exp_raw.get("checkpoint_label_prefix", "checkpoint_study")),
        hex_normalization=bool(exp_raw.get("hex_normalization", True)),
        restore_protocol_mode=str(exp_raw.get("restore_protocol_mode", "deterministic_v2")),
    )
    return StudyConfig(config_path=config_path, base=base, experiment=experiment)


def install_tdt_imports(study: StudyConfig) -> None:
    tdt_root = Path(__file__).resolve().parents[2]
    scripts_dir = tdt_root / "scripts"
    scripts_dir_str = str(scripts_dir)
    if scripts_dir_str not in sys.path:
        sys.path.insert(0, scripts_dir_str)


def import_tdt_modules(study: StudyConfig) -> dict[str, object]:
    install_tdt_imports(study)
    modules = {}
    for name in ("tdt_config", "tdt_logcheck", "tdt_orchestrator"):
        modules[name] = importlib.import_module(name)
    return modules


def selected_setups(study: StudyConfig, setup_arg: str) -> list[int]:
    if setup_arg == "all":
        return study.experiment.setups
    setup = int(setup_arg)
    if setup not in study.experiment.setups:
        raise ValueError(f"setup {setup} not declared in {study.config_path}")
    return [setup]


def build_tdt_config(study: StudyConfig, modules: dict[str, object], beacon_nodes: int, work_dir: Path):
    tdt_config = modules["tdt_config"]
    config = copy.deepcopy(tdt_config.load_tdt_config(study.base.tdt_config))
    config.cluster.beacon_nodes = beacon_nodes
    config.cluster.validators_total = beacon_nodes * study.experiment.validators_per_beacon
    config.simulation.work_dir = str(work_dir)
    config.simulation.clean_runtime_before_prepare = True
    config.simulation.interactive = False
    config.simulation.edit_shadow_yaml_before_run = False
    config.simulation.default_mode = "smoke"
    config.checkpoint_restore.managed_external_paths = list(study.experiment.managed_external_paths)
    return config


def launch_shadow(config, log_path: Path, restore_protocol_mode: str) -> ShadowSession:
    socket_path = config.work_dir / "control.sock"
    if len(str(socket_path)) > MAX_UNIX_SOCKET_PATH:
        raise ValueError(
            f"control socket path is too long for Unix sockets: {socket_path} "
            f"({len(str(socket_path))} > {MAX_UNIX_SOCKET_PATH}); use a shorter work_root"
        )
    if socket_path.exists():
        socket_path.unlink()
    env = os.environ.copy()
    env["SHADOW_CONTROL_SOCKET"] = str(socket_path)
    env["CRIU_BIN"] = str(config.criu_bin)
    env["SHADOW_RESTORE_PROTOCOL_MODE"] = restore_protocol_mode
    if config.simulation.packet_route_cache:
        env.setdefault("SHADOW_PACKET_ROUTE_CACHE", "1")
    if config.simulation.fast_file_sync:
        env.setdefault("SHADOW_FAST_FILE_SYNC", "1")
    if config.checkpoint_restore.checkpoint_criu_jobs > 0:
        env.setdefault(
            "SHADOW_CHECKPOINT_CRIU_JOBS", str(config.checkpoint_restore.checkpoint_criu_jobs)
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("wb")
    process = subprocess.Popen(
        [str(config.shadow_bin), str(config.work_dir / "shadow.yaml")],
        cwd=str(config.work_dir),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return ShadowSession(process=process, socket_path=socket_path)


def terminate_process(session: ShadowSession) -> None:
    if session.sock is not None:
        try:
            session.sock.close()
        except OSError:
            pass
    if session.process.poll() is None:
        session.process.terminate()
        try:
            session.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            session.process.kill()
            session.process.wait(timeout=10)


def wait_for_socket(path: Path, timeout_sec: float, process: subprocess.Popen[bytes]) -> socket.socket:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Shadow exited before socket became ready (code={process.returncode})")
        if path.exists():
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(str(path))
                return sock
            except OSError:
                pass
        time.sleep(0.2)
    raise TimeoutError(f"control socket {path} did not become ready within {timeout_sec}s")


def ensure_connected(session: ShadowSession, timeout_sec: float = 60.0) -> socket.socket:
    if session.sock is None:
        session.sock = wait_for_socket(session.socket_path, timeout_sec, session.process)
    return session.sock


def send_command(session: ShadowSession, cmd: dict, timeout_sec: float) -> dict:
    sock = ensure_connected(session)
    sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
    sock.settimeout(timeout_sec)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("control socket closed by Shadow")
        buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def expect_ok(resp: dict, label: str) -> None:
    if resp.get("status") != "ok":
        raise RuntimeError(f"{label} failed: {resp}")


def status(session: ShadowSession) -> dict:
    resp = send_command(session, {"cmd": "status"}, timeout_sec=30.0)
    expect_ok(resp, "status")
    return resp


def is_paused(status_resp: dict) -> bool:
    return "sim_waiting=true" in (status_resp.get("message") or "")


def wait_until_paused(
    session: ShadowSession,
    timeout_sec: float = 300.0,
    min_sim_time_ns: int | None = None,
) -> dict:
    if min_sim_time_ns is None:
        try:
            resp = send_command(session, {"cmd": "wait_until_paused"}, timeout_sec=timeout_sec)
            if resp.get("status") == "ok":
                return resp
        except (ConnectionError, TimeoutError):
            raise
        except Exception:
            # Older Shadow builds don't support the blocking wait command.
            pass

    deadline = time.time() + timeout_sec
    last = None
    while time.time() < deadline:
        last = status(session)
        sim_time_ns = int(last.get("sim_time_ns") or 0)
        if is_paused(last) and (min_sim_time_ns is None or sim_time_ns >= min_sim_time_ns):
            return last
        if session.process.poll() is not None:
            raise RuntimeError(f"Shadow exited while waiting to pause (code={session.process.returncode})")
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for Shadow to pause; last status={last}")


def continue_for(session: ShadowSession, duration_seconds: int) -> None:
    before = status(session)
    start_sim_time_ns = int(before.get("sim_time_ns") or 0)
    resp = send_command(
        session,
        {"cmd": "continue_for", "duration_ns": duration_seconds * 1_000_000_000},
        timeout_sec=30.0,
    )
    expect_ok(resp, f"continue_for({duration_seconds}s)")
    wait_until_paused(
        session,
        timeout_sec=max(duration_seconds + 180.0, 300.0),
        min_sim_time_ns=start_sim_time_ns + duration_seconds * 1_000_000_000,
    )


def restore_with_reconnect(session: ShadowSession, label: str) -> None:
    try:
        resp = send_command(session, {"cmd": "restore", "label": label}, timeout_sec=900.0)
        expect_ok(resp, "restore")
    except ConnectionError:
        if session.sock is not None:
            try:
                session.sock.close()
            except OSError:
                pass
            session.sock = None
        ensure_connected(session, 120.0)
    wait_until_paused(session, timeout_sec=300.0)


def ready_for_checkpoint(summary: dict, expected_nodes: int) -> bool:
    if summary["peer_lines"] < expected_nodes:
        return False
    if sum(summary["geth"].values()) == 0:
        return False
    for idx in range(1, expected_nodes + 1):
        beacon = summary["beacons"].get(f"beacon-{idx}", {})
        validator = summary["validators"].get(f"validator-{idx}", {})
        if sum(beacon.values()) == 0 or sum(validator.values()) == 0:
            return False
    return True


def role_for_host(hostname: str) -> str | None:
    if hostname == "geth-node":
        return "geth"
    if hostname.startswith("prysm-beacon-"):
        return "beacon"
    if hostname.startswith("prysm-validator-"):
        return "validator"
    return None


def primary_log_files(host_dir: Path, role: str) -> list[Path]:
    if role not in {"geth", "beacon", "validator"}:
        return []
    files = sorted(host_dir.glob("*.1000.stderr"))
    files.extend(sorted(host_dir.glob("*.1000.stdout")))
    return files


def normalize_text(text: str, normalize_hex: bool) -> str:
    if normalize_hex:
        text = HEX_RE.sub("HEX", text)
    return text


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def sync_contribution_order_key(line: str) -> tuple[str, str, str, str, str] | None:
    if SYNC_CONTRIBUTION_TEXT not in line:
        return None
    plain = strip_ansi(line)
    timestamp = re.search(r"^\[([^\]]+)\]", plain)
    block_root = re.search(r"\bblockRoot=([^ ]+)", plain)
    slot = re.search(r"\bslot=([^ ]+)", plain)
    slot_start_time = re.search(r"\bslotStartTime=(.*?)\s+subcommitteeIndex=", plain)
    time_since_slot_start = re.search(r"\btimeSinceSlotStart=([^ ]+)", plain)
    if not (timestamp and block_root and slot and slot_start_time and time_since_slot_start):
        return None
    return (
        timestamp.group(1),
        slot.group(1),
        block_root.group(1),
        slot_start_time.group(1),
        time_since_slot_start.group(1),
    )


def canonicalize_commutative_validator_logs(text: str) -> str:
    """Sort only consecutive Prysm sync-contribution lines with the same log-time key."""
    lines = text.splitlines()
    canonical: list[str] = []
    idx = 0
    while idx < len(lines):
        key = sync_contribution_order_key(lines[idx])
        if key is None:
            canonical.append(lines[idx])
            idx += 1
            continue

        group = [lines[idx]]
        idx += 1
        while idx < len(lines) and sync_contribution_order_key(lines[idx]) == key:
            group.append(lines[idx])
            idx += 1
        canonical.extend(sorted(group))

    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(canonical) + suffix


def is_validator_sync_contribution_order_only(ref_text: str, rep_text: str) -> bool:
    ref_lines = ref_text.splitlines()
    rep_lines = rep_text.splitlines()
    if Counter(ref_lines) != Counter(rep_lines):
        return False
    return (
        canonicalize_commutative_validator_logs(ref_text)
        == canonicalize_commutative_validator_logs(rep_text)
    )


def snapshot_offsets(runtime_dir: Path) -> dict[str, dict[str, int]]:
    hosts_dir = runtime_dir / "shadow.data" / "hosts"
    offsets: dict[str, dict[str, int]] = {}
    if not hosts_dir.exists():
        return offsets
    for host_dir in sorted(p for p in hosts_dir.iterdir() if p.is_dir()):
        role = role_for_host(host_dir.name)
        if role is None:
            continue
        host_offsets: dict[str, int] = {}
        for path in primary_log_files(host_dir, role):
            host_offsets[str(path.resolve())] = path.stat().st_size
        offsets[host_dir.name] = host_offsets
    return offsets


def snapshot_prefixes(
    runtime_dir: Path,
    offsets: dict[str, dict[str, int]],
) -> dict[str, dict[str, bytes]]:
    hosts_dir = runtime_dir / "shadow.data" / "hosts"
    prefixes: dict[str, dict[str, bytes]] = {}
    if not hosts_dir.exists():
        return prefixes
    for host_dir in sorted(p for p in hosts_dir.iterdir() if p.is_dir()):
        role = role_for_host(host_dir.name)
        if role is None:
            continue
        host_offsets = offsets.get(host_dir.name, {})
        host_prefixes = {"stderr": b"", "stdout": b""}
        for path in primary_log_files(host_dir, role):
            current_bytes = path.read_bytes()
            old_size = min(host_offsets.get(str(path.resolve()), 0), len(current_bytes))
            stream = "stderr" if path.name.endswith(".stderr") else "stdout"
            host_prefixes[stream] += current_bytes[:old_size]
        prefixes[host_dir.name] = host_prefixes
    return prefixes


def trim_restored_log_prefix(current_bytes: bytes, checkpoint_prefix: bytes) -> bytes:
    if not current_bytes or not checkpoint_prefix:
        return current_bytes
    checkpoint_prefix = checkpoint_prefix[-1_000_000:]
    max_len = min(len(current_bytes), len(checkpoint_prefix))
    for size in range(max_len, 0, -1):
        if checkpoint_prefix.endswith(current_bytes[:size]):
            return current_bytes[size:]
    return current_bytes


def wait_for_log_quiescence(
    runtime_dir: Path,
    quiet_seconds: float = 0.2,
    timeout_seconds: float = 3.0,
) -> None:
    """Wait until application log files stop appearing or growing.

    Shadow may create restored stdout/stderr files and drain inherited file
    buffers shortly after a restore while simulated time is still paused. Taking
    offsets before that drain completes makes the replay slice include
    pre-window log lines. This waits on wall-clock file stability only; it does
    not advance simulated time.
    """
    deadline = time.monotonic() + timeout_seconds
    previous: dict[str, dict[str, int]] | None = None
    stable_since: float | None = None

    while True:
        current = snapshot_offsets(runtime_dir)
        now = time.monotonic()
        if current == previous:
            if stable_since is None:
                stable_since = now
            if now - stable_since >= quiet_seconds:
                return
        else:
            previous = current
            stable_since = now

        if now >= deadline:
            return
        time.sleep(0.05)


def capture_window(
    runtime_dir: Path,
    offsets: dict[str, dict[str, int]],
    normalize_hex: bool,
    checkpoint_prefixes: dict[str, dict[str, bytes]] | None = None,
) -> dict[str, SliceSnapshot]:
    hosts_dir = runtime_dir / "shadow.data" / "hosts"
    captured: dict[str, SliceSnapshot] = {}
    if not hosts_dir.exists():
        return captured
    for host_dir in sorted(p for p in hosts_dir.iterdir() if p.is_dir()):
        role = role_for_host(host_dir.name)
        if role is None:
            continue
        snap = SliceSnapshot()
        previous = offsets.get(host_dir.name, {})
        for path in primary_log_files(host_dir, role):
            current_bytes = path.read_bytes()
            resolved_path = str(path.resolve())
            old_size = previous.get(resolved_path, 0)
            if old_size > len(current_bytes):
                old_size = 0
            stream = "stderr" if path.name.endswith(".stderr") else "stdout"
            window_bytes = current_bytes[old_size:]
            if (
                checkpoint_prefixes is not None
                and old_size == 0
                and path.name.startswith("restored.")
            ):
                prefix = checkpoint_prefixes.get(host_dir.name, {}).get(stream, b"")
                window_bytes = trim_restored_log_prefix(window_bytes, prefix)
            text = normalize_text(window_bytes.decode("utf-8", errors="replace"), normalize_hex)
            if path.name.endswith(".stderr"):
                snap.stderr += text
            else:
                snap.stdout += text
        captured[host_dir.name] = snap
    return captured


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def window_host_metadata(snapshot: SliceSnapshot) -> dict[str, object]:
    return {
        "stderr_sha256": hash_text(snapshot.stderr),
        "stdout_sha256": hash_text(snapshot.stdout),
        "stderr_bytes": len(snapshot.stderr.encode("utf-8")),
        "stdout_bytes": len(snapshot.stdout.encode("utf-8")),
        "stderr_lines": line_count(snapshot.stderr),
        "stdout_lines": line_count(snapshot.stdout),
    }


def persist_window_snapshot(
    window_root: Path,
    label: str,
    snapshots: dict[str, SliceSnapshot],
) -> dict[str, object]:
    target_dir = window_root / label
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    hosts_meta: dict[str, object] = {}
    for hostname, snapshot in sorted(snapshots.items()):
        host_dir = target_dir / hostname
        host_dir.mkdir(parents=True, exist_ok=True)
        (host_dir / "stderr.log").write_text(snapshot.stderr, encoding="utf-8")
        (host_dir / "stdout.log").write_text(snapshot.stdout, encoding="utf-8")
        hosts_meta[hostname] = window_host_metadata(snapshot)

    metadata = {
        "label": label,
        "hostnames": sorted(snapshots.keys()),
        "hosts": hosts_meta,
    }
    (target_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"dir": str(target_dir), "metadata": metadata}


def first_text_mismatch(ref_text: str, rep_text: str) -> dict[str, object] | None:
    ref_lines = ref_text.splitlines()
    rep_lines = rep_text.splitlines()
    shared = min(len(ref_lines), len(rep_lines))
    for idx in range(shared):
        if ref_lines[idx] != rep_lines[idx]:
            return {
                "line_number": idx + 1,
                "reference_line": ref_lines[idx],
                "replay_line": rep_lines[idx],
            }
    if len(ref_lines) != len(rep_lines):
        return {
            "line_number": shared + 1,
            "reference_line": ref_lines[shared] if shared < len(ref_lines) else "",
            "replay_line": rep_lines[shared] if shared < len(rep_lines) else "",
        }
    return None


def compare_windows(reference: dict[str, SliceSnapshot], replay: dict[str, SliceSnapshot], diff_dir: Path) -> tuple[bool, list[dict[str, object]], list[dict[str, object]]]:
    if diff_dir.exists():
        shutil.rmtree(diff_dir)
    diff_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    results: list[dict[str, object]] = []
    first_mismatches: list[dict[str, object]] = []
    for hostname in sorted(set(reference) | set(replay)):
        ref = reference.get(hostname, SliceSnapshot())
        rep = replay.get(hostname, SliceSnapshot())
        stderr_equal = ref.stderr == rep.stderr
        stdout_equal = ref.stdout == rep.stdout
        stderr_order_only = False
        stdout_order_only = False
        if not stderr_equal and hostname.startswith("prysm-validator-"):
            stderr_order_only = is_validator_sync_contribution_order_only(ref.stderr, rep.stderr)
        if not stdout_equal and hostname.startswith("prysm-validator-"):
            stdout_order_only = is_validator_sync_contribution_order_only(ref.stdout, rep.stdout)
        stderr_ok = stderr_equal or stderr_order_only
        stdout_ok = stdout_equal or stdout_order_only
        stderr_class = (
            "equal" if stderr_equal else "validator_sync_contribution_order_only" if stderr_order_only else "byte_drift"
        )
        stdout_class = (
            "equal" if stdout_equal else "validator_sync_contribution_order_only" if stdout_order_only else "byte_drift"
        )
        if not (stderr_ok and stdout_ok):
            ok = False
        if not (stderr_equal and stdout_equal):
            if not stderr_equal:
                diff = "\n".join(
                    difflib.unified_diff(
                        ref.stderr.splitlines(),
                        rep.stderr.splitlines(),
                        fromfile=f"{hostname}-reference.stderr",
                        tofile=f"{hostname}-replay.stderr",
                        lineterm="",
                    )
                )
                diff_path = diff_dir / f"{hostname}.stderr.diff"
                diff_path.write_text(diff + ("\n" if diff else ""), encoding="utf-8")
                mismatch = first_text_mismatch(ref.stderr, rep.stderr) or {}
                mismatch.update(
                    {
                        "hostname": hostname,
                        "stream": "stderr",
                        "diff_path": str(diff_path),
                        "order_only": stderr_order_only,
                    }
                )
                if not stderr_order_only:
                    first_mismatches.append(mismatch)
            if not stdout_equal:
                diff = "\n".join(
                    difflib.unified_diff(
                        ref.stdout.splitlines(),
                        rep.stdout.splitlines(),
                        fromfile=f"{hostname}-reference.stdout",
                        tofile=f"{hostname}-replay.stdout",
                        lineterm="",
                    )
                )
                diff_path = diff_dir / f"{hostname}.stdout.diff"
                diff_path.write_text(diff + ("\n" if diff else ""), encoding="utf-8")
                mismatch = first_text_mismatch(ref.stdout, rep.stdout) or {}
                mismatch.update(
                    {
                        "hostname": hostname,
                        "stream": "stdout",
                        "diff_path": str(diff_path),
                        "order_only": stdout_order_only,
                    }
                )
                if not stdout_order_only:
                    first_mismatches.append(mismatch)
        results.append(
            {
                "hostname": hostname,
                "stderr_equal": stderr_equal,
                "stdout_equal": stdout_equal,
                "stderr_order_only": stderr_order_only,
                "stdout_order_only": stdout_order_only,
                "stderr_class": stderr_class,
                "stdout_class": stdout_class,
                "stderr_ok": stderr_ok,
                "stdout_ok": stdout_ok,
                "reference_stderr_sha256": hash_text(ref.stderr),
                "replay_stderr_sha256": hash_text(rep.stderr),
                "reference_stdout_sha256": hash_text(ref.stdout),
                "replay_stdout_sha256": hash_text(rep.stdout),
            }
        )
    return ok, results, first_mismatches


def bundle_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def checkpoint_artifact_sizes(meta_path: Path, work_dir: Path, label: str) -> dict[str, int]:
    checkpoint_dir = work_dir / "shadow.data" / "checkpoints" / label
    return {
        "checkpoint_metadata_bytes": bundle_size_bytes(meta_path),
        "checkpoint_bundle_bytes": bundle_size_bytes(checkpoint_dir),
    }


def advance_until_ready(session: ShadowSession, runtime_dir: Path, collect_counts, expected_nodes: int, step_seconds: int, max_warmup_seconds: int) -> tuple[int, dict]:
    elapsed = 0
    last_summary = collect_counts(runtime_dir)
    while elapsed < max_warmup_seconds:
        continue_for(session, step_seconds)
        elapsed += step_seconds
        last_summary = collect_counts(runtime_dir)
        if ready_for_checkpoint(last_summary, expected_nodes):
            return elapsed, last_summary
    raise RuntimeError(
        f"network did not reach checkpoint-ready state within {max_warmup_seconds}s; last summary={json.dumps(last_summary, sort_keys=True)}"
    )


def issue_checkpoint(session: ShadowSession, label: str) -> float:
    start = time.perf_counter()
    resp = send_command(session, {"cmd": "checkpoint", "label": label}, timeout_sec=900.0)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    expect_ok(resp, "checkpoint")
    wait_until_paused(session, timeout_sec=120.0)
    return elapsed_ms


def issue_restore(session: ShadowSession, label: str) -> float:
    start = time.perf_counter()
    restore_with_reconnect(session, label)
    return (time.perf_counter() - start) * 1000.0


def run_determinism(study: StudyConfig, modules: dict[str, object], beacon_nodes: int) -> dict[str, object]:
    tdt_orchestrator = modules["tdt_orchestrator"]
    tdt_logcheck = modules["tdt_logcheck"]
    work_dir = study.base.work_root / f"determinism-setup-{beacon_nodes}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    config = build_tdt_config(study, modules, beacon_nodes, work_dir)
    tdt_orchestrator.ensure_binaries(config, "cprestore")
    tdt_orchestrator.prepare_runtime(config)

    session = launch_shadow(
        config,
        work_dir / "determinism.log",
        study.experiment.restore_protocol_mode,
    )
    label = f"{study.experiment.checkpoint_label_prefix}-determinism-{beacon_nodes}"
    diff_dir = study.base.results_dir / "diffs" / f"setup-{beacon_nodes}"
    windows_dir = study.base.results_dir / "windows" / f"setup-{beacon_nodes}"
    try:
        wait_until_paused(session, timeout_sec=120.0)
        ready_elapsed, ready_summary = advance_until_ready(
            session,
            work_dir,
            tdt_logcheck.collect_counts,
            beacon_nodes,
            study.experiment.warmup_step_seconds,
            study.experiment.max_warmup_seconds,
        )
        if study.experiment.settle_seconds > 0:
            continue_for(session, study.experiment.settle_seconds)
        wait_until_paused(session, timeout_sec=120.0)

        checkpoint_elapsed_ms = issue_checkpoint(session, label)
        meta_path = work_dir / "shadow.data" / "checkpoints" / f"{label}.checkpoint.json"
        if not meta_path.exists():
            raise RuntimeError(f"checkpoint metadata missing: {meta_path}")

        bundle_root = work_dir / "checkpoint-bundles" / label
        bundle_start = time.perf_counter()
        tdt_orchestrator.backup_managed_external_state(work_dir, bundle_root, study.experiment.managed_external_paths)
        bundle_backup_ms = (time.perf_counter() - bundle_start) * 1000.0

        wait_for_log_quiescence(work_dir)
        pre_offsets = snapshot_offsets(work_dir)
        checkpoint_prefixes = snapshot_prefixes(work_dir, pre_offsets)
        comparison_run_seconds = (
            study.experiment.comparison_window_seconds
            + study.experiment.comparison_boundary_guard_seconds
        )
        continue_for(session, comparison_run_seconds)
        wait_until_paused(session, timeout_sec=120.0)
        wait_for_log_quiescence(work_dir)
        reference = capture_window(work_dir, pre_offsets, study.experiment.hex_normalization)

        restore_bundle_start = time.perf_counter()
        tdt_orchestrator.restore_managed_external_state(work_dir, bundle_root, study.experiment.managed_external_paths)
        bundle_restore_ms = (time.perf_counter() - restore_bundle_start) * 1000.0

        restore_elapsed_ms = issue_restore(session, label)
        wait_for_log_quiescence(work_dir)
        post_offsets = snapshot_offsets(work_dir)
        continue_for(session, comparison_run_seconds)
        wait_until_paused(session, timeout_sec=120.0)
        wait_for_log_quiescence(work_dir)
        replay = capture_window(
            work_dir,
            post_offsets,
            study.experiment.hex_normalization,
            checkpoint_prefixes,
        )

        reference_artifacts = persist_window_snapshot(windows_dir, "reference", reference)
        replay_artifacts = persist_window_snapshot(windows_dir, "replay", replay)
        passed, comparisons, first_mismatches = compare_windows(reference, replay, diff_dir)
        strict_passed = all(
            item["stderr_equal"] and item["stdout_equal"] for item in comparisons
        )
        allowed_order_only_mismatches = [
            item
            for item in comparisons
            if item["stderr_order_only"] or item["stdout_order_only"]
        ]
        determinism_class = (
            "strict"
            if strict_passed
            else "validator_sync_contribution_order_only"
            if passed and allowed_order_only_mismatches
            else "byte_drift"
        )
        result = {
            "mode": "determinism",
            "setup_beacon_nodes": beacon_nodes,
            "validators_total": config.cluster.validators_total,
            "checkpoint_label": label,
            "ready_elapsed_seconds": ready_elapsed,
            "settle_seconds": study.experiment.settle_seconds,
            "comparison_window_seconds": study.experiment.comparison_window_seconds,
            "comparison_boundary_guard_seconds": study.experiment.comparison_boundary_guard_seconds,
            "comparison_run_seconds": comparison_run_seconds,
            "checkpoint_elapsed_ms": checkpoint_elapsed_ms,
            "restore_elapsed_ms": restore_elapsed_ms,
            "managed_external_backup_ms": bundle_backup_ms,
            "managed_external_restore_ms": bundle_restore_ms,
            "ready_summary": ready_summary,
            "checkpoint_sizes": checkpoint_artifact_sizes(meta_path, work_dir, label),
            "managed_external_bundle_bytes": bundle_size_bytes(bundle_root),
            "passed": passed,
            "strict_passed": strict_passed,
            "determinism_class": determinism_class,
            "allowed_order_only_mismatches": allowed_order_only_mismatches,
            "comparisons": comparisons,
            "first_mismatches": first_mismatches,
            "window_artifacts": {
                "root_dir": str(windows_dir),
                "reference_dir": reference_artifacts["dir"],
                "replay_dir": replay_artifacts["dir"],
                "reference_metadata": reference_artifacts["metadata"],
                "replay_metadata": replay_artifacts["metadata"],
            },
        }
        out = study.base.results_dir / f"determinism-setup-{beacon_nodes}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result
    finally:
        terminate_process(session)


def run_performance(study: StudyConfig, modules: dict[str, object], beacon_nodes: int, trials: int) -> dict[str, object]:
    tdt_orchestrator = modules["tdt_orchestrator"]
    tdt_logcheck = modules["tdt_logcheck"]
    trial_results: list[dict[str, object]] = []

    for trial in range(1, trials + 1):
        work_dir = study.base.work_root / f"performance-setup-{beacon_nodes}-trial-{trial}"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        config = build_tdt_config(study, modules, beacon_nodes, work_dir)
        tdt_orchestrator.ensure_binaries(config, "cprestore")
        tdt_orchestrator.prepare_runtime(config)

        session = launch_shadow(
            config,
            work_dir / "performance.log",
            study.experiment.restore_protocol_mode,
        )
        label = f"{study.experiment.checkpoint_label_prefix}-perf-{beacon_nodes}-trial-{trial}"
        try:
            wait_until_paused(session, timeout_sec=120.0)
            ready_elapsed, ready_summary = advance_until_ready(
                session,
                work_dir,
                tdt_logcheck.collect_counts,
                beacon_nodes,
                study.experiment.warmup_step_seconds,
                study.experiment.max_warmup_seconds,
            )
            if study.experiment.settle_seconds > 0:
                continue_for(session, study.experiment.settle_seconds)
            wait_until_paused(session, timeout_sec=120.0)

            checkpoint_elapsed_ms = issue_checkpoint(session, label)
            meta_path = work_dir / "shadow.data" / "checkpoints" / f"{label}.checkpoint.json"
            if not meta_path.exists():
                raise RuntimeError(f"checkpoint metadata missing: {meta_path}")

            bundle_root = work_dir / "checkpoint-bundles" / label
            bundle_start = time.perf_counter()
            tdt_orchestrator.backup_managed_external_state(work_dir, bundle_root, study.experiment.managed_external_paths)
            bundle_backup_ms = (time.perf_counter() - bundle_start) * 1000.0

            continue_for(session, study.experiment.comparison_window_seconds)
            wait_until_paused(session, timeout_sec=120.0)

            restore_bundle_start = time.perf_counter()
            tdt_orchestrator.restore_managed_external_state(work_dir, bundle_root, study.experiment.managed_external_paths)
            bundle_restore_ms = (time.perf_counter() - restore_bundle_start) * 1000.0

            restore_elapsed_ms = issue_restore(session, label)
            baseline = tdt_logcheck.collect_counts(work_dir)
            continue_for(session, study.experiment.warmup_step_seconds)
            wait_until_paused(session, timeout_sec=120.0)
            current = tdt_logcheck.collect_counts(work_dir)

            trial_results.append(
                {
                    "trial": trial,
                    "checkpoint_elapsed_ms": checkpoint_elapsed_ms,
                    "restore_elapsed_ms": restore_elapsed_ms,
                    "managed_external_backup_ms": bundle_backup_ms,
                    "managed_external_restore_ms": bundle_restore_ms,
                    "ready_elapsed_seconds": ready_elapsed,
                    "ready_summary": ready_summary,
                    "checkpoint_sizes": checkpoint_artifact_sizes(meta_path, work_dir, label),
                    "managed_external_bundle_bytes": bundle_size_bytes(bundle_root),
                    "baseline_before_post_restore_window": baseline,
                    "post_restore_progress": current,
                }
            )
        finally:
            terminate_process(session)

    checkpoint_ms = [float(item["checkpoint_elapsed_ms"]) for item in trial_results]
    restore_ms = [float(item["restore_elapsed_ms"]) for item in trial_results]
    result = {
        "mode": "performance",
        "setup_beacon_nodes": beacon_nodes,
        "validators_total": beacon_nodes * study.experiment.validators_per_beacon,
        "trials": trial_results,
        "summary": {
            "checkpoint_ms_min": min(checkpoint_ms),
            "checkpoint_ms_median": statistics.median(checkpoint_ms),
            "checkpoint_ms_max": max(checkpoint_ms),
            "restore_ms_min": min(restore_ms),
            "restore_ms_median": statistics.median(restore_ms),
            "restore_ms_max": max(restore_ms),
        },
    }
    out = study.base.results_dir / f"performance-setup-{beacon_nodes}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def render_report(results_dir: Path) -> None:
    determinism_files = sorted(results_dir.glob("determinism-setup-*.json"))
    performance_files = sorted(results_dir.glob("performance-setup-*.json"))
    lines = ["# Checkpoint Study Report", ""]

    if determinism_files:
        lines.extend(
            [
                "## Determinism",
                "",
                "| Setup | Validators | Pass | Strict | Class | Checkpoint ms | Restore ms |",
                "| --- | ---: | :---: | :---: | --- | ---: | ---: |",
            ]
        )
        for path in determinism_files:
            data = json.loads(path.read_text(encoding="utf-8"))
            strict_passed = data.get("strict_passed", data["passed"])
            determinism_class = data.get(
                "determinism_class",
                "strict" if strict_passed else "byte_drift",
            )
            lines.append(
                f"| {data['setup_beacon_nodes']} | {data['validators_total']} | {'yes' if data['passed'] else 'no'} | {'yes' if strict_passed else 'no'} | {determinism_class} | {data['checkpoint_elapsed_ms']:.2f} | {data['restore_elapsed_ms']:.2f} |"
            )
        lines.append("")

    if performance_files:
        lines.extend(
            [
                "## Performance",
                "",
                "| Setup | Validators | Trials | Checkpoint median ms | Restore median ms |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for path in performance_files:
            data = json.loads(path.read_text(encoding="utf-8"))
            summary = data["summary"]
            lines.append(
                f"| {data['setup_beacon_nodes']} | {data['validators_total']} | {len(data['trials'])} | {summary['checkpoint_ms_median']:.2f} | {summary['restore_ms_median']:.2f} |"
            )
        lines.append("")

    (results_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    study = load_study_config(args.config)
    if args.results_dir:
        study.base.results_dir = Path(args.results_dir).resolve()
    if args.work_root:
        study.base.work_root = Path(args.work_root).resolve()
    study.base.results_dir.mkdir(parents=True, exist_ok=True)
    study.base.work_root.mkdir(parents=True, exist_ok=True)

    modules = import_tdt_modules(study)
    trials = args.trials or study.experiment.performance_trials

    for setup in selected_setups(study, args.setup):
        if args.mode in {"determinism", "all"}:
            log(f"running determinism experiment for setup={setup}")
            result = run_determinism(study, modules, setup)
            log(f"determinism setup={setup} passed={result['passed']}")
        if args.mode in {"performance", "all"}:
            log(f"running performance experiment for setup={setup} trials={trials}")
            run_performance(study, modules, setup, trials)

    render_report(study.base.results_dir)
    log(f"results written to {study.base.results_dir}")


if __name__ == "__main__":
    main()
