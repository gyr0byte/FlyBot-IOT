"""Learning-experiment support: lamps, labelled sources, phases, relative training starts,
mirroring, and the automated training reward (plan step 5.6)."""
import numpy as np
import pytest

from flybot_contract import Contract
from flybot_sim.config import (Light, LightPatch, RelStart, Scenario, StartPos, check_training_starts, load_scenario, mirror_scenario, phase_starts,
                               source_by_label, validate)
from flybot_sim.link import FakeLink
from flybot_sim.runner import trial_done
from flybot_sim.sensors import LightSensor
from flybot_sim.sim import Simulation
from flybot_sim.world import World

C = Contract("01")


def lamp_world(**patch):
    sc = Scenario(light=Light(day=False, patches=[LightPatch(x=1.0, y=1.0, r=0.4, level=0.9, edge_m=0.1, **patch)]))
    return sc, World(sc, np.random.default_rng(0))


# ----------------------------------------------------------------------------- light patches

def test_lamp_is_bright_inside_dark_outside_and_fades_over_its_edge():
    sc, w = lamp_world()
    night = sc.light.night_level
    assert w.light_level() == night                     # no position: ambient only
    assert w.light_level(1.0, 1.0) == pytest.approx(0.9)
    assert w.light_level(1.39, 1.0) == pytest.approx(0.9)
    assert w.light_level(1.45, 1.0) == pytest.approx(night + (0.9 - night) * 0.5)
    assert w.light_level(1.6, 1.0) == pytest.approx(night)


def test_light_sensor_reads_the_light_where_the_robot_is():
    sc, w = lamp_world()
    sc.light.noise = 0.0
    sensor = LightSensor(sc.light, np.random.default_rng(0))

    class R:                                            # just enough robot for the sensor
        class pose:
            x, y = 1.0, 1.0
    assert sensor.read(w, R) == pytest.approx(0.9)
    R.pose.x = 0.2
    assert sensor.read(w, R) == pytest.approx(sc.light.night_level)


# ----------------------------------------------------------------------------- the learning scenario

def test_learning_scenario_loads_with_labelled_sources_and_phases():
    sc = load_scenario("learning")
    assert sc.goal == "any"
    assert [s.label for s in sc.sources] == ["lit", "dark"]
    assert [p.name for p in sc.experiment.phases] == ["pre", "train", "post"]
    lit, dark = source_by_label(sc, "lit"), source_by_label(sc, "dark")
    w = World(sc, np.random.default_rng(0))
    assert w.light_level(lit.x, lit.y) > 0.8 > 0.1 > w.light_level(dark.x, dark.y)
    for sp in sc.start_positions:                       # test starts: equally far from both sources
        assert abs(abs(sp.x - lit.x) - abs(sp.x - dark.x)) < 0.2


@pytest.mark.parametrize("mirrored", [False, True])
def test_each_animals_training_starts_are_near_its_own_lit_source(mirrored):
    sc = load_scenario("learning")
    if mirrored:
        sc = mirror_scenario(sc)
    assert check_training_starts(sc) == []
    train = next(p for p in sc.experiment.phases if p.relative_to)
    lit, dark = source_by_label(sc, "lit"), source_by_label(sc, "dark")
    for sp in phase_starts(sc, train):
        assert abs(sp.x - lit.x) < abs(sp.x - dark.x)
        assert (sp.x < sc.arena.width_m / 2) == (not mirrored)


def test_mirror_flips_sources_lamps_starts_and_headings():
    sc = load_scenario("learning")
    m = mirror_scenario(sc)
    W = sc.arena.width_m
    assert source_by_label(m, "lit").x == pytest.approx(W - source_by_label(sc, "lit").x)
    assert m.light.patches[0].x == pytest.approx(W - sc.light.patches[0].x)
    assert m.start_positions[1].x == pytest.approx(W - sc.start_positions[1].x)
    assert m.start_positions[1].heading_deg == pytest.approx(180 - sc.start_positions[1].heading_deg)
    train = sc.experiment.phases[1]
    a, b = phase_starts(sc, train), phase_starts(m, m.experiment.phases[1])
    for p, q in zip(a, b):                              # relative starts mirror with their source
        assert (q.x, q.y, q.heading_deg) == pytest.approx((W - p.x, p.y, 180 - p.heading_deg))
    assert sc.sources[0].x != m.sources[0].x            # the original is untouched


