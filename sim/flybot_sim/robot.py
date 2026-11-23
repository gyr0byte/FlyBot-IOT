"""Differential-drive robot body.

Input: per-wheel commands -1.0 .. +1.0 (after the firmware's motor mixer, see edge.MotorMixer).
Physics: each wheel accelerates toward command * max_speed * its gain (left/right mismatch),
limited by max_accel; small random slip on the distance actually travelled. A move that would
overlap a wall, obstacle or looming object is refused (the robot stops, wheels stall) and counted
as one collision per contact; turning on the spot is always allowed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import RobotParams
from .world import World


@dataclass
class Pose:
    x: float
    y: float
    theta: float  # radians, counter-clockwise from +x


class Robot:
    def __init__(self, p: RobotParams, pose: Pose, rng: np.random.Generator):
        self.p = p
        self.pose = pose
        self.rng = rng
        self.vl = 0.0  # actual wheel speeds, m/s
        self.vr = 0.0
        self.in_contact = False
        self.collisions = 0
        self.path_length = 0.0

    def body_to_world(self, fwd: float, left: float) -> tuple[float, float]:
        """A point given in robot coordinates (forward, left) in world coordinates."""
        c, s = math.cos(self.pose.theta), math.sin(self.pose.theta)
        return self.pose.x + fwd * c - left * s, self.pose.y + fwd * s + left * c

    def step(self, dt: float, cmd_l: float, cmd_r: float, world: World) -> None:
        p = self.p
        tl = max(-1.0, min(1.0, cmd_l)) * p.max_speed_mps * p.left_gain
        tr = max(-1.0, min(1.0, cmd_r)) * p.max_speed_mps * p.right_gain
        dv = p.max_accel_mps2 * dt
        self.vl += max(-dv, min(dv, tl - self.vl))
        self.vr += max(-dv, min(dv, tr - self.vr))

        # Distance each wheel actually covers this step, with random slip.
        sl = self.vl * dt * (1.0 + self.rng.normal(0.0, p.slip_sigma)) if p.slip_sigma else self.vl * dt
        sr = self.vr * dt * (1.0 + self.rng.normal(0.0, p.slip_sigma)) if p.slip_sigma else self.vr * dt
        ds = (sl + sr) / 2.0
        dth = (sr - sl) / p.wheel_base_m
        th_mid = self.pose.theta + dth / 2.0
        nx = self.pose.x + ds * math.cos(th_mid)
        ny = self.pose.y + ds * math.sin(th_mid)
        ntheta = (self.pose.theta + dth + math.pi) % (2 * math.pi) - math.pi

        if abs(ds) > 1e-12 and world.collides(nx, ny, p.radius_m):
            # Blocked: keep position, allow the rotation part, wheels stall.
            if not self.in_contact:
                self.collisions += 1
            self.in_contact = True
            self.pose.theta = ntheta
            self.vl = self.vr = 0.0
            return
        if abs(ds) > 1e-12:
            self.in_contact = False
        self.path_length += abs(ds)
        self.pose = Pose(nx, ny, ntheta)
