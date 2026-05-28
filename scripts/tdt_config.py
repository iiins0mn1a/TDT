#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import shutil
import tomllib


@dataclass
class ClusterConfig:
    shared_geth_nodes: int = 1
    beacon_nodes: int = 4
    validators_total: int = 16


@dataclass
class SimulationConfig:
    default_mode: str = "smoke"
    interactive: bool = True
    edit_shadow_yaml_before_run: bool = True
    clean_runtime_before_prepare: bool = True
    work_dir: str = "runtime"
    duration_seconds: int = 1800


@dataclass
class CheckpointRestoreConfig:
    checkpoint_label: str = "tdt_real_clients"
    warmup_seconds: int = 600
    post_checkpoint_seconds: int = 120
    post_restore_step_seconds: int = 60
    post_restore_steps: int = 6
    restore_protocol_mode: str = "deterministic_v2"
    managed_external_paths: list[str] = field(
        default_factory=lambda: ["network", "beacon_peers.txt"]
    )


@dataclass
class BinariesConfig:
    shadow: str = ""
    prysmctl: str = ""
    criu: str = ""
    geth: str = ""
    beacon: str = ""
    validator: str = ""


@dataclass
class TdtConfig:
    root_dir: Path
    config_path: Path
    cluster: ClusterConfig
    simulation: SimulationConfig
    checkpoint_restore: CheckpointRestoreConfig
    binaries: BinariesConfig

    @property
    def repos_dir(self) -> Path:
        return self.root_dir / "deps"

    @property
    def assets_dir(self) -> Path:
        return self.root_dir / "assets"

    @property
    def work_dir(self) -> Path:
        return _resolve_path(self.root_dir, self.simulation.work_dir)

    @property
    def shadow_bin(self) -> Path:
        return _resolve_binary(
            self.root_dir,
            self.binaries.shadow,
            self.repos_dir / "shadow/build/src/main/shadow",
        )

    @property
    def prysmctl_bin(self) -> Path:
        return _resolve_binary(
            self.root_dir,
            self.binaries.prysmctl,
            self.repos_dir / "prysm/bazel-bin/cmd/prysmctl/prysmctl_/prysmctl",
        )

    @property
    def criu_bin(self) -> Path:
        if self.binaries.criu:
            return _resolve_path(self.root_dir, self.binaries.criu)
        if raw := os.environ.get("CRIU_BIN"):
            return Path(raw).resolve() if Path(raw).is_absolute() else Path(raw)
        if found := shutil.which("criu"):
            return Path(found).resolve()
        return Path("criu")

    @property
    def geth_bin(self) -> Path:
        return _resolve_binary(
            self.root_dir,
            self.binaries.geth,
            self.repos_dir / "go-ethereum/build/bin/geth",
        )

    @property
    def beacon_bin(self) -> Path:
        return _resolve_binary(
            self.root_dir,
            self.binaries.beacon,
            self.repos_dir / "prysm/bazel-bin/cmd/beacon-chain/beacon-chain_/beacon-chain",
        )

    @property
    def validator_bin(self) -> Path:
        return _resolve_binary(
            self.root_dir,
            self.binaries.validator,
            self.repos_dir / "prysm/bazel-bin/cmd/validator/validator_/validator",
        )

    def to_display_dict(self) -> dict:
        return {
            "config_path": str(self.config_path),
            "root_dir": str(self.root_dir),
            "cluster": asdict(self.cluster),
            "simulation": {
                **asdict(self.simulation),
                "work_dir": str(self.work_dir),
            },
            "checkpoint_restore": asdict(self.checkpoint_restore),
            "binaries": {
                "shadow": str(self.shadow_bin),
                "prysmctl": str(self.prysmctl_bin),
                "criu": str(self.criu_bin),
                "geth": str(self.geth_bin),
                "beacon": str(self.beacon_bin),
                "validator": str(self.validator_bin),
            },
        }


def _resolve_path(base_dir: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _resolve_binary(base_dir: Path, raw: str, default: Path) -> Path:
    if raw:
        return _resolve_path(base_dir, raw)
    return default.resolve()


def _merge_dataclass(dc_cls, raw: dict | None):
    raw = raw or {}
    field_names = {field.name for field in dc_cls.__dataclass_fields__.values()}
    merged = {key: value for key, value in raw.items() if key in field_names}
    return dc_cls(**merged)


def load_tdt_config(config_path: str | Path) -> TdtConfig:
    path = Path(config_path).resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    root_dir = path.parent.resolve()

    cluster = _merge_dataclass(ClusterConfig, data.get("cluster"))
    simulation = _merge_dataclass(SimulationConfig, data.get("simulation"))
    checkpoint_restore = _merge_dataclass(
        CheckpointRestoreConfig, data.get("checkpoint_restore")
    )
    binaries = _merge_dataclass(BinariesConfig, data.get("binaries"))

    if cluster.shared_geth_nodes != 1:
        raise ValueError("Only shared_geth_nodes = 1 is supported in this milestone")
    if cluster.beacon_nodes < 1:
        raise ValueError("beacon_nodes must be >= 1")
    if cluster.validators_total < 0:
        raise ValueError("validators_total must be >= 0")
    if simulation.default_mode not in {"smoke", "cprestore"}:
        raise ValueError("simulation.default_mode must be 'smoke' or 'cprestore'")
    if checkpoint_restore.post_restore_steps < 1:
        raise ValueError("post_restore_steps must be >= 1")
    if not checkpoint_restore.managed_external_paths:
        raise ValueError("managed_external_paths must not be empty")

    return TdtConfig(
        root_dir=root_dir,
        config_path=path,
        cluster=cluster,
        simulation=simulation,
        checkpoint_restore=checkpoint_restore,
        binaries=binaries,
    )
