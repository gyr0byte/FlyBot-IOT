"""Baseline controller decisions."""
from flybot_brain.config import Baseline
from flybot_brain.controllers.base import Inputs
from flybot_brain.controllers.baseline import BaselineController


def ctl(**kw):
    return BaselineController(Baseline(**kw), seed=1)


def run(c, ol, orr, d=150.0, n=1, t0=0, dt=50):
    dec = None
    for i in range(n):
        dec = c.step(Inputs(ol, orr, d, 0.8, 7.9, dt, t0 + i * dt))
    return dec


def test_turns_toward_the_stronger_side():
    left = run(ctl(), 0.30, 0.10, n=10)
    right = run(ctl(), 0.10, 0.30, n=10)
    assert left.dec == "seek" and left.r > left.l and left.why == ["odour_l"]
    assert right.dec == "seek" and right.l > right.r and right.why == ["odour_r"]


def test_equal_sides_go_straight():
    d = run(ctl(), 0.2, 0.2, n=10)
    assert d.l == d.r > 0


def test_small_differences_are_ignored():
    d = run(ctl(diff_deadband=0.05), 0.205, 0.195, n=10)    # normalised diff 0.025
    assert d.l == d.r


def test_stops_and_eats_at_the_source_with_hysteresis():
    c = ctl(arrive=0.55, leave_ratio=0.7)
    assert run(c, 0.7, 0.6, n=10).dec == "eat"
    d = run(c, 0.45, 0.45, n=20, t0=1000)                    # dipped, but above 0.385: stay
    assert d.dec == "eat" and (d.l, d.r) == (0.0, 0.0)
    assert run(c, 0.2, 0.2, n=20, t0=3000).dec == "seek"     # well below: leave


def test_obstacle_triggers_a_held_turn_toward_the_odour_side():
    c = ctl(avoid_cm=22, avoid_ms=700)
    run(c, 0.3, 0.1, n=5)
    d = run(c, 0.3, 0.1, d=15.0, t0=250)
    assert d.dec == "avoid" and d.r > 0 > d.l               # turning left (towards stronger left)
    d = run(c, 0.3, 0.1, d=150.0, t0=600)                    # clear already, but keep turning
    assert d.dec == "avoid"
    assert run(c, 0.3, 0.1, d=150.0, t0=1000).dec == "seek"


def test_slows_down_near_obstacles():
    far = run(ctl(), 0.2, 0.2, d=150.0, n=10)
    near = run(ctl(), 0.2, 0.2, d=30.0, n=10)
    assert 0 < near.l < far.l and "obstacle" in near.why


def test_wanders_with_changing_curves_when_there_is_no_odour():
    c = ctl(wander_change_ms=500)
    decs = [c.step(Inputs(0.0, 0.0, 150.0, 0.8, 7.9, 50, i * 50)) for i in range(100)]
    assert all(d.dec == "explore" and d.l + d.r > 0 for d in decs)
    curves = {round(d.r - d.l, 4) for d in decs}
    assert len(curves) >= 5                                    # it doesn't drive one fixed circle


def test_wandering_is_reproducible_and_reset_restarts_it():
    def seq(c):
        return [c.step(Inputs(0.0, 0.0, None, None, 7.9, 50, i * 50)).r for i in range(80)]
    a, b = ctl(), ctl()
    first = seq(a)
    assert first == seq(b)
    a.reset()
    assert seq(a) == first


def test_smoothing_damps_a_single_spike():
    c = ctl(smooth_alpha=0.35)
    run(c, 0.0, 0.0, n=10)
    d = c.step(Inputs(0.8, 0.0, 150.0, 0.8, 7.9, 50, 1000))   # one noisy left spike
    assert d.dec == "seek"                                       # reacts, but ...
    assert d.dec != "eat"                                        # ... a spike is not arrival
