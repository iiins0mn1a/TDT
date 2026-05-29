#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path

EXPERIMENT_DURATION_SECONDS = 1800
NETWORK_LATENCY = "100 ms"
NETWORK_BANDWIDTH = "1000 Gbit"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate TDT Shadow config")
    parser.add_argument("--nodes", type=int, default=4)
    parser.add_argument("--shared-geth-nodes", type=int, default=1)
    parser.add_argument("--validators", type=int, default=16)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--duration-seconds", type=int, default=EXPERIMENT_DURATION_SECONDS)
    parser.add_argument("--geth-bin", default="")
    parser.add_argument("--beacon-bin", default="")
    parser.add_argument("--validator-bin", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.nodes < 1:
        raise SystemExit("nodes must be >= 1")
    if args.shared_geth_nodes != 1:
        raise SystemExit("Only shared_geth_nodes = 1 is supported in this milestone")
    if args.validators < 0:
        raise SystemExit("validators must be >= 0")

    script_dir = Path(__file__).resolve().parent
    tdt_dir = script_dir.parent
    deps_dir = tdt_dir / "deps"
    repos_dir = deps_dir
    runtime_dir = Path(args.runtime_dir).resolve()
    output = Path(args.output).resolve()
    geth_bin = Path(args.geth_bin).resolve() if args.geth_bin else repos_dir / "go-ethereum/build/bin/geth"
    beacon_bin = Path(args.beacon_bin).resolve() if args.beacon_bin else repos_dir / "prysm/bazel-bin/cmd/beacon-chain/beacon-chain_/beacon-chain"
    validator_bin = Path(args.validator_bin).resolve() if args.validator_bin else repos_dir / "prysm/bazel-bin/cmd/validator/validator_/validator"

    shadow_epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    time_offset = int((now - shadow_epoch).total_seconds())
    last_validator_start = time_offset + 11 + (args.nodes - 1) * 7
    stop_time_seconds = last_validator_start + args.duration_seconds
    shadow_parallelism = os.environ.get("TDT_SHADOW_PARALLELISM", "").strip()
    if shadow_parallelism:
        try:
            shadow_parallelism_value = int(shadow_parallelism)
        except ValueError as e:
            raise SystemExit("TDT_SHADOW_PARALLELISM must be an integer") from e
        if shadow_parallelism_value < 0:
            raise SystemExit("TDT_SHADOW_PARALLELISM must be >= 0")
        shadow_parallelism_line = f"  parallelism: {shadow_parallelism_value}\n"
    else:
        shadow_parallelism_line = ""
    shadow_heartbeat_interval = os.environ.get(
        "TDT_SHADOW_HEARTBEAT_INTERVAL", "1 sec"
    ).strip()
    if not shadow_heartbeat_interval:
        raise SystemExit("TDT_SHADOW_HEARTBEAT_INTERVAL must not be empty")
    network_latency = os.environ.get("TDT_NETWORK_LATENCY", NETWORK_LATENCY).strip()
    if not network_latency:
        raise SystemExit("TDT_NETWORK_LATENCY must not be empty")
    worker_spinning = os.environ.get("TDT_SHADOW_USE_WORKER_SPINNING", "").strip().lower()
    cpu_pinning = os.environ.get("TDT_SHADOW_USE_CPU_PINNING", "").strip().lower()

    def parse_bool_env(raw: str, name: str) -> bool | None:
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        if raw == "":
            return None
        raise SystemExit(f"{name} must be a boolean")

    worker_spinning_value = parse_bool_env(
        worker_spinning, "TDT_SHADOW_USE_WORKER_SPINNING"
    )
    cpu_pinning_value = parse_bool_env(cpu_pinning, "TDT_SHADOW_USE_CPU_PINNING")
    experimental_lines = []
    if worker_spinning_value is not None:
        experimental_lines.append(
            f"  use_worker_spinning: {str(worker_spinning_value).lower()}"
        )
    if cpu_pinning_value is not None:
        experimental_lines.append(
            f"  use_cpu_pinning: {str(cpu_pinning_value).lower()}"
        )
    native_preemption = os.environ.get("TDT_NATIVE_PREEMPTION", "").strip().lower()
    if native_preemption in {"1", "true", "yes", "on"}:
        native_interval = os.environ.get(
            "TDT_NATIVE_PREEMPTION_NATIVE_INTERVAL", "100 ms"
        ).strip()
        sim_interval = os.environ.get(
            "TDT_NATIVE_PREEMPTION_SIM_INTERVAL", "10 ms"
        ).strip()
        if not native_interval:
            raise SystemExit("TDT_NATIVE_PREEMPTION_NATIVE_INTERVAL must not be empty")
        if not sim_interval:
            raise SystemExit("TDT_NATIVE_PREEMPTION_SIM_INTERVAL must not be empty")
        preemption_lines = [
            "  native_preemption_enabled: true",
            f"  native_preemption_native_interval: {native_interval}",
            f"  native_preemption_sim_interval: {sim_interval}",
        ]
        experimental_lines.extend(preemption_lines)
    elif native_preemption in {"", "0", "false", "no", "off"}:
        pass
    else:
        raise SystemExit("TDT_NATIVE_PREEMPTION must be a boolean")

    if experimental_lines:
        experimental_block = "experimental:\n" + "\n".join(experimental_lines) + "\n"
    else:
        experimental_block = ""

    def header() -> str:
        return f"""general:
  stop_time: {stop_time_seconds}
  heartbeat_interval: {shadow_heartbeat_interval}
{shadow_parallelism_line}network:
  graph:
    type: gml
    inline: |
      graph [
        directed 0
        node [
          id 0
          host_bandwidth_up \"{NETWORK_BANDWIDTH}\"
          host_bandwidth_down \"{NETWORK_BANDWIDTH}\"
        ]
        edge [
          source 0
          target 0
          latency \"{network_latency}\"
          packet_loss 0.0
        ]
      ]
{experimental_block}hosts:
  geth-node:
    network_node_id: 0
    ip_addr: 11.0.2.10
    processes:
    - path: {geth_bin}
      args: --networkid=32382 --http --http.api=eth,net,web3 --http.addr=0.0.0.0 --http.corsdomain=\"*\" --http.port=8000 --port=8400 --metrics.port=8300 --ws --ws.api=eth,net,web3 --ws.addr=0.0.0.0 --ws.origins=\"*\" --ws.port=8100 --authrpc.vhosts=\"*\" --authrpc.addr=0.0.0.0 --authrpc.jwtsecret={runtime_dir}/network/node-1/execution/jwtsecret --authrpc.port=8200 --datadir={runtime_dir}/network/node-1/execution --password={runtime_dir}/network/node-1/geth_password.txt --identity=node-0 --maxpendpeers=0 --verbosity=3 --syncmode=full --ipcdisable --nodiscover --maxpeers=0 --nat=none
      start_time: {time_offset + 1}
"""

    def beacon_host(idx: int) -> str:
        beacon_ip = f"11.0.0.{idx - 1}"
        rpc_port = 4000 + idx - 1
        http_port = 4100 + idx - 1
        tcp_port = 4200 + idx - 1
        udp_port = 4300 + idx - 1
        mon_port = 4400 + idx - 1
        beacon_start = time_offset + 6 + (idx - 1) * 7
        record_start = beacon_start + 1
        return f"""  prysm-beacon-{idx}:
    network_node_id: 0
    ip_addr: {beacon_ip}
    processes:
    - path: {tdt_dir}/scripts/start_beacon_shadow.sh
      args: \"{idx} {rpc_port} {http_port} {tcp_port} {udp_port} {mon_port}\"
      environment:
        TDT_RUNTIME_DIR: \"{runtime_dir}\"
        TDT_BEACON_BIN: \"{beacon_bin}\"
      start_time: {beacon_start}
      shutdown_time: {stop_time_seconds - 1}
      shutdown_signal: SIGKILL
      expected_final_state: {{signaled: SIGKILL}}
    - path: {tdt_dir}/scripts/record_beacon_peer.sh
      args: \"{idx} {tcp_port}\"
      environment:
        TDT_RUNTIME_DIR: \"{runtime_dir}\"
      start_time: {record_start}
"""

    def validator_host(idx: int, start_index: int, count: int) -> str:
        validator_ip = f"11.0.1.{idx - 1}"
        beacon_ip = f"11.0.0.{idx - 1}"
        beacon_rpc_port = 4000 + idx - 1
        node_dir = runtime_dir / "network" / f"node-{idx}"
        beacon_record_time = time_offset + 7 + (idx - 1) * 7
        validator_start = beacon_record_time + 4
        return f"""  prysm-validator-{idx}:
    network_node_id: 0
    ip_addr: {validator_ip}
    processes:
    - path: {validator_bin}
      args: --beacon-rpc-provider={beacon_ip}:{beacon_rpc_port} --datadir={node_dir}/consensus/validatordata --accept-terms-of-use --interop-num-validators={count} --interop-start-index={start_index} --rpc-port=7000 --grpc-gateway-port=7100 --monitoring-port=7200 --graffiti=\"node-{idx - 1}\" --chain-config-file={node_dir}/consensus/config.yml
      environment:
        SPEC_LOG_NODE: \"node-{idx}\"
      start_time: {validator_start}
"""

    base_per_node = args.validators // args.nodes
    remainder = args.validators % args.nodes
    content = header()
    start_index = 0
    for idx in range(1, args.nodes + 1):
        content += beacon_host(idx)
        count = base_per_node + (remainder if idx == args.nodes else 0)
        content += validator_host(idx, start_index, count)
        start_index += count

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote TDT shadow.yaml to {output}")
    print(f"time_offset={time_offset}")
    print(f"last_validator_start={last_validator_start}")
    print(f"stop_time={stop_time_seconds}")
    print(f"network_latency={network_latency}")


if __name__ == "__main__":
    main()
