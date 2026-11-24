"""Lesion switches and decision explanations (plan step 5.10)."""
from __future__ import annotations

import argparse

import numpy as np
import pytest

from flybot_brain import __main__ as cli
from flybot_brain.config import BrainConfig
from flybot_brain.controllers.base import Inputs
from flybot_brain.core import Brain
from flybot_brain.network import Network, load_circuit
from flybot_contract import Contract
from test_learning import DARK, LIT, Runner

C = Contract()
BID = 2847193551
D = "dash-3fa2c1"


@pytest.fixture(scope="module")
def designed():
    return load_circuit(BrainConfig().neural.circuit_path())


def counts(net, ms=1000, ol=0.4, orr=0.2, lt=0.8):
    net.set_input("odour_left", ol); net.set_input("odour_right", orr); net.set_input("light", lt)
    c = net.run(ms)
    return {g: int(c[net.group_names.index(g)]) for g in net.group_names}


# ----------------------------------------------------------------------------- engine

def test_circuit_offers_the_contract_lesion_targets(designed):
    assert Network(designed, seed=1).lesion_targets() == {"rcpt", "ln", "mb", "apl"}


def test_a_restored_lesion_leaves_the_network_bit_identical(designed):
    a, b = Network(designed, seed=4), Network(designed, seed=4)
    b.set_lesion("mb", True)
    b.set_lesion("mb", False)
    assert counts(a) == counts(b)


@pytest.mark.parametrize("target, groups", [("rcpt", ["rl", "rr"]), ("mb", ["kc"]), ("apl", ["apl"]),
                                            ("ln", ["ln_l", "ln_r"])])
def test_lesioned_groups_never_spike(designed, target, groups):
    net = Network(designed, seed=1)
    assert net.set_lesion(target, True) and target in net.lesions
    c = counts(net)
    assert all(c[g] == 0 for g in groups)
    net.set_lesion(target, False)
    assert target not in net.lesions and all(counts(net)[g] > 0 for g in groups)


def test_unknown_lesion_target_is_refused(designed):
    assert not Network(designed, seed=1).set_lesion("hippocampus", True)


def test_receptor_lesion_silences_the_odour_pathway(designed):
    net = Network(designed, seed=1)
    net.set_lesion("rcpt", True)
    c = counts(net, ol=0.9, orr=0.9, lt=0.0)                 # in the dark: light reaches no KCs either
    assert c["pl"] + c["pr"] == 0 and c["kc"] < 50
    assert counts(Network(designed, seed=1), ol=0.9, orr=0.9, lt=0.0)["kc"] > 500   # intact: odour drives KCs


def test_apl_lesion_makes_the_kenyon_cell_code_dense(designed):
    intact, lesioned = Network(designed, seed=1), Network(designed, seed=1)
    lesioned.set_lesion("apl", True)
    assert counts(lesioned, ol=0.6, orr=0.6)["kc"] > 3 * counts(intact, ol=0.6, orr=0.6)["kc"]


# ----------------------------------------------------------------------------- controller: learning and seeking

def test_mushroom_body_lesion_stops_learning_but_not_steering():
    r = Runner(seed=3)
    assert r.c.lesion("mb", True)
    r.pair(LIT, -1, n=5)
    assert np.array_equal(r.c.net.p_raw, r.c.net.p_raw0)       # no KC activity: nothing to depress
    assert r.c.valence == 0.0                                   # output neurons silent: no valence
    left = None
    for _ in range(20):
        r.t += 50
        left = r.c.step(Inputs(0.4, 0.1, 150.0, 0.05, 7.9, 50, r.t))
    assert left.dec == "seek" and left.r > left.l               # steering from the antennal lobe intact


def test_learning_lesion_stops_weight_changes_only():
    r = Runner(seed=3)
    assert r.c.lesion("learn", True)
    r.pair(LIT, -1, n=5)
    assert np.array_equal(r.c.net.p_raw, r.c.net.p_raw0)
    assert r.c.net.lesions == {"learn"}
    assert r.c.lesion("learn", False) and r.c.net.learning


# ----------------------------------------------------------------------------- core: brain/control and states

