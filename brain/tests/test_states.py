"""Internal states (plan step 5.7): hunger and fear as circuit modulation, and their dynamics."""
from __future__ import annotations

import math

import numpy as np
import pytest

from flybot_brain.config import BrainConfig
from flybot_brain.controllers.base import Inputs
from flybot_brain.controllers.neural import NeuralController
from flybot_brain.core import Brain
from flybot_brain.network import CircuitError, Network, load_circuit, write_circuit
from flybot_contract import Contract

C = Contract()
BID = 2847193551
D = "dash-3fa2c1"


@pytest.fixture(scope="module")
def designed():
    return load_circuit(BrainConfig().neural.circuit_path())


def rates(net, groups, ms=3000, settle=500):
    net.run(settle)
    cnt = net.run(ms)
    return {g: cnt[net.group_names.index(g)] / (net.circuit.groups[g].size * ms / 1000) for g in groups}


# ----------------------------------------------------------------------------- format

LIF = dict(model="lif", tau_m_ms=20, v_thresh=1.0, v_reset=0.0, t_ref_ms=2, tau_syn_ms=5, bias=0.0, noise=0.0)


def small(tmp_path, state_mod):
    groups = [{"group": "inp", "size": 4, "model": "poisson", "side": "-"},
              {"group": "a", "size": 2, **LIF, "side": "-"},
              {"group": "b", "size": 2, **LIF, "side": "-"}]
    conns = [("inp_a", i, j, 1.0, "exc") for i in range(4) for j in range(2)]
    conns += [("a_b", i, j, 1.0, "exc") for i in range(2) for j in range(2)]
    spec = {"rule": "three_factor", "lr": 0.1, "tau_elig_ms": 100.0, "w_min": 0.0, "w_max": 1.0}
    manifest = {"name": "s", "source": {"kind": "designed"},
                "inputs": {"x": {"group": "inp", "encoding": "poisson_rate", "base_hz": 0, "gain_hz": 100}},
                "outputs": {},
                "projections": [{"name": "inp_a", "pre": "inp", "post": "a", "scale": 0.2},
                                {"name": "a_b", "pre": "a", "post": "b", "scale": 0.5, "plastic": True,
                                 "plasticity": spec}],
                "state_modulation": state_mod}
    return write_circuit(tmp_path / "s", manifest, groups, conns)


def test_state_modulation_is_parsed(tmp_path):
    c = small(tmp_path, [{"state": "hunger", "kind": "gain", "projection": "inp_a", "k": 0.5},
                         {"state": "fear", "kind": "bias", "group": "b", "k": -0.2}])
    assert [(s.state, s.kind, s.target, s.k) for s in c.state_modulation] == [
        ("hunger", "gain", "inp_a", 0.5), ("fear", "bias", "b", -0.2)]


@pytest.mark.parametrize("spec, message", [
    ({"state": "thirst", "kind": "gain", "projection": "inp_a", "k": 0.5}, "state must be"),
    ({"state": "hunger", "kind": "scale", "projection": "inp_a", "k": 0.5}, "kind must be"),
    ({"state": "hunger", "kind": "gain", "group": "a", "k": 0.5}, "keys must be"),
    ({"state": "hunger", "kind": "gain", "projection": "a_b", "k": 0.5}, "plastic"),
    ({"state": "hunger", "kind": "gain", "projection": "nope", "k": 0.5}, "unknown projection"),
    ({"state": "fear", "kind": "bias", "group": "inp", "k": 0.5}, "not lif"),
    ({"state": "fear", "kind": "bias", "group": "b", "k": "big"}, "finite number"),
])
def test_invalid_state_modulation_is_rejected(tmp_path, spec, message):
    with pytest.raises(CircuitError, match=message):
        small(tmp_path, [spec])


def test_engine_applies_gain_and_bias_and_restores_them_at_zero(tmp_path):
    c = small(tmp_path, [{"state": "hunger", "kind": "gain", "projection": "inp_a", "k": 0.5},
                         {"state": "fear", "kind": "bias", "group": "b", "k": 0.3}])
    net = Network(c, seed=1)
    k = net.projection_names.index("inp_a")
    edges = net.e_proj == k
    w0, b0 = net.e_w[edges].copy(), net.bias.copy()
    net.set_states(hunger=1.0, fear=0.5)
    assert np.allclose(net.e_w[edges], w0 * 1.5)
    ob = net.offset["b"]
    assert np.allclose(net.bias[ob:ob + 2], b0[ob:ob + 2] + 0.15)
    net.set_states(hunger=0.0, fear=0.0)
    assert np.allclose(net.e_w[edges], w0) and np.allclose(net.bias, b0)
    with pytest.raises(KeyError):
        net.set_states(boredom=1.0)


# ----------------------------------------------------------------------------- the designed circuit

def test_hunger_raises_the_pn_response_to_a_weak_odour(designed):
    def pn(h):
        out = []
        for s in range(4):
            net = Network(designed, seed=s)
            net.set_states(hunger=h)
            net.set_input("odour_left", 0.05); net.set_input("odour_right", 0.05); net.set_input("light", 0.05)
            r = rates(net, ["pl", "pr"])
            out.append(r["pl"] + r["pr"])
        return np.mean(out)
    assert pn(1.0) > 1.3 * pn(0.0)                    # criterion ~+50% (measured +53%)


