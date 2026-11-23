"""Sensor physics (sensors.py) and firmware sensor processing (edge_sensing.py)."""
import math

import numpy as np
import pytest

from flybot_sim.config import (BatteryParams, Light, Obstacle, OdourParams, RobotParams, Scenario,
                               Source, UltrasonicParams)
from flybot_sim.edge_config import EdgeConfig
from flybot_sim.edge_sensing import BatteryMonitor, DistanceFilter, OdourChannel
from flybot_sim.robot import Pose, Robot
from flybot_sim.sensors import Battery, LightSensor, OdourSensor, Ultrasonic
from flybot_sim.world import World

QUIET = dict(noise_v=0.0, drift_v_per_sqrt_s=0.0, warmup_excess_v=0.0)


class ConstWorld:
    """Stub world with a uniform, settable concentration."""
    def __init__(self, c=0.0):
        self.c = c

    def odour(self, x, y, t):
        return self.c


def robot_at(x=1.0, y=1.0, th=0.0):
    return Robot(RobotParams(), Pose(x, y, th), np.random.default_rng(0))


def world(**kw):
    return World(Scenario(**kw), np.random.default_rng(0))


# ----------------------------------------------------------------------------- odour physics

def test_odour_lag_rises_with_tau_rise_and_decays_with_tau_decay():
    p = OdourParams(tau_rise_s=2.0, tau_decay_s=6.0, **QUIET)
    s, r, w = OdourSensor(p, +1, np.random.default_rng(0)), robot_at(), ConstWorld(1.0)
    dt = 0.01
    for _ in range(round(2.0 / dt)):
        s.update(w, r, 0, dt)
    assert s.lagged == pytest.approx(1 - math.exp(-1), rel=1e-3)   # 63 % after one tau_rise
    level = s.lagged
    w.c = 0.0
    for _ in range(round(6.0 / dt)):
        s.update(w, r, 0, dt)
    assert s.lagged == pytest.approx(level * math.exp(-1), rel=1e-3)  # 37 % left after tau_decay


def test_odour_voltage_rises_and_saturates():
    p = OdourParams(**QUIET)
    s = OdourSensor(p, +1, np.random.default_rng(0))
    s.lagged = 0.0
    base = s.read_v(10)
    s.lagged = 0.3
    mid = s.read_v(10)
    s.lagged = 100.0
    top = s.read_v(10)
    assert base == pytest.approx(p.baseline_v[0])
    assert base < mid < top <= base + p.v_span + 1e-9


def test_left_sensor_reads_more_when_source_is_on_the_left():
    w = world(sources=[Source(1.0, 1.4, 1.0, 0.25)])
    w.sc.plume.patch_amp = 0.0
    r = robot_at(1.0, 1.0, 0.0)  # facing +x, source to the left (+y)
    p = OdourParams(**QUIET, gain_spread=(1.0, 1.0), baseline_v=(0.5, 0.5))
    left, right = OdourSensor(p, +1, np.random.default_rng(0)), OdourSensor(p, -1, np.random.default_rng(1))
    for _ in range(3000):
        left.update(w, r, 0, 0.01)
        right.update(w, r, 0, 0.01)
    assert left.read_v(30) > right.read_v(30) + 0.02


def test_sensors_are_about_12_cm_apart_and_angled_out():
    r = robot_at(0.0, 0.0, 0.0)
    p = OdourParams(inlet_m=0.0)
    l, rr = OdourSensor(p, +1, np.random.default_rng(0)), OdourSensor(p, -1, np.random.default_rng(0))
    (lx, ly), (rx, ry) = l.inlet(r), rr.inlet(r)
    assert math.hypot(lx - rx, ly - ry) == pytest.approx(0.12)
    assert l.direction(r) == pytest.approx(math.radians(30))
    assert rr.direction(r) == pytest.approx(math.radians(-30))


def test_odour_noise_and_drift_are_present_by_default():
    s = OdourSensor(OdourParams(), +1, np.random.default_rng(0))
    w, r = ConstWorld(0.0), robot_at()
    vals = []
    for i in range(2000):
        s.update(w, r, 100, 0.05)
        vals.append(s.read_v(100))
    assert np.std(vals) > 0.003


# ----------------------------------------------------------------------------- odour firmware processing

def test_channel_is_null_during_warmup_then_zero_in_clean_air():
    ch = OdourChannel(EdgeConfig(odour_warmup_ms=5000))
    outs = [ch.feed(0.5, t) for t in range(0, 6000, 50)]
    assert all(o is None for o in outs[:100])          # first 5 s
    assert outs[-1] == pytest.approx(0.0, abs=1e-9)
    assert ch.baseline == pytest.approx(0.5)


def test_channel_scales_change_from_baseline_to_0_1_and_clamps():
    cfg = EdgeConfig(odour_warmup_ms=1000, odour_full_scale_v=1.2, odour_smooth_alpha=1.0)
    ch = OdourChannel(cfg)
    for t in range(0, 1100, 50):
        ch.feed(0.5, t)
    assert ch.feed(0.5 + 0.6, 1200) == pytest.approx(0.5)
    assert ch.feed(3.0, 1250) == 1.0
    assert ch.feed(0.2, 1300) == 0.0


