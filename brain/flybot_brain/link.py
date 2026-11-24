"""MQTT link for the brain program (user "brain", client id flybot-<id>-brain).

  - last will: retained brain/status "offline" (contract 3.9), keep-alive 10 s, auto-reconnect;
  - subscribes to flybot/<id>/# (the logger module will share this connection in Phase 8);
  - every incoming message is validated against the schemas; invalid ones are dropped and counted;
  - pings are answered at once in the network thread; everything else goes through one worker
    thread in arrival order, so control ticks never overlap;
  - every outgoing message was already validated by Outbox.build and is validated again on encode.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from flybot_contract import TOPIC_SCHEMA, Contract, ContractError, offline_status

from .config import MqttSettings
from .core import Brain, Out

log = logging.getLogger("flybot_brain.link")

QOS = {"cmd/motor": 0, "cmd/sleep": 1, "pong": 0, "brain/state": 0, "brain/status": 1, "brain/weights": 1}
RETAIN = {"brain/status", "brain/weights"}
OWN = set(QOS)                         # our own messages come back through the # subscription
POLL_S = 0.02


class BrainLink:
    def __init__(self, settings: MqttSettings, contract: Contract, brain: Brain):
        self.settings = settings
        self.contract = contract
        self.brain = brain
        self.inbox: queue.Queue = queue.Queue()
        self.connected = threading.Event()
        self.running = False
        self.worker: threading.Thread | None = None
        self.invalid_in = 0
        self.taps: list = []                # e.g. the logger: tap(name, payload, retained) for every message
        self.lwt = offline_status(contract, brain.up, "brain/status")
        c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"flybot-{settings.robot_id}-brain",
                        protocol=mqtt.MQTTv311, clean_session=True)
        c.username_pw_set(settings.user, settings.password)
        c.will_set(contract.topic("brain/status"), contract.encode("brain/status", self.lwt), qos=1, retain=True)
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self.client = c

    # ------------------------------------------------------------------ lifecycle

    def start(self, wait_s: float = 5.0) -> bool:
        self.running = True
        self.worker = threading.Thread(target=self._work, name="brain-worker", daemon=True)
        self.worker.start()
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=10)
        self.client.loop_start()
        return self.connected.wait(wait_s)

    def stop(self) -> None:
        self.running = False
        if self.worker:
            self.worker.join(2.0)
        if self.connected.is_set():
            info = self.client.publish(self.contract.topic("brain/status"),
                                       self.contract.encode("brain/status", self.lwt), qos=1, retain=True)
            info.wait_for_publish(2.0)
        self.client.disconnect()
        self.client.loop_stop()

    # ------------------------------------------------------------------ network thread

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("broker refused the brain: %s", reason_code)
            return
        client.subscribe(self.contract.prefix + "#", qos=1)
        self.brain.status_dirty = True          # announce "online" again after every (re)connect
        self.connected.set()
        log.info("connected to %s:%d as %s", self.settings.host, self.settings.port, self.settings.user)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected.is_set():
            log.warning("disconnected (%s); reconnecting", reason_code)
        self.connected.clear()

    def _on_message(self, client, userdata, m):
        name = self.contract.name_of(m.topic)
        if name is not None and name in TOPIC_SCHEMA:
            for tap in self.taps:           # the logger sees everything, our own messages included
                tap(name, m.payload, bool(m.retain))
        if name is None or name in OWN or name not in TOPIC_SCHEMA:
            return
        if m.retain and not m.payload:
            return                              # a cleared retained message
        try:
            msg = self.contract.decode(name, m.payload)
        except ContractError as e:
            self.invalid_in += 1
            self.brain.counters["invalid"] += 1
            log.warning("dropped invalid %s: %s", name, e)
            return
        if name == "ping":                      # answer immediately (contract 3.15)
            self._publish([self.brain.pong(msg)])
            return
        self.inbox.put((name, msg, bool(m.retain)))

    # ------------------------------------------------------------------ worker thread

    def _work(self) -> None:
        while self.running:
            self._work_once(POLL_S)

    def _work_once(self, timeout: float) -> None:
        """Handle one inbound message (if any) and do the periodic checks. The queue length is passed
        to the brain first, so it can report a backlog (contract 3.9 lag)."""
        self.brain.backlog = self.inbox.qsize()
        try:
            name, msg, retained = self.inbox.get(timeout=timeout)
            self._publish(self.brain.handle(name, msg, retained))
        except queue.Empty:
            pass
        except Exception:                       # never let one bad message kill the brain
            log.exception("error handling a message")
        self._publish(self.brain.poll())

    def _publish(self, outs: list[Out]) -> None:
        for name, msg in outs:
            try:
                payload = self.contract.encode(name, msg)
            except ContractError:
                log.exception("NOT publishing invalid %s", name)
                continue
            self.client.publish(self.contract.topic(name), payload, qos=QOS[name], retain=name in RETAIN)
