"""The logger (plan step 8.2): every FlyBot message -> validated -> SQLite, in small batches.

Feed it raw MQTT messages with `tap(name, payload, retained)` (from any thread: it only queues). Its
own thread decodes and validates each message with flybot_contract, tracks sequence numbers per
sender (contract section 6: lost = gaps, duplicates and late ones counted), and writes rows in
batches (every `batch_s` seconds or `batch_rows` rows). Invalid messages are counted, not stored.
"""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

from flybot_contract import Contract, ContractError, StreamTracker

from . import db

log = logging.getLogger("flybot_brain.logger")
ROBOT = {"sensors", "events", "status", "ping"}


def _sender(name: str, msg: dict) -> str:
    """Who sent it: robot topics by the robot, brain topics by the brain, controls by their src."""
    if name in ROBOT or name == "reward" and msg.get("src") == "btn":
        return "robot"
    if name.startswith("brain/") and name not in ("brain/control", "brain/sleep") or name in ("cmd/motor", "cmd/sleep", "pong"):
        return "brain"
    return str(msg.get("src", "unknown"))


class Logger:
    def __init__(self, contract: Contract, db_path: str | Path, batch_s: float = 0.5, batch_rows: int = 200,
                 clock=time.time):
        self.contract = contract
        self.db_path = Path(db_path)
        self.batch_s, self.batch_rows = batch_s, batch_rows
        self.clock = clock
        self.q: queue.Queue = queue.Queue()
        self.con: sqlite3.Connection | None = None
        self.streams = StreamTracker()
        self.counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # recv, invalid, lost, dup
        self.thread: threading.Thread | None = None
        self.running = False
        self.written = 0

    # ------------------------------------------------------------------ feeding (any thread)

    def tap(self, name: str, payload: bytes, retained: bool = False) -> None:
        self.q.put((name, payload, retained, self.clock()))

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.con = db.connect(self.db_path)
        self.running = True
        self.thread = threading.Thread(target=self._run, name="logger", daemon=True)
        self.thread.start()
        log.info("logging to %s", self.db_path)

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(5.0)
        if self.con is not None:
            self.flush_now()
            self.con.close()
            self.con = None

    def _run(self) -> None:
        last = time.monotonic()
        pending = 0
        while self.running:
            try:
                item = self.q.get(timeout=0.1)
                self._handle(*item)
                pending += 1
            except queue.Empty:
                pass
            if pending and (pending >= self.batch_rows or time.monotonic() - last >= self.batch_s):
                self._commit()
                pending, last = 0, time.monotonic()

    def flush_now(self) -> None:
        """Process everything queued and commit (tests, shutdown)."""
        if self.con is None:
            self.con = db.connect(self.db_path)
        while True:
            try:
                self._handle(*self.q.get_nowait())
            except queue.Empty:
                break
        self._commit()

    def _commit(self) -> None:
        now = self.clock()
        self.con.executemany(
            "INSERT INTO reliability (topic, sender, received, invalid, lost, duplicates, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(topic, sender) DO UPDATE SET received=excluded.received, "
            "invalid=excluded.invalid, lost=excluded.lost, duplicates=excluded.duplicates, updated_ts=excluded.updated_ts",
            [(t, s, *c, now) for (t, s), c in self.counts.items()])
        self.con.commit()

    # ------------------------------------------------------------------ one message

    def _handle(self, name: str, payload: bytes, retained: bool, ts: float) -> None:
        if retained and not payload:
            return
        try:
            msg = self.contract.decode(name, payload)
        except ContractError:
            self.counts[(name, "?")][1] += 1
            return
        sender = _sender(name, msg)
        c = self.counts[(name, sender)]
        c[0] += 1
        offline = name in ("status", "brain/status") and msg.get("st") == "offline"
        if not retained and not offline:         # retained = current state; the offline last will is fixed
                                                 # at connect (seq 0, contract 3.3 / 3.9): not stream steps
            before = (self.streams.lost, self.streams.dropped)
            self.streams.accept((name, sender), msg["seq"], msg["up"], int(ts * 1000), bid=msg.get("bid"))
            c[2] += self.streams.lost - before[0]
            c[3] += self.streams.dropped - before[1]
        self._write(name, msg, sender, ts)
        self.written += 1

    def _write(self, name: str, m: dict, sender: str, ts: float) -> None:
        ins = self.con.execute
        up, seq, bid = m.get("up"), m.get("seq"), m.get("bid")
        rest = lambda *drop: json.dumps({k: v for k, v in m.items() if k not in ("v", "seq", "up", "bid") + drop})  # noqa: E731
        if name == "sensors":
            ins("INSERT INTO sensors (ts, up, bid, seq, ol, orr, d, lt, bat) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, up, bid, seq, m["ol"], m["or"], m["d"], m["lt"], m["bat"]))
        elif name == "events":
            ins("INSERT INTO events (ts, up, bid, seq, source, type, details) VALUES (?,?,?,?,?,?,?)",
                (ts, up, bid, seq, "robot", m["e"], rest("e")))
        elif name == "reward":
            ins("INSERT INTO events (ts, up, bid, seq, source, type, details) VALUES (?,?,?,?,?,?,?)",
                (ts, up, bid, seq, m["src"], "reward", json.dumps({"r": m["r"]})))
        elif name in ("cmd/motor", "cmd/manual"):
            ins("INSERT INTO commands (ts, up, seq, topic, source, l, r, ttl, rs, details) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, up, seq, name, "brain" if name == "cmd/motor" else f"manual:{m['src']}", m["l"], m["r"],
                 m["ttl"], m["rs"], None))
        elif name in ("cmd/mode", "cmd/sleep", "brain/control", "brain/sleep"):
            ins("INSERT INTO commands (ts, up, seq, topic, source, details) VALUES (?,?,?,?,?,?)",
                (ts, up, seq, name, sender, rest()))
        elif name == "brain/state":
            ins("INSERT INTO brain_state (ts, up, seq, hun, fear, hab, slp, dec, why, spk, les, pref, rs) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, up, seq, m["hun"], m["fear"], m["hab"], int(m["slp"]), m["dec"], json.dumps(m["why"]),
                 json.dumps(m["spk"]), json.dumps(m["les"]), json.dumps(m["pref"]), m["rs"]))
        elif name == "brain/weights":
            ins("INSERT INTO weights (ts, up, seq, n, sc, ap, av) VALUES (?,?,?,?,?,?,?)",
                (ts, up, seq, m["n"], m["sc"], json.dumps(m["ap"]), json.dumps(m["av"])))
        elif name in ("status", "brain/status"):
            src = "robot" if name == "status" else "brain"
            ins("INSERT INTO status (ts, up, seq, source, st, details) VALUES (?,?,?,?,?,?)",
                (ts, up, seq, src, m["st"], rest("st")))
            if m["st"] != "offline":
                if name == "status":
                    ins("INSERT INTO latency (ts, up, source, rtt_ms, crtt_ms) VALUES (?,?,?,?,?)",
                        (ts, up, "robot", m.get("rtt"), m.get("crtt")))
                else:
                    ins("INSERT INTO latency (ts, up, source, tick_ms, lag) VALUES (?,?,?,?,?)",
                        (ts, up, "brain", m.get("tick"), m.get("lag")))
        # ping / pong: counted for reliability only (round trips arrive via status.rtt)

    # ------------------------------------------------------------------ trials (evaluation tools)

    def add_trial(self, start_ts: float, end_ts: float | None, scenario: str, start_position: str,
                  controller: str, outcome: str, notes: str = "") -> None:
        con = self.con or db.connect(self.db_path)
        con.execute("INSERT INTO trials (start_ts, end_ts, scenario, start_position, controller, outcome, notes) "
                    "VALUES (?,?,?,?,?,?,?)", (start_ts, end_ts, scenario, start_position, controller, outcome, notes))
        con.commit()

    def reliability(self) -> dict:
        return {f"{t}|{s}": {"received": c[0], "invalid": c[1], "lost": c[2], "duplicates": c[3]}
                for (t, s), c in sorted(self.counts.items())}
