"""Save and restore everything the brain has learned or is in (plan step 5.11)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from flybot_brain.config import BrainConfig
from flybot_brain.core import Brain
from flybot_brain.persistence import Snapshots
from flybot_contract import Contract
from test_learning import LIT, Runner

C = Contract()
D = "dash-3fa2c1"


def brain(tmp_path, restore_lesions=False):
    cfg = BrainConfig(controller="neural", seed=7)
    cfg.persistence.restore_lesions = restore_lesions
    return Brain(cfg, C, Snapshots(tmp_path), wall=lambda: 1000.0)


def trained_brain(tmp_path):
    b = brain(tmp_path)
    r = Runner(seed=7)
    r.pair(LIT, -1, n=10)
    r.c.net.consolidate(30000, 60000)
    b.ctrl = r.c
    return b


def test_a_restarted_brain_keeps_what_it_learned(tmp_path):
    """Plan 5.11 done-when: save, start a new brain, load: the same learned weights and valence."""
    a = trained_brain(tmp_path)
    assert a.save("latest")
    b = brain(tmp_path)
    assert b.load("latest")
    assert np.allclose(b.ctrl.net.p_raw, a.ctrl.net.p_raw, atol=1e-6)
    assert np.allclose(b.ctrl.net.p_stable, a.ctrl.net.p_stable, atol=1e-6)
    assert b.ctrl.pref() == a.ctrl.pref()


def test_internal_states_habituation_and_sleep_are_restored(tmp_path):
    a = brain(tmp_path)
    a.hun, a.fear, a.hab = 0.7, 0.4, 0.55
    a.asleep, a.sleep_why, a.sleep_armed = True, "dark", True
    a.save("s")
    b = brain(tmp_path)
    b.load("s")
    assert (b.hun, b.fear, b.hab) == (0.7, 0.4, 0.55)
    assert (b.asleep, b.sleep_why) == (True, "dark")
    data = json.loads((tmp_path / "s.json").read_text())
    assert data["format"] == 2 and data["seed"] == 7


def test_lesions_are_saved_but_not_restored_by_default(tmp_path, caplog):
    a = brain(tmp_path)
    a.set_lesion("mb", True)
    a.set_lesion("fear", True)
    a.save("l")
    assert json.loads((tmp_path / "l.json").read_text())["lesions"] == ["fear", "mb"]
    b = brain(tmp_path)
    with caplog.at_level("INFO", logger="flybot_brain"):
        b.load("l")
    assert b.lesions == set() and not b.ctrl.net.silenced.any()
    assert "starting intact" in caplog.text


def test_lesions_are_restored_only_with_the_explicit_option_and_a_warning(tmp_path, caplog):
    a = brain(tmp_path)
    a.set_lesion("mb", True)
    a.save("l")
    b = brain(tmp_path, restore_lesions=True)
    with caplog.at_level("WARNING", logger="flybot_brain"):
        b.load("l")
    assert b.lesions == {"mb"} and b.ctrl.net.silenced.any()
    assert "restoring lesions" in caplog.text


def test_loading_replaces_current_lesions_with_the_snapshots(tmp_path):
    a = brain(tmp_path)
    a.save("intact")
    b = brain(tmp_path, restore_lesions=True)
    b.set_lesion("apl", True)
    b.load("intact")
    assert b.lesions == set() and not b.ctrl.net.silenced.any()


def test_format_1_snapshots_still_load(tmp_path):
    a = trained_brain(tmp_path)
    old = {"format": 1, "controller": "neural", "robot_ms": 0, "internal": {"hun": 0.3, "fear": 0.1},
           "controller_state": {k: v for k, v in a.ctrl.state_dict().items() if k != "stable"}}
    (tmp_path / "old.json").write_text(json.dumps(old), encoding="utf-8")
    b = brain(tmp_path)
    assert b.load("old")
    assert (b.hun, b.fear, b.hab, b.asleep, b.lesions) == (0.3, 0.1, 0.0, False, set())
    assert np.allclose(b.ctrl.net.p_raw, a.ctrl.net.p_raw, atol=1e-6) and not b.ctrl.net.p_stable.any()


def test_save_and_load_through_brain_control(tmp_path):
    a = trained_brain(tmp_path)
    msg = {"v": 4, "seq": 1, "up": 1, "src": D, "c": "save", "name": "after_training"}
    C.validate("brain/control", msg)
    st = next(m for n, m in a.handle("brain/control", msg) if n == "brain/state")
    assert st["ack"]["ok"] and (tmp_path / "after_training.json").exists()
