"""MQTT link for the simulated robot: exactly what the ESP32 does on the wire.

Connects as user "sim" (client id flybot-<id>-sim, keep-alive 5 s, clean session) with the
retained "offline" status as last will. Subscribes to cmd/motor, cmd/manual, cmd/mode, cmd/sleep
and pong. Incoming messages are validated against the schemas and handed to the simulation
thread through a queue; outgoing ones are validated before publishing.
"""
from __future__ import annotations

import logging
import queue
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .config import MqttSettings
from flybot_contract import Contract, ContractError, new_boot_id, offline_status

log = logging.getLogger("flybot_sim.link")

SUBSCRIPTIONS = {"cmd/motor": 0, "cmd/manual": 0, "cmd/mode": 1, "cmd/sleep": 1, "pong": 0}
QOS = {"sensors": 0, "events": 1, "status": 1, "reward": 1, "ping": 0}
RETAIN = {"status"}


class Link:
    """Real MQTT link. Inbox items: ("link", up) | ("msg", name, msg, retained) | ("invalid", name, error)."""

    def __init__(self, settings: MqttSettings, contract: Contract, up_at_connect: int = 0, strict: bool = False):
        self.contract = contract
        self.strict = strict                 # raise on an invalid outgoing message (tests)
        self.inbox: queue.Queue = queue.Queue()
        self.connected = False
        self.invalid_out = 0
        self.invalid_in = 0
        self.bid = new_boot_id()             # one link = one robot boot (contract section 2)
        self.lwt = offline_status(contract, up_at_connect, bid=self.bid)
        c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"flybot-{settings.robot_id}-sim",
                        protocol=mqtt.MQTTv311, clean_session=True)
        c.username_pw_set(settings.user, settings.password)
        c.will_set(contract.topic("status"), contract.encode("status", self.lwt), qos=1, retain=True)
        c.reconnect_delay_set(min_delay=1, max_delay=5)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self.client = c
        self.settings = settings

    # ------------------------------------------------------------------ lifecycle

    def start(self, wait_s: float = 5.0) -> bool:
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=5)
        self.client.loop_start()
        deadline = time.monotonic() + wait_s
        while not self.connected and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.connected

    def stop(self) -> None:
        """Clean shutdown: publish the offline status ourselves (section 3.3), then disconnect."""
        if self.connected:
            info = self.client.publish(self.contract.topic("status"),
                                       self.contract.encode("status", self.lwt), qos=1, retain=True)
            info.wait_for_publish(2.0)
        self.client.disconnect()
        self.client.loop_stop()

    # ------------------------------------------------------------------ paho callbacks (network thread)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("broker refused connection: %s", reason_code)
            return
        for name, qos in SUBSCRIPTIONS.items():
            client.subscribe(self.contract.topic(name), qos=qos)
        self.connected = True
        self.inbox.put(("link", True))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected:
            self.inbox.put(("link", False))
        self.connected = False

    def _on_message(self, client, userdata, m):
        name = self.contract.name_of(m.topic)
        if name not in SUBSCRIPTIONS:
            return
        if name == "cmd/mode" and m.retain and not m.payload:
            return                                          # retained mode was cleared
        try:
            self.inbox.put(("msg", name, self.contract.decode(name, m.payload), bool(m.retain)))
        except ContractError as e:
            self.invalid_in += 1
            log.warning("dropped invalid %s: %s", name, e)
            self.inbox.put(("invalid", name, str(e)))

    # ------------------------------------------------------------------ publishing (sim thread)

    def publish(self, name: str, msg: dict) -> None:
        try:
            payload = self.contract.encode(name, msg)
        except ContractError:
            self.invalid_out += 1
            if self.strict:
                raise
            log.exception("NOT publishing invalid %s", name)
            return
        self.client.publish(self.contract.topic(name), payload, qos=QOS[name], retain=name in RETAIN)


class FakeLink:
    """In-memory stand-in for tests and offline runs: records what would be published."""

    def __init__(self, contract: Contract, connected: bool = True):
        self.contract = contract
        self.inbox: queue.Queue = queue.Queue()
        self.connected = connected
        self.published: list[tuple[str, dict]] = []
        self.invalid_out = 0
        self.invalid_in = 0
        self.strict = True
        self.bid = new_boot_id()
        if connected:
            self.inbox.put(("link", True))

    def start(self, wait_s: float = 0) -> bool:
        return self.connected

    def stop(self) -> None:
        self.connected = False

    def publish(self, name: str, msg: dict) -> None:
        self.contract.encode(name, msg)          # validates; raises in tests
        self.published.append((name, msg))

    def send(self, name: str, msg: dict, retained: bool = False) -> None:
        """Inject an incoming message (as if from the broker), validated like the real link."""
        try:
            self.inbox.put(("msg", name, self.contract.decode(
                name, self.contract.encode(name, msg)), retained))
        except ContractError as e:
            self.invalid_in += 1
            self.inbox.put(("invalid", name, str(e)))
