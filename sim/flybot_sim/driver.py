"""Test driver: a stand-in for the brain until it exists (tools/drive.py is the command line).

Connects with the BRAIN credentials (only the brain user may publish cmd/motor), so never run
it at the same time as the real brain. Like the real brain (contract section 7.1) it is
sensor-clocked: it answers every sensors message with one cmd/motor (rs = that seq), or with
nothing during a "silent" segment, and it measures its script in robot time taken from
sensors.up (monotonic; a reboot counts 0; gaps over 500 ms are capped at 100 ms). It also
answers every ping with a pong.

Script: comma-separated segments "<action>:<seconds>", e.g. "straight:3,turn:2,silent:2,stop:1".
Actions: straight, back, turn (spin left), right (spin right), arc, stop, silent.
"""
from __future__ import annotations

import logging
import threading
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .config import MqttSettings
from flybot_contract import Contract, ContractError, Outbox, RobotClock  # noqa: F401 (RobotClock re-exported)

log = logging.getLogger("flybot_sim.driver")

ACTIONS = {
    "straight": (0.5, 0.5), "back": (-0.4, -0.4), "turn": (-0.45, 0.45), "right": (0.45, -0.45),
    "arc": (0.3, 0.5), "stop": (0.0, 0.0), "silent": None,
}


def parse_script(text: str) -> list[tuple[str, float]]:
    segs = []
    for part in text.split(","):
        name, _, secs = part.strip().partition(":")
        if name not in ACTIONS:
            raise ValueError(f"unknown action {name!r}; use one of {sorted(ACTIONS)}")
        segs.append((name, float(secs or "1e9")))
    return segs


class Driver:
    def __init__(self, settings: MqttSettings, script: str, loop: bool = False, wait_boot: bool = True,
                 ttl: int = 300, contract: Contract | None = None):
        self.contract = contract or Contract(settings.robot_id)
        self.out = Outbox(self.contract)
        self.script = parse_script(script)
        self.loop = loop
        self.wait_boot = wait_boot
        self.ttl = ttl
        self.clock = RobotClock()
        self.started_at: int | None = None      # robot time when the script started
        self.segment = None
        self.sent = 0
        self.t0 = time.monotonic()
        self.done = threading.Event()
        c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"flybot-{settings.robot_id}-testdriver",
                        protocol=mqtt.MQTTv311, clean_session=True)
        c.username_pw_set(settings.user, settings.password)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        self.client = c
        self.settings = settings

    @property
    def up(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def start(self) -> None:
        self.client.connect(self.settings.host, self.settings.port, keepalive=10)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("broker refused the driver: %s", reason_code)
            return
        client.subscribe(self.contract.topic("sensors"), qos=0)
        client.subscribe(self.contract.topic("ping"), qos=0)

    def _publish(self, name: str, qos: int = 0, **fields) -> None:
        msg = self.out.build(name, self.up, **fields)
        self.client.publish(self.contract.topic(name), self.contract.encode(name, msg), qos=qos)

    def action_at(self, t_ms: int) -> str | None:
        """Script action at robot time t_ms after the start, or None when the script has ended."""
        total = sum(s for _, s in self.script)
        t = t_ms / 1000.0
        if self.loop and total > 0:
            t %= total
        for name, secs in self.script:
            if t < secs:
                return name
            t -= secs
        return None

    def _on_message(self, client, userdata, m):
        name = self.contract.name_of(m.topic)
        try:
            msg = self.contract.decode(name, m.payload)
        except ContractError as e:
            log.warning("ignoring invalid %s: %s", name, e)
            return
        if name == "ping":
            self._publish("pong", ps=msg["seq"], pu=msg["up"])
            return
        now = self.clock.advance(msg["up"])
        if self.started_at is None:
            if self.wait_boot and msg["ol"] is None:
                return                          # robot still warming up: don't start the script yet
            self.started_at = now
        action = self.action_at(now - self.started_at)
        if action != self.segment:
            log.info("robot time %.1f s: %s", now / 1000, action or "script finished (silent)")
            self.segment = action
        if action is None:
            self.done.set()
            return
        lr = ACTIONS[action]
        if lr is None:
            return                              # silent: let the robot's watchdog act
        self._publish("cmd/motor", l=lr[0], r=lr[1], ttl=self.ttl, rs=msg["seq"])
        self.sent += 1
