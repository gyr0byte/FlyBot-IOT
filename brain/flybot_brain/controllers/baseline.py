"""Rule-based baseline controller (plan step 4.3): Braitenberg-style odour seeking.

Rules, in priority order, every tick:
  1. arrival: max(left, right) >= arrive -> stop, "eat" (hysteresis: stay until it falls below
     arrive * leave_ratio, so the plume's patchiness doesn't make it twitch on the source);
  2. obstacle -> the designed motor layer's on-the-spot turn (towards the stronger odour side if
     there is a signal), "avoid";
  3. odour signal (left + right >= detect) -> steer towards the stronger side in proportion to
     the normalised difference (left - right) / (left + right), weighted by the summed signal up to
     steer_full_at, slowing down near obstacles, "seek";
  4. no signal -> the motor layer's wandering, "explore".
Odour inputs are smoothed (EMA) against sensor noise. All numbers come from brain.yaml
(baseline: and motor:). It is the comparison point for the neural controller.
"""
from __future__ import annotations

import random

from ..config import Baseline, Motor
from .base import Controller, Decision, Inputs
from .motor import MotorLayer


class BaselineController(Controller):
    name = "baseline"

    def __init__(self, p: Baseline, motor: Motor, seed: int):
        self.p = p
        self.motor_p = motor
        self.seed = seed
        self.reset()

    def reset(self, seed: int | None = None) -> None:
        if seed is not None:
            self.seed = int(seed)
        self.rng = random.Random(self.seed)
        self.motor = MotorLayer(self.motor_p, self.rng)
        self.sl: float | None = None
        self.sr: float | None = None
        self.feeding = False

    def state_dict(self) -> dict:
        return {"feeding": self.feeding}

    def load_state(self, state: dict) -> None:
        self.feeding = bool(state.get("feeding", False))

    def step(self, inp: Inputs) -> Decision:
        p, t = self.p, inp.t_ms
        a = p.smooth_alpha
        self.sl = inp.ol if self.sl is None else self.sl + a * (inp.ol - self.sl)
        self.sr = inp.orr if self.sr is None else self.sr + a * (inp.orr - self.sr)
        total, peak = self.sl + self.sr, max(self.sl, self.sr)

        # 1. arrival, with hysteresis
        if self.feeding and peak < p.arrive * p.leave_ratio:
            self.feeding = False
        elif not self.feeding and peak >= p.arrive:
            self.feeding = True
        if self.feeding:
            return Decision(0.0, 0.0, "eat", ["odour_high"])

        # 2. obstacle (designed motor layer)
        prefer = None
        if total >= p.detect and self.sl != self.sr:
            prefer = 1.0 if self.sl > self.sr else -1.0
        avoid = self.motor.avoid(t, inp.d, prefer)
        if avoid is not None:
            return avoid
        slow = self.motor.slow_factor(inp.d)

        # 3. seek: steer towards the stronger side (weak signals are mostly patchiness)
        if total >= p.detect:
            nd = (self.sl - self.sr) / total
            if abs(nd) < p.diff_deadband:
                nd = 0.0
            confidence = 1.0 if p.steer_full_at <= 0 else min(1.0, total / p.steer_full_at)
            turn = p.turn_gain * nd * confidence
            fwd = p.speed * slow
            why = ["odour_l" if nd > 0 else "odour_r" if nd < 0 else "odour"]
            if slow < 1.0:
                why.append("obstacle")
            return Decision(fwd - turn, fwd + turn, "seek", why)

        # 4. wander (designed motor layer)
        return self.motor.wander(t, slow)
