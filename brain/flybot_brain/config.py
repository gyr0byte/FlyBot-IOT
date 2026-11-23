"""Brain configuration: brain/config/brain.yaml (strict keys) and .env (MQTT)."""
from __future__ import annotations

import dataclasses
import os
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / "brain" / "config" / "brain.yaml"


@dataclass
class Experiment:
    reset_on_reboot: bool = False


@dataclass
class Timing:
    ttl_ms: int = 300
    state_every_ms: int = 200
    sleep_repeat_ms: int = 5000
    stale_ms: int = 250
    status_every_s: float = 10.0


@dataclass
class Persistence:
    dir: str = ".local/brain/snapshots"
    load_on_start: bool = True
    save_on_exit: bool = True

    def path(self) -> Path:
        p = Path(self.dir)
        return p if p.is_absolute() else REPO / p


@dataclass
class Baseline:
    smooth_alpha: float = 0.35
    detect: float = 0.02
    arrive: float = 0.55
    leave_ratio: float = 0.7
    speed: float = 0.55
    turn_gain: float = 0.9
    diff_deadband: float = 0.03
    steer_full_at: float = 0.0      # summed odour at which steering gets full weight (0 = always full)


@dataclass
class Motor:
    """Designed motor layer shared by both controllers: obstacles and wandering."""
    slow_cm: float = 45.0
    avoid_cm: float = 22.0
    avoid_turn: float = 0.55
    avoid_ms: int = 700
    wander_speed: float = 0.45
    wander_curve: float = 0.25
    wander_change_ms: int = 1500


@dataclass
class Neural:
    """Neural controller: which circuit, and how projection-neuron activity is read out."""
    circuit: str = "brain/circuits/designed"   # circuit folder (format v1), relative to the repo root
    smooth_ms: float = 400.0        # robot-time smoothing of PN rates (MQ-3 lag, patchiness)
    detect_hz: float = 2.0          # summed left + right PN rate above this = an odour signal
    arrive_hz: float = 120.0        # max(left, right) PN rate above this = at the source: stop, "eat"
    leave_ratio: float = 0.7        # ... until it falls below arrive_hz * leave_ratio
    turn_gain: float = 0.9          # turn per unit of PN contrast (left - right) / (left + right)
    diff_deadband: float = 0.05     # ignore smaller contrasts
    steer_full_hz: float = 20.0     # summed PN rate for full steering weight
    speed_far: float = 0.7          # forward speed with weak PN activity ...
    speed_near: float = 0.6         # ... and with strong activity (total activity sets the speed)
    speed_full_hz: float = 100.0    # summed PN rate counted as "strong"

    def circuit_path(self) -> Path:
        p = Path(self.circuit)
        return p if p.is_absolute() else REPO / p


@dataclass
class BrainConfig:
    controller: str = "baseline"
    seed: int = 1
    experiment: Experiment = field(default_factory=Experiment)
    timing: Timing = field(default_factory=Timing)
    persistence: Persistence = field(default_factory=Persistence)
    baseline: Baseline = field(default_factory=Baseline)
    motor: Motor = field(default_factory=Motor)
    neural: Neural = field(default_factory=Neural)

    def __post_init__(self):
        if self.controller not in ("baseline", "neural"):
            raise ValueError(f"controller must be baseline or neural, not {self.controller!r}")


def _build(cls, data: Any, where: str):
    if not isinstance(data, dict):
        raise ValueError(f"{where}: expected a mapping")
    hints = typing.get_type_hints(cls)
    unknown = set(data) - set(hints)
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    kw = {}
    for k, v in data.items():
        t = hints[k]
        kw[k] = _build(t, v, f"{where}.{k}") if dataclasses.is_dataclass(t) else v
    return cls(**kw)


def load_config(path: str | Path | None = None) -> BrainConfig:
    p = Path(path) if path else DEFAULT_CONFIG
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return _build(BrainConfig, data, p.name)


def set_param(cfg: BrainConfig, key: str, value: Any) -> None:
    """Change one setting at run time (brain/control "param"): "ctrl" or "<section>.<field>".

    Raises KeyError / TypeError / ValueError if the key is unknown or the value has the wrong type.
    """
    if key == "ctrl":
        if value not in ("baseline", "neural"):
            raise ValueError("ctrl must be baseline or neural")
        cfg.controller = value
        return
    section, _, name = key.partition(".")
    if section not in ("baseline", "motor", "neural", "timing", "experiment") or not name:
        raise KeyError(key)
    obj = getattr(cfg, section)
    hints = typing.get_type_hints(type(obj))
    if name not in hints:
        raise KeyError(key)
    t = hints[name]
    if t is bool:
        if not isinstance(value, bool):
            raise TypeError(f"{key} needs true/false")
    elif t in (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{key} needs a number")
        value = t(value)
    else:
        raise TypeError(f"{key} cannot be changed at run time")
    setattr(obj, name, value)


@dataclass
class MqttSettings:
    host: str
    port: int
    user: str
    password: str
    robot_id: str = "01"

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "MqttSettings":
        path = env_path or REPO / ".env"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found: run infra/mosquitto/make_passwords.py first")
        env = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        host = os.environ.get("MQTT_HOST", env.get("MQTT_HOST", "127.0.0.1"))
        if host == "localhost":
            host = "127.0.0.1"
        return cls(host, int(env.get("MQTT_PORT", "1883")), env["MQTT_BRAIN_USER"], env["MQTT_BRAIN_PASS"],
                   env.get("FLYBOT_ROBOT_ID", "01"))