def test_fear_and_hunger_shift_the_untrained_valence(designed):
    def v(**states):
        out = []
        for s in range(4):
            net = Network(designed, seed=s)
            net.set_states(**states)
            net.set_input("odour_left", 0.45); net.set_input("odour_right", 0.45); net.set_input("light", 0.05)
            r = rates(net, ["ma", "mv"])
            out.append((r["ma"] - r["mv"]) / (r["ma"] + r["mv"]))
        return np.mean(out)
    assert abs(v()) < 0.1
    assert v(fear=1.0) < -0.15                        # bias 0.6: about -0.25 (target -0.5 not reachable)
    assert v(hunger=1.0) > 0.15                       # mirror image
    assert abs(v(hunger=1.0, fear=1.0)) < 0.15        # they meet on the same valence


@pytest.mark.parametrize("light", [0.05, 0.0])
def test_mbons_stay_silent_without_odour_at_full_hunger_and_fear(designed, light):
    """With full hunger and fear, no odour and no lamp, the output neurons must stay (almost) silent.
    The state biases (0.6) are subthreshold, but near threshold the neurons fire on the faint
    light-driven KC background (worst of 10 seeds: 0.28 Hz per group at 0.6, 0.74 at 0.8). Asserted:
    approach + avoid per-neuron rate (what the controller compares with its valence gate) below the
    gate, and each group below 0.5 Hz, so drift towards tonic firing fails here."""
    gate = BrainConfig().neural.valence_min_hz
    for s in range(10):
        net = Network(designed, seed=s)
        net.set_states(hunger=1.0, fear=1.0)
        net.set_input("odour_left", 0.0); net.set_input("odour_right", 0.0); net.set_input("light", light)
        r = rates(net, ["ma", "mv"], ms=5000)
        assert r["ma"] < 0.5 and r["mv"] < 0.5, (s, r)
        assert r["ma"] + r["mv"] < gate, (s, r)


# ----------------------------------------------------------------------------- dynamics in the core

def sensors(seq, up, ol=0.0, orr=0.0, d=150.0, bid=BID):
    msg = {"v": 2, "seq": seq, "up": up, "bid": bid, "ol": ol, "or": orr, "d": d, "lt": 0.8, "bat": 7.9}
    C.validate("sensors", msg)
    return msg


def escape(seq, up, resp=1.0, hab=0.0):
    msg = {"v": 3, "seq": seq, "up": up, "bid": BID, "e": "escape", "ttc": 400, "d": 30.0, "resp": resp, "hab": hab}
    if resp > 0:
        msg["lat"] = 3.0
    C.validate("events", msg)
    return msg


def brain(**states):
    cfg = BrainConfig(controller="baseline")
    for k, v in states.items():
        setattr(cfg.states, k, v)
    return Brain(cfg, C, None, wall=lambda: 1000.0)


def test_hunger_rises_with_robot_time_and_eating_resets_it():
    b = brain(hunger_rise_s=10.0)
    for i in range(101):                               # 5 s of robot time, no odour
        b.handle("sensors", sensors(i, i * 50))
    assert b.hun == pytest.approx(0.5, abs=0.01)
    up = 100 * 50
    for i in range(101, 140):                          # strong odour: the baseline arrives and eats
        up += 50
        b.handle("sensors", sensors(i, up, ol=0.95, orr=0.95))
        if b.dec == "eat":
            break
    assert b.dec == "eat" and b.hun == 0.0


def test_fear_jumps_with_escape_strength_decays_and_ignores_duplicates():
    b = brain(fear_jump=0.5, fear_tau_s=20.0)
    b.handle("sensors", sensors(0, 0))
    b.handle("events", escape(1, 10, resp=1.0))
    b.handle("events", escape(1, 10, resp=1.0))        # QoS 1 redelivery
    assert b.fear == pytest.approx(0.5)
    b.handle("events", escape(2, 20, resp=0.0, hab=0.9))   # habituated: no movement, no fright
    assert b.fear == pytest.approx(0.5) and b.hab == 0.9
    for i in range(1, 401):                            # 20 s of robot time
        b.handle("sensors", sensors(i, i * 50))
    assert b.fear == pytest.approx(0.5 * math.exp(-1), rel=0.02)


def test_reset_restores_the_configured_starting_hunger():
    b = brain(hunger_start=1.0)
    assert b.hun == 1.0
    b.hun = 0.2
    b._reset_internal("state")
    assert (b.hun, b.fear) == (1.0, 0.0)


def test_low_drive_slows_seeking():
    """R1: seek speed x clip(drive, 0.4, 1)."""
    def fwd(fear):
        c = NeuralController(BrainConfig(controller="neural", seed=1))
        d = None
        for t in range(1, 41):
            d = c.step(Inputs(0.3, 0.3, 150.0, 0.05, 7.9, 50, t * 50, hun=0.0, fear=fear))
        return (d.l + d.r) / 2, c.drive
    calm, d0 = fwd(0.0)
    afraid, d1 = fwd(1.0)
    assert d1 < d0 and afraid < calm
