"""Neural controller (plan steps 5.1-5.6): a spiking network drives odour steering and learning.

What is NEURAL and what is DESIGNED (brain/README.md says the same):
  - neural: odour and light drive the circuit's input groups (rate-coded); the network (antennal
    lobe, mushroom body, output and dopamine neurons; any circuit in format v1) is simulated for the
    tick's robot-time step. Readout: projection-neuron activity per side (smoothed over robot time)
    gives the turn (contrast), the speed (total) and arrival (peak). Approach vs avoid MBON activity
    gives a learned valence v = (A - V) / (A + V), which modulates the innate odour attraction:
    drive = clip(1 + valence_gain * v) multiplies the turn; a negative drive steers away from the
    odour and suppresses "eat". Reward messages drive the reward / punishment DANs for a short pulse;
    the engine's three-factor rule then depresses recently active KC->MBON synapses in their
    compartment (modulation.csv);
  - designed: obstacle avoidance, slowing down near obstacles and wandering when there is no odour
    signal come from the shared motor layer (controllers/motor.py), exactly as in the baseline.
The circuit is data (config neural.circuit), so the designed and FlyWire circuits are interchangeable.
"""
from __future__ import annotations

import math
import random

import numpy as np

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
        outs = [o for o in ("steer_left", "steer_right", "approach", "avoid") if o in circuit.outputs]
        self.out_idx = {o: [gi[g] for g in circuit.outputs[o]] for o in outs}
        self.out_size = {o: sum(circuit.groups[g].size for g in circuit.outputs[o]) for o in outs}
        self.has_light = "light" in circuit.inputs
        self.has_valence = "approach" in outs and "avoid" in outs
        self.has_reward = "reward" in circuit.inputs and "punish" in circuit.inputs
        self._init_weight_summary()
        self.reset()

    # ------------------------------------------------------------------ state

    def reset(self, seed: int | None = None) -> None:
        """Dynamic state only (smoothing, membranes, traces); learned weights are kept."""
        if seed is not None:
            self.seed = self.net.seed = int(seed)
        self.net.reset()
        self.rng = random.Random(self.seed)
        self.motor = MotorLayer(self.motor_p, self.rng)
        self.left_hz = self.right_hz = 0.0
        self.app_hz = self.av_hz = 0.0
        self.feeding = False
        self.pulse = None                     # ("reward" | "punish", until_ms)
        self.pending_reward: int | None = None
        self.pref_lt: float | None = None
        self.pref_odr: float | None = None
        self.valence = 0.0
        self.drive = 1.0

    def reset_learning(self) -> None:
        self.net.reset_weights()
        self.pref_lt = self.pref_odr = None

    def on_reward(self, r: int, t_ms: int) -> None:
        if self.has_reward:
            self.pending_reward = r            # starts at the next tick, in robot time

    def spikes(self) -> dict[str, int]:
        return self.net.take_spk()

    def learning_changes(self) -> int:
        return self.net.plastic_changes

    def pref(self) -> dict[str, float]:
        out = {}
        if self.pref_odr is not None:
            out["odr"] = round(max(-1.0, min(1.0, self.pref_odr)), 2)
        if self.pref_lt is not None:
            out["lt"] = round(max(-1.0, min(1.0, self.pref_lt)), 2)
        return out

    def state_dict(self) -> dict:
        return {"feeding": self.feeding, "circuit": self.circuit.name, "n_plastic": int(self.net.p_raw.size),
                "plastic": [round(float(w), 6) for w in self.net.plastic_weights()],
                "pref_lt": self.pref_lt, "pref_odr": self.pref_odr}

    def load_state(self, state: dict) -> None:
        self.feeding = bool(state.get("feeding", False))
        if "plastic" in state:
            if state.get("circuit") != self.circuit.name or state.get("n_plastic") != self.net.p_raw.size:
                raise ValueError(f"snapshot was saved with circuit {state.get('circuit')!r} "
                                 f"({state.get('n_plastic')} plastic synapses), not {self.circuit.name!r} "
                                 f"({self.net.p_raw.size})")
            self.net.set_plastic_weights(state["plastic"])
        self.pref_lt, self.pref_odr = state.get("pref_lt"), state.get("pref_odr")

    # ------------------------------------------------------------------ brain/weights (contract 3.11)

    def _init_weight_summary(self) -> None:
        """Per KC: mean weight of its plastic synapses onto the approach / avoid MBON groups."""
        self.summary = None
        if not self.has_valence:
            return
        net, c = self.net, self.circuit
        groups = {"ap": set(c.outputs["approach"]), "av": set(c.outputs["avoid"])}
        projs = {k: [i for i, n in enumerate(net.projection_names)
                     if c.projections[n].plastic and c.projections[n].post in g] for k, g in groups.items()}
        pres = {c.projections[net.projection_names[i]].pre for ps in projs.values() for i in ps}
        if len(pres) != 1 or not all(projs.values()):
            return
        pre = pres.pop()
        n = c.groups[pre].size
        masks = {k: np.isin(net.p_proj, ps) for k, ps in projs.items()}
        idx = {k: (net.p_pre[m] - net.offset[pre]) for k, m in masks.items()}
        counts = {k: np.bincount(idx[k], minlength=n) for k in idx}
        w_max = float(net.p_wmax.max())
        self.summary = (n, w_max, masks, idx, counts, net.p_raw0.copy())

    def weights_summary(self) -> dict | None:
        if self.summary is None:
            return None
        n, w_max, masks, idx, counts, raw0 = self.summary
        out = {"n": n, "sc": round(w_max, 3)}
        for k in ("ap", "av"):
            sums = np.bincount(idx[k], weights=self.net.p_raw[masks[k]], minlength=n)
            init = np.bincount(idx[k], weights=raw0[masks[k]], minlength=n)
            # A KC without synapses onto this group reports the initial weight (nothing to learn).
            mean = np.where(counts[k] > 0, sums / np.maximum(counts[k], 1), w_max)
            if not counts[k].all():
                mean[counts[k] == 0] = (init.sum() / counts[k].sum()) if counts[k].any() else w_max
            out[k] = np.clip(np.round(mean / w_max * 255), 0, 255).astype(int).tolist()
        return out

    # ------------------------------------------------------------------ the tick

    def _rate(self, counts, out, sec) -> float:
        return sum(int(counts[i]) for i in self.out_idx[out]) / self.out_size[out] / sec

    def step(self, inp: Inputs) -> Decision:
        p, t = self.p, inp.t_ms
        net = self.net
        net.set_states(hunger=inp.hun, fear=inp.fear)          # circuit state_modulation (5.7)
        net.set_input("odour_left", inp.ol)
        net.set_input("odour_right", inp.orr)
        if self.has_light:
            net.set_input("light", 0.0 if inp.lt is None else inp.lt)
        if self.pending_reward is not None:
            self.pulse = ("reward" if self.pending_reward > 0 else "punish", t + p.reward_pulse_ms)
            self.pending_reward = None
        if self.has_reward:
            on = self.pulse is not None and t < self.pulse[1]
            net.set_input("reward", 1.0 if on and self.pulse[0] == "reward" else 0.0)
            net.set_input("punish", 1.0 if on and self.pulse[0] == "punish" else 0.0)
            if not on:
                self.pulse = None

        if inp.dt_ms > 0:
            counts = net.run(inp.dt_ms)                           # robot time, 1 ms steps
            sec = inp.dt_ms / 1000.0
            a = 1.0 - math.exp(-inp.dt_ms / p.smooth_ms)
            self.left_hz += a * (self._rate(counts, "steer_left", sec) - self.left_hz)
            self.right_hz += a * (self._rate(counts, "steer_right", sec) - self.right_hz)
            if self.has_valence:
                b = 1.0 - math.exp(-inp.dt_ms / p.valence_smooth_ms)
                self.app_hz += b * (self._rate(counts, "approach", sec) - self.app_hz)
                self.av_hz += b * (self._rate(counts, "avoid", sec) - self.av_hz)
            if p.forget_tau_s > 0:
                net.relax(inp.dt_ms, p.forget_tau_s * 1000.0)
        L, R = self.left_hz, self.right_hz
        total, peak = L + R, max(L, R)

        # Learned valence and the drive it gives the innate odour attraction.
        mb_total = self.app_hz + self.av_hz
        self.valence = (self.app_hz - self.av_hz) / mb_total if mb_total >= p.valence_min_hz else 0.0
        self.drive = max(p.drive_min, min(p.drive_max, 1.0 + p.valence_gain * self.valence))
        if total >= p.detect_hz and mb_total >= p.valence_min_hz:
            lit = inp.lt is not None and inp.lt >= 0.5
            prev = self.pref_lt if lit else self.pref_odr
            new = self.valence if prev is None else prev + p.pref_alpha * (self.valence - prev)
            if lit:
                self.pref_lt = new
            else:
                self.pref_odr = new
        learned = (["learned_avoid"] if self.drive < 0.5 else ["learned_approach"] if self.drive > 1.2 else [])

        # 1. arrival from network activity, with hysteresis; only where the odour is wanted
        if self.feeding and (peak < p.arrive_hz * p.leave_ratio or self.drive <= 0):
            self.feeding = False
        elif not self.feeding and peak >= p.arrive_hz and self.drive > 0:
            self.feeding = True
        if self.feeding:
            return Decision(0.0, 0.0, "eat", ["pn_high"] + learned)

        # 2. obstacle (designed motor layer), turning towards the more active side if it is wanted
        prefer = None
        if total >= p.detect_hz and L != R:
            prefer = (1.0 if L > R else -1.0) * (1.0 if self.drive >= 0 else -1.0)
        avoid = self.motor.avoid(t, inp.d, prefer)
        if avoid is not None:
            return avoid
        slow = self.motor.slow_factor(inp.d)

        # 3. seek (or flee): PN contrast x confidence x learned drive sets the turn, PN total the speed
        if total >= p.detect_hz:
            c = (L - R) / total
            if abs(c) < p.diff_deadband:
                c = 0.0
            confidence = min(1.0, total / p.steer_full_hz)
            turn = p.turn_gain * c * confidence * self.drive
            strength = min(1.0, total / p.speed_full_hz)
            fwd = (p.speed_far + (p.speed_near - p.speed_far) * strength) * slow
            fwd *= min(1.0, max(p.speed_drive_min, self.drive))   # low drive (fear, punishment): slower
            side = "pn_left" if c > 0 else "pn_right" if c < 0 else "pn"
            dec = "avoid" if self.drive < 0 else "approach" if learned == ["learned_approach"] else "seek"
            why = learned + [side] + (["obstacle"] if slow < 1.0 else [])
            return Decision(fwd - turn, fwd + turn, dec, why[:3])

        # 4. no odour signal: wander (designed motor layer)
        return self.motor.wander(t, slow)
