"""Configuration: .env (MQTT) and YAML scenarios.

A scenario file only lists what differs from the defaults below; unknown keys are an error,
so a typo never silently falls back to a default. All lengths in metres, angles in degrees in
the YAML (converted where used), times in seconds unless the name says _ms.
"""
from __future__ import annotations

import copy
import dataclasses
import math
import os
import types
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
    label: str | None = None   # optional unique name (experiments: rewards, which source was reached)


@dataclass
class Plume:
    patch_amp: float = 0.45       # relative size of patchy fluctuations (0 = smooth plume)
    patch_scale_m: float = 0.25   # spatial size of patches
    patch_period_s: float = 3.0   # how fast patches drift past
    background: float = 0.0       # ambient concentration everywhere


@dataclass
class LightPatch:
    """A lamp: full `level` within radius r, fading linearly to the base level over edge_m."""
    x: float
    y: float
    r: float
    level: float = 0.9
    edge_m: float = 0.05


@dataclass
class Light:
    day: bool = True
    day_level: float = 0.80
    night_level: float = 0.05
    noise: float = 0.01
    ldr_fitted: bool = True
    patches: list[LightPatch] = field(default_factory=list)


@dataclass
class StartPos:
    x: float
    y: float
    heading_deg: float = 0.0


@dataclass
class RelStart:
    """A start position relative to a labelled source, written for a source on the LEFT half of the
    arena; when that source is on the right half, dx and the heading are mirrored."""
    dx: float
    dy: float
    heading_deg: float = 90.0


@dataclass
class Phase:
    name: str
    trials: int = 1
    rewards: dict[str, int] = field(default_factory=dict)     # source label -> +1 reward / -1 punishment
    start_positions: list[StartPos] = field(default_factory=list)   # absolute (default: the scenario's)
    relative_to: str | None = None                             # or: starts relative to this source label
    relative_starts: list[RelStart] = field(default_factory=list)
    kind: str = "trials"               # trials | rest (one period of duration_s, e.g. sleep vs awake)
    duration_s: float | None = None    # rest: how long
    ambient: float | None = None       # light level everywhere for this phase (overrides day/night, lamps)
    odour: bool = True                 # false: no odour sources during this phase
    dawn_s: float = 0.0                # rest: the last dawn_s seconds at light dawn_ambient (wakes sleepers)
    dawn_ambient: float = 0.8


@dataclass
class ExperimentCfg:
    phases: list[Phase]
    hold_after_reward_s: float = 2.0   # keep running after a reward, so the brain pairs it with the source
    reward_delay_s: float = 0.0        # time between reaching the source and the button press


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
class Threat:
    """A recurring looming threat: while the robot is within radius_m of (x, y), a looming object is
    launched at it (as with the live view's L key) first_s after it enters and then every every_s."""
    x: float
    y: float
    radius_m: float = 0.5
    every_s: float = 4.0
    first_s: float = 0.5


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
    threats: list[Threat] = field(default_factory=list)
    timing: Timing = field(default_factory=Timing)
    edge: dict[str, Any] = field(default_factory=dict)  # overrides for edge.EdgeConfig
    goal: str = "first"            # first: reach sources[0]; any: the first source reached ends the trial
    experiment: ExperimentCfg | None = None


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
        if origin in (typing.Union, types.UnionType):                    # X | None
            inner = [a for a in typing.get_args(t) if a is not type(None)]
            kwargs[name] = None if value is None else _build(inner[0], value, f"{where}.{name}")
        elif origin is list:
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
    validate(sc, p.name)
    return sc


