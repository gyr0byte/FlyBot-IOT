"""Looming habituation in the edge logic (plan step 5.8, contract revision 8)."""
import math

import pytest

from flybot_contract import Contract
from flybot_sim.edge import CLOUD, ESCAPE
from test_edge import Harness

C = Contract("01")
SLOW, FAST = 70.0, 150.0        # cm/s: FAST is more than 1.5 x SLOW (dishabituates)


def stimulus(h, speed=SLOW, start_cm=60.0, every=40, rest_ms=5000):
    """One looming object approaching the still robot, then quiet until rest_ms after its start.
    Returns the escape event it produced (or None)."""
    before = len(h.collect())
    d, t0 = start_cm, h.now
    while d > 8 and h.e.state != ESCAPE:
        h.drive_brain(every, 0.0, 0.0, every=every)
        h.e.on_distance(d, d, h.now)
        d -= speed * every / 1000
    h.e.on_distance(None, None, h.now)                        # gone
    h.drive_brain(max(50, rest_ms - (h.now - t0)), 0.0, 0.0)   # >= one update: the event is emitted there
    new = [ev for ev in h.collect()[before:] if ev["e"] == "escape"]
    assert len(new) <= 1
    if new:
        C.validate("events", {"v": 3, "seq": 0, "up": 1, "bid": 1, **new[0]})
    return new[0] if new else None


def booted(**cfg):
    h = Harness(**cfg).boot()
    h.drive_brain(200, 0.0, 0.0)
    return h


def test_repeated_harmless_stimuli_give_weaker_escapes():
    """5 s apart: weaker each time, levelling off where each step and the recovery balance:
    h* = e^(-5/60) (0.7 h* + 0.3) = 0.78, so the response settles near 0.22, just above the floor."""
    h = booted()
    resp = [stimulus(h)["resp"] for _ in range(12)]
    assert resp[0] == 1.0
    assert all(b < a for a, b in zip(resp[:5], resp[1:6]))
    assert 0.2 <= resp[-1] <= 0.3 and abs(resp[-1] - resp[-2]) < 0.03


def test_frequent_stimuli_stop_the_escape():
    """2 s apart the balance point is h* = 0.90: the response drops below the floor (0.2) and the
    robot no longer moves."""
    h = booted()
    resp = [stimulus(h, rest_ms=2000)["resp"] for _ in range(12)]
    assert resp[-1] == 0.0
    assert h.e.stats["habituated"] >= 1 and h.e.state == CLOUD


def test_habituated_stimulus_is_reported_without_lat_and_without_movement():
    h = booted()
    events = [stimulus(h, rest_ms=2000) for _ in range(12)]
    quiet = [ev for ev in events if ev["resp"] == 0.0]
    assert quiet and all("lat" not in ev and ev["hab"] > 0.7 for ev in quiet)
    moved = [ev for ev in events if ev["resp"] > 0]
    assert all("lat" in ev for ev in moved)


def test_escape_duration_scales_with_the_response():
    h = booted()
    cfg = h.e.cfg
    first = stimulus(h, rest_ms=200)
    assert first["resp"] == 1.0
    assert h.e.escape_turn_until - h.e.escape_reverse_until == cfg.escape_turn_ms
    assert h.e.escape_speed_now == pytest.approx(cfg.escape_speed)
    h.drive_brain(5000, 0.0, 0.0)
    ev = stimulus(h, rest_ms=200)
    assert 0 < ev["resp"] < 1
    assert h.e.escape_turn_until - h.e.escape_reverse_until == round(cfg.escape_turn_ms * ev["resp"])
    assert h.e.escape_speed_now == pytest.approx(cfg.escape_speed * ev["resp"])


def test_rest_restores_the_response():
    h = booted()
    for _ in range(6):
        stimulus(h)
    level = h.e.hab_level()
    h.drive_brain(60000, 0.0, 0.0)                            # one recovery time constant
    assert h.e.hab_level() == pytest.approx(level * math.exp(-1), rel=0.05)
    long_rest = booted()
    for _ in range(6):
        stimulus(long_rest)
    long_rest.drive_brain(300000, 0.0, 0.0)                   # 5 min
    assert stimulus(long_rest)["resp"] > 0.95


def test_a_faster_stimulus_gets_the_full_response_and_dishabituates():
    h = booted()
    for _ in range(12):
        stimulus(h, SLOW, rest_ms=2000)
    assert stimulus(h, SLOW, rest_ms=2000)["resp"] == 0.0
    fast = stimulus(h, FAST, rest_ms=2000)
    assert fast["resp"] == 1.0 and fast["hab"] > 0.7          # hab: the level before it
    assert stimulus(h, SLOW)["resp"] == 1.0                   # the slower class was reset


def test_habituation_off_always_gives_the_full_response():
    h = booted(hab_enabled=False)
    assert [stimulus(h)["resp"] for _ in range(8)] == [1.0] * 8
    assert h.e.hab_level() == 0.0


def test_status_reports_the_current_level():
    from flybot_sim.config import StartPos, load_scenario
    from flybot_sim.link import FakeLink
    from flybot_sim.sim import Simulation
    sc = load_scenario("default")
    sc.obstacles = []
    sim = Simulation(sc, FakeLink(C), C, seed=1, start=StartPos(1.0, 1.0, 0.0))
    for _ in range(int(3000 / 5)):
        sim.step()
    for _ in range(4):
        sim.robot.pose.x, sim.robot.pose.y, sim.robot.pose.theta = 1.0, 1.0, 0.0
        sim.launch_looming()
        for _ in range(int(3000 / 5)):
            sim.step()
    sim._publish_status(sim.t_ms)
    st = [m for n, m in sim.link.published if n == "status"][-1]
    assert st["hab"] == round(sim.edge.hab_level(), 2) and st["hab"] > 0
