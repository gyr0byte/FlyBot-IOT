"""World geometry, odour field and robot kinematics."""
import math

import numpy as np
import pytest

from flybot_sim.config import Obstacle, RobotParams, Scenario, Source, load_scenario
from flybot_sim.robot import Pose, Robot
from flybot_sim.world import World


def make_world(**kw) -> World:
    sc = Scenario(**kw)
    return World(sc, np.random.default_rng(0))


def ideal_params(**kw) -> RobotParams:
    """No mismatch, no slip: for exact kinematics checks."""
    base = dict(left_gain=1.0, right_gain=1.0, slip_sigma=0.0, max_accel_mps2=1e9)
    base.update(kw)
    return RobotParams(**base)


def run(robot: Robot, world: World, l: float, r: float, seconds: float, dt: float = 0.005) -> None:
    for _ in range(round(seconds / dt)):
        robot.step(dt, l, r, world)


# ----------------------------------------------------------------------------- scenarios

@pytest.mark.parametrize("name", ["default", "obstacles", "two_sources", "night"])
def test_bundled_scenarios_load(name):
    sc = load_scenario(name)
    assert sc.sources and sc.start_positions


def test_unknown_scenario_key_is_an_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: bad\nsorces: []\n")
    with pytest.raises(ValueError, match="unknown keys"):
        load_scenario(p)


# ----------------------------------------------------------------------------- geometry

def test_ray_hits_wall_at_expected_distance():
    w = make_world()
    hit = w.cast(0.5, 1.0, 0.0, 4.0)  # pointing at the right wall (x = 2)
    assert hit.dist == pytest.approx(1.5)
    assert hit.incidence == pytest.approx(0.0, abs=1e-9)


def test_ray_incidence_on_angled_approach():
    w = make_world()
    hit = w.cast(1.0, 1.0, math.radians(45), 4.0)
    assert hit.incidence == pytest.approx(math.radians(45))


def test_ray_hits_circle_and_rect():
    w = make_world(obstacles=[Obstacle("circle", 1.0, 1.0, r=0.1), Obstacle("rect", 0.5, 0.2, w=0.2, h=0.2)])
    assert w.cast(0.5, 1.0, 0.0, 4.0).dist == pytest.approx(0.4)        # circle surface at x = 0.9
    assert w.cast(0.2, 0.3, 0.0, 4.0).dist == pytest.approx(0.3)        # rect face at x = 0.5
    assert w.cast(0.6, 0.6, -math.pi / 2, 4.0).dist == pytest.approx(0.2)  # rect top at y = 0.4


def test_ray_beyond_max_range_is_none():
    w = make_world()
    assert w.cast(0.1, 1.0, 0.0, 1.0) is None


def test_collision_checks():
    w = make_world(obstacles=[Obstacle("circle", 1.0, 1.0, r=0.1)])
    assert w.collides(0.05, 1.0, 0.08)           # wall
    assert w.collides(1.15, 1.0, 0.08)           # obstacle
    assert not w.collides(1.5, 1.5, 0.08)


# ----------------------------------------------------------------------------- odour field

def test_odour_falls_off_with_distance_and_halves_at_spread():
    w = make_world(sources=[Source(1.0, 1.0, strength=1.0, spread_m=0.3)])
    w.sc.plume.patch_amp = 0.0
    assert w.odour(1.0, 1.0, 0) == pytest.approx(1.0)
    assert w.odour(1.3, 1.0, 0) == pytest.approx(0.5)
    assert w.odour(1.0, 1.0, 0) > w.odour(1.2, 1.0, 0) > w.odour(1.6, 1.0, 0) > 0


def test_patchiness_varies_in_space_and_time_but_stays_bounded():
    w = make_world()
    vals = [w.patch_factor(x, y, t) for x in np.linspace(0, 2, 7) for y in np.linspace(0, 2, 7) for t in (0, 1, 2)]
    assert min(vals) >= 0.05 and max(vals) <= 1 + w.sc.plume.patch_amp + 1e-9
    assert np.std(vals) > 0.05


# ----------------------------------------------------------------------------- kinematics

def test_straight_line_distance():
    w = make_world()
    r = Robot(ideal_params(), Pose(0.3, 1.0, 0.0), np.random.default_rng(0))
    run(r, w, 1.0, 1.0, 2.0)
    assert r.pose.x == pytest.approx(0.3 + 0.30 * 2.0, rel=1e-6)
    assert r.pose.y == pytest.approx(1.0, abs=1e-9)
    assert r.path_length == pytest.approx(0.6, rel=1e-6)


def test_spin_in_place():
    w = make_world()
    r = Robot(ideal_params(), Pose(1.0, 1.0, 0.0), np.random.default_rng(0))
    # wheel speed 0.3 m/s opposite: angular rate = 0.6 / 0.13 rad/s
    run(r, w, -1.0, 1.0, 0.5)
    assert (r.pose.x, r.pose.y) == pytest.approx((1.0, 1.0), abs=1e-9)
    assert r.pose.theta == pytest.approx(0.6 / 0.13 * 0.5, rel=1e-6)


def test_acceleration_limit():
    w = make_world()
    r = Robot(ideal_params(max_accel_mps2=0.5), Pose(0.3, 1.0, 0.0), np.random.default_rng(0))
    run(r, w, 1.0, 1.0, 0.2)
    assert r.vl == pytest.approx(0.1, rel=1e-6)  # 0.5 m/s^2 * 0.2 s


def test_motor_mismatch_curves_toward_weaker_side():
    w = make_world()
    r = Robot(ideal_params(right_gain=0.95), Pose(0.3, 1.0, 0.0), np.random.default_rng(0))
    run(r, w, 1.0, 1.0, 2.0)
    assert r.pose.theta < -0.05  # right wheel slower: turns right (clockwise)


def test_slip_adds_randomness_but_is_reproducible():
    w = make_world()
    ends = []
    for seed in (1, 1, 2):
        r = Robot(ideal_params(slip_sigma=0.05), Pose(0.3, 1.0, 0.0), np.random.default_rng(seed))
        run(r, w, 1.0, 1.0, 2.0)
        ends.append((r.pose.x, r.pose.y, r.pose.theta))
    assert ends[0] == ends[1]
    assert ends[0] != ends[2]


def test_wall_collision_stops_and_counts_once():
    w = make_world()
    r = Robot(ideal_params(), Pose(1.7, 1.0, 0.0), np.random.default_rng(0))
    run(r, w, 1.0, 1.0, 3.0)
    assert r.pose.x <= 2.0 - r.p.radius_m + 1e-9
    assert r.pose.x > 1.8
    assert r.collisions == 1
    run(r, w, -1.0, -1.0, 0.5)   # back off ...
    run(r, w, 1.0, 1.0, 2.0)     # ... and hit it again
    assert r.collisions == 2


def test_can_turn_while_touching_a_wall():
    w = make_world()
    r = Robot(ideal_params(), Pose(1.7, 1.0, 0.0), np.random.default_rng(0))
    run(r, w, 1.0, 1.0, 3.0)
    th = r.pose.theta
    run(r, w, -1.0, 1.0, 0.2)
    assert r.pose.theta != th
