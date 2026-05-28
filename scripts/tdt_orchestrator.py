#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Iterable

from tdt_config import TdtConfig, load_tdt_config


def log(message: str) -> None:
    print(f"[TDT] {message}")


def warn(message: str) -> None:
    print(f"[TDT] WARNING: {message}")


def ensure_executable(path: Path, label: str) -> None:
    if not path.exists() or not os.access(path, os.X_OK):
        raise FileNotFoundError(f"{label} not found or not executable: {path}")


def resolve_script(config: TdtConfig, name: str) -> Path:
    return (config.root_dir / "scripts" / name).resolve()


def prepare_runtime_layout(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "network").mkdir(parents=True, exist_ok=True)


def copy_seed_inputs(config: TdtConfig) -> None:
    network_dir = config.work_dir / "network"
    network_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config.assets_dir / "config.yml", network_dir / "config.yml")
    shutil.copy2(config.assets_dir / "genesis.json", network_dir / "genesis.json")


def reset_peer_file(work_dir: Path) -> None:
    (work_dir / "beacon_peers.txt").write_text("", encoding="utf-8")


def backup_managed_external_state(work_dir: Path, backup_root: Path, managed_paths: Iterable[str]) -> None:
    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    for rel in managed_paths:
        src = work_dir / rel
        dst = backup_root / rel
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text("", encoding="utf-8")


def restore_managed_external_state(work_dir: Path, backup_root: Path, managed_paths: Iterable[str]) -> None:
    for rel in managed_paths:
        dst = work_dir / rel
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