def test_training_start_check_catches_a_start_nearer_the_other_source():
    sc = load_scenario("learning")
    sc.experiment.phases[1].relative_starts.append(RelStart(dx=0.8, dy=-0.1))
    problems = check_training_starts(sc)
    assert len(problems) == 1 and "train" in problems[0]


@pytest.mark.parametrize("change, message", [
    (lambda sc: sc.sources[1].__setattr__("label", "lit"), "unique"),
    (lambda sc: setattr(sc, "goal", "nearest"), "goal"),
    (lambda sc: sc.experiment.phases[1].rewards.update(lamp=1), "unknown source label"),
    (lambda sc: sc.experiment.phases[1].rewards.update(lit=2), "1 or -1"),
    (lambda sc: setattr(sc.experiment.phases[1], "relative_to", "moon"), "relative_to"),
    (lambda sc: setattr(sc.experiment.phases[1], "relative_starts", []), "relative_starts"),
    (lambda sc: setattr(sc.experiment.phases[0], "trials", 0), "trials"),
])
def test_invalid_experiments_are_rejected(change, message):
    sc = load_scenario("learning")
    change(sc)
    with pytest.raises(ValueError, match=message):
        validate(sc, "learning.yaml")


# ----------------------------------------------------------------------------- arrival and the training reward

def run_until_done(sc, start, **kw):
    """Run offline (no brain) from a start on top of a source, until the trial is over."""
    link = FakeLink(C)
    sim = Simulation(sc, link, C, seed=1, start=start, **kw)
    done = trial_done(sc.duration_s)
    steps = 0
    while not done(sim):
        sim.step()
        steps += 1
        assert steps < 20000, "trial never ended"
    return sim, link


def on(sc, label):
    s = source_by_label(sc, label)
    return StartPos(s.x, s.y, 90.0)


def test_reaching_the_rewarded_source_presses_btn_after_the_delay_and_holds():
    sc = load_scenario("learning")
    sim, link = run_until_done(sc, on(sc, "lit"), rewards={"lit": -1}, reward_delay_s=1.5, hold_after_reward_s=2.0)
    rewards = [m for n, m in link.published if n == "reward"]
    assert len(rewards) == 1
    r = rewards[0]
    assert (r["r"], r["src"], r["bid"]) == (-1, "btn", link.bid)
    reached_ms = sim.m.boot_done_ms + sim.m.time_to_source_s * 1000
    assert r["up"] - reached_ms == pytest.approx(1500, abs=10)
    assert sim.t_ms - r["up"] == pytest.approx(2000, abs=10)       # the hold after the press
    res = sim.results()
    assert (res["reached"], res["reached_label"], res["reward_sent"]) == (True, "lit", -1)


def test_reaching_an_unrewarded_source_ends_the_trial_without_a_reward():
    sc = load_scenario("learning")
    sim, link = run_until_done(sc, on(sc, "dark"), rewards={"lit": -1})
    assert not [m for n, m in link.published if n == "reward"]
    res = sim.results()
    assert (res["reached_label"], res["reward_sent"]) == ("dark", None)
    assert sim.trial_time_s() == pytest.approx(res["time_to_source_s"], abs=0.01)


def test_goal_first_only_counts_the_first_source():
    sc = load_scenario("learning")
    sc.goal = "first"
    sc.duration_s = 3.0
    sim, _ = run_until_done(sc, on(sc, "dark"))
    assert not sim.m.reached and sim.m.reached_label is None
