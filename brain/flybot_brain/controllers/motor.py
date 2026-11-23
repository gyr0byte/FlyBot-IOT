"""Designed motor layer, shared by the baseline and the neural controller.

These rules are DESIGNED, not neural, in both controllers (brain/README.md says so):
  - obstacle avoidance: an obstacle closer than avoid_cm starts an on-the-spot turn held for
    avoid_ms (towards a preferred side if the caller gives one, else a random side);
  - slowing down: forward speed scales down between slow_cm and avoid_cm;
  - wandering: with no odour signal, drive forward on a gentle random curve that changes every
    wander_change_ms, so the robot covers ground instead of sitting still.
The randomness comes from the caller's random.Random, so a controller's draws stay in one sequence.
"""
from __future__ import annotations

import random

from ..config import Motor
from .base import Decision


class MotorLayer:
    def __init__(self, p: Motor, rng: random.Random):
        self.p = p
        self.rng = rng
        self.reset()

    def reset(self) -> None:
        self.avoid_until = -1
        self.avoid_dir = 1.0
        self.curve = 0.0
        self.next_curve = 0

    def avoid(self, t: int, d: float | None, prefer: float | None) -> Decision | None:
        """An avoid decision while an obstacle turn is active, else None.

        prefer: +1 turn left, -1 turn right, None = choose at random.
        """
        p = self.p
        if t < self.avoid_until or (d is not None and d < p.avoid_cm):
            if t >= self.avoid_until:
                self.avoid_dir = prefer if prefer is not None else (1.0 if self.rng.random() < 0.5 else -1.0)
                self.avoid_until = t + p.avoid_ms
            s = p.avoid_turn * self.avoid_dir
            return Decision(-s, s, "avoid", ["obstacle"])
        return None

    def slow_factor(self, d: float | None) -> float:
        p = self.p
        if d is not None and d < p.slow_cm:
            return max(0.25, (d - p.avoid_cm) / (p.slow_cm - p.avoid_cm))
        return 1.0

    def wander(self, t: int, slow: float) -> Decision:
        p = self.p
        if t >= self.next_curve:
            self.curve = (self.rng.random() * 2.0 - 1.0) * p.wander_curve
            self.next_curve = t + p.wander_change_ms
        fwd = p.wander_speed * slow
        return Decision(fwd - self.curve, fwd + self.curve, "explore", ["no_odour"])
