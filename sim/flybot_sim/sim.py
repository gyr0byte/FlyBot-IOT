"""Simulation core: world + robot + sensors + firmware edge logic + contract messages.

Time: the simulation has its own clock, t_ms, which is also the robot's uptime (`up`). It
advances in 5 ms physics steps. The runner decides how that clock relates to the wall clock
(real time, or lockstep as fast as the brain answers; contract section 7.1).
"""
from __future__ import annotations

import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from . import FIRMWARE_VERSION
from .config import Scenario
from .contract import Contract, ContractError, Outbox
from .edge import BOOT, CLOUD, ESCAPE, FALLBACK, MODE_AUTO, Edge
from .edge_config import EdgeConfig
from .edge_sensing import BatteryMonitor, DistanceFilter, OdourChannel
from .robot import Pose, Robot
from .sensors import Battery, LightSensor, OdourSensor, Ultrasonic
from .world import World

PHYSICS_MS = 5
log = logging.getLogger("flybot_sim")


@dataclass
class TrialMetrics:
    reached: bool = False
    time_to_source_s: float | None = None
    boot_done_ms: int | None = None
    pings_sent: int = 0
    pongs: int = 0
    rtts: deque = field(default_factory=lambda: deque(maxlen=2000))    # (t_ms, rtt_ms)
    crtts: deque = field(default_factory=lambda: deque(maxlen=20000))  # (t_ms, crtt_ms)
    motor_cmds: int = 0
    reward_presses: int = 0


