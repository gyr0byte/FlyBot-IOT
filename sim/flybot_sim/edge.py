"""FIRMWARE EDGE LOGIC: the robot's own behaviour, independent of the cloud brain.

This module is the reference for the ESP32 firmware (plan steps 6.6-6.8) and must follow
docs/message-contract.md sections 5, 5.1, 6 and 7 exactly. Port it 1:1:
  - plain Python, no numpy / MQTT / threads; all times are integer milliseconds passed in;
  - every threshold comes from EdgeConfig (edge_config.py -> config.h);
  - randomness comes from an injected rand() -> [0, 1) (esp_random() on the ESP32).

Inputs (call as things happen):
  on_link(up, now)                         MQTT connected / lost
  on_mode(msg, now, retained)              cmd/mode   (already schema-validated)
  on_motor(msg, now)                       cmd/motor
  on_manual(msg, now)                      cmd/manual
  on_sleep(msg, now)                       cmd/sleep
  on_invalid(topic)                        a message on topic failed validation
  on_distance(raw_cm, filtered_cm, now)    every ultrasonic reading (raw may be None)
  on_battery(volts, now)                   smoothed pack voltage
  set_odour_ready(ready)                   odour warm-up / calibration finished
Output:
  update(now) -> (left, right)             call every control step (e.g. every 5-20 ms)
  pop_events() -> list of event dicts      to publish on flybot/01/events (without v/seq/up)
  state, mode, status_dirty                for flybot/01/status
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from flybot_contract import StreamTracker  # contract section 6 rules (shared with the brain)

from .edge_config import EdgeConfig

# Robot states (contract status.st) and modes (contract cmd/mode.m).
BOOT, CLOUD, MANUAL, ESCAPE, FALLBACK, SLEEP, ESTOP = (
    "boot", "cloud", "manual", "escape", "fallback", "sleep", "estop")
MODE_AUTO, MODE_MANUAL, MODE_ESTOP = "auto", "manual", "estop"


class MotorMixer:
    """Command -> motor output: dead zone, duty cap, ramp (plan step 6.3)."""

    def __init__(self, cfg: EdgeConfig):
        self.cfg = cfg
        self.l = 0.0
        self.r = 0.0
        self.last_ms: int | None = None

    def _shape(self, v: float) -> float:
        v = max(-1.0, min(1.0, v))
        if abs(v) < self.cfg.motor_dead_zone:
            return 0.0
        return max(-self.cfg.motor_duty_cap, min(self.cfg.motor_duty_cap, v))

    def apply(self, l: float, r: float, now: int, immediate: bool = False) -> tuple[float, float]:
        tl, tr = self._shape(l), self._shape(r)
        dt = 0 if self.last_ms is None else max(0, now - self.last_ms)
        self.last_ms = now
        if immediate:
            self.l, self.r = tl, tr
        else:
            step = self.cfg.motor_ramp_per_s * dt / 1000.0
            self.l += max(-step, min(step, tl - self.l))
            self.r += max(-step, min(step, tr - self.r))
        return self.l, self.r


@dataclass
class Command:
    l: float
    r: float
    expires_ms: int


class Edge:
    def __init__(self, cfg: EdgeConfig, rand: Callable[[], float]):
        self.cfg = cfg
        self.rand = rand
        self.state = BOOT
        self.mode = MODE_AUTO          # assumed until the retained cmd/mode arrives (section 5.1)
        self.mode_known = False
        self.status_dirty = True
        self.events: list[dict] = []

        self.connected = False
        self.connected_at: int | None = None
        self.boot_t0: int | None = None
        self.odour_ready = False

        self.streams = StreamTracker(cfg.stream_idle_forget_ms)
        self.mixer = MotorMixer(cfg)
        self.out = (0.0, 0.0)

        self.motor_cmd: Command | None = None
        self.manual_cmd: Command | None = None
        self.last_valid_motor: int | None = None
        self.last_valid_manual: int | None = None
        self.watchdog_ref = 0          # watchdog counts from max(this, last valid command)

        self.good_count = 0            # section 5.1 (4): consecutive valid cmd/motor in fallback
        self.last_good_ms: int | None = None
        self.last_good_ttl = 0
        self.fallback_since: int | None = None

        self.last_sleep_true = 0

        self.escape_return: str | None = None
        self.escape_reverse_until = 0
        self.escape_turn_until = 0
        self.escape_turn_dir = 1.0
        self.escape_end_ms = -10 ** 9
        self.escape_pending: dict | None = None   # escape event waiting for its measured lat
        self.loom_hits = 0
        self.hab = (0.0, 0)                          # habituation: (level, at robot ms)
        self.hab_ref: float | None = None            # running average speed of recent stimuli, cm/s
        self.approach: list[tuple[int, float, float]] = []   # current approach run: (t, cm, own cm/s)
        self.escape_speed_now = 0.0
        self.prev_reading: tuple[int, float] | None = None
        self.last_turn_ms = -10 ** 9

        self.dist_cm: float | None = None         # filtered distance
        self.obstacle_blocking = False

        self.wander = (cfg.fallback_speed, cfg.fallback_speed)
        self.next_wander_ms = 0
        self.turning_until = 0
        self.turn_started = 0
        self.turn_dir = 1.0

        self.battery_low_sent = False
        self.battery_cut = False

        self.last_update: int | None = None
        self.stats = {"escapes": 0, "fallback_ms": 0, "obstacle_stops": 0, "looms": 0, "habituated": 0}

    # ------------------------------------------------------------------ helpers

    def _set_state(self, new: str) -> None:
        if new != self.state:
            self.state = new
            self.status_dirty = True

    def _emit(self, **event) -> None:
        self.events.append(event)

    def pop_events(self) -> list[dict]:
        ev, self.events = self.events, []
        return ev

    def in_fallback(self) -> bool:
        return self.state == FALLBACK or (self.state == ESCAPE and self.escape_return == FALLBACK)

    def _enter_fallback(self, why: str, now: int) -> None:
        if self.in_fallback():
            return
        self._set_state(FALLBACK)
        self.fallback_since = now
        self.good_count = 0
        self.last_good_ms = None
        self.next_wander_ms = now
        self.turning_until = 0
        self._emit(e="fallback_on", why=why)

    def _leave_fallback(self, to: str, now: int) -> None:
        if not self.in_fallback():
            return
        self._emit(e="fallback_off", dur=max(0, now - (self.fallback_since or now)))
        self.fallback_since = None
        if self.state == ESCAPE:
            self.escape_return = to
        else:
            self._set_state(to)

    def _enter_mode_state(self, now: int) -> None:
        """Go to the state that belongs to the current mode (also used at the end of boot)."""
        self.escape_return = None
        if self.mode == MODE_ESTOP:
            self._leave_fallback(ESTOP, now)
            self._set_state(ESTOP)
        elif self.mode == MODE_MANUAL:
            self._leave_fallback(MANUAL, now)
            self._set_state(MANUAL)
        elif not self.connected:
            self._set_state(CLOUD)
            self._enter_fallback("mqtt_lost", now)
        else:
            self._leave_fallback(CLOUD, now)
            self._set_state(CLOUD)
        self.watchdog_ref = now

    # ------------------------------------------------------------------ inputs

    def on_link(self, up: bool, now: int) -> None:
        self.connected = up
        if up and self.connected_at is None:
            self.connected_at = now
        if not up and self.state == CLOUD:
            self._enter_fallback("mqtt_lost", now)

    def set_odour_ready(self, ready: bool) -> None:
        self.odour_ready = ready

    def on_mode(self, msg: dict, now: int, retained: bool = False) -> None:
        key = ("cmd/mode", msg["src"])
        if retained:  # rule 10: retained = current state; (re)start the stream, never drop it
            self.streams.remember(key, msg["seq"], msg["up"], now)
        elif not self.streams.accept(key, msg["seq"], msg["up"], now):
            return
        new = msg["m"]
        changed = new != self.mode
        self.mode = new
        self.mode_known = True
        if self.state == BOOT:
            self.status_dirty = True
            return
        if changed:
            self._enter_mode_state(now)
            self.status_dirty = True

    def on_motor(self, msg: dict, now: int) -> None:
        if not self.streams.accept(("cmd/motor",), msg["seq"], msg["up"], now):
            return
        self.last_valid_motor = now
        self.motor_cmd = Command(msg["l"], msg["r"], now + msg["ttl"])
        if self.in_fallback() and self.mode == MODE_AUTO:
            # Section 5.1 (4): 3 consecutive valid commands, each within the previous one's ttl.
            if self.last_good_ms is not None and now - self.last_good_ms <= self.last_good_ttl:
                self.good_count += 1
            else:
                self.good_count = 1
            self.last_good_ms, self.last_good_ttl = now, msg["ttl"]
            if self.good_count >= self.cfg.leave_fallback_good:
                self._leave_fallback(CLOUD, now)
                self.watchdog_ref = now

    def on_manual(self, msg: dict, now: int) -> None:
        if not self.streams.accept(("cmd/manual", msg["src"]), msg["seq"], msg["up"], now):
            return
        self.last_valid_manual = now
        self.manual_cmd = Command(msg["l"], msg["r"], now + msg["ttl"])

    def on_sleep(self, msg: dict, now: int) -> None:
        if not self.streams.accept(("cmd/sleep",), msg["seq"], msg["up"], now):
            return
        if self.mode != MODE_AUTO or self.state in (BOOT, ESTOP, MANUAL):
            return                                     # ignored in estop / manual (section 3.7)
        if msg["s"]:
            self.last_sleep_true = now
            if self.state != SLEEP:
                self._leave_fallback(SLEEP, now)
                self.escape_return = None
                self._set_state(SLEEP)
        elif self.state == SLEEP:
            self._set_state(CLOUD)
            self.watchdog_ref = now

    def on_invalid(self, topic: str) -> None:
        if topic == "cmd/motor":
            self.good_count = 0                        # section 5.1 (4): an invalid message resets

    def on_distance(self, raw_cm: float | None, filtered_cm: float | None, now: int) -> None:
        self.dist_cm = filtered_cm
        reflexes = self.state in (CLOUD, MANUAL, FALLBACK) and not self.battery_cut
        cfg = self.cfg
        if abs(self.out[0] - self.out[1]) > cfg.loom_max_turn:
            self.last_turn_ms = now
        turning = now - self.last_turn_ms < cfg.loom_turn_holdoff_ms
        if raw_cm is None or self.prev_reading is None or turning:
            # While we turn, the cone sweeps across walls and the range shrinks fast: that is our
            # own rotation, not something approaching (a fly suppresses looming during saccades too).
            self.loom_hits = 0
            self.approach = []
        else:
            pt, pd = self.prev_reading
            dt = now - pt
            if dt > 0:
                closing = (pd - raw_cm) * 1000.0 / dt                 # cm/s, positive = getting closer
                own = cfg.ego_speed_cm_s * (self.out[0] + self.out[1]) / 2.0
                obj = closing - own                                   # the object's own approach speed
                if obj > cfg.loom_max_speed_cm_s:
                    ttc = None
                    self.loom_hits = 0                                # a jump: something appeared
                elif raw_cm < cfg.loom_range_cm and obj > cfg.loom_min_speed_cm_s:
                    ttc = raw_cm / obj * 1000.0
                    self.loom_hits = self.loom_hits + 1 if ttc < cfg.loom_ttc_ms else 0
                    if not self.approach:
                        self.approach.append((pt, pd, own))
                    self.approach = (self.approach + [(now, raw_cm, own)])[-8:]
                else:
                    ttc = None
                    self.loom_hits = 0
                    self.approach = []
                if ttc is None:
                    self.approach = []
                if (reflexes and self.loom_hits >= cfg.loom_agree
                        and now - self.escape_end_ms >= cfg.escape_cooldown_ms):
                    self._on_loom(raw_cm, ttc, self._approach_speed(obj), now)
        self.prev_reading = (now, raw_cm) if raw_cm is not None else None

    def on_battery(self, volts: float, now: int) -> None:
        if volts < self.cfg.battery_cutoff_v and not self.battery_cut:
            self.battery_cut = True
            self._emit(e="low_battery", bat=round(volts, 2), cut=True)
        elif volts < self.cfg.battery_low_v and not self.battery_low_sent:
            self.battery_low_sent = True
            self._emit(e="low_battery", bat=round(volts, 2), cut=False)

    # ------------------------------------------------------------------ escape

    # Habituation (plan step 5.8): one level h (0 = full response) that rises with every looming
    # stimulus and recovers with rest. A stimulus clearly faster than the recent ones (by
    # hab_dishab_ratio) gets the full response and resets h: dishabituation. (Per-speed-class levels
    # were tried first; the ultrasonic speed estimate is too noisy to classify stimuli reliably.)

    def _approach_speed(self, last_estimate: float) -> float:
        """The object's approach speed over the whole approach run (up to 8 readings), for
        classifying the stimulus: one pair of readings 50 ms apart is too noisy (a 0.7 m/s object
        reads 43-124 cm/s that way)."""
        if len(self.approach) < 3:
            return last_estimate
        (t0, d0, _), (t1, d1, _) = self.approach[0], self.approach[-1]
        own = sum(o for _, _, o in self.approach) / len(self.approach)
        return (d0 - d1) * 1000.0 / (t1 - t0) - own if t1 > t0 else last_estimate

    def hab_level(self, now: int | None = None) -> float:
        """Current looming habituation level (contract 3.3 status.hab)."""
        now = (self.last_update or 0) if now is None else now
        level, t = self.hab
        return level * math.exp(-max(0, now - t) / self.cfg.hab_tau_ms) if level else 0.0

    def _on_loom(self, d_cm: float, ttc: float, obj_cm_s: float, now: int) -> None:
        """A looming stimulus was detected: respond according to habituation, then habituate."""
        cfg = self.cfg
        h = self.hab_level(now) if cfg.hab_enabled else 0.0
        dishab = (cfg.hab_enabled and h >= cfg.hab_dishab_min and self.hab_ref is not None
                  and obj_cm_s >= cfg.hab_dishab_ratio * self.hab_ref)
        resp = 1.0 if dishab else round(1.0 - h, 2)              # reported exactly as used (2 dp)
        if resp < cfg.hab_min_resp:
            resp = 0.0
        if cfg.hab_enabled:
            if dishab:
                self.hab, self.hab_ref = (0.0, now), obj_cm_s     # dishabituation: start afresh
            else:
                self.hab = (h + cfg.hab_step * (1.0 - h), now)
                self.hab_ref = obj_cm_s if self.hab_ref is None else (
                    self.hab_ref + cfg.hab_ref_alpha * (obj_cm_s - self.hab_ref))
        self.stats["looms"] += 1
        self.loom_hits = 0
        ev = {"e": "escape", "ttc": int(min(max(round(ttc), 0), 10000)), "d": round(min(max(d_cm, 2.0), 400.0), 1),
              "resp": resp, "hab": round(min(max(h, 0.0), 1.0), 2)}
        if resp > 0:
            self._start_escape(ev, resp, now)
        else:
            self.stats["habituated"] += 1
            self.escape_end_ms = now                              # cooldown: one stimulus counts once
            self._emit(**ev)                                      # seen, not acted on (no lat)

    def _start_escape(self, ev: dict, resp: float, now: int) -> None:
        cfg = self.cfg
        self.escape_return = self.state
        self._set_state(ESCAPE)
        self.escape_reverse_until = now + int(round(cfg.escape_reverse_ms * resp))
        self.escape_turn_until = self.escape_reverse_until + int(round(cfg.escape_turn_ms * resp))
        self.escape_speed_now = cfg.escape_speed * resp
        self.escape_turn_dir = 1.0 if self.rand() < 0.5 else -1.0
        self.stats["escapes"] += 1
        self.escape_pending = {**ev, "detected_ms": now}

    # ------------------------------------------------------------------ main step

    def update(self, now: int) -> tuple[float, float]:
        cfg = self.cfg
        dt = 0 if self.last_update is None else now - self.last_update
        self.last_update = now
        if self.in_fallback():
            self.stats["fallback_ms"] += dt

        # Boot: wait for odour warm-up and the retained mode (or 2 s after connecting).
        if self.state == BOOT:
            if self.boot_t0 is None:
                self.boot_t0 = now
            mode_decided = self.mode_known or (
                self.connected_at is not None and now - self.connected_at >= cfg.boot_mode_wait_ms)
            timed_out = now - self.boot_t0 >= cfg.boot_link_timeout_ms
            if self.odour_ready and (mode_decided or timed_out):
                self._enter_mode_state(now)

        if self.state == SLEEP and now - self.last_sleep_true > cfg.sleep_timeout_ms:
            self._set_state(CLOUD)                    # brain gone while we slept
            self.watchdog_ref = now

        if self.state == CLOUD:
            ref = max(self.watchdog_ref, self.last_valid_motor or 0)
            if now - ref > cfg.watchdog_ms:
                self._enter_fallback("cmd_timeout", now)

        immediate = False
        if self.state == ESCAPE:
            immediate = True
            if now < self.escape_reverse_until:
                target = (-self.escape_speed_now, -self.escape_speed_now)
            elif now < self.escape_turn_until:
                s = self.escape_speed_now * self.escape_turn_dir
                target = (-s, s)
            else:
                self.escape_end_ms = now
                ret = self.escape_return or CLOUD
                self.escape_return = None
                self._set_state(ret)
                if ret == CLOUD:
                    self.watchdog_ref = max(self.watchdog_ref, now - cfg.watchdog_ms // 2)
                target = (0.0, 0.0)
        if self.state != ESCAPE:
            target = self._target(now)

        if self.state in (ESTOP, BOOT, SLEEP) or self.battery_cut:
            target, immediate = (0.0, 0.0), True
        self.out = self.mixer.apply(target[0], target[1], now, immediate)

        if self.escape_pending is not None and self.state == ESCAPE:
            ev = self.escape_pending
            self.escape_pending = None
            self._emit(e="escape", ttc=ev["ttc"], d=ev["d"], lat=round(float(now - ev["detected_ms"]), 1),
                       resp=ev["resp"], hab=ev["hab"])
        return self.out

    def _target(self, now: int) -> tuple[float, float]:
        if self.state == CLOUD:
            return self._obstacle_stop(self._live(self.motor_cmd, now))
        if self.state == MANUAL:
            return self._obstacle_stop(self._live(self.manual_cmd, now))   # section 5.1 (2)
        if self.state == FALLBACK:
            return self._wander(now)
        return (0.0, 0.0)

    @staticmethod
    def _live(cmd: Command | None, now: int) -> tuple[float, float]:
        if cmd is None or now >= cmd.expires_ms:      # time-to-live expired: stop
            return (0.0, 0.0)
        return (cmd.l, cmd.r)

    def _obstacle_stop(self, lr: tuple[float, float]) -> tuple[float, float]:
        l, r = lr
        fwd, turn = (l + r) / 2.0, (l - r) / 2.0
        blocking = self.dist_cm is not None and self.dist_cm < self.cfg.obstacle_stop_cm and fwd > 0
        if blocking and not self.obstacle_blocking:
            self.stats["obstacle_stops"] += 1
            self._emit(e="obstacle", d=round(max(self.dist_cm, 2.0), 1))
        self.obstacle_blocking = blocking
        return (turn, -turn) if blocking else (l, r)

    def _wander(self, now: int) -> tuple[float, float]:
        """Fallback: slow wandering with obstacle avoidance on local sensors only (plan 6.8)."""
        cfg = self.cfg
        d = self.dist_cm
        if now < self.turning_until:
            clear = d is None or d >= cfg.fallback_clear_cm
            if clear and now - self.turn_started >= cfg.fallback_turn_min_ms:
                self.turning_until = now                       # way is clear: stop turning early
            else:
                s = cfg.fallback_turn_speed * self.turn_dir
                return (-s, s)
        if d is not None and d < cfg.fallback_turn_cm:
            self.turn_dir = 1.0 if self.rand() < 0.5 else -1.0
            span = cfg.fallback_turn_max_ms - cfg.fallback_turn_min_ms
            self.turn_started = now
            self.turning_until = now + cfg.fallback_turn_min_ms + int(self.rand() * span)
            s = cfg.fallback_turn_speed * self.turn_dir
            return (-s, s)
        if now >= self.next_wander_ms:
            c = (self.rand() * 2.0 - 1.0) * cfg.fallback_wander_curve
            self.wander = (cfg.fallback_speed - c, cfg.fallback_speed + c)
            self.next_wander_ms = now + cfg.fallback_wander_every_ms
        return self.wander
