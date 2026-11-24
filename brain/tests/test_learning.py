"""Output neurons, dopamine and learning (plan step 5.6) on the designed circuit."""
from __future__ import annotations

import numpy as np
import pytest

from flybot_brain.config import BrainConfig
from flybot_brain.controllers.base import Inputs
from flybot_brain.controllers.neural import NeuralController
from flybot_brain.core import Brain
from flybot_brain.persistence import Snapshots
from flybot_contract import Contract

C = Contract()
LIT = (0.45, 0.45, 0.8)        # odour + light (at the lit source)
DARK = (0.45, 0.45, 0.05)      # odour in the dark


class Runner:
    def __init__(self, seed=1):
        cfg = BrainConfig(controller="neural", seed=seed)
        self.c = NeuralController(cfg)
        self.t = 0

    def cue(self, cue, ms=1000, d=150.0):
        ol, orr, lt = cue
        dec = None
        for _ in range(ms // 50):
            self.t += 50
            dec = self.c.step(Inputs(ol, orr, d, lt, 7.9, 50, self.t))
        return dec

    def valence(self, cue, ms=1500):
        self.cue(cue, ms)
        return self.c.valence

    def proj_weights(self, name):
        net = self.c.net
        return net.p_raw[net.p_proj == net.projection_names.index(name)]

    def pair(self, cue, r, n=3, delay_ms=0, cue_during_delay=True):
        """n pairings: cue for 1 s, then (optionally after a delay) a reward pulse, then 1 s more."""
        for _ in range(n):
            self.cue(cue, 1000)
            if delay_ms:
                self.cue(cue if cue_during_delay else (0.0, 0.0, 0.05), delay_ms)
            self.c.on_reward(r, self.t)
            self.cue(cue if cue_during_delay or not delay_ms else (0.0, 0.0, 0.05), 1000)
            self.cue((0.0, 0.0, 0.05), 1000)


def test_circuit_has_outputs_dopamine_and_modulation_map():
    c = Runner().c.circuit
    assert {"approach", "avoid"} <= set(c.outputs) and {"reward", "punish"} <= set(c.inputs)
    gated = {(r.modulator_group, r.target_group) for r in c.modulation}
    assert gated == {("dp", "mv"), ("dn", "ma")}          # reward -> avoid compartment, punish -> approach
    for p in ("kc_ma", "kc_mv"):
        assert c.projections[p].plastic and c.projections[p].plasticity.tau_elig_ms >= 2000


@pytest.mark.parametrize("cue", [LIT, DARK, (0.0, 0.0, 0.8)])
def test_untrained_valence_is_near_zero(cue):
    r = Runner()
    assert abs(r.valence(cue)) < 0.15 and 0.7 < r.c.drive < 1.3


def test_reward_drives_reward_dans_and_punishment_drives_punishment_dans():
    r = Runner()
    r.cue(DARK, 500)
    r.c.spikes()
    r.c.on_reward(+1, r.t)
    r.cue(DARK, 300)
    s = r.c.spikes()
    assert s["dp"] > 20 and s["dn"] == 0
    r.cue(DARK, 500)
    r.c.on_reward(-1, r.t)
    r.cue(DARK, 300)
    s = r.c.spikes()
    assert s["dn"] > 20


def test_punishment_with_the_lit_cue_depresses_approach_synapses_and_makes_it_aversive():
    r = Runner()
    r.pair(LIT, -1, n=10)                                         # graded learning: ~10 pairings (circuit lr note)
    assert r.proj_weights("kc_ma").mean() < 0.97                  # approach synapses depressed ...
    assert np.all(r.proj_weights("kc_mv") == 1.0)                 # ... avoid synapses untouched
    v_lit, v_dark = r.valence(LIT), r.valence(DARK)
    assert v_lit < -0.2                                           # the punished cue became aversive
    assert v_lit < v_dark                                         # more so than the unpunished one


def test_reward_with_a_cue_makes_it_attractive():
    r = Runner()
    r.pair(LIT, +1, n=10)
    assert r.proj_weights("kc_mv").mean() < 0.97 and np.all(r.proj_weights("kc_ma") == 1.0)
    assert r.valence(LIT) > 0.2 and r.c.drive > 1.3


def test_delayed_punishment_still_teaches():
    """Button pressed 1.5 s after the cue ended: the eligibility trace (tau 2.5 s) bridges the gap."""
    now, late = Runner(), Runner()
    now.pair(LIT, -1, n=10)
    late.pair(LIT, -1, n=10, delay_ms=1500, cue_during_delay=False)
    drop_now = 1.0 - now.proj_weights("kc_ma").mean()
    drop_late = 1.0 - late.proj_weights("kc_ma").mean()
    assert drop_late > 0.3 * drop_now
    assert late.valence(LIT) < -0.1


def test_one_pairing_is_a_small_graded_change():
    """Saturating eligibility: one pulse depresses active synapses by a few percent, not to w_min."""
    r = Runner()
    r.pair(LIT, -1, n=1)
    w = r.proj_weights("kc_ma")
    drop = 1.0 - w[w < 1.0]
    assert drop.size > 100 and drop.max() < 0.15 and w.min() > 0.85


def test_weights_summary_matches_the_contract():
    r = Runner()
    w = r.c.weights_summary()
    assert w["n"] == 2000 and len(w["ap"]) == len(w["av"]) == 2000
    assert set(w["ap"]) == {255} and w["sc"] == 1.0
    r.pair(LIT, -1, n=2)
    w = r.c.weights_summary()
    assert min(w["ap"]) < 255 and set(w["av"]) == {255}
    C.validate("brain/weights", {"v": 1, "seq": 0, "up": 0, **w})


def test_snapshot_keeps_learned_weights_and_rejects_another_circuit(tmp_path):
    r = Runner()
    r.pair(LIT, -1, n=2)
    state = r.c.state_dict()
    fresh = Runner()
    fresh.c.load_state(state)
    assert np.allclose(fresh.c.net.p_raw, r.c.net.p_raw, atol=1e-6)   # snapshots round to 6 decimals
    bad = dict(state, n_plastic=5)
    with pytest.raises(ValueError, match="plastic synapses"):
        fresh.c.load_state(bad)


def test_reset_learning_restores_initial_weights_but_reset_keeps_them():
    r = Runner()
    r.pair(LIT, -1, n=2)
    learned = r.c.net.p_raw.copy()
    r.c.reset()
    assert np.array_equal(r.c.net.p_raw, learned)
    r.c.reset_learning()
    assert np.all(r.c.net.p_raw == 1.0)


def test_pref_reports_valence_per_cue():
    r = Runner()
    r.pair(LIT, -1, n=4)
    r.cue(LIT, 3000)
    r.cue(DARK, 3000)
    p = r.c.pref()
    assert p["lt"] < p.get("odr", 0.0) and p["lt"] < 0


# ----------------------------------------------------------------------------- core integration

def sensors(seq, up, ol=0.45, orr=0.45, lt=0.8, bid=7):
    return {"v": 2, "seq": seq, "up": up, "bid": bid, "ol": ol, "or": orr, "d": 150.0, "lt": lt, "bat": 7.9}


def make_brain(tmp_path=None):
    cfg = BrainConfig(controller="neural")
    clock = [1000.0]
    b = Brain(cfg, C, Snapshots(tmp_path) if tmp_path else None, wall=lambda: clock[0])
    return b, clock


def ticks(b, clock, start, n):
    outs = []
    for i in range(start, start + n):
        clock[0] += 0.05
        outs += b.handle("sensors", sensors(i, 1000 + i * 50))
    return outs


def test_core_publishes_valid_weights_at_start_and_after_learning():
    b, clock = make_brain()
    outs = ticks(b, clock, 0, 2)
    w = [m for n, m in outs if n == "brain/weights"]
    assert len(w) == 1 and len(w[0]["ap"]) == w[0]["n"] == 2000
    C.validate("brain/weights", w[0])
    for r_seq in range(3):                                   # three punishments at the lit source
        ticks(b, clock, 2 + r_seq * 60, 30)
        b.handle("reward", {"v": 3, "seq": r_seq, "up": 1, "bid": 7, "r": -1, "src": "btn"})
        ticks(b, clock, 32 + r_seq * 60, 30)
    w2 = [m for n, m in ticks(b, clock, 200, 60) if n == "brain/weights"]
    assert b.counters["rewards"] == 3
    assert b.counters["weights_published"] >= 2 and min(b.ctrl.weights_summary()["ap"]) < 255


def test_core_drops_duplicate_rewards():
    b, clock = make_brain()
    ticks(b, clock, 0, 2)
    msg = {"v": 3, "seq": 5, "up": 1, "bid": 7, "r": -1, "src": "btn"}
    b.handle("reward", msg)
    b.handle("reward", msg)                                  # QoS 1 redelivery
    assert b.counters["rewards"] == 1


def test_core_refuses_weights_with_wrong_lengths(caplog):
    b, clock = make_brain()
    real = b.ctrl.weights_summary
    b.ctrl.weights_summary = lambda: {**real(), "ap": [255] * 1999}
    outs = ticks(b, clock, 0, 2)
    assert not [m for n, m in outs if n == "brain/weights"]
    assert "lengths" in caplog.text


def test_core_reset_weights_publishes_fresh_weights(tmp_path):
    b, clock = make_brain(tmp_path)
    ticks(b, clock, 0, 2)
    b.ctrl.net.set_plastic_weights(np.full(b.ctrl.net.p_raw.size, 0.5))
    msg = {"v": 3, "seq": 1, "up": 1, "src": "dash-3fa2c1", "c": "reset", "what": "weights"}
    b.handle("brain/control", msg)
    assert np.all(b.ctrl.net.p_raw == 1.0)
    outs = ticks(b, clock, 100, 50)
    assert [m for n, m in outs if n == "brain/weights"]
