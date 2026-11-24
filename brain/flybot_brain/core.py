"""Brain core: the contract rules around the controller. No MQTT here.

handle(name, msg) takes one validated incoming message and returns the messages to publish,
poll() does the wall-clock checks (stale sensors, brain/status heartbeat), pong() answers a ping.
The wall clock is injected so every rule can be tested without a broker.

Contract rules implemented here:
  - one control tick per `sensors` message (7.1 (2)); cmd/motor with rs = that seq and ttl;
  - robot time from sensors.up via RobotClock: monotonic, reboot step 0, gaps capped (7.1 (3)(4));
  - per-sender seq tracking with reboot detection (6); duplicates and late sensors are not ticked;
  - silence instead of commands when stale (6 rule 11), paused (mode not auto), asleep or warming
    up (dec "warmup", sens stays ok: revision 5);
  - brain/state every 200 ms of robot time (and once a second wall time while no sensors arrive);
  - brain/status on change + every 10 s wall time, with sens ok / stale / none;
  - brain/sleep -> cmd/sleep why "user", repeated every 5 s of robot time while asleep;
  - brain/control: param, save, load, reset, state, lesion (5.10; hab is robot-side: ok false).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Callable

from flybot_contract import Contract, Outbox, RobotClock, StreamTracker

from .config import BrainConfig, set_param
from .controllers import Inputs, make_controller
from .controllers.base import trial_seed
from .persistence import Snapshots

log = logging.getLogger("flybot_brain")
Out = tuple[str, dict]          # (topic name, message)
TICK_BUDGET_MS = 50.0


def _clamp(v: float) -> float:
    return round(max(-1.0, min(1.0, v)), 2)


class Brain:
    def __init__(self, cfg: BrainConfig, contract: Contract, snapshots: Snapshots | None = None,
                 wall: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.contract = contract
        self.snapshots = snapshots
        self.wall = wall
        self.t0 = wall()
        self.lock = threading.Lock()            # the outbox is shared with the ping thread
        self.outbox = Outbox(contract)
        self.clock = RobotClock()
        self.streams = StreamTracker()
        self.ctrl = make_controller(cfg)

        self.mode = "auto"                      # until the retained cmd/mode says otherwise
        self.asleep = False
        self.sleep_why = "user"
        self.next_sleep_repeat = 0
        self.dark_ms = self.light_ms = 0                 # auto sleep (5.9): time spent dark / light
        self.sleep_armed = True                          # after a user wake: light must be seen first
        self.hun = cfg.states.hunger_start      # internal states (5.7): robot-time dynamics here,
        self.fear = 0.0                         # their effect on the network is circuit data

        self.last_sensors_wall: float | None = None
        self.sens = "none"
        self.dec = "stale"
        self.why: list[str] = ["no_sensors"]
        self.last_rs: int | None = None
        self.ack: dict | None = None
        self.backlog = 0                                 # inbound messages waiting (set by the link)
        self.backlog_high = False
        self.hab = 0.0                                   # robot's looming habituation, as last reported
        self.lesions: set[str] = set()                   # brain/state.les (5.10)

        self.next_state_ms = 0                  # robot time
        self.next_weights_ms = 0                # robot time
        self.weights_dirty = True               # publish brain/weights at start-up
        self.last_weights_changes = -1
        self.next_state_wall = 0.0
        self.status_dirty = True
        self.next_status_wall = 0.0
        self.tick_log: deque = deque()          # (wall, compute ms)
        self.counters = {"ticks": 0, "commands": 0, "dropped": 0, "reboots": 0, "gaps": 0, "resets": 0,
                         "invalid": 0, "rewards": 0, "weights_published": 0, "escapes": 0, "sleeps": 0}

    # ------------------------------------------------------------------ helpers

    @property
    def up(self) -> int:
        return int((self.wall() - self.t0) * 1000)

    def _out(self, name: str, **fields) -> Out:
        with self.lock:
            return name, self.outbox.build(name, self.up, **fields)

    def _now_ms(self) -> int:
        return int(self.wall() * 1000)

    # ------------------------------------------------------------------ inputs

    def handle(self, name: str, msg: dict, retained: bool = False) -> list[Out]:
        if name == "sensors":
            return self._tick(msg)
        if name == "ping":
            return [self.pong(msg)]
        if name == "cmd/mode":
            return self._on_mode(msg, retained)
        if name == "brain/sleep":
            return self._on_sleep_request(msg)
        if name == "brain/control":
            return self._on_control(msg)
        if name == "reward":
            return self._on_reward(msg)
        if name == "status":
            if msg.get("st") != "offline" and "hab" in msg:
                self.hab = float(msg["hab"])             # mirrored into brain/state.hab (revision 8)
            return []
        if name == "events":
            return self._on_event(msg)
        return []

    def pong(self, msg: dict) -> Out:
        return self._out("pong", ps=msg["seq"], pu=msg["up"])

    def _on_mode(self, msg: dict, retained: bool) -> list[Out]:
        key = ("cmd/mode", msg["src"])
        if retained:
            self.streams.remember(key, msg["seq"], msg["up"], self._now_ms())
        elif not self.streams.accept(key, msg["seq"], msg["up"], self._now_ms()):
            return []
        if msg["m"] != self.mode:
            log.info("robot mode %s -> %s", self.mode, msg["m"])
        self.mode = msg["m"]
        return []

    # ------------------------------------------------------------------ the control tick

    def _tick(self, msg: dict) -> list[Out]:
        t_start = time.perf_counter()
        now = self.wall()
        if not self.streams.accept(("sensors",), msg["seq"], msg["up"], int(now * 1000), bid=msg["bid"]):
            self.counters["dropped"] += 1
            return []
        restarted = self.streams.last_restarted
        t = self.clock.advance(msg["up"], msg["bid"])        # a bid change is a reboot (revision 6)
        if self.clock.rebooted or restarted:
            self._on_robot_reboot()
        self._advance_states(self.clock.step)
        if self.clock.gaps > self.counters["gaps"]:
            self.counters["gaps"] = self.clock.gaps          # recorded (7.1 (4)); step was capped
            log.info("sensor data gap: integrated only %d ms", self.clock.step)

        self.last_sensors_wall = now
        if self.sens != "ok":
            self.sens = "ok"
            self.status_dirty = True
        self.last_rs = msg["seq"]
        outs: list[Out] = self._light_sleep(msg["lt"], self.clock.step, t)
        if self.asleep:
            self.ctrl.sleep_tick(self.clock.step)           # consolidation (5.9)

        if msg["ol"] is None or msg["or"] is None:
            dec, why = "warmup", ["odour_warmup"]            # robot holds still in boot (section 5)
        elif self.mode != "auto":
            dec, why = "paused", [f"mode_{self.mode}"]
        elif self.asleep:
            dec, why = "sleep", [self.sleep_why]
            if t >= self.next_sleep_repeat:
                outs.append(self._out("cmd/sleep", s=True, why=self.sleep_why))
                self.next_sleep_repeat = t + self.cfg.timing.sleep_repeat_ms
        else:
            d = self.ctrl.step(Inputs(msg["ol"], msg["or"], msg["d"], msg["lt"], msg["bat"], self.clock.step, t,
                                      hun=self.hun, fear=self.fear))
            outs.append(self._out("cmd/motor", l=_clamp(d.l), r=_clamp(d.r), ttl=self.cfg.timing.ttl_ms,
                                  rs=msg["seq"]))
            self.counters["commands"] += 1
            dec, why = d.dec, d.why
        self.dec, self.why = dec, why[:3]
        if dec == "eat":
            self.hun = 0.0                                   # eating at a source resets hunger

        if t >= self.next_state_ms:
            outs.append(self._state())
            self.next_state_ms = t + self.cfg.timing.state_every_ms
        outs += self._maybe_weights(t)
        self.counters["ticks"] += 1
        self.tick_log.append((now, (time.perf_counter() - t_start) * 1000.0))
        return outs

    def set_lesion(self, target: str, off: bool) -> bool:
        """Lesion switch (contract 3.12, plan 5.10). hun / fear hold that state at 0; hab is robot-side
        (no brain command reaches the reflex): False; everything else goes to the controller."""
        if target == "hab":
            log.info("lesion hab: habituation runs on the robot; not switchable from the brain")
            return False
        if target in ("hun", "fear"):
            ok = True
            if off:
                if target == "hun":
                    self.hun = 0.0
                else:
                    self.fear = 0.0
        else:
            ok = self.ctrl.lesion(target, off)
        if ok:
            (self.lesions.add if off else self.lesions.discard)(target)
            log.info("lesion %s %s", target, "off" if off else "restored")
        return ok

    def _advance_states(self, dt_ms: int) -> None:
        """Hunger rises and fear decays with robot time (5.7)."""
        if dt_ms <= 0:
            return
        if "hun" in self.lesions:
            self.hun = 0.0
        st = self.cfg.states
        if st.hunger_rise_s > 0 and "hun" not in self.lesions:
            self.hun = min(1.0, self.hun + dt_ms / (st.hunger_rise_s * 1000.0))
        if st.fear_tau_s > 0:
            self.fear *= math.exp(-dt_ms / (st.fear_tau_s * 1000.0))

    def _on_event(self, msg: dict) -> list[Out]:
        if not self.streams.accept(("events",), msg["seq"], msg["up"], self._now_ms(), bid=msg.get("bid")):
            return []                                    # duplicate or late
        if msg["e"] == "escape":
            self.hab = float(msg["hab"])
            if "fear" not in self.lesions:
                self.fear = min(1.0, self.fear + self.cfg.states.fear_jump * float(msg["resp"]))
            self.counters["escapes"] += 1
        return []

    def _on_robot_reboot(self) -> None:
        """Robot time stepped 0 and the seq stream restarted (7.1 (3)). State carries on unless
        experiment.reset_on_reboot is set (independent batch trials)."""
        self.counters["reboots"] += 1
        if self.cfg.experiment.reset_on_reboot:
            seed = trial_seed(self.cfg.seed, self.counters["reboots"])   # one noise sequence per trial
            self._reset_internal("all", seed=seed)
            log.info("robot rebooted (boot %d): internal state reset, seed %d (experiment.reset_on_reboot)",
                     self.counters["reboots"], seed)
        else:
            log.info("robot rebooted: internal state kept")

    def _reset_internal(self, what: str, seed: int | None = None) -> None:
        if what in ("state", "all"):
            self.ctrl.reset(seed)
            self.hun, self.fear = self.cfg.states.hunger_start, 0.0
        if what in ("weights", "all"):
            self.ctrl.reset_learning()
            self.weights_dirty = True
        self.counters["resets"] += 1

    # ------------------------------------------------------------------ reward and learned weights

    def _on_reward(self, msg: dict) -> list[Out]:
        key = ("reward", msg["src"])
        if not self.streams.accept(key, msg["seq"], msg["up"], self._now_ms(), bid=msg.get("bid")):
            return []                                        # duplicate (QoS 1 redelivery) or late
        self.counters["rewards"] += 1
        self.ctrl.on_reward(msg["r"], self.clock.ms)
        log.info("%s from %s", "reward" if msg["r"] > 0 else "punishment", msg["src"])
        return []

    def _maybe_weights(self, t: int) -> list[Out]:
        """brain/weights (contract 3.11): retained, at start-up, after load/reset and when learning
        changed the weights, at most once per weights_every_ms of robot time."""
        if t < self.next_weights_ms:
            return []
        changes = self.ctrl.learning_changes()
        if not self.weights_dirty and changes == self.last_weights_changes:
            return []
        w = self.ctrl.weights_summary()
        if w is None:
            return []
        if not (len(w["ap"]) == len(w["av"]) == w["n"]):     # known issue: the schema cannot check this
            log.error("brain/weights not published: ap/av lengths %d/%d != n %d", len(w["ap"]), len(w["av"]), w["n"])
            return []
        self.weights_dirty = False
        self.last_weights_changes = changes
        self.next_weights_ms = t + self.cfg.timing.weights_every_ms
        self.counters["weights_published"] += 1
        return [self._out("brain/weights", **w)]

    # ------------------------------------------------------------------ periodic (wall clock)

    def poll(self) -> list[Out]:
        now = self.wall()
        outs: list[Out] = []
        high = self.backlog > self.cfg.timing.backlog_warn          # contract 3.9 lag (revision 9)
        if high != self.backlog_high:
            if high:
                log.warning("falling behind: %d inbound messages waiting (threshold %d)", self.backlog,
                            self.cfg.timing.backlog_warn)
            else:
                log.info("caught up: %d inbound messages waiting", self.backlog)
            self.backlog_high = high
            self.status_dirty = True
        if self.last_sensors_wall is None:
            sens = "none"
        elif now - self.last_sensors_wall > self.cfg.timing.stale_ms / 1000.0:
            sens = "stale"
        else:
            sens = "ok"
        if sens != self.sens:
            log.info("sensor input %s -> %s", self.sens, sens)
            self.sens = sens
            self.status_dirty = True
        if sens != "ok":
            self.dec, self.why = "stale", ["no_sensors"]
            if now >= self.next_state_wall:       # keep the dashboard informed while silent
                outs.append(self._state())
                self.next_state_wall = now + 1.0
        if self.status_dirty or now >= self.next_status_wall:
            outs.append(self._status(now))
            self.status_dirty = False
            self.next_status_wall = now + self.cfg.timing.status_every_s
        return outs

    def _status(self, now: float) -> Out:
        while self.tick_log and now - self.tick_log[0][0] > 10.0:
            self.tick_log.popleft()
        times = [c for _, c in self.tick_log]
        tick = round(min(sum(times) / len(times), 10000.0), 1) if times else 0.0
        ovr = min(sum(1 for c in times if c > TICK_BUDGET_MS), 1000)
        return self._out("brain/status", st="online", ctrl=self.cfg.controller, tick=tick, ovr=ovr, sens=self.sens,
                         lag=min(self.backlog, 100000))

    def _state(self) -> Out:
        return self._out("brain/state", hun=round(self.hun, 2), fear=round(self.fear, 2),
                         hab=round(self.hab, 2), slp=self.asleep,
                         dec=self.dec, why=list(self.why[:3]), spk=self.ctrl.spikes(), les=sorted(self.lesions),
                         pref=self.ctrl.pref(),
                         rs=self.last_rs, ack=self.ack)

    # ------------------------------------------------------------------ sleep requests

    def _light_sleep(self, lt: float | None, dt_ms: int, t: int) -> list[Out]:
        """Sleep after sustained darkness, wake after sustained light (5.9), with hysteresis."""
        sc = self.cfg.sleep
        if not sc.auto or lt is None:
            self.dark_ms = self.light_ms = 0
            return []
        if lt > sc.light_above:
            self.sleep_armed = True
        if not self.asleep:
            self.dark_ms = self.dark_ms + dt_ms if (lt < sc.dark_below and self.sleep_armed) else 0
            if self.dark_ms >= sc.dark_for_s * 1000:
                self.asleep, self.sleep_why, self.dark_ms = True, "dark", 0
                self.next_sleep_repeat = t + self.cfg.timing.sleep_repeat_ms
                self.counters["sleeps"] += 1
                log.info("dark for %.0f s: sleep", sc.dark_for_s)
                return [self._out("cmd/sleep", s=True, why="dark")]
        elif self.sleep_why == "dark":
            self.light_ms = self.light_ms + dt_ms if lt > sc.light_above else 0
            if self.light_ms >= sc.light_for_s * 1000:
                self.asleep, self.light_ms = False, 0
                log.info("light for %.0f s: wake", sc.light_for_s)
                return [self._out("cmd/sleep", s=False, why="light")]
        return []

    def _on_sleep_request(self, msg: dict) -> list[Out]:
        if not self.streams.accept(("brain/sleep", msg["src"]), msg["seq"], msg["up"], self._now_ms()):
            return []
        self.sleep_why = "user"
        if msg["s"]:
            self.asleep = True
            self.next_sleep_repeat = self.clock.ms + self.cfg.timing.sleep_repeat_ms
        else:
            self.asleep = False
            self.sleep_armed = False                     # stay awake until the light has been seen
            self.dark_ms = 0
        log.info("sleep request from %s: %s", msg["src"], "sleep" if msg["s"] else "wake")
        return [self._out("cmd/sleep", s=msg["s"], why="user")]

    # ------------------------------------------------------------------ control

    def _on_control(self, msg: dict) -> list[Out]:
        if not self.streams.accept(("brain/control", msg["src"]), msg["seq"], msg["up"], self._now_ms()):
            return []
        c, ok = msg["c"], False
        try:
            if c == "param":
                set_param(self.cfg, msg["k"], msg["val"])
                if msg["k"] == "ctrl":
                    self.ctrl = make_controller(self.cfg)
                    self.status_dirty = True
                    self.weights_dirty = True
                ok = True
            elif c == "save":
                ok = self.save(msg.get("name", "latest"))
            elif c == "load":
                ok = self.load(msg.get("name", "latest"))
            elif c == "reset":
                self._reset_internal(msg["what"])
                ok = True
            elif c == "state":
                if ("hun" in msg and "hun" in self.lesions) or ("fear" in msg and "fear" in self.lesions):
                    raise ValueError("that state is lesioned")
                if "hun" in msg:
                    self.hun = min(max(float(msg["hun"]), 0.0), 1.0)
                if "fear" in msg:
                    self.fear = min(max(float(msg["fear"]), 0.0), 1.0)
                ok = True
            elif c == "lesion":
                ok = self.set_lesion(msg["tgt"], msg["off"])
        except (KeyError, TypeError, ValueError) as e:
            log.warning("brain/control %s rejected: %s", c, e)
        log.info("brain/control %s from %s: %s", c, msg["src"], "ok" if ok else "failed")
        self.ack = {"src": msg["src"], "seq": msg["seq"], "ok": ok}
        return [self._state()]

    # ------------------------------------------------------------------ snapshots

    def snapshot(self) -> dict:
        """Everything the brain has learned or is in (5.11): learned weights with their labile and
        stable parts and preferences (controller state), hunger, fear, the robot's habituation as last
        reported, sleep state and active lesions. Format 1 snapshots (hunger, fear, weights) still load."""
        return {"format": 2, "controller": self.cfg.controller, "seed": self.cfg.seed, "robot_ms": self.clock.ms,
                "internal": {"hun": self.hun, "fear": self.fear, "hab": self.hab},
                "sleep": {"asleep": self.asleep, "why": self.sleep_why, "armed": self.sleep_armed},
                "lesions": sorted(self.lesions),
                "controller_state": self.ctrl.state_dict()}

    def restore(self, data: dict) -> None:
        internal = data["internal"]
        self.hun = float(internal["hun"])
        self.fear = float(internal["fear"])
        self.hab = float(internal.get("hab", 0.0))
        sl = data.get("sleep") or {}
        self.asleep = bool(sl.get("asleep", False))
        self.sleep_why = sl.get("why", "user") if sl.get("why") in ("dark", "light", "user") else "user"
        self.sleep_armed = bool(sl.get("armed", True))
        if self.asleep:
            self.next_sleep_repeat = 0                       # tell the robot again at the next tick
        if data.get("controller") == self.cfg.controller:
            self.ctrl.load_state(data.get("controller_state", {}))
            self.weights_dirty = True
        saved = [t for t in data.get("lesions", []) if isinstance(t, str)]
        for t in sorted(self.lesions - set(saved)):          # the snapshot's lesion set, nothing else
            self.set_lesion(t, False)
        if saved and self.cfg.persistence.restore_lesions:
            log.warning("restoring lesions from the snapshot: %s", ", ".join(saved))
            for t in saved:
                self.set_lesion(t, True)
        elif saved:
            log.info("snapshot had lesions %s; starting intact (persistence.restore_lesions is off)",
                     ", ".join(saved))
            for t in saved:
                self.set_lesion(t, False)
        self.status_dirty = True

    def save(self, name: str) -> bool:
        if self.snapshots is None:
            return False
        try:
            self.snapshots.save(name, self.snapshot())
            return True
        except OSError as e:
            log.error("snapshot save failed: %s", e)
            return False

    def load(self, name: str) -> bool:
        if self.snapshots is None or not self.snapshots.exists(name):
            return False
        try:
            self.restore(self.snapshots.load(name))
            return True
        except (OSError, KeyError, ValueError) as e:
            log.error("snapshot load failed: %s", e)
            return False
