"""Logger, export, retention and the read API (Phase 8)."""
from __future__ import annotations

import csv
import json
import time
import types

import pytest
from fastapi.testclient import TestClient

from flybot_brain import __main__ as cli
from flybot_brain.config import BrainConfig, MqttSettings
from flybot_brain.core import Brain
from flybot_brain.link import BrainLink
from flybot_brain.logger import db, tools
from flybot_brain.logger.api import create_app
from flybot_brain.logger.core import Logger
from flybot_contract import Contract

C = Contract()
BID = 2847193551
D = "dash-3fa2c1"


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def enc(name, msg):
    C.validate(name, msg)
    return C.encode(name, msg)


def sensors(seq, up, ol=0.2, lt=0.8):
    return {"v": 2, "seq": seq, "up": up, "bid": BID, "ol": ol, "or": 0.1, "d": 80.0, "lt": lt, "bat": 7.9}


def brain_state(seq, up, dec="seek", slp=False):
    return {"v": 5, "seq": seq, "up": up, "hun": 0.3, "fear": 0.1, "hab": 0.0, "slp": slp, "dec": dec,
            "why": ["odour_l"], "spk": {"pl": 3}, "les": [], "pref": {}, "rs": seq, "ack": None}


@pytest.fixture
def logger(tmp_path):
    clock = Clock()
    lg = Logger(C, tmp_path / "flybot.db", clock=clock)
    lg.clock_obj = clock
    yield lg
    lg.stop()


def rows(lg, sql):
    con = db.connect_readonly(lg.db_path)
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def feed_all(lg):
    clk = lg.clock_obj
    for i in range(5):
        clk.t += 0.05
        lg.tap("sensors", enc("sensors", sensors(i, 1000 + 50 * i)))
    lg.tap("events", enc("events", {"v": 3, "seq": 0, "up": 1100, "bid": BID, "e": "escape", "ttc": 400, "d": 30.0,
                                    "lat": 2.0, "resp": 1.0, "hab": 0.0}))
    lg.tap("reward", enc("reward", {"v": 3, "seq": 0, "up": 500, "r": -1, "src": D}))
    lg.tap("cmd/motor", enc("cmd/motor", {"v": 1, "seq": 0, "up": 1000, "l": 0.4, "r": 0.5, "ttl": 300, "rs": 0}))
    lg.tap("cmd/manual", enc("cmd/manual", {"v": 2, "seq": 0, "up": 600, "l": 0.5, "r": 0.5, "ttl": 300, "rs": 0,
                                            "src": D}))
    lg.tap("cmd/mode", enc("cmd/mode", {"v": 2, "seq": 0, "up": 700, "m": "manual", "src": D}), True)
    lg.tap("brain/state", enc("brain/state", brain_state(0, 1200)))
    lg.tap("brain/weights", enc("brain/weights", {"v": 1, "seq": 0, "up": 1200, "n": 2, "sc": 1.0, "ap": [255, 200],
                                                  "av": [255, 255]}))
    lg.tap("status", enc("status", {"v": 3, "seq": 0, "up": 1300, "bid": BID, "st": "cloud", "m": "auto", "rssi": -60,
                                    "heap": 1, "bat": 7.9, "rtt": 12.5, "crtt": 20.0, "fw": "0.1.0", "hab": 0.0}))
    lg.tap("brain/status", enc("brain/status", {"v": 3, "seq": 0, "up": 1300, "st": "online", "ctrl": "neural",
                                                "tick": 5.1, "ovr": 0, "sens": "ok", "lag": 2}))
    lg.flush_now()


# ----------------------------------------------------------------------------- writing

def test_every_message_type_lands_in_its_table(logger):
    feed_all(logger)
    assert len(rows(logger, "SELECT * FROM sensors")) == 5
    ev = rows(logger, "SELECT source, type, details FROM events ORDER BY id")
    assert [(e["source"], e["type"]) for e in ev] == [("robot", "escape"), (D, "reward")]
    assert json.loads(ev[0]["details"])["resp"] == 1.0
    cmds = rows(logger, "SELECT topic, source, l FROM commands ORDER BY id")
    assert [(c["topic"], c["source"]) for c in cmds] == [("cmd/motor", "brain"), ("cmd/manual", f"manual:{D}"),
                                                          ("cmd/mode", D)]
    bs = rows(logger, "SELECT * FROM brain_state")[0]
    assert (bs["dec"], json.loads(bs["why"]), bs["hab"]) == ("seek", ["odour_l"], 0.0)
    assert json.loads(rows(logger, "SELECT ap FROM weights")[0]["ap"]) == [255, 200]
    lat = rows(logger, "SELECT source, rtt_ms, crtt_ms, tick_ms, lag FROM latency ORDER BY id")
    assert lat == [{"source": "robot", "rtt_ms": 12.5, "crtt_ms": 20.0, "tick_ms": None, "lag": None},
                   {"source": "brain", "rtt_ms": None, "crtt_ms": None, "tick_ms": 5.1, "lag": 2}]
    assert {r["source"] for r in rows(logger, "SELECT source FROM status")} == {"robot", "brain"}