def validate(sc: Scenario, where: str) -> None:
    labels = [s.label for s in sc.sources if s.label]
    if len(labels) != len(set(labels)):
        raise ValueError(f"{where}: source labels must be unique")
    if sc.goal not in ("first", "any"):
        raise ValueError(f"{where}: goal must be first or any")
    if sc.experiment is None:
        return
    if not sc.experiment.phases:
        raise ValueError(f"{where}: experiment needs at least one phase")
    for ph in sc.experiment.phases:
        w = f"{where}: phase {ph.name!r}"
        if ph.kind not in ("trials", "rest"):
            raise ValueError(f"{w}: kind must be trials or rest")
        if ph.kind == "rest":
            if not ph.duration_s or ph.duration_s <= 0 or ph.trials != 1 or ph.rewards:
                raise ValueError(f"{w}: a rest phase needs duration_s > 0, one trial and no rewards")
            if not 0.0 <= ph.dawn_s < ph.duration_s:
                raise ValueError(f"{w}: dawn_s must be 0 or shorter than duration_s")
        elif ph.duration_s is not None or ph.dawn_s:
            raise ValueError(f"{w}: duration_s and dawn_s are for rest phases")
        if ph.ambient is not None and not 0.0 <= ph.ambient <= 1.0:
            raise ValueError(f"{w}: ambient must be 0..1")
        if ph.trials < 1:
            raise ValueError(f"{w}: trials must be >= 1")
        for label, r in ph.rewards.items():
            if label not in labels:
                raise ValueError(f"{w}: reward for unknown source label {label!r}")
            if r not in (1, -1):
                raise ValueError(f"{w}: reward must be 1 or -1")
        if ph.relative_to is not None:
            if ph.relative_to not in labels:
                raise ValueError(f"{w}: relative_to unknown source label {ph.relative_to!r}")
            if not ph.relative_starts or ph.start_positions:
                raise ValueError(f"{w}: relative_to needs relative_starts (and no start_positions)")


def source_by_label(sc: Scenario, label: str) -> Source:
    return next(s for s in sc.sources if s.label == label)


def phase_starts(sc: Scenario, ph: Phase) -> list[StartPos]:
    """The absolute start positions of a phase (relative ones resolved against their source)."""
    if ph.relative_to is None:
        return ph.start_positions or sc.start_positions
    src = source_by_label(sc, ph.relative_to)
    right = src.x > sc.arena.width_m / 2
    return [StartPos(src.x + (-r.dx if right else r.dx), src.y + r.dy,
                     (180.0 - r.heading_deg) if right else r.heading_deg) for r in ph.relative_starts]


def check_training_starts(sc: Scenario, max_dist_m: float = 0.8) -> list[str]:
    """Every start of a phase with relative starts must be near its own source: closer to it than
    to any other labelled source, and within max_dist_m. Returns the problems (empty = fine)."""
    problems = []
    for ph in (sc.experiment.phases if sc.experiment else []):
        if ph.relative_to is None:
            continue
        own = source_by_label(sc, ph.relative_to)
        others = [s for s in sc.sources if s.label and s.label != ph.relative_to]
        for sp in phase_starts(sc, ph):
            d_own = math.hypot(sp.x - own.x, sp.y - own.y)
            d_other = min((math.hypot(sp.x - s.x, sp.y - s.y) for s in others), default=float("inf"))
            if not (d_own < d_other and d_own <= max_dist_m):
                problems.append(f"{sc.name} phase {ph.name}: start ({sp.x:.2f}, {sp.y:.2f}) is {d_own:.2f} m from "
                                f"{ph.relative_to!r} and {d_other:.2f} m from the nearest other source")
            if not (0 < sp.x < sc.arena.width_m and 0 < sp.y < sc.arena.height_m):
                problems.append(f"{sc.name} phase {ph.name}: start ({sp.x:.2f}, {sp.y:.2f}) outside the arena")
    return problems


def mirror_scenario(sc: Scenario) -> Scenario:
    """Left-right mirror image (for counterbalancing): sources, lamps, obstacles and absolute start
    positions flip; relative starts follow their source automatically."""
    m = copy.deepcopy(sc)
    w = m.arena.width_m
    m.name = sc.name + "-mirrored"
    for s in m.sources:
        s.x = w - s.x
    for p in m.light.patches:
        p.x = w - p.x
    for ob in m.obstacles:
        ob.x = w - ob.x - (ob.w if ob.type == "rect" else 0.0)
    for sp in m.start_positions:
        sp.x, sp.heading_deg = w - sp.x, 180.0 - sp.heading_deg
    for ph in (m.experiment.phases if m.experiment else []):
        for sp in ph.start_positions:
            sp.x, sp.heading_deg = w - sp.x, 180.0 - sp.heading_deg
    return m
