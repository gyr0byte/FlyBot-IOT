"""Failure containment: nothing that goes wrong in the logger or the Telegram bot (network errors, Telegram
API failures, SQLite errors, bugs) may stop the brain's control loop. Each test injects a failure and
checks that the brain still answers every sensors message with a cmd/motor, and that the failing part
logs, retries and recovers."""
from __future__ import annotations

import sqlite3
import threading
import time
import types

import pytest

from flybot_brain import __main__ as cli
from flybot_brain.bot import runner as bot_runner
from flybot_brain.bot.core import Bot, BotConfig, Reply
from flybot_brain.bot.runner import BotRunner
from flybot_brain.bot.telegram import FakeTelegram, HttpTelegram, TelegramError, Update
from flybot_brain.config import BrainConfig, MqttSettings
from flybot_brain.core import Brain
from flybot_brain.link import BrainLink
from flybot_brain.logger import db
from flybot_brain.logger.core import Logger
from flybot_contract import Contract

C = Contract()
BID = 2847193551
ME = 111


# ----------------------------------------------------------------------------- helpers

def brain_link():
    """A real brain (baseline controller) behind a real BrainLink whose MQTT publish is captured."""
    b = Brain(BrainConfig(controller="baseline"), C, None)
    link = BrainLink(MqttSettings("127.0.0.1", 1883, "brain", "x"), C, b)
    sent: list[tuple[str, bytes]] = []
    link.client.publish = lambda topic, payload, qos=0, retain=False: sent.append((topic, payload))
    return b, link, sent


class Feeder:
    """Pushes sensors messages through the link exactly as paho would (network thread, then worker)."""

    def __init__(self, link):
        self.link, self.seq = link, 0

    def __call__(self, n: int) -> None:
        for _ in range(n):
            payload = C.encode("sensors", {"v": 2, "seq": self.seq, "up": 1000 + self.seq * 50, "bid": BID,
                                           "ol": 0.2, "or": 0.1, "d": 90.0, "lt": 0.7, "bat": 7.9})
            self.seq += 1
            self.link._on_message(None, None, types.SimpleNamespace(topic=C.topic("sensors"), payload=payload,
                                                                    retain=False))
            self.link._work_once(0.01)


def motors(sent):
    return [C.decode("cmd/motor", p) for t, p in sent if t == C.topic("cmd/motor")]


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def count(path, table):
    con = db.connect_readonly(path)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


# ----------------------------------------------------------------------------- the link's taps

def test_a_tap_that_raises_never_stops_cmd_motor():
    b, link, sent = brain_link()
    calls = []

    def broken_tap(name, payload, retained):
        calls.append(name)
        raise RuntimeError("tap bug")

    link.taps.append(broken_tap)
    Feeder(link)(20)
    assert len(calls) >= 20 and link.tap_errors == len(calls)
    assert [m["rs"] for m in motors(sent)] == list(range(20))         # one cmd/motor per sensors, in order


# ----------------------------------------------------------------------------- the logger

def test_sqlite_errors_while_running_are_contained_and_the_logger_recovers(tmp_path, monkeypatch):
    b, link, sent = brain_link()
    lg = Logger(C, tmp_path / "flybot.db", batch_s=0.02, backoff_s=(0.02, 0.1))
    real_write = lg._write
    failing = {"n": 0, "on": True}

    def flaky_write(*a):
        if failing["on"]:
            failing["n"] += 1
            raise sqlite3.OperationalError("disk I/O error")
        return real_write(*a)

    monkeypatch.setattr(lg, "_write", flaky_write)
    lg.start()
    link.taps.append(lg.tap)
    feed = Feeder(link)
    try:
        feed(40)                                                        # the database is "broken"
        assert wait_for(lambda: failing["n"] >= 2)                      # retried after reconnecting
        assert lg.errors >= 2 and lg.thread.is_alive()
        assert len(motors(sent)) == 40                                  # the brain did not notice
        failing["on"] = False                                           # the disk comes back
        feed(40)
        assert wait_for(lambda: lg.written >= 40)
        assert len(motors(sent)) == 80
    finally:
        lg.stop()
    assert lg.dropped >= 1 and count(tmp_path / "flybot.db", "sensors") >= 40


