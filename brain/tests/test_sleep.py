"""Sleep and wake from the light sensor, and consolidation in the two-part synapse (plan step 5.9)."""
from __future__ import annotations

import numpy as np
import pytest

from flybot_brain.config import BrainConfig
from flybot_brain.core import Brain
from flybot_contract import Contract
from test_learning import LIT, Runner

C = Contract()
BID = 2847193551
D = "dash-3fa2c1"


# ----------------------------------------------------------------------------- two-part synapse (D1)

def trained(n=10, forget_tau_s=0.0):
    r = Runner(seed=3)
    r.c.p.forget_tau_s = forget_tau_s
    r.pair(LIT, -1, n=n)
    return r


def test_without_sleep_the_stable_part_stays_zero_and_forgetting_is_unchanged():
    r = trained()
    net = r.c.net
    assert not net.p_stable.any()
    w = net.p_raw.copy()
    net.relax(60000, 30000)                           # old rule: toward the initial weights
    expected = w + (net.p_raw0 - w) * (1 - np.exp(-2.0))
    assert np.array_equal(net.p_raw, expected)


def test_consolidation_protects_learned_depression_from_forgetting():
    awake, asleep = trained(), trained()
    depression = awake.c.net.p_raw0 - awake.c.net.p_raw
    assert depression.max() > 0.3
    asleep.c.net.consolidate(600000, 60000)            # 10 min asleep, tau 60 s
    assert np.allclose(asleep.c.net.p_stable, depression, atol=1e-4)
    for r in (awake, asleep):
        r.c.net.relax(3600000, 300000)                 # an hour of forgetting, tau 5 min
    assert np.allclose(awake.c.net.p_raw, awake.c.net.p_raw0, atol=1e-4)          # forgotten
    assert np.allclose(asleep.c.net.p_raw0 - asleep.c.net.p_raw, depression, atol=1e-3)   # kept


def test_forgetting_counts_as_a_weight_change_so_brain_weights_is_republished():
    r = trained()
    before = r.c.learning_changes()
    r.c.net.relax(1000, 300000)
    assert r.c.learning_changes() > before


def test_consolidation_does_not_change_the_weights_themselves():
    r = trained()
    w = r.c.net.p_raw.copy()
    r.c.net.consolidate(60000, 60000)
    assert np.array_equal(r.c.net.p_raw, w) and r.c.net.p_stable.max() > 0


def test_reset_learning_clears_the_stable_part():
    r = trained()
    r.c.net.consolidate(600000, 60000)
    r.c.reset_learning()
    assert not r.c.net.p_stable.any() and np.array_equal(r.c.net.p_raw, r.c.net.p_raw0)


def test_snapshot_keeps_the_stable_part_and_reads_older_snapshots():
    r = trained()
    r.c.net.consolidate(30000, 60000)
    state = r.c.state_dict()
    fresh = Runner(seed=3)
    fresh.c.load_state(state)
    assert np.allclose(fresh.c.net.p_stable, r.c.net.p_stable, atol=1e-6)
    old = dict(state)
    del old["stable"]
    older = Runner(seed=3)
    older.c.load_state(old)
    assert not older.c.net.p_stable.any() and np.allclose(older.c.net.p_raw, r.c.net.p_raw, atol=1e-6)


# ----------------------------------------------------------------------------- sleep and wake in the core

class Robot:
    """Feeds sensors messages every 50 ms of robot time with a chosen light level."""

    def __init__(self, b):
        self.b, self.seq, self.up, self.outs = b, 0, 0, []

    def run(self, seconds, lt, ol=0.1):
        for _ in range(int(seconds * 20)):
            msg = {"v": 2, "seq": self.seq, "up": self.up, "bid": BID, "ol": ol, "or": ol, "d": 150.0, "lt": lt,
                   "bat": 7.9}
            C.validate("sensors", msg)
            self.outs += self.b.handle("sensors", msg)
            self.seq += 1
            self.up += 50
        return self

    def sleeps(self):
        return [(m["s"], m["why"]) for n, m in self.outs if n == "cmd/sleep"]

    def motors_since(self, k):
        return sum(1 for n, _ in self.outs[k:] if n == "cmd/motor")


def brain(**sleep):
    cfg = BrainConfig(controller="neural", seed=1)
    for k, v in sleep.items():
        setattr(cfg.sleep, k, v)
    return Brain(cfg, C, None, wall=lambda: 1000.0)


def test_sustained_darkness_puts_the_brain_to_sleep_and_inputs_are_ignored():
    r = Robot(brain()).run(5, 0.8).run(19, 0.05)
    assert not r.b.asleep
    r.run(1.5, 0.05)
    assert r.b.asleep and r.sleeps() == [(True, "dark")]
    k = len(r.outs)
    r.run(6, 0.05)
    assert r.motors_since(k) == 0 and r.b.dec == "sleep"
    assert (True, "dark") in r.sleeps()[1:]           # repeated every 5 s while asleep


def test_a_passing_shadow_does_not_cause_sleep():
    r = Robot(brain())
    for _ in range(5):
        r.run(10, 0.05).run(1, 0.8)                     # 10 s dark, 1 s light, repeatedly
    assert not r.b.asleep and r.sleeps() == []


def test_waking_needs_clear_light_for_two_seconds():
    r = Robot(brain()).run(21, 0.05)
    assert r.b.asleep
    r.run(10, 0.2)                                       # between the thresholds: stays asleep
    assert r.b.asleep
    r.run(1.5, 0.6)
    assert r.b.asleep
    r.run(1.0, 0.6)
    assert not r.b.asleep and r.sleeps()[-1] == (False, "light")


def test_consolidation_runs_only_while_asleep():
    b = brain(consolidate_tau_s=60.0)
    r = Robot(b)
    b.ctrl.net.p_raw[:50] -= 0.3                         # some learned depression
    r.run(10, 0.8)
    assert not b.ctrl.net.p_stable.any()
    r.run(80, 0.05)                                      # 20 s to fall asleep, then 60 s asleep
    assert b.asleep and 0.15 < b.ctrl.net.p_stable[:50].mean() < 0.25    # ~1 - e^-1 of 0.3


def test_light_does_not_wake_a_user_requested_sleep_and_user_wake_stays_awake_in_the_dark():
    b = brain()
    r = Robot(b).run(2, 0.8)
    b.handle("brain/sleep", {"v": 2, "seq": 0, "up": 1, "s": True, "src": D})
    r.run(5, 0.9)
    assert b.asleep and b.sleep_why == "user"
    b.handle("brain/sleep", {"v": 2, "seq": 1, "up": 2, "s": False, "src": D})
    r.run(30, 0.05)                                      # dark, but no light seen since the user wake
    assert not b.asleep
    r.run(1, 0.8).run(21, 0.05)
    assert b.asleep and b.sleep_why == "dark"


def test_auto_sleep_off_never_sleeps():
    r = Robot(brain(auto=False)).run(60, 0.0)
    assert not r.b.asleep and r.sleeps() == []
