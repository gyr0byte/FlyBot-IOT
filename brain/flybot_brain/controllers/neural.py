"""Neural controller (plan steps 5.1-5.4): a spiking network drives odour steering.

What is NEURAL and what is DESIGNED (brain/README.md says the same):
  - neural: the odour values drive the circuit's input groups (rate-coded receptors); the
    network (receptors -> projection neurons with cross-lateral inhibition, any circuit in format
    v1) is simulated for the tick's robot-time step; the readout takes the projection-neuron
    activity per side, smoothed over robot time: their contrast sets the turn, their total sets
    the speed, and a high peak means "arrived" (stop, "eat");
  - designed: obstacle avoidance, slowing down near obstacles and wandering when there is no odour
    signal come from the shared motor layer (controllers/motor.py), exactly as in the baseline.
The circuit is data (config neural.circuit), so the designed and FlyWire circuits are interchangeable.
"""
from __future__ import annotations

import math
import random

from ..config import BrainConfig
from ..network import CircuitError, Network, load_circuit
from .base import Controller, Decision, Inputs
from .motor import MotorLayer

REQUIRED_INPUTS = ("odour_left", "odour_right")
REQUIRED_OUTPUTS = ("steer_left", "steer_right")


class NeuralController(Controller):
    name = "neural"

    def __init__(self, cfg: BrainConfig):
        self.p = cfg.neural
        self.motor_p = cfg.motor
        self.seed = cfg.seed
        circuit = load_circuit(self.p.circuit_path())
        missing = [b for b in REQUIRED_INPUTS if b not in circuit.inputs] + \
                  [b for b in REQUIRED_OUTPUTS if b not in circuit.outputs]
        if missing:
            raise CircuitError(f"circuit {circuit.name!r} lacks bindings needed by the neural controller: {missing}")
        self.circuit = circuit
        self.net = Network(circuit, seed=cfg.seed)
        gi = {g: i for i, g in enumerate(self.net.group_names)}
        self.out_idx = {o: [gi[g] for g in circuit.outputs[o]] for o in REQUIRED_OUTPUTS}
        self.out_size = {o: sum(circuit.groups[g].size for g in circuit.outputs[o]) for o in REQUIRED_OUTPUTS}
        self.reset()

    def reset(self) -> None:
        self.net.reset()
        self.rng = random.Random(self.seed)
        self.motor = MotorLayer(self.motor_p, self.rng)
        self.left_hz = 0.0          # smoothed PN rate per neuron, left / right
        self.right_hz = 0.0
        self.feeding = False

    def spikes(self) -> dict[str, int]:
        return self.net.take_spk()

    def state_dict(self) -> dict:
        return {"feeding": self.feeding}

    def load_state(self, state: dict) -> None:
        self.feeding = bool(state.get("feeding", False))

    def step(self, inp: Inputs) -> Decision:
        p, t = self.p, inp.t_ms
        self.net.set_input("odour_left", inp.ol)
        self.net.set_input("odour_right", inp.orr)
        if inp.dt_ms > 0:
            counts = self.net.run(inp.dt_ms)                      # robot time, 1 ms steps
            sec = inp.dt_ms / 1000.0
            lhz = sum(int(counts[i]) for i in self.out_idx["steer_left"]) / self.out_size["steer_left"] / sec
            rhz = sum(int(counts[i]) for i in self.out_idx["steer_right"]) / self.out_size["steer_right"] / sec
            a = 1.0 - math.exp(-inp.dt_ms / p.smooth_ms)
            self.left_hz += a * (lhz - self.left_hz)
            self.right_hz += a * (rhz - self.right_hz)
        L, R = self.left_hz, self.right_hz
        total, peak = L + R, max(L, R)

        # 1. arrival from network activity, with hysteresis
        if self.feeding and peak < p.arrive_hz * p.leave_ratio:
            self.feeding = False
        elif not self.feeding and peak >= p.arrive_hz:
            self.feeding = True
        if self.feeding:
            return Decision(0.0, 0.0, "eat", ["pn_high"])

        # 2. obstacle (designed motor layer), turning towards the more active side
        prefer = (1.0 if L > R else -1.0) if total >= p.detect_hz and L != R else None
        avoid = self.motor.avoid(t, inp.d, prefer)
        if avoid is not None:
            return avoid
        slow = self.motor.slow_factor(inp.d)

        # 3. seek: PN contrast sets the turn, PN total sets the speed
        if total >= p.detect_hz:
            c = (L - R) / total
            if abs(c) < p.diff_deadband:
                c = 0.0
            confidence = min(1.0, total / p.steer_full_hz)
            turn = p.turn_gain * c * confidence
            strength = min(1.0, total / p.speed_full_hz)
            fwd = (p.speed_far + (p.speed_near - p.speed_far) * strength) * slow
            why = ["pn_left" if c > 0 else "pn_right" if c < 0 else "pn"]
            if slow < 1.0:
                why.append("obstacle")
            return Decision(fwd - turn, fwd + turn, "seek", why)

        # 4. no odour signal: wander (designed motor layer)
        return self.motor.wander(t, slow)