def test_a_failing_commit_and_a_failing_reconnect_back_off_and_recover(tmp_path, monkeypatch):
    lg = Logger(C, tmp_path / "flybot.db", batch_s=0.01, backoff_s=(0.02, 0.05))
    real_commit, real_connect = lg._commit, db.connect
    state = {"commit_fails": 2, "connect_fails": 2}

    def flaky_commit():
        if state["commit_fails"]:
            state["commit_fails"] -= 1
            raise sqlite3.OperationalError("database is locked")
        real_commit()

    def flaky_connect(path):
        if state["connect_fails"]:
            state["connect_fails"] -= 1
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(path)

    lg.start()                                                          # first connect is fine
    monkeypatch.setattr(lg, "_commit", flaky_commit)
    monkeypatch.setattr(db, "connect", flaky_connect)
    b, link, sent = brain_link()
    link.taps.append(lg.tap)
    feed = Feeder(link)
    try:
        end = time.monotonic() + 5.0                                    # keep the robot talking until both
        while (state["commit_fails"] or state["connect_fails"]) and time.monotonic() < end:   # faults are used up
            feed(1)
            time.sleep(0.005)
        assert state == {"commit_fails": 0, "connect_fails": 0}
        sent_before = len(motors(sent))
        feed(10)
        assert wait_for(lambda: count(tmp_path / "flybot.db", "sensors") >= 10)
        assert lg.thread.is_alive() and lg.errors >= 4                  # 2 commits + 2 reconnects
    finally:
        lg.stop()
    assert len(motors(sent)) == sent_before + 10 == feed.seq


def test_a_bad_message_is_skipped_not_fatal(tmp_path, monkeypatch):
    lg = Logger(C, tmp_path / "flybot.db", batch_s=0.01)
    real_handle = lg._handle
    seen = []

    def buggy_handle(name, *a):
        seen.append(name)
        if len(seen) == 1:
            raise KeyError("bug in a writer")
        return real_handle(name, *a)

    monkeypatch.setattr(lg, "_handle", buggy_handle)
    lg.start()
    b, link, sent = brain_link()
    link.taps.append(lg.tap)
    try:
        Feeder(link)(5)
        assert wait_for(lambda: lg.written >= 4)
    finally:
        lg.stop()
    assert lg.errors == 1 and len(motors(sent)) == 5


def test_a_full_logger_queue_drops_instead_of_blocking_the_brain(tmp_path):
    lg = Logger(C, tmp_path / "flybot.db", max_queue=5)                 # never started: nothing drains it
    b, link, sent = brain_link()
    link.taps.append(lg.tap)
    t0 = time.monotonic()
    Feeder(link)(30)
    assert time.monotonic() - t0 < 5.0
    assert lg.dropped > 0 and len(motors(sent)) == 30