def make(controller="neural"):
    return Brain(BrainConfig(controller=controller, seed=1), C, None, wall=lambda: 1000.0)


def control(b, seq, **kw):
    msg = {"v": 4, "seq": seq, "up": seq, "src": D, **kw}
    C.validate("brain/control", msg)
    outs = b.handle("brain/control", msg)
    return next(m for n, m in outs if n == "brain/state")


def test_lesions_through_brain_control_are_reported_in_brain_state():
    b = make()
    st = control(b, 1, c="lesion", tgt="mb", off=True)
    assert st["ack"]["ok"] and st["les"] == ["mb"]
    st = control(b, 2, c="lesion", tgt="fear", off=True)
    assert st["les"] == ["fear", "mb"]
    st = control(b, 3, c="lesion", tgt="mb", off=False)
    assert st["ack"]["ok"] and st["les"] == ["fear"]


def test_hab_lesion_is_refused_because_habituation_is_on_the_robot():
    st = control(make(), 1, c="lesion", tgt="hab", off=True)
    assert st["ack"]["ok"] is False and st["les"] == []


def test_hunger_and_fear_lesions_hold_the_state_at_zero():
    b = make()
    b.hun, b.fear = 0.6, 0.6
    control(b, 1, c="lesion", tgt="hun", off=True)
    control(b, 2, c="lesion", tgt="fear", off=True)
    assert (b.hun, b.fear) == (0.0, 0.0)
    assert control(b, 3, c="state", hun=0.9)["ack"]["ok"] is False
    esc = {"v": 3, "seq": 1, "up": 10, "bid": BID, "e": "escape", "ttc": 400, "d": 30.0, "lat": 2.0,
           "resp": 1.0, "hab": 0.0}
    b.handle("events", esc)
    assert b.fear == 0.0


def test_baseline_has_no_circuit_lesions_but_states_still_work():
    b = make("baseline")
    assert control(b, 1, c="lesion", tgt="mb", off=True)["ack"]["ok"] is False
    assert control(b, 2, c="lesion", tgt="hun", off=True)["ack"]["ok"] is True


def test_start_option_applies_lesions(tmp_path, monkeypatch):
    real = cli.load_config

    def load_config(path=None):
        cfg = real(path)
        cfg.persistence.dir = str(tmp_path)
        return cfg
    monkeypatch.setattr(cli, "load_config", load_config)
    monkeypatch.setattr(cli.MqttSettings, "from_env", classmethod(lambda cls: cls("127.0.0.1", 1883, "brain", "x", "01")))
    args = argparse.Namespace(config=None, controller="neural", reset_on_reboot=False, seed=None, no_persistence=True,
                              lesion=["mb", "learn"], restore_lesions=False)
    brain, _ = cli.build(args)
    assert brain.lesions == {"mb", "learn"} and brain.ctrl.net.silenced.any()


# ----------------------------------------------------------------------------- explanations

def steady(r, cue, ms=1500, d=150.0, **states):
    ol, orr, lt = cue
    dec = None
    for _ in range(ms // 50):
        r.t += 50
        dec = r.c.step(Inputs(ol, orr, d, lt, 7.9, 50, r.t, **states))
    return dec


def test_explanation_names_the_odour_side():
    assert steady(Runner(seed=1), (0.4, 0.1, 0.05)).why[0] == "odour_l"
    assert steady(Runner(seed=1), (0.1, 0.4, 0.05)).why[0] == "odour_r"


def test_explanation_names_fear_and_hunger():
    assert "fear" in steady(Runner(seed=1), (0.3, 0.25, 0.05), fear=1.0).why
    assert "hunger" in steady(Runner(seed=1), (0.3, 0.25, 0.05), hun=1.0).why


def test_explanation_names_learned_avoidance():
    r = Runner(seed=3)
    r.pair(LIT, -1, n=10)
    d = steady(r, (0.3, 0.25, 0.8))
    assert "learned_avoid" in d.why and "fear" not in d.why


def test_explanation_names_obstacles_and_no_odour():
    assert "obstacle" in steady(Runner(seed=1), (0.4, 0.1, 0.05), d=40.0).why
    assert steady(Runner(seed=1), (0.0, 0.0, 0.05)).why == ["no_odour"]
