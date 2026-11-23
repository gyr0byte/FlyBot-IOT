"""Configuration: .env (MQTT) and YAML scenarios.

A scenario file only lists what differs from the defaults below; unknown keys are an error,
so a typo never silently falls back to a default. All lengths in metres, angles in degrees in
the YAML (converted where used), times in seconds unless the name says _ms.
"""
from __future__ import annotations

import dataclasses
import os
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO / "sim" / "scenarios"


# ----------------------------------------------------------------------------- .env

def load_env(path: Path | None = None) -> dict[str, str]:
    path = path or REPO / ".env"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run infra/mosquitto/make_passwords.py first")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


@dataclass
class MqttSettings:
    host: str
    port: int
    user: str
    password: str
    robot_id: str = "01"

    @classmethod
    def from_env(cls, user_key: str = "SIM", env: dict[str, str] | None = None) -> "MqttSettings":
        env = env if env is not None else load_env()
        host = os.environ.get("MQTT_HOST", env.get("MQTT_HOST", "127.0.0.1"))
        if host == "localhost":
            host = "127.0.0.1"  # Windows tries IPv6 first for "localhost" (2 s stall)
        return cls(host=host, port=int(env.get("MQTT_PORT", "1883")),
                   user=env[f"MQTT_{user_key}_USER"], password=env[f"MQTT_{user_key}_PASS"],
                   robot_id=env.get("FLYBOT_ROBOT_ID", "01"))


# ----------------------------------------------------------------------------- scenario

@dataclass
class Arena:
    width_m: float = 2.0
    height_m: float = 2.0


@dataclass
class Obstacle:
    type: str  # "circle" (x, y, r) or "rect" (x, y = lower-left corner, w, h)
    x: float
    y: float
    r: float = 0.0
    w: float = 0.0
    h: float = 0.0


@dataclass
class Source:
    x: float
    y: float
    strength: float = 1.0      # concentration at the source (arbitrary units, ~1 = strong)
    spread_m: float = 0.35     # distance at which concentration falls to half


@dataclass
class Plume:
    patch_amp: float = 0.45       # relative size of patchy fluctuations (0 = smooth plume)
    patch_scale_m: float = 0.25   # spatial size of patches
    patch_period_s: float = 3.0   # how fast patches drift past
    background: float = 0.0       # ambient concentration everywhere


@dataclass
class Light:
    day: bool = True
    day_level: float = 0.80
    night_level: float = 0.05
    noise: float = 0.01
    ldr_fitted: bool = True


@dataclass
class StartPos:
    x: float
    y: float
    heading_deg: float = 0.0


@dataclass
class RobotParams:
    radius_m: float = 0.08          # body is modelled as a circle
    wheel_base_m: float = 0.13
    max_speed_mps: float = 0.30     # wheel speed at command 1.0 (gear motors at 6 V)
    max_accel_mps2: float = 0.8     # per wheel
    left_gain: float = 1.00         # motor mismatch: the right motor is slightly weaker
    right_gain: float = 0.95
    slip_sigma: float = 0.03        # random per-step wheel slip (fraction of wheel speed)


@dataclass
class OdourParams:
    mount_forward_m: float = 0.07   # sensors on the front edge
    mount_side_m: float = 0.06      # +/- 6 cm: about 12 cm apart
    angle_out_deg: float = 30.0     # angled outward
    inlet_m: float = 0.02           # sampling point sits this far along the sensor's direction
    tau_rise_s: float = 2.0         # MQ-3 first-order lag: rising
    tau_decay_s: float = 6.0        # ... and recovering (slower)
    gain_v: float = 1.6             # volts per unit concentration (before saturation)
    v_span: float = 2.4             # saturation headroom above baseline (ADC limit)
    baseline_v: tuple[float, float] = (0.55, 0.48)   # clean-air output, left/right differ
    gain_spread: tuple[float, float] = (1.00, 0.90)  # left/right sensitivity differ
    noise_v: float = 0.006
    drift_v_per_sqrt_s: float = 0.0015
    warmup_excess_v: float = 0.8    # heater warm-up: output starts high and settles
    warmup_tau_s: float = 1.5


@dataclass
class UltrasonicParams:
    mount_forward_m: float = 0.08
    cone_deg: float = 25.0
    rays: int = 9
    min_range_m: float = 0.02
    max_range_m: float = 4.0
    rate_hz: float = 25.0
    noise_m: float = 0.004
    noise_frac: float = 0.01
    dropout_p: float = 0.03          # missed echo
    max_incidence_deg: float = 60.0  # steeper than this: echo reflects away (missed)


@dataclass
class BatteryParams:
    start_v: float = 8.20            # 2S Li-ion, nearly full
    drain_v_per_hour: float = 0.60
    sag_v_at_full: float = 0.30      # voltage sag with both motors at full
    noise_v: float = 0.01


@dataclass
class Looming:
    start_dist_m: float = 0.75       # launched this far in front of the robot
    speed_mps: float = 1.0           # brisk hand speed
    radius_m: float = 0.06


@dataclass
class Timing:
    sensors_hz: float = 20.0
    ping_hz: float = 1.0
    status_every_s: float = 10.0


@dataclass
class Scenario:
    name: str = "default"
    seed: int = 1
    duration_s: float = 60.0
    success_radius_m: float = 0.15
    arena: Arena = field(default_factory=Arena)
    obstacles: list[Obstacle] = field(default_factory=list)
    sources: list[Source] = field(default_factory=lambda: [Source(x=1.6, y=1.6)])
    plume: Plume = field(default_factory=Plume)
    light: Light = field(default_factory=Light)
    start_positions: list[StartPos] = field(default_factory=lambda: [StartPos(0.3, 0.3, 45.0)])
    robot: RobotParams = field(default_factory=RobotParams)
    odour: OdourParams = field(default_factory=OdourParams)
    ultrasonic: UltrasonicParams = field(default_factory=UltrasonicParams)
    battery: BatteryParams = field(default_factory=BatteryParams)
    looming: Looming = field(default_factory=Looming)
    timing: Timing = field(default_factory=Timing)
    edge: dict[str, Any] = field(default_factory=dict)  # overrides for edge.EdgeConfig


def _build(cls, data: Any, where: str):
    """Build a dataclass from a dict, recursively, rejecting unknown keys."""
    if not dataclasses.is_dataclass(cls):
        return data
    if not isinstance(data, dict):
        raise ValueError(f"{where}: expected a mapping, got {data!r}")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(fields)
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    kwargs = {}
    hints = typing.get_type_hints(cls)
    for name, value in data.items():
        t = hints[name]
        origin = typing.get_origin(t)
        if origin is list:
            (item_t,) = typing.get_args(t)
            kwargs[name] = [_build(item_t, v, f"{where}.{name}[{i}]") for i, v in enumerate(value)]
        elif origin is tuple:
            kwargs[name] = tuple(value)
        elif dataclasses.is_dataclass(t):
            kwargs[name] = _build(t, value, f"{where}.{name}")
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_scenario(name_or_path: str | Path) -> Scenario:
    p = Path(name_or_path)
    if not p.suffix:
        p = SCENARIO_DIR / f"{name_or_path}.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    sc = _build(Scenario, data, p.name)
    if not sc.sources:
        raise ValueError(f"{p.name}: at least one odour source is required")
    if not sc.start_positions:
        raise ValueError(f"{p.name}: at least one start position is required")
    return sc
