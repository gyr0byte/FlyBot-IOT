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
    weights_every_ms: int = 2000      # brain/weights at most this often (robot time, contract 3.11)
    backlog_warn: int = 40            # unprocessed inbound messages above this: warning + brain/status (3.9 lag)


@dataclass
class Persistence:
    dir: str = ".local/brain/snapshots"
    load_on_start: bool = True
    save_on_exit: bool = True
    restore_lesions: bool = False     # loading a snapshot re-applies its lesions only if true (warned)

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
class States:
    """Internal states (plan step 5.7). Their dynamics live here; how they modulate the network is
    circuit data (manifest state_modulation)."""
    hunger_rise_s: float = 600.0    # hunger rises 0 -> 1 over this much robot time; eating resets it
    hunger_start: float = 0.0       # hunger at start-up and after a state reset (experiments: fed / hungry)
    fear_jump: float = 0.5          # each escape event adds fear_jump x its strength (resp)
    fear_tau_s: float = 20.0        # fear decays with this time constant (robot time)


@dataclass
class LoggerCfg:
    """Logger module (Phase 8): records every message into SQLite (FLYBOT_DB_PATH)."""
    enabled: bool = True              # python -m flybot_brain --no-logger switches it off (experiments)
    retention_days: float = 7.0       # raw sensors / commands / brain_state older than this are pruned


@dataclass
class Sleep:
    """Sleep and wake from the light sensor (plan step 5.9), and consolidation during sleep."""
    auto: bool = True               # sleep when it stays dark (off: only brain/sleep requests)
    dark_below: float = 0.10        # light below this ...
    dark_for_s: float = 20.0        # ... for this long (robot time) -> sleep (cmd/sleep why "dark")
    light_above: float = 0.25       # light above this ...
    light_for_s: float = 2.0        # ... for this long -> wake (why "light"); only after a "dark" sleep
    consolidate_tau_s: float = 60.0  # while asleep, labile depression becomes stable with this tau (0 = off)


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
    # Learned valence (5.6): approach vs avoid MBON activity modulates the innate odour attraction.
    valence_smooth_ms: float = 300.0  # robot-time smoothing of MBON rates
    valence_min_hz: float = 2.0     # below this summed MBON rate the valence counts as 0
    valence_gain: float = 2.0       # drive = clip(1 + valence_gain * valence, drive_min, drive_max)
    drive_min: float = -1.0         # -1: steer away from the odour as strongly as it is approached
    drive_max: float = 1.5
    reward_pulse_ms: float = 300.0  # a reward message drives the reward / punish DANs this long
    forget_tau_s: float = 0.0       # plastic weights relax back with this time constant (0 = no forgetting)
    pref_alpha: float = 0.05        # EMA of the valence per tick for brain/state.pref (lt / odr)
    speed_drive_min: float = 0.4    # seek speed x clip(drive, speed_drive_min, 1): low drive (fear) slows

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
    states: States = field(default_factory=States)
    sleep: Sleep = field(default_factory=Sleep)
    logger: LoggerCfg = field(default_factory=LoggerCfg)

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
    if section not in ("baseline", "motor", "neural", "timing", "experiment", "states", "sleep") or not name:
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
    def from_env(cls, env_path: Path | None = None, user_key: str = "BRAIN") -> "MqttSettings":
        env = read_env(env_path)
        host = os.environ.get("MQTT_HOST", env.get("MQTT_HOST", "127.0.0.1"))
        if host == "localhost":
            host = "127.0.0.1"
        return cls(host, int(env.get("MQTT_PORT", "1883")), env[f"MQTT_{user_key}_USER"],
                   env[f"MQTT_{user_key}_PASS"], env.get("FLYBOT_ROBOT_ID", "01"))


def read_env(env_path: Path | None = None) -> dict[str, str]:
    """.env as a dict (KEY=VALUE lines; # comments). Process environment variables are not merged."""
    path = env_path or REPO / ".env"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run infra/mosquitto/make_passwords.py first")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def db_path_from_env(env_path: Path | None = None) -> str:
    """The logger database: FLYBOT_DB_PATH (environment first, then .env), default data/flybot.db."""
    if os.environ.get("FLYBOT_DB_PATH"):
        return os.environ["FLYBOT_DB_PATH"]
    try:
        return read_env(env_path).get("FLYBOT_DB_PATH") or str(REPO / "data" / "flybot.db")
    except FileNotFoundError:
        return str(REPO / "data" / "flybot.db")
