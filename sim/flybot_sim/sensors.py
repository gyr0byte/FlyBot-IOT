"""Sensor PHYSICS: what the hardware would output. Deliberately imperfect.

The firmware-side processing of these raw outputs (warm-up, baseline, 0-1 scaling, filtering)
is in edge_sensing.py, which is what the ESP32 will run.
"""
from __future__ import annotations

import math

import numpy as np

from .config import BatteryParams, Light, OdourParams, UltrasonicParams
from .robot import Robot
from .world import World


class OdourSensor:
    """One MQ-3: samples the plume at its inlet, first-order lag, drift, noise, warm-up.

    side = +1 for left, -1 for right. Output is the sensor's analogue voltage.
    """

    def __init__(self, p: OdourParams, side: int, rng: np.random.Generator):
        self.p = p
        self.side = side
        self.idx = 0 if side > 0 else 1
        self.rng = rng
        self.lagged = 0.0
        self.drift = 0.0

    def inlet(self, robot: Robot) -> tuple[float, float]:
        a = math.radians(self.p.angle_out_deg) * self.side
        fwd = self.p.mount_forward_m + self.p.inlet_m * math.cos(a)
        left = self.side * self.p.mount_side_m + self.p.inlet_m * math.sin(a)
        return robot.body_to_world(fwd, left)

    def direction(self, robot: Robot) -> float:
        return robot.pose.theta + math.radians(self.p.angle_out_deg) * self.side

    def update(self, world: World, robot: Robot, t: float, dt: float) -> None:
        x, y = self.inlet(robot)
        c = world.odour(x, y, t)
        tau = self.p.tau_rise_s if c > self.lagged else self.p.tau_decay_s
        self.lagged += (c - self.lagged) * (1.0 - math.exp(-dt / tau))
        self.drift += self.rng.normal(0.0, self.p.drift_v_per_sqrt_s * math.sqrt(dt))

    def read_v(self, t: float) -> float:
        p = self.p
        gain = p.gain_v * p.gain_spread[self.idx]
        response = p.v_span * (1.0 - math.exp(-gain * self.lagged / p.v_span))  # saturates
        warm = p.warmup_excess_v * math.exp(-t / p.warmup_tau_s)
        v = p.baseline_v[self.idx] + response + self.drift + warm + self.rng.normal(0.0, p.noise_v)
        return min(max(v, 0.0), 3.3)


class Ultrasonic:
    """HC-SR04-like: nearest echo within a cone; noise, missed echoes, steep-angle misses.

    read() returns the raw distance in metres, or None for no echo.
    """

    def __init__(self, p: UltrasonicParams, rng: np.random.Generator):
        self.p = p
        self.rng = rng
        half = math.radians(p.cone_deg) / 2
        self.offsets = np.linspace(-half, half, p.rays) if p.rays > 1 else np.array([0.0])

    def origin(self, robot: Robot) -> tuple[float, float]:
        return robot.body_to_world(self.p.mount_forward_m, 0.0)

    def read(self, world: World, robot: Robot) -> float | None:
        p = self.p
        if self.rng.random() < p.dropout_p:
            return None
        ox, oy = self.origin(robot)
        best = None
        max_inc = math.radians(p.max_incidence_deg)
        for off in self.offsets:
            hit = world.cast(ox, oy, robot.pose.theta + float(off), p.max_range_m)
            if hit is not None and hit.incidence <= max_inc and (best is None or hit.dist < best):
                best = hit.dist
        if best is None or best < p.min_range_m:
            return None
        d = best + self.rng.normal(0.0, p.noise_m + p.noise_frac * best)
        return min(max(d, p.min_range_m), p.max_range_m)


class LightSensor:
    def __init__(self, p: Light, rng: np.random.Generator):
        self.p = p
        self.rng = rng

    def read(self, world: World, robot: Robot | None = None) -> float | None:
        """Light at the position of the robot (the LDR sits on top of it)."""
        if not self.p.ldr_fitted:
            return None
        level = world.light_level() if robot is None else world.light_level(robot.pose.x, robot.pose.y)
        return min(max(level + self.rng.normal(0.0, self.p.noise), 0.0), 1.0)


class Battery:
    """Pack voltage: slow drain plus sag under motor load."""

    def __init__(self, p: BatteryParams, rng: np.random.Generator):
        self.p = p
        self.rng = rng
        self.open_v = p.start_v
        self.load = 0.0

    def update(self, dt: float, motor_load: float) -> None:
        self.load = min(max(motor_load, 0.0), 1.0)
        self.open_v -= self.p.drain_v_per_hour * (0.5 + self.load) * dt / 3600.0

    def read_v(self) -> float:
        return self.open_v - self.p.sag_v_at_full * self.load + self.rng.normal(0.0, self.p.noise_v)