def test_channel_follows_slow_drift_in_clean_air_but_not_real_odour():
    cfg = EdgeConfig(odour_warmup_ms=1000, odour_smooth_alpha=1.0)
    ch = OdourChannel(cfg)
    t = 0
    for t in range(0, 1100, 50):
        ch.feed(0.5, t)
    v = 0.5
    for t in range(1100, 1100 + 120000, 50):  # 2 minutes of +0.02 V/min drift
        v += 0.02 / 1200
        out = ch.feed(v, t)
    assert out < 0.02                              # drift mostly removed
    assert ch.feed(v + 0.6, t + 50) > 0.45         # a real odour still shows


def test_warmup_excess_does_not_end_up_as_odour():
    p = OdourParams()  # default warm-up excess, noise, drift
    s = OdourSensor(p, +1, np.random.default_rng(3))
    ch = OdourChannel(EdgeConfig())
    w, r = ConstWorld(0.0), robot_at()
    out = None
    for i in range(0, 20000, 50):
        s.update(w, r, i / 1000, 0.05)
        out = ch.feed(s.read_v(i / 1000), i)
    assert out is not None and out < 0.05


# ----------------------------------------------------------------------------- ultrasonic

def quiet_us(**kw):
    base = dict(noise_m=0.0, noise_frac=0.0, dropout_p=0.0)
    base.update(kw)
    return Ultrasonic(UltrasonicParams(**base), np.random.default_rng(0))


def test_ultrasonic_measures_distance_to_wall_head_on():
    us, w, r = quiet_us(), world(), robot_at(1.0, 1.0, 0.0)
    # sensor at x = 1.08, wall at x = 2.0
    assert us.read(w, r) == pytest.approx(0.92)


def test_ultrasonic_cone_sees_off_axis_object_inside_but_not_outside():
    us = quiet_us(cone_deg=25.0, rays=25, max_range_m=1.0)
    r = robot_at(0.5, 1.0, 0.0)
    ox, oy = r.body_to_world(0.08, 0.0)
    inside = world(obstacles=[Obstacle("circle", ox + 0.5 * math.cos(math.radians(10)),
                                       oy + 0.5 * math.sin(math.radians(10)), r=0.02)])
    outside = world(obstacles=[Obstacle("circle", ox + 0.5 * math.cos(math.radians(25)),
                                        oy + 0.5 * math.sin(math.radians(25)), r=0.02)])
    assert us.read(inside, r) == pytest.approx(0.48, abs=0.01)
    assert us.read(outside, r) is None   # wall at 1.42 m is beyond max range 1.0


def test_ultrasonic_misses_steep_walls():
    # Heading 18 deg near the top wall (y = 2): the 25 deg cone's rays (5.5..30.5 deg) meet it at
    # 59.5..84.5 deg incidence, and the right wall is beyond the 0.9 m range.
    w, r = world(), robot_at(1.0, 1.8, math.radians(18))
    assert quiet_us(max_incidence_deg=50.0, max_range_m=0.9).read(w, r) is None   # all too steep
    assert quiet_us(max_incidence_deg=65.0, max_range_m=0.9).read(w, r) is not None  # some rays echo


def test_ultrasonic_dropout_rate():
    us = Ultrasonic(UltrasonicParams(dropout_p=0.03), np.random.default_rng(1))
    w, r = world(), robot_at(1.0, 1.0, 0.0)
    misses = sum(us.read(w, r) is None for _ in range(4000))
    assert 0.02 < misses / 4000 < 0.04


def test_ultrasonic_noise_grows_with_distance():
    us = Ultrasonic(UltrasonicParams(dropout_p=0.0), np.random.default_rng(1))
    w = world()
    near = [us.read(w, robot_at(1.7, 1.0, 0.0)) for _ in range(500)]
    far = [us.read(w, robot_at(0.2, 1.0, 0.0)) for _ in range(500)]
    assert np.std(far) > np.std(near) > 0


def test_distance_filter_rejects_a_single_spike_and_expires():
    f = DistanceFilter(EdgeConfig(dist_median_n=3, dist_valid_ms=200))
    assert f.feed(50.0, 0) == 50.0
    f.feed(51.0, 40)
    assert f.feed(5.0, 80) == 50.0          # spike rejected by the median
    assert f.feed(None, 120) == 50.0
    assert f.feed(None, 400) is None        # nothing valid for 200 ms


# ----------------------------------------------------------------------------- light and battery

def test_light_follows_day_night_and_can_be_absent():
    w = world()
    ls = LightSensor(Light(noise=0.0), np.random.default_rng(0))
    assert ls.read(w) == pytest.approx(0.80)
    w.day = False
    assert ls.read(w) == pytest.approx(0.05)
    assert LightSensor(Light(ldr_fitted=False), np.random.default_rng(0)).read(w) is None


def test_battery_drains_and_sags_under_load():
    b = Battery(BatteryParams(noise_v=0.0), np.random.default_rng(0))
    v0 = b.read_v()
    for _ in range(3600):
        b.update(1.0, 0.0)
    assert b.read_v() < v0                  # drained over an hour at idle
    idle = b.read_v()
    b.update(0.001, 1.0)
    assert b.read_v() == pytest.approx(idle - 0.30, abs=0.01)


def test_battery_monitor_smooths():
    m = BatteryMonitor(EdgeConfig(battery_smooth_alpha=0.1))
    m.feed(8.0)
    assert m.feed(7.0) == pytest.approx(7.9)