def test_brain_program_runs_on_when_the_logger_cannot_open_its_database(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYBOT_DB_PATH", str(tmp_path))                 # a directory: SQLite cannot open it
    b, link, sent = brain_link()
    assert cli.start_logger(BrainConfig(), link) is None and link.taps == []
    Feeder(link)(3)
    assert len(motors(sent)) == 3


# ----------------------------------------------------------------------------- the Telegram bot

class FakeMqtt:
    def __init__(self):
        self.published = []

    def username_pw_set(self, *a): pass
    def reconnect_delay_set(self, **k): pass
    def connect_async(self, *a, **k): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


class DownTelegram(FakeTelegram):
    """Telegram unreachable for the first `down` calls of each kind, then fine."""

    def __init__(self, down_polls=0, down_sends=0):
        super().__init__()
        self.down_polls, self.down_sends = down_polls, down_sends
        self.polls = self.sends = 0

    def get_updates(self, offset, timeout_s):
        self.polls += 1
        if self.polls <= self.down_polls:
            raise TelegramError("getUpdates failed")
        time.sleep(0.01)                                                # the real call long-polls
        return super().get_updates(offset, timeout_s)

    def send_message(self, chat_id, text):
        self.sends += 1
        if self.sends <= self.down_sends:
            raise OSError("network is unreachable")
        return super().send_message(chat_id, text)


def runner(tg, **kw):
    return BotRunner(MqttSettings("127.0.0.1", 1883, "bot", "x"), C, Bot(C, BotConfig(frozenset({ME}))), tg,
                     client=FakeMqtt(), backoff_s=(0.01, 0.05), **kw)


def test_http_telegram_reports_a_failed_poll_so_the_runner_can_back_off(monkeypatch):
    tg = HttpTelegram("123456789:AAH" + "x" * 32)
    monkeypatch.setattr(tg, "_call", lambda *a: None)                   # what a network error turns into
    with pytest.raises(TelegramError):
        tg.get_updates(None, 1)
    assert tg.send_message(ME, "hi") is False


def test_telegram_outage_backs_off_retries_and_recovers_while_the_brain_runs():
    tg = DownTelegram(down_polls=4, down_sends=2)
    r = runner(tg)
    b, link, sent = brain_link()
    feed = Feeder(link)
    r.start()
    try:
        feed(10)                                                        # brain busy while Telegram is down
        assert wait_for(lambda: tg.polls > 4)                          # polled again after backing off
        tg.say(ME, "/stop")
        assert wait_for(lambda: tg.sent)                                # reply arrived after 2 failed sends
        feed(10)
    finally:
        r.stop()
    assert r.errors >= 6 and all(t.is_alive() is False or t.daemon for t in r.threads)
    assert "Emergency stop" in tg.sent[0][1]
    assert C.decode("cmd/mode", r.client.published[0][1])["m"] == "estop"
    assert len(motors(sent)) == 20


def test_poll_backoff_does_not_spin():
    tg = DownTelegram(down_polls=10_000)
    r = runner(tg)
    r.backoff_s = (0.05, 0.2)
    r.start()
    time.sleep(0.5)
    r.stop()
    assert 2 <= tg.polls <= 8                                           # 0.05 + 0.1 + 0.2 + 0.2 ...: no busy loop


def test_a_message_is_dropped_after_its_retries_and_the_sender_carries_on():
    tg = DownTelegram(down_sends=3)
    r = runner(tg, send_tries=3)
    assert r.send_once(Reply(ME, "lost")) is False and tg.sent == []
    assert r.send_once(Reply(ME, "next")) is True and tg.sent == [(ME, "next")]


def test_bugs_in_the_bot_logic_are_contained(monkeypatch):
    tg = FakeTelegram()
    r = runner(tg)

    def boom(*a, **k):
        raise ValueError("bot bug")

    monkeypatch.setattr(r.bot, "on_mqtt", boom)
    monkeypatch.setattr(r.bot, "on_text", boom)
    msg = types.SimpleNamespace(topic=C.topic("cmd/mode"),
                                payload=C.encode("cmd/mode", {"v": 2, "seq": 1, "up": 1, "m": "auto", "src": "bot"}),
                                retain=False)
    r._on_message(None, None, msg)                                      # MQTT thread survives
    r.handle_update(ME, "/status")                                      # command fails: the user is told
    reply = r.outq.get_nowait()
    assert "failed" in reply.text and r.errors == 2


def test_a_publish_failure_in_a_command_is_contained():
    tg = FakeTelegram()
    r = runner(tg)

    def refuse(*a, **k):
        raise OSError("socket closed")

    r.client.publish = refuse
    r.handle_update(ME, "/good")
    assert "failed" in r.outq.get_nowait().text and r.errors == 1


def test_brain_program_runs_on_when_the_bot_cannot_start(monkeypatch):
    monkeypatch.setattr(bot_runner, "bot_settings", lambda env=None: ("123456789:AAH" + "x" * 32, frozenset({ME})))

    def no_bot_user(*a, **k):
        raise KeyError("MQTT_BOT_PASSWORD")

    monkeypatch.setattr(bot_runner.MqttSettings, "from_env", staticmethod(no_bot_user))
    assert bot_runner.start_bot(C) is None
    b, link, sent = brain_link()
    Feeder(link)(3)
    assert len(motors(sent)) == 3


def test_everything_failing_at_once_still_leaves_cmd_motor_flowing(tmp_path, monkeypatch):
    """Logger database broken, a tap raising, Telegram down, bot logic raising: the brain carries on."""
    b, link, sent = brain_link()
    lg = Logger(C, tmp_path / "flybot.db", batch_s=0.01, backoff_s=(0.01, 0.05))
    monkeypatch.setattr(lg, "_write", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
    lg.start()
    link.taps += [lg.tap, lambda *a: 1 / 0]
    tg = DownTelegram(down_polls=10_000, down_sends=10_000)
    r = runner(tg)
    monkeypatch.setattr(r.bot, "on_mqtt", lambda *a, **k: 1 / 0)
    r.start()
    stop = threading.Event()

    def bot_traffic():                                                  # the bot's MQTT thread, misbehaving
        msg = types.SimpleNamespace(topic=C.topic("events"), payload=b"{}", retain=False)
        while not stop.is_set():
            r._on_message(None, None, msg)
            time.sleep(0.001)

    t = threading.Thread(target=bot_traffic, daemon=True)
    t.start()
    try:
        Feeder(link)(100)
    finally:
        stop.set()
        t.join(2)
        r.stop()
        lg.stop()
    assert [m["rs"] for m in motors(sent)] == list(range(100))
    assert lg.errors >= 1 and link.tap_errors >= 100 and r.errors >= 1