def run_command(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    log("Running: " + " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def generate_shadow_yaml(config: TdtConfig) -> None:
    cmd = [
        sys.executable,
        str(resolve_script(config, "generate_shadow_yaml.py")),
        "--nodes",
        str(config.cluster.beacon_nodes),
        "--shared-geth-nodes",
        str(config.cluster.shared_geth_nodes),
        "--validators",
        str(config.cluster.validators_total),
        "--runtime-dir",
        str(config.work_dir),
        "--output",
        str(config.work_dir / "shadow.yaml"),
        "--duration-seconds",
        str(config.simulation.duration_seconds),
        "--geth-bin",
        str(config.geth_bin),
        "--beacon-bin",
        str(config.beacon_bin),
        "--validator-bin",
        str(config.validator_bin),
    ]
    run_command(cmd, cwd=config.root_dir)


def generate_consensus_genesis(config: TdtConfig) -> None:
    cmd = [
        str(config.prysmctl_bin),
        "testnet",
        "generate-genesis",
        "--fork=deneb",
        f"--num-validators={config.cluster.validators_total}",
        f"--chain-config-file={config.work_dir / 'network' / 'config.yml'}",
        f"--geth-genesis-json-in={config.work_dir / 'network' / 'genesis.json'}",
        f"--output-ssz={config.work_dir / 'network' / 'genesis.ssz'}",
        f"--geth-genesis-json-out={config.work_dir / 'network' / 'genesis.json'}",
    ]
    run_command(cmd, cwd=config.root_dir)


def create_network_db(config: TdtConfig) -> None:
    env = os.environ.copy()
    env["NODE_COUNT"] = str(config.cluster.beacon_nodes)
    env["RUNTIME_DIR"] = str(config.work_dir)
    run_command([str(resolve_script(config, "create_network_db.sh"))], cwd=config.root_dir, env=env)


def init_geth_genesis(config: TdtConfig) -> None:
    cmd = [
        str(config.geth_bin),
        "init",
        f"--datadir={config.work_dir / 'network' / 'node-1' / 'execution'}",
        str(config.work_dir / "network" / "node-1" / "execution" / "genesis.json"),
    ]
    run_command(cmd, cwd=config.root_dir)


def clean_runtime(config: TdtConfig) -> None:
    target = config.work_dir
    if target.exists():
        log(f"Cleaning runtime directory {target}")
        shutil.rmtree(target)
    prepare_runtime_layout(target)


def prepare_runtime(config: TdtConfig) -> None:
    if config.simulation.clean_runtime_before_prepare:
        clean_runtime(config)
    else:
        prepare_runtime_layout(config.work_dir)
    copy_seed_inputs(config)
    reset_peer_file(config.work_dir)
    generate_shadow_yaml(config)
    generate_consensus_genesis(config)
    create_network_db(config)
    init_geth_genesis(config)
    backup_managed_external_state(
        config.work_dir,
        config.work_dir / "network.bck",
        config.checkpoint_restore.managed_external_paths,
    )
    log(f"Prepared runtime at {config.work_dir}")


def maybe_pause_for_edit(config: TdtConfig, interactive: bool) -> None:
    if interactive and config.simulation.edit_shadow_yaml_before_run:
        input(
            f"shadow.yaml has been generated at {config.work_dir / 'shadow.yaml'}. "
            "Edit it if needed, then press Enter to continue..."
        )


class ShadowPanelSession:
    def __init__(self, process: subprocess.Popen[bytes], socket_path: Path, log_path: Path) -> None:
        self.process = process
        self.socket_path = socket_path
        self.log_path = log_path
        self.sock: socket.socket | None = None


def _log_relay_thread(stream, log_path: Path) -> None:
    with log_path.open("ab") as handle:
        while True:
            chunk = stream.readline()
            if not chunk:
                break
            handle.write(chunk)
            handle.flush()


def launch_shadow_with_panel(config: TdtConfig) -> ShadowPanelSession:
    socket_path = config.work_dir / "control.sock"
    if socket_path.exists():
        socket_path.unlink()
    shadow_log = config.work_dir / "shadow-test.log"
    env = os.environ.copy()
    env["SHADOW_CONTROL_SOCKET"] = str(socket_path)
    env["CRIU_BIN"] = str(config.criu_bin)
    env["SHADOW_RESTORE_PROTOCOL_MODE"] = config.checkpoint_restore.restore_protocol_mode
    process = subprocess.Popen(
        [str(config.shadow_bin), str(config.work_dir / "shadow.yaml")],
        cwd=str(config.work_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    thread = threading.Thread(
        target=_log_relay_thread,
        args=(process.stdout, shadow_log),
        daemon=True,
    )
    thread.start()
    return ShadowPanelSession(process=process, socket_path=socket_path, log_path=shadow_log)


def panel_connect(session: ShadowPanelSession, timeout_sec: float = 60.0) -> socket.socket:
    if session.sock is not None:
        return session.sock
    start = time.time()
    while time.time() - start < timeout_sec:
        if session.process.poll() is not None:
            raise RuntimeError(f"Shadow exited before control socket was ready (code={session.process.returncode})")
        if session.socket_path.exists():
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(str(session.socket_path))
                session.sock = sock
                return sock
            except OSError:
                pass
        time.sleep(0.2)
    raise TimeoutError(f"control socket {session.socket_path} did not become ready in time")


def panel_send(session: ShadowPanelSession, cmd: dict, timeout_sec: float = 300.0) -> dict:
    sock = panel_connect(session)
    sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
    sock.settimeout(timeout_sec)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("control socket closed by Shadow")
        buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def reconnect_after_restore(session: ShadowPanelSession, timeout_sec: float = 60.0) -> None:
    if session.sock is not None:
        try:
            session.sock.close()
        except OSError:
            pass
        session.sock = None
    panel_connect(session, timeout_sec=timeout_sec)


def panel_status(session: ShadowPanelSession) -> dict:
    return panel_send(session, {"cmd": "status"}, timeout_sec=30.0)


def panel_is_paused(status_resp: dict) -> bool:
    message = status_resp.get("message") or ""
    return "sim_waiting=true" in message


def wait_until_paused(session: ShadowPanelSession, timeout_sec: float = 300.0) -> dict:
    start = time.time()
    last = None
    while time.time() - start < timeout_sec:
        status = panel_status(session)
        last = status
        if panel_is_paused(status):
            return status
        if session.process.poll() is not None:
            raise RuntimeError(f"Shadow exited while waiting for pause (code={session.process.returncode})")
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for Shadow to pause; last status={last}")


def print_panel_help() -> None:
    print(
        "\n".join(
            [
                "Panel commands:",
                "  p              pause at next window boundary",
                "  c              continue until paused",
                "  cN             continue for N simulated seconds, then pause",
                "  n              run exactly one window, then pause",
                "  info | s       show next-window hosts/PIDs while paused",
                "  s:<pid>        print gdb attach hint",
                "  cp [label]     checkpoint and back up managed external state",
                "  restore LABEL  restore managed external state and Shadow checkpoint",
                "  r | rN         external restart from t=0, after restoring initial managed external state",
                "  status         show control-socket status",
                "  summary        summarize current runtime logs",
                "  help           show this help",
                "  quit           terminate the panel",
            ]
        )
    )


def run_interactive_panel(config: TdtConfig) -> None:
    log("Starting Shadow interactive panel")
    warn("Panel uses Shadow control socket. Use panel commands, not direct Shadow stdin.")
    session = launch_shadow_with_panel(config)
    try:
        wait_until_paused(session, timeout_sec=120.0)
        print(f"Shadow log: {session.log_path}")
        print_panel_help()
        print("Shadow is paused at start. Use c/c10/n/info/cp/restore/r commands here.\n")

        backup_root = config.work_dir / "network.bck"
        checkpoint_root = config.work_dir / "checkpoint-bundles"
        checkpoint_root.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                raw = input("shadow-panel> ").strip()
            except EOFError:
                print()
                break
            if not raw:
                continue
            if raw in {"quit", "exit"}:
                break
            if raw in {"help", "h", "?"}:
                print_panel_help()
                continue
            if raw == "status":
                print(json.dumps(panel_status(session), indent=2, sort_keys=True))
                continue
            if raw == "summary":
                print_summary(config)
                continue
            if raw in {"info", "s"}:
                resp = panel_send(session, {"cmd": "info"}, timeout_sec=60.0)
                if resp.get("status") == "ok":
                    print((resp.get("message") or "").rstrip())
                else:
                    print(resp)
                continue
            if raw.startswith("s:"):
                pid = raw[2:]
                print(f"Attach gdb manually with: gdb -p {pid}")
                continue
            if raw == "p":
                resp = panel_send(session, {"cmd": "pause"}, timeout_sec=30.0)
                print(resp.get("message") or resp)
                status = wait_until_paused(session, timeout_sec=300.0)
                print(f"Paused at sim_time_ns={status.get('sim_time_ns')}")
                continue
            if raw == "c":
                resp = panel_send(session, {"cmd": "continue"}, timeout_sec=30.0)
                print(resp.get("message") or resp)
                continue
            if raw == "n":
                resp = panel_send(session, {"cmd": "step_one_window"}, timeout_sec=30.0)
                print(resp.get("message") or resp)
                status = wait_until_paused(session, timeout_sec=120.0)
                print(f"Paused at sim_time_ns={status.get('sim_time_ns')}")
                continue
            if raw.startswith("c") and raw != "cp":
                rest = raw[1:]
                if rest.isdigit():
                    seconds = int(rest)
                    resp = panel_send(
                        session,
                        {"cmd": "continue_for", "duration_ns": seconds * 1_000_000_000},
                        timeout_sec=30.0,
                    )
                    print(resp.get("message") or resp)
                    status = wait_until_paused(session, timeout_sec=max(300.0, seconds + 60.0))
                    print(f"Paused at sim_time_ns={status.get('sim_time_ns')}")
                    continue
            if raw == "r" or (raw.startswith("r") and raw[1:].isdigit()):
                restore_managed_external_state(
                    config.work_dir,
                    backup_root,
                    config.checkpoint_restore.managed_external_paths,
                )
                run_until_ns = None
                if raw != "r":
                    run_until_ns = int(raw[1:]) * 1_000_000_000
                resp = panel_send(
                    session,
                    {"cmd": "restart", **({"run_until_ns": run_until_ns} if run_until_ns is not None else {})},
                    timeout_sec=300.0,
                )
                print(resp.get("message") or f"restart issued at sim_time_ns={resp.get('sim_time_ns')}")
                status = wait_until_paused(session, timeout_sec=300.0)
                print(f"Paused after restart at sim_time_ns={status.get('sim_time_ns')}")
                continue
            if raw == "cp" or raw.startswith("cp "):
                label = config.checkpoint_restore.checkpoint_label
                if raw.startswith("cp "):
                    label = raw.split(maxsplit=1)[1].strip()
                resp = panel_send(session, {"cmd": "checkpoint", "label": label}, timeout_sec=300.0)
                if resp.get("status") != "ok":
                    print(resp)
                    continue
                bundle_root = checkpoint_root / label
                backup_managed_external_state(
                    config.work_dir,
                    bundle_root,
                    config.checkpoint_restore.managed_external_paths,
                )
                print(f"Checkpoint '{label}' completed and external state saved to {bundle_root}")
                continue
            if raw.startswith("restore "):
                label = raw.split(maxsplit=1)[1].strip()
                bundle_root = checkpoint_root / label
                if not bundle_root.exists():
                    print(f"No external-state bundle found for checkpoint '{label}' at {bundle_root}")
                    continue
                restore_managed_external_state(
                    config.work_dir,
                    bundle_root,
                    config.checkpoint_restore.managed_external_paths,
                )
                try:
                    resp = panel_send(session, {"cmd": "restore", "label": label}, timeout_sec=600.0)
                except ConnectionError:
                    reconnect_after_restore(session, timeout_sec=120.0)
                    resp = panel_status(session)
                print(resp.get("message") or f"restore issued for '{label}'")
                status = wait_until_paused(session, timeout_sec=300.0)
                print(f"Paused after restore at sim_time_ns={status.get('sim_time_ns')}")
                continue

            print("Unknown command. Use: p | c | cN | n | info | s:<pid> | cp [label] | restore LABEL | r | rN | status | summary | quit")
    finally:
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


def run_smoke(config: TdtConfig, interactive: bool) -> None:
    log("Starting Shadow smoke run")
    shadow_yaml = config.work_dir / "shadow.yaml"
    shadow_log = config.work_dir / "shadow-test.log"
    if interactive:
        log(f"Interactive smoke mode: commands go to this terminal; log mirrored to {shadow_log}")
        command = f"{shlex.quote(str(config.shadow_bin))} {shlex.quote(str(shadow_yaml))} 2>&1 | tee {shlex.quote(str(shadow_log))}"
        result = subprocess.run(["bash", "-lc", command], cwd=str(config.work_dir))
        exit_code = result.returncode
    else:
        # Shadow's run-control mode pauses at t=0 waiting for stdin. In
        # non-interactive smoke runs, continue once so the run can complete
        # while still capturing all Shadow output in the log file.
        with shadow_log.open("wb") as handle:
            result = subprocess.run(
                [str(config.shadow_bin), str(shadow_yaml)],
                cwd=str(config.work_dir),
                input=b"c\n",
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
        exit_code = result.returncode
    log(f"Shadow exited with code {exit_code}")
    verify_smoke(config)


def run_cprestore(config: TdtConfig) -> None:
    warn("Known limitation: only managed external paths are rewound; shadow.data is not rewound")
    cmd = [
        sys.executable,
        str(resolve_script(config, "control_cp_restore.py")),
        "--shadow-bin",
        str(config.shadow_bin),
        "--config",
        str(config.work_dir / "shadow.yaml"),
        "--runtime-dir",
        str(config.work_dir),
        "--expected-nodes",
        str(config.cluster.beacon_nodes),
        "--checkpoint-label",
        config.checkpoint_restore.checkpoint_label,
        "--warmup-step-seconds",
        "60",
        "--max-warmup-seconds",
        str(config.checkpoint_restore.warmup_seconds),
        "--post-checkpoint-seconds",
        str(config.checkpoint_restore.post_checkpoint_seconds),
        "--post-restore-step-seconds",
        str(config.checkpoint_restore.post_restore_step_seconds),
        "--post-restore-steps",
        str(config.checkpoint_restore.post_restore_steps),
        "--restore-protocol-mode",
        config.checkpoint_restore.restore_protocol_mode,
    ]
    for rel in config.checkpoint_restore.managed_external_paths:
        cmd.extend(["--managed-external-path", rel])
    env = os.environ.copy()
    env["CRIU_BIN"] = str(config.criu_bin)
    run_command(cmd, cwd=config.root_dir, env=env)


def verify_smoke(config: TdtConfig) -> None:
    cmd = [
        sys.executable,
        str(resolve_script(config, "tdt_logcheck.py")),
        "verify-smoke",
        "--runtime-dir",
        str(config.work_dir),
        "--expected-nodes",
        str(config.cluster.beacon_nodes),
    ]
    run_command(cmd, cwd=config.root_dir)


def print_summary(config: TdtConfig) -> None:
    cmd = [
        sys.executable,
        str(resolve_script(config, "tdt_logcheck.py")),
        "summary",
        "--runtime-dir",
        str(config.work_dir),
        "--expected-nodes",
        str(config.cluster.beacon_nodes),
    ]
    run_command(cmd, cwd=config.root_dir)


def ensure_binaries(config: TdtConfig, mode: str) -> None:
    ensure_executable(config.shadow_bin, "shadow")
    ensure_executable(config.prysmctl_bin, "prysmctl")
    ensure_executable(config.geth_bin, "geth")
    ensure_executable(config.beacon_bin, "beacon-chain")
    ensure_executable(config.validator_bin, "validator")
    if mode == "cprestore":
        ensure_executable(config.criu_bin, "criu")


def apply_cli_overrides(config: TdtConfig, args: argparse.Namespace) -> TdtConfig:
    if args.mode:
        config.simulation.default_mode = args.mode
    if args.interactive is not None:
        config.simulation.interactive = args.interactive
    if args.nodes is not None:
        config.cluster.beacon_nodes = args.nodes
    if args.validators is not None:
        config.cluster.validators_total = args.validators
    if args.work_dir is not None:
        config.simulation.work_dir = args.work_dir
    if args.duration_seconds is not None:
        config.simulation.duration_seconds = args.duration_seconds
    if args.no_clean:
        config.simulation.clean_runtime_before_prepare = False
    if args.shadow_bin:
        config.binaries.shadow = args.shadow_bin
    if args.prysmctl_bin:
        config.binaries.prysmctl = args.prysmctl_bin
    if args.criu_bin:
        config.binaries.criu = args.criu_bin
    if args.warmup_seconds is not None:
        config.checkpoint_restore.warmup_seconds = args.warmup_seconds
    if args.post_checkpoint_seconds is not None:
        config.checkpoint_restore.post_checkpoint_seconds = args.post_checkpoint_seconds
    if args.post_restore_step_seconds is not None:
        config.checkpoint_restore.post_restore_step_seconds = args.post_restore_step_seconds
    if args.post_restore_steps is not None:
        config.checkpoint_restore.post_restore_steps = args.post_restore_steps
    return config


def run_once(config: TdtConfig, mode: str, interactive: bool) -> None:
    ensure_binaries(config, mode)
    prepare_runtime(config)
    maybe_pause_for_edit(config, interactive)
    if interactive:
        run_interactive_panel(config)
    elif mode == "smoke":
        run_smoke(config, interactive)
    elif mode == "cprestore":
        run_cprestore(config)
    else:
        raise ValueError(f"Unsupported mode: {mode}")


def command_shell(config: TdtConfig) -> None:
    log("Entering TDT command shell. Type 'help' for commands.")
    while True:
        try:
            raw = input("tdt> ").strip()
        except EOFError:
            print()
            return
        if not raw:
            continue
        cmd = raw.lower()
        if cmd in {"quit", "exit"}:
            return
        if cmd in {"help", "h", "?"}:
            print(
                "Commands: help, show, prepare, smoke, cprestore, summary, reload, clean, paths, quit"
            )
            continue
        if cmd == "show":
            print(json.dumps(config.to_display_dict(), indent=2, sort_keys=True))
            continue
        if cmd == "paths":
            print("\n".join(config.checkpoint_restore.managed_external_paths))
            continue
        if cmd == "reload":
            config = load_tdt_config(config.config_path)
            log(f"Reloaded {config.config_path}")
            continue
        if cmd == "clean":
            clean_runtime(config)
            continue
        if cmd == "prepare":
            prepare_runtime(config)
            continue
        if cmd == "summary":
            print_summary(config)
            continue
        if cmd == "smoke":
            ensure_binaries(config, "smoke")
            prepare_runtime(config)
            maybe_pause_for_edit(config, True)
            run_smoke(config, True)
            continue
        if cmd == "cprestore":
            ensure_binaries(config, "cprestore")
            prepare_runtime(config)
            maybe_pause_for_edit(config, True)
            run_cprestore(config)
            continue
        warn(f"Unknown command: {raw}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TDT config-driven Shadow orchestrator")
    parser.add_argument("--config", default=str((Path(__file__).resolve().parent.parent / "tdt_config.toml")))
    parser.add_argument("--mode", choices=("smoke", "cprestore"))
    parser.add_argument("--interactive", dest="interactive", action="store_true")
    parser.add_argument("--non-interactive", dest="interactive", action="store_false")
    parser.add_argument("--command-shell", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--show-config", action="store_true")
    parser.add_argument("--nodes", type=int)
    parser.add_argument("--validators", type=int)
    parser.add_argument("--work-dir")
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("--shadow-bin")
    parser.add_argument("--prysmctl-bin")
    parser.add_argument("--criu-bin")
    parser.add_argument("--warmup-seconds", type=int)
    parser.add_argument("--post-checkpoint-seconds", type=int)
    parser.add_argument("--post-restore-step-seconds", type=int)
    parser.add_argument("--post-restore-steps", type=int)
    parser.add_argument("--no-clean", action="store_true")
    parser.set_defaults(interactive=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = apply_cli_overrides(load_tdt_config(args.config), args)

    if args.show_config:
        print(json.dumps(config.to_display_dict(), indent=2, sort_keys=True))
        return
    if args.summary:
        print_summary(config)
        return
    if args.prepare_only:
        ensure_binaries(config, config.simulation.default_mode)
        prepare_runtime(config)
        return

    interactive = config.simulation.interactive
    mode = config.simulation.default_mode
    if args.command_shell:
        command_shell(config)
        return
    run_once(config, mode, interactive)


if __name__ == "__main__":
    main()