def test_both_clocks_are_kept(logger):
    feed_all(logger)
    s = rows(logger, "SELECT ts, up, bid FROM sensors ORDER BY id")
    assert [r["up"] for r in s] == [1000, 1050, 1100, 1150, 1200] and s[0]["bid"] == BID
    assert s[1]["ts"] > s[0]["ts"]


def test_invalid_lost_and_duplicate_messages_are_counted(logger):
    logger.tap("sensors", b"not json")
    logger.tap("sensors", b'{"v":2,"seq":0,"up":0,"bid":1,"ol":5,"or":0,"d":10,"lt":null,"bat":7}')   # ol > 1
    for seq in (0, 1, 4, 4, 5):                       # 2 and 3 lost, 4 duplicated
        logger.tap("sensors", enc("sensors", sensors(seq, 50 * seq)))
    logger.flush_now()
    r = logger.reliability()
    assert r["sensors|?"]["invalid"] == 2
    assert (r["sensors|robot"]["received"], r["sensors|robot"]["lost"], r["sensors|robot"]["duplicates"]) == (5, 2, 1)
    stored = rows(logger, "SELECT * FROM reliability WHERE topic = 'sensors' AND sender = 'robot'")[0]
    assert (stored["lost"], stored["duplicates"]) == (2, 1)


def test_a_reboot_is_not_counted_as_lost(logger):
    for seq in range(3):
        logger.tap("sensors", enc("sensors", sensors(seq, 50 * seq)))
    rebooted = sensors(0, 0)
    rebooted["bid"] = 77
    logger.tap("sensors", enc("sensors", rebooted))
    logger.flush_now()
    assert logger.reliability()["sensors|robot"]["lost"] == 0


def test_the_offline_last_will_is_not_counted_as_a_duplicate(logger):
    for seq in range(3):
        logger.tap("status", enc("status", {"v": 3, "seq": seq, "up": 100 * seq, "bid": BID, "st": "cloud", "m": "auto",
                                            "rssi": -60, "heap": 1, "bat": 7.9, "rtt": None, "crtt": None, "fw": "0.1.0",
                                            "hab": 0.0}))
    logger.tap("status", enc("status", {"v": 3, "seq": 0, "up": 50, "bid": BID, "st": "offline"}))
    logger.flush_now()
    r = logger.reliability()["status|robot"]
    assert (r["received"], r["duplicates"], r["lost"]) == (4, 0, 0)


def test_writer_thread_commits_in_batches(tmp_path):
    lg = Logger(C, tmp_path / "t.db", batch_s=0.05)
    lg.start()
    for i in range(20):
        lg.tap("sensors", enc("sensors", sensors(i, 50 * i)))
    deadline = time.time() + 3
    while time.time() < deadline and rows(lg, "SELECT COUNT(*) AS n FROM sensors")[0]["n"] < 20:
        time.sleep(0.05)
    assert rows(lg, "SELECT COUNT(*) AS n FROM sensors")[0]["n"] == 20
    lg.stop()


# ----------------------------------------------------------------------------- export and retention

def test_export_writes_one_csv_per_table(logger, tmp_path):
    feed_all(logger)
    logger.add_trial(1.0, 30.0, "default", "0", "neural", "reached")
    n = tools.export_csv(logger.db_path, tmp_path / "out")
    assert n["sensors"] == 5 and n["trials"] == 1
    with (tmp_path / "out" / "sensors.csv").open(encoding="utf-8") as f:
        data = list(csv.DictReader(f))
    assert len(data) == 5 and data[0]["ol"] == "0.2"
    assert set(p.name for p in (tmp_path / "out").iterdir()) == {f"{t}.csv" for t in db.TABLES}


