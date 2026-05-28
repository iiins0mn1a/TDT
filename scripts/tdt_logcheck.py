#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

GETH_PATTERNS = (
    "Chain head was updated",
    "Imported new potential chain segment",
    "Starting work on payload",
)
BEACON_PATTERNS = (
    "Synced new block",
    "Finished applying state transition",
    "Connected peers",
)
VALIDATOR_PATTERNS = (
    "Submitted new block",
    "Submitted new sync message",
)
RECORDER_PATTERNS = (
    "Recorded peer:",
)


def _host_role(hostname: str) -> tuple[str, int | None]:
    if hostname == "geth-node":
        return ("geth", None)
    if hostname.startswith("prysm-beacon-"):
        return ("beacon", int(hostname.rsplit("-", 1)[1]))
    if hostname.startswith("prysm-validator-"):
        return ("validator", int(hostname.rsplit("-", 1)[1]))
    return ("other", None)


def _count_patterns(text: str, patterns: Iterable[str]) -> dict[str, int]:
    return {pattern: text.count(pattern) for pattern in patterns}


def collect_counts(runtime_dir: Path) -> dict:
    hosts_dir = runtime_dir / "shadow.data" / "hosts"
    summary: dict[str, object] = {
        "geth": {pattern: 0 for pattern in GETH_PATTERNS},
        "beacons": {},
        "validators": {},
        "recorders": {},
        "peer_lines": 0,
    }

    peer_file = runtime_dir / "beacon_peers.txt"
    if peer_file.exists():
        summary["peer_lines"] = sum(1 for line in peer_file.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())

    if not hosts_dir.exists():
        return summary

    for host_dir in sorted(p for p in hosts_dir.iterdir() if p.is_dir()):
        role, index = _host_role(host_dir.name)
        stderr_text = ""
        stdout_text = ""
        for path in sorted(host_dir.glob("*.stderr")):
            stderr_text += path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(host_dir.glob("*.stdout")):
            stdout_text += path.read_text(encoding="utf-8", errors="replace")

        if role == "geth":
            summary["geth"] = _count_patterns(stderr_text, GETH_PATTERNS)
        elif role == "beacon" and index is not None:
            summary["beacons"][f"beacon-{index}"] = _count_patterns(stderr_text, BEACON_PATTERNS)
            summary["recorders"][f"recorder-{index}"] = _count_patterns(stdout_text, RECORDER_PATTERNS)
        elif role == "validator" and index is not None:
            summary["validators"][f"validator-{index}"] = _count_patterns(stderr_text, VALIDATOR_PATTERNS)

    return summary


def _sum_counts(mapping: dict[str, int]) -> int:
    return sum(mapping.values())


def verify_smoke(runtime_dir: Path, expected_nodes: int) -> None:
    summary = collect_counts(runtime_dir)
    if summary["peer_lines"] < expected_nodes:
        raise AssertionError(f"expected at least {expected_nodes} beacon peers, got {summary['peer_lines']}")

    if _sum_counts(summary["geth"]) == 0:
        raise AssertionError("geth produced no expected progress logs")

    for idx in range(1, expected_nodes + 1):
        beacon = summary["beacons"].get(f"beacon-{idx}")
        if not beacon or _sum_counts(beacon) == 0:
            raise AssertionError(f"beacon-{idx} produced no expected progress logs")
        validator = summary["validators"].get(f"validator-{idx}")
        if not validator or _sum_counts(validator) == 0:
            raise AssertionError(f"validator-{idx} produced no expected progress logs")
        recorder = summary["recorders"].get(f"recorder-{idx}")
        if not recorder or _sum_counts(recorder) == 0:
            raise AssertionError(f"recorder-{idx} produced no expected peer recording logs")


def format_summary(summary: dict) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check TDT real-client logs")
    parser.add_argument("command", choices=("summary", "verify-smoke"))
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--expected-nodes", type=int, default=4)
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir).resolve()
    summary = collect_counts(runtime_dir)
    if args.command == "summary":
        print(format_summary(summary))
        return

    verify_smoke(runtime_dir, args.expected_nodes)
    print("[verify] PASS: real-client smoke logs show expected progress")
    print(format_summary(summary))


if __name__ == "__main__":
    main()
