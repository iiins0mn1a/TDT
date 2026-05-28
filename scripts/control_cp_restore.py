#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tdt_logcheck import collect_counts

@dataclass
class ShadowSession:
    process: subprocess.Popen
    socket_path: Path
    sock: socket.socket | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real-client Shadow cp/restore in TDT")
    parser.add_argument("--shadow-bin", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--socket-path", default="")
    parser.add_argument("--connect-timeout", type=float, default=60.0)
    parser.add_argument("--response-timeout", type=float, default=180.0)
    parser.add_argument("--checkpoint-label", default="tdt_real_clients")
    parser.add_argument("--expected-nodes", type=int, default=4)
    parser.add_argument("--managed-external-path", action="append", default=[])
    parser.add_argument("--warmup-step-seconds", type=int, default=60)
    parser.add_argument("--max-warmup-seconds", type=int, default=900)
    parser.add_argument("--post-checkpoint-seconds", type=int, default=120)
    parser.add_argument("--post-restore-step-seconds", type=int, default=60)
    parser.add_argument("--post-restore-steps", type=int, default=6)
    parser.add_argument("--restore-protocol-mode", default="deterministic_v2")
    return parser.parse_args()


def wait_for_socket(path: Path, timeout_sec: float, process: subprocess.Popen) -> socket.socket:
    start = time.time()
    while time.time() - start < timeout_sec:
        if process.poll() is not None:
            raise RuntimeError(f"Shadow exited before socket became available (code={process.returncode})")
        if path.exists():
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(str(path))
                return sock
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                pass
        time.sleep(0.3)
    raise TimeoutError(f"socket {path} not available within {timeout_sec}s")


def send_command(sock: socket.socket, cmd: dict, timeout_sec: float) -> dict:
    sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
    sock.settimeout(timeout_sec)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("socket closed by Shadow")
        buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def expect_ok(resp: dict, label: str) -> None:
    if resp.get("status") != "ok":
        raise AssertionError(f"{label}: expected ok, got {resp!r}")


def status(session: ShadowSession, timeout_sec: float) -> dict:
    assert session.sock is not None
    resp = send_command(session.sock, {"cmd": "status"}, timeout_sec)
    expect_ok(resp, "status")
    return resp


def _is_paused(resp: dict) -> bool:
    return "sim_waiting=true" in (resp.get("message") or "")


def wait_until_paused(session: ShadowSession, timeout_sec: float) -> dict:
    deadline = time.time() + timeout_sec
    last_resp = None
    while time.time() < deadline:
        last_resp = status(session, min(30.0, timeout_sec))
        if _is_paused(last_resp):
            return last_resp
        if session.process.poll() is not None:
            raise RuntimeError(
                f"Shadow exited while waiting to pause (code={session.process.returncode})"
            )
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for Shadow to pause; last status={last_resp}")


def launch_shadow(args: argparse.Namespace, runtime_dir: Path, socket_path: Path) -> ShadowSession:
    if socket_path.exists():
        socket_path.unlink()
    env = os.environ.copy()
    env["SHADOW_CONTROL_SOCKET"] = str(socket_path)
    env["SHADOW_RESTORE_PROTOCOL_MODE"] = args.restore_protocol_mode
    log_path = runtime_dir / "cprestore.log"
    handle = log_path.open("wb")
    process = subprocess.Popen(
        [args.shadow_bin, args.config],
        cwd=str(runtime_dir),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    return ShadowSession(process=process, socket_path=socket_path)


def ensure_connected(session: ShadowSession, timeout_sec: float) -> None:
    if session.sock is None:
        session.sock = wait_for_socket(session.socket_path, timeout_sec, session.process)


def continue_for(session: ShadowSession, duration_seconds: int, timeout_sec: float) -> None:
    assert session.sock is not None
    resp = send_command(session.sock, {"cmd": "continue_for", "duration_ns": duration_seconds * 1_000_000_000}, timeout_sec)
    expect_ok(resp, f"continue_for({duration_seconds}s)")
    wait_until_paused(session, max(timeout_sec, duration_seconds + 60.0))


def restore_with_reconnect(session: ShadowSession, label: str, connect_timeout: float, response_timeout: float) -> None:
    try:
        assert session.sock is not None
        resp = send_command(session.sock, {"cmd": "restore", "label": label}, max(response_timeout, 300.0))
        expect_ok(resp, "restore")
    except ConnectionError:
        if session.sock is not None:
            try:
                session.sock.close()
            except OSError:
                pass
            session.sock = None
        ensure_connected(session, connect_timeout)
        assert session.sock is not None
        wait_until_paused(session, max(response_timeout, 300.0))




def checkpoint_json_path(runtime_dir: Path, label: str) -> Path:
    return runtime_dir / "shadow.data" / "checkpoints" / f"{label}.checkpoint.json"

def role_names(expected_nodes: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    beacons = tuple(f"beacon-{idx}" for idx in range(1, expected_nodes + 1))
    validators = tuple(f"validator-{idx}" for idx in range(1, expected_nodes + 1))
    return beacons, validators


def backup_checkpoint_state(runtime_dir: Path, managed_paths: list[str]) -> Path:
    backup_root = runtime_dir / "checkpoint.bck"
    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    for rel in managed_paths:
        src = runtime_dir / rel
        dst = backup_root / rel
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text("", encoding="utf-8")
    return backup_root


def restore_checkpoint_state(runtime_dir: Path, backup_root: Path, managed_paths: list[str]) -> None:
    for rel in managed_paths:
        dst = runtime_dir / rel
        src = backup_root / rel
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def ready_for_checkpoint(summary: dict, expected_nodes: int) -> bool:
    beacon_roles, validator_roles = role_names(expected_nodes)
    if summary["peer_lines"] < expected_nodes:
        return False
    if sum(summary["geth"].values()) == 0:
        return False
    for role in beacon_roles:
        if sum(summary["beacons"].get(role, {}).values()) == 0:
            return False
    for role in validator_roles:
        if sum(summary["validators"].get(role, {}).values()) == 0:
            return False
    return True


def role_progress(before: dict, after: dict) -> bool:
    return sum(after.values()) > sum(before.values())


def verify_post_restore(baseline: dict, runtime_dir: Path, expected_nodes: int) -> None:
    beacon_roles, validator_roles = role_names(expected_nodes)
    current = collect_counts(runtime_dir)
    if current["peer_lines"] < expected_nodes:
        raise AssertionError(f"expected {expected_nodes} peer lines after restore, got {current['peer_lines']}")
    if not role_progress(baseline["geth"], current["geth"]):
        raise AssertionError("geth logs did not progress after restore")
    for role in beacon_roles:
        if not role_progress(baseline["beacons"].get(role, {}), current["beacons"].get(role, {})):
            raise AssertionError(f"{role} logs did not progress after restore")
    for role in validator_roles:
        if not role_progress(baseline["validators"].get(role, {}), current["validators"].get(role, {})):
            raise AssertionError(f"{role} logs did not progress after restore")


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


def main() -> None:
    args = parse_args()
    runtime_dir = Path(args.runtime_dir).resolve()
    socket_path = Path(args.socket_path).resolve() if args.socket_path else runtime_dir / "control.sock"
    managed_paths = args.managed_external_path or ["network", "beacon_peers.txt"]

    session = launch_shadow(args, runtime_dir, socket_path)
    try:
        ensure_connected(session, args.connect_timeout)
        elapsed = 0
        while elapsed < args.max_warmup_seconds:
            continue_for(session, args.warmup_step_seconds, args.response_timeout)
            elapsed += args.warmup_step_seconds
            summary = collect_counts(runtime_dir)
            if ready_for_checkpoint(summary, args.expected_nodes):
                break
        else:
            raise AssertionError("real-client network did not reach checkpoint-ready state before timeout")

        assert session.sock is not None
        checkpoint_resp = send_command(session.sock, {"cmd": "checkpoint", "label": args.checkpoint_label}, args.response_timeout)
        expect_ok(checkpoint_resp, "checkpoint")
        checkpoint_json = checkpoint_json_path(runtime_dir, args.checkpoint_label)
        if not checkpoint_json.exists():
            raise AssertionError(f"checkpoint metadata missing after checkpoint: {checkpoint_json}")
        backup_root = backup_checkpoint_state(runtime_dir, managed_paths)
        continue_for(session, args.post_checkpoint_seconds, args.response_timeout)
        baseline = collect_counts(runtime_dir)
        restore_checkpoint_state(runtime_dir, backup_root, managed_paths)
        restore_with_reconnect(session, args.checkpoint_label, args.connect_timeout, args.response_timeout)

        for _ in range(args.post_restore_steps):
            continue_for(session, args.post_restore_step_seconds, args.response_timeout)
            try:
                verify_post_restore(baseline, runtime_dir, args.expected_nodes)
                print("[verify] PASS: real-client Shadow network progressed after checkpoint/restore")
                print(json.dumps(collect_counts(runtime_dir), indent=2, sort_keys=True))
                print("[verify] NOTE: host-side shadow.data rollback is intentionally not implemented in this milestone")
                return
            except AssertionError:
                pass
        verify_post_restore(baseline, runtime_dir, args.expected_nodes)
        print("[verify] PASS: real-client Shadow network progressed after checkpoint/restore")
        print(json.dumps(collect_counts(runtime_dir), indent=2, sort_keys=True))
        print("[verify] NOTE: host-side shadow.data rollback is intentionally not implemented in this milestone")
    finally:
        terminate_process(session)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[verify] FAIL: {exc}", file=sys.stderr)
        raise
