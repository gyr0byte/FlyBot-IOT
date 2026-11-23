"""Firmware edge logic: state machine, TTL, watchdog, fallback, escape, sleep, estop (edge.py).

Every test drives Edge purely through its inputs with explicit times, as the firmware will.
"""
import random

import pytest

from flybot_sim.edge import BOOT, CLOUD, ESCAPE, ESTOP, FALLBACK, MANUAL, SLEEP, Edge, MotorMixer
from flybot_sim.edge_config import EdgeConfig


class Harness:
    """Edge plus message builders and a clock that steps update() every 10 ms."""

    def __init__(self, **cfg):
        self.e = Edge(EdgeConfig(**cfg), random.Random(0).random)
        self.now = 0
        self.seq = {}
        self.events = []

    def msg(self, topic, up=None, **fields):
        s = self.seq.get(topic, -1) + 1
        self.seq[topic] = s
        return {"v": 1, "seq": s, "up": self.now if up is None else up, **fields}

    def run(self, ms, step=10):
        out = self.e.out
        for _ in range(ms // step):
            self.now += step
            out = self.e.update(self.now)
            self.events += self.e.pop_events()
        return out

    def motor(self, l=0.5, r=0.5, ttl=300):
        self.e.on_motor(self.msg("motor", l=l, r=r, ttl=ttl, rs=0), self.now)

    def manual(self, l=0.5, r=0.5, ttl=300):
        self.e.on_manual(self.msg("manual", l=l, r=r, ttl=ttl, rs=0, src="dash-3fa2c1"), self.now)

    def mode(self, m, retained=False, src="dash-3fa2c1"):
        self.e.on_mode(self.msg(("mode", src), m=m, src=src), self.now, retained)

    def sleep(self, s, why="dark"):
        self.e.on_sleep(self.msg("sleep", s=s, why=why), self.now)

    def drive_brain(self, ms, l=0.5, r=0.5, every=50):
        """A healthy brain: one cmd/motor every 50 ms."""
        for _ in range(ms // every):
            self.motor(l, r)
            self.run(every)

    def boot(self, mode=None):
        self.e.on_link(True, self.now)
        self.e.set_odour_ready(True)
        if mode:
            self.mode(mode, retained=True)
        self.run(2100 if mode is None else 20)
        return self

    def collect(self):
        self.events += self.e.pop_events()
        return self.events

    def kinds(self):
        return [ev["e"] for ev in self.collect()]


# ----------------------------------------------------------------------------- boot and modes (5.1)

def test_boot_waits_2s_for_mode_then_assumes_auto():
    h = Harness()
    h.e.on_link(True, 0)
    h.e.set_odour_ready(True)
    h.run(1900)
    assert h.e.state == BOOT
    h.run(200)
    assert h.e.state == CLOUD and h.e.mode == "auto"


def test_boot_waits_for_odour_warmup_even_with_mode_known():
    h = Harness()
    h.e.on_link(True, 0)
    h.mode("auto", retained=True)
    h.run(3000)
    assert h.e.state == BOOT
    h.e.set_odour_ready(True)
    h.run(10)
    assert h.e.state == CLOUD


def test_retained_estop_at_boot_wins_and_blocks_motion():
    h = Harness().boot("estop")
    assert h.e.state == ESTOP
    h.motor(1, 1)
    h.manual(1, 1)
    assert h.run(200) == (0.0, 0.0)
    h.mode("auto")
    assert h.e.state == CLOUD


def test_boot_without_broker_times_out_into_fallback():
    h = Harness()
    h.e.set_odour_ready(True)
    h.run(9990)
    assert h.e.state == BOOT
    h.run(20)
    assert h.e.state == FALLBACK
    assert h.events[-1] == {"e": "fallback_on", "why": "mqtt_lost"}


# ----------------------------------------------------------------------------- cloud, TTL, watchdog

def test_cloud_follows_cmd_motor_after_ramp():
    h = Harness().boot()
    h.motor(0.5, -0.3)
    l, r = h.run(200)
    assert (l, r) == pytest.approx((0.5, -0.3))


def test_ttl_expiry_stops_motors_before_watchdog():
    h = Harness().boot()
    h.motor(0.6, 0.6, ttl=300)
    assert h.run(250)[0] == pytest.approx(0.6)
    out = h.run(200)        # 450 ms: ttl gone, watchdog not yet
    assert h.e.state == CLOUD and out == (0.0, 0.0)


def test_watchdog_500ms_silence_enters_fallback():
    h = Harness().boot()
    h.motor()
    h.run(500)
    assert h.e.state == CLOUD
    h.run(20)
    assert h.e.state == FALLBACK
    assert h.events[-1] == {"e": "fallback_on", "why": "cmd_timeout"}


def test_fallback_wanders_forward():
    h = Harness().boot()
    h.run(1000)
    l, r = h.run(500)
    assert h.e.state == FALLBACK and l > 0 and r > 0


def test_three_good_commands_leave_fallback():
    h = Harness().boot()
    h.run(1000)
    t_fb = h.now
    for _ in range(2):
        h.motor()
        h.run(50)
    assert h.e.state == FALLBACK
    h.motor()
    assert h.e.state == CLOUD
    off = [ev for ev in h.collect() if ev["e"] == "fallback_off"]
    assert off and off[0]["dur"] > 0
    assert h.run(200) == pytest.approx((0.5, 0.5))


def test_gap_longer_than_ttl_resets_the_good_count():
    h = Harness().boot()
    h.run(1000)
    h.motor(ttl=100)
    h.run(50)
    h.motor(ttl=100)
    h.run(150)              # gap 150 ms > ttl 100 ms
    h.motor(ttl=100)
    assert h.e.state == FALLBACK and h.e.good_count == 1


def test_invalid_message_resets_the_good_count():
    h = Harness().boot()
    h.run(1000)
    h.motor()
    h.motor()
    h.e.on_invalid("cmd/motor")
    h.motor()
    assert h.e.state == FALLBACK


def test_duplicates_and_late_commands_do_not_feed_the_watchdog():
    h = Harness().boot()
    h.motor()
    first = h.msg("motor", l=0.5, r=0.5, ttl=300, rs=0)
    h.e.on_motor(first, h.now)
    h.run(300)
    h.e.on_motor(first, h.now)                                   # duplicate
    h.e.on_motor({**first, "seq": first["seq"] - 1}, h.now)      # late
    h.run(250)
    assert h.e.state == FALLBACK
    assert h.e.streams.dropped == 2


def test_brain_restart_is_accepted():
    h = Harness().boot()
    h.e.on_motor({"v": 1, "seq": 500, "up": 90000, "l": 0.5, "r": 0.5, "ttl": 300, "rs": 0}, h.now)
    h.e.on_motor({"v": 1, "seq": 0, "up": 100, "l": 0.4, "r": 0.4, "ttl": 300, "rs": 0}, h.now)
    assert h.e.motor_cmd.l == 0.4 and h.e.streams.restarts == 1


def test_link_loss_in_cloud_enters_fallback_immediately():
    h = Harness().boot()
    h.motor()
    h.e.on_link(False, h.now)
    assert h.e.state == FALLBACK
    assert h.collect()[-1] == {"e": "fallback_on", "why": "mqtt_lost"}


# ----------------------------------------------------------------------------- manual (5.1 rule 2)

def test_manual_obeys_cmd_manual_and_ignores_cmd_motor():
    h = Harness().boot("manual")
    assert h.e.state == MANUAL
    h.motor(1, 1)
    assert h.run(100) == (0.0, 0.0)
    h.manual(0.4, 0.4)
    assert h.run(100) == pytest.approx((0.4, 0.4))


def test_manual_silence_stops_but_does_not_fall_back():
    h = Harness().boot("manual")
    h.manual()
    h.run(2000)
    assert h.e.state == MANUAL and h.e.out == (0.0, 0.0)
    assert "fallback_on" not in h.kinds()


# ----------------------------------------------------------------------------- sleep

def test_sleep_stops_motors_ignores_commands_and_never_falls_back():
    h = Harness().boot()
    h.motor()
    h.sleep(True)
    assert h.e.state == SLEEP
    h.motor(1, 1)
    out = h.run(5000)
    assert out == (0.0, 0.0) and h.e.state == SLEEP
    assert "fallback_on" not in h.kinds()


def test_wake_on_sleep_false_returns_to_cloud():
    h = Harness().boot()
    h.sleep(True)
    h.run(1000)
    h.sleep(False, why="light")
    assert h.e.state == CLOUD


def test_sleep_times_out_after_15s_without_repeat():
    h = Harness().boot()
    h.sleep(True)
    h.run(10000)
    h.sleep(True)            # repeat at 10 s keeps it asleep
    h.run(14000)
    assert h.e.state == SLEEP
    h.run(1100)
    assert h.e.state == CLOUD


def test_sleep_ignored_in_manual_and_estop():
    for m, st in (("manual", MANUAL), ("estop", ESTOP)):
        h = Harness().boot(m)
        h.sleep(True)
        assert h.e.state == st


def test_sleep_from_fallback_ends_fallback():
    h = Harness().boot()
    h.run(1000)
    assert h.e.state == FALLBACK
    h.sleep(True)
    assert h.e.state == SLEEP and "fallback_off" in h.kinds()


# ----------------------------------------------------------------------------- escape and obstacle

def looming(h, start_cm=70.0, speed_cm_s=100.0, every=40, n=6):
    """Feed ultrasonic readings of an object approaching at speed_cm_s."""
    d = start_cm
    for _ in range(n):
        h.e.on_distance(d, d, h.now)
        h.run(every)
        d -= speed_cm_s * every / 1000


def test_looming_object_triggers_escape_with_event():
    h = Harness().boot()
    h.drive_brain(200, 0.0, 0.0)
    looming(h, n=3)
    assert h.e.state == ESCAPE
    ev = [ev for ev in h.events if ev["e"] == "escape"]
    assert ev and 0 < ev[0]["ttc"] < 700 and 2 <= ev[0]["d"] <= 80 and ev[0]["lat"] >= 0
    l, r = h.e.out
    assert l < 0 and r < 0            # reversing


def test_escape_reverses_turns_then_returns_to_previous_state():
    h = Harness().boot()
    h.drive_brain(200, 0.0, 0.0)
    looming(h, n=3)
    h.drive_brain(300, 0.0, 0.0)
    assert h.e.state == ESCAPE
    h.drive_brain(700, 0.0, 0.0)      # reverse 400 + turn 450 ms done
    assert h.e.state == CLOUD


def test_driving_toward_a_wall_is_not_looming():
    h = Harness().boot()
    h.motor(1.0, 1.0, ttl=1000)
    h.run(300)                         # up to speed (output capped at 0.85)
    d = 60.0
    own = h.e.cfg.ego_speed_cm_s * h.e.out[0]
    for _ in range(10):
        h.motor(1.0, 1.0, ttl=1000)
        h.e.on_distance(d, d, h.now)
        h.run(40)
        d -= own * 0.04
    assert h.e.state == CLOUD and "escape" not in h.kinds()


def test_estop_overrides_escape():
    h = Harness().boot()
    h.drive_brain(200, 0.0, 0.0)
    looming(h, n=3)
    assert h.e.state == ESCAPE
    h.mode("estop")
    assert h.e.state == ESTOP
    assert h.run(10) == (0.0, 0.0)


def test_no_escape_while_asleep_or_in_estop():
    h = Harness().boot()
    h.sleep(True)
    looming(h)
    assert h.e.state == SLEEP
    h = Harness().boot("estop")
    looming(h)
    assert h.e.state == ESTOP


def test_obstacle_stop_blocks_forward_but_allows_turning():
    h = Harness().boot()
    h.e.on_distance(10.0, 10.0, h.now)
    h.motor(0.6, 0.6)
    assert h.run(200) == (0.0, 0.0)
    assert h.kinds().count("obstacle") == 1
    h.motor(0.5, -0.5)
    assert h.run(200) == pytest.approx((0.5, -0.5))


def test_fallback_turns_away_from_close_obstacle():
    h = Harness().boot()
    h.run(1000)
    h.e.on_distance(20.0, 20.0, h.now)
    l, r = h.run(200)
    assert l * r < 0                   # spinning on the spot


# ----------------------------------------------------------------------------- battery

def test_battery_warning_then_cutoff():
    h = Harness().boot()
    h.e.on_battery(6.7, h.now)
    h.e.on_battery(6.7, h.now)
    h.e.on_battery(6.3, h.now)
    h.motor(1, 1)
    out = h.run(100)
    lows = [ev for ev in h.collect() if ev["e"] == "low_battery"]
    assert [ev["cut"] for ev in lows] == [False, True]
    assert out == (0.0, 0.0)


# ----------------------------------------------------------------------------- building blocks

def test_mixer_dead_zone_cap_and_ramp():
    m = MotorMixer(EdgeConfig(motor_dead_zone=0.08, motor_duty_cap=0.85, motor_ramp_per_s=6.0))
    assert m.apply(0.05, -0.05, 0, immediate=True) == (0.0, 0.0)
    assert m.apply(1.0, -1.0, 0, immediate=True) == (0.85, -0.85)
    m.apply(0.0, 0.0, 0, immediate=True)
    assert m.apply(1.0, 1.0, 50)[0] == pytest.approx(0.3)   # 6 per second * 50 ms




def test_object_suddenly_appearing_is_not_counted_as_approach():
    """A reading jumping from far to near (something appeared) must not count as a looming hit."""
    h = Harness().boot()
    h.drive_brain(200, 0.0, 0.0)
    h.e.on_distance(195.0, 195.0, h.now)
    h.run(40)
    h.e.on_distance(60.0, 60.0, h.now)          # 3375 cm/s: an appearance, not an approach
    assert h.e.loom_hits == 0
    h.run(40)
    h.e.on_distance(56.0, 56.0, h.now)          # now a real approach (100 cm/s)
    assert h.e.loom_hits == 1 and h.e.state == CLOUD


def test_boot_ignores_motion_commands_and_has_no_watchdog():
    h = Harness()
    h.e.on_link(True, 0)
    h.mode("auto", retained=True)
    h.motor(1, 1, ttl=1000)
    h.manual(1, 1)
    h.sleep(True)
    out = h.run(3000)                 # odour not ready yet: still booting, 3 s without commands
    assert h.e.state == BOOT and out == (0.0, 0.0)
    assert "fallback_on" not in h.kinds()


def test_turning_toward_a_wall_is_not_looming():
    """The cone sweeping across a wall while we turn shrinks the range fast: not an approach."""
    h = Harness().boot()
    d = 95.0
    for _ in range(12):
        h.motor(0.7, 0.1, ttl=300)              # curving hard (left/right difference 0.6)
        h.e.on_distance(d, d, h.now)
        h.run(40)
        d -= 8.0                                # 200 cm/s of apparent closing from the sweep
    assert "escape" not in h.kinds() and h.e.state == CLOUD


def test_looming_still_detected_while_driving_almost_straight():
    h = Harness().boot()
    h.drive_brain(300, 0.4, 0.35)               # gentle curve: difference 0.05
    looming(h, n=4)
    assert "escape" in h.kinds()