def test_prune_drops_old_raw_rows_but_keeps_events(logger):
    feed_all(logger)
    con = db.connect(logger.db_path)
    con.execute("UPDATE sensors SET ts = ts - 30 * 86400")
    con.execute("UPDATE events SET ts = ts - 30 * 86400")
    con.commit()
    con.close()
    deleted = tools.prune(logger.db_path, keep_days=7)
    assert deleted["sensors"] == 5
    assert len(rows(logger, "SELECT * FROM events")) == 2


# ----------------------------------------------------------------------------- read API

@pytest.fixture
def api(tmp_path):
    clock = Clock(time.time() - 60)
    lg = Logger(C, tmp_path / "api.db", clock=clock)
    lg.clock_obj = clock
    feed_all(lg)
    for i, (dec, slp) in enumerate([("seek", False), ("eat", False), ("eat", False), ("sleep", True),
                                    ("sleep", True), ("seek", False)]):
        clock.t += 1
        lg.tap("brain/state", enc("brain/state", brain_state(10 + i, 2000 + 200 * i, dec, slp)))
    lg.add_trial(time.time() - 50, time.time() - 20, "default", "0", "neural", "reached")
    lg.add_trial(time.time() - 40, time.time() - 10, "default", "1", "neural", "timeout")
    lg.flush_now()
    yield TestClient(create_app(lg.db_path))
    lg.stop()


def test_api_health_and_sensors(api):
    h = api.get("/api/health").json()
    assert h["ok"] and h["rows"]["sensors"] == 5
    s = api.get("/api/sensors", params={"minutes": 10, "max_points": 10}).json()
    assert s["count"] == 5 and s["points"][0]["or"] == 0.1
    assert api.get("/api/sensors", params={"minutes": 0}).status_code == 422


def test_api_events_latency_trials(api):
    ev = api.get("/api/events").json()
    assert {e["type"] for e in ev} == {"escape", "reward"}
    lat = api.get("/api/latency").json()
    assert lat["rtt_ms"]["median"] == 12.5 and lat["tick_ms"]["n"] == 1 and lat["lag_max"] == 2
    assert sum(b["count"] for b in lat["rtt_ms"]["histogram"]) == 1
    t = api.get("/api/trials").json()
    assert len(t["trials"]) == 2 and t["summary"] == [{"scenario": "default", "controller": "neural", "n": 2,
                                                       "success": 1, "rate": 0.5}]


def test_api_timeline_and_reliability(api):
    tl = api.get("/api/timeline").json()
    assert len(tl["meals"]) == 1 and len(tl["sleep"]) == 1 and tl["sleep"][0]["end"] is not None
    assert len(tl["startles"]) == 1 and tl["startles"][0]["resp"] == 1.0
    rel = api.get("/api/reliability").json()
    assert any(r["topic"] == "sensors" and r["received"] == 5 for r in rel)


# ----------------------------------------------------------------------------- brain integration

def test_link_taps_see_every_message_including_our_own():
    b = Brain(BrainConfig(controller="baseline"), C, None, wall=lambda: 1000.0)
    link = BrainLink(MqttSettings("127.0.0.1", 1883, "brain", "x"), C, b)
    seen = []
    link.taps.append(lambda name, payload, retained: seen.append(name))
    msg = types.SimpleNamespace(topic=C.topic("cmd/motor"), payload=b'{"v":1,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0}',
                                retain=False)
    link._on_message(None, None, msg)
    msg2 = types.SimpleNamespace(topic=C.topic("sensors"), payload=enc("sensors", sensors(0, 0)), retain=False)
    link._on_message(None, None, msg2)
    assert seen == ["cmd/motor", "sensors"] and link.inbox.qsize() == 1


def test_brain_program_starts_the_logger_unless_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYBOT_DB_PATH", str(tmp_path / "brain.db"))
    b = Brain(BrainConfig(controller="baseline"), C, None, wall=lambda: 1000.0)
    link = BrainLink(MqttSettings("127.0.0.1", 1883, "brain", "x"), C, b)
    cfg = BrainConfig()
    lg = cli.start_logger(cfg, link)
    try:
        assert lg is not None and link.taps == [lg.tap] and (tmp_path / "brain.db").exists()
    finally:
        lg.stop()
    cfg.logger.enabled = False
    assert cli.start_logger(cfg, BrainLink(MqttSettings("127.0.0.1", 1883, "brain", "x"), C, b)) is None
