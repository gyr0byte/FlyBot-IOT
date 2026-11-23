"""Neural controller: network-driven steering readout (plan step 5.4) + designed motor layer."""
from __future__ import annotations

import pytest

from flybot_brain.config import BrainConfig
from flybot_brain.controllers import make_controller
from flybot_brain.controllers.base import Inputs
from flybot_brain.controllers.neural import NeuralController
from flybot_brain.network import CircuitError


def ctl(**neural):
    cfg = BrainConfig(controller="neural")
    for k, v in neural.items():
        setattr(cfg.neural, k, v)
    return NeuralController(cfg)


def run(c, ol, orr, d=150.0, ms=2000, t0=0, dt=50):
    dec = None
    for i in range(ms // dt):
        dec = c.step(Inputs(ol, orr, d, 0.8, 7.9, dt, t0 + i * dt))
    return dec


def test_factory_builds_the_neural_controller_on_the_designed_circuit():
    c = make_controller(BrainConfig(controller="neural"))
    assert isinstance(c, NeuralController) and c.circuit.name == "designed"


def test_turns_toward_the_stronger_side():
    left = run(ctl(), 0.30, 0.12)
    right = run(ctl(), 0.12, 0.30)
    assert left.dec == "seek" and left.r > left.l and left.why[0] == "pn_left"
    assert right.dec == "seek" and right.l > right.r and right.why[0] == "pn_right"


def test_equal_odour_goes_roughly_straight():
    d = run(ctl(), 0.3, 0.3)
    assert d.dec == "seek" and abs(d.r - d.l) < 0.15 and d.l > 0


def test_weak_far_field_odour_is_still_followed():
    d = run(ctl(), 0.08, 0.03, ms=3000)
    assert d.dec == "seek" and d.r > d.l


def test_no_odour_wanders_forward():
    c = ctl()
    decs = [c.step(Inputs(0.0, 0.0, 150.0, 0.8, 7.9, 50, i * 50)) for i in range(100)]
    assert all(d.dec == "explore" and d.l + d.r > 0 for d in decs)


def test_strong_odour_means_arrived_with_hysteresis():
    c = ctl()
    assert run(c, 0.95, 0.9).dec == "eat"
    assert run(c, 0.75, 0.7, t0=2000).dec == "eat"          # dipped, but above arrive * leave_ratio
    assert run(c, 0.2, 0.2, t0=4000).dec == "seek"


def test_obstacle_uses_the_designed_motor_layer_turning_toward_the_active_side():
    c = ctl()
    run(c, 0.3, 0.1)
    d = c.step(Inputs(0.3, 0.1, 15.0, 0.8, 7.9, 50, 2000))
    assert d.dec == "avoid" and d.r > 0 > d.l


def test_steering_follows_a_side_switch_only_after_smoothing():
    c = ctl(smooth_ms=400)
    run(c, 0.35, 0.10)
    first = c.step(Inputs(0.10, 0.35, 150.0, 0.8, 7.9, 50, 2000))       # odour jumps to the right
    assert first.r > first.l                                              # still turning left (smoothed)
    later = run(c, 0.10, 0.35, ms=1500, t0=2050)
    assert later.l > later.r                                              # now turning right


def test_network_time_is_robot_time():
    c = ctl()
    c.step(Inputs(0.5, 0.5, 150.0, 0.8, 7.9, 0, 0))          # reboot / first tick: no steps
    assert sum(c.spikes().values()) == 0
    c.step(Inputs(0.5, 0.5, 150.0, 0.8, 7.9, 50, 50))
    assert c.spikes()["rl"] > 0


def test_spike_report_uses_contract_keys():
    c = ctl()
    run(c, 0.4, 0.3, ms=1000)
    assert set(c.spikes()) == {"rl", "rr", "pl", "pr", "ln", "kc", "apl"}


def test_reset_makes_runs_reproducible():
    def seq(c):
        return [round(c.step(Inputs(0.3, 0.2, 150.0, 0.8, 7.9, 50, i * 50)).r, 6) for i in range(60)]
    a = ctl()
    first = seq(a)
    a.reset()
    assert seq(a) == first


def test_circuit_without_steering_outputs_is_rejected(tmp_path):
    import shutil
    from flybot_brain.config import REPO
    src = REPO / "brain" / "circuits" / "designed"
    shutil.copytree(src, tmp_path / "c")
    m = (tmp_path / "c" / "manifest.yaml")
    m.write_text(m.read_text().replace("steer_right:", "other:"))
    with pytest.raises(CircuitError, match="steer_right"):
        ctl(circuit=str(tmp_path / "c"))
