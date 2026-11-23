"""Rule-based baseline controller (plan step 4.3): Braitenberg-style odour seeking.

Rules, in priority order, every tick:
  1. arrival: max(left, right) >= arrive -> stop, "eat" (hysteresis: stay until it falls below
     arrive * leave_ratio, so the plume's patchiness doesn't make it twitch on the source);
  2. obstacle closer than avoid_cm -> turn on the spot for avoid_ms (towards the stronger odour
     side if there is a signal, else a random side), "avoid";
  3. odour signal (left + right >= detect) -> steer towards the stronger side in proportion to
     the normalised difference (left - right) / (left + right), slowing down near obstacles, "seek";
  4. no signal -> wander forward with a gentle random curve that changes every wander_change_ms,
     so the robot covers ground instead of sitting still, "explore".
Odour inputs are smoothed (EMA) against sensor noise. All numbers come from brain.yaml.
It is the comparison point for the neural controller ("why not just if-else?").
"""
from __future__ import annotations

import random

from ..config import Baseline
from .base import Controller, Decision, Inputs


class BaselineController(Controller):
    name = "baseline"

    def __init__(self, p: Baseline, seed: int):
        self.p = p
        self.seed = seed
        self.reset()

    def reset(self) -> None:
        self.rng = random.Random(self.seed)
        self.sl: float | None = None
        self.sr: float | None = None
        self.feeding = False
        self.avoid_until = -1
        self.avoid_dir = 1.0
        self.curve = 0.0
        self.next_curve = 0

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

        # 2. obstacle: turn on the spot, keep the direction for avoid_ms
        d = inp.d
        if t < self.avoid_until or (d is not None and d < p.avoid_cm):
            if t >= self.avoid_until:
                if total >= p.detect and self.sl != self.sr:
                    self.avoid_dir = 1.0 if self.sl > self.sr else -1.0
                else:
                    self.avoid_dir = 1.0 if self.rng.random() < 0.5 else -1.0
                self.avoid_until = t + p.avoid_ms
            s = p.avoid_turn * self.avoid_dir
            return Decision(-s, s, "avoid", ["obstacle"])

        slow = 1.0
        if d is not None and d < p.slow_cm:
            slow = max(0.25, (d - p.avoid_cm) / (p.slow_cm - p.avoid_cm))

        # 3. seek: steer towards the stronger side
        if total >= p.detect:
            nd = (self.sl - self.sr) / total
            if abs(nd) < p.diff_deadband:
                nd = 0.0
            turn = p.turn_gain * nd
            fwd = p.speed * slow
            why = ["odour_l" if nd > 0 else "odour_r" if nd < 0 else "odour"]
            if slow < 1.0:
                why.append("obstacle")
            return Decision(fwd - turn, fwd + turn, "seek", why)

        # 4. wander
        if t >= self.next_curve:
            self.curve = (self.rng.random() * 2.0 - 1.0) * p.wander_curve
            self.next_curve = t + p.wander_change_ms
        fwd = p.wander_speed * slow
        return Decision(fwd - self.curve, fwd + self.curve, "explore", ["no_odour"])