class Simulation:
    def __init__(self, sc: Scenario, link, contract: Contract | None = None, start_index: int = 0,
                 seed: int | None = None, lockstep: bool = False):
        self.sc = sc
        self.seed = sc.seed if seed is None else seed
        self.lockstep = lockstep
        rngs = np.random.default_rng(self.seed).spawn(8)
        self.world = World(sc, rngs[0])
        self.start_index = start_index % len(sc.start_positions)
        sp = sc.start_positions[self.start_index]
        self.start = sp
        self.robot = Robot(sc.robot, Pose(sp.x, sp.y, math.radians(sp.heading_deg)), rngs[1])
        self.odour = [OdourSensor(sc.odour, +1, rngs[2]), OdourSensor(sc.odour, -1, rngs[3])]
        self.ultrasonic = Ultrasonic(sc.ultrasonic, rngs[4])
        self.light = LightSensor(sc.light, rngs[5])
        self.battery = Battery(sc.battery, rngs[6])

        self.cfg = EdgeConfig.with_overrides(sc.edge)
        self.edge = Edge(self.cfg, random.Random(int(rngs[7].integers(2 ** 31))).random)
        self.odour_ch = [OdourChannel(self.cfg), OdourChannel(self.cfg)]
        self.dist_filter = DistanceFilter(self.cfg)
        self.batt_mon = BatteryMonitor(self.cfg)

        self.contract = contract or link.contract
        self.outbox = Outbox(self.contract)
        self.link = link

        self.t_ms = 0
        tm = sc.timing
        self.sensors_period = round(1000 / tm.sensors_hz)
        self.us_period = round(1000 / sc.ultrasonic.rate_hz)
        self.ping_period = round(1000 / tm.ping_hz)
        self.status_period = round(tm.status_every_s * 1000)
        self.next_sensors = self.next_us = self.next_ping = 0
        self.next_status = 0
        self.boot_event_sent = False

        self.values = {"ol": None, "or": None, "d": None, "lt": None, "bat": sc.battery.start_v}
        self.raw_d_cm: float | None = None
        self.sent_sensors: dict[int, tuple[int, float]] = {}   # seq -> (t_ms, wall)
        self.pings: dict[int, tuple[int, float]] = {}
        self.m = TrialMetrics()
        self.trail: deque = deque(maxlen=4000)

    # ------------------------------------------------------------------ properties

    @property
    def up(self) -> int:
        return self.t_ms

    def booted(self) -> bool:
        return self.m.boot_done_ms is not None

    def trial_time_s(self) -> float:
        return 0.0 if not self.booted() else (self.t_ms - self.m.boot_done_ms) / 1000.0

    def expect_command(self) -> bool:
        """Lockstep: is a cmd/motor expected in answer to the last sensors message? (section 7.1 (5))"""
        return (self.link.connected and self.edge.mode == MODE_AUTO
                and self.edge.state in (CLOUD, FALLBACK, ESCAPE))

    def target(self):
        return self.world.sources[0]

    # ------------------------------------------------------------------ inbound

    def handle_inbox(self) -> None:
        q = self.link.inbox
        while not q.empty():
            self.handle(q.get_nowait())

    def handle(self, item) -> None:
        t, e = self.t_ms, self.edge
        kind = item[0]
        if kind == "link":
            e.on_link(item[1], t)
            if item[1] and not self.boot_event_sent:
                self.boot_event_sent = True
                self._publish("events", e="boot", rst="power_on", fw=FIRMWARE_VERSION)
            return
        if kind == "invalid":
            e.on_invalid(item[1])
            return
        _, name, msg, retained = item
        if name == "cmd/motor":
            self.m.motor_cmds += 1
            sent = self.sent_sensors.get(msg["rs"])
            if sent is not None:
                rt = (time.perf_counter() - sent[1]) * 1000 if self.lockstep else t - sent[0]
                self.m.crtts.append((t, rt))
                self.sent_sensors.pop(msg["rs"], None)   # count the first answer only
            e.on_motor(msg, t)
        elif name == "cmd/manual":
            e.on_manual(msg, t)
        elif name == "cmd/mode":
            e.on_mode(msg, t, retained)
        elif name == "cmd/sleep":
            e.on_sleep(msg, t)
        elif name == "pong":
            sent = self.pings.pop(msg["ps"], None)
            if sent is not None:
                self.m.pongs += 1
                rt = (time.perf_counter() - sent[1]) * 1000 if self.lockstep else t - msg["pu"]
                self.m.rtts.append((t, rt))

    # ------------------------------------------------------------------ user actions (live view)

    def move_source(self, i: int, dx: float, dy: float) -> None:
        s = self.world.sources[i % len(self.world.sources)]
        s["x"] = min(max(s["x"] + dx, 0.05), self.world.w - 0.05)
        s["y"] = min(max(s["y"] + dy, 0.05), self.world.h - 0.05)

    def launch_looming(self) -> None:
        p = self.robot.pose
        self.world.launch_looming(p.x, p.y, p.theta, self.t_ms / 1000)

    def toggle_day(self) -> None:
        self.world.day = not self.world.day

    def press_reward(self, r: int) -> None:
        self.m.reward_presses += 1
        self._publish("reward", r=r, src="btn")

    # ------------------------------------------------------------------ stepping

    def step(self) -> int | None:
        """Advance one physics step (5 ms). Returns the seq of a sensors message published now."""
        self.handle_inbox()
        t = self.t_ms
        dt = PHYSICS_MS / 1000.0
        ts = t / 1000.0

        for s in self.odour:
            s.update(self.world, self.robot, ts, dt)
        self.battery.update(dt, (abs(self.edge.out[0]) + abs(self.edge.out[1])) / 2)

        if t >= self.next_us:
            self.next_us += self.us_period
            raw = self.ultrasonic.read(self.world, self.robot)
            self.raw_d_cm = None if raw is None else raw * 100.0
            filt = self.dist_filter.feed(self.raw_d_cm, t)
            self.values["d"] = None if filt is None else round(min(max(filt, 2.0), 400.0), 1)
            self.edge.on_distance(self.raw_d_cm, filt, t)

        sensors_seq = None
        if t >= self.next_sensors:
            self.next_sensors += self.sensors_period
            sensors_seq = self._sample_and_publish_sensors(t)

        was_boot = self.edge.state == BOOT
        l, r = self.edge.update(t)
        if was_boot and self.edge.state != BOOT and self.m.boot_done_ms is None:
            self.m.boot_done_ms = t
        for ev in self.edge.pop_events():
            self._publish("events", **ev)

        self.robot.step(dt, l, r, self.world)
        self.world.update(ts, dt)
        if t % 50 == 0:
            self.trail.append((self.robot.pose.x, self.robot.pose.y))
        self._check_reached(t)

        if self.link.connected:
            if self.edge.status_dirty or t >= self.next_status:
                self._publish_status(t)
            if t >= self.next_ping:
                self.next_ping += self.ping_period
                msg = self._publish("ping")
                if msg is not None:
                    self.m.pings_sent += 1
                    self.pings[msg["seq"]] = (t, time.perf_counter())
        self.t_ms += PHYSICS_MS
        return sensors_seq

    def _sample_and_publish_sensors(self, t: int) -> int | None:
        vals = self.values
        for i, ch in enumerate(self.odour_ch):
            v = ch.feed(self.odour[i].read_v(t / 1000.0), t)
            vals["ol" if i == 0 else "or"] = None if v is None else round(v, 3)
        self.edge.set_odour_ready(all(ch.ready for ch in self.odour_ch))
        lt = self.light.read(self.world)
        vals["lt"] = None if lt is None else round(lt, 2)
        bat = self.batt_mon.feed(self.battery.read_v())
        vals["bat"] = round(min(max(bat, 0.0), 10.0), 2)
        self.edge.on_battery(bat, t)
        if not self.link.connected:
            return None
        msg = self._publish("sensors", {k: vals[k] for k in ("ol", "or", "d", "lt", "bat")})
        if msg is None:
            return None
        self.sent_sensors[msg["seq"]] = (t, time.perf_counter())
        if len(self.sent_sensors) > 400:
            for k in list(self.sent_sensors)[:200]:
                del self.sent_sensors[k]
        return msg["seq"]

    def _publish_status(self, t: int) -> None:
        e = self.edge
        self.next_status = t + self.status_period
        e.status_dirty = False
        rtt = self._median(self.m.rtts, t)
        crtt = self._median(self.m.crtts, t)
        self._publish("status", st=e.state, m=e.mode, rssi=-55, heap=182000, bat=self.values["bat"],
                      rtt=rtt, crtt=crtt, fw=FIRMWARE_VERSION)

    @staticmethod
    def _median(samples: deque, t: int) -> float | None:
        recent = sorted(v for ts, v in samples if t - ts <= 10000)
        if not recent:
            return None
        return round(min(recent[len(recent) // 2], 60000.0), 1)

    def _publish(self, name: str, fields: dict | None = None, **kw) -> dict | None:
        """Build (v/seq/up), validate and publish. Invalid: raise in strict mode (tests), else log and drop."""
        try:
            msg = self.outbox.build(name, self.up, **(fields or {}), **kw)
        except ContractError:
            if getattr(self.link, "strict", True):
                raise
            log.exception("NOT publishing invalid %s", name)
            self.link.invalid_out += 1
            return None
        self.link.publish(name, msg)
        return msg

    def _check_reached(self, t: int) -> None:
        if self.m.reached or not self.booted():
            return
        s = self.target()
        if math.hypot(self.robot.pose.x - s["x"], self.robot.pose.y - s["y"]) <= self.sc.success_radius_m:
            self.m.reached = True
            self.m.time_to_source_s = round(self.trial_time_s(), 2)

    # ------------------------------------------------------------------ results

    def lost_messages(self) -> int:
        """Gaps in incoming command streams + pings older than 2 s that never got a pong."""
        stale_pings = sum(1 for t0, _ in self.pings.values() if self.t_ms - t0 > 2000)
        return self.edge.streams.lost + stale_pings

    def results(self) -> dict:
        m = self.m
        rt = not self.lockstep
        rtts = sorted(v for _, v in m.rtts)
        crtts = sorted(v for _, v in m.crtts)
        return {
            "reached": m.reached,
            "time_to_source_s": m.time_to_source_s,
            "path_length_m": round(self.robot.path_length, 3),
            "collisions": self.robot.collisions,
            "escapes": self.edge.stats["escapes"],
            "fallback_time_s": round(self.edge.stats["fallback_ms"] / 1000.0, 2),
            "messages_lost": self.lost_messages(),
            "motor_cmds": m.motor_cmds,
            # Latency only from real-time runs (contract section 7.1 (6)); empty in lockstep.
            "rtt_median_ms": (round(rtts[len(rtts) // 2], 1) if rtts and rt else None),
            "crtt_median_ms": (round(crtts[len(crtts) // 2], 1) if crtts and rt else None),
        }
